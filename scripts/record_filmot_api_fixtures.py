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
    if lowered in {"newshortname"}:
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
    if lowered in {"channelcountryname", "country"}:
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
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Optional dotenv file loaded before importing the API client",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tests/fixtures/filmot_api"),
    )
    parser.add_argument("--query", default='"SQLite WAL"')
    parser.add_argument("--channel-term", default="SQLite")
    parser.add_argument("--video-id", default="dQw4w9WgXcQ")
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
    from filmot.api import FilmotClient
    from filmot.api_contract import validate_api_response

    client = FilmotClient(use_cache=False)
    responses = {
        "search_subtitles_context": client.search_subtitles(
            args.query,
            lang="en",
            page=1,
            hit_format=0,
        ),
        "search_subtitles_lines": client.search_subtitles(
            args.query,
            lang="en",
            page=1,
            hit_format=1,
        ),
        "search_channels": client.search_channels(args.channel_term),
        "get_videos": client.get_videos(args.video_id),
    }
    endpoints = {
        "search_subtitles_context": "/getsearchsubtitles",
        "search_subtitles_lines": "/getsearchsubtitles",
        "search_channels": "/getsearchchannels",
        "get_videos": "/getvideos",
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, response in responses.items():
        endpoint = endpoints[name]
        validate_api_response(endpoint, response)
        sanitized = sanitize_response(compact_response(name, response))
        validate_api_response(endpoint, sanitized)
        destination = args.output_dir / "{}.json".format(name)
        if destination.exists() and not args.force:
            raise SystemExit(
                "{} already exists; pass --force to replace fixtures".format(
                    destination
                )
            )
        destination.write_text(
            json.dumps(sanitized, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
