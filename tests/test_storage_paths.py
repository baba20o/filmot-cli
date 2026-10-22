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
        "RAPIDAPI_KEY=project-value\nFILMOT_API_KEY=project-direct\n"
        "PROJECT_ONLY=loaded\n",
        encoding="utf-8",
    )
    user_config = tmp_path / "config.env"
    user_config.write_text(
        "RAPIDAPI_KEY=user-value\nFILMOT_API_KEY=user-direct\nUSER_ONLY=loaded\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    # The test process may have loaded a developer's real keys from .env;
    # never pass them to the child or let them decide precedence.
    for name in (
        "PARENT_ONLY", "PROJECT_ONLY", "USER_ONLY",
        "FILMOT_API_KEY", "FILMOT_API_BASE_URL", "FILMOT_API_PROVIDER",
    ):
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
                "'direct': config.DIRECT_API_KEY, "
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
        "direct": "user-direct",
        "user": "loaded",
        "project": "loaded",
        "parent": None,
    }


_CONFIG_PROBE = '''
import importlib, json, os, subprocess, sys
import filmot.config as config

NAMES = ("FILMOT_API_BASE_URL", "RAPIDAPI_HOST", "FILMOT_CONFIG_FILE",
         "REQUESTS_CA_BUNDLE", "HTTPS_PROXY")


def report():
    return {
        "direct": config.DIRECT_BASE_URL,
        "host": config.API_HOST,
        "ignored": list(config.IGNORED_PROJECT_SETTINGS),
        "env": {name: os.environ.get(name) for name in NAMES},
        "odd_names": sorted(name for name in os.environ if ":" in name or "," in name),
    }


first = report()
importlib.reload(config)
reloaded = report()
child = subprocess.run(
    [sys.executable, "-c",
     "import json, filmot.config as c; print(json.dumps([c.DIRECT_BASE_URL, c.API_HOST]))"],
    capture_output=True, text=True, check=True,
)
print(json.dumps({"first": first, "reloaded": reloaded, "child": json.loads(child.stdout)}))
'''

DEFAULT_ENDPOINTS = ["https://apiv1.filmot.com/api/v1", "filmot-tube-metadata-archive.p.rapidapi.com"]


def _probe_config(tmp_path, project_env, user_env="", process_env=None, user_file=True):
    """Import, reload, and inherit filmot.config; report endpoints at each step.

    ``user_file=True`` locates the trusted per-user config with FILMOT_CONFIG_FILE,
    ``False`` with FILMOT_CONFIG_DIR only (a default install), and a string is
    used verbatim as FILMOT_CONFIG_FILE.
    """
    project = tmp_path / "project"
    user_dir = tmp_path / "user-config"
    project.mkdir()
    user_dir.mkdir()
    (project / ".env").write_text(project_env, encoding="utf-8")
    user_config = user_dir / "config.env"
    user_config.write_text("FILMOT_API_KEY=user-direct\n" + user_env, encoding="utf-8")
    probe = tmp_path / "probe.py"
    probe.write_text(_CONFIG_PROBE, encoding="utf-8")
    environment = os.environ.copy()
    for name in (
        "FILMOT_API_KEY", "FILMOT_API_BASE_URL", "RAPIDAPI_HOST", "RAPIDAPI_KEY",
        "FILMOT_CONFIG_FILE", "FILMOT_CONFIG_DIR", "REQUESTS_CA_BUNDLE", "HTTPS_PROXY",
    ):
        environment.pop(name, None)
    # Start like a fresh shell: no pin inherited from this test process.
    for name in [name for name in environment if name.upper() == "FILMOT_TRUSTED_CONFIG"]:
        environment.pop(name)
    if isinstance(user_file, str):
        environment["FILMOT_CONFIG_FILE"] = user_file
    elif user_file:
        environment["FILMOT_CONFIG_FILE"] = str(user_config)
    else:
        environment["FILMOT_CONFIG_DIR"] = str(user_dir)
    environment.update(process_env or {})
    result = subprocess.run(
        [sys.executable, str(probe)],
        cwd=project, env=environment, check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def _endpoints(report):
    return [report["direct"], report["host"]]


def test_a_project_env_cannot_redirect_api_endpoints(tmp_path):
    reported = _probe_config(
        tmp_path,
        "FILMOT_API_BASE_URL=https://evil.invalid/api/v1\nRAPIDAPI_HOST=evil.invalid\n",
    )

    for step in ("first", "reloaded"):
        assert _endpoints(reported[step]) == DEFAULT_ENDPOINTS
        assert sorted(reported[step]["ignored"]) == ["FILMOT_API_BASE_URL", "RAPIDAPI_HOST"]
        assert reported[step]["env"]["FILMOT_API_BASE_URL"] is None
    assert reported["child"] == DEFAULT_ENDPOINTS


def test_a_project_env_cannot_choose_the_trusted_config_file(tmp_path):
    # Without a trusted FILMOT_CONFIG_FILE, a project could name itself as the
    # per-user config for a reload or a child process to trust.
    reported = _probe_config(
        tmp_path,
        "FILMOT_CONFIG_FILE=.env\nFILMOT_API_BASE_URL=https://evil.invalid/api/v1\n",
        user_file=False,
    )

    for step in ("first", "reloaded"):
        assert _endpoints(reported[step]) == DEFAULT_ENDPOINTS
        assert sorted(reported[step]["ignored"]) == ["FILMOT_API_BASE_URL", "FILMOT_CONFIG_FILE"]
        assert reported[step]["env"]["FILMOT_CONFIG_FILE"] is None
    assert reported["child"] == DEFAULT_ENDPOINTS


def test_a_project_variable_cannot_complete_a_trusted_config_path(tmp_path):
    # The trusted path refers to a variable only the project .env defines. A
    # reload or child must resolve it exactly as the first import did.
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    (attacker / "config.env").write_text(
        "FILMOT_API_BASE_URL=https://evil.invalid/api/v1\n", encoding="utf-8",
    )
    reported = _probe_config(
        tmp_path,
        "CONFIG_ANCHOR={}\n".format(attacker),
        user_file="${CONFIG_ANCHOR}/config.env",
    )

    assert _endpoints(reported["first"]) == DEFAULT_ENDPOINTS
    assert _endpoints(reported["reloaded"]) == DEFAULT_ENDPOINTS
    assert reported["child"] == DEFAULT_ENDPOINTS


def test_names_that_are_not_identifiers_cannot_steer_config_loading(tmp_path):
    # python-dotenv accepts ':' and ',' in names. Such a name must neither be
    # exported nor change how a trusted ${CONFIG_ANCHOR} path resolves later,
    # even with a project directory literally named "${CONFIG_ANCHOR}".
    safe = tmp_path / "safe"
    safe.mkdir()
    # The trusted config leaves the endpoint unset, so a later load could fill it.
    (safe / "config.env").write_text("FILMOT_API_KEY=user-direct\n", encoding="utf-8")
    decoy = tmp_path / "project" / "${CONFIG_ANCHOR}"
    decoy.mkdir(parents=True)
    (decoy / "config.env").write_text(
        "FILMOT_API_BASE_URL=https://evil.invalid/api/v1\n", encoding="utf-8",
    )
    (tmp_path / "project" / ".env").write_text(
        "JUNK:ignored,CONFIG_ANCHOR={}\nCONFIG:ANCHOR=x\n".format(safe), encoding="utf-8",
    )
    probe = tmp_path / "probe.py"
    probe.write_text(_CONFIG_PROBE, encoding="utf-8")
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() not in {
            "FILMOT_API_KEY", "FILMOT_API_BASE_URL", "RAPIDAPI_HOST", "FILMOT_CONFIG_FILE",
            "FILMOT_CONFIG_DIR", "FILMOT_TRUSTED_CONFIG", "CONFIG_ANCHOR",
        }
    }
    environment.update({
        "FILMOT_CONFIG_FILE": "${CONFIG_ANCHOR}/config.env",
        "CONFIG_ANCHOR": str(safe),
    })
    result = subprocess.run(
        [sys.executable, str(probe)],
        cwd=tmp_path / "project", env=environment, check=True, capture_output=True, text=True,
    )
    reported = json.loads(result.stdout)

    assert _endpoints(reported["first"]) == DEFAULT_ENDPOINTS
    assert _endpoints(reported["reloaded"]) == DEFAULT_ENDPOINTS
    assert reported["child"] == DEFAULT_ENDPOINTS
    assert reported["first"]["odd_names"] == []


_PARENT_PROBE = '''
import json, os, subprocess, sys
import filmot.config as config  # writes the real pin for this environment

child_environment = dict(os.environ)
child_environment.update(json.loads(sys.argv[1]))
child = subprocess.run(
    [sys.executable, "-c",
     "import json, os, filmot.config as c; "
     "print(json.dumps([os.environ.get('YOUTUBE_API_KEY', ''), c.DIRECT_BASE_URL, "
     "str(c.USER_CONFIG_FILE)]))"],
    env=child_environment, capture_output=True, text=True, check=True,
)
print(json.dumps({"ignored": list(config.IGNORED_PROJECT_SETTINGS),
                  "parent_config": str(config.USER_CONFIG_FILE),
                  "child": json.loads(child.stdout)}))
'''

_LOCATION_NAMES = {
    "FILMOT_API_KEY", "YOUTUBE_API_KEY", "FILMOT_API_BASE_URL", "FILMOT_CONFIG_FILE",
    "FILMOT_CONFIG_DIR", "FILMOT_TRUSTED_CONFIG", "CONFIG_ANCHOR", "PREFIX",
    "XDG_CONFIG_HOME",
}


def _parent_and_child(tmp_path, trusted, child_change, project_env=""):
    """Import config in a parent with ``trusted`` settings, then in a child."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    (project / ".env").write_text(project_env, encoding="utf-8")
    probe = tmp_path / "parent_probe.py"
    probe.write_text(_PARENT_PROBE, encoding="utf-8")
    environment = {
        key: value for key, value in os.environ.items() if key.upper() not in _LOCATION_NAMES
    }
    environment.update(trusted)
    result = subprocess.run(
        [sys.executable, str(probe), json.dumps(child_change)],
        cwd=project, env=environment, check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def _config_dir(root, name, content):
    (root / name).mkdir(parents=True, exist_ok=True)
    (root / name / "config.env").write_text(content, encoding="utf-8")


@pytest.mark.parametrize("parent_change,expected", [
    ({}, ""),
    # Following this change would mean expanding ${CONFIG_ANCHOR} again in an
    # environment that may hold project values, so the child keeps the pin.
    ({"CONFIG_ANCHOR": "second"}, ""),
    ({"FILMOT_CONFIG_FILE": "second/config.env"}, "from-second"),
    ({"FILMOT_CONFIG_FILE": "", "FILMOT_CONFIG_DIR": "second"}, "from-second"),
    # A changed FILMOT_CONFIG_FILE that would itself need expanding or
    # resolving against the working directory keeps the pin too.
    ({"FILMOT_CONFIG_FILE": "=${CONFIG_ANCHOR}/../second/config.env"}, ""),
    ({"FILMOT_CONFIG_FILE": "=%CONFIG_ANCHOR%/../second/config.env"}, ""),
    ({"FILMOT_CONFIG_FILE": "=~/second/config.env"}, ""),
    ({"FILMOT_CONFIG_FILE": "=../second/config.env"}, ""),
])
def test_a_child_honors_only_literal_location_changes_from_its_parent(tmp_path, parent_change, expected):
    _config_dir(tmp_path, "first", "FILMOT_API_KEY=first\n")
    _config_dir(tmp_path, "second", "YOUTUBE_API_KEY=from-second\n")

    def child_value(value):
        # "=" marks a value passed verbatim; others are paths under tmp_path.
        if value.startswith("="):
            return value[1:]
        return str(tmp_path / value) if value else ""

    reported = _parent_and_child(
        tmp_path,
        {"FILMOT_CONFIG_FILE": "${CONFIG_ANCHOR}/config.env", "CONFIG_ANCHOR": str(tmp_path / "first")},
        {name: child_value(value) for name, value in parent_change.items()},
    )

    assert reported["child"][0] == expected


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS config dirs do not expand variables")
@pytest.mark.parametrize("changed", ["XDG_CONFIG_HOME", "APPDATA", "HOME", "HOMEPATH", "USERNAME"])
def test_a_child_never_reexpands_the_config_location(tmp_path, changed):
    # paths expands the default base twice and can read home fallbacks and the
    # working directory, so resolving again in a child could reach a project
    # variable (here CONFIG_ANCHOR, through APPDATA or XDG_CONFIG_HOME). After
    # any parent change to a platform input, the child keeps the pinned path.
    _config_dir(tmp_path / "project" / "attacker", "filmot",
                "FILMOT_API_BASE_URL=https://evil.invalid/api/v1\n")
    reported = _parent_and_child(
        tmp_path,
        {"APPDATA": "${PREFIX}", "XDG_CONFIG_HOME": "${PREFIX}", "PREFIX": "${CONFIG_ANCHOR}"},
        {changed: str(tmp_path / "elsewhere")},
        project_env="CONFIG_ANCHOR=attacker\n",
    )

    assert reported["child"][2] == reported["parent_config"]
    assert reported["child"][1] == "https://apiv1.filmot.com/api/v1"


def test_a_location_named_inside_the_per_user_config_does_not_move_children(tmp_path):
    # The per-user config sets a literal FILMOT_CONFIG_FILE; the first import
    # never loaded that file, so a child must not either.
    _config_dir(tmp_path, "located", "FILMOT_CONFIG_FILE={}\n".format(tmp_path / "other" / "config.env"))
    _config_dir(tmp_path, "other", "YOUTUBE_API_KEY=from-other\n")
    reported = _parent_and_child(tmp_path, {"FILMOT_CONFIG_DIR": str(tmp_path / "located")}, {})

    assert reported["child"][2] == reported["parent_config"]
    assert reported["child"][0] == ""


_RECORDING_PROBE = '''
import json, os
from collections.abc import MutableMapping

real, writes, deletes = os.environ, [], []


class Recorder(MutableMapping):
    def __getitem__(self, key):
        return real[key]

    def __setitem__(self, key, value):
        writes.append([key, value])
        real[key] = value

    def __delitem__(self, key):
        deletes.append(key)
        del real[key]

    def __iter__(self):
        return iter(real)

    def __len__(self):
        return len(real)

    def copy(self):
        return dict(real)


os.environ = Recorder()
import filmot.config
print(json.dumps({"writes": writes, "deletes": deletes}))
'''


def test_config_loading_never_publishes_protected_values_or_removes_variables(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text(
        "FILMOT_API_BASE_URL=https://evil.invalid/api/v1\nfilmot_api_base_url=https://evil.invalid\n"
        "REQUESTS_CA_BUNDLE=./attacker-ca.pem\nPROJECT_ONLY=kept\n",
        encoding="utf-8",
    )
    user_config = tmp_path / "config.env"
    user_config.write_text("FILMOT_API_KEY=user-direct\n", encoding="utf-8")
    probe = tmp_path / "recording_probe.py"
    probe.write_text(_RECORDING_PROBE, encoding="utf-8")
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() not in {
            "FILMOT_API_KEY", "FILMOT_API_BASE_URL", "REQUESTS_CA_BUNDLE",
            "FILMOT_CONFIG_FILE", "FILMOT_TRUSTED_CONFIG", "PROJECT_ONLY",
        }
    }
    environment["FILMOT_CONFIG_FILE"] = str(user_config)
    result = subprocess.run(
        [sys.executable, str(probe)],
        cwd=project, env=environment, check=True, capture_output=True, text=True,
    )
    recorded = json.loads(result.stdout)

    written = {key.upper(): value for key, value in recorded["writes"]}
    assert recorded["deletes"] == []
    assert written["PROJECT_ONLY"] == "kept"
    assert "FILMOT_API_BASE_URL" not in written
    assert "REQUESTS_CA_BUNDLE" not in written
    assert all("evil.invalid" not in value for value in written.values())


def test_project_env_values_cannot_embed_keys_or_set_tls_trust(tmp_path):
    reported = _probe_config(
        tmp_path,
        "HTTPS_PROXY=http://relay:${FILMOT_API_KEY}@proxy.invalid:8080\n"
        "REQUESTS_CA_BUNDLE=./attacker-ca.pem\n",
    )

    environment = reported["first"]["env"]
    # Read literally: the key loaded from the per-user config is never embedded.
    assert environment["HTTPS_PROXY"] == "http://relay:${FILMOT_API_KEY}@proxy.invalid:8080"
    assert environment["REQUESTS_CA_BUNDLE"] is None
    assert reported["first"]["ignored"] == ["REQUESTS_CA_BUNDLE"]


def test_endpoint_overrides_still_come_from_the_environment_and_user_config(tmp_path):
    reported = _probe_config(
        tmp_path,
        "FILMOT_API_BASE_URL=https://evil.invalid/api/v1\n"
        "RAPIDAPI_HOST=filmot-tube-metadata-archive.p.rapidapi.com\n",
        user_env="FILMOT_API_BASE_URL=https://staging.filmot.invalid/api/v1/\n",
        process_env={"RAPIDAPI_HOST": "gateway.filmot.invalid"},
    )

    expected = ["https://staging.filmot.invalid/api/v1", "gateway.filmot.invalid"]
    assert _endpoints(reported["first"]) == expected
    assert _endpoints(reported["reloaded"]) == expected
    assert reported["child"] == expected
    assert reported["first"]["ignored"] == []


def test_a_project_env_default_endpoint_is_dropped_without_a_warning(tmp_path):
    reported = _probe_config(
        tmp_path, "RAPIDAPI_HOST=filmot-tube-metadata-archive.p.rapidapi.com\n",
    )

    assert reported["first"]["ignored"] == []
    assert reported["first"]["env"]["RAPIDAPI_HOST"] is None
    assert _endpoints(reported["first"]) == DEFAULT_ENDPOINTS


def test_whitespace_only_overrides_mean_the_defaults(tmp_path):
    reported = _probe_config(
        tmp_path, "", process_env={"FILMOT_API_BASE_URL": "   ", "RAPIDAPI_HOST": " "},
    )

    assert _endpoints(reported["first"]) == DEFAULT_ENDPOINTS


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
