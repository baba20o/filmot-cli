#!/usr/bin/env python3
"""Record small, sanitized Filmot API response-contract fixtures.

Only JSON response bodies are retained. Request headers and credentials are
never serialized. Human-readable upstream content and identifiers are replaced
with deterministic placeholders while field names, container shapes, and value
types remain representative.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dotenv import load_dotenv


_SENSITIVE_KEY_PARTS = (
    "api-key",
    "apikey",
    "authorization",
    "credential",
    "headers",
    "password",
    "rapidapi",
    "secret",
)
_SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "api_token",
    "refresh_token",
}


_FIXTURE_NAMES = (
    "search_subtitles_context",
    "search_subtitles_lines",
    "search_channels",
    "get_videos",
    "get_videos_columns",
    "get_videos_subtitles",
    "get_channels",
    "get_channels_all",
    "get_channel_videos",
)


def _safe_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        return "https://example.invalid/resource"
    return "https://example.invalid/resource"


def _sanitize_string(key: str, value: str, ordinal: int) -> str:
    lowered = key.casefold()
    if "url" in lowered or lowered.endswith("thumbnail"):
        return _safe_url(value)
    if lowered in {"id", "videoid"}:
        return "recorded-video-{:03d}".format(ordinal)
    if lowered in {"channelid", "channel_id", "value"}:
        return "recorded-channel-{:03d}".format(ordinal)
    if lowered in {"title"}:
        return "Recorded video title {:03d}".format(ordinal)
    if lowered in {"channelname", "label", "name"}:
        return "Recorded channel {:03d}".format(ordinal)
    if lowered in {"newshortname", "handle"}:
        return "@recorded-channel-{:03d}".format(ordinal)
    if lowered in {"token", "text", "ctx_before", "ctx_after"}:
        return {
            "token": "recorded phrase",
            "text": "recorded transcript line",
            "ctx_before": "context before ",
            "ctx_after": " context after",
        }[lowered]
    if lowered in {"uploaddate", "published_at"}:
        return "2026-01-01"
    if lowered == "lastfetchdate":
        # Keep the upstream layout (date-only today); replace only the instant.
        value = re.sub(r"\d{4}-\d{2}-\d{2}", "2026-01-02", value)
        return re.sub(r"\d{2}:\d{2}:\d{2}", "03:04:05", value)
    if lowered == "description":
        return "Recorded video description"
    if lowered == "biography":
        return "Recorded channel biography"
    if lowered == "username":
        return "recorded-username-{:03d}".format(ordinal)
    if lowered == "val":
        # A previous channel profile value (channelchanges).
        return "Recorded previous value {:03d}".format(ordinal)
    if lowered == "t":
        # Indexed caption text (subtitles column).
        return "recorded caption line"
    if lowered in {"channelcountryname", "countryname", "country"}:
        return "Recorded country"
    if re.search(r"(?i)https?://[^/\s@]+@", value):
        return "https://example.invalid/resource"
    return value


def sanitize_response(value: Any, *, ordinal: int = 1) -> Any:
    """Recursively sanitize a response body without changing its structure."""
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            lowered = str(key).casefold()
            if (
                lowered in _SENSITIVE_KEYS
                or any(part in lowered for part in _SENSITIVE_KEY_PARTS)
            ):
                continue
            if isinstance(item, str):
                sanitized[key] = _sanitize_string(str(key), item, ordinal)
            else:
                sanitized[key] = sanitize_response(item, ordinal=ordinal)
        return sanitized
    if isinstance(value, list):
        return [
            sanitize_response(item, ordinal=index)
            for index, item in enumerate(value, 1)
        ]
    return value


def compact_response(name: str, payload: Any) -> Any:
    """Keep fixtures reviewable while retaining every nested contract shape."""
    if name.startswith("search_subtitles") and isinstance(payload, dict):
        payload = dict(payload)
        payload["result"] = [
            dict(video, hits=list(video.get("hits", []))[:2])
            for video in list(payload.get("result", []))[:2]
        ]
    elif isinstance(payload, list):
        payload = payload[:3]
        if name.startswith("get_channels"):
            payload = [_compact_channel(channel) for channel in payload]
        if name == "get_videos_subtitles":
            payload = [
                dict(video, subtitles=[
                    dict(track, transcript=list(track.get("transcript") or [])[:3])
                    for track in video["subtitles"][:2]
                ])
                if isinstance(video, dict) and isinstance(video.get("subtitles"), list)
                else video
                for video in payload
            ]
    return payload


def _compact_channel(channel: Any) -> Any:
    """Shorten a channel record's long series while keeping every group."""
    if not isinstance(channel, dict):
        return channel
    channel = dict(channel)
    for key in ("subscribergrowth", "wordcloud"):
        if isinstance(channel.get(key), list):
            channel[key] = channel[key][:3]
    if isinstance(channel.get("channelchanges"), dict):
        channel["channelchanges"] = {
            field: values[:2] if isinstance(values, list) else values
            for field, values in channel["channelchanges"].items()
        }
    stats = channel.get("videostats")
    if isinstance(stats, dict):
        stats = dict(stats)
        for key in ("months", "languages"):
            if isinstance(stats.get(key), list):
                stats[key] = stats[key][:2]
        channel["videostats"] = stats
    return channel


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help=(
            "Optional dotenv file loaded before importing the API client. It is "
            "trusted like the process environment, so it may set endpoint overrides"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tests/fixtures/filmot_api"),
    )
    parser.add_argument("--query", default='"SQLite WAL"')
    parser.add_argument("--channel-term", default="SQLite")
    parser.add_argument("--video-id", default="dQw4w9WgXcQ")
    parser.add_argument("--channel-id", default="UCHnyfMqiRRG1u-2MsSQLbXA")
    parser.add_argument(
        "--stats-start",
        default="2026-04-01",
        help="startDate for the columns=all channel fixture; keeps it small",
    )
    parser.add_argument(
        "--provider",
        choices=("auto", "direct", "rapidapi"),
        default="auto",
        help="Filmot API provider policy used for recording",
    )
    parser.add_argument(
        "--only",
        action="append",
        choices=_FIXTURE_NAMES,
        help="Record only this fixture (repeatable)",
    )
    parser.add_argument(
        "--suffix",
        default="",
        help="Append to fixture file names, e.g. _direct for provider shapes",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing fixture files",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.env_file is not None:
        load_dotenv(dotenv_path=args.env_file, override=False)

    # Import only after dotenv loading: filmot.config reads credentials at
    # module import time and applies the normal explicit user/CWD files when
    # no recorder-specific file was supplied.
    from filmot import config
    from filmot.api import VIDEO_METADATA_COLUMNS, FilmotClient
    from filmot.api_contract import validate_api_response

    config.API_PROVIDER = args.provider
    configured_secrets = (config.DIRECT_API_KEY, config.RAPIDAPI_KEY)
    client = FilmotClient(use_cache=False)
    requests_by_name = {
        "search_subtitles_context": lambda: client.search_subtitles(
            args.query,
            lang="en",
            page=1,
            hit_format=0,
        ),
        "search_subtitles_lines": lambda: client.search_subtitles(
            args.query,
            lang="en",
            page=1,
            hit_format=1,
        ),
        # Record the provider's raw body; search_channels() adds canonical fields.
        "search_channels": lambda: client.get(
            "/getsearchchannels", params={"term": args.channel_term},
        ),
        "get_videos": lambda: client.get_videos(args.video_id),
        "get_videos_columns": lambda: client.get_videos(
            args.video_id, columns=VIDEO_METADATA_COLUMNS,
        ),
        "get_videos_subtitles": lambda: client.get_videos(
            args.video_id, columns=("subtitles", "lastfetchdate"), lang="en",
        ),
        "get_channels": lambda: client.get_channels(args.channel_id),
        "get_channels_all": lambda: client.get_channels(
            args.channel_id, columns="all", start_date=args.stats_start,
        ),
        "get_channel_videos": lambda: client.get_channel_videos(
            args.channel_id, count_per_channel=3, columns=VIDEO_METADATA_COLUMNS,
        ),
    }
    endpoints = {
        "search_subtitles_context": "/getsearchsubtitles",
        "search_subtitles_lines": "/getsearchsubtitles",
        "search_channels": "/getsearchchannels",
        "get_videos": "/getvideos",
        "get_videos_columns": "/getvideos",
        "get_videos_subtitles": "/getvideos",
        "get_channels": "/getchannels",
        "get_channels_all": "/getchannels",
        "get_channel_videos": "/getchannelvideos",
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name in args.only or _FIXTURE_NAMES:
        response = requests_by_name[name]()
        if isinstance(response, dict) and "error" in response:
            raise SystemExit("{}: {}".format(name, response["error"]))
        endpoint = endpoints[name]
        validate_api_response(endpoint, response)
        sanitized = sanitize_response(compact_response(name, response))
        validate_api_response(endpoint, sanitized)
        rendered = json.dumps(sanitized, ensure_ascii=False)
        if any(secret and secret in rendered for secret in configured_secrets):
            raise SystemExit(
                "{}: refusing to write a fixture containing a configured key".format(name)
            )
        destination = args.output_dir / "{}{}.json".format(name, args.suffix)
        if destination.exists() and not args.force:
            raise SystemExit(
                "{} already exists; pass --force to replace fixtures".format(
                    destination
                )
            )
        # Bytes keep LF line endings on Windows too (.gitattributes eol=lf).
        destination.write_bytes(
            (json.dumps(sanitized, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        )
        print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
