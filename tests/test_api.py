"""Tests for Filmot API query rewriting."""

from unittest.mock import patch

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
