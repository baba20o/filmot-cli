"""Tests for filmot.library module."""

import pytest
import json
from pathlib import Path

from filmot.library import TranscriptLibrary, normalize_topic_name


# ── Topic normalization ──────────────────────────────────────────

class TestNormalizeTopic:
    """Tests for TranscriptLibrary._normalize_topic()."""

    def test_simple(self, library):
        assert library._normalize_topic("my-topic") == "my-topic"

    def test_spaces_to_hyphens(self, library):
        assert library._normalize_topic("my topic") == "my-topic"

    def test_uppercase(self, library):
        assert library._normalize_topic("My Topic") == "my-topic"

    def test_special_chars_removed(self, library):
        assert library._normalize_topic("topic!@#$%") == "topic"

    def test_underscores_to_hyphens(self, library):
        assert library._normalize_topic("my_topic_name") == "my-topic-name"

    def test_empty_returns_uncategorized(self, library):
        assert library._normalize_topic("") == "uncategorized"

    def test_only_special_chars_get_distinct_stable_slugs(self, library):
        exclamation = library._normalize_topic("!!!")
        assert exclamation.startswith("topic-")
        assert exclamation == library._normalize_topic("!!!")
        assert exclamation != library._normalize_topic("???")

    def test_multiple_hyphens_collapsed(self, library):
        assert library._normalize_topic("a--b---c") == "a-b-c"

    @pytest.mark.parametrize("topic", [
        "人工知能",
        "초전도체",
        "искусственный интеллект",
        "الذكاء الاصطناعي",
    ])
    def test_non_latin_topics_remain_readable(self, library, topic):
        slug = library._normalize_topic(topic)
        assert slug not in {"uncategorized", "session"}
        assert any(char.isalnum() for char in slug)

    def test_non_latin_topics_are_distinct(self, library):
        topics = ["人工知能", "초전도체", "искусственный интеллект", "الذكاء الاصطناعي"]
        assert len({library._normalize_topic(topic) for topic in topics}) == len(topics)

    def test_unicode_equivalent_spellings_normalize_consistently(self, library):
        assert library._normalize_topic("Ｃａｆé") == library._normalize_topic("Cafe\u0301")
        assert library._normalize_topic("ИСКУССТВЕННЫЙ ИНТЕЛЛЕКТ") == (
            library._normalize_topic("искусственный интеллект")
        )

    def test_shared_normalizer_preserves_historical_ascii_slugs(self, library):
        assert normalize_topic_name("My_topic! name") == "my-topic-name"
        assert library._normalize_topic("My_topic! name") == "my-topic-name"

    @pytest.mark.parametrize(("topic", "legacy_slug"), [
        ("foo.bar", "foobar"),
        ("foo/bar", "foobar"),
        ("C#.NET", "cnet"),
        ("my(topic)", "mytopic"),
    ])
    def test_internal_ascii_punctuation_preserves_legacy_slug(
        self, library, topic, legacy_slug
    ):
        assert library._normalize_topic(topic) == legacy_slug


# ── Save / Get ───────────────────────────────────────────────────

class TestSaveAndGet:
    def test_empty_library_inspection_does_not_create_storage(self, tmp_path):
        data_dir = tmp_path / ".filmot_data"
        library = TranscriptLibrary(data_dir)

        assert library.list_topics() == []
        assert library.list_transcripts("missing") == []
        assert library.search("anything") == []
        assert library.get("missing") is None
        assert library.stats()["total_transcripts"] == 0
        assert not data_dir.exists()

    """Tests for save() and get()."""

    def test_save_creates_file(self, library):
        path = library.save("vid123456789", "ml", "Hello world transcript")
        assert Path(path).exists()
        with open(path, "r") as f:
            data = json.load(f)
        assert data["video_id"] == "vid123456789"
        assert data["transcript"] == "Hello world transcript"
        assert data["topic"] == "ml"

    def test_get_by_topic(self, library):
        library.save("vid123456789", "ml", "Some text")
        result = library.get("vid123456789", "ml")
        assert result is not None
        assert result["transcript"] == "Some text"

    def test_get_without_topic_searches_all(self, library):
        library.save("vid123456789", "ml", "Some text")
        result = library.get("vid123456789")
        assert result is not None

    def test_get_missing_returns_none(self, library):
        assert library.get("nonexistent") is None

    def test_save_with_metadata(self, library):
        library.save(
            "vid123456789",
            "ml",
            "text",
            metadata={"title": "Cool Video", "channel": "TestCh"},
        )
        result = library.get("vid123456789", "ml")
        assert result["metadata"]["title"] == "Cool Video"

    def test_save_preserves_segments_and_normalized_source_metadata(self, library):
        segments = [
            {"text": "First claim", "start": 12.5, "duration": 2.0},
            {
                "text": "Second claim",
                "start": 14.5,
                "duration": 1.5,
                "speaker": "Host",
            },
        ]

        path = library.save(
            "vid123456789",
            "ml",
            "First claim Second claim",
            metadata={
                "title": "Evidence source",
                "channelname": "Primary Channel",
                "source": "youtube-transcript-api",
            },
            segments=segments,
        )

        stored = json.loads(path.read_text(encoding="utf-8"))
        assert stored["segments"] == segments
        assert stored["metadata"]["channel"] == "Primary Channel"
        assert stored["metadata"]["source"] == "youtube"
        assert stored["metadata"]["segment_count"] == 2
        assert stored["source"] == {
            "platform": "youtube",
            "video_id": "vid123456789",
            "url": "https://www.youtube.com/watch?v=vid123456789",
            "transcript_source": "youtube",
            "title": "Evidence source",
            "channel": "Primary Channel",
        }

    def test_get_normalizes_legacy_record_without_rewriting_it(self, library):
        topic_dir = library.transcripts_dir / "ml"
        topic_dir.mkdir(parents=True)
        path = topic_dir / "legacy-video.json"
        legacy = {
            "video_id": "legacy-video",
            "topic": "ml",
            "saved_at": "2026-01-01T00:00:00",
            "transcript": "Legacy text",
            "metadata": {
                "title": "Legacy source",
                "channel": "Archive",
                "route": "aws-transcribe",
            },
        }
        path.write_text(json.dumps(legacy), encoding="utf-8")

        loaded = library.get("legacy-video", "ml")

        assert loaded["transcript"] == "Legacy text"
        assert loaded["segments"] == []
        assert loaded["metadata"]["source"] == "aws_transcribe"
        assert loaded["source"]["url"].endswith("watch?v=legacy-video")
        assert loaded["source"]["title"] == "Legacy source"
        assert json.loads(path.read_text(encoding="utf-8")) == legacy

    def test_search_returns_timestamped_citation_details(self, library):
        library.save(
            "vid123456789",
            "science",
            "opening words target phrase closing words",
            segments=[
                {"text": "opening words", "start": 0.0, "duration": 2.0},
                {"text": "target phrase", "start": 12.5, "duration": 2.0},
                {"text": "closing words", "start": 14.5, "duration": 2.0},
            ],
        )

        result = library.search("target phrase", topic="science")[0]
        detail = result["match_details"][0]

        assert detail["start_seconds"] == 12.5
        assert detail["timestamp"] == "0:12"
        assert detail["url"].endswith("vid123456789&t=12s")
        assert "target phrase" in detail["excerpt"]

    def test_unicode_case_insensitive_offsets_stay_source_relative(self, library):
        """Unicode case expansion must not shift citation offsets/timestamps."""
        text = "İİİİ x tail"
        library.save(
            "unicode-offset",
            "citations",
            text,
            metadata={"title": "Unicode offsets"},
            segments=[
                {"text": "İİİİ x", "start": 10.0, "duration": 2.0},
                {"text": "tail", "start": 20.0, "duration": 2.0},
            ],
        )

        rows = library.search("X", topic="citations")

        detail = rows[0]["match_details"][0]
        assert detail["start_char"] == text.index("x")
        assert detail["end_char"] == text.index("x") + 1
        assert detail["start_seconds"] == 10.0
        assert detail["url"].endswith("&t=10s")

    def test_legacy_search_locator_is_explicitly_untimed(self, library):
        library.save("vid123456789", "science", "an untimed target")

        detail = library.search("target", topic="science")[0]["match_details"][0]

        assert detail["start_seconds"] is None
        assert detail["timestamp"] is None
        assert detail["url"].endswith("watch?v=vid123456789")

    def test_missing_or_partial_segment_times_do_not_invent_citations(self, library):
        library.save(
            "partial-segments",
            "science",
            "hello world uncovered tail",
            segments=[
                {"text": "hello", "start": 5.0},
                {"text": "world"},
            ],
        )

        world = library.search("world", topic="science")[0]["match_details"][0]
        tail = library.search("tail", topic="science")[0]["match_details"][0]

        assert world["start_seconds"] is None
        assert world["timestamp"] is None
        assert world["url"].endswith("watch?v=partial-segments")
        assert tail["start_seconds"] is None
        assert tail["timestamp"] is None
        assert tail["url"].endswith("watch?v=partial-segments")

    def test_misaligned_segments_do_not_map_an_in_range_offset(self, library):
        library.save(
            "misaligned-segments",
            "science",
            "tiny target",
            segments=[
                {"text": "wrong prefix", "start": 10.0},
                {"text": "target", "start": 20.0},
            ],
        )

        detail = library.search("target", topic="science")[0]["match_details"][0]

        assert detail["start_seconds"] is None
        assert detail["timestamp"] is None
        assert detail["url"].endswith("watch?v=misaligned-segments")

    @pytest.mark.parametrize(
        "segment",
        [
            {"text": "alpha target", "start": True, "duration": 1},
            {"text": 123, "start": 4, "duration": 1},
        ],
    )
    def test_invalid_legacy_segments_never_create_timed_citations(
        self,
        library,
        segment,
    ):
        topic_dir = library.transcripts_dir / "science"
        topic_dir.mkdir(parents=True, exist_ok=True)
        (topic_dir / "legacy-segment.json").write_text(
            json.dumps({
                "video_id": "legacy-segment",
                "transcript": "alpha target" if isinstance(segment["text"], str) else "123",
                "metadata": {},
                "segments": [segment],
            }),
            encoding="utf-8",
        )

        query = "target" if isinstance(segment["text"], str) else "123"
        detail = library.search(query, topic="science")[0]["match_details"][0]

        assert detail["start_seconds"] is None
        assert detail["timestamp"] is None
        assert detail["url"].endswith("watch?v=legacy-segment")

    def test_overwrite(self, library):
        library.save("vid123456789", "ml", "first")
        library.save("vid123456789", "ml", "second")
        result = library.get("vid123456789", "ml")
        assert result["transcript"] == "second"

    def test_failed_overwrite_preserves_prior_record_atomically(self, library):
        original = library.save("vid123456789", "ml", "first")

        with pytest.raises(TypeError):
            library.save(
                "vid123456789",
                "ml",
                "second",
                metadata={"title": object()},
            )

        assert library.get("vid123456789", "ml")["transcript"] == "first"
        assert not list(original.parent.glob("*.tmp"))

    @pytest.mark.parametrize(
        ("transcript", "segments", "message"),
        [
            (None, None, "transcript_text"),
            ("valid", {"text": "bad container"}, "segments"),
            ("valid", [{"text": object()}], "text must be text"),
            (
                "valid",
                [{"text": "caption", "start": float("nan")}],
                "finite and non-negative",
            ),
        ],
    )
    def test_save_rejects_invalid_record_before_publication(
        self,
        library,
        transcript,
        segments,
        message,
    ):
        with pytest.raises((TypeError, ValueError), match=message):
            library.save(
                "vid123456789",
                "strict",
                transcript,
                segments=segments,
            )

        assert library.get("vid123456789", "strict") is None

    def test_same_video_in_distinct_unicode_topics_does_not_overwrite(self, library):
        library.save("vid123456789", "人工知能", "Japanese corpus")
        library.save("vid123456789", "초전도체", "Korean corpus")

        assert library.get("vid123456789", "人工知能")["transcript"] == "Japanese corpus"
        assert library.get("vid123456789", "초전도체")["transcript"] == "Korean corpus"

    def test_reads_and_explicitly_migrates_legacy_non_latin_topic(self, library):
        legacy_dir = library.transcripts_dir / "uncategorized"
        legacy_dir.mkdir(parents=True)
        legacy_file = legacy_dir / "vid123456789.json"
        legacy_file.write_text(json.dumps({
            "video_id": "vid123456789",
            "topic": "uncategorized",
            "saved_at": "2026-01-01T00:00:00",
            "transcript": "legacy text",
            "metadata": {},
        }), encoding="utf-8")

        # The ambiguous shared corpus must not be silently attributed to an
        # arbitrary Unicode topic.
        assert library.get("vid123456789", "人工知能") is None
        assert library.migrate_legacy_topic("人工知能") == 1
        migrated = library.get("vid123456789", "人工知能")
        assert migrated["topic"] == "人工知能"
        assert not legacy_file.exists()

    def test_mixed_script_legacy_ascii_remainder_is_not_read_implicitly(self, library):
        legacy_dir = library.transcripts_dir / "ai"
        legacy_dir.mkdir(parents=True)
        legacy_file = legacy_dir / "vid123456789.json"
        legacy_file.write_text(json.dumps({
            "video_id": "vid123456789",
            "topic": "ai",
            "saved_at": "2026-01-01T00:00:00",
            "transcript": "ambiguous legacy text",
            "metadata": {},
        }), encoding="utf-8")

        # Both names used to collapse to ``ai``. Neither may claim the old
        # directory until an operator explicitly chooses its owner.
        assert library.get("vid123456789", "AI 人工知能") is None
        assert library.get("vid123456789", "AI 초전도체") is None

    def test_legacy_migration_preserves_non_object_json(self, library):
        legacy_dir = library.transcripts_dir / "uncategorized"
        legacy_dir.mkdir(parents=True)
        invalid_shape = legacy_dir / "not-an-entry.json"
        invalid_shape.write_text("[]", encoding="utf-8")

        assert library.migrate_legacy_topic("人工知能") == 0
        assert invalid_shape.read_text(encoding="utf-8") == "[]"

    def test_legacy_migration_failure_never_strands_partial_destination(
        self,
        library,
    ):
        legacy_dir = library.transcripts_dir / "uncategorized"
        legacy_dir.mkdir(parents=True)
        legacy_file = legacy_dir / "legacy-video.json"
        legacy_file.write_text(
            '{"video_id":"legacy-video","transcript":"\\ud800"}',
            encoding="utf-8",
        )

        assert library.migrate_legacy_topic("人工知能") == 0
        destination = library.transcripts_dir / "人工知能" / legacy_file.name
        assert legacy_file.exists()
        assert not destination.exists()
        assert not list(destination.parent.glob("*.tmp"))

        legacy_file.write_text(
            json.dumps({
                "video_id": "legacy-video",
                "transcript": "repaired legacy transcript",
            }),
            encoding="utf-8",
        )
        assert library.migrate_legacy_topic("人工知能") == 1
        assert destination.exists()
        assert not legacy_file.exists()


class TestLedgerRobustness:
    def test_non_object_jsonl_records_are_ignored_and_preserved_on_migration(
        self, tmp_path
    ):
        from filmot.ledger import (
            list_sessions,
            migrate_legacy_session,
            read_events,
        )

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        legacy = sessions_dir / "uncategorized.jsonl"
        legacy.write_text(
            "[]\n"
            + json.dumps({
                "ts": "2026-01-01T00:00:00",
                "kind": "research",
                "query": "人工知能",
            }, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )

        assert migrate_legacy_session("人工知能", str(tmp_path)) == 1
        assert legacy.read_text(encoding="utf-8") == "[]\n"
        assert len(read_events("人工知能", str(tmp_path))) == 1
        assert all(row["name"] != "uncategorized" for row in list_sessions(str(tmp_path)))


# ── Exists ────────────────────────────────────────────────────────

class TestExists:
    """Tests for exists()."""

    def test_exists_true(self, populated_library):
        assert populated_library.exists("abc12345678", "test-topic") is True

    def test_exists_false(self, populated_library):
        assert populated_library.exists("zzz12345678", "test-topic") is False

    def test_exists_wrong_topic(self, populated_library):
        assert populated_library.exists("abc12345678", "other-topic") is False


# ── List topics / transcripts ────────────────────────────────────

class TestListTopics:
    """Tests for list_topics()."""

    def test_lists_all_topics(self, populated_library):
        topics = populated_library.list_topics()
        names = [t["topic"] for t in topics]
        assert "test-topic" in names
        assert "other-topic" in names

    def test_counts_are_correct(self, populated_library):
        topics = populated_library.list_topics()
        counts = {t["topic"]: t["count"] for t in topics}
        assert counts["test-topic"] == 2
        assert counts["other-topic"] == 1

    def test_empty_library(self, library):
        assert library.list_topics() == []


class TestListTranscripts:
    """Tests for list_transcripts()."""

    def test_lists_transcripts_in_topic(self, populated_library):
        transcripts = populated_library.list_transcripts("test-topic")
        assert len(transcripts) == 2
        ids = {t["video_id"] for t in transcripts}
        assert ids == {"abc12345678", "def12345678"}

    def test_empty_topic(self, library):
        assert library.list_transcripts("nonexistent") == []

    def test_strict_topic_records_reject_malformed_corpus(self, library):
        topic_dir = library.transcripts_dir / "science"
        topic_dir.mkdir(parents=True)
        (topic_dir / "broken.json").write_text("{not-json\n", encoding="utf-8")

        with pytest.raises(ValueError, match="Unable to read transcript record"):
            library.read_topic_records("science")

    def test_strict_topic_records_reject_missing_transcript_text(self, library):
        topic_dir = library.transcripts_dir / "science"
        topic_dir.mkdir(parents=True)
        (topic_dir / "empty.json").write_text(
            json.dumps({"video_id": "abcdefghijk"}),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="missing or empty"):
            library.read_topic_records("science")

    @pytest.mark.parametrize("video_id", [None, "   ", {"id": "x"}])
    def test_strict_topic_records_reject_invalid_video_identity(
        self,
        library,
        video_id,
    ):
        topic_dir = library.transcripts_dir / "science"
        topic_dir.mkdir(parents=True, exist_ok=True)
        (topic_dir / "bad-id.json").write_text(
            json.dumps({
                "video_id": video_id,
                "transcript": "complete text",
            }),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="valid video_id"):
            library.read_topic_records("science")

    @pytest.mark.parametrize("invalid_number", ["NaN", "Infinity", "1e999"])
    def test_strict_topic_records_reject_non_finite_json_numbers(
        self,
        library,
        invalid_number,
    ):
        topic_dir = library.transcripts_dir / "science"
        topic_dir.mkdir(parents=True, exist_ok=True)
        (topic_dir / "non-finite.json").write_text(
            '{"video_id":"x","transcript":"complete text",'
            '"metadata":{"views":' + invalid_number + "}}",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="non-finite"):
            library.read_topic_records("science")


# ── Search ────────────────────────────────────────────────────────

class TestSearch:
    """Tests for search()."""

    def test_search_finds_match(self, populated_library):
        results = populated_library.search("machine learning")
        assert len(results) == 1
        assert results[0]["video_id"] == "abc12345678"

    def test_search_case_insensitive(self, populated_library):
        results = populated_library.search("MACHINE LEARNING")
        assert len(results) == 1

    def test_search_across_topics(self, populated_library):
        results = populated_library.search("transcript")
        # "transcript" appears in both topics' text
        assert len(results) >= 2

    def test_search_within_topic(self, populated_library):
        results = populated_library.search("transcript", topic="test-topic")
        assert all(r["topic"] == "test-topic" for r in results)

    def test_search_no_match(self, populated_library):
        results = populated_library.search("xyzzyplugh")
        assert len(results) == 0

    def test_search_returns_match_count(self, populated_library):
        results = populated_library.search("neural")
        assert results[0]["match_count"] >= 1

    def test_substring_search_preserves_match_mode_and_excerpt(self, library):
        library.save(
            "vid123456789",
            "water",
            "The facility consumed twelve liters before recycling two more liters.",
        )

        assert library.search("liter", topic="water") == []
        results = library.search("liter", topic="water", substring=True)

        assert results[0]["match_count"] == 2
        assert results[0]["match_mode"] == "substring"
        assert "liters" in results[0]["matches"][0]

    def test_word_search_identifies_match_mode(self, populated_library):
        results = populated_library.search("neural")
        assert results[0]["match_mode"] == "word"


# ── get_context ───────────────────────────────────────────────────

class TestGetContext:
    """Tests for get_context()."""

    def test_concatenates_transcripts(self, populated_library):
        ctx = populated_library.get_context("test-topic")
        assert "machine learning" in ctx
        assert "neural networks" in ctx

    def test_max_chars(self, populated_library):
        ctx = populated_library.get_context("test-topic", max_chars=200)
        assert len(ctx) <= 200 + 50  # allow some slack for headers/truncation marker

    def test_empty_topic(self, library):
        ctx = library.get_context("nonexistent")
        assert ctx == ""


# ── Delete ────────────────────────────────────────────────────────

class TestDelete:
    """Tests for delete() and delete_topic()."""

    def test_delete_single(self, populated_library):
        assert populated_library.delete("abc12345678", "test-topic") is True
        assert populated_library.exists("abc12345678", "test-topic") is False
        # Other video still exists
        assert populated_library.exists("def12345678", "test-topic") is True

    def test_delete_not_found(self, populated_library):
        assert populated_library.delete("zzz12345678", "test-topic") is False

    def test_delete_from_all_topics(self, populated_library):
        # Save same video in two topics
        populated_library.save("abc12345678", "other-topic", "cross-posted")
        assert populated_library.delete("abc12345678") is True
        assert populated_library.get("abc12345678") is None

    def test_delete_topic(self, populated_library):
        count = populated_library.delete_topic("test-topic")
        assert count == 2
        assert populated_library.list_transcripts("test-topic") == []


# ── Stats ─────────────────────────────────────────────────────────

class TestStats:
    """Tests for stats()."""

    def test_stats_totals(self, populated_library):
        s = populated_library.stats()
        assert s["total_topics"] == 2
        assert s["total_transcripts"] == 3
        assert s["total_size_bytes"] > 0

    def test_stats_empty(self, library):
        s = library.stats()
        assert s["total_topics"] == 0
        assert s["total_transcripts"] == 0
