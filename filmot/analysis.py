"""Deterministic, local analysis helpers for saved transcript corpora."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

from .library import normalize_topic_name
from .paths import (
    project_data_dir,
    publish_file_exclusive,
    wait_for_file_publication,
)
from .schemas import ECHO_ANALYSIS_SCHEMA


ECHO_METHOD = "word-ngram-jaccard"
ECHO_METHOD_VERSION = "2"


def _artifact_digest(payload: Dict[str, Any]) -> str:
    """Hash canonical artifact content, excluding the self-describing hash."""
    content = {
        str(key): value
        for key, value in payload.items()
        if key != "artifact_hash"
    }
    canonical = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _tokens(text: str) -> List[str]:
    """Return Unicode-aware word/number tokens with stable normalization."""
    normalized = unicodedata.normalize("NFKC", str(text or "")).casefold()

    def is_cjk(char: str) -> bool:
        codepoint = ord(char)
        return (
            0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
            or 0x20000 <= codepoint <= 0x2EE5F
            or 0x2F800 <= codepoint <= 0x2FA1F
            or 0x30000 <= codepoint <= 0x323AF
            or 0x1100 <= codepoint <= 0x11FF
            or 0x3040 <= codepoint <= 0x30FF
            or 0x3100 <= codepoint <= 0x312F
            or 0x3130 <= codepoint <= 0x318F
            or 0x31A0 <= codepoint <= 0x31BF
            or 0xA960 <= codepoint <= 0xA97F
            or 0xAC00 <= codepoint <= 0xD7AF
            or 0xD7B0 <= codepoint <= 0xD7FF
            or 0x1AFF0 <= codepoint <= 0x1AFFF
            or 0x1B000 <= codepoint <= 0x1B16F
            or 0xFF65 <= codepoint <= 0xFF9F
        )

    tokens: List[str] = []
    buffer: List[str] = []

    def flush_buffer() -> None:
        if buffer:
            tokens.extend(
                re.findall(
                    r"[^\W_]+",
                    "".join(buffer),
                    flags=re.UNICODE,
                )
            )
            buffer.clear()

    for char in normalized:
        if is_cjk(char):
            flush_buffer()
            tokens.append(char)
        else:
            buffer.append(char)
    flush_buffer()
    return tokens


def _shingles(text: str, ngram: int) -> set:
    words = _tokens(text)
    if len(words) < ngram:
        return set()
    return {
        " ".join(words[index:index + ngram])
        for index in range(len(words) - ngram + 1)
    }


def analyze_echoes(
    sources: Iterable[Dict[str, Any]],
    *,
    ngram: int = 5,
    threshold: float = 0.5,
    include_pair_rows: bool = True,
    topic: Optional[str] = None,
) -> Dict[str, Any]:
    """Compare full source texts and return all pair scores plus clusters.

    The result is advisory lineage evidence, not a claim that one source
    copied another. Cluster construction uses deterministic single linkage;
    complete pair rows make bridge relationships visible to the analyst.
    Transient callers that only need clusters may disable pair rows to avoid
    retaining quadratic output; the public library analysis keeps the default.
    """
    if ngram < 1:
        raise ValueError("ngram must be at least 1")
    if not math.isfinite(threshold) or threshold <= 0 or threshold > 1:
        raise ValueError("threshold must be greater than 0 and at most 1")

    ordered = []
    source_ids = set()
    for source in sources:
        source_id = str(source.get("source_id") or source.get("video_id") or "")
        if not source_id:
            raise ValueError("Every echo-analysis source requires source_id")
        if source_id in source_ids:
            raise ValueError("Duplicate echo-analysis source_id: {}".format(source_id))
        source_ids.add(source_id)
        text = str(source.get("text") or "")
        grams = _shingles(text, ngram)
        ordered.append({
            "source_id": source_id,
            "title": str(source.get("title") or ""),
            "channel": str(source.get("channel") or ""),
            "url": str(source.get("url") or ""),
            "language": str(source.get("language") or ""),
            "transcript_source": str(source.get("transcript_source") or ""),
            "saved_at": str(source.get("saved_at") or ""),
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "shingle_count": len(grams),
            "_shingles": grams,
        })
    ordered.sort(key=lambda item: item["source_id"])

    parent = list(range(len(ordered)))
    component_matched_pairs = [0] * len(ordered)
    component_strongest_scores = [0.0] * len(ordered)

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int, score: float) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            component_matched_pairs[left_root] += 1
            component_strongest_scores[left_root] = max(
                component_strongest_scores[left_root], score
            )
            return
        root, child = sorted((left_root, right_root))
        parent[child] = root
        component_matched_pairs[root] = (
            component_matched_pairs[left_root]
            + component_matched_pairs[right_root]
            + 1
        )
        component_strongest_scores[root] = max(
            component_strongest_scores[left_root],
            component_strongest_scores[right_root],
            score,
        )

    pairs = []
    pair_count = 0
    matched_pair_count = 0
    for left in range(len(ordered)):
        left_grams = ordered[left]["_shingles"]
        for right in range(left + 1, len(ordered)):
            pair_count += 1
            right_grams = ordered[right]["_shingles"]
            if include_pair_rows:
                intersection = left_grams & right_grams
                shared_count = len(intersection)
            else:
                smaller, larger = (
                    (left_grams, right_grams)
                    if len(left_grams) <= len(right_grams)
                    else (right_grams, left_grams)
                )
                shared_count = sum(1 for gram in smaller if gram in larger)
                intersection = None
            union_count = len(left_grams) + len(right_grams) - shared_count
            score = shared_count / union_count if union_count else 0.0
            matched = bool(shared_count) and score >= threshold
            if matched:
                matched_pair_count += 1
                union(left, right, round(score, 6))
            if include_pair_rows:
                pairs.append({
                    "source_a": ordered[left]["source_id"],
                    "source_b": ordered[right]["source_id"],
                    "score": round(score, 6),
                    "matched": matched,
                    "shared_shingles": shared_count,
                    "union_shingles": union_count,
                    "representative_shingles": heapq.nsmallest(
                        5,
                        intersection or (),
                    ),
                })

    grouped: Dict[int, List[str]] = {}
    for index, source in enumerate(ordered):
        grouped.setdefault(find(index), []).append(source["source_id"])
    clusters = []
    for root, members in sorted(
        (
            (root, sorted(items))
            for root, items in grouped.items()
            if len(items) >= 2
        ),
        key=lambda item: tuple(item[1]),
    ):
        cluster_id = "echo-{}".format(
            hashlib.sha256(
                "{}\x1f{}\x1f{}".format(ngram, threshold, "\x1f".join(members)).encode(
                    "utf-8"
                )
            ).hexdigest()[:12]
        )
        clusters.append({
            "cluster_id": cluster_id,
            "source_ids": members,
            "matched_pairs": component_matched_pairs[root],
            "strongest_score": component_strongest_scores[root],
        })

    visible_sources = []
    for source in ordered:
        visible_sources.append({
            key: value for key, value in source.items() if key != "_shingles"
        })
    result = {
        "schema": ECHO_ANALYSIS_SCHEMA,
        "method": {
            "name": ECHO_METHOD,
            "version": ECHO_METHOD_VERSION,
            "unicode_version": unicodedata.unidata_version,
            "ngram": ngram,
            "threshold": threshold,
            "normalization": (
                "Unicode NFKC + casefold + Unicode word tokens; pinned Han, "
                "Kana, Bopomofo, and Hangul ranges use character tokens"
            ),
            "cjk_range_version": "east-asian-blocks/v2",
            "linkage": "single",
            "interpretation": (
                "Shared phrasing is an advisory lineage/reuse candidate, not "
                "proof of copying, dependence, credibility, falsity, or truth."
            ),
        },
        "sources": visible_sources,
        "pairs": pairs,
        "clusters": clusters,
        "summary": {
            "sources": len(visible_sources),
            "pairs": pair_count,
            "matched_pairs": matched_pair_count,
            "clusters": len(clusters),
            "clustered_sources": sum(len(cluster["source_ids"]) for cluster in clusters),
        },
    }
    if topic is not None:
        result["topic"] = normalize_topic_name(topic, fallback="analysis")
    result["artifact_hash"] = _artifact_digest(result)
    return result


def persist_echo_analysis(
    topic: str,
    analysis: Dict[str, Any],
    *,
    data_dir: Optional[Union[str, Path]] = None,
) -> Path:
    """Persist and verify one content-addressed artifact without overwriting.

    The digest commits to every stored field except the self-describing
    ``artifact_hash`` itself. Persistence adds the normalized topic to a copy
    when needed and verifies an existing logical artifact before reusing it.
    Callers that expose the analysis hash should pass ``topic`` to
    :func:`analyze_echoes` so inspected and persisted identities are identical.
    """
    supplied_hash = str(analysis.get("artifact_hash") or "")
    if not supplied_hash:
        raise ValueError("Analysis has no artifact_hash")
    computed_hash = _artifact_digest(analysis)
    if supplied_hash != computed_hash:
        raise ValueError(
            "Analysis artifact_hash does not match its canonical content"
        )

    normalized_topic = normalize_topic_name(topic, fallback="analysis")
    artifact = dict(analysis)
    analysis_topic = artifact.get("topic")
    if (
        analysis_topic is not None
        and normalize_topic_name(str(analysis_topic), fallback="analysis")
        != normalized_topic
    ):
        raise ValueError("Analysis topic does not match persistence topic")
    artifact["topic"] = normalized_topic
    artifact_hash = _artifact_digest(artifact)
    artifact["artifact_hash"] = artifact_hash

    directory = (
        project_data_dir(data_dir)
        / "analysis"
        / normalized_topic
    )
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "echoes-{}.json".format(artifact_hash[:12])

    def verify_existing() -> None:
        try:
            with destination.open("r", encoding="utf-8") as handle:
                existing = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(
                "Existing echo artifact is unreadable or invalid: {}".format(
                    destination
                )
            ) from error
        if not isinstance(existing, dict):
            raise ValueError(
                "Existing echo artifact is not a JSON object: {}".format(
                    destination
                )
            )
        existing_hash = str(existing.get("artifact_hash") or "")
        if (
            existing_hash != artifact_hash
            or _artifact_digest(existing) != artifact_hash
            or existing != artifact
        ):
            raise ValueError(
                "Existing echo artifact does not match its content address: "
                "{}".format(destination)
            )

    wait_for_file_publication(destination)
    if destination.exists():
        verify_existing()
        return destination

    temporary = directory / ".echoes-{}.{}.tmp".format(
        artifact_hash[:12], uuid.uuid4().hex
    )
    try:
        descriptor = os.open(
            str(temporary),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                artifact,
                handle,
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        publish_file_exclusive(temporary, destination)
        verify_existing()
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
    return destination
