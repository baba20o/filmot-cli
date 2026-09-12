"""Contracts for provider-neutral discovery candidate normalization."""

from copy import deepcopy
import json

import pytest

from filmot.discovery import (
    DiscoveryArtifactError,
    DiscoveryValidationError,
    ProviderFieldLimits,
    extract_candidates,
    normalize_candidate,
    normalize_candidates,
    preflight_candidates,
)


def test_filmot_aliases_normalize_without_losing_observed_zero():
    source = {
        "id": "synthetic-filmot-id",
        "title": "Field study",
        "description": "Result summary",
        "channelname": "Research channel",
        "channelid": "UC_SYNTHETIC",
        "uploaddate": "2026-08-01",
        "viewcount": 0,
        "likecount": "0",
        "commentcount": None,
        "duration": 90,
        "hits": [{"start": 4, "token": "field"}],
    }

    candidate = normalize_candidate(source)

    assert candidate == {
        "video_id": "synthetic-filmot-id",
        "title": "Field study",
        "description": "Result summary",
        "channel_title": "Research channel",
        "channel_id": "UC_SYNTHETIC",
        "published_at": "2026-08-01",
        "views": 0,
        "likes": 0,
        "comments": None,
        "duration": 90,
        "url": None,
        "provider": "filmot",
        "provenance": {},
        "provider_fields": {"hits": [{"start": 4, "token": "field"}]},
    }


@pytest.mark.parametrize("field", ["result", "videos", "items"])
def test_candidate_arrays_extract_from_supported_envelopes(field):
    rows = [{"video_id": "test-synthetic-id"}]
    assert extract_candidates({field: rows}) == tuple(rows)


def test_bare_sequence_and_empty_envelope_arrays_are_supported():
    rows = [{"video_id": "one"}, {"video_id": "two"}]
    assert extract_candidates(rows) == tuple(rows)
    assert normalize_candidates({"videos": []}) == []


@pytest.mark.parametrize(
    "artifact,code",
    [
        ({"query": "nothing here"}, "candidate_array_missing"),
        ({"result": [], "videos": []}, "candidate_array_ambiguous"),
        ({"items": {}}, "candidate_array_type"),
        ("not an artifact", "artifact_type"),
    ],
)
def test_invalid_artifact_shapes_have_typed_errors(artifact, code):
    with pytest.raises(DiscoveryArtifactError) as caught:
        extract_candidates(artifact)
    assert caught.value.issue.code == code
    assert code in caught.value.issue.to_dict()["code"]


def test_versioned_yt_search_raw_sets_provider_and_preserves_provenance():
    artifact = {
        "query": "embodied cognition",
        "days": 3,
        "max_results": 25,
        "order": "date",
        "filters": {"region": "US"},
        "videos": [{
            "video_id": "dQw4w9WgXcQ",
            "title": "Recent lecture",
            "views": 0,
        }],
        "_filmot": {
            "schema": "filmot.result/v1",
            "command": "yt-search",
            "status": "completed",
            "warnings": ["not copied into provenance"],
        },
    }

    candidate = normalize_candidates(artifact)[0]

    assert candidate["provider"] == "youtube"
    assert candidate["video_id"] == "dQw4w9WgXcQ"
    assert candidate["views"] == 0
    assert candidate["likes"] is None
    assert candidate["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert candidate["provenance"] == {
        "artifact": {
            "schema": "filmot.result/v1",
            "command": "yt-search",
            "status": "completed",
        },
        "query": "embodied cognition",
        "filters": {"region": "US"},
        "order": "date",
        "days": 3,
        "max_results": 25,
    }


@pytest.mark.parametrize("command", ["yt-video", "yt-playlist"])
def test_exact_youtube_artifact_commands_imply_strict_provider(command):
    artifact = {
        "videos": [{"video_id": "abc12345678", "title": "Curated source"}],
        "_filmot": {
            "schema": "filmot.result/v1",
            "command": command,
            "status": "completed",
        },
    }

    candidate = normalize_candidates(artifact)[0]

    assert candidate["provider"] == "youtube"
    assert candidate["provenance"]["artifact"]["command"] == command


def test_direct_youtube_resource_normalizes_nested_aliases_and_extras():
    resource = {
        "kind": "youtube#video",
        "etag": "etag-value",
        "id": "abc12345678",
        "snippet": {
            "title": "A title",
            "description": "A description",
            "channelTitle": "A channel",
            "channelId": "UC123",
            "publishedAt": "2026-01-02T03:04:05Z",
            "tags": ["one", "two"],
            "categoryId": "28",
        },
        "statistics": {
            "viewCount": "0",
            "likeCount": "12",
            "commentCount": "3",
            "favoriteCount": "0",
        },
        "contentDetails": {"duration": "PT2M", "caption": "true"},
    }

    candidate = normalize_candidate(resource)

    assert candidate["provider"] == "youtube"
    assert candidate["title"] == "A title"
    assert candidate["description"] == "A description"
    assert candidate["channel_title"] == "A channel"
    assert candidate["channel_id"] == "UC123"
    assert candidate["published_at"] == "2026-01-02T03:04:05Z"
    assert candidate["views"] == 0
    assert candidate["likes"] == 12
    assert candidate["comments"] == 3
    assert candidate["duration"] == "PT2M"
    assert candidate["provider_fields"] == {
        "kind": "youtube#video",
        "etag": "etag-value",
        "snippet": {"tags": ["one", "two"], "categoryId": "28"},
        "statistics": {"favoriteCount": "0"},
        "contentDetails": {"caption": "true"},
    }


def test_direct_search_resource_accepts_nested_id_and_detects_url_conflict():
    resource = {
        "id": {"kind": "youtube#video", "videoId": "abc12345678"},
        "snippet": {"title": "Search hit"},
        "url": "https://youtu.be/def12345678",
    }

    with pytest.raises(DiscoveryValidationError) as caught:
        normalize_candidate(resource)

    assert [issue.code for issue in caught.value.issues] == ["conflicting_video_id"]
    assert "abc12345678" not in str(caught.value)
    assert "def12345678" not in str(caught.value)


@pytest.mark.parametrize(
    "record,code",
    [
        ({"title": "Missing"}, "missing_video_id"),
        ({"video_id": "   "}, "malformed_video_id"),
        ({"video_id": {"unexpected": "shape"}}, "malformed_video_id"),
        ({"id": "one", "video_id": "two"}, "conflicting_video_id"),
    ],
)
def test_missing_malformed_and_conflicting_ids_are_rejected(record, code):
    with pytest.raises(DiscoveryValidationError) as caught:
        normalize_candidate(record)
    assert code in {issue.code for issue in caught.value.issues}


@pytest.mark.parametrize(
    "record,provider",
    [
        ({"video_id": "not-eleven"}, "youtube"),
        ({"videoId": "not-eleven"}, None),
        ({"id": {"videoId": "not-eleven"}}, None),
        ({"video_id": "not-eleven", "url": "https://youtube.com/watch?v=not-eleven"}, None),
    ],
)
def test_youtube_and_direct_artifacts_require_real_youtube_id_shape(record, provider):
    with pytest.raises(DiscoveryValidationError) as caught:
        normalize_candidate(record, provider=provider)
    assert "invalid_youtube_video_id" in {
        issue.code for issue in caught.value.issues
    }


def test_synthetic_ids_remain_compatible_when_not_explicitly_youtube():
    assert normalize_candidate({"video_id": "fixture-id"})["video_id"] == "fixture-id"
    filmot = normalize_candidate({"id": "fixture-id", "channelname": "Fixture"})
    assert filmot["video_id"] == "fixture-id"
    assert filmot["provider"] == "filmot"


def test_yt_search_artifact_strictness_cannot_be_bypassed_by_row_provider():
    artifact = {
        "videos": [{"video_id": "fixture-id", "provider": "filmot"}],
        "_filmot": {"schema": "filmot.result/v1", "command": "yt-search"},
    }
    result = preflight_candidates(artifact)
    assert not result.ok
    assert result.errors[0].code == "invalid_youtube_video_id"


def test_preflight_reports_every_bad_row_and_never_returns_partial_candidates():
    artifact = {
        "result": [
            {"id": "valid-fixture"},
            {"title": "missing"},
            {"video_id": "one", "id": "two"},
            "not an object",
            {"video_id": None},
        ]
    }
    original = deepcopy(artifact)

    result = preflight_candidates(artifact)

    assert not result.ok
    assert result.candidate_count == 5
    assert result.candidates == ()
    assert [issue.code for issue in result.errors] == [
        "missing_video_id",
        "conflicting_video_id",
        "candidate_type",
        "malformed_video_id",
    ]
    assert [issue.index for issue in result.errors] == [1, 2, 3, 4]
    assert artifact == original

    with pytest.raises(DiscoveryValidationError) as caught:
        normalize_candidates(artifact)
    assert caught.value.issues == result.errors


def test_normalization_does_not_mutate_input_or_reuse_nested_objects():
    source = {
        "video_id": "fixture-id",
        "custom": {"nested": [1, 2]},
        "provenance": {"query": "original"},
    }
    original = deepcopy(source)

    candidate = normalize_candidate(source)
    candidate["provider_fields"]["custom"]["nested"].append(3)
    candidate["provenance"]["query"] = "changed"

    assert source == original


def test_unknown_fields_are_bounded_and_credentials_are_never_copied():
    api_key = "DUMMY-SENTINEL-DISCOVERY-CREDENTIAL"
    source = {
        "video_id": "fixture-id",
        "description": "diagnostic https://api.example.test/?key={}".format(api_key),
        "url": "https://example.test/video?key={}&page=2".format(api_key),
        "title": "canonical",
        "viewcount": 7,
        "apiKey": api_key,
        "Authorization": "Bearer {}".format(api_key),
        "custom": {
            "access_token": api_key,
            "clientSecret": api_key,
            "sessionToken": api_key,
            "privateKey": api_key,
            "endpoint": "https://api.example.test/?api_key={}".format(api_key),
            "safe": "retained",
        },
        "provider_fields": {
            "title": "must not duplicate",
            "token": api_key,
            "native": True,
        },
        "provenance": {"request": "url?key={}".format(api_key)},
    }

    candidate = normalize_candidate(source)
    encoded = json.dumps(candidate, sort_keys=True)

    assert api_key not in encoded
    assert candidate["url"] == "https://example.test/video?page=2"
    assert candidate["description"].endswith("key=***")
    assert candidate["provider_fields"] == {
        "native": True,
        "custom": {
            "endpoint": "https://api.example.test/?api_key=***",
            "safe": "retained",
        },
    }
    assert "title" not in candidate["provider_fields"]
    assert "viewcount" not in candidate["provider_fields"]


def test_provider_field_limits_bound_width_depth_nodes_and_text():
    source = {
        "video_id": "fixture-id",
        "first": "abcdefghij",
        "second": ["one", "two", "three", "four"],
        "third": {"one": {"two": {"three": "too deep"}}},
        "fourth": "not copied due to width",
    }
    limits = ProviderFieldLimits(
        max_nodes=12,
        max_depth=2,
        max_items=3,
        max_string_length=4,
        max_key_length=20,
    )

    fields = normalize_candidate(source, limits=limits)["provider_fields"]

    assert list(fields) == ["first", "second", "third"]
    assert fields["first"] == "abcd"
    assert fields["second"] == ["one", "two", "thre"]
    assert "three" not in json.dumps(fields["third"])
    assert "fourth" not in fields


def test_rich_youtube_rows_reserve_budget_for_freshness_and_disclosures():
    thumbnails = {
        "size-{:02d}".format(index): {
            "url": "https://images.example.test/{}.jpg".format(index),
            "width": 1280,
            "height": 720,
        }
        for index in range(32)
    }
    source = {
        "video_id": "abc12345678",
        "title": "Rich provider row",
        "thumbnail": "https://images.example.test/default.jpg",
        "thumbnails": thumbnails,
        "tags": ["tag-{:02d}".format(index) for index in range(32)],
        "localized": {"title": "Localized", "description": "Description"},
        "content_rating": {
            "rating-{:02d}".format(index): "allowed"
            for index in range(32)
        },
        "region_restriction": {
            "allowed": ["US", "CA", "GB"],
            "blocked": ["DE", "FR"],
        },
        "upload_status": "processed",
        "failure_reason": None,
        "rejection_reason": None,
        "privacy_status": "public",
        "scheduled_publish_at": None,
        "license": "youtube",
        "embeddable": True,
        "public_stats_viewable": True,
        "made_for_kids": False,
        "self_declared_made_for_kids": False,
        "contains_synthetic_media": True,
        "paid_product_placement": False,
        "paid_product_placement_disclosure": "not_declared",
        "topic_ids": ["/m/science"],
        "relevant_topic_ids": ["/m/research"],
        "topic_categories": ["https://en.wikipedia.org/wiki/Science"],
        "metadata_observed_at": "2026-09-12T10:00:00Z",
        "metadata_expires_at": "2026-10-12T10:00:00Z",
        "observed_at": "2026-09-12T10:00:00Z",
        "expires_at": "2026-10-12T10:00:00Z",
        "metadata_status": "observed",
        "provider": "youtube-data-api-v3",
    }

    fields = normalize_candidate(source)["provider_fields"]

    assert fields["metadata_observed_at"] == "2026-09-12T10:00:00Z"
    assert fields["metadata_expires_at"] == "2026-10-12T10:00:00Z"
    assert fields["observed_at"] == "2026-09-12T10:00:00Z"
    assert fields["expires_at"] == "2026-10-12T10:00:00Z"
    assert fields["metadata_status"] == "observed"
    assert fields["paid_product_placement"] is False
    assert fields["paid_product_placement_disclosure"] == "not_declared"
    assert fields["made_for_kids"] is False
    assert fields["self_declared_made_for_kids"] is False
    assert fields["contains_synthetic_media"] is True
    assert fields["upload_status"] == "processed"
    assert fields["privacy_status"] == "public"
    assert fields["license"] == "youtube"
    assert fields["embeddable"] is True
    assert fields["public_stats_viewable"] is True
    assert fields["topic_ids"] == ["/m/science"]
    assert fields["relevant_topic_ids"] == ["/m/research"]
    assert fields["topic_categories"] == [
        "https://en.wikipedia.org/wiki/Science"
    ]
    # The hard global budget still truncates lower-priority bulk data.
    assert len(fields.get("thumbnails", {})) < len(thumbnails)


def test_record_and_caller_provenance_merge_with_caller_precedence():
    candidate = normalize_candidate(
        {
            "video_id": "fixture-id",
            "provenance": {"query": "record query", "stage": "discovery"},
        },
        provenance={"query": "explicit query", "run_id": "run-1"},
    )
    assert candidate["provenance"] == {
        "query": "explicit query",
        "stage": "discovery",
        "run_id": "run-1",
    }


def test_youtube_url_is_canonical_and_does_not_retain_query_credentials():
    candidate = normalize_candidate({
        "video_id": "abc12345678",
        "provider": "YouTube Data API v3",
        "url": "https://www.youtube.com/watch?v=abc12345678&key=secret&t=2",
    })
    assert candidate["provider"] == "youtube"
    assert candidate["url"] == "https://www.youtube.com/watch?v=abc12345678"
