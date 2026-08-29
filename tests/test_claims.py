"""Strict claim-store and command contracts."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from filmot.claims import (
    ClaimStore,
    ClaimStoreError,
    _digest,
    _legacy_digest,
    default_claim_id,
)
from filmot.cli import cli


def test_unicode_claim_is_stable_and_exact_duplicate_is_idempotent(tmp_path):
    store = ClaimStore(tmp_path / ".filmot_data")

    first, created = store.add_claim("人工知能", "  薬剤 X は AMD を改善した  ")
    second, created_again = store.add_claim("人工知能", "薬剤 X は AMD を改善した")

    assert created is True
    assert created_again is False
    assert second["claim_id"] == first["claim_id"]
    assert second["text"] == "薬剤 X は AMD を改善した"
    assert len(list((tmp_path / ".filmot_data" / "claims" / "人工知能").glob("*.json"))) == 1


def test_default_claim_id_does_not_depend_on_runtime_unicode_tables():
    # U+A7F2 gained an NFKC/case-fold mapping in a newer Unicode database.
    # The v1 algorithm deliberately hashes its exact UTF-8 code point instead.
    assert default_claim_id(" \t\ua7f2\n") == "c-09105bd6e5ce"


def test_claim_declaration_records_id_method(tmp_path):
    store = ClaimStore(tmp_path / ".filmot_data")

    derived, _ = store.add_claim("science", "An exact statement")
    explicit, _ = store.add_claim(
        "science",
        "Another exact statement",
        claim_id="manual-claim",
    )

    assert derived["id_method"] == "utf8-ascii-whitespace/v1"
    assert explicit["id_method"] == "explicit"


def test_sequence_less_v1_history_remains_readable_and_appendable(tmp_path):
    data_dir = tmp_path / ".filmot_data"
    topic_dir = data_dir / "claims" / "science"
    topic_dir.mkdir(parents=True)
    claim_id = "legacy-claim"
    claim_event = {
        "schema": "filmot.claim/v1",
        "event_id": "ce-legacy-claim",
        "event_type": "claim",
        "ts": "2026-01-01T00:00:00Z",
        "topic": "science",
        "data": {
            "claim_id": claim_id,
            "text": "A legacy claim",
        },
    }
    assessment_event = {
        "schema": "filmot.claim/v1",
        "event_id": "ce-legacy-assessment",
        "event_type": "assessment",
        "ts": "2026-01-01T00:00:01Z",
        "topic": "science",
        "data": {
            "claim_id": claim_id,
            "assessment_id": _legacy_digest(
                "a",
                claim_id,
                "",
                "supported",
                "medium",
                "legacy review",
            ),
            "verdict": "supported",
            "confidence": "medium",
            "note": "legacy review",
        },
    }
    first_path = topic_dir / "legacy-claim.json"
    second_path = topic_dir / "legacy-assessment.json"
    first_path.write_text(json.dumps(claim_event), encoding="utf-8")
    second_path.write_text(json.dumps(assessment_event), encoding="utf-8")
    store = ClaimStore(data_dir)

    before = store.get_claim("science", claim_id)
    after, changed = store.assess(
        "science",
        claim_id,
        verdict="mixed",
        confidence="low",
        note="current review",
    )

    assert before is not None and before["verdict"] == "supported"
    assert changed is True
    assert after["verdict"] == "mixed"
    assert "sequence" not in json.loads(first_path.read_text(encoding="utf-8"))
    schemas = {
        event["schema"] for event in store._read_events("science")
    }
    assert schemas == {"filmot.claim/v1", "filmot.claim/v2"}


def test_supporting_and_contradicting_evidence_coexist(tmp_path):
    store = ClaimStore(tmp_path / ".filmot_data")
    claim, _ = store.add_claim("science", "The intervention improved vision")

    _, first, first_created = store.add_evidence(
        "science",
        claim["claim_id"],
        relation="supports",
        source="https://example.test/paper",
        source_kind="paper",
        locator="table 2",
        excerpt="Visual acuity improved.",
        primary=True,
        independence="independent",
    )
    _, duplicate, duplicate_created = store.add_evidence(
        "science",
        claim["claim_id"],
        relation="supports",
        source="https://example.test/paper",
        source_kind="paper",
        locator="table 2",
        excerpt="Visual acuity improved.",
        primary=True,
        independence="independent",
    )
    folded, _, _ = store.add_evidence(
        "science",
        claim["claim_id"],
        relation="contradicts",
        source="https://example.test/review",
        source_kind="paper",
        excerpt="The evidence remains inconclusive.",
        independence="unknown",
    )

    assert first_created is True
    assert duplicate_created is False
    assert duplicate["evidence_id"] == first["evidence_id"]
    assert len(folded["evidence"]) == 2
    assert folded["summary"]["relations"]["supports"] == 1
    assert folded["summary"]["relations"]["contradicts"] == 1
    assert folded["verdict"] == "open"  # never inferred from evidence counts


def test_structured_evidence_ids_do_not_have_separator_collisions(tmp_path):
    store = ClaimStore(tmp_path / ".filmot_data")
    claim, _ = store.add_claim("science", "Two locators remain distinct")

    _, first, _ = store.add_evidence(
        "science",
        claim["claim_id"],
        relation="supports",
        source="doi:10/example",
        source_kind="paper",
        locator="a\x1fb",
        excerpt="c",
    )
    folded, second, second_created = store.add_evidence(
        "science",
        claim["claim_id"],
        relation="supports",
        source="doi:10/example",
        source_kind="paper",
        locator="a",
        excerpt="b\x1fc",
    )

    assert second_created is True
    assert first["evidence_id"] != second["evidence_id"]
    assert first["id_method"] == "canonical-json-array/v2"
    assert len(folded["evidence"]) == 2


def test_video_evidence_has_timestamped_deep_link_and_echo_group(tmp_path):
    store = ClaimStore(tmp_path / ".filmot_data")
    claim, _ = store.add_claim("science", "A quoted statement appeared")

    folded, evidence, _ = store.add_evidence(
        "science",
        claim["claim_id"],
        relation="origin",
        source="https://youtube.com/watch?v=abcdefghijk",
        source_kind="video",
        video_id="abcdefghijk",
        start_seconds=312.9,
        independence="echo",
        lineage_group="wire-copy-1",
    )

    assert evidence["deep_link"].endswith("abcdefghijk&t=312s")
    assert folded["summary"]["echo_groups"] == 1
    assert folded["summary"]["independent_groups"] == 0


def test_video_timestamp_negative_zero_is_canonicalized(tmp_path):
    store = ClaimStore(tmp_path / ".filmot_data")
    claim, _ = store.add_claim("science", "A video starts at zero")

    folded, first, _ = store.add_evidence(
        "science",
        claim["claim_id"],
        relation="supports",
        source="https://youtube.com/watch?v=abcdefghijk",
        source_kind="video",
        video_id="abcdefghijk",
        start_seconds=-0.0,
    )
    folded, second, created = store.add_evidence(
        "science",
        claim["claim_id"],
        relation="supports",
        source="https://youtube.com/watch?v=abcdefghijk",
        source_kind="video",
        video_id="abcdefghijk",
        start_seconds=0.0,
    )

    assert created is False
    assert first["evidence_id"] == second["evidence_id"]
    assert first["start_seconds"] == 0.0
    assert len(folded["evidence"]) == 1


@pytest.mark.parametrize(
    "video_id",
    [
        "not a video",
        "abcdefghij",
        "https://youtu.be/abcdefghijk",
    ],
)
def test_store_rejects_malformed_video_identity(tmp_path, video_id):
    store = ClaimStore(tmp_path / ".filmot_data")
    claim, _ = store.add_claim("science", "A video identity is exact")

    with pytest.raises(ValueError, match="exactly 11"):
        store.add_evidence(
            "science",
            claim["claim_id"],
            relation="supports",
            source="https://youtube.com/watch?v={}".format(video_id),
            source_kind="video",
            video_id=video_id,
        )


def test_assessment_is_manual_and_supersedes_prior_state(tmp_path):
    store = ClaimStore(tmp_path / ".filmot_data")
    claim, _ = store.add_claim("science", "The result replicated")

    first, _ = store.assess(
        "science",
        claim["claim_id"],
        verdict="supported",
        confidence="medium",
        note="One direct replication",
    )
    second, changed = store.assess(
        "science",
        claim["claim_id"],
        verdict="mixed",
        confidence="low",
        note="A later study disagreed",
    )
    same, changed_again = store.assess(
        "science",
        claim["claim_id"],
        verdict="mixed",
        confidence="low",
        note="A later study disagreed",
    )

    assert first["verdict"] == "supported"
    assert second["verdict"] == "mixed"
    assert second["confidence"] == "low"
    assert changed is True
    assert changed_again is False
    assert same["assessment_id"] == second["assessment_id"]


def test_concurrent_assessments_serialize_without_forking_history(tmp_path):
    store = ClaimStore(tmp_path / ".filmot_data")
    claim, _ = store.add_claim("science", "The result replicated")
    first_append_entered = threading.Event()
    release_first_append = threading.Event()
    original_append = store._append

    def paused_first_append(topic, event_type, data):
        if event_type == "assessment" and not first_append_entered.is_set():
            first_append_entered.set()
            assert release_first_append.wait(timeout=5)
        return original_append(topic, event_type, data)

    store._append = paused_first_append
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            store.assess,
            "science",
            claim["claim_id"],
            verdict="supported",
            confidence="medium",
            note="first",
        )
        assert first_append_entered.wait(timeout=5)
        second = pool.submit(
            store.assess,
            "science",
            claim["claim_id"],
            verdict="contradicted",
            confidence="low",
            note="second",
        )
        assert not second.done()
        release_first_append.set()
        first.result(timeout=5)
        second.result(timeout=5)

    folded = store.get_claim("science", claim["claim_id"])
    assert folded is not None
    assert folded["verdict"] == "contradicted"
    assert folded["assessment_note"] == "second"


def test_claim_replay_uses_sequence_when_wall_clock_moves_backward(tmp_path):
    store = ClaimStore(tmp_path / ".filmot_data")
    with patch(
        "filmot.claims._now",
        side_effect=[
            "2026-01-01T00:00:10Z",
            "2026-01-01T00:00:20Z",
            "2026-01-01T00:00:15Z",
        ],
    ):
        claim, _ = store.add_claim("science", "The clock can move backward")
        store.assess(
            "science",
            claim["claim_id"],
            verdict="supported",
            confidence="medium",
            note="first",
        )
        final, _ = store.assess(
            "science",
            claim["claim_id"],
            verdict="contradicted",
            confidence="low",
            note="second",
        )

    assert final["verdict"] == "contradicted"
    assert [event["sequence"] for event in store._read_events("science")] == [1, 2, 3]


def test_tampered_assessment_supersedes_chain_is_rejected(tmp_path):
    data_dir = tmp_path / ".filmot_data"
    store = ClaimStore(data_dir)
    claim, _ = store.add_claim("science", "The result replicated")
    first, _ = store.assess(
        "science",
        claim["claim_id"],
        verdict="supported",
        confidence="medium",
        note="first",
    )
    store.assess(
        "science",
        claim["claim_id"],
        verdict="mixed",
        confidence="low",
        note="second",
    )
    paths = sorted((data_dir / "claims" / "science").glob("*.json"))
    second_path = next(
        path
        for path in paths
        if json.loads(path.read_text(encoding="utf-8")).get("data", {}).get(
            "supersedes"
        ) == first["assessment_id"]
    )
    event = json.loads(second_path.read_text(encoding="utf-8"))
    event["data"]["supersedes"] = "a-nonexistent"
    event["data"]["assessment_id"] = _digest(
        "a",
        claim["claim_id"],
        "a-nonexistent",
        event["data"]["verdict"],
        event["data"]["confidence"],
        event["data"]["note"],
    )
    second_path.write_text(json.dumps(event), encoding="utf-8")

    with pytest.raises(ClaimStoreError, match="does not supersede"):
        store.get_claim("science", claim["claim_id"])


def test_tampered_evidence_optional_field_type_is_rejected(tmp_path):
    data_dir = tmp_path / ".filmot_data"
    store = ClaimStore(data_dir)
    claim, _ = store.add_claim("science", "A source has metadata")
    store.add_evidence(
        "science",
        claim["claim_id"],
        relation="supports",
        source="doi:10/example",
        source_kind="paper",
        title="A paper",
    )
    event_path = next(
        path
        for path in (data_dir / "claims" / "science").glob("*.json")
        if json.loads(path.read_text(encoding="utf-8"))["event_type"] == "evidence"
    )
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["data"]["title"] = ["not", "text"]
    event_path.write_text(json.dumps(event), encoding="utf-8")

    with pytest.raises(ClaimStoreError, match="title must be text"):
        store.get_claim("science", claim["claim_id"])


def test_orphan_evidence_and_echo_without_lineage_are_rejected(tmp_path):
    store = ClaimStore(tmp_path / ".filmot_data")
    with pytest.raises(ValueError, match="Unknown claim"):
        store.add_evidence(
            "science",
            "c-missing",
            relation="supports",
            source="doi:10/example",
            source_kind="paper",
        )

    claim, _ = store.add_claim("science", "A real claim")
    with pytest.raises(ValueError, match="lineage"):
        store.add_evidence(
            "science",
            claim["claim_id"],
            relation="supports",
            source="https://example.test",
            source_kind="web",
            independence="echo",
        )


def test_strict_store_rejects_non_finite_or_non_video_timestamps(tmp_path):
    store = ClaimStore(tmp_path / ".filmot_data")
    claim, _ = store.add_claim("science", "A timestamped claim")

    with pytest.raises(ValueError, match="finite non-negative"):
        store.add_evidence(
            "science",
            claim["claim_id"],
            relation="supports",
            source="https://youtube.com/watch?v=abcdefghijk",
            source_kind="video",
            video_id="abcdefghijk",
            start_seconds=float("nan"),
        )
    with pytest.raises(ValueError, match="video ID"):
        store.add_evidence(
            "science",
            claim["claim_id"],
            relation="supports",
            source="https://example.test/paper",
            source_kind="paper",
            start_seconds=12.0,
        )
    with pytest.raises(ValueError, match="source kind 'video'"):
        store.add_evidence(
            "science",
            claim["claim_id"],
            relation="supports",
            source="doi:10/example",
            source_kind="paper",
            video_id="abcdefghijk",
        )
    with pytest.raises(ValueError, match="same YouTube video"):
        store.add_evidence(
            "science",
            claim["claim_id"],
            relation="supports",
            source="doi:10/example",
            source_kind="video",
            video_id="abcdefghijk",
        )

    assert len(store.get_claim("science", claim["claim_id"])["evidence"]) == 0


def test_claim_persistence_failure_is_visible(tmp_path):
    store = ClaimStore(tmp_path / ".filmot_data")
    with patch("filmot.claims.os.open", side_effect=OSError("disk full")):
        with pytest.raises(ClaimStoreError, match="disk full"):
            store.add_claim("science", "This write must not disappear")


def test_claim_read_rejects_non_finite_tampered_evidence(tmp_path, monkeypatch):
    data_dir = tmp_path / ".filmot_data"
    store = ClaimStore(data_dir)
    claim, _ = store.add_claim("science", "A timestamped statement")
    store.add_evidence(
        "science",
        claim["claim_id"],
        relation="supports",
        source="https://youtube.com/watch?v=abcdefghijk",
        source_kind="video",
        video_id="abcdefghijk",
        start_seconds=90,
    )
    event_paths = sorted((data_dir / "claims" / "science").glob("*.json"))
    evidence_path = next(
        path
        for path in event_paths
        if json.loads(path.read_text(encoding="utf-8"))["event_type"] == "evidence"
    )
    event = json.loads(evidence_path.read_text(encoding="utf-8"))
    event["data"]["start_seconds"] = float("nan")
    evidence_path.write_text(json.dumps(event), encoding="utf-8")

    with pytest.raises(ClaimStoreError, match="Non-standard JSON constant"):
        store.get_claim("science", claim["claim_id"])

    monkeypatch.chdir(tmp_path)
    shown = CliRunner().invoke(
        cli,
        ["claims", "show", "science", claim["claim_id"], "--raw"],
    )
    assert shown.exit_code == 1
    payload = json.loads(shown.stdout)
    assert payload["_filmot"]["errors"][0]["stage"] == "read-claims"
    json.dumps(payload, allow_nan=False)


def test_claim_cli_raw_round_trip_and_compact_session_log(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    added = runner.invoke(
        cli,
        ["claims", "add", "science", "Ripasudil was proposed for AMD", "--raw"],
    )
    assert added.exit_code == 0, added.output
    added_payload = json.loads(added.stdout)
    claim_id = added_payload["claim_id"]
    assert added_payload["rows"][0]["text"] == "Ripasudil was proposed for AMD"

    cited = runner.invoke(
        cli,
        [
            "claims",
            "cite",
            "science",
            claim_id,
            "--video",
            "abcdefghijk",
            "--at",
            "90",
            "--relation",
            "supports",
            "--excerpt",
            "A short exact excerpt",
            "--raw",
        ],
    )
    assert cited.exit_code == 0, cited.output

    shown = runner.invoke(cli, ["claims", "show", "science", claim_id, "--raw"])
    assert shown.exit_code == 0, shown.output
    payload = json.loads(shown.stdout)
    assert payload["rows"][0]["evidence"][0]["start_seconds"] == 90.0
    assert payload["_filmot"]["command"] == "claims-show"

    ledger_path = tmp_path / ".filmot_data" / "sessions" / "science.jsonl"
    ledger_text = ledger_path.read_text(encoding="utf-8")
    assert claim_id in ledger_text
    assert "Ripasudil was proposed for AMD" not in ledger_text
    assert "A short exact excerpt" not in ledger_text


def test_claim_show_on_empty_workspace_is_read_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli, ["claims", "show", "empty", "--raw"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["_filmot"]["status"] == "empty"
    assert not (tmp_path / ".filmot_data").exists()


def test_claim_cli_rejects_source_less_evidence_as_one_raw_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        cli,
        [
            "claims",
            "cite",
            "science",
            "c-missing",
            "--relation",
            "supports",
            "--raw",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["_filmot"]["status"] == "failed"
    assert payload["_filmot"]["errors"][0]["stage"] == "validate-evidence"


@pytest.mark.parametrize(
    "video_id",
    ["not a video", "abcdefghij", "https://youtu.be/abcdefghijk"],
)
def test_claim_cli_rejects_malformed_video_id_before_storage(
    tmp_path,
    monkeypatch,
    video_id,
):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        cli,
        [
            "claims",
            "cite",
            "science",
            "c-missing",
            "--video",
            video_id,
            "--relation",
            "supports",
            "--raw",
        ],
    )

    assert result.exit_code == 1
    error = json.loads(result.stdout)["_filmot"]["errors"][0]
    assert error["stage"] == "validate-evidence"
    assert "exactly 11" in error["message"]
    assert not (tmp_path / ".filmot_data").exists()


def test_claim_cli_rejects_non_video_kind_with_video_locator(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        cli,
        [
            "claims",
            "cite",
            "science",
            "c-missing",
            "--source",
            "doi:10/example",
            "--video",
            "abcdefghijk",
            "--at",
            "5",
            "--source-kind",
            "paper",
            "--relation",
            "supports",
            "--raw",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    error = payload["_filmot"]["errors"][0]
    assert error["stage"] == "validate-evidence"
    assert "non-video" in error["message"]
    assert not (tmp_path / ".filmot_data").exists()


def test_claim_cli_rejects_two_source_identifiers_for_one_evidence_item(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        cli,
        [
            "claims",
            "cite",
            "science",
            "c-missing",
            "--source",
            "doi:10/example",
            "--video",
            "abcdefghijk",
            "--relation",
            "supports",
            "--raw",
        ],
    )

    assert result.exit_code == 1
    error = json.loads(result.stdout)["_filmot"]["errors"][0]
    assert error["stage"] == "validate-evidence"
    assert "mutually exclusive" in error["message"]
