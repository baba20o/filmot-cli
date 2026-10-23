"""Trusted variable expansion and tolerant numeric settings."""

import os
import subprocess
import sys

import pytest

from filmot import environment
from filmot.environment import (
    PROJECT_ENV_NAMES,
    env_number,
    expand_trusted,
    project_env_names,
    record_project_env_names,
)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    # Set before deleting: delenv alone records nothing to restore when the
    # name was absent, so the record a test writes would outlive it.
    monkeypatch.setenv(PROJECT_ENV_NAMES, "")
    monkeypatch.delenv(PROJECT_ENV_NAMES)
    monkeypatch.setattr(environment, "_warned", set())


def test_variables_a_project_env_added_are_never_expanded(monkeypatch):
    monkeypatch.setenv("TRUSTED_DIR", "/trusted")
    monkeypatch.setenv("PROXY_DIR", "/attacker")
    record_project_env_names(["PROXY_DIR"])

    assert expand_trusted("$TRUSTED_DIR/a:${TRUSTED_DIR}/b") == "/trusted/a:/trusted/b"
    assert expand_trusted("$PROXY_DIR/sessions.txt") == "$PROXY_DIR/sessions.txt"
    assert expand_trusted("${PROXY_DIR}/x and $UNSET_ANYWHERE") == "${PROXY_DIR}/x and $UNSET_ANYWHERE"


@pytest.mark.skipif(os.name != "nt", reason="%VAR% expands only on Windows")
def test_windows_percent_variables_follow_the_same_rule(monkeypatch):
    monkeypatch.setenv("TRUSTED_DIR", "C:\\trusted")
    monkeypatch.setenv("PROXY_DIR", "C:\\attacker")
    record_project_env_names(["proxy_dir"])  # names are case-insensitive here

    assert expand_trusted("%trusted_dir%\\a") == "C:\\trusted\\a"
    assert expand_trusted("%PROXY_DIR%\\a") == "%PROXY_DIR%\\a"


def test_the_record_merges_with_what_a_parent_passed_down(monkeypatch):
    monkeypatch.setenv(PROJECT_ENV_NAMES, "FROM_PARENT")

    record_project_env_names(["FROM_CHILD"])
    record_project_env_names([])

    assert project_env_names() == {"FROM_PARENT", "FROM_CHILD"}


PARITY_CASES = [
    "$FILMOT_T_DIR/a", "${FILMOT_T_DIR}/b", "$$FILMOT_T_DIR", "%%FILMOT_T_DIR%%",
    "'%FILMOT_T_DIR%'", "%FILMOT_T_DIR%/c", "%UNDEFINED_X%", "$UNDEFINED_X",
    "${UNCLOSED", "%UNCLOSED", "'unclosed %FILMOT_T_DIR%", "$FILMOT_T_DIR-x", "${}",
    "%%", "$$", "a%b%c", "$1abc", "plain", "%", "$", "'", "''%FILMOT_T_DIR%",
    # Older and newer CPython releases expand these differently on POSIX.
    "${unclosed/$FILMOT_T_DIR/file", "${a}${unclosed $FILMOT_T_DIR",
]


@pytest.mark.parametrize("case", PARITY_CASES)
def test_expansion_matches_the_standard_library_on_both_platforms(monkeypatch, case):
    import ntpath
    import posixpath

    monkeypatch.setenv("FILMOT_T_DIR", "/trusted")

    assert environment._expand_windows(case, os.environ.get) == ntpath.expandvars(case)
    assert environment._expand_posix(case, os.environ.get) == posixpath.expandvars(case)
    assert expand_trusted(case) == os.path.expandvars(case)


def test_expansion_matches_the_standard_library_on_random_strings(monkeypatch):
    import ntpath
    import posixpath
    import random

    monkeypatch.setenv("FILMOT_T_A", "/a")
    monkeypatch.setenv("FILMOT_T_B-1", "/b")
    pieces = ["$", "{", "}", "%", "'", "FILMOT_T_A", "FILMOT_T_B-1", "filmot_t_a", "-", "/", "é"]
    generator = random.Random(20261023)
    for _ in range(5000):
        case = "".join(generator.choice(pieces) for _ in range(generator.randint(1, 9)))
        assert environment._expand_windows(case, os.environ.get) == ntpath.expandvars(case), case
        assert environment._expand_posix(case, os.environ.get) == posixpath.expandvars(case), case


@pytest.mark.parametrize("raw,expected", [
    ("", 4), ("  ", 4), ("7", 7), (" 9 ", 9),
    ("four", 4), ("0", 4), ("-3", 4), ("2.5", 4),
    ("9" * 400, int("9" * 400)),
])
def test_integer_settings_fall_back_to_the_default(monkeypatch, raw, expected):
    monkeypatch.setenv("FILMOT_TEST_NUMBER", raw)

    assert env_number("FILMOT_TEST_NUMBER", 4, minimum=1) == expected


@pytest.mark.parametrize("raw,expected", [
    ("1.5", 1.5), ("nan", 6.0), ("inf", 6.0), ("-1", 6.0), ("x", 6.0), ("1e400", 6.0),
])
def test_float_settings_reject_unusable_values(monkeypatch, raw, expected):
    monkeypatch.setenv("FILMOT_TEST_HOURS", raw)

    assert env_number("FILMOT_TEST_HOURS", 6.0, cast=float, minimum=0) == expected


def test_an_unusable_value_is_reported_once(monkeypatch, capsys):
    monkeypatch.setenv("FILMOT_TEST_NUMBER", "lots")

    env_number("FILMOT_TEST_NUMBER", 4)
    env_number("FILMOT_TEST_NUMBER", 4)

    assert capsys.readouterr().err.count("ignoring FILMOT_TEST_NUMBER='lots'; using 4") == 1


def test_a_typo_in_a_proxy_setting_no_longer_breaks_importing_filmot(tmp_path):
    # A fresh interpreter, so the import-time parse runs without reloading
    # filmot.transcript under the other tests.
    environment = dict(os.environ, FILMOT_PROXY_RETRY_LIMIT="four")

    result = subprocess.run(
        [sys.executable, "-c", "import filmot.transcript as t; print(t._POOL_RETRY_LIMIT)"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, check=True,
    )

    assert result.stdout.strip() == "4"
    assert "ignoring FILMOT_PROXY_RETRY_LIMIT='four'; using 4" in result.stderr


def test_a_typo_in_a_proxy_setting_no_longer_breaks_the_pool(monkeypatch, tmp_path):
    import filmot.proxy_pool as proxy_pool

    monkeypatch.setenv("FILMOT_PROXY_MAX_SESSIONS", "lots")
    monkeypatch.setenv("FILMOT_PROXY_LEASE_SECONDS", "soon")
    monkeypatch.delenv("WEBSHARE_API_TOKEN", raising=False)
    monkeypatch.setenv("WEBSHARE_SESSION_FILE", str(tmp_path / "sessions.txt"))
    (tmp_path / "sessions.txt").write_text("proxy.invalid:8080:user:secret\n", encoding="utf-8")
    try:
        assert proxy_pool.get_pool(force_new=True) is not None
    finally:
        # The fixtures restore the environment afterwards; only drop the pool.
        proxy_pool.reset_pool()
