"""Tests for Filmot API query rewriting."""

from unittest.mock import MagicMock, patch

from filmot.api import FilmotClient, _rewrite_quoted_or_in_proximity_query


def test_rewrites_pipe_phrase_in_left_near_operand():
    query = '"memory|context" NEAR/20 "production"'
    rewritten, rewrite = _rewrite_quoted_or_in_proximity_query(query)

    assert rewritten == '("memory" | "context") NEAR/20 "production"'
    assert rewrite == {"from": query, "to": rewritten}


def test_rewrites_pipe_phrase_in_right_near_operand():
    query = '"agent memory" NEAR/20 "vector|graph|database"'
    rewritten, rewrite = _rewrite_quoted_or_in_proximity_query(query)

    assert rewritten == '"agent memory" NEAR/20 ("vector" | "graph" | "database")'
    assert rewrite == {"from": query, "to": rewritten}


def test_leaves_grouped_or_proximity_query_unchanged():
    query = '("memory" | "context") NEAR/20 "production"'
    rewritten, rewrite = _rewrite_quoted_or_in_proximity_query(query)

    assert rewritten == query
    assert rewrite is None


@patch("filmot.api.get_headers", return_value={})
@patch("filmot.api.validate_config")
def test_search_subtitles_uses_rewritten_query(mock_validate, mock_headers):
    with patch.object(FilmotClient, "get", return_value={"result": []}) as mock_get:
        client = FilmotClient(use_cache=False)
        client.search_subtitles(query='"Letta|Mem0" NEAR/15 "production"')

    assert mock_get.call_args.kwargs["params"]["query"] == '("Letta" | "Mem0") NEAR/15 "production"'
    assert client.last_query_rewrite == {
        "from": '"Letta|Mem0" NEAR/15 "production"',
        "to": '("Letta" | "Mem0") NEAR/15 "production"',
    }


@patch("filmot.api.get_headers", return_value={})
@patch("filmot.api.validate_config")
def test_search_subtitles_preserves_valid_grouped_query(mock_validate, mock_headers):
    query = '("memory" | "context") NEAR/20 "production"'

    with patch.object(FilmotClient, "get", return_value={"result": []}) as mock_get:
        client = FilmotClient(use_cache=False)
        client.search_subtitles(query=query)

    assert mock_get.call_args.kwargs["params"]["query"] == query
    assert client.last_query_rewrite is None


@patch("filmot.api.get_headers", return_value={})
@patch("filmot.api.validate_config")
def test_request_uses_bounded_connect_and_read_timeout(mock_validate, mock_headers):
    client = FilmotClient(use_cache=False, request_timeout=(3, 9))
    response = client.session.request = MagicMock()
    response.return_value.raise_for_status.return_value = None
    response.return_value.json.return_value = {"result": []}

    client.get("/endpoint")

    assert response.call_args.kwargs["timeout"] == (3, 9)


@patch("filmot.api.get_headers", return_value={})
@patch("filmot.api.validate_config")
def test_paginated_search_marks_later_page_failure_as_partial(
    mock_validate, mock_headers
):
    client = FilmotClient(use_cache=False)
    page_one = {
        "result": [{"id": f"v{i}"} for i in range(50)],
        "totalresultcount": 100,
    }
    with patch.object(
        client,
        "search_subtitles",
        side_effect=[page_one, {"error": "page two failed"}],
    ):
        result = client.search_subtitles_all("query", max_pages=3)

    assert result["results_returned"] == 50
    assert result["partial"] is True
    assert result["page_error"] == "page two failed"


@patch("filmot.api.get_headers", return_value={})
@patch("filmot.api.validate_config")
def test_paginated_search_deduplicates_video_ids_before_limit(
    mock_validate, mock_headers
):
    client = FilmotClient(use_cache=False)
    pages = [
        {
            "result": [{"id": "a"}, {"id": "b"}],
            "totalresultcount": 4,
        },
        {
            "result": [{"id": "b"}, {"id": "c"}],
            "totalresultcount": 4,
        },
    ]
    with patch.object(
        client,
        "search_subtitles_paginated",
        return_value=iter(pages),
    ):
        result = client.search_subtitles_all(
            "query",
            max_pages=2,
            max_results=3,
        )

    assert [video["id"] for video in result["result"]] == ["a", "b", "c"]
    assert result["duplicates_skipped"] == 1


@patch("filmot.api.get_headers", return_value={})
@patch("filmot.api.validate_config")
def test_empty_first_page_still_counts_as_fetched_scope(
    mock_validate, mock_headers
):
    client = FilmotClient(use_cache=False)
    with patch.object(
        client,
        "search_subtitles",
        return_value={"result": [], "totalresultcount": 0},
    ):
        result = client.search_subtitles_all("query", max_pages=3)

    assert result["result"] == []
    assert result["pages_fetched"] == 1
    assert result["totalresultcount"] == 0
