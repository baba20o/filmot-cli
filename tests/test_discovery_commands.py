"""The titles, find-channels, and subtitle-export commands."""

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from filmot.api import ExportBody
from filmot.cli import cli
from filmot.discovery import normalize_candidates


FIXTURES = Path(__file__).parent / "fixtures" / "filmot_api"
LTT = "UCXuqSBlHAE6Xw-yeJA0Tunw"
OTHER = "UC_Kn-LYuJPFq62K8mX5Hmow"
VIDEO_IDS = ("aaaaaaaaaa1", "aaaaaaaaaa2", "aaaaaaaaaa3")


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def metadata_page():
    rows = fixture("metadata_search.json")["result"]
    return {"result": [dict(row, id=video_id) for row, video_id in zip(rows, VIDEO_IDS)]}


def jsonl(*ids):
    return ExportBody(b"".join(b'{"id": "%s"}\n' % video_id.encode() for video_id in ids), "application/x-ndjson")


class FakeClient:
    def __init__(self, metadata=(), channels=(), exports=(), suggestions=()):
        self.metadata, self.channels, self.exports = list(metadata), list(channels), list(exports)
        self.suggestions = list(suggestions)
        self.metadata_calls, self.channel_calls, self.export_calls, self.searches = [], [], [], []

    def search_channels(self, term):
        self.searches.append(term)
        return self.suggestions

    def search_metadata(self, params):
        self.metadata_calls.append(dict(params))
        return self.metadata.pop(0)

    def search_advanced_channels(self, params):
        self.channel_calls.append(dict(params))
        return self.channels.pop(0)

    def export_subtitle_search(self, params):
        self.export_calls.append(dict(params))
        return self.exports.pop(0)


@pytest.fixture
def run(stream_runner):
    def invoke(client, *args, **kwargs):
        with patch("filmot.commands.discovery.FilmotClient", return_value=client):
            return stream_runner.invoke(cli, list(args), env={"COLUMNS": "200"}, **kwargs)

    return invoke


# ------------------------------------------------------------------- titles


def test_titles_sends_every_filter_under_its_api_name(run):
    client = FakeClient(metadata=[metadata_page()])

    result = run(
        client, "titles", '"fusion" reactor', "--min-views", "1000", "--start-date", "2026-01-01",
        "--end-date", "2026-06-30", "--min-duration", "60", "-c", "Education", "--exclude", "Gaming,Music",
        "--license", "2", "--country", "217,153", "--caption-lang", "en,fr", "--only-caption-langs",
        "--detected-lang", "en", "--detected-in-captions", "--listed", "--live", "--partial",
        "--sort", "viewcount", "--per-page", "30",
    )

    assert result.exit_code == 0, result.output
    assert client.metadata_calls == [{
        "titleQuery": '"fusion" reactor', "minViews": 1000, "startDate": "2026-01-01",
        "endDate": "2026-06-30", "startDuration": 60, "category": "Education",
        "excludeCategory": "Gaming,Music", "license": 2, "country": "217,153",
        "captionLanguages": "en,fr", "capLangExactMatch": 1, "detectedLanguage": "en",
        "qDetectedLangInCapLangs": "true", "unlisted": 0, "live": 1, "completeData": 0,
        "sortField": "viewcount", "sortOrder": "desc", "page": 1, "perPageCount": 30,
    }]


def test_titles_resolves_channel_filters_and_excludes(run):
    suggestions = [{"channelid": LTT, "name": "Linus Tech Tips", "handle": "@LinusTechTips"}]
    included = FakeClient(metadata=[metadata_page()], suggestions=suggestions)
    excluded = FakeClient(metadata=[metadata_page()])

    first = run(included, "titles", "--channel-id", "@LinusTechTips,{}".format(OTHER), "--raw")
    second = run(excluded, "titles", "gpu", "--exclude-channels", OTHER, "--raw")

    assert first.exit_code == 0 and second.exit_code == 0, first.output + second.output
    assert included.searches == ["LinusTechTips"]
    assert included.metadata_calls[0]["channelID"] == "{},{}".format(LTT, OTHER)
    assert excluded.metadata_calls[0]["channelID"] == "-{}".format(OTHER)


def test_titles_raw_output_is_a_download_envelope(run):
    result = run(FakeClient(metadata=[metadata_page()]), "titles", "gpu", "--raw")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert (payload["exhausted"], payload["next_page"], payload["last_page_reached"]) == (False, 2, False)
    assert [candidate["video_id"] for candidate in normalize_candidates(payload)] == list(VIDEO_IDS)


@pytest.mark.parametrize("arguments,message", [
    ([], "give a title query or at least one filter"),
    (["--channel-id", LTT, "--exclude-channels", OTHER], "not both"),
    (["gpu", "--channel-id", ","], "--channel-id names no channel"),
    (["gpu", "--only-caption-langs"], "--only-caption-langs needs --caption-lang"),
    (["gpu", "--detected-in-captions"], "--detected-in-captions needs --detected-lang"),
    (["gpu", "--order", "asc"], "--order needs --sort"),
    (["gpu", "--start-date", "2026-02-01", "--end-date", "2026-01-01"], "must not be later"),
])
def test_titles_refuses_requests_it_cannot_send_as_given(run, arguments, message):
    client = FakeClient()

    result = run(client, "titles", *arguments)

    assert result.exit_code == 2
    assert message in result.output + result.stderr
    assert client.metadata_calls == []


def test_titles_stops_at_page_500_the_last_filmot_serves(run):
    client = FakeClient(metadata=[metadata_page()])

    result = run(client, "titles", "gpu", "--page", "500", "--pages", "3", "--raw")

    assert result.exit_code == 0, result.output
    assert [call["page"] for call in client.metadata_calls] == [500]
    payload = json.loads(result.stdout)
    assert (payload["last_page_reached"], payload["exhausted"], payload["next_page"]) == (True, True, None)


def test_titles_renders_matches_and_the_end_of_the_results(run):
    client = FakeClient(metadata=[metadata_page(), {"result": []}])

    result = run(client, "titles", "gpu", "--pages", "3")

    assert result.exit_code == 0, result.output
    assert [call["page"] for call in client.metadata_calls] == [1, 2]
    assert "Recorded video title 002" in result.stdout
    assert "No more results." in result.stdout


def test_titles_keeps_earlier_pages_when_a_later_one_fails(run):
    failure = {"error": "busy", "error_type": "FilmotServiceError"}

    partial = run(FakeClient(metadata=[metadata_page(), failure]), "titles", "gpu", "--pages", "2", "--raw")
    failed = run(FakeClient(metadata=[failure]), "titles", "gpu", "--raw")

    assert partial.exit_code == 0, partial.output
    payload = json.loads(partial.stdout)
    assert (payload["_filmot"]["status"], len(payload["videos"]), payload["next_page"]) == ("partial", 3, 2)
    assert failed.exit_code == 1
    assert json.loads(failed.stdout)["_filmot"]["errors"][0]["type"] == "FilmotServiceError"


# ------------------------------------------------------------ find-channels


def test_find_channels_sends_filters_under_their_api_names(run):
    client = FakeClient(channels=[fixture("advanced_channel_search.json")])

    result = run(
        client, "find-channels", "--topics", "sqlite", "--min-subscribers", "1000",
        "--country", "Canada", "--country", "United States", "--auto-lang", "en", "--auto-lang", "fr",
        "--min-gained-subs", "100", "--subs-window", "90", "--sort", "gainedsubcount",
        "--created-after", "2010-01-01", "--raw",
    )

    assert result.exit_code == 0, result.output
    assert client.channel_calls == [{
        "topics": "sqlite", "minSubscribers": 1000.0, "channelCreatedAfter": "2010-01-01",
        "minGainedSubCount": 100.0, "gainedSubPeriod": 90, "countries": "Canada,United States",
        "autoLanguages": "en,fr", "sortField": "gainedsubcount", "sortOrder": "desc",
        "page": 1, "perPageCount": 25,
    }]
    payload = json.loads(result.stdout)
    assert payload["max_results"] == 10000
    assert len(payload["channels"]) == 3


@pytest.mark.parametrize("arguments,message", [
    (["--order", "asc"], "--order needs --sort"),
    (["--views-window", "90"], "--views-window applies to"),
    (["--subs-window", "90", "--sort", "subcount"], "--subs-window applies to"),
    (["--page", "400", "--per-page", "30"], "first 10,000 matching channels"),
])
def test_find_channels_refuses_options_without_effect_and_unreachable_pages(run, arguments, message):
    client = FakeClient()

    result = run(client, "find-channels", *arguments)

    assert result.exit_code == 2
    assert message in result.output + result.stderr
    assert client.channel_calls == []


def test_find_channels_stops_at_the_last_reachable_page(run):
    client = FakeClient(channels=[fixture("advanced_channel_search.json")])

    result = run(client, "find-channels", "--per-page", "300", "--page", "34", "--pages", "3")

    assert result.exit_code == 0, result.output
    assert [call["page"] for call in client.channel_calls] == [34]
    assert "Views gained (28d)" in result.stdout
    assert "Recorded channel 001" in result.stdout
    assert "Filmot pages this search no further." in result.stdout


# ---------------------------------------------------------- subtitle-export


def test_subtitle_export_merges_pages_into_the_named_file(run, tmp_path):
    client = FakeClient(exports=[jsonl("a", "b"), jsonl("c")])
    target = tmp_path / "matches.jsonl"

    result = run(client, "subtitle-export", "gpu", "--channel-id", LTT, "-o", str(target), "--per-page", "2", "--pages", "4")

    assert result.exit_code == 0, result.output
    assert target.read_bytes() == b'{"id": "a"}\n{"id": "b"}\n{"id": "c"}\n'
    assert client.export_calls[0] == {
        "query": "gpu", "channelID": LTT, "page": 1, "perPageCount": 2, "format": "jsonl", "exportInline": 1,
    }
    assert len(client.export_calls) == 2
    assert "1 credit each" in result.stderr
    assert "Wrote 3 matches (2 pages" in result.stdout


def zip_body(*ids):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("metadata.jsonl", "".join('{"id": "%s"}\n' % video_id for video_id in ids))
        for video_id in ids:
            archive.writestr("subs/{}.a.en.srt".format(video_id), "1\n00:00:01,000 --> 00:00:02,000\nwords\n")
    return ExportBody(buffer.getvalue(), "application/zip")


def test_subtitles_are_added_to_the_default_or_chosen_columns(run, tmp_path):
    client = FakeClient(exports=[zip_body("a"), ExportBody(b'{"result": [{"id": "a", "title": "One"}]}', "")])

    archive = run(client, "subtitle-export", "gpu", "-o", str(tmp_path / "a.zip"), "--subtitles", "--raw")
    merged = run(client, "subtitle-export", "gpu", "-o", str(tmp_path / "b.json"), "--columns", "id,title", "--subtitles", "--raw")

    assert archive.exit_code == merged.exit_code == 0, archive.output + merged.output
    default_columns = client.export_calls[0]["columns"].split(",")
    assert (client.export_calls[0]["format"], default_columns[-1]) == ("zip", "subtitles")
    assert "hits" in default_columns
    assert client.export_calls[1]["columns"] == "id,title,subtitles"
    with zipfile.ZipFile(tmp_path / "a.zip") as written:
        assert written.namelist() == ["metadata.jsonl", "subs/a.a.en.srt"]
    assert json.loads((tmp_path / "b.json").read_text(encoding="utf-8"))["result"][0]["id"] == "a"


def test_an_export_with_no_matches_leaves_an_existing_file_alone(run, tmp_path):
    target = tmp_path / "matches.jsonl"
    target.write_bytes(b'{"id": "kept"}\n')

    result = run(FakeClient(exports=[ExportBody(b"", "")]), "subtitle-export", "gpu", "-o", str(target), "--raw")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert (payload["_filmot"]["status"], payload["written"], payload["rows"]) == ("empty", False, 0)
    assert target.read_bytes() == b'{"id": "kept"}\n'


@pytest.mark.parametrize("arguments,message", [
    (["--subtitles"], "--subtitles needs --format zip, json, or jsonl"),
    (["--columns", "id,votes"], "unknown --columns votes"),
    (["--order", "asc"], "--order needs --sort"),
    (["--sort", "bm25match", "--order", "asc"], "--sort bm25match has its own order"),
    (["--channel-id", " , "], "--channel-id names no channel"),
    (["--exclude-channels", ","], "--exclude-channels names no channel"),
])
def test_subtitle_export_refuses_what_it_cannot_write(run, tmp_path, arguments, message):
    client = FakeClient()

    result = run(client, "subtitle-export", "gpu", "-o", str(tmp_path / "out.csv"), *arguments)

    assert result.exit_code == 2
    assert message in result.output + result.stderr
    assert client.export_calls == []


def test_a_failed_later_page_keeps_the_pages_before_it(run, tmp_path):
    failure = {"error": "No credits left", "error_type": "FilmotQuotaExhausted"}
    target = tmp_path / "matches.jsonl"

    result = run(
        FakeClient(exports=[jsonl("a", "b"), failure]), "subtitle-export", "gpu", "-o", str(target),
        "--per-page", "2", "--pages", "3", "--raw",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert (payload["_filmot"]["status"], payload["rows"], payload["next_page"]) == ("partial", 2, 2)
    assert target.read_bytes() == b'{"id": "a"}\n{"id": "b"}\n'


def test_failures_before_any_page_write_nothing(run, tmp_path):
    target = tmp_path / "matches.jsonl"
    quota = {"error": "No credits left", "error_type": "FilmotQuotaExhausted"}

    failed = run(FakeClient(exports=[quota]), "subtitle-export", "gpu", "-o", str(target), "--raw")
    malformed = run(FakeClient(exports=[ExportBody(b"not json\n", "")]), "subtitle-export", "gpu", "-o", str(target), "--raw")

    assert failed.exit_code == malformed.exit_code == 1
    assert json.loads(failed.stdout)["_filmot"]["errors"][0]["type"] == "FilmotQuotaExhausted"
    error = json.loads(malformed.stdout)["_filmot"]["errors"][0]
    assert (error["type"], error["stage"]) == ("ExportFormatError", "invalid-response")
    assert not target.exists()


def test_a_write_failure_is_reported_as_such(run, tmp_path):
    with patch("filmot.commands.discovery.write_atomically", side_effect=PermissionError("in use")):
        result = run(FakeClient(exports=[jsonl("a")]), "subtitle-export", "gpu", "-o", str(tmp_path / "m.jsonl"), "--raw")

    assert result.exit_code == 1
    error = json.loads(result.stdout)["_filmot"]["errors"][0]
    assert (error["type"], error["stage"]) == ("PermissionError", "write-output")
