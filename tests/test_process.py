"""Tests for the hard-cancellable JSON worker supervisor."""

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
