"""Tests for the hard-cancellable JSON worker supervisor."""

import os
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from filmot._process import (
    IsolatedWorkerProtocolError,
    IsolatedWorkerTimeout,
    run_json_worker,
)


HELPER = Path(__file__).parent / "helpers" / "json_worker.py"


def test_json_worker_round_trips_unicode():
    result = run_json_worker(
        "unused.test.module",
        {"mode": "echo", "value": "人工知能 café"},
        timeout_seconds=5,
        command=[sys.executable, str(HELPER)],
    )

    assert result == {"echo": "人工知能 café"}


def test_the_real_worker_never_imports_from_the_working_directory(tmp_path, monkeypatch):
    marker = tmp_path / "planted-module-imported.txt"
    trap = "open({!r}, 'a').write(__name__ + '\\n')\n".format(str(marker))
    for name in ("json.py", "runpy.py", "sitecustomize.py", "youtube_transcript_api.py"):
        (tmp_path / name).write_text(trap, encoding="utf-8")
    (tmp_path / "filmot").mkdir()
    (tmp_path / "filmot" / "__init__.py").write_text(trap, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    response = run_json_worker(
        "filmot._transcript_worker", {"protocol_version": 1}, timeout_seconds=30,
    )

    assert response["error"]["error_type"] == "WorkerRequestError"
    assert not marker.exists(), marker.read_text(encoding="utf-8")


def test_a_relative_or_empty_pythonpath_cannot_reach_the_working_directory(tmp_path, monkeypatch):
    # "." and an empty component both name the working directory, and
    # sitecustomize would run during startup, before any bootstrap code.
    marker = tmp_path / "planted-module-imported.txt"
    trap = "open({!r}, 'a').write(__name__ + '\\n')\n".format(str(marker))
    for name in ("sitecustomize.py", "usercustomize.py", "json.py"):
        (tmp_path / name).write_text(trap, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(["", ".", ""]))
    monkeypatch.chdir(tmp_path)

    response = run_json_worker(
        "filmot._transcript_worker", {"protocol_version": 1}, timeout_seconds=30,
    )

    assert response["error"]["error_type"] == "WorkerRequestError"
    assert not marker.exists(), marker.read_text(encoding="utf-8")


def test_the_worker_import_path_drops_the_working_directory(tmp_path, monkeypatch):
    from filmot import _process

    elsewhere = tmp_path / "site"
    elsewhere.mkdir()
    monkeypatch.chdir(tmp_path)
    working_directory = str(tmp_path)
    if os.name == "nt":
        working_directory = working_directory.upper()  # same directory, other case
    monkeypatch.setattr(sys, "path", ["", ".", "relative", working_directory, str(elsewhere)])

    path = _process._worker_sys_path()

    assert path == [str(elsewhere)]


def test_a_checkout_run_from_its_root_never_runs_an_installed_filmot(tmp_path, monkeypatch):
    # From the checkout root, the checkout leaves the worker's import path
    # with the rest of the working directory; an installed copy that is still
    # on it must not stand in for the package this process runs.
    from filmot import _process

    marker = tmp_path / "installed-filmot-imported.txt"
    trap = "open({!r}, 'a').write(__name__ + '\\n')\n".format(str(marker))
    installed = tmp_path / "site-packages" / "filmot"
    installed.mkdir(parents=True)
    for name in ("__init__.py", "_transcript_worker.py"):
        (installed / name).write_text(trap, encoding="utf-8")
    monkeypatch.setattr(sys, "path", [str(installed.parent)] + sys.path)
    monkeypatch.chdir(_process._PACKAGE_ROOT)

    response = run_json_worker(
        "filmot._transcript_worker", {"protocol_version": 1}, timeout_seconds=30,
    )

    assert response["error"]["error_type"] == "WorkerRequestError"
    assert not marker.exists(), marker.read_text(encoding="utf-8")


def test_timed_out_worker_is_killed_and_reaped(tmp_path):
    started = tmp_path / "started"
    completed = tmp_path / "completed"

    with pytest.raises(IsolatedWorkerTimeout) as captured:
        run_json_worker(
            "unused.test.module",
            {
                "mode": "hang",
                "started_path": str(started),
                "completed_path": str(completed),
                "delay": 60,
            },
            timeout_seconds=1,
            command=[sys.executable, str(HELPER)],
        )

    assert started.read_text(encoding="utf-8") == "started"
    assert not completed.exists()
    assert captured.value.worker_terminated is True
    assert captured.value.returncode is not None


def test_worker_failure_redacts_credentials():
    secret = "top-secret-password"
    proxy_url = f"http://proxy-user:{secret}@proxy.example:8080"

    with pytest.raises(IsolatedWorkerProtocolError) as captured:
        run_json_worker(
            "unused.test.module",
            {
                "mode": "fail",
                "diagnostic": f"could not connect through {proxy_url}",
            },
            timeout_seconds=5,
            secrets=(secret, proxy_url),
            command=[sys.executable, str(HELPER)],
        )

    message = str(captured.value)
    assert secret not in message
    assert "proxy-user" not in message
    assert "http://***:***@proxy.example:8080" in message


def test_non_object_worker_response_is_rejected():
    with pytest.raises(
        IsolatedWorkerProtocolError,
        match="must be one JSON object",
    ):
        run_json_worker(
            "unused.test.module",
            {"mode": "array"},
            timeout_seconds=5,
            command=[sys.executable, str(HELPER)],
        )


def test_base_exception_kills_and_reaps_worker():
    class InterruptingProcess:
        pid = 123

        def __init__(self):
            self.returncode = None
            self.killed = False
            self.communicate_calls = 0

        def poll(self):
            return self.returncode

        def communicate(self, input=None, timeout=None):
            self.communicate_calls += 1
            if not self.killed:
                raise KeyboardInterrupt
            return b"", b""

        def kill(self):
            self.killed = True
            self.returncode = -9

    process = InterruptingProcess()
    with patch("filmot._process.subprocess.Popen", return_value=process):
        with pytest.raises(KeyboardInterrupt):
            run_json_worker(
                "filmot._transcript_worker",
                {"secret": "stdin-only"},
                timeout_seconds=5,
            )

    assert process.killed is True
    assert process.returncode == -9
    assert process.communicate_calls == 2


def test_sensitive_payload_is_sent_on_stdin_not_argv():
    secret = "stdin-only-password"

    class ImmediateProcess:
        pid = 456
        returncode = 0

        def __init__(self):
            self.input = None

        def communicate(self, input=None, timeout=None):
            self.input = input
            return b'{"ok":true}', b""

    process = ImmediateProcess()
    with patch(
        "filmot._process.subprocess.Popen",
        return_value=process,
    ) as popen:
        result = run_json_worker(
            "filmot._transcript_worker",
            {"password": secret},
            timeout_seconds=5,
            secrets=(secret,),
        )

    command = popen.call_args.args[0]
    assert secret not in " ".join(command)
    assert secret.encode("utf-8") in process.input
    assert result == {"ok": True}
