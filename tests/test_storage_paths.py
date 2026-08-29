"""Storage-boundary and cross-process proxy persistence tests."""

import ast
import json
import os
import sqlite3
import stat
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from click.testing import CliRunner

from filmot.cache import Cache
from filmot.channel_dl import ChannelDownloader
from filmot.ledger import log_event, read_events
from filmot.library import TranscriptLibrary
from filmot.paths import (
    default_proxy_session_file,
    ensure_private_dir,
    project_data_dir,
    proxy_health_db,
    restrict_private_file,
    user_cache_dir,
    user_config_dir,
    user_state_dir,
)
from filmot.proxy_pool import (
    WebshareProxyPool,
    WebshareSession,
    get_pool,
    reset_pool,
)
from filmot.watchlist import Watchlist
from filmot.cli import cli


def _assert_owner_only_when_supported(path: Path) -> None:
    """Assert 0600 only when the backing filesystem exposes POSIX modes."""
    if os.name == "nt":
        return
    probe = path.parent / ".filmot-permission-probe"
    probe.write_text("", encoding="utf-8")
    try:
        restrict_private_file(probe)
        if stat.S_IMODE(probe.stat().st_mode) == 0o600:
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
    finally:
        probe.unlink()


def _assert_private_dir_when_supported(path: Path) -> None:
    """Assert 0700 only when the backing filesystem exposes POSIX modes."""
    if os.name == "nt":
        return
    probe = path.parent / ".filmot-private-dir-probe"
    ensure_private_dir(probe)
    try:
        if stat.S_IMODE(probe.stat().st_mode) == 0o700:
            assert stat.S_IMODE(path.stat().st_mode) == 0o700
    finally:
        probe.rmdir()


def _assert_sqlite_family_omits(path: Path, *secrets: str) -> None:
    """Check the database and any live journal sidecars for raw secrets."""
    files = list(path.parent.glob("{}*".format(path.name)))
    assert files
    for candidate in files:
        content = candidate.read_bytes()
        for secret in secrets:
            assert secret.encode("utf-8") not in content


def test_project_and_machine_roots_have_distinct_dynamic_scopes(
    monkeypatch, tmp_path
):
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()

    monkeypatch.chdir(project_a)
    assert project_data_dir() == project_a / ".filmot_data"
    config_a = user_config_dir()
    state_a = user_state_dir()
    cache_a = user_cache_dir()

    monkeypatch.chdir(project_b)
    assert project_data_dir() == project_b / ".filmot_data"
    assert user_config_dir() == config_a
    assert user_state_dir() == state_a
    assert user_cache_dir() == cache_a

    explicit = tmp_path / "shared-project-data"
    monkeypatch.setenv("FILMOT_DATA_DIR", str(explicit))
    assert project_data_dir() == explicit


def test_read_only_research_inspection_does_not_create_project_state(
    monkeypatch, tmp_path
):
    project = tmp_path / "empty-project"
    project.mkdir()
    monkeypatch.chdir(project)
    runner = CliRunner()

    invocations = [
        ["library", "list", "--raw"],
        ["library", "search", "alpha", "--raw"],
        ["library", "compare", "alpha", "--raw"],
        ["library", "echoes", "empty", "--raw"],
        ["sessions", "--raw"],
        ["claims", "show", "empty", "--raw"],
    ]
    for arguments in invocations:
        result = runner.invoke(cli, arguments)
        assert result.exit_code == 0, result.output
        json.loads(result.stdout)

    assert not (project / ".filmot_data").exists()


def test_configuration_precedence_and_no_parent_dotenv_search(
    monkeypatch, tmp_path
):
    parent = tmp_path / "parent"
    project = parent / "project"
    project.mkdir(parents=True)
    (parent / ".env").write_text(
        "PARENT_ONLY=must-not-load\n",
        encoding="utf-8",
    )
    (project / ".env").write_text(
        "RAPIDAPI_KEY=project-value\nPROJECT_ONLY=loaded\n",
        encoding="utf-8",
    )
    user_config = tmp_path / "config.env"
    user_config.write_text(
        "RAPIDAPI_KEY=user-value\nUSER_ONLY=loaded\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    for name in ("PARENT_ONLY", "PROJECT_ONLY", "USER_ONLY"):
        environment.pop(name, None)
    environment.update(
        {
            "FILMOT_CONFIG_FILE": str(user_config),
            "RAPIDAPI_KEY": "process-value",
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, os; import filmot.config as config; "
                "print(json.dumps({"
                "'key': config.API_KEY, "
                "'user': os.getenv('USER_ONLY'), "
                "'project': os.getenv('PROJECT_ONLY'), "
                "'parent': os.getenv('PARENT_ONLY')"
                "}))"
            ),
        ],
        cwd=project,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "key": "process-value",
        "user": "loaded",
        "project": "loaded",
        "parent": None,
    }


def test_environment_loading_is_centralized_and_explicit():
    package = Path(__file__).resolve().parents[1] / "filmot"
    calls = []
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if isinstance(function, ast.Name) and function.id == "load_dotenv":
                calls.append((path.name, node))

    assert calls
    assert {name for name, _ in calls} == {"config.py"}
    assert all(
        any(keyword.arg == "dotenv_path" for keyword in node.keywords)
        for _, node in calls
    )


def test_research_storage_honors_one_project_data_override(
    monkeypatch, tmp_path
):
    root = tmp_path / "research"
    monkeypatch.setenv("FILMOT_DATA_DIR", str(root))

    library = TranscriptLibrary()
    channel = ChannelDownloader()
    watchlist = Watchlist()
    log_event("search", query="storage boundary")

    assert library.data_dir == root
    assert channel.data_dir == root
    assert watchlist.storage_dir == root
    assert read_events("storage boundary") == []
    assert (root / "sessions").is_dir()


def test_cache_is_per_user_and_not_created_in_project(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    cache = Cache()

    assert cache.cache_dir == user_cache_dir()
    assert cache.cache_dir.is_dir()
    assert not (project / ".filmot_cache").exists()


def _file_pool(
    session_file: Path,
    state_path: Path,
    *,
    sessions=None,
    lease_seconds=120.0,
) -> WebshareProxyPool:
    pool = WebshareProxyPool.__new__(WebshareProxyPool)
    pool._init_file_backed(
        sessions
        or [
            WebshareSession(id="session-a", username="user-a", password="pass-a"),
            WebshareSession(id="session-b", username="user-b", password="pass-b"),
        ],
        session_file_path=session_file,
        state_path=state_path,
        lease_seconds=lease_seconds,
    )
    return pool


def test_machine_global_sqlite_coordinates_leases_across_pool_instances(tmp_path):
    session_file = tmp_path / "sessions.txt"
    session_file.write_text("placeholder", encoding="utf-8")
    state_path = tmp_path / "health.sqlite3"
    first_pool = _file_pool(session_file, state_path)
    second_pool = _file_pool(session_file, state_path)

    first = first_pool.pick(lease=True)
    second = second_pool.pick(lease=True)

    assert first is not None
    assert second is not None
    assert first.id != second.id
    assert first_pool.available_count() == 0
    assert second_pool.available_count() == 0

    first_pool.report_success(first)
    second_pool.report_failure(second, "connection", summary="timed out")

    reloaded = _file_pool(session_file, state_path)
    by_id = {session.id: session for session in reloaded._sessions}
    assert by_id[first.id].success == 1
    assert by_id[second.id].fail_other == 1


def test_per_attempt_lease_override_is_shared_across_pool_instances(tmp_path):
    session_file = tmp_path / "sessions.txt"
    session_file.write_text("placeholder", encoding="utf-8")
    state_path = tmp_path / "health.sqlite3"
    sessions = [
        WebshareSession(
            id="long-attempt",
            username="secret-user",
            password="secret-password",
        )
    ]
    first_pool = _file_pool(
        session_file,
        state_path,
        sessions=sessions,
        lease_seconds=2,
    )
    second_pool = _file_pool(
        session_file,
        state_path,
        sessions=[
            WebshareSession(
                id="long-attempt",
                username="secret-user",
                password="secret-password",
            )
        ],
        lease_seconds=2,
    )
    started = time.time()

    selected = first_pool.pick(lease=True, lease_seconds=330)

    assert selected is not None
    assert second_pool.pick() is None
    with sqlite3.connect(state_path) as connection:
        lease_until = connection.execute(
            "SELECT lease_until FROM session_health"
        ).fetchone()[0]
    assert lease_until >= started + 329

    first_pool.release(selected)
    assert second_pool.pick() is not None


def test_concurrent_health_updates_are_not_lost(tmp_path):
    session_file = tmp_path / "sessions.txt"
    session_file.write_text("placeholder", encoding="utf-8")
    state_path = tmp_path / "health.sqlite3"
    first_pool = _file_pool(
        session_file,
        state_path,
        sessions=[
            WebshareSession(
                id="shared-session",
                username="secret-user",
                password="secret-password",
            )
        ],
    )
    second_pool = _file_pool(
        session_file,
        state_path,
        sessions=[
            WebshareSession(
                id="shared-session",
                username="secret-user",
                password="secret-password",
            )
        ],
    )

    updates = [
        (first_pool, first_pool._sessions[0]),
        (second_pool, second_pool._sessions[0]),
    ] * 10
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda pair: pair[0].report_success(pair[1]), updates))

    reloaded = _file_pool(
        session_file,
        state_path,
        sessions=[
            WebshareSession(
                id="shared-session",
                username="secret-user",
                password="secret-password",
            )
        ],
    )
    assert reloaded._sessions[0].success == len(updates)
    _assert_sqlite_family_omits(
        state_path,
        "shared-session",
        "secret-user",
        "secret-password",
    )


def test_sqlite_lease_survives_process_boundary(tmp_path):
    session_file = tmp_path / "sessions.txt"
    session_file.write_text("placeholder", encoding="utf-8")
    state_path = tmp_path / "health.sqlite3"
    worker = (
        "import sys; from pathlib import Path; "
        "from filmot.proxy_pool import WebshareProxyPool, WebshareSession; "
        "pool = WebshareProxyPool.__new__(WebshareProxyPool); "
        "pool._init_file_backed(["
        "WebshareSession(id='a', username='ua', password='pa'), "
        "WebshareSession(id='b', username='ub', password='pb')"
        "], session_file_path=Path(sys.argv[1]), "
        "state_path=Path(sys.argv[2]), lease_seconds=120); "
        "print(pool.pick(lease=True).id)"
    )
    command = [
        sys.executable,
        "-c",
        worker,
        str(session_file),
        str(state_path),
    ]
    environment = os.environ.copy()
    environment.pop("WEBSHARE_API_TOKEN", None)
    environment.pop("WEBSHARE_SESSION_FILE", None)

    first = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    second = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert {first, second} == {"a", "b"}
    _assert_sqlite_family_omits(state_path, "ua", "pa", "ub", "pb")


def test_legacy_file_pool_is_copied_and_health_imported(
    monkeypatch, tmp_path
):
    project = tmp_path / "project"
    legacy = project / ".filmot_data"
    legacy.mkdir(parents=True)
    monkeypatch.chdir(project)
    monkeypatch.delenv("WEBSHARE_API_TOKEN", raising=False)
    monkeypatch.delenv("WEBSHARE_SESSION_FILE", raising=False)

    legacy_inventory = legacy / "webshare_info.txt"
    legacy_inventory.write_text(
        "proxy.test:80:legacy-user:legacy-password\n",
        encoding="utf-8",
    )
    legacy_state = legacy / "webshare_pool_file.json"
    legacy_state.write_text(
        json.dumps(
            {
                "last_refresh": 10,
                "cursor": 0,
                "sessions": [
                    {
                        "id": "legacy-user",
                        "username": "legacy-user",
                        "password": "legacy-password",
                        "success": 4,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    reset_pool()
    pool = get_pool()

    assert pool is not None
    assert pool.session_file_path == default_proxy_session_file()
    assert default_proxy_session_file().read_text(encoding="utf-8") == (
        legacy_inventory.read_text(encoding="utf-8")
    )
    assert pool.status_snapshot()["sessions"][0]["success"] == 4
    assert legacy_inventory.exists()
    assert legacy_state.exists()
    _assert_sqlite_family_omits(
        proxy_health_db(),
        "legacy-user",
        "legacy-password",
    )
    _assert_owner_only_when_supported(default_proxy_session_file())


def test_existing_default_session_inventory_permissions_are_tightened(
    monkeypatch, tmp_path
):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.delenv("WEBSHARE_API_TOKEN", raising=False)
    monkeypatch.delenv("WEBSHARE_SESSION_FILE", raising=False)

    inventory = default_proxy_session_file()
    inventory.parent.mkdir(parents=True)
    inventory.write_text(
        "proxy.test:80:private-user:private-password\n",
        encoding="utf-8",
    )
    if os.name != "nt":
        inventory.parent.chmod(0o755)
        inventory.chmod(0o644)

    reset_pool()
    pool = get_pool()

    assert pool is not None
    assert pool.session_file_path == inventory
    _assert_owner_only_when_supported(inventory)
    _assert_private_dir_when_supported(inventory.parent)


def test_corrupt_legacy_file_pool_metadata_is_non_fatal(
    monkeypatch, tmp_path
):
    project = tmp_path / "project"
    legacy = project / ".filmot_data"
    legacy.mkdir(parents=True)
    monkeypatch.chdir(project)
    monkeypatch.delenv("WEBSHARE_API_TOKEN", raising=False)
    monkeypatch.delenv("WEBSHARE_SESSION_FILE", raising=False)
    default_proxy_session_file().parent.mkdir(parents=True)
    default_proxy_session_file().write_text(
        "proxy.test:80:legacy-user:legacy-password\n",
        encoding="utf-8",
    )
    (legacy / "webshare_pool_file.json").write_text(
        json.dumps(
            {
                "last_refresh": "not-a-timestamp",
                "cursor": {"not": "an integer"},
                "sessions": [
                    {
                        "id": "legacy-user",
                        "username": "legacy-user",
                        "password": "legacy-password",
                        "success": 3,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    reset_pool()
    pool = get_pool()

    assert pool is not None
    assert pool.status_snapshot()["sessions"][0]["success"] == 3


def test_legacy_api_snapshot_splits_credentials_from_health(
    monkeypatch, tmp_path
):
    project = tmp_path / "project"
    legacy = project / ".filmot_data"
    legacy.mkdir(parents=True)
    monkeypatch.chdir(project)
    monkeypatch.setenv("WEBSHARE_API_TOKEN", "current-token")

    (legacy / "webshare_pool.json").write_text(
        json.dumps(
            {
                "last_refresh": 20,
                "cursor": 0,
                "sessions": [
                    {
                        "id": "api-session-id",
                        "username": "api-secret-user",
                        "password": "api-secret-password",
                        "country_code": "US",
                        "success": 2,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    reset_pool()
    pool = get_pool()

    assert pool is not None
    assert pool.source == "webshare-api"
    assert pool._sessions[0].success == 2
    assert pool.inventory_path.exists()
    assert "api-secret-user" in pool.inventory_path.read_text(encoding="utf-8")
    _assert_sqlite_family_omits(
        proxy_health_db(),
        "api-session-id",
        "api-secret-user",
        "api-secret-password",
    )
    _assert_owner_only_when_supported(pool.inventory_path)


def test_existing_api_inventory_permissions_are_tightened(
    monkeypatch, tmp_path
):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    inventory = user_config_dir() / "existing-api-inventory.json"
    inventory.parent.mkdir(parents=True)
    inventory.write_text(
        json.dumps(
            {
                "last_refresh": 20,
                "sessions": [
                    {
                        "id": "api-session-id",
                        "username": "api-secret-user",
                        "password": "api-secret-password",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    if os.name != "nt":
        inventory.parent.chmod(0o755)
        inventory.chmod(0o644)

    pool = WebshareProxyPool(
        "current-token",
        inventory_path=inventory,
        state_path=tmp_path / "api-health.sqlite3",
    )

    assert [session.id for session in pool._sessions] == ["api-session-id"]
    _assert_owner_only_when_supported(inventory)
    _assert_private_dir_when_supported(inventory.parent)


def test_corrupt_legacy_api_metadata_is_non_fatal(
    monkeypatch, tmp_path
):
    project = tmp_path / "project"
    legacy = project / ".filmot_data"
    legacy.mkdir(parents=True)
    monkeypatch.chdir(project)
    monkeypatch.setenv("WEBSHARE_API_TOKEN", "current-token")
    (legacy / "webshare_pool.json").write_text(
        json.dumps(
            {
                "last_refresh": float("nan"),
                "cursor": "not-an-integer",
                "sessions": [
                    {
                        "id": "api-session-id",
                        "username": "api-secret-user",
                        "password": "api-secret-password",
                        "success": 2,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    reset_pool()
    pool = get_pool()

    assert pool is not None
    assert pool.source == "webshare-api"
    assert pool._last_refresh == 0.0
    assert pool.status_snapshot()["sessions"][0]["success"] == 2


def test_custom_session_inventory_is_honored_but_health_remains_global(
    monkeypatch, tmp_path
):
    project = tmp_path / "project"
    project.mkdir()
    custom = project / "custom-sessions.txt"
    custom.write_text(
        "proxy.test:80:custom-user:custom-password\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    monkeypatch.delenv("WEBSHARE_API_TOKEN", raising=False)
    monkeypatch.setenv("WEBSHARE_SESSION_FILE", "custom-sessions.txt")

    reset_pool()
    pool = get_pool()

    assert pool is not None
    assert pool.session_file_path == custom
    assert pool.state_path == proxy_health_db()
    assert not (project / ".filmot_data").exists()


def test_existing_global_inventory_is_never_overwritten(
    monkeypatch, tmp_path
):
    project = tmp_path / "project"
    legacy = project / ".filmot_data"
    legacy.mkdir(parents=True)
    monkeypatch.chdir(project)
    monkeypatch.delenv("WEBSHARE_API_TOKEN", raising=False)
    monkeypatch.delenv("WEBSHARE_SESSION_FILE", raising=False)
    (legacy / "webshare_info.txt").write_text(
        "legacy.test:80:legacy-user:legacy-password\n",
        encoding="utf-8",
    )
    target = default_proxy_session_file()
    target.parent.mkdir(parents=True)
    target.write_text(
        "current.test:80:current-user:current-password\n",
        encoding="utf-8",
    )

    reset_pool()
    pool = get_pool()

    assert pool is not None
    assert pool.session_file_path == target
    assert [session.id for session in pool._sessions] == ["current-user"]
    assert "legacy-user" not in target.read_text(encoding="utf-8")
