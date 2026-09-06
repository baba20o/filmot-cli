"""Staged research workflow and relationship-probe command."""

from contextlib import redirect_stdout
from dataclasses import replace
from functools import wraps
import math
import os
import re
import shlex
import sys
from typing import Optional

import click
from rich.progress import Progress, SpinnerColumn, TextColumn

from ..api_contract import FilmotAPIContractError
from ..api import FilmotClient
from ..cli_support import (
    command_error as _command_error,
    console,
    emit_raw_result,
    prepare_raw_result,
    route_progress_for as _route_progress_for,
    transcript_failure_detail as _transcript_failure_detail,
    whole_word_summary as _whole_word_summary,
)
from ..session_context import current_session, session_option
from ..schemas import (
    CommandResult,
    ErrorDetail,
    ResearchResultData,
    ResultStatus,
)
from .search import (
    _backfill_metadata,
    _detect_echo_clusters,
    _density,
    _merge_channel_ids,
    _resolve_channel_filter,
    _result_videos,
)


_PROBE_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "up", "about", "into", "through",
    "during", "before", "after", "above", "below", "between", "out", "off",
    "over", "under", "again", "further", "then", "once", "and", "but", "or",
    "nor", "not", "so", "very", "just", "than", "too", "also", "that",
    "this", "these", "those", "it", "its", "he", "she", "they", "them",
    "their", "his", "her", "we", "our", "you", "your", "me", "my",
    "which", "who", "whom", "what", "where", "when", "how", "why", "all",
    "each", "every", "both", "few", "more", "most", "other", "some", "such",
    "no", "only", "own", "same", "if", "as", "because", "while", "until",
    "there", "here", "now", "well", "like", "know", "think", "say", "said",
    "going", "really", "right", "get", "got", "go", "come", "came", "make",
    "made", "take", "took", "see", "seen", "want", "look", "way", "thing",
    "things", "much", "many", "even", "still", "back", "kind", "mean",
    "actually", "something", "anything", "nothing", "yeah", "okay", "yes",
    "um", "uh", "oh", "people", "time", "year", "years", "one", "two",
    "first", "new", "last", "long", "great", "little", "world", "good",
    "big", "need", "help", "try", "start", "part", "day", "days", "point",
    "fact", "lot", "talk", "talking", "tell", "told", "called", "keep",
    "let", "put", "end", "set", "run", "show", "turn", "move", "play",
    "live", "believe", "hold", "bring", "happen", "must", "pay", "meet",
    "include", "continue", "stand", "give", "work", "number", "already",
    "since", "different", "away", "able", "possible", "another", "quite",
    "enough", "done", "left", "second", "next", "three", "four", "five",
    "high", "important", "hand", "sure", "question", "course",
    "video", "watch", "subscribe", "channel", "comment", "share",
    "gonna", "dont", "wont", "cant", "didnt", "doesnt", "isnt", "wasnt",
    "youre", "theyre", "weve", "thats", "whats", "heres", "theres",
    "says", "president", "country", "government", "minister", "official",
    "officials", "state", "report", "reports", "according",
    "million", "billion", "percent", "tonight", "today", "yesterday",
    "breaking", "update", "latest", "news", "story", "coverage",
})


def _probe_words(text: str) -> list[str]:
    """Tokenize Latin and common non-Latin transcript scripts conservatively."""
    import unicodedata

    def script(character: str) -> str:
        codepoint = ord(character)
        if 0x4E00 <= codepoint <= 0x9FFF:
            return "han"
        if (
            0x3040 <= codepoint <= 0x30FF
            or 0x31F0 <= codepoint <= 0x31FF
        ):
            return "kana"
        if 0xAC00 <= codepoint <= 0xD7AF:
            return "hangul"
        return "other"

    words = []
    for raw in re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE):
        if raw.isascii():
            if len(raw) >= 3:
                words.append(raw)
            continue

        # Split unspaced mixed-script Japanese into useful lexical runs.
        runs = []
        current = ""
        current_script = None
        for character in raw:
            if not unicodedata.category(character).startswith(("L", "N")):
                continue
            character_script = script(character)
            if current and character_script != current_script:
                runs.append((current_script, current))
                current = ""
            current_script = character_script
            current += character
        if current:
            runs.append((current_script, current))

        for run_script, run in runs:
            if run_script == "han" and len(run) > 6:
                # Chinese commonly has no word separators. Repeated 2–3
                # character units provide a bounded, transparent fallback.
                words.extend(run[index:index + 3] for index in range(len(run) - 2))
            elif len(run) >= 2:
                words.append(run)
    return words


def _probe_term_key(term: str) -> tuple[str, ...]:
    """Return a conservative identity key for probe-term de-duplication.

    Probe discovery is not a stemming task, but a singular/plural spelling
    difference must not consume a relationship slot (``model``/``models`` or
    ``prediction error``/``prediction errors``).  Keep non-ASCII lexical runs
    exact so this English inflection guard cannot alter multilingual queries.
    """
    key = []
    for word in term.casefold().split():
        if not word.isascii():
            key.append(word)
        elif len(word) > 4 and word.endswith("ies"):
            key.append(word[:-3] + "y")
        elif (
            len(word) > 4
            and word.endswith("s")
            and not word.endswith(("ss", "us", "is"))
        ):
            key.append(word[:-1])
        else:
            key.append(word)
    return tuple(key)


def _extract_probe_terms(texts, topic, top_n=12):
    """Extract significant terms from transcript texts for NEAR/N probing.

    Preserve transcript and sentence boundaries, then prefer terms supported by
    multiple distinct sources.  This prevents a repeated caption glitch in one
    video—or a bigram accidentally formed at a transcript boundary—from
    consuming the probe budget.
    """
    import re
    from collections import Counter, defaultdict
    from difflib import SequenceMatcher

    topic_words = set(_probe_words(topic))
    source_sentences = []
    total_words = 0
    for text in texts:
        sentences = []
        # Newlines are meaningful for timestamped/manual transcripts; terminal
        # punctuation provides the best available boundary for continuous ASR.
        for sentence in re.split(r"(?:[.!?]+|\r?\n+)", text.lower()):
            words = _probe_words(sentence)
            if words:
                sentences.append(words)
                total_words += len(words)
        source_sentences.append(sentences)

    # Adaptive thresholds: scale with corpus size
    # ~100 words -> min 2, ~1000 -> min 3, ~5000+ -> min 5
    bigram_min = max(2, min(5, total_words // 500))
    single_min = max(2, min(8, total_words // 300))

    # Bigrams: two consecutive non-stopwords
    bigrams = Counter()
    bigram_sources = defaultdict(set)
    singles = Counter()
    single_sources = defaultdict(set)
    for source_index, sentences in enumerate(source_sentences):
        for words in sentences:
            for i in range(len(words) - 1):
                w1, w2 = words[i], words[i + 1]
                if w1 in _PROBE_STOPWORDS or w2 in _PROBE_STOPWORDS:
                    continue
                if w1 in topic_words and w2 in topic_words:
                    continue
                # Artificial whitespace can change CJK query semantics. Use
                # repeated non-Latin terms as singles and pair them later.
                if not (w1.isascii() and w2.isascii()):
                    continue
                term = f"{w1} {w2}"
                bigrams[term] += 1
                bigram_sources[term].add(source_index)
            for word in words:
                if (
                    word not in _PROBE_STOPWORDS
                    and word not in topic_words
                    # A standalone English word is far too broad for a global
                    # subtitle query.  ASCII probes must be phrases; retain
                    # non-ASCII lexical runs because many supported scripts do
                    # not delimit words with spaces.
                    and not word.isascii()
                    and len(word) >= (4 if word.isascii() else 2)
                ):
                    singles[word] += 1
                    single_sources[word].add(source_index)

    # Rank by source support before raw repetition.  Bigrams remain preferable
    # at equal support/count because they are usually more discriminating.
    candidates = []
    for term, count in bigrams.items():
        if count >= bigram_min:
            candidates.append((len(bigram_sources[term]), count, 1, term))
    for term, count in singles.items():
        if count >= single_min:
            candidates.append((len(single_sources[term]), count, 0, term))
    candidates.sort(key=lambda item: (item[0], item[2], item[1]), reverse=True)

    # If at least two sources exist, first admit cross-source terms; retain a
    # single-source fallback so small or heterogeneous corpora still probe.
    if len(texts) >= 2 and any(item[0] >= 2 for item in candidates):
        candidates = [item for item in candidates if item[0] >= 2] + [
            item for item in candidates if item[0] < 2
        ]

    terms = []
    seen_words = set()
    seen_keys = set()
    for _, _, is_bigram, term in candidates:
        term_key = _probe_term_key(term)
        if term_key in seen_keys:
            continue
        if not is_bigram and term in seen_words:
            continue

        # Cluster likely ASR variants (e.g. "threei atlas" /
        # "threeey atlas") and retain the higher-ranked canonical form.
        is_variant = False
        for existing in terms:
            same_tail = (
                len(term.split()) > 1
                and len(existing.split()) > 1
                and term.split()[-1] == existing.split()[-1]
            )
            if same_tail and SequenceMatcher(None, term, existing).ratio() >= 0.80:
                is_variant = True
                break
        if is_variant:
            continue

        terms.append(term)
        seen_keys.add(term_key)
        if is_bigram:
            seen_words.update(term.split())
        if len(terms) >= top_n:
            break

    return terms


def _find_probe_pairs(
    texts,
    terms,
    window_size=50,
    max_pairs=5,
    *,
    include_scores=False,
):
    """Find co-occurring term pairs within text windows.

    Windows never cross transcript or sentence boundaries. Returns
    ``(term1, term2, co_window_count, supporting_source_count)``. Callers that
    request ``include_scores`` receive one additional, JSON-safe basis mapping
    with a language-neutral source-normalized salience score.

    The score treats extracted terms as opaque lexical units. It rewards
    repeated cross-source evidence while source IDF and median-length
    normalization prevent a ubiquitous phrase or one unusually long
    transcript from dominating the five-query probe budget.
    """
    import re
    from collections import Counter, defaultdict
    from statistics import median

    # Build lookup: word -> set of terms it belongs to
    word_to_terms = {}
    for term in terms:
        for w in term.split():
            word_to_terms.setdefault(w, set()).add(term)

    # Slide through text in overlapping windows (inclusive of the tail)
    pair_counts = Counter()
    pair_sources = defaultdict(set)
    term_source_counts = defaultdict(Counter)
    source_lengths = [0] * len(texts)
    step = max(window_size // 2, 1)
    for source_index, text in enumerate(texts):
        for sentence in re.split(r"(?:[.!?]+|\r?\n+)", text.lower()):
            words = _probe_words(sentence)
            if not words:
                continue
            source_lengths[source_index] += len(words)

            # Count exact term occurrences once per sentence. Pair windows
            # overlap by design; using them for term frequency would inflate
            # long sentences and make the score depend on window stride.
            for position, word in enumerate(words):
                for term in word_to_terms.get(word, ()):
                    parts = term.split()
                    if (
                        (len(parts) == 1 and word == parts[0])
                        or (
                            word == parts[0]
                            and position + len(parts) <= len(words)
                            and words[position:position + len(parts)] == parts
                        )
                    ):
                        term_source_counts[term][source_index] += 1

            starts = list(range(0, max(len(words) - window_size, 0) + 1, step))
            tail_start = max(len(words) - window_size, 0)
            if tail_start not in starts:
                starts.append(tail_start)
            for start in starts:
                window = words[start:start + window_size]
                window_terms = set()

                for i, word in enumerate(window):
                    if word in word_to_terms:
                        for term in word_to_terms[word]:
                            parts = term.split()
                            if len(parts) == 1:
                                window_terms.add(term)
                            elif (
                                word == parts[0]
                                and i + len(parts) <= len(window)
                                and window[i:i + len(parts)] == parts
                            ):
                                window_terms.add(term)

                window_list = sorted(window_terms)
                for i in range(len(window_list)):
                    for j in range(i + 1, len(window_list)):
                        pair = (window_list[i], window_list[j])
                        pair_counts[pair] += 1
                        pair_sources[pair].add(source_index)

    source_count = len(texts)
    median_source_length = float(median(source_lengths)) if source_lengths else 1.0
    median_source_length = max(median_source_length, 1.0)
    term_salience = {}
    term_basis = {}
    for term in terms:
        counts = term_source_counts.get(term, {})
        document_frequency = sum(count > 0 for count in counts.values())
        normalized_tf = sum(
            math.log1p(
                count
                * median_source_length
                / max(float(source_lengths[source_index]), 1.0)
            )
            for source_index, count in counts.items()
            if count > 0
        )
        idf = (
            math.log(
                1.0
                + (
                    source_count - document_frequency + 0.5
                ) / (document_frequency + 0.5)
            )
            if source_count and document_frequency
            else 0.0
        )
        score = normalized_tf * idf
        term_salience[term] = score
        term_basis[term] = {
            "score": round(score, 3),
            "document_frequency": document_frequency,
            "occurrences": sum(counts.values()),
        }

    # Filter pairs that share a lexical unit, then rank by explainable local
    # salience rather than treating every two-word phrase as equally specific.
    candidates = []
    for (t1, t2), count in pair_counts.items():
        if count < 2:
            continue
        if len(texts) >= 2 and len(pair_sources[(t1, t2)]) < 2:
            continue
        words_t1 = set(_probe_term_key(t1))
        words_t2 = set(_probe_term_key(t2))
        if words_t1 & words_t2:
            continue  # overlapping terms, skip
        if _probe_term_key(t1) == _probe_term_key(t2):
            continue
        pair_score = (
            math.sqrt(term_salience.get(t1, 0.0) * term_salience.get(t2, 0.0))
            * math.log1p(count)
            * math.log1p(len(pair_sources[(t1, t2)]))
        )
        basis = {
            "method": "source_normalized_tfidf_v1",
            "score": round(pair_score, 3),
            "seed_sources": source_count,
            "co_windows": count,
            "source_support": len(pair_sources[(t1, t2)]),
            "left": {"term": t1, **term_basis[t1]},
            "right": {"term": t2, **term_basis[t2]},
        }
        candidates.append((
            pair_score,
            len(pair_sources[(t1, t2)]),
            count,
            t1,
            t2,
            basis,
        ))

    candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    if include_scores:
        return [
            (t1, t2, count, supporting_sources, basis)
            for _, supporting_sources, count, t1, t2, basis
            in candidates[:max_pairs]
        ]
    return [
        (t1, t2, count, supporting_sources)
        for _, supporting_sources, count, t1, t2, _
        in candidates[:max_pairs]
    ]


def _probe_pair_parts(pair) -> tuple[str, str, int, int, dict]:
    """Normalize scored pairs and legacy four-tuples used by integrations."""
    term1, term2, co_windows, source_support = pair[:4]
    basis = pair[4] if len(pair) > 4 and isinstance(pair[4], dict) else {
        "method": "unscored",
        "score": None,
        "seed_sources": None,
        "co_windows": co_windows,
        "source_support": source_support,
    }
    return term1, term2, co_windows, source_support, basis


def _probe_seed_stage(record: dict) -> str:
    """Return the provenance stage used to bound recursive probe expansion."""
    metadata = record.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    stage = metadata.get("selection_stage") or record.get("selection_stage")
    return str(stage or "manual").strip().casefold()


def _bounded_topic_span_count(passage: str, tokens: list[str]) -> int:
    """Count non-overlapping ordered topic spans in one visible passage."""
    text = str(passage or "").casefold()
    if not text or not tokens:
        return 0

    if len(tokens) == 1:
        token = tokens[0]
        if token.isascii():
            return len(
                re.findall(
                    rf"(?<!\w){re.escape(token)}(?!\w)",
                    text,
                    flags=re.UNICODE,
                )
            )
        return text.count(token)

    words = re.findall(r"[^\W_]{2,}", text, flags=re.UNICODE)
    max_span = max(8, len(tokens) * 4)
    count = 0
    next_start = 0
    for start, word in enumerate(words):
        if start < next_start or word != tokens[0]:
            continue
        cursor = start
        for token in tokens[1:]:
            cursor = next(
                (
                    index
                    for index in range(
                        cursor + 1,
                        min(len(words), start + max_span),
                    )
                    if words[index] == token
                ),
                -1,
            )
            if cursor < 0:
                break
        else:
            count += 1
            next_start = cursor + 1

    # Preserve the previous exact-substring behavior for mixed-script phrases
    # whose visible lexical units may not be separated by spaces.
    if not all(token.isascii() for token in tokens):
        compact_topic = "".join(tokens)
        compact_passage = re.sub(r"\s+", "", text)
        count = max(count, compact_passage.count(compact_topic))
    return count


def _topic_relationship_evidence(video: dict, topic: str) -> dict:
    """Return field-aware lexical evidence for automatic admission.

    A single query-shaped phrase in a long description can be a related-topics
    list rather than the video's focus. Metadata is therefore admitted only
    when the bounded ordered span occurs in the title, or when at least two
    non-overlapping spans corroborate one another across the description and
    visible hit passages. This is a lexical safety gate, not a semantic claim.
    """
    tokens = _research_topic_tokens(topic)
    title_spans = _bounded_topic_span_count(video.get("title", ""), tokens)
    description_spans = _bounded_topic_span_count(
        video.get("description", ""),
        tokens,
    )

    # Search responses can repeat the same rendered hit object. Count a
    # normalized visible passage once so duplicate API rows cannot manufacture
    # corroboration.
    hit_spans = 0
    seen_hit_passages = set()
    for hit in video.get("hits", [])[:25]:
        if not isinstance(hit, dict):
            continue
        passage = _research_hit_text(hit).strip()
        if not passage or passage in seen_hit_passages:
            continue
        seen_hit_passages.add(passage)
        hit_spans += _bounded_topic_span_count(passage, tokens)

    corroborating_spans = description_spans + hit_spans
    return {
        "admitted": bool(tokens) and (
            title_spans >= 1 or corroborating_spans >= 2
        ),
        "title_spans": title_spans,
        "description_spans": description_spans,
        "hit_spans": hit_spans,
        "corroborating_spans": corroborating_spans,
        "max_span_words": max(8, len(tokens) * 4) if len(tokens) > 1 else 1,
    }


def _research_topic_tokens(topic: str) -> list[str]:
    """Meaningful Unicode topic tokens used for transparent relevance scoring."""
    tokens = re.findall(r"[^\W_]{2,}", topic.casefold(), flags=re.UNICODE)
    return [token for token in tokens if token not in _PROBE_STOPWORDS]


def _topic_token_present(text: str, token: str) -> bool:
    """Match ASCII tokens as words and non-ASCII tokens as script substrings."""
    if token.isascii():
        return bool(
            re.search(
                rf"(?<!\w){re.escape(token)}(?!\w)",
                text,
                flags=re.UNICODE,
            )
        )
    return token in text


def _research_query_ladder(topic: str) -> list[tuple[str, str]]:
    """Relationship-preserving transcript-only fallbacks, narrow to broad."""
    escaped = topic.replace('"', " ").strip()
    tokens = _research_topic_tokens(escaped)
    ladder = []
    if escaped:
        ladder.append(("exact_phrase", f'"{escaped}"'))
    proximity_tokens = tokens
    if len(tokens) >= 4 and len(tokens[0]) <= 2:
        # A leading acronym is often a modifier ("AI data center ..."); the
        # adjacent multi-word concepts carry the relationship being tested.
        proximity_tokens = tokens[1:]
    if len(proximity_tokens) >= 2:
        midpoints = [max(1, (len(proximity_tokens) + 1) // 2)]
        alternate = max(1, len(proximity_tokens) // 2)
        if alternate not in midpoints:
            midpoints.append(alternate)
        for split_index, midpoint in enumerate(midpoints):
            left = " ".join(proximity_tokens[:midpoint])
            right = " ".join(proximity_tokens[midpoint:])
            if left and right:
                stage = "proximity" if split_index == 0 else "proximity_alt"
                ladder.append((stage, f'"{left}" NEAR/25 "{right}"'))
    # De-duplicate degenerate forms while retaining stage names.
    seen = set()
    return [
        (stage, query)
        for stage, query in ladder
        if not (query in seen or seen.add(query))
    ]


def _research_hit_text(hit: dict) -> str:
    parts = [
        hit.get("ctx_before", ""),
        hit.get("token", ""),
        hit.get("ctx_after", ""),
    ]
    for line in hit.get("lines", []):
        parts.append(line if isinstance(line, str) else str(line.get("text", "")))
    return " ".join(parts).casefold()


def _candidate_assessment(video: dict, topic: str, echo_cluster=None) -> dict:
    """Return separate, explainable relevance and source-quality signals."""
    tokens = _research_topic_tokens(topic)
    title = str(video.get("title", "")).casefold()
    passages = []
    description = str(video.get("description", "")).casefold()
    if description:
        passages.append(description)
    passages.extend(
        _research_hit_text(hit)
        for hit in video.get("hits", [])[:25]
    )
    combined = " ".join([title] + passages)

    if tokens:
        token_coverage = (
            sum(_topic_token_present(combined, token) for token in tokens)
            / len(tokens)
        )
        passage_coverage = max(
            (
                sum(
                    _topic_token_present(passage, token)
                    for token in tokens
                ) / len(tokens)
                for passage in [title] + passages
            ),
            default=0.0,
        )
        title_coverage = (
            sum(_topic_token_present(title, token) for token in tokens)
            / len(tokens)
        )
    else:
        # Non-Latin topics still get relationship safety from the exact/proximity
        # fallback stage; do not pretend an ASCII tokenizer scored them.
        token_coverage = passage_coverage = title_coverage = 0.0

    views = max(int(video.get("viewcount", 0) or 0), 0)
    likes = max(int(video.get("likecount", 0) or 0), 0)
    subscribers = max(int(video.get("channelsubcount", 0) or 0), 0)
    engagement = likes / views if views else 0.0

    # A prior, not a verdict: independent visible signals remain inspectable.
    audience_signal = min(math.log10(subscribers + 1) / 6.0, 1.0)
    reach_signal = min(math.log10(views + 1) / 7.0, 1.0)
    engagement_signal = min(engagement / 0.05, 1.0)
    source_signal = (
        0.50 * audience_signal
        + 0.30 * reach_signal
        + 0.20 * engagement_signal
    )
    echo_penalty = 0.25 if echo_cluster is not None else 0.0
    density = _density(video)
    density_signal = min(math.log1p(density) / math.log(6), 1.0)
    relevance_signal = (
        0.50 * passage_coverage
        + 0.30 * title_coverage
        + 0.20 * token_coverage
    )
    balanced = (
        0.55 * relevance_signal
        + 0.25 * source_signal
        + 0.20 * density_signal
        - echo_penalty
    )
    return {
        "token_coverage": round(token_coverage, 3),
        "passage_coverage": round(passage_coverage, 3),
        "title_coverage": round(title_coverage, 3),
        "lexical_admission": _topic_relationship_evidence(video, topic),
        "density": round(density, 3),
        "source_signal": round(source_signal, 3),
        "echo_cluster": echo_cluster,
        "balanced_score": round(balanced, 3),
        "views": views,
        "subscribers": subscribers,
        "engagement": round(engagement, 4),
    }


def _rank_research_candidates(videos: list, topic: str, sort_by: str) -> list:
    """Attach evidence and rank without conflating density/source authority."""
    echo_clusters = _detect_echo_clusters(videos)
    for index, video in enumerate(videos):
        video["_selection"] = _candidate_assessment(
            video, topic, echo_clusters.get(index)
        )

    if sort_by == "viewcount":
        key = lambda video: int(video.get("viewcount", 0) or 0)
    elif sort_by == "density":
        key = _density
    elif sort_by == "source-prior":
        key = lambda video: video["_selection"]["source_signal"]
    else:
        key = lambda video: video["_selection"]["balanced_score"]
    return sorted(videos, key=key, reverse=True)


def _research_candidate_preview(videos: list, count: int = 8) -> None:
    """Show why automatic research selected each candidate."""
    if not videos:
        return
    console.print("\n[bold]Candidate preview (relevance and source signals are separate):[/bold]")
    for index, video in enumerate(videos[:count], 1):
        selection = video.get("_selection", {})
        lexical = selection.get("lexical_admission", {})
        from_scout = bool(video.get("_from_scout"))
        origin = "scout" if from_scout else video.get("_fallback_stage", "filmot")
        lexical_detail = ""
        if from_scout:
            lexical_detail = (
                f"; scout-lexical-spans=t{lexical.get('title_spans', 0)}"
                f"/d{lexical.get('description_spans', 0)}"
                f"/h{lexical.get('hit_spans', 0)}"
            )
        console.print(
            f"  {index}. {str(video.get('title', 'Unknown'))[:66]} "
            f"[dim]({video.get('channelname', 'Unknown')})[/dim]\n"
            f"     [dim]stage={origin}; relevance={selection.get('passage_coverage', 0):.2f}; "
            f"source-prior={selection.get('source_signal', 0):.2f}; "
            f"density={selection.get('density', 0):.2f}/min"
            f"{lexical_detail}"
            f"{'; echo=' + str(selection['echo_cluster']) if selection.get('echo_cluster') else ''}[/dim]"
        )


def _render_research_result(
    outcome: CommandResult[ResearchResultData],
) -> None:
    """Render the same typed summary persisted to the research ledger."""
    data = outcome.data
    if outcome.status_value == ResultStatus.EMPTY.value:
        console.print(
            "[yellow]No candidates passed the search and relevance "
            "gates.[/yellow]"
        )
    elif outcome.status_value == ResultStatus.PARTIAL.value:
        console.print(
            "[yellow]Research completed with a partial scope or item "
            "failures; inspect the ledger errors before drawing "
            "conclusions.[/yellow]"
        )
    for warning in outcome.warnings:
        console.print(f"[yellow]{warning}[/yellow]")
    console.print(f"\n{'=' * 60}")
    console.print(f"[bold]Research complete: {data['topic']}[/bold]")
    console.print(
        f"  Saved: {data['saved']} | Skipped: {data['skipped']} | "
        f"Failed: {data['failed']} | Deduped: {data['deduped']} | "
        f"Probe: {data['probe_saved']} saved / "
        f"{data['probe_download_failed']} download failed / "
        f"{data['probe_query_failed']} query failed"
    )
    total_chars = int(data["chars"])
    console.print(
        f"  Total content: {total_chars:,} characters "
        f"({total_chars / 1024:.0f} KB)"
    )

    sources = data.get("sources") or []
    if sources:
        console.print(f"\n[bold]Sources ({len(sources)}):[/bold]")
        for item in sources:
            console.print(
                f"  - {item.get('title', 'Unknown')} "
                f"({item.get('channel', 'Unknown')})"
            )
    console.print("\n[dim]Next steps:[/dim]")
    console.print(
        f'  filmot library search "your query" --topic {data["topic"]}'
    )
    console.print(
        f'  filmot library compare "claim" --topic {data["topic"]}'
    )
    console.print(
        f'  filmot library echoes {data["topic"]}'
    )
    console.print(
        f'  filmot claims add {data["topic"]} "exact claim statement"'
    )
    console.print(
        f'  filmot sessions {shlex.quote(current_session() or data["topic"])} --summary'
    )
    console.print(
        f'  filmot library context {data["topic"]} -o context.txt'
    )


def _research_output(callback):
    """Keep progress on stderr and emit JSON only after the run is finalized.

    The redirect includes nested scout/download helpers. Emission happens outside
    their exception handling so Exit(1) for a failed result cannot be caught as
    another research failure or produce a second result.
    """
    @wraps(callback)
    def run(*args, **kwargs):
        if not kwargs.get("raw", False):
            return callback(*args, **kwargs)
        with redirect_stdout(sys.stderr):
            outcome = callback(*args, **kwargs)
        return emit_raw_result(outcome, indent=2)

    return run


# ========== RESEARCH COMMAND ==========

@click.command("research")
@session_option
@click.argument("topic")
@click.option(
    "--depth",
    "-n",
    default=10,
    type=click.IntRange(0),
    show_default=True,
    help=(
        "Maximum transcripts to select from the accumulated safe pool; "
        "0 previews the initial Filmot scope without selected downloads "
        "(enabled scout and explicit probes still run)"
    ),
)
@click.option("--min-views", default=None, type=click.IntRange(0), help="Minimum view count filter")
@click.option("--lang", "-l", default=None, help="Language code (default: en)")
@click.option("--fallback", is_flag=True, help="Use AWS Transcribe fallback when captions are unavailable")
@click.option("--dedupe", is_flag=True, help="Skip duplicate transcripts")
@click.option("--min-matches", default=2, type=click.IntRange(0), show_default=True,
              help="Minimum subtitle hits per Filmot candidate (0 disables)")
@click.option(
    "--sort", "sort_by", default="balanced", show_default=True,
    type=click.Choice(["balanced", "density", "source-prior", "viewcount"]),
    help="Candidate ranking; source-prior is an unverified audience/engagement heuristic",
)
@click.option("--candidate-pages", default=3, type=click.IntRange(1, 20), show_default=True,
              help="Filmot pages to fetch before client-side ranking")
@click.option("--candidate-pool", default=150, type=click.IntRange(1), show_default=True,
              help="Maximum Filmot candidates to score")
@click.option("--accept-broad", is_flag=True,
              help="Allow a high-cardinality loose fallback after relationship-preserving stages fail")
@click.option("--broad-threshold", default=1000, type=click.IntRange(1), show_default=True,
              help="Require --accept-broad above this loose-fallback result count")
@click.option("--channel-id", default=None, help="Limit Filmot candidates to exact channel ID(s)")
@click.option("--channel", default=None, help="Resolve channel text explicitly, then fail closed")
@click.option("--channel-count", default=None, type=click.IntRange(1),
              help="Maximum fuzzy-channel resolutions (default: 10)")
@click.option("--scout/--no-scout", default=True,
              help="Run the YouTube freshness scout (requires YOUTUBE_API_KEY)")
@click.option("--scout-days", default=7, type=click.IntRange(1), show_default=True)
@click.option("--probe", is_flag=True,
              help=(
                  "Probe eligible saved/selected sources with transparent "
                  "NEAR/N queries, even when current discovery is empty"
              ))
@click.option("--no-proxy", is_flag=True, help="Bypass proxy for transcript downloads")
@click.option("--verbose", is_flag=True, help="Show full transcript failure details")
@click.option("--raw", is_flag=True, help="Emit one typed JSON result; progress goes to stderr")
@_research_output
def research(
    topic: str,
    depth: int,
    min_views: int,
    lang: str,
    fallback: bool,
    dedupe: bool,
    min_matches: int,
    sort_by: str,
    candidate_pages: int,
    candidate_pool: int,
    accept_broad: bool,
    broad_threshold: int,
    channel_id: str,
    channel: str,
    channel_count: int,
    scout: bool,
    scout_days: int,
    probe: bool,
    no_proxy: bool,
    verbose: bool,
    raw: bool,
):
    """Research TOPIC with staged search, visible selection, and checkpoints.

    The search ladder accumulates unique title+transcript, exact-phrase, and
    NEAR/N candidates until the depth maximum is met or those relationship
    stages are exhausted. A nonempty safe pool is never widened with a loose
    transcript-wide query merely because it is under depth. A loose result set
    above ``--broad-threshold`` is never downloaded unless ``--accept-broad``
    is explicit. Density is topical concentration, not source credibility.
    When ``--probe`` is explicit, a legitimately empty discovery is recorded
    as an empty selection and probing continues from eligible transcripts
    already saved in the topic; broad-scope safety failures still fail closed.

    \b
      filmot research "deep sea mining"
      filmot research "AI data center electricity demand" --candidate-pages 5
      filmot research "niche topic" --accept-broad --probe
      filmot research "fusion" --channel "International Energy Agency"
    """
    import hashlib
    import uuid

    from ..ledger import log_event, log_result
    from ..library import get_library, normalize_topic_name
    from ..transcript import (
        describe_routing_plan,
        disable_proxy,
        get_transcript,
        get_transcript_with_fallback,
        routing_plan,
    )

    library = None
    normalized_topic = normalize_topic_name(topic)
    selected_session = current_session()
    normalized_session = (
        normalize_topic_name(selected_session, fallback="session")
        if selected_session is not None
        else None
    )
    run_id = uuid.uuid4().hex[:12]
    run_status = "failed"
    run_error = None
    phase = "initializing"

    scout_videos = []
    total = 0
    fallback_stage = None
    success_count = 0
    skip_count = 0
    fail_count = 0
    dedupe_count = 0
    probe_success = 0
    probe_fail_count = 0
    probe_query_fail_count = 0
    total_chars = 0
    probe_chars = 0
    selected_count = 0
    resolved_channels = []
    effective_channel_ids = channel_id
    route_plan = None
    search_partial = False
    search_page_errors = []
    scout_error = None
    recorded_outcome = None

    def aggregate_data(*, include_sources=True) -> ResearchResultData:
        """Build the one result payload used by renderer and ledger."""
        return {
            "run_id": run_id,
            "topic": normalized_topic,
            **({"session": normalized_session} if normalized_session is not None else {}),
            "query": topic,
            "phase": phase,
            "scout": len(scout_videos),
            "filmot_total": total,
            "fallback_stage": fallback_stage,
            "selected": selected_count,
            "saved": success_count,
            "skipped": skip_count,
            "failed": fail_count,
            "deduped": dedupe_count,
            "probe_saved": probe_success,
            "probe_download_failed": probe_fail_count,
            "probe_query_failed": probe_query_fail_count,
            "chars": total_chars + probe_chars,
            "sources": (
                library.list_transcripts(normalized_topic)
                if library is not None and include_sources
                else []
            ),
            "routing_plan": route_plan or {},
        }

    def aggregate_errors() -> list[ErrorDetail]:
        errors = [scout_error] if scout_error is not None else []
        if search_page_errors:
            errors.append(
                ErrorDetail(
                    type="PartialSearch",
                    message="; ".join(search_page_errors),
                    stage="search",
                    details={"page_errors": list(search_page_errors)},
                )
            )
        if fail_count or probe_fail_count or probe_query_fail_count:
            errors.append(
                ErrorDetail(
                    type="ResearchItemFailure",
                    message=(
                        f"{fail_count} selected downloads, "
                        f"{probe_fail_count} probe downloads, and "
                        f"{probe_query_fail_count} probe queries failed"
                    ),
                    stage="download",
                    details={
                        "selected_downloads": fail_count,
                        "probe_downloads": probe_fail_count,
                        "probe_queries": probe_query_fail_count,
                    },
                )
            )
        return errors

    def record_result(outcome):
        """Persist exactly the outcome emitted by the selected renderer."""
        nonlocal run_status, recorded_outcome
        if raw:
            outcome = prepare_raw_result(outcome)
            if normalized_session is not None:
                # Serialization failures replace the payload; retain the safe
                # routing identity so stdout and the ledger still agree.
                outcome = replace(
                    outcome, data={**outcome.data, "session": normalized_session},
                )
        run_status = outcome.status_value
        recorded_outcome = outcome
        log_result(
            "research",
            outcome,
            topic=normalized_topic,
            data=outcome.data,
        )
        return outcome

    def failure_result(error, *, status=ResultStatus.FAILED):
        if recorded_outcome is not None:
            return recorded_outcome
        # A failed library read may itself be the exception; do not repeat it
        # while constructing the failure report. Counters still preserve work.
        return record_result(CommandResult(
            command="research",
            status=status,
            data=aggregate_data(include_sources=False),
            errors=[*aggregate_errors(), ErrorDetail.from_exception(error, stage=phase)],
        ))

    start_fields = {
        "run_id": run_id,
        "query": topic,
        "depth": depth,
        "lang": lang or "en",
        "min_views": min_views,
        "min_matches": min_matches,
        "sort": sort_by,
        "candidate_pages": candidate_pages,
        "candidate_pool": candidate_pool,
        "accept_broad": accept_broad,
        "broad_threshold": broad_threshold,
        "channel": channel,
        "channel_id": channel_id,
        "channel_count": channel_count,
        "scout": scout,
        "scout_days": scout_days,
        "probe": probe,
        "dedupe": dedupe,
        "fallback": fallback,
        "raw": raw,
    }
    log_event("research_start", topic=normalized_topic, **start_fields)

    def checkpoint(current_phase: str, **fields) -> None:
        nonlocal phase
        phase = current_phase
        log_event(
            "research_checkpoint",
            topic=normalized_topic,
            run_id=run_id,
            phase=current_phase,
            **fields,
        )

    def fetch_transcript(video_id: str):
        route_progress = _route_progress_for(video_id)
        languages = [lang] if lang else None
        if fallback:
            return get_transcript_with_fallback(
                video_id,
                languages=languages,
                use_aws_fallback=True,
                aws_progress_callback=lambda stage, message: click.echo(
                    f"{video_id} AWS:{stage} {message}",
                    err=True,
                ),
                progress_callback=route_progress,
                fresh_primary=True,
            )
        return get_transcript(
            video_id,
            languages=languages,
            progress_callback=route_progress,
            fresh_primary=True,
        )

    try:
        library = get_library()
        normalized_topic = library._normalize_topic(topic)
        if no_proxy:
            disable_proxy()
        route_plan = routing_plan()
        click.echo(
            f"Transcript routes ({route_plan['mode']}): "
            f"{describe_routing_plan(route_plan)}; "
            f"route deadline {route_plan['route_timeout_s']:g}s",
            err=True,
        )
        checkpoint("routing", status="ready", routing_plan=route_plan)

        client = FilmotClient()
        if channel:
            resolved_ids, resolved_channels = _resolve_channel_filter(
                client, channel, channel_count
            )
            effective_channel_ids = _merge_channel_ids(channel_id, resolved_ids)
            console.print(
                "[cyan]Resolved --channel:[/cyan] "
                + ", ".join(
                    f'{item["name"]} ({item["id"]})'
                    for item in resolved_channels
                )
            )

        console.print(f"[bold]Researching: {topic}[/bold]")
        console.print(f"[dim]Run ID: {run_id}[/dim]\n")

        # Phase 1: freshness scout. A scout failure is non-fatal but explicit.
        if scout:
            scout_max_results = 10
            scout_order = "relevance"
            scout_allowed_ids = set(
                (effective_channel_ids or "").split(",")
            ) - {""}
            scout_channel_id = (
                next(iter(scout_allowed_ids))
                if len(scout_allowed_ids) == 1
                else None
            )
            checkpoint(
                "scout",
                status="started",
                query=topic,
                days=scout_days,
                channel_id=effective_channel_ids,
                request_channel_id=scout_channel_id,
                max_results=scout_max_results,
                order=scout_order,
            )
            try:
                from ..youtube_search import search_recent, validate_youtube_api

                validate_youtube_api()
                with console.status(
                    f"[bold cyan]Scouting YouTube for '{topic}' "
                    f"(last {scout_days} days)...[/bold cyan]"
                ):
                    scout_videos = search_recent(
                        query=topic,
                        days_back=scout_days,
                        max_results=scout_max_results,
                        order=scout_order,
                        channel_id=scout_channel_id,
                    ) or []
                if scout_allowed_ids:
                    scout_videos = [
                        video
                        for video in scout_videos
                        if str(video.get("channel_id") or "")
                        in scout_allowed_ids
                    ]
                checkpoint(
                    "scout",
                    status="completed",
                    results=len(scout_videos),
                    channel_id=effective_channel_ids,
                    request_channel_id=scout_channel_id,
                    max_results=scout_max_results,
                    order=scout_order,
                )
                console.print(
                    f"[cyan]Scout:[/cyan] Found {len(scout_videos)} recent upload(s)"
                )
                for index, video in enumerate(scout_videos[:5], 1):
                    console.print(
                        f"  {index}. {str(video.get('title', 'Unknown'))[:70]} "
                        f"[dim]({str(video.get('published_at', ''))[:10]}, "
                        f"{int(video.get('views', 0) or 0):,} views)[/dim]"
                    )
            except (ValueError, ImportError) as error:
                checkpoint("scout", status="skipped", error=str(error))
                console.print("[dim]Scout: Skipped (YOUTUBE_API_KEY not configured)[/dim]")
            except Exception as error:
                scout_error = ErrorDetail.from_exception(error, stage="scout")
                detail = f"{type(error).__name__}: {_whole_word_summary(error)}"
                checkpoint(
                    "scout",
                    status="failed",
                    error=detail,
                )
                console.print(
                    f"[yellow]Scout: Failed ({detail})[/yellow]"
                )
        else:
            checkpoint("scout", status="disabled", results=0)

        api_kwargs = {
            "lang": lang or "en",
            "min_views": min_views,
            "channel_id": effective_channel_ids,
        }

        def run_search(
            stage: str,
            query: str,
            *,
            title_filter: Optional[str] = None,
        ) -> dict:
            nonlocal search_partial
            checkpoint(
                "search",
                status="started",
                stage=stage,
                query=query,
                title=title_filter,
                channel_id=effective_channel_ids,
                lang=lang or "en",
                candidate_pages=candidate_pages,
                candidate_pool=candidate_pool,
            )
            with console.status(
                f"[bold green]Filmot {stage}: {query}[/bold green]"
            ):
                response = client.search_subtitles_all(
                    query=query,
                    title=title_filter,
                    max_pages=candidate_pages,
                    max_results=candidate_pool,
                    **api_kwargs,
                )
            if "error" in response:
                checkpoint(
                    "search",
                    status="failed",
                    stage=stage,
                    query=query,
                    title=title_filter,
                    error=response["error"],
                )
                _command_error(
                    f"Filmot {stage} search failed: {response['error']}"
                )
            stage_videos = _result_videos(response)
            if effective_channel_ids:
                allowed_ids = set(effective_channel_ids.split(","))
                outside = [
                    video for video in stage_videos
                    if str(
                        video.get("channelid")
                        or video.get("channel_id")
                        or ""
                    ) not in allowed_ids
                ]
                if outside:
                    checkpoint(
                        "search",
                        status="failed_closed",
                        stage=stage,
                        query=query,
                        channel_id=effective_channel_ids,
                        outside_results=len(outside),
                    )
                    _command_error(
                        "Filmot returned candidates outside, or without an ID "
                        "in, the requested channel set; refusing an "
                        "unrestricted fallback."
                    )
            checkpoint(
                "search",
                status="completed",
                stage=stage,
                query=query,
                title=title_filter,
                api_total=response.get("totalresultcount", len(stage_videos)),
                candidates=len(stage_videos),
                pages=response.get("pages_fetched", 1),
                partial=response.get("partial", False),
                page_error=response.get("page_error"),
            )
            if response.get("partial"):
                search_partial = True
                page_error = str(
                    response.get("page_error") or "later page failed"
                )
                if page_error not in search_page_errors:
                    search_page_errors.append(page_error)
                console.print(
                    f"[yellow]Search scope is partial:[/yellow] "
                    f"{response.get('page_error', 'later page failed')}"
                )
            return response

        def eligible_stage_candidates(response: dict, stage: str) -> list:
            candidates = _result_videos(response)
            if min_matches <= 0:
                return candidates
            eligible = [
                video
                for video in candidates
                if len(video.get("hits", [])) >= min_matches
            ]
            if len(eligible) != len(candidates):
                console.print(
                    f"[dim]{stage} hit filter: {len(candidates)} -> "
                    f"{len(eligible)} candidates (minimum {min_matches})[/dim]"
                )
                checkpoint(
                    "search_filter",
                    status="completed",
                    stage=stage,
                    candidates=len(candidates),
                    eligible=len(eligible),
                    min_matches=min_matches,
                )
            return eligible

        def relationship_evidence_gate(candidates: list, stage: str) -> list:
            """Require visible topic coherence before trusting fallback syntax."""
            if stage not in {"exact_phrase", "proximity", "proximity_alt"}:
                return candidates
            before = len(candidates)
            kept = []
            for video in candidates:
                assessment = _candidate_assessment(video, topic)
                video["_selection"] = assessment
                if (
                    assessment["passage_coverage"] >= 0.75
                    and assessment["token_coverage"] >= 0.75
                ):
                    kept.append(video)
            if len(kept) != before:
                checkpoint(
                    "relationship_gate",
                    status="completed",
                    stage=stage,
                    candidates_before=before,
                    candidates_after=len(kept),
                    threshold={
                        "passage_coverage": 0.75,
                        "token_coverage": 0.75,
                    },
                )
                console.print(
                    f"[dim]{stage} relationship-evidence gate: {before} -> "
                    f"{len(kept)} candidates (at least 75% topic coverage "
                    "within one visible passage)[/dim]"
                )
            return kept

        # Phase 2: progressively fill a relationship-preserving pool. ``depth``
        # is a maximum download target, not a reason to discard candidates from
        # an earlier/stronger stage or to enter the loose fallback as soon as a
        # safe pool happens to be under target.
        videos = []
        seen_candidate_ids = set()
        relationship_stages = []

        def accumulate_stage_candidates(candidates: list, stage: str) -> int:
            """Add unique candidates once, preserving their strongest origin."""
            added = 0
            duplicates = 0
            for video in candidates:
                video_id = video.get("id") or video.get("videoid")
                identity = str(video_id) if video_id is not None else None
                if identity is not None and identity in seen_candidate_ids:
                    duplicates += 1
                    continue
                if identity is not None:
                    seen_candidate_ids.add(identity)
                # Stages run strongest-to-weaker. Set the origin only on first
                # admission so a later duplicate cannot rewrite provenance.
                video["_fallback_stage"] = stage
                videos.append(video)
                added += 1
            checkpoint(
                "search_accumulate",
                status="completed",
                stage=stage,
                qualified=len(candidates),
                added=added,
                duplicates=duplicates,
                unique_pool=len(videos),
                target=depth,
            )
            pool_progress = (
                f"unique pool {len(videos)}/{depth}"
                if depth > 0
                else f"preview pool {len(videos)}"
            )
            console.print(
                f"[dim]{stage} accumulation: {len(candidates)} qualified, "
                f"{added} new, {duplicates} duplicate(s); "
                f"{pool_progress}.[/dim]"
            )
            return added

        results = run_search("title+transcript", topic, title_filter=topic)
        title_filter_works = bool(_result_videos(results))
        title_candidates = eligible_stage_candidates(
            results, "title+transcript"
        )
        fallback_stage = "title+transcript"
        relationship_stages.append(fallback_stage)
        accumulate_stage_candidates(title_candidates, fallback_stage)

        ladder = _research_query_ladder(topic)
        if depth == 0:
            console.print(
                "[dim]Depth target is 0: keeping the title+transcript preview "
                "scope and skipping expansion stages.[/dim]"
            )
            checkpoint(
                "search_ladder",
                status="target_zero",
                target=depth,
                unique_pool=len(videos),
                stages=relationship_stages,
            )
        elif len(videos) >= depth:
            checkpoint(
                "search_ladder",
                status="target_reached",
                target=depth,
                unique_pool=len(videos),
                stages=relationship_stages,
            )
        elif len(videos) < depth:
            for stage, query in ladder:
                console.print(
                    f"[dim]Qualified relationship pool: {len(videos)}/{depth}; "
                    f"trying {stage}: {query}[/dim]"
                )
                results = run_search(stage, query)
                stage_candidates = eligible_stage_candidates(results, stage)
                stage_candidates = relationship_evidence_gate(
                    stage_candidates, stage
                )
                fallback_stage = stage
                relationship_stages.append(stage)
                accumulate_stage_candidates(stage_candidates, stage)
                if len(videos) >= depth:
                    checkpoint(
                        "search_ladder",
                        status="target_reached",
                        target=depth,
                        unique_pool=len(videos),
                        stages=relationship_stages,
                    )
                    break

        relationship_underfilled = (
            depth > 0
            and 0 < len(videos) < depth
            and len(relationship_stages) == 1 + len(ladder)
        )
        if relationship_underfilled:
            console.print(
                f"[dim]Relationship-preserving ladder exhausted with "
                f"{len(videos)}/{depth} unique qualified Filmot candidates. "
                "Keeping that safe pool; loose transcript-wide fallback is "
                "not entered for a nonempty relationship pool.[/dim]"
            )
            checkpoint(
                "search_ladder",
                status="underfilled",
                target=depth,
                unique_pool=len(videos),
                stages=relationship_stages,
                broad_fallback="not_entered_nonempty_pool",
            )

        broad_blocked = False
        broad_total = 0
        if not videos and depth > 0:
            console.print(
                "[dim]Relationship-preserving stages returned no candidates; "
                "measuring loose transcript-wide fallback...[/dim]"
            )
            results = run_search("broad_loose", topic)
            videos = eligible_stage_candidates(results, "broad_loose")
            fallback_stage = "broad_loose"
            for video in videos:
                video["_fallback_stage"] = fallback_stage
            broad_total = int(results.get("totalresultcount", len(videos)) or 0)
            if broad_total > broad_threshold and not accept_broad:
                broad_blocked = True
                videos = []
                console.print(
                    f"[yellow]Safety gate:[/yellow] loose fallback has "
                    f"{broad_total:,} results (threshold {broad_threshold:,}). "
                    "It will not be downloaded automatically. Refine the topic "
                    "or rerun with --accept-broad after reviewing the scope."
                )
                checkpoint(
                    "broad_gate",
                    status="blocked",
                    stage=fallback_stage,
                    api_total=broad_total,
                    threshold=broad_threshold,
                )

        # Multiple ladder stages overlap, so no single API total describes the
        # accumulated universe. Keep the aggregate honest as the unique
        # qualified Filmot pool; per-stage API totals remain in search
        # checkpoints, and ``broad_total`` remains the safety-gate cardinality.
        total = len(videos)
        videos = _rank_research_candidates(videos, topic, sort_by)

        # Even with explicit acceptance, a loose pool needs a relationship
        # threshold. Repeated isolated terms are not enough to auto-download.
        if fallback_stage == "broad_loose" and not broad_blocked:
            before = len(videos)
            topic_tokens = _research_topic_tokens(topic)
            if topic_tokens:
                videos = [
                    video for video in videos
                    if video["_selection"]["passage_coverage"] >= 0.60
                    and video["_selection"]["token_coverage"] >= 0.75
                ]
            console.print(
                f"[dim]Broad relevance gate: {before} -> {len(videos)} candidates "
                "(terms must cohere in a title/hit passage)[/dim]"
            )
            checkpoint(
                "broad_gate",
                status="accepted_explicit" if accept_broad else "accepted_bounded",
                api_total=total,
                candidates_before=before,
                candidates_after=len(videos),
                threshold={"passage_coverage": 0.60, "token_coverage": 0.75},
            )
            total = len(videos)

        # Merge fresh scout videos and score them transparently. They remain
        # clearly tagged because Filmot hit evidence is unavailable.
        filmot_ids = {
            video.get("id") or video.get("videoid")
            for video in videos
        }
        for scout_video in scout_videos:
            if scout_video.get("video_id") in filmot_ids:
                continue
            videos.append({
                "id": scout_video.get("video_id"),
                "videoid": scout_video.get("video_id"),
                "title": scout_video.get("title", ""),
                "description": scout_video.get("description", ""),
                "channelname": scout_video.get("channel_title", ""),
                "channelid": scout_video.get("channel_id", ""),
                "uploaddate": scout_video.get("published_at", ""),
                "viewcount": scout_video.get("views", 0),
                "duration": 0,
                "hits": [],
                "_from_scout": True,
                "_fallback_stage": "scout",
            })

        videos = _rank_research_candidates(videos, topic, sort_by)
        scout_before = sum(
            bool(video.get("_from_scout"))
            for video in videos
        )
        if scout_before:
            videos = [
                video
                for video in videos
                if not video.get("_from_scout")
                or video["_selection"]["lexical_admission"]["admitted"]
            ]
            scout_after = sum(
                bool(video.get("_from_scout"))
                for video in videos
            )
            console.print(
                f"[dim]Scout lexical admission gate: {scout_before} -> "
                f"{scout_after} candidates (ordered topic span in title, or "
                "two corroborating description/hit spans)[/dim]"
            )
            checkpoint(
                "scout_gate",
                status="completed",
                candidates_before=scout_before,
                candidates_after=scout_after,
                threshold={
                    "title_spans": 1,
                    "corroborating_description_hit_spans": 2,
                },
            )
        accepted_scouts = any(
            video.get("_from_scout")
            for video in videos
        )
        if depth > 0 and 0 < len(videos) < depth:
            underfill_reason = (
                "relationship_ladder_exhausted_nonempty"
                if relationship_underfilled
                else "qualified_pool_exhausted"
            )
            console.print(
                f"[yellow]Qualified pool under target:[/yellow] "
                f"{len(videos)}/{depth}. Depth is a maximum, so Filmot will "
                f"continue with {len(videos)} source(s). Next action: inspect "
                "these candidates, then refine TOPIC or run a targeted "
                "`filmot search` exact/NEAR query; increasing --depth alone "
                "will not widen the safe pool."
            )
            checkpoint(
                "candidate_underfill",
                status="completed",
                target=depth,
                qualified=len(videos),
                remaining=depth - len(videos),
                reason=underfill_reason,
                next_action="targeted_exact_or_near_search",
            )
        if broad_blocked and not accepted_scouts:
            _command_error(
                f"Broad fallback blocked at {broad_total:,} results. Refine TOPIC or "
                "rerun with --accept-broad after reviewing the risk."
            )
        if not videos:
            run_status = (
                ResultStatus.PARTIAL.value
                if search_partial or scout_error is not None
                else ResultStatus.EMPTY.value
            )
            if not probe:
                checkpoint(
                    "selection",
                    status="empty",
                    fallback_stage=fallback_stage,
                    filmot_total=total,
                )
                outcome = CommandResult(
                    command="research",
                    status=run_status,
                    data=aggregate_data(),
                    errors=aggregate_errors(),
                    warnings=(
                        [
                            "No candidates passed the search and relevance "
                            "gates."
                        ]
                        if search_partial
                        else []
                    ),
                )
                outcome = record_result(outcome)
                if not raw:
                    _render_research_result(outcome)
                return outcome
            console.print(
                "[dim]No current discovery candidates passed the gates; "
                "continuing the requested probe from eligible transcripts "
                "already saved in this topic.[/dim]"
            )

        _research_candidate_preview(videos)
        previewed = videos[: min(depth or 8, 8)]
        if previewed and all(
            video.get("_selection", {}).get("source_signal", 0) < 0.20
            for video in previewed
        ):
            console.print(
                "[yellow]Source-quality warning:[/yellow] every leading candidate "
                "has a weak visible source prior. Treat the corpus as discovery "
                "material and add authoritative channels/primary sources."
            )
        echoed = sum(
            bool(video.get("_selection", {}).get("echo_cluster"))
            for video in previewed
        )
        if echoed:
            console.print(
                f"[yellow]Echo warning:[/yellow] {echoed} leading candidate(s) "
                "share near-identical hit phrasing; the balanced rank penalized them."
            )

        # Selection follows the displayed global rank exactly. Origin is an
        # inspectable signal, not a hidden quota that can displace a stronger
        # candidate at small depths.
        videos_to_download = videos[:depth]
        selected_count = len(videos_to_download)
        checkpoint(
            "selection",
            status="empty" if not videos else "completed",
            fallback_stage=fallback_stage,
            filmot_total=total,
            candidates=len(videos),
            selected=selected_count,
            candidate_pages=candidate_pages,
            candidate_pool=candidate_pool,
            ranking=sort_by,
            selections=[
                {
                    "video_id": video.get("id") or video.get("videoid"),
                    "title": video.get("title"),
                    "channel": video.get("channelname"),
                    "stage": video.get("_fallback_stage"),
                    "signals": video.get("_selection"),
                }
                for video in videos_to_download
            ],
        )

        seen_hashes = set()
        if dedupe:
            for item in library.list_transcripts(normalized_topic):
                data = library.get(item["video_id"], normalized_topic)
                if data:
                    seen_hashes.add(
                        hashlib.md5(
                            data.get("transcript", "")[:500].encode()
                        ).hexdigest()
                    )

        if videos_to_download:
            console.print(
                f"\n[bold]Downloading "
                f"{len(videos_to_download)} transcript(s)...[/bold]"
            )
        proxy_errors = False
        for index, video in enumerate(videos_to_download, 1):
            video_id = video.get("id") or video.get("videoid")
            title_text = str(video.get("title", "Unknown"))
            channel_name = (
                video.get("channelname")
                or video.get("channeltitle")
                or video.get("channel")
                or "Unknown"
            )
            source = (
                "scout"
                if video.get("_from_scout")
                else video.get("_fallback_stage") or fallback_stage or "unknown"
            )
            selection = video.get("_selection", {})
            checkpoint(
                "download_item",
                status="started",
                index=index,
                total=selected_count,
                video_id=video_id,
                title=title_text,
                stage=source,
                signals=selection,
            )
            title_text, channel_name = _backfill_metadata(
                video_id, title_text, channel_name
            )

            if library.exists(video_id, normalized_topic):
                skip_count += 1
                data = library.get(video_id, normalized_topic)
                if data:
                    total_chars += len(data.get("transcript", ""))
                checkpoint(
                    "download_item",
                    status="skipped",
                    video_id=video_id,
                    reason="already_exists",
                    stage=source,
                    signals=selection,
                )
                console.print(
                    f"  [{index}/{selected_count}] [yellow]Skip[/yellow] "
                    f"{title_text[:60]} (already saved)"
                )
                continue

            console.print(
                f"  [{index}/{selected_count}] [dim]Fetching {title_text[:60]} "
                "(route ladder in progress)...[/dim]"
            )
            try:
                transcript_result = fetch_transcript(video_id)
                if "error" in transcript_result:
                    error_text = str(transcript_result["error"])
                    error_display = _transcript_failure_detail(
                        transcript_result,
                        verbose=verbose,
                    )
                    fail_count += 1
                    if "proxy" in error_text.casefold():
                        proxy_errors = True
                    checkpoint(
                        "download_item",
                        status="failed",
                        video_id=video_id,
                        title=title_text,
                        stage=source,
                        signals=selection,
                        error=_whole_word_summary(error_text, 500),
                        error_type=transcript_result.get("error_type"),
                        route=transcript_result.get("route"),
                        routes_tried=transcript_result.get("routes_tried"),
                        route_errors=transcript_result.get("route_errors"),
                    )
                    console.print(
                        f"  [{index}/{selected_count}] [red]Fail[/red] "
                        f"{title_text[:60]} [dim]- {error_display}[/dim]"
                    )
                    continue

                full_text = transcript_result.get("full_text", "")
                if dedupe and full_text:
                    digest = hashlib.md5(full_text[:500].encode()).hexdigest()
                    if digest in seen_hashes:
                        dedupe_count += 1
                        checkpoint(
                            "download_item",
                            status="skipped",
                            video_id=video_id,
                            reason="duplicate",
                            stage=source,
                            signals=selection,
                        )
                        console.print(
                            f"  [{index}/{selected_count}] [magenta]Dedupe[/magenta] "
                            f"{title_text[:60]}"
                        )
                        continue
                    seen_hashes.add(digest)

                if not full_text.strip():
                    checkpoint(
                        "download_item",
                        status="skipped",
                        video_id=video_id,
                        reason="empty_transcript",
                        stage=source,
                        signals=selection,
                    )
                    console.print(
                        f"  [{index}/{selected_count}] [yellow]Skip[/yellow] "
                        f"{title_text[:60]} [dim]- empty transcript[/dim]"
                    )
                    continue

                metadata = {
                    "title": title_text,
                    "channel": channel_name,
                    "channel_id": video.get("channelid"),
                    "published_at": video.get("uploaddate"),
                    "source": transcript_result.get("source", "youtube"),
                    "language": transcript_result.get("language"),
                    "is_generated": transcript_result.get("is_generated"),
                    "duration_seconds": transcript_result.get("duration_seconds"),
                    "segment_count": transcript_result.get("segment_count"),
                    "views": video.get("viewcount"),
                    "research_run_id": run_id,
                    "selection_stage": source,
                    "selection_signals": selection,
                    "route": transcript_result.get("route"),
                    "routes_tried": transcript_result.get("routes_tried"),
                }
                library.save(
                    video_id=video_id,
                    topic=normalized_topic,
                    transcript_text=full_text,
                    metadata=metadata,
                    segments=transcript_result.get("segments", []),
                )
                success_count += 1
                total_chars += len(full_text)
                checkpoint(
                    "download_item",
                    status="saved",
                    video_id=video_id,
                    title=title_text,
                    channel=channel_name,
                    stage=source,
                    signals=selection,
                    chars=len(full_text),
                    route=transcript_result.get("route"),
                    routes_tried=transcript_result.get("routes_tried"),
                )
                console.print(
                    f"  [{index}/{selected_count}] [green]✓[/green] "
                    f"{title_text[:60]}"
                )
            except KeyboardInterrupt:
                raise
            except Exception as error:
                fail_count += 1
                detail = f"{type(error).__name__}: {error}"
                checkpoint(
                    "download_item",
                    status="failed",
                    video_id=video_id,
                    title=title_text,
                    stage=source,
                    signals=selection,
                    error=detail,
                )
                console.print(
                    f"  [{index}/{selected_count}] [red]Fail[/red] "
                    f"{title_text[:60]} [dim]- "
                    f"{detail if verbose else _whole_word_summary(detail)}[/dim]"
                )

        if proxy_errors:
            console.print(
                "[yellow]Proxy failures occurred. Inspect routes_tried in the "
                "session ledger or retry with --no-proxy.[/yellow]"
            )

        # Phase 4: probes with every effective constraint and count visible.
        if probe:
            checkpoint("probe", status="started")
            library_items = library.list_transcripts(normalized_topic)
            eligible_records = []
            excluded_seed_stages = {}
            transcript_record_count = 0
            for item in library_items:
                data = library.get(item["video_id"], normalized_topic)
                if data and data.get("transcript"):
                    transcript_record_count += 1
                    stage = _probe_seed_stage(data)
                    if stage in {"scout", "probe"}:
                        excluded_seed_stages[stage] = (
                            excluded_seed_stages.get(stage, 0) + 1
                        )
                        continue
                    eligible_records.append(data)

            transcript_texts = [
                record["transcript"] for record in eligible_records
            ]
            excluded_seed_count = sum(excluded_seed_stages.values())
            stage_summary = ", ".join(
                f"{stage}:{count}"
                for stage, count in sorted(excluded_seed_stages.items())
            ) or "none"
            checkpoint(
                "probe_seed",
                status="completed",
                transcripts=transcript_record_count,
                eligible=len(transcript_texts),
                excluded=excluded_seed_count,
                excluded_by_stage=excluded_seed_stages,
                policy="selected_or_manual_non_frontier",
            )
            console.print(
                f"[dim]Probe seeds: {len(transcript_texts)} eligible "
                f"selected/manual transcript(s); {excluded_seed_count} "
                f"automatic frontier source(s) excluded ({stage_summary}).[/dim]"
            )

            if len(transcript_texts) < 2:
                checkpoint(
                    "probe",
                    status="skipped",
                    reason="insufficient_eligible_seeds",
                    transcripts=transcript_record_count,
                    eligible_seeds=len(transcript_texts),
                    excluded_frontier=excluded_seed_count,
                    excluded_by_stage=excluded_seed_stages,
                )
                console.print(
                    f"[dim]Probe skipped: need at least 2 eligible "
                    f"selected/manual seed transcripts; got "
                    f"{len(transcript_texts)}. Automatic scout/probe "
                    "discoveries remain readable but cannot recursively "
                    "expand the corpus.[/dim]"
                )
            else:
                terms = _extract_probe_terms(transcript_texts, topic)
                pairs = _find_probe_pairs(
                    transcript_texts,
                    terms,
                    include_scores=True,
                )
                pair_rows = [_probe_pair_parts(pair) for pair in pairs]
                console.print(
                    f"\n[bold]Probing relationships from "
                    f"{len(transcript_texts)} eligible seed transcript(s)...[/bold]"
                )
                console.print(
                    f"  Entities: [cyan]{', '.join(terms[:8]) or 'none'}[/cyan]"
                )
                probe_empty_reason = None
                if not terms:
                    probe_empty_reason = "no_candidate_terms"
                    console.print(
                        "  [dim]Probe empty: no sufficiently specific "
                        "multiword terms were available from the eligible "
                        "seeds; 0 queries run.[/dim]"
                    )
                elif not pair_rows:
                    probe_empty_reason = "no_cross_source_pairs"
                    console.print(
                        f"  [dim]Probe empty: extracted {len(terms)} candidate "
                        "term(s), but no relationship pair met the "
                        "cross-source support and co-window requirements; "
                        "0 queries run.[/dim]"
                    )
                existing_ids = {
                    item["video_id"]
                    for item in library_items
                }
                probe_candidates = []
                probe_title = topic if title_filter_works else None
                probe_queries_executed = 0
                probe_queries_deferred = 0
                effective_probe_scope = {
                    "title": probe_title,
                    "channel_id": effective_channel_ids,
                    "lang": lang or "en",
                }

                def defer_probe_tail(start_offset: int, reason: str) -> int:
                    """Persist each unexecuted query so early stopping is visible."""
                    deferred = 0
                    for deferred_index, deferred_pair in enumerate(
                        pair_rows[start_offset:],
                        start_offset + 1,
                    ):
                        (
                            deferred_term1,
                            deferred_term2,
                            deferred_windows,
                            deferred_support,
                            deferred_basis,
                        ) = deferred_pair
                        deferred_query = (
                            f'"{deferred_term1}" NEAR/15 '
                            f'"{deferred_term2}"'
                        )
                        checkpoint(
                            "probe_search",
                            status="deferred",
                            index=deferred_index,
                            query=deferred_query,
                            constraints=effective_probe_scope,
                            co_windows=deferred_windows,
                            source_support=deferred_support,
                            informativeness=deferred_basis,
                            reason=reason,
                        )
                        deferred += 1
                    return deferred

                for index, pair in enumerate(pair_rows, 1):
                    (
                        term1,
                        term2,
                        co_windows,
                        source_support,
                        informativeness,
                    ) = pair
                    probe_query = f'"{term1}" NEAR/15 "{term2}"'
                    effective_scope = effective_probe_scope
                    checkpoint(
                        "probe_search",
                        status="started",
                        index=index,
                        query=probe_query,
                        constraints=effective_scope,
                        co_windows=co_windows,
                        source_support=source_support,
                        informativeness=informativeness,
                    )
                    probe_queries_executed += 1
                    try:
                        with console.status(f"Probing: {probe_query}"):
                            probe_result = client.search_subtitles(
                                query=probe_query,
                                title=probe_title,
                                lang=lang or "en",
                                channel_id=effective_channel_ids,
                            )
                    except Exception as error:
                        detail = f"{type(error).__name__}: {error}"
                        probe_query_fail_count += 1
                        log_event(
                            "research_probe",
                            topic=normalized_topic,
                            run_id=run_id,
                            query=probe_query,
                            constraints=effective_scope,
                            co_windows=co_windows,
                            source_support=source_support,
                            informativeness=informativeness,
                            status="failed",
                            error=detail,
                        )
                        checkpoint(
                            "probe_search",
                            status="failed",
                            index=index,
                            query=probe_query,
                            constraints=effective_scope,
                            informativeness=informativeness,
                            error=detail,
                        )
                        console.print(
                            f"  Probe {index}: {probe_query} → "
                            f"[red]error[/red] ({_whole_word_summary(detail)})"
                        )
                        continue

                    if "error" in probe_result:
                        detail = str(probe_result["error"])
                        probe_query_fail_count += 1
                        log_event(
                            "research_probe",
                            topic=normalized_topic,
                            run_id=run_id,
                            query=probe_query,
                            constraints=effective_scope,
                            co_windows=co_windows,
                            source_support=source_support,
                            informativeness=informativeness,
                            status="failed",
                            error=detail,
                        )
                        checkpoint(
                            "probe_search",
                            status="failed",
                            index=index,
                            query=probe_query,
                            constraints=effective_scope,
                            informativeness=informativeness,
                            error=detail,
                        )
                        console.print(
                            f"  Probe {index}: {probe_query} → "
                            f"[red]error[/red] ({_whole_word_summary(detail)})"
                        )
                        continue

                    raw_hits = _result_videos(probe_result)
                    if effective_channel_ids:
                        allowed_ids = set(effective_channel_ids.split(","))
                        outside = [
                            video
                            for video in raw_hits
                            if str(
                                video.get("channelid")
                                or video.get("channel_id")
                                or ""
                            ) not in allowed_ids
                        ]
                        if outside:
                            checkpoint(
                                "probe_search",
                                status="failed_closed",
                                index=index,
                                query=probe_query,
                                constraints=effective_scope,
                                informativeness=informativeness,
                                outside_results=len(outside),
                            )
                            log_event(
                                "research_probe",
                                topic=normalized_topic,
                                run_id=run_id,
                                query=probe_query,
                                constraints=effective_scope,
                                informativeness=informativeness,
                                status="failed_closed",
                                outside_results=len(outside),
                            )
                            _command_error(
                                "Filmot returned probe candidates outside, or "
                                "without an ID in, the requested channel set; "
                                "refusing an unrestricted probe."
                            )
                    scoped_hits = [
                        video for video in raw_hits
                        if (video.get("id") or video.get("videoid")) not in existing_ids
                    ]
                    # The title parameter is only an API hint: do not trust it
                    # as proof that the returned discovery preserves the
                    # topic relationship.  Every candidate must show the full
                    # ordered topic within one visible title/description/hit.
                    scoped_hits = [
                        video for video in scoped_hits
                        if _topic_relationship_evidence(video, topic)["admitted"]
                    ]
                    api_total = int(
                        probe_result.get("totalresultcount", len(raw_hits)) or 0
                    )
                    sample_coverage = (
                        round(min(len(raw_hits) / api_total, 1.0), 4)
                        if api_total > 0
                        else 1.0
                    )
                    broad_sampled_zero = (
                        api_total > broad_threshold and not scoped_hits
                    )
                    probe_event_status = (
                        "broad_sampled"
                        if broad_sampled_zero
                        else "completed"
                    )
                    peak_density = max(
                        (_density(video) for video in raw_hits),
                        default=0.0,
                    )
                    scope_label = (
                        f'title="{probe_title}"'
                        if probe_title else "topic relevance post-filter"
                    )
                    log_event(
                        "research_probe",
                        topic=normalized_topic,
                        run_id=run_id,
                        query=probe_query,
                        constraints=effective_scope,
                        co_windows=co_windows,
                        source_support=source_support,
                        informativeness=informativeness,
                        status=probe_event_status,
                        api_total=api_total,
                        returned=len(raw_hits),
                        scoped=len(scoped_hits),
                        sample_coverage=sample_coverage,
                        broad_threshold=broad_threshold,
                        peak_density=round(peak_density, 3),
                    )
                    checkpoint(
                        "probe_search",
                        status=probe_event_status,
                        index=index,
                        query=probe_query,
                        constraints=effective_scope,
                        co_windows=co_windows,
                        source_support=source_support,
                        informativeness=informativeness,
                        api_total=api_total,
                        returned=len(raw_hits),
                        scoped=len(scoped_hits),
                        sample_coverage=sample_coverage,
                        broad_threshold=broad_threshold,
                    )
                    pair_score = informativeness.get("score")
                    score_label = (
                        f"{pair_score:.3f}"
                        if isinstance(pair_score, (int, float))
                        else "n/a"
                    )
                    console.print(
                        f"  Probe {index}: {probe_query} "
                        f"[dim](co-windows:{co_windows}; sources:{source_support}; "
                        f"score:{score_label}; scope:{scope_label})[/dim] → "
                        f"[green]{api_total:,} API results[/green]; "
                        f"{len(raw_hits)} returned; {len(scoped_hits)} new+scoped; "
                        f"peak {peak_density:.1f}/min"
                    )
                    ranked_probe = _rank_research_candidates(
                        scoped_hits, topic, "balanced"
                    )
                    for video in ranked_probe[:2]:
                        if len(probe_candidates) >= 3:
                            break
                        video_id = video.get("id") or video.get("videoid")
                        if video_id not in existing_ids:
                            video["_probe_query"] = probe_query
                            video["_probe_index"] = index
                            probe_candidates.append(video)
                            existing_ids.add(video_id)

                    if broad_sampled_zero:
                        manual_argv = [
                            "filmot",
                            "search",
                            probe_query,
                            "--lang",
                            lang or "en",
                        ]
                        if probe_title:
                            manual_argv.extend(["--title", probe_title])
                        if effective_channel_ids:
                            manual_argv.extend([
                                "--channel-id",
                                effective_channel_ids,
                            ])
                        manual_query = shlex.join(manual_argv)
                        console.print(
                            f"  [yellow]Sampled zero:[/yellow] the returned "
                            f"sample covered {len(raw_hits):,}/{api_total:,} "
                            f"results ({sample_coverage:.1%}) and contained no "
                            "new candidate with sufficient visible topic "
                            "evidence. This is not a global zero."
                        )
                        console.print(
                            "  [dim]Stopping the lower-ranked automatic tail "
                            f"at the {broad_threshold:,}-result budget. Exact "
                            f"manual query: {manual_query}[/dim]"
                        )
                        deferred = defer_probe_tail(
                            index,
                            "broad_sampled_tail_budget",
                        )
                        probe_queries_deferred += deferred
                        break

                    if len(probe_candidates) >= 3:
                        deferred = defer_probe_tail(
                            index,
                            "candidate_capacity_reached",
                        )
                        probe_queries_deferred += deferred
                        if deferred:
                            console.print(
                                "  [dim]Probe candidate capacity reached "
                                f"(3); deferred {deferred} lower-ranked "
                                "automatic query(s).[/dim]"
                            )
                        break

                for video in probe_candidates[:3]:
                    video_id = video.get("id") or video.get("videoid")
                    title_text = str(video.get("title", "Unknown"))
                    channel_name = (
                        video.get("channelname")
                        or video.get("channeltitle")
                        or "Unknown"
                    )
                    checkpoint(
                        "probe_download",
                        status="started",
                        video_id=video_id,
                        title=title_text,
                        channel=channel_name,
                        probe_query=video.get("_probe_query"),
                        probe_index=video.get("_probe_index"),
                    )
                    title_text, channel_name = _backfill_metadata(
                        video_id, title_text, channel_name
                    )
                    console.print(
                        f"  [dim]Fetching probe discovery {title_text[:60]}...[/dim]"
                    )
                    try:
                        transcript_result = fetch_transcript(video_id)
                        if "error" in transcript_result:
                            detail = str(transcript_result["error"])
                            probe_fail_count += 1
                            checkpoint(
                                "probe_download",
                                status="failed",
                                video_id=video_id,
                                title=title_text,
                                channel=channel_name,
                                probe_query=video.get("_probe_query"),
                                probe_index=video.get("_probe_index"),
                                error=_whole_word_summary(detail, 500),
                                error_type=transcript_result.get("error_type"),
                                route=transcript_result.get("route"),
                                routes_tried=transcript_result.get("routes_tried"),
                                route_errors=transcript_result.get("route_errors"),
                            )
                            console.print(
                                f"  [red]Fail[/red] {title_text[:60]} "
                                f"[dim]- {_transcript_failure_detail(transcript_result, verbose=verbose)}[/dim]"
                            )
                            continue
                        full_text = transcript_result.get("full_text", "")
                        if not full_text.strip():
                            checkpoint(
                                "probe_download",
                                status="skipped",
                                video_id=video_id,
                                title=title_text,
                                channel=channel_name,
                                probe_query=video.get("_probe_query"),
                                probe_index=video.get("_probe_index"),
                                reason="empty_transcript",
                            )
                            console.print(
                                f"  [yellow]Skip[/yellow] {title_text[:60]} "
                                f"[dim]- empty transcript[/dim]"
                            )
                            continue
                        library.save(
                            video_id=video_id,
                            topic=normalized_topic,
                            transcript_text=full_text,
                            metadata={
                                "title": title_text,
                                "channel": channel_name,
                                "channel_id": video.get("channelid"),
                                "published_at": video.get("uploaddate"),
                                "source": transcript_result.get("source", "youtube"),
                                "language": transcript_result.get("language"),
                                "is_generated": transcript_result.get("is_generated"),
                                "duration_seconds": transcript_result.get("duration_seconds"),
                                "segment_count": transcript_result.get("segment_count"),
                                "views": video.get("viewcount"),
                                "research_run_id": run_id,
                                "selection_stage": "probe",
                                "probe_query": video.get("_probe_query"),
                                "probe_index": video.get("_probe_index"),
                                "selection_signals": video.get("_selection"),
                                "route": transcript_result.get("route"),
                                "routes_tried": transcript_result.get("routes_tried"),
                            },
                            segments=transcript_result.get("segments", []),
                        )
                        probe_success += 1
                        probe_chars += len(full_text)
                        checkpoint(
                            "probe_download",
                            status="saved",
                            video_id=video_id,
                            title=title_text,
                            channel=channel_name,
                            probe_query=video.get("_probe_query"),
                            probe_index=video.get("_probe_index"),
                            chars=len(full_text),
                            route=transcript_result.get("route"),
                            routes_tried=transcript_result.get("routes_tried"),
                        )
                        console.print(
                            f"  [green]✓[/green] {title_text[:60]} "
                            "[dim](probe)[/dim]"
                        )
                    except KeyboardInterrupt:
                        raise
                    except Exception as error:
                        probe_fail_count += 1
                        detail = f"{type(error).__name__}: {error}"
                        checkpoint(
                            "probe_download",
                            status="failed",
                            video_id=video_id,
                            title=title_text,
                            channel=channel_name,
                            probe_query=video.get("_probe_query"),
                            probe_index=video.get("_probe_index"),
                            error=detail,
                        )
                        console.print(
                            f"  [red]Fail[/red] {title_text[:60]} "
                            f"[dim]- "
                            f"{detail if verbose else _whole_word_summary(detail)}[/dim]"
                        )
                checkpoint(
                    "probe",
                    status=(
                        "empty"
                        if probe_empty_reason
                        else
                        "completed_with_failures"
                        if probe_fail_count or probe_query_fail_count
                        else "completed"
                    ),
                    transcripts=transcript_record_count,
                    eligible_seeds=len(transcript_texts),
                    excluded_frontier=excluded_seed_count,
                    excluded_by_stage=excluded_seed_stages,
                    terms=len(terms),
                    queries=probe_queries_executed,
                    queries_planned=len(pair_rows),
                    queries_deferred=probe_queries_deferred,
                    query_failed=probe_query_fail_count,
                    reason=probe_empty_reason,
                    candidates=len(probe_candidates),
                    saved=probe_success,
                    download_failed=probe_fail_count,
                )
        else:
            checkpoint("probe", status="disabled")

        total_item_failure = (
            selected_count > 0
            and fail_count >= selected_count
            and success_count + skip_count + dedupe_count + probe_success == 0
        )
        run_status = (
            ResultStatus.FAILED.value
            if total_item_failure
            else ResultStatus.PARTIAL.value
            if (
                fail_count
                or probe_fail_count
                or probe_query_fail_count
                or search_partial
                or scout_error is not None
            )
            else ResultStatus.COMPLETED.value
        )
        result_data = aggregate_data()
        outcome = CommandResult(
            command="research",
            status=run_status,
            data=result_data,
            errors=aggregate_errors(),
        )
        outcome = record_result(outcome)
        if not raw:
            _render_research_result(outcome)
            if total_item_failure:
                _command_error(
                    f"All {fail_count} selected transcript downloads failed."
                )
        return outcome

    except KeyboardInterrupt as error:
        run_status = "interrupted"
        run_error = f"Interrupted during {phase}"
        checkpoint(
            phase,
            status="interrupted",
            saved=success_count,
            failed=fail_count,
            selected=selected_count,
        )
        outcome = failure_result(error, status=ResultStatus.INTERRUPTED)
        if raw:
            return outcome
        raise click.Abort()
    except click.ClickException as error:
        run_error = str(error)
        outcome = failure_result(error)
        if raw:
            return outcome
        raise
    except FilmotAPIContractError as error:
        run_error = f"Invalid Filmot API response: {error}"
        outcome = failure_result(error)
        if raw:
            return outcome
        _command_error(run_error)
    except ValueError as error:
        run_error = f"Configuration error: {error}"
        outcome = failure_result(error)
        if raw:
            return outcome
        _command_error(run_error)
    except Exception as error:
        run_error = f"{type(error).__name__}: {error}"
        outcome = failure_result(error)
        if raw:
            return outcome
        _command_error(run_error)
    finally:
        log_event(
            "research_end",
            topic=normalized_topic,
            run_id=run_id,
            status=run_status,
            phase=phase,
            error=run_error,
            fallback_stage=fallback_stage,
            scout=len(scout_videos),
            filmot_total=total,
            selected=selected_count,
            saved=success_count,
            skipped=skip_count,
            failed=fail_count,
            deduped=dedupe_count,
            probe=probe_success,
            probe_failed=probe_fail_count,
            probe_query_failed=probe_query_fail_count,
            chars=total_chars + probe_chars,
            routing_plan=route_plan,
        )


# ========== CHANNEL DOWNLOAD ==========
