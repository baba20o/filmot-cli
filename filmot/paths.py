"""Filesystem locations for project research data and per-user runtime state.

Research corpora belong to the directory in which Filmot is being used. Proxy
credentials and health, on the other hand, describe this user's machine and
must be shared across projects. Keeping both policies here prevents individual
modules from quietly falling back to a relative ``.filmot_data`` path.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Optional, Union


PathInput = Optional[Union[str, os.PathLike]]
APP_NAME = "filmot"


def _absolute(path: Union[str, os.PathLike], *, base: Optional[Path] = None) -> Path:
    """Return an expanded absolute path without requiring it to exist."""
    expanded = Path(os.path.expandvars(str(path))).expanduser()
    if not expanded.is_absolute():
        expanded = (base or Path.cwd()) / expanded
    return expanded.resolve(strict=False)


def project_data_dir(explicit: PathInput = None) -> Path:
    """Return the project-local root for transcripts, sessions, and corpora."""
    value = explicit
    if value is None:
        value = os.environ.get("FILMOT_DATA_DIR")
    if value is None:
        value = Path.cwd() / ".filmot_data"
    return _absolute(value)


def _windows_config_home() -> Path:
    value = os.environ.get("APPDATA")
    if value:
        return _absolute(value)
    return Path.home() / "AppData" / "Roaming"


def _windows_state_home() -> Path:
    value = os.environ.get("LOCALAPPDATA")
    if value:
        return _absolute(value)
    return Path.home() / "AppData" / "Local"


def user_config_dir(explicit: PathInput = None) -> Path:
    """Return Filmot's per-user, cross-project configuration directory."""
    value = explicit
    if value is None:
        value = os.environ.get("FILMOT_CONFIG_DIR")
    if value is not None:
        return _absolute(value)
    if os.name == "nt":
        base = _windows_config_home()
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = _absolute(
            os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
        )
    return _absolute(base / APP_NAME)


def user_state_dir(explicit: PathInput = None) -> Path:
    """Return Filmot's per-user, cross-project mutable state directory."""
    value = explicit
    if value is None:
        value = os.environ.get("FILMOT_STATE_DIR")
    if value is not None:
        return _absolute(value)
    if os.name == "nt":
        base = _windows_state_home()
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = _absolute(
            os.environ.get(
                "XDG_STATE_HOME",
                Path.home() / ".local" / "state",
            )
        )
    return _absolute(base / APP_NAME)


def user_cache_dir(explicit: PathInput = None) -> Path:
    """Return Filmot's per-user cache directory."""
    value = explicit
    if value is None:
        value = os.environ.get("FILMOT_CACHE_DIR")
    if value is not None:
        return _absolute(value)
    if os.name == "nt":
        base = _windows_state_home()
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = _absolute(
            os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
        )
    return _absolute(base / APP_NAME)


def config_file() -> Path:
    """Return the explicit or default per-user environment file."""
    value = os.environ.get("FILMOT_CONFIG_FILE")
    if value:
        return _absolute(value)
    return user_config_dir() / "config.env"


def default_proxy_session_file() -> Path:
    return user_config_dir() / "webshare_info.txt"


def proxy_inventory_file() -> Path:
    """Credential-bearing cache of API-discovered Webshare sessions."""
    return user_config_dir() / "webshare_api_sessions.json"


def proxy_health_db() -> Path:
    """Credential-free, transactional proxy health and lease state."""
    return user_state_dir() / "proxy" / "health.sqlite3"


def legacy_proxy_session_file(data_dir: PathInput = None) -> Path:
    return project_data_dir(data_dir) / "webshare_info.txt"


def legacy_proxy_state_file(
    *,
    file_backed: bool,
    data_dir: PathInput = None,
) -> Path:
    name = "webshare_pool_file.json" if file_backed else "webshare_pool.json"
    return project_data_dir(data_dir) / name


def ensure_private_dir(path: Path) -> Path:
    """Create a per-user directory and restrict POSIX access best-effort."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        try:
            path.chmod(0o700)
        except OSError:
            pass
    return path


def restrict_private_file(path: Path) -> None:
    """Restrict a credential/state file to its owner on POSIX."""
    if os.name != "nt":
        try:
            path.chmod(0o600)
        except OSError:
            pass


def _publication_guard_path(destination: Path) -> Path:
    """Return the persistent inode used for crash-releasing publication locks."""
    return destination.parent / ".filmot-publish.guard"


@contextmanager
def exclusive_file_guard(
    guard_path: Path,
    *,
    timeout: float = 30.0,
    mode: int = 0o600,
):
    """Hold an OS-backed exclusive lock that is released on process exit.

    The small guard file intentionally remains on disk. Lock ownership lives in
    the kernel rather than in the file's existence, so a killed publisher never
    leaves a stale lock that future commands must guess how to recover.
    """
    descriptor = os.open(
        guard_path,
        os.O_RDWR | os.O_CREAT,
        mode,
    )
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        if os.name == "nt":  # pragma: no cover - exercised on Windows
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            while True:
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "Timed out waiting for exclusive file lock: {}".format(
                                guard_path
                            )
                        )
                    time.sleep(0.01)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "Timed out waiting for exclusive file lock: {}".format(
                                guard_path
                            )
                        )
                    time.sleep(0.01)
        yield
    finally:
        if acquired:
            if os.name == "nt":  # pragma: no cover - exercised on Windows
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def _publication_guard(
    destination: Path,
    *,
    timeout: float = 30.0,
    mode: int = 0o600,
):
    """Serialize publishers of files within one destination directory."""
    with exclusive_file_guard(
        _publication_guard_path(destination),
        timeout=timeout,
        mode=mode,
    ):
        yield


def publish_file_exclusive(
    source: Path,
    destination: Path,
    *,
    mode: int = 0o600,
) -> bool:
    """Publish a complete file without overwriting a concurrent destination.

    A same-directory hard link is atomic. Filesystems without hard-link support
    atomically rename the already-complete source while holding a crash-
    releasing OS lock. ``True`` means this call published the file; ``False``
    means the destination already existed. Callers supply a private temporary
    source in the destination directory.
    """
    with _publication_guard(destination, mode=mode):
        if destination.exists():
            return False
        try:
            os.link(source, destination)
            return True
        except FileExistsError:
            return False
        except OSError:
            # ``source`` is complete and fsynced before this call. Rename keeps
            # the destination absent until the whole file is visible, avoiding
            # the partial-file window of an O_EXCL copy fallback.
            try:
                os.replace(source, destination)
            except FileExistsError:
                return False
            return True


def wait_for_file_publication(
    destination: Path,
    *,
    timeout: float = 30.0,
) -> None:
    """Wait until any active publisher releases its crash-safe OS lock."""
    if not destination.parent.exists():
        return
    with _publication_guard(destination, timeout=timeout):
        return


def secure_copy(source: Path, destination: Path) -> Path:
    """Copy a legacy credential file without overwriting a global target."""
    wait_for_file_publication(destination)
    if destination.exists():
        return destination
    ensure_private_dir(destination.parent)
    temporary = destination.with_name(
        ".{}.{}.{}.tmp".format(destination.name, os.getpid(), uuid.uuid4().hex)
    )
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as target, source.open(
            "rb"
        ) as source_file:
            shutil.copyfileobj(source_file, target)
            target.flush()
            os.fsync(target.fileno())
        restrict_private_file(temporary)
        publish_file_exclusive(temporary, destination)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
    restrict_private_file(destination)
    return destination
