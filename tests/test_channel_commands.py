"""The channel and channel-videos commands."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import filmot.config as app_config
from filmot.api import VIDEO_METADATA_COLUMNS
from filmot.cli import cli


FIXTURES = Path(__file__).parent / "fixtures" / "filmot_api"
LTT = "UCXuqSBlHAE6Xw-yeJA0Tunw"
OTHER = "UC_Kn-LYuJPFq62K8mX5Hmow"
VIDEO_IDS = ("aaaaaaaaaa1", "aaaaaaaaaa2", "aaaaaaaaaa3")


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def channel_record(name="get_channels_all.json"):
    return dict(fixture(name)[0], channelid=LTT)


def listing_rows():
    return [
        dict(row, id=video_id, channelid=LTT, channelname="Linus Tech Tips")
        for row, video_id in zip(fixture("get_channel_videos.json"), VIDEO_IDS)
    ]


class FakeClient:
    def __init__(self, channels=None, pages=(), suggestions=()):
        self.channels = channels
        self.pages = list(pages)
        self.suggestions = list(suggestions)
        self.searches, self.channel_calls, self.listing_calls = [], [], []

    def search_channels(self, term):
        self.searches.append(term)
        return self.suggestions

    def get_channels(self, channel_ids, **params):
        self.channel_calls.append(dict(params, ids=list(channel_ids)))
        return self.channels

    def get_channel_videos(self, channel_ids, **params):
        self.listing_calls.append(dict(params, ids=list(channel_ids)))
        return self.pages.pop(0)


@pytest.fixture
def run(stream_runner):
    def invoke(client, *args, **kwargs):
        with patch("filmot.commands.channel.FilmotClient", return_value=client):
            return stream_runner.invoke(cli, list(args), env={"COLUMNS": "200"}, **kwargs)

    return invoke


# ------------------------------------------------------------------ channel


def test_channel_shows_the_profile_and_every_requested_group(run):
    client = FakeClient(channels=[channel_record()])

    result = run(client, "channel", LTT, "--all")

    assert result.exit_code == 0, result.output
    assert client.channel_calls[0]["columns"] == "all"
    for text in (
        "Recorded channel 001", "Subscribers: 21,400,000", "Indexed videos: 582 (75 partial)",
        "Growth (observed days in parentheses)", "Subscriber observations: 3",
        "Top caption terms:", "Previous profile values", "Video statistics: ",
        "1h 30m+", "Per-video distributions", "Caption languages: en", "By upload month",
    ):
        assert text in result.stdout, text


def test_channel_renders_nulls_and_additions_the_contract_allows(run):
    record = channel_record()
    record.update(name=None, handle=None, subcount=None, counts={"indexedvideos": None})
    record["growth"]["source"] = "metadata"
    record["growth"]["subcount"]["28d"]["actualdays"] = None
    record["growth"]["totalviewcount"]["90d"] = None
    record["channelchanges"].update(futurefield={"note": "new"}, country=None)
    record["videostats"]["videotypes"]["premieres"] = "n/a"
    record["videostats"]["distributions"]["commentcount"] = "later"
    record["videostats"]["distributions"]["duration"]["unit"] = "seconds"

    result = run(FakeClient(channels=[record]), "channel", LTT, "--all")

    assert result.exit_code == 0, result.output
    assert "Unknown" in result.stdout
    assert "Subscribers: N/A" in result.stdout
    assert "(?d)" in result.stdout
    assert "Noned" not in result.stdout


def test_channel_raw_reports_what_was_asked_and_what_is_missing(run):
    client = FakeClient(channels=[channel_record("get_channels.json")])

    result = run(client, "channel", "{},{}".format(LTT, OTHER), "--raw")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["_filmot"]["status"] == "completed"
    assert [entry["channel_id"] for entry in payload["requested"]] == [LTT, OTHER]
    assert payload["missing"] == [OTHER]
    assert payload["columns"] is None
    assert payload["channels"][0]["counts"]["indexedvideos"] == 582


def test_channel_sends_groups_dates_and_archive(run):
    client = FakeClient(channels=[channel_record()])

    result = run(
        client, "channel", LTT, "--growth", "--stats", "--since", "2026-01-01",
        "--until", "2026-03-01", "--include-archive",
    )

    assert result.exit_code == 0, result.output
    assert client.channel_calls == [{
        "ids": [LTT],
        "columns": "growth,subscribergrowth,videostats,distributionstats,languagestats",
        "start_date": "2026-01-01",
        "end_date": "2026-03-01",
        "include_archive": True,
    }]


def test_a_handle_is_resolved_before_the_profile_lookup(run):
    client = FakeClient(
        channels=[channel_record("get_channels.json")],
        suggestions=[{"channelid": LTT, "name": "Linus Tech Tips", "handle": "@LinusTechTips"}],
    )

    result = run(client, "channel", "@linustechtips", "--raw")

    assert result.exit_code == 0, result.output
    assert client.searches == ["linustechtips"]
    assert client.channel_calls[0]["ids"] == [LTT]


@pytest.mark.parametrize("arguments,message", [
    (["--since", "2026-01-01"], "--since and --until apply to"),
    (["--growth", "--include-archive"], "--include-archive applies to"),
    (["--stats", "--since", "2026-02-01", "--until", "2026-01-01"], "--since must not be later"),
])
def test_options_that_would_have_no_effect_are_refused(run, arguments, message):
    client = FakeClient()

    human = run(client, "channel", LTT, *arguments)
    raw = run(client, "channel", LTT, *arguments, "--raw")

    assert human.exit_code == 2
    assert message in human.output + human.stderr
    assert raw.exit_code == 1
    assert json.loads(raw.stdout)["_filmot"]["errors"][0]["type"] == "BadParameter"
    assert client.channel_calls == []


@pytest.mark.parametrize("command", ["channel", "channel-videos"])
def test_an_empty_channel_list_is_refused(run, command):
    client = FakeClient()

    result = run(client, command, " , ")

    assert result.exit_code == 2
    assert "name at least one channel" in result.output + result.stderr
    assert (client.channel_calls, client.listing_calls) == ([], [])


def test_channel_needs_the_direct_api_before_spending_a_handle_lookup(run, monkeypatch):
    monkeypatch.setattr(app_config, "DIRECT_API_KEY", "")
    client = FakeClient(suggestions=[{"channelid": LTT, "handle": "@LinusTechTips"}])

    result = run(client, "channel", "@LinusTechTips", "--raw")

    assert result.exit_code == 1
    assert "FILMOT_API_KEY" in result.stdout
    assert client.searches == []


def test_channel_api_failures_keep_their_error_type(run):
    failure = {"error": "No credits left", "error_type": "FilmotQuotaExhausted", "provider": "direct"}

    result = run(FakeClient(channels=failure), "channel", LTT, "--raw")

    assert result.exit_code == 1
    assert json.loads(result.stdout)["_filmot"]["errors"][0]["type"] == "FilmotQuotaExhausted"


def test_an_unknown_handle_is_a_typed_failure(run):
    result = run(FakeClient(suggestions=[]), "channel", "@nobody", "--raw")

    assert result.exit_code == 1
    error = json.loads(result.stdout)["_filmot"]["errors"][0]
    assert (error["type"], error["stage"]) == ("ChannelNotFound", "resolve-channel")


# ----------------------------------------------------------- channel-videos


def test_channel_videos_lists_rows_and_points_at_the_next_page(run):
    client = FakeClient(pages=[listing_rows()])

    result = run(client, "channel-videos", LTT, "--count", "3", "--with-subtitles")

    assert result.exit_code == 0, result.output
    assert client.listing_calls == [{
        "ids": [LTT], "count_per_channel": 3, "page": 1,
        "only_with_subtitles": True, "columns": VIDEO_METADATA_COLUMNS,
    }]
    assert "Recorded video title 002" in result.stdout
    assert "(partial record)" in result.stdout
    assert "en (manual)" in result.stdout
    assert "More may be available: add --page 2." in result.stdout


def test_channel_videos_raw_output_pipes_into_download(run, stream_runner):
    listed = run(FakeClient(pages=[listing_rows()]), "channel-videos", LTT, "--count", "3", "--raw")
    assert listed.exit_code == 0, listed.output
    fetched = []

    def get_transcript(video_id, **kwargs):
        fetched.append(video_id)
        return {
            "video_id": video_id, "full_text": "words", "language": "en",
            "segments": [{"text": "words", "start": 0.0, "duration": 1.0}],
            "duration_seconds": 1.0, "segment_count": 1, "source": "youtube", "route": "direct",
        }

    with (
        patch("filmot.library.get_library") as get_library,
        patch("filmot.transcript.get_transcript", side_effect=get_transcript),
    ):
        get_library.return_value.exists.return_value = False
        get_library.return_value.list_transcripts.return_value = []
        downloaded = stream_runner.invoke(
            cli, ["download", "--topic", "ltt", "--count", "3"], input=listed.stdout,
        )

    assert downloaded.exit_code == 0, downloaded.output
    assert fetched == list(VIDEO_IDS)


def test_channel_videos_refuses_more_than_200_videos_per_page(run):
    client = FakeClient()

    result = run(client, "channel-videos", LTT, OTHER, "--count", "150")

    assert result.exit_code == 2
    assert "may not exceed 200 (got 2 x 150)" in result.output + result.stderr
    assert client.listing_calls == []


@pytest.mark.parametrize("selection", [["--all"], ["--columns", "subtitles,lastfetchdate"]])
def test_channel_videos_subtitle_selection_adjusts_default_count_and_keeps_tracks(run, selection):
    rows = listing_rows()
    rows[0]["subtitles"] = [{"lang": "en", "isauto": 1, "transcript": [{"s": 1.5, "t": "Full text"}]}]
    client = FakeClient(pages=[rows])

    result = run(client, "channel-videos", LTT, OTHER, *selection, "--lang", "en", "--raw")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["videos"][0]["subtitles"] == rows[0]["subtitles"]
    assert payload["count_per_channel"] == 5
    assert client.listing_calls[0]["count_per_channel"] == 5
    assert client.listing_calls[0]["lang"] == "en"


@pytest.mark.parametrize("options", [
    ["--columns", "all", "--count", "11"],
    ["--all", "--count", "20"],
    ["--columns", "invented"],
    ["--columns", "all,title"],
    ["--all", "--columns", "description"],
    ["--lang", "x" * 21],
])
def test_channel_videos_refuses_invalid_subtitle_requests_before_listing(run, options):
    client = FakeClient()

    result = run(client, "channel-videos", LTT, *options, "--raw")

    assert result.exit_code != 0
    assert json.loads(result.stdout)["_filmot"]["status"] == "failed"
    assert client.listing_calls == []


def test_channel_videos_keeps_earlier_pages_when_a_later_one_fails(run):
    failure = {"error": "service unavailable", "error_type": "FilmotServiceError"}
    client = FakeClient(pages=[listing_rows(), failure])

    result = run(client, "channel-videos", LTT, "--count", "3", "--pages", "3", "--raw")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["_filmot"]["status"] == "partial"
    assert payload["_filmot"]["errors"][0]["type"] == "FilmotServiceError"
    assert (len(payload["videos"]), payload["pages_fetched"], payload["next_page"]) == (3, 1, 2)


def test_impossible_subtitle_count_is_rejected_before_paid_handle_resolution(run):
    client = FakeClient()

    result = run(client, "channel-videos", "@LinusTechTips", "--all", "--count", "11", "--raw")

    assert result.exit_code == 1
    assert client.searches == []
    assert client.listing_calls == []


def test_channel_videos_fails_when_the_first_page_fails(run):
    failure = {"error": "No credits left", "error_type": "FilmotQuotaExhausted"}

    result = run(FakeClient(pages=[failure]), "channel-videos", LTT, "--raw")

    assert result.exit_code == 1
    assert json.loads(result.stdout)["_filmot"]["errors"][0]["type"] == "FilmotQuotaExhausted"


def test_channel_videos_reports_the_end_of_the_listing(run):
    client = FakeClient(pages=[listing_rows(), []])

    result = run(client, "channel-videos", LTT, "--count", "3", "--pages", "4")

    assert result.exit_code == 0, result.output
    assert [call["page"] for call in client.listing_calls] == [1, 2]
    assert "End of Filmot's listing for these channels." in result.stdout
