"""Structural contracts for the modular Click surface."""

import ast
from pathlib import Path

from filmot.cli import cli


def test_top_level_command_topology_is_stable():
    assert set(cli.commands) == {
        "batch",
        "batch-template",
        "cache",
        "channel-download",
        "channel-search",
        "channel-status",
        "channels",
        "claims",
        "config",
        "download",
        "export",
        "interactive",
        "library",
        "proxy",
        "research",
        "search",
        "search-all",
        "sessions",
        "transcript",
        "transcript-search",
        "video",
        "watchlist",
        "yt-search",
        "yt-data",
        "yt-playlist",
        "yt-playlists",
        "yt-video",
    }
    assert set(cli.commands["library"].commands) == {
        "compare",
        "context",
        "delete",
        "echoes",
        "list",
        "migrate-topic",
        "search",
        "stats",
    }
    assert set(cli.commands["proxy"].commands) == {
        "refresh",
        "status",
        "test",
    }
    assert set(cli.commands["claims"].commands) == {
        "add",
        "assess",
        "cite",
        "show",
    }
    assert set(cli.commands["watchlist"].commands) == {
        "add",
        "clear",
        "list",
        "remove",
        "watched",
    }
    assert set(cli.commands["yt-data"].commands) == {
        "purge",
        "refresh",
        "status",
    }


def test_major_commands_are_owned_by_domain_modules():
    expected_modules = {
        "search": "filmot.commands.search",
        "search-all": "filmot.commands.search",
        "research": "filmot.commands.research",
        "transcript": "filmot.commands.transcript",
        "download": "filmot.commands.transcript",
        "claims": "filmot.commands.claims",
        "library": "filmot.commands.library",
        "sessions": "filmot.commands.library",
        "proxy": "filmot.commands.proxy",
        "yt-playlist": "filmot.commands.youtube",
        "yt-playlists": "filmot.commands.youtube",
    }
    assert {
        name: cli.commands[name].callback.__module__
        for name in expected_modules
    } == expected_modules


def test_core_modules_do_not_import_command_layer():
    package_dir = Path(__file__).resolve().parents[1] / "filmot"
    command_dir = package_dir / "commands"
    for path in package_dir.glob("*.py"):
        if path.name in {"cli.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
        assert not any(
            name == "commands"
            or name.startswith("commands.")
            or name.startswith("filmot.commands")
            for name in imported
        ), "{} must not depend on command modules".format(path)

    assert command_dir.is_dir()
