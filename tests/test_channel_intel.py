"""Channel references, analytics groups, and paged listings for Filmot channels."""

import pytest

from filmot.channel_intel import (
    ChannelReferenceError,
    channel_columns,
    channel_credits,
    fetch_channel_videos,
    resolve_channels,
)


LTT = "UCXuqSBlHAE6Xw-yeJA0Tunw"
OTHER = "UC_Kn-LYuJPFq62K8mX5Hmow"


class SearchClient:
    """Answers channel searches; refuses listing calls."""

    def __init__(self, suggestions):
        self.suggestions = suggestions
        self.terms = []

    def search_channels(self, term):
        self.terms.append(term)
        return self.suggestions


def suggestion(handle, channel_id):
    return {"channelid": channel_id, "name": "Channel", "handle": handle}


# ------------------------------------------------------------------ groups


@pytest.mark.parametrize("groups,columns,credits", [
    ([], None, 1),
    (["about", "growth"], "description,biography,growth,subscribergrowth", 1),
    (["words"], "wordcloud", 1),
    (["history"], "channelchanges", 3),
    (["stats", "monthly"], "videostats,distributionstats,languagestats,monthlystats", 3),
    (["all"], "all", 3),
])
def test_options_map_to_columns_and_credits(groups, columns, credits):
    assert channel_columns(groups) == columns
    assert channel_credits(groups) == credits


# -------------------------------------------------------------- references


def test_ids_and_channel_urls_need_no_lookup():
    client = SearchClient([])

    resolved = resolve_channels(
        [LTT, "https://www.youtube.com/channel/{}".format(OTHER)], client, limit=20,
    )

    assert [entry["channel_id"] for entry in resolved] == [LTT, OTHER]
    assert {entry["resolved_by"] for entry in resolved} == {"id"}
    assert client.terms == []


@pytest.mark.parametrize("reference", ["@LinusTechTips", "@linustechtips", "https://www.youtube.com/@LinusTechTips"])
def test_handles_resolve_only_on_an_exact_handle_match(reference):
    client = SearchClient([
        suggestion("@LinusTechTipsRussia", OTHER),
        suggestion(None, "UCnohandle0000000000000a"),
        suggestion("@LinusTechTips", LTT),
    ])

    resolved = resolve_channels([reference], client, limit=20)

    assert resolved == [{"reference": reference, "channel_id": LTT, "resolved_by": "handle"}]
    assert len(client.terms) == 1


def test_a_handle_without_an_exact_match_is_refused():
    client = SearchClient([suggestion("@LinusTechTipsRussia", OTHER)])

    with pytest.raises(ChannelReferenceError) as caught:
        resolve_channels(["@LinusTechTips"], client, limit=20)

    assert caught.value.error_type == "ChannelNotFound"
    assert "pass its UC channel ID" in str(caught.value)


def test_a_failed_handle_lookup_keeps_the_api_error():
    failure = {"error": "quota exhausted", "error_type": "FilmotQuotaExhausted"}

    with pytest.raises(ChannelReferenceError) as caught:
        resolve_channels(["@LinusTechTips"], SearchClient(failure), limit=20)

    assert caught.value.error_type == "FilmotQuotaExhausted"
    assert caught.value.payload == failure


def test_a_channel_named_twice_is_kept_once_in_order():
    client = SearchClient([suggestion("@LinusTechTips", LTT)])

    resolved = resolve_channels([OTHER, "@LinusTechTips", LTT], client, limit=2)

    assert [entry["channel_id"] for entry in resolved] == [OTHER, LTT]


@pytest.mark.parametrize("references", [
    [OTHER, "@LinusTechTips"],
    [OTHER, "UCnohandle0000000000000a", "@LinusTechTips"],
])
def test_too_many_channels_are_refused_before_more_handles_are_looked_up(references):
    client = SearchClient([suggestion("@LinusTechTips", LTT)])

    with pytest.raises(ChannelReferenceError) as too_many:
        resolve_channels(references, client, limit=1)

    assert too_many.value.error_type == "TooManyChannels"
    assert client.terms == []


def test_a_repeated_handle_is_looked_up_once():
    client = SearchClient([suggestion("@LinusTechTips", LTT)])

    resolved = resolve_channels(["@LinusTechTips", "@linustechtips"], client, limit=1)

    assert [entry["channel_id"] for entry in resolved] == [LTT]
    assert client.terms == ["LinusTechTips"]


def test_unsupported_references_are_refused():
    with pytest.raises(ChannelReferenceError) as unsupported:
        resolve_channels(["https://www.youtube.com/c/LegacyName"], SearchClient([]), limit=20)

    assert unsupported.value.error_type == "ChannelReferenceError"


# ---------------------------------------------------------------- listings


class ListingClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get_channel_videos(self, channel_ids, **params):
        self.calls.append(dict(params, channel_ids=list(channel_ids)))
        return self.pages.pop(0)


def rows(*ids):
    return [{"id": video_id, "title": video_id, "channelid": LTT} for video_id in ids]


def test_listing_pages_until_an_empty_page():
    client = ListingClient([rows("a", "b"), rows("c"), []])

    listing = fetch_channel_videos(client, [LTT], count=2, pages=5, columns=("viewcount",))

    assert [video["id"] for video in listing["videos"]] == ["a", "b", "c"]
    assert (listing["pages_fetched"], listing["exhausted"], listing["next_page"]) == (3, True, None)
    assert [call["page"] for call in client.calls] == [1, 2, 3]
    assert client.calls[0] == {
        "channel_ids": [LTT], "count_per_channel": 2, "page": 1,
        "only_with_subtitles": False, "columns": ("viewcount",),
    }


def test_a_short_page_is_not_taken_as_the_end():
    # Filmot documents only an empty page as the end of a listing.
    client = ListingClient([rows("a")])

    listing = fetch_channel_videos(client, [LTT], count=5, first_page=3, pages=1)

    assert (listing["exhausted"], listing["next_page"]) == (False, 4)


def test_listing_forwards_subtitle_language_on_every_page():
    client = ListingClient([rows("a"), []])

    fetch_channel_videos(client, [LTT], count=10, pages=2, columns="subtitles", lang="en")

    assert [call["lang"] for call in client.calls] == ["en", "en"]
    assert all(call["columns"] == "subtitles" for call in client.calls)


def test_videos_repeated_across_pages_are_listed_once():
    client = ListingClient([rows("a", "b"), rows("b", "c")])

    listing = fetch_channel_videos(client, [LTT], count=2, pages=2)

    assert [video["id"] for video in listing["videos"]] == ["a", "b", "c"]


def test_a_failed_page_keeps_earlier_pages_and_points_at_the_failure():
    failure = {"error": "service unavailable", "error_type": "FilmotServiceError"}
    client = ListingClient([rows("a"), failure])

    listing = fetch_channel_videos(client, [LTT], count=1, pages=3)

    assert [video["id"] for video in listing["videos"]] == ["a"]
    assert listing["error"] == failure
    assert (listing["pages_fetched"], listing["exhausted"], listing["next_page"]) == (1, False, 2)
