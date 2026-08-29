"""Reproducible full-transcript echo-analysis contracts."""

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from click.testing import CliRunner

from filmot.analysis import analyze_echoes, persist_echo_analysis
from filmot.cli import cli
from filmot.library import TranscriptLibrary


def test_unicode_echo_analysis_is_deterministic_and_order_stable():
    shared = "研究者は新しい治療法の結果を詳しく報告した"
    sources = [
        {"source_id": "b", "text": shared},
        {"source_id": "a", "text": shared},
        {"source_id": "c", "text": "料理と庭園に関する別の話題です"},
    ]

    first = analyze_echoes(sources, ngram=3, threshold=0.5)
    second = analyze_echoes(list(reversed(sources)), ngram=3, threshold=0.5)

    assert first == second
    assert first["artifact_hash"] == second["artifact_hash"]
    assert first["summary"]["matched_pairs"] == 1
    assert first["clusters"][0]["source_ids"] == ["a", "b"]
    pair = next(row for row in first["pairs"] if row["matched"])
    assert pair["score"] == 1.0
    assert pair["representative_shingles"]


def test_echo_analysis_retains_all_pair_scores_and_method_parameters():
    result = analyze_echoes(
        [
            {"source_id": "a", "text": "one two three four five six"},
            {"source_id": "b", "text": "one two three four five seven"},
            {"source_id": "c", "text": "unrelated words about a garden"},
        ],
        ngram=2,
        threshold=0.4,
    )

    assert len(result["pairs"]) == 3
    assert result["method"]["name"] == "word-ngram-jaccard"
    assert result["method"]["version"] == "2"
    assert result["method"]["unicode_version"]
    assert result["method"]["ngram"] == 2
    assert result["method"]["threshold"] == 0.4
    assert "not proof" in result["method"]["interpretation"]


def test_extension_b_han_uses_pinned_character_tokens():
    text = "".join(chr(0x20000 + index) for index in range(8))

    result = analyze_echoes(
        [
            {"source_id": "a", "text": text},
            {"source_id": "b", "text": text},
        ],
        ngram=5,
    )

    assert result["sources"][0]["shingle_count"] == 4
    assert result["pairs"][0]["score"] == 1.0
    assert result["method"]["cjk_range_version"] == "east-asian-blocks/v2"


def test_internal_cluster_scan_can_omit_quadratic_pair_rows():
    result = analyze_echoes(
        [
            {"source_id": "a", "text": "one two three four five"},
            {"source_id": "b", "text": "one two three four five"},
            {"source_id": "c", "text": "other unrelated source words here"},
        ],
        include_pair_rows=False,
    )

    assert result["pairs"] == []
    assert result["summary"]["pairs"] == 3
    assert result["summary"]["matched_pairs"] == 1
    assert result["clusters"][0]["source_ids"] == ["a", "b"]


def test_non_finite_threshold_is_rejected_as_standard_raw_json(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="threshold"):
        analyze_echoes([], threshold=float("nan"))

    monkeypatch.chdir(tmp_path)
    command = CliRunner().invoke(
        cli,
        ["library", "echoes", "empty", "--threshold", "nan", "--raw"],
    )

    assert command.exit_code == 1
    assert "NaN" not in command.stdout
    assert json.loads(command.stdout)["_filmot"]["status"] == "failed"


def test_content_addressed_echo_artifact_is_reused(tmp_path):
    result = analyze_echoes(
        [
            {"source_id": "a", "text": "one two three four five"},
            {"source_id": "b", "text": "one two three four five"},
        ],
        topic="Science",
    )
    original_result = json.loads(json.dumps(result))

    first = persist_echo_analysis("Science", result, data_dir=tmp_path)
    original = first.read_text(encoding="utf-8")
    second = persist_echo_analysis("Science", result, data_dir=tmp_path)

    assert second == first
    assert result == original_result
    assert second.read_text(encoding="utf-8") == original
    assert result["artifact_hash"][:12] in first.name
    stored = json.loads(original)
    assert stored["schema"] == "filmot.echo-analysis/v1"
    assert stored["topic"] == "science"
    content = dict(stored)
    stored_hash = content.pop("artifact_hash")
    canonical = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert stored_hash == hashlib.sha256(canonical).hexdigest()


def test_echo_persistence_rejects_stale_caller_hash(tmp_path):
    result = analyze_echoes([
        {"source_id": "a", "text": "one two three four five"},
    ])
    result["summary"]["sources"] = 99

    with pytest.raises(ValueError, match="artifact_hash"):
        persist_echo_analysis("science", result, data_dir=tmp_path)

    assert not (tmp_path / "analysis").exists()


def test_echo_persistence_refuses_corrupt_existing_artifact(tmp_path):
    result = analyze_echoes([
        {"source_id": "a", "text": "one two three four five"},
    ])
    destination = persist_echo_analysis("science", result, data_dir=tmp_path)
    destination.write_text("{not-json\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unreadable or invalid"):
        persist_echo_analysis("science", result, data_dir=tmp_path)


def test_echo_persistence_falls_back_when_hard_links_are_unavailable(
    tmp_path,
    monkeypatch,
):
    result = analyze_echoes(
        [{"source_id": "a", "text": "one two three four five"}],
        topic="science",
    )
    def no_hard_links(*_args):
        raise OSError("no hard links")

    monkeypatch.setattr("filmot.paths.os.link", no_hard_links)

    destination = persist_echo_analysis("science", result, data_dir=tmp_path)

    assert json.loads(destination.read_text(encoding="utf-8")) == result


def test_no_hardlink_fallback_hides_partial_file_from_concurrent_writer(
    tmp_path,
    monkeypatch,
):
    result = analyze_echoes(
        [{"source_id": "a", "text": "one two three four five"}],
        topic="science",
    )
    publication_started = threading.Event()
    release_publication = threading.Event()

    def no_hard_links(*_args):
        raise OSError("no hard links")

    from filmot import paths as paths_module

    original_replace = paths_module.os.replace

    def paused_replace(source, destination):
        publication_started.set()
        assert release_publication.wait(timeout=5)
        original_replace(source, destination)

    monkeypatch.setattr(paths_module.os, "link", no_hard_links)
    monkeypatch.setattr(paths_module.os, "replace", paused_replace)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            persist_echo_analysis,
            "science",
            result,
            data_dir=tmp_path,
        )
        assert publication_started.wait(timeout=5)
        second = pool.submit(
            persist_echo_analysis,
            "science",
            result,
            data_dir=tmp_path,
        )
        assert not second.done()
        release_publication.set()
        first_path = first.result(timeout=5)
        second_path = second.result(timeout=5)

    assert first_path == second_path
    assert json.loads(first_path.read_text(encoding="utf-8")) == result
    assert not list(first_path.parent.glob("*.publish.lock"))


def test_persistent_publication_guard_is_not_a_stale_lock(tmp_path, monkeypatch):
    result = analyze_echoes(
        [{"source_id": "a", "text": "one two three four five"}],
        topic="science",
    )
    directory = tmp_path / "analysis" / "science"
    directory.mkdir(parents=True)
    (directory / ".filmot-publish.guard").write_text(
        "left by a terminated process",
        encoding="utf-8",
    )

    def no_hard_links(*_args):
        raise OSError("no hard links")

    monkeypatch.setattr("filmot.paths.os.link", no_hard_links)

    destination = persist_echo_analysis("science", result, data_dir=tmp_path)

    assert json.loads(destination.read_text(encoding="utf-8")) == result


def test_library_echoes_is_read_only_until_persisted(tmp_path, monkeypatch):
    data_dir = tmp_path / ".filmot_data"
    library = TranscriptLibrary(data_dir)
    text = "scientists reported a repeated sequence of words in this source"
    library.save("abcdefghijk", "science", text)
    library.save("lmnopqrstuv", "science", text)
    monkeypatch.setattr("filmot.library.get_library", lambda: library)

    inspected = CliRunner().invoke(
        cli,
        ["library", "echoes", "science", "--raw"],
    )

    assert inspected.exit_code == 0, inspected.output
    payload = json.loads(inspected.stdout)
    assert payload["summary"]["matched_pairs"] == 1
    assert payload["rows"][0]["representative_shingles"]
    assert not (data_dir / "analysis").exists()
    assert not (data_dir / "sessions").exists()

    persisted = CliRunner().invoke(
        cli,
        ["library", "echoes", "science", "--persist", "--raw"],
    )
    assert persisted.exit_code == 0, persisted.output
    persisted_payload = json.loads(persisted.stdout)
    artifact = persisted_payload["artifact"]
    assert persisted_payload["artifact_hash"] == payload["artifact_hash"]
    assert artifact.endswith(".json")
    assert (data_dir / "analysis" / "science").exists()
    assert not (data_dir / "sessions").exists()


def test_library_echoes_fails_closed_on_unreadable_corpus_record(
    tmp_path,
    monkeypatch,
):
    data_dir = tmp_path / ".filmot_data"
    library = TranscriptLibrary(data_dir)
    library.save("abcdefghijk", "science", "one two three four five")
    corrupt = data_dir / "transcripts" / "science" / "broken.json"
    corrupt.write_text("{not-json\n", encoding="utf-8")
    monkeypatch.setattr("filmot.library.get_library", lambda: library)

    command = CliRunner().invoke(
        cli,
        ["library", "echoes", "science", "--raw"],
    )

    assert command.exit_code == 1
    payload = json.loads(command.stdout)
    assert payload["_filmot"]["status"] == "failed"
    assert payload["_filmot"]["errors"][0]["stage"] == "read-corpus"
    assert "broken.json" in payload["_filmot"]["errors"][0]["message"]
    assert not (data_dir / "analysis").exists()


def test_library_echoes_persist_reports_corpus_failure_before_persistence(
    tmp_path,
    monkeypatch,
):
    data_dir = tmp_path / ".filmot_data"
    corrupt = data_dir / "transcripts" / "science" / "broken.json"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("{not-json\n", encoding="utf-8")
    monkeypatch.setattr(
        "filmot.library.get_library",
        lambda: TranscriptLibrary(data_dir),
    )

    command = CliRunner().invoke(
        cli,
        ["library", "echoes", "science", "--persist", "--raw"],
    )

    assert command.exit_code == 1
    error = json.loads(command.stdout)["_filmot"]["errors"][0]
    assert error["stage"] == "read-corpus"
    assert not (data_dir / "analysis").exists()


def test_library_echoes_caps_human_pairs_but_raw_remains_complete(
    tmp_path,
    monkeypatch,
):
    library = TranscriptLibrary(tmp_path / ".filmot_data")
    text = "one two three four five six seven eight"
    for index in range(8):
        library.save("video{:06d}".format(index), "science", text)
    monkeypatch.setattr("filmot.library.get_library", lambda: library)
    runner = CliRunner()

    human = runner.invoke(cli, ["library", "echoes", "science"])
    raw = runner.invoke(cli, ["library", "echoes", "science", "--raw"])

    assert human.exit_code == 0, human.output
    assert "Showing 25 of 28 matched pairs" in human.output
    assert raw.exit_code == 0, raw.output
    assert len(json.loads(raw.stdout)["rows"]) == 28
