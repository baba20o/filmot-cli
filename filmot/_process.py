"""Small, cross-platform helpers for isolated JSON worker processes.

The transcript transport uses this module as a hard cancellation boundary.
Worker input is written to stdin so proxy credentials never appear in the
process command line, and worker output must be exactly one JSON object.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, Optional, Sequence


class IsolatedWorkerError(RuntimeError):
    """Base class for failures supervising an isolated worker."""


class IsolatedWorkerTimeout(TimeoutError):
    """Raised after a timed-out worker has been killed and reaped."""

    def __init__(
        self,
        timeout_seconds: float,
        pid: int,
        returncode: Optional[int],
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.pid = pid
        self.returncode = returncode
        self.worker_terminated = True
        super().__init__(
            f"Isolated worker timed out after {timeout_seconds:g} seconds"
        )


class IsolatedWorkerProtocolError(IsolatedWorkerError):
    """Raised when a worker cannot start or violates the JSON protocol."""


def _redact_diagnostic(value: object, secrets: Iterable[str] = ()) -> str:
    """Redact URL userinfo and caller-known secrets from worker diagnostics."""
    text = str(value)
    text = re.sub(
        r"(?i)(https?://)[^/\s@]+@",
        r"\1***:***@",
        text,
    )
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "***")
    return text


def _kill_and_reap(
    process: subprocess.Popen,
) -> tuple[bytes, bytes]:
    """Kill ``process`` if needed, drain its pipes, and reap it."""
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            # The worker may have exited between poll() and kill().
            pass

    # communicate() after kill both drains redirected pipes and waits for the
    # process handle. It is safe to call again after TimeoutExpired.
    try:
        stdout, stderr = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        # A second kill is harmless and protects against a platform race where
        # the first signal arrived while the child was still being created.
        try:
            process.kill()
        except OSError:
            pass
        stdout, stderr = process.communicate()
    return stdout or b"", stderr or b""


def run_json_worker(
    module: str,
    payload: Dict[str, Any],
    *,
    timeout_seconds: float,
    secrets: Iterable[str] = (),
    command: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Run one Python worker and return its single JSON-object response.

    ``command`` is an internal test seam. Production callers pass a module
    name, which is launched as ``sys.executable -m <module>``. Sensitive
    values belong only in ``payload`` and are sent over stdin.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")

    worker_command = (
        [str(part) for part in command]
        if command is not None
        else [sys.executable, "-m", module]
    )
    encoded_payload = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    secret_values = tuple(str(secret) for secret in secrets if secret)
    deadline = time.monotonic() + timeout_seconds
    worker_environment = os.environ.copy()
    package_root = str(Path(__file__).resolve().parent.parent)
    existing_python_path = worker_environment.get("PYTHONPATH")
    worker_environment["PYTHONPATH"] = (
        package_root
        if not existing_python_path
        else package_root + os.pathsep + existing_python_path
    )

    try:
        process = subprocess.Popen(
            worker_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            env=worker_environment,
        )
    except OSError as exc:
        detail = _redact_diagnostic(exc, secret_values)
        raise IsolatedWorkerProtocolError(
            f"Could not start isolated worker: {detail}"
        ) from exc

    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(worker_command, timeout_seconds)
        stdout, stderr = process.communicate(
            input=encoded_payload,
            timeout=remaining,
        )
    except subprocess.TimeoutExpired as exc:
        _kill_and_reap(process)
        raise IsolatedWorkerTimeout(
            timeout_seconds,
            process.pid,
            process.returncode,
        ) from exc
    except BaseException:
        # KeyboardInterrupt and SystemExit must not strand a child process.
        _kill_and_reap(process)
        raise

    stderr_text = _redact_diagnostic(
        (stderr or b"").decode("utf-8", errors="replace"),
        secret_values,
    ).strip()
    if process.returncode != 0:
        detail = f": {stderr_text[:1000]}" if stderr_text else ""
        raise IsolatedWorkerProtocolError(
            f"Isolated worker exited with status {process.returncode}{detail}"
        )

    try:
        decoded = (stdout or b"").decode("utf-8")
        response = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        detail = f" ({stderr_text[:1000]})" if stderr_text else ""
        raise IsolatedWorkerProtocolError(
            f"Isolated worker returned invalid JSON{detail}"
        ) from exc

    if not isinstance(response, dict):
        raise IsolatedWorkerProtocolError(
            "Isolated worker response must be one JSON object"
        )
    return response
