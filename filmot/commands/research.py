"""Staged research workflow and relationship-probe command."""

import json as json_mod
import math
import os
import re
from typing import Optional

import click
from rich.progress import Progress, SpinnerColumn, TextColumn

from ..api_contract import FilmotAPIContractError
from ..api import FilmotClient
from ..cli_support import (
    command_error as _command_error,
    console,
    route_progress_for as _route_progress_for,
    transcript_failure_detail as _transcript_failure_detail,
    whole_word_summary as _whole_word_summary,
)
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
    for _, _, is_bigram, term in candidates:
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
        if is_bigram:
            seen_words.update(term.split())
        if len(terms) >= top_n:
            break

    return terms


def _find_probe_pairs(texts, terms, window_size=50, max_pairs=5):
    """Find co-occurring term pairs within text windows.

    Windows never cross transcript or sentence boundaries. Returns
    ``(term1, term2, co_window_count, supporting_source_count)``.
    """
    import re
    from collections import Counter, defaultdict

    # Build lookup: word -> set of terms it belongs to
    word_to_terms = {}
    for term in terms:
        for w in term.split():
            word_to_terms.setdefault(w, set()).add(term)

    # Slide through text in overlapping windows (inclusive of the tail)
    pair_counts = Counter()
    pair_sources = defaultdict(set)
    step = max(window_size // 2, 1)
    for source_index, text in enumerate(texts):
        for sentence in re.split(r"(?:[.!?]+|\r?\n+)", text.lower()):
            words = _probe_words(sentence)
            if not words:
                continue
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

    # Filter: skip pairs where terms share any word (e.g., "president vladimir" + "vladimir putin")
    # Rank by specificity first (multi-word terms beat frequent generic singles), then count
    candidates = []
    for (t1, t2), count in pair_counts.items():
        if count < 2:
            continue
        if len(texts) >= 2 and len(pair_sources[(t1, t2)]) < 2:
            continue
        words_t1 = set(t1.split())
        words_t2 = set(t2.split())
        if words_t1 & words_t2:
            continue  # overlapping terms, skip
        specificity = (len(words_t1) > 1) + (len(words_t2) > 1)
        candidates.append((
            len(pair_sources[(t1, t2)]),
            specificity,
            count,
            t1,
            t2,
        ))

    candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return [
        (t1, t2, count, source_count)
        for source_count, _, count, t1, t2 in candidates[:max_pairs]
    ]


def _probe_topic_words(topic: str) -> list[str]:
    """Significant topic words used to relevance-check probe results."""
    return [
        word
        for word in _probe_words(topic)
        if word not in _PROBE_STOPWORDS
        and len(word) >= (4 if word.isascii() else 2)
    ]


def _probe_hit_is_relevant(video: dict, topic_words: list[str]) -> bool:
    """True if a probe result mentions the research topic in its title or hit context."""
    if not topic_words:
        return True
    parts = [video.get("title", "")]
    for hit in video.get("hits", [])[:10]:
        parts.append(hit.get("ctx_before", ""))
        parts.append(hit.get("token", ""))
        parts.append(hit.get("ctx_after", ""))
        for line in hit.get("lines", [])[:3]:
            parts.append(line if isinstance(line, str) else str(line.get("text", "")))
    text = " ".join(parts).casefold()
    return any(_topic_token_present(text, word) for word in topic_words)


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
        origin = "scout" if video.get("_from_scout") else video.get("_fallback_stage", "filmot")
        console.print(
            f"  {index}. {str(video.get('title', 'Unknown'))[:66]} "
            f"[dim]({video.get('channelname', 'Unknown')})[/dim]\n"
            f"     [dim]stage={origin}; relevance={selection.get('passage_coverage', 0):.2f}; "
            f"source-prior={selection.get('source_signal', 0):.2f}; "
            f"density={selection.get('density', 0):.2f}/min"
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
        f'  filmot sessions {data["topic"]} --summary'
    )
    console.print(
        f'  filmot library context {data["topic"]} -o context.txt'
    )


# ========== RESEARCH COMMAND ==========

@click.command("research")
@click.argument("topic")
@click.option("--depth", "-n", default=10, type=click.IntRange(0), show_default=True,
              help="Number of transcripts to download")
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
              help="Extract cross-source entities and run transparent NEAR/N probes")
@click.option("--no-proxy", is_flag=True, help="Bypass proxy for transcript downloads")
@click.option("--verbose", is_flag=True, help="Show full transcript failure details")
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
):
    """Research TOPIC with staged search, visible selection, and checkpoints.

    The search ladder tries title+transcript, an exact phrase, and NEAR/N
    before a loose transcript-wide query. A loose result set above
    ``--broad-threshold`` is never downloaded unless ``--accept-broad`` is
    explicit. Density is topical concentration, not source credibility.

    \b
      filmot research "deep sea mining"
      filmot research "AI data center electricity demand" --candidate-pages 5
      filmot research "niche topic" --accept-broad --probe
      filmot research "fusion" --channel "International Energy Agency"
    """
    import hashlib
    import uuid

    from ..ledger import log_event, log_result
    from ..library import get_library
    from ..transcript import (
        describe_routing_plan,
        disable_proxy,
        get_transcript,
        get_transcript_with_fallback,
        routing_plan,
    )

    library = get_library()
    normalized_topic = library._normalize_topic(topic)
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

    def aggregate_data() -> ResearchResultData:
        """Build the one result payload used by renderer and ledger."""
        return {
            "run_id": run_id,
            "topic": normalized_topic,
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
            "sources": library.list_transcripts(normalized_topic),
            "routing_plan": route_plan or {},
        }

    def aggregate_errors() -> list[ErrorDetail]:
        errors = []
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
            checkpoint(
                "scout",
                status="started",
                query=topic,
                days=scout_days,
                channel_id=effective_channel_ids,
            )
            try:
                from ..youtube_search import search_recent, validate_youtube_api

                validate_youtube_api()
                scout_allowed_ids = set(
                    (effective_channel_ids or "").split(",")
                ) - {""}
                scout_channel_id = (
                    next(iter(scout_allowed_ids))
                    if len(scout_allowed_ids) == 1
                    else None
                )
                with console.status(
                    f"[bold cyan]Scouting YouTube for '{topic}' "
                    f"(last {scout_days} days)...[/bold cyan]"
                ):
                    scout_videos = search_recent(
                        query=topic,
                        days_back=scout_days,
                        max_results=10,
                        order="relevance",
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

        # Phase 2: relationship-preserving search ladder. A stage only stops
        # the ladder when it contains candidates that pass the download gate.
        results = run_search("title+transcript", topic, title_filter=topic)
        title_filter_works = bool(_result_videos(results))
        videos = eligible_stage_candidates(results, "title+transcript")
        fallback_stage = "title_transcript"

        if not videos:
            for stage, query in _research_query_ladder(topic):
                console.print(
                    f"[dim]No title+transcript candidates; trying {stage}: {query}[/dim]"
                )
                results = run_search(stage, query)
                videos = eligible_stage_candidates(results, stage)
                videos = relationship_evidence_gate(videos, stage)
                fallback_stage = stage
                if videos:
                    break

        broad_blocked = False
        if not videos:
            console.print(
                "[dim]Relationship-preserving stages returned no candidates; "
                "measuring loose transcript-wide fallback...[/dim]"
            )
            results = run_search("broad_loose", topic)
            videos = eligible_stage_candidates(results, "broad_loose")
            fallback_stage = "broad_loose"
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

        total = int(results.get("totalresultcount", len(videos)) or 0)
        for video in videos:
            video["_fallback_stage"] = fallback_stage

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
                or (
                    video["_selection"]["passage_coverage"] >= 0.50
                    and video["_selection"]["token_coverage"] >= 0.60
                )
            ]
            scout_after = sum(
                bool(video.get("_from_scout"))
                for video in videos
            )
            console.print(
                f"[dim]Scout relevance gate: {scout_before} -> "
                f"{scout_after} candidates (topic terms must occur together "
                "in the title or description)[/dim]"
            )
            checkpoint(
                "scout_gate",
                status="completed",
                candidates_before=scout_before,
                candidates_after=scout_after,
                threshold={
                    "passage_coverage": 0.50,
                    "token_coverage": 0.60,
                },
            )
        accepted_scouts = any(
            video.get("_from_scout")
            for video in videos
        )
        if broad_blocked and not accepted_scouts:
            _command_error(
                f"Broad fallback blocked at {total:,} results. Refine TOPIC or "
                "rerun with --accept-broad after reviewing the risk."
            )
        if not videos:
            run_status = (
                ResultStatus.PARTIAL.value
                if search_partial
                else ResultStatus.EMPTY.value
            )
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
            log_result(
                "research",
                outcome,
                topic=normalized_topic,
                data=outcome.data,
            )
            _render_research_result(outcome)
            return

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

        scout_candidates = [video for video in videos if video.get("_from_scout")]
        if scout_candidates:
            scout_slots = min(len(scout_candidates), max(1, depth // 3)) if depth else 0
            filmot_candidates = [video for video in videos if not video.get("_from_scout")]
            videos_to_download = (
                filmot_candidates[: max(depth - scout_slots, 0)]
                + scout_candidates[:scout_slots]
            )
            if len(videos_to_download) < depth:
                selected_ids = {
                    video.get("id") or video.get("videoid")
                    for video in videos_to_download
                }
                videos_to_download.extend(
                    video for video in videos
                    if (video.get("id") or video.get("videoid")) not in selected_ids
                )
                videos_to_download = videos_to_download[:depth]
        else:
            videos_to_download = videos[:depth]
        selected_count = len(videos_to_download)
        checkpoint(
            "selection",
            status="completed",
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

        console.print(
            f"\n[bold]Downloading {len(videos_to_download)} transcript(s)...[/bold]"
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
            source = "scout" if video.get("_from_scout") else fallback_stage
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
            transcript_texts = []
            for item in library.list_transcripts(normalized_topic):
                data = library.get(item["video_id"], normalized_topic)
                if data and data.get("transcript"):
                    transcript_texts.append(data["transcript"])

            if len(transcript_texts) < 2:
                checkpoint(
                    "probe",
                    status="skipped",
                    reason="insufficient_transcripts",
                    transcripts=len(transcript_texts),
                )
                console.print(
                    f"[dim]Probe: Need at least 2 transcripts; got "
                    f"{len(transcript_texts)}.[/dim]"
                )
            else:
                terms = _extract_probe_terms(transcript_texts, topic)
                pairs = _find_probe_pairs(transcript_texts, terms)
                console.print(
                    f"\n[bold]Probing relationships from "
                    f"{len(transcript_texts)} transcript(s)...[/bold]"
                )
                console.print(
                    f"  Entities: [cyan]{', '.join(terms[:8]) or 'none'}[/cyan]"
                )
                existing_ids = {
                    item["video_id"]
                    for item in library.list_transcripts(normalized_topic)
                }
                probe_candidates = []
                topic_words = _probe_topic_words(topic)
                probe_title = topic if title_filter_works else None

                for index, (term1, term2, co_windows, source_support) in enumerate(
                    pairs, 1
                ):
                    probe_query = f'"{term1}" NEAR/15 "{term2}"'
                    effective_scope = {
                        "title": probe_title,
                        "channel_id": effective_channel_ids,
                        "lang": lang or "en",
                    }
                    checkpoint(
                        "probe_search",
                        status="started",
                        index=index,
                        query=probe_query,
                        constraints=effective_scope,
                        co_windows=co_windows,
                        source_support=source_support,
                    )
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
                            status="failed",
                            error=detail,
                        )
                        checkpoint(
                            "probe_search",
                            status="failed",
                            index=index,
                            query=probe_query,
                            constraints=effective_scope,
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
                            status="failed",
                            error=detail,
                        )
                        checkpoint(
                            "probe_search",
                            status="failed",
                            index=index,
                            query=probe_query,
                            constraints=effective_scope,
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
                                outside_results=len(outside),
                            )
                            log_event(
                                "research_probe",
                                topic=normalized_topic,
                                run_id=run_id,
                                query=probe_query,
                                constraints=effective_scope,
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
                    if not probe_title:
                        scoped_hits = [
                            video for video in scoped_hits
                            if _probe_hit_is_relevant(video, topic_words)
                        ]
                    api_total = int(
                        probe_result.get("totalresultcount", len(raw_hits)) or 0
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
                        status="completed",
                        api_total=api_total,
                        returned=len(raw_hits),
                        scoped=len(scoped_hits),
                        peak_density=round(peak_density, 3),
                    )
                    checkpoint(
                        "probe_search",
                        status="completed",
                        index=index,
                        query=probe_query,
                        constraints=effective_scope,
                        api_total=api_total,
                        returned=len(raw_hits),
                        scoped=len(scoped_hits),
                    )
                    console.print(
                        f"  Probe {index}: {probe_query} "
                        f"[dim](co-windows:{co_windows}; sources:{source_support}; "
                        f"scope:{scope_label})[/dim] → "
                        f"[green]{api_total:,} API results[/green]; "
                        f"{len(raw_hits)} returned; {len(scoped_hits)} new+scoped; "
                        f"peak {peak_density:.1f}/min"
                    )
                    ranked_probe = _rank_research_candidates(
                        scoped_hits, topic, "balanced"
                    )
                    for video in ranked_probe[:2]:
                        video_id = video.get("id") or video.get("videoid")
                        if video_id not in existing_ids:
                            probe_candidates.append(video)
                            existing_ids.add(video_id)

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
                        "completed_with_failures"
                        if probe_fail_count or probe_query_fail_count
                        else "completed"
                    ),
                    transcripts=len(transcript_texts),
                    terms=len(terms),
                    queries=len(pairs),
                    query_failed=probe_query_fail_count,
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
        # The aggregate result and its human rendering now consume one typed
        # accounting object. Checkpoints remain smaller phase events.
        log_result(
            "research",
            outcome,
            topic=normalized_topic,
            data=result_data,
        )
        _render_research_result(outcome)
        if total_item_failure:
            _command_error(
                f"All {fail_count} selected transcript downloads failed."
            )

    except KeyboardInterrupt:
        run_status = "interrupted"
        run_error = f"Interrupted during {phase}"
        checkpoint(
            phase,
            status="interrupted",
            saved=success_count,
            failed=fail_count,
            selected=selected_count,
        )
        raise click.Abort()
    except click.ClickException as error:
        run_error = str(error)
        raise
    except FilmotAPIContractError as error:
        run_error = f"Invalid Filmot API response: {error}"
    except ValueError as error:
        run_error = f"Configuration error: {error}"
        _command_error(run_error)
    except Exception as error:
        run_error = f"{type(error).__name__}: {error}"
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
