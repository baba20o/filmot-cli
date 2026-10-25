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
from filmot.discovery_search import DEFAULT_TITLE_COLUMNS, VIDEO_BASE_COLUMNS


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

    export_metadata_search = export_subtitle_search
    export_advanced_channel_search = export_subtitle_search


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
        "columns": ",".join(DEFAULT_TITLE_COLUMNS),
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


def test_titles_selects_enrichment_without_losing_video_identity(run):
    client = FakeClient(metadata=[metadata_page()])
    result = run(client, "titles", "gpu", "--columns", "description,dislikecount", "--raw")
    assert result.exit_code == 0, result.output
    assert client.metadata_calls[0]["columns"].split(",") == [*VIDEO_BASE_COLUMNS, "description", "dislikecount"]


@pytest.mark.parametrize("selection", [("--all",), ("--columns", "all"), ("--columns", "subtitles")])
def test_titles_raw_subtitles_adjust_default_size_and_allow_page_5000(run, selection):
    client = FakeClient(metadata=[metadata_page()])
    result = run(client, "titles", "gpu", *selection, "--page", "5000", "--pages", "2", "--raw")
    assert result.exit_code == 0, result.output
    assert len(client.metadata_calls) == 1
    assert client.metadata_calls[0]["perPageCount"] == 10
    assert json.loads(result.stdout)["last_page_reached"] is True


@pytest.mark.parametrize("args", [
    ("--page", "501"), ("--all", "--per-page", "11"),
    ("--columns", "votes"), ("--columns", ","), ("--all", "--columns", "title"),
    ("--format", "jsonl"),
])
def test_titles_rejects_invalid_selection_and_conditional_limits_before_requests(run, args):
    client = FakeClient()
    result = run(client, "titles", "gpu", *args, "--raw")
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["_filmot"]["errors"][0]["stage"] == "validate-request"
    assert not client.metadata_calls and not client.export_calls


@pytest.mark.parametrize("selection,expected", [
    (("--all",), "all"), (("--columns", "description,topiccloud"), "channelid,description,topiccloud"),
])
def test_find_channels_requests_enriched_fields(run, selection, expected):
    client = FakeClient(channels=[fixture("advanced_channel_search.json")])
    result = run(client, "find-channels", *selection, "--raw")
    assert result.exit_code == 0, result.output
    assert client.channel_calls[0]["columns"] == expected


@pytest.mark.parametrize("arguments", [
    ("--description", "x" * 501), ("--topics", "x" * 501),
    ("--auto-lang", "x" * 21), ("--auto-lang", ","),
    tuple(item for n in range(101) for item in ("--auto-lang", "en")),
    ("--columns", "unsupported"), ("--format", "jsonl"),
])
def test_find_channels_rejects_out_of_contract_options_before_requests(run, arguments):
    client = FakeClient()
    result = run(client, "find-channels", *arguments, "--raw")
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["_filmot"]["errors"][0]["stage"] == "validate-request"
    assert not client.channel_calls and not client.export_calls


@pytest.mark.parametrize("option,expected", [("--no-guess-video-language", 1), ("--guess-video-language", 0)])
def test_subtitle_export_selects_all_and_manual_language_policy(run, tmp_path, option, expected):
    client = FakeClient(exports=[jsonl("a")])
    result = run(client, "subtitle-export", "gpu", "--manual-subs", option,
                 "--columns", "all", "--subtitles", "-o", str(tmp_path / "all.jsonl"), "--raw")
    assert result.exit_code == 0, result.output
    assert client.export_calls[0]["columns"] == "all"
    assert client.export_calls[0]["noGuessVideoLanguage"] == expected
    assert client.export_calls[0]["searchManualSubs"] == 1


def test_subtitle_export_rejects_overlong_language_before_requests(run, tmp_path):
    client = FakeClient()
    result = run(client, "subtitle-export", "gpu", "--lang", "x" * 21, "-o", str(tmp_path / "m.jsonl"), "--raw")
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["_filmot"]["errors"][0]["stage"] == "validate-request"
    assert not client.export_calls


@pytest.mark.parametrize("command,args", [("titles", ("gpu",)), ("find-channels", ())])
def test_discovery_exports_continue_past_short_pages(run, tmp_path, command, args):
    client = FakeClient(exports=[jsonl("a"), jsonl("b"), jsonl()])
    path = tmp_path / "matches.jsonl"
    result = run(client, command, *args, "--output", str(path), "--pages", "4", "--raw")
    assert result.exit_code == 0, result.output
    assert path.read_bytes() == b'{"id": "a"}\n{"id": "b"}\n'
    assert [request["page"] for request in client.export_calls] == [1, 2, 3]
    summary = json.loads(result.stdout)
    assert summary["written"] and summary["exhausted"] and summary["rows"] == 2


def test_title_zip_export_obeys_subtitle_limit(run, tmp_path):
    client = FakeClient(exports=[zip_body("a")])
    path = tmp_path / "matches.zip"
    result = run(client, "titles", "gpu", "--all", "--page", "5000", "--pages", "2", "--output", str(path), "--raw")
    assert result.exit_code == 0, result.output
    assert client.export_calls == [{
        "titleQuery": "gpu", "columns": "all", "page": 5000, "perPageCount": 10,
        "format": "zip", "exportInline": 1,
    }]
    with zipfile.ZipFile(path) as archive:
        assert "subs/a.a.en.srt" in archive.namelist()


def test_find_channel_json_export_stops_at_result_window(run, tmp_path):
    body = ExportBody(b'{"result":[{"channelid":"UCtest"}],"page":34,"maxresults":10000}', "")
    client = FakeClient(exports=[body])
    path = tmp_path / "channels.json"
    result = run(client, "find-channels", "--all", "--per-page", "300", "--page", "34", "--pages", "2", "-o", str(path), "--raw")
    assert result.exit_code == 0, result.output
    assert len(client.export_calls) == 1
    assert json.loads(path.read_bytes())["result"][0]["channelid"] == "UCtest"
    assert json.loads(result.stdout)["last_page_reached"] is True


def test_malformed_later_export_page_keeps_valid_prefix_and_cursor(run, tmp_path):
    client = FakeClient(exports=[jsonl("a", "b"), ExportBody(b"<html>broken</html>", "")])
    path = tmp_path / "partial.jsonl"
    result = run(client, "subtitle-export", "gpu", "--pages", "2", "--per-page", "2", "-o", str(path), "--raw")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert path.read_bytes() == b'{"id": "a"}\n{"id": "b"}\n'
    assert payload["_filmot"]["status"] == "partial"
    assert payload["next_page"] == 2
    assert payload["_filmot"]["errors"][0]["stage"] == "invalid-response"


def test_subtitle_export_all_columns_preserves_multiline_csv_across_pages(run, tmp_path):
    import csv

    first = b'id,subtitles\r\na,"line one\nline two"\r\n'
    second = b'id,subtitles\r\nb,"another line\nlast line"\r\n'
    client = FakeClient(exports=[ExportBody(first, "text/csv"), ExportBody(second, "text/csv")])
    path = tmp_path / "subtitles.csv"
    result = run(client, "subtitle-export", "gpu", "--columns", "all", "--subtitles",
                 "--pages", "2", "--per-page", "1", "-o", str(path), "--raw")
    assert result.exit_code == 0, result.output
    assert all(request["columns"] == "all" for request in client.export_calls)
    with path.open(newline="") as stream:
        assert list(csv.reader(stream)) == [
            ["id", "subtitles"], ["a", "line one\nline two"], ["b", "another line\nlast line"],
        ]
    assert json.loads(result.stdout)["rows"] == 2


@pytest.mark.parametrize("bad_page", [b"title,id\nWrong,b\n", b'id,title\nb,"unterminated'])
def test_invalid_later_csv_page_writes_only_valid_prefix_as_typed_partial(run, tmp_path, bad_page):
    first = b"id,title\na,First\n"
    client = FakeClient(exports=[ExportBody(first, ""), ExportBody(bad_page, "")])
    path = tmp_path / "partial.csv"
    result = run(client, "subtitle-export", "gpu", "--per-page", "1", "--pages", "2", "-o", str(path), "--raw")
    assert result.exit_code == 0, result.output
    assert path.read_bytes() == first
    summary = json.loads(result.stdout)
    assert summary["_filmot"]["status"] == "partial" and summary["next_page"] == 2
    assert summary["_filmot"]["errors"][0]["type"] == "ExportFormatError"


def test_large_csv_caption_field_is_exported_without_changing_parser_limit(run, tmp_path):
    import csv

    original_limit = csv.field_size_limit()
    body = b'id,subtitles\na,"' + b"x" * (original_limit + 100) + b'"\n'
    client = FakeClient(exports=[ExportBody(body, "")])
    path = tmp_path / "large.csv"
    result = run(client, "subtitle-export", "gpu", "--subtitles", "-o", str(path), "--raw")
    assert result.exit_code == 0, result.output
    assert path.read_bytes() == body
    assert csv.field_size_limit() == original_limit


def test_find_channel_json_export_preserves_each_page_envelope(run, tmp_path):
    pages = [
        {"result": [{"channelid": channel_id}], "page": page, "perpagecount": 2, "maxresults": 10000}
        for page, channel_id in [(7, "UCfirst"), (8, "UCsecond")]
    ]
    pages[1]["result"][0]["name"] = "\ud800"
    client = FakeClient(exports=[ExportBody(json.dumps(page).encode(), "") for page in pages])
    path = tmp_path / "channels.json"
    result = run(client, "find-channels", "--page", "7", "--pages", "2", "--per-page", "2", "-o", str(path), "--raw")
    assert result.exit_code == 0, result.output
    merged = json.loads(path.read_bytes())
    assert merged["result"] == [{"channelid": "UCfirst"}, {"channelid": "UCsecond", "name": "\ud800"}]
    assert merged["pages"] == [
        {"page": 7, "perpagecount": 2, "maxresults": 10000},
        {"page": 8, "perpagecount": 2, "maxresults": 10000},
    ]
    assert "page" not in merged and "perpagecount" not in merged
    assert json.loads(result.stdout)["next_page"] == 9


def test_duplicate_zip_metadata_is_a_typed_partial_without_replacing_prior_pages(run, tmp_path):
    duplicate = io.BytesIO()
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("metadata.jsonl", '{"id":"b"}\n')
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("metadata.jsonl", '{"id":"c"}\n')
    first = zip_body("a")
    client = FakeClient(exports=[first, ExportBody(duplicate.getvalue(), "application/zip")])
    path = tmp_path / "partial.zip"
    result = run(client, "subtitle-export", "gpu", "--pages", "2", "--per-page", "1", "-o", str(path), "--raw")
    assert result.exit_code == 0, result.output
    assert path.read_bytes() == first.content
    summary = json.loads(result.stdout)
    assert summary["rows"] == 1 and summary["next_page"] == 2
    assert summary["_filmot"]["status"] == "partial"
    assert summary["_filmot"]["errors"][0]["type"] == "ExportFormatError"
