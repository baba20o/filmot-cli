"""Trusted environment lookups shared by configuration and storage paths.

A project ``.env`` (in a cloned repository, say) may add variables. A path
setting must not let one of those complete a trusted value such as
``WEBSHARE_SESSION_FILE=$PROXY_DIR/sessions.txt``, so expansion consults only
variables the project did not add. ``filmot.config`` records the names it
added in ``FILMOT_PROJECT_ENV_NAMES``, which child processes inherit.

Numeric settings are parsed tolerantly: a typo falls back to the default with
one warning instead of a traceback.
"""

from __future__ import annotations

import math
import os
import posixpath
import re
import sys
from typing import Callable, Iterable, Optional, Set, TypeVar


PROJECT_ENV_NAMES = "FILMOT_PROJECT_ENV_NAMES"

# The patterns posixpath.expandvars and ntpath.expandvars use, so escapes
# ($$, %%) and quoting ('...' on Windows) keep their usual meaning. The 2025
# CPython security releases changed the POSIX one (an unclosed "${" now ends
# expansion), so it comes from the running interpreter where it has one.
_POSIX_VARIABLE = re.compile(
    getattr(posixpath, "_varpattern", r"\$(\w+|\{[^}]*\})"), re.ASCII
)
_WINDOWS_VARIABLE = re.compile(r"'[^']*'?|%(%|[^%]*%?)|\$(\$|[-\w]+|\{[^}]*\}?)", re.ASCII)
_warned: Set[str] = set()

Number = TypeVar("Number", int, float)


def _key(name: str) -> str:
    # Windows environment names are case-insensitive.
    return name.upper() if os.name == "nt" else name


def project_env_names() -> Set[str]:
    """Names a project .env added in this process or one of its parents."""
    return {
        _key(name) for name in (os.environ.get(PROJECT_ENV_NAMES) or "").split(",") if name
    }


def record_project_env_names(names: Iterable[str]) -> None:
    """Add ``names`` to the inherited record of project-supplied variables."""
    merged = sorted(project_env_names() | {_key(name) for name in names})
    if merged:
        os.environ[PROJECT_ENV_NAMES] = ",".join(merged)


Lookup = Callable[[str], Optional[str]]


def _expand_posix(path: str, lookup: Lookup) -> str:
    """``posixpath.expandvars`` with ``lookup`` in place of ``os.environ``."""
    if "$" not in path:
        return path

    def replace(match: "re.Match[str]") -> str:
        name = match[1]
        if name.startswith("{"):
            if not name.endswith("}"):
                return match[0]
            name = name[1:-1]
        value = lookup(name)
        return match[0] if value is None else value

    return _POSIX_VARIABLE.sub(replace, path)


def _expand_windows(path: str, lookup: Lookup) -> str:
    """``ntpath.expandvars`` with ``lookup`` in place of ``os.environ``."""
    if "$" not in path and "%" not in path:
        return path

    def replace(match: "re.Match[str]") -> str:
        if match.lastindex is None:
            return match[0]  # text in single quotes stays as written
        name = match[match.lastindex]
        if match.lastindex == 1:
            if name == "%":
                return name  # %% is a literal %
            if not name.endswith("%"):
                return match[0]
            name = name[:-1]
        else:
            if name == "$":
                return name  # $$ is a literal $
            if name.startswith("{"):
                if not name.endswith("}"):
                    return match[0]
                name = name[1:-1]
        value = lookup(name)
        return match[0] if value is None else value

    return _WINDOWS_VARIABLE.sub(replace, path)


def expand_trusted(value: str) -> str:
    """``os.path.expandvars(value)``, but only from the trusted environment.

    The platform's syntax, escapes, and quoting apply unchanged. A variable a
    project .env added is treated as undefined, so its reference is left as
    written.
    """
    blocked = project_env_names()
    environ = {
        _key(name): text for name, text in os.environ.items() if _key(name) not in blocked
    }

    def lookup(name: str) -> Optional[str]:
        return environ.get(_key(name))

    expand = _expand_windows if os.name == "nt" else _expand_posix
    return expand(value, lookup)


def env_number(
    name: str,
    default: Number,
    *,
    cast: Callable[[str], Number] = int,
    minimum: Optional[float] = None,
) -> Number:
    """A numeric setting, or ``default`` with one warning when it is unusable."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value: Optional[Number] = cast(raw)
    except ValueError:
        value = None
    if (
        value is None
        # Only floats can be non-finite; math.isfinite would overflow on a
        # huge integer.
        or (isinstance(value, float) and not math.isfinite(value))
        or (minimum is not None and value < minimum)
    ):
        if name not in _warned:
            _warned.add(name)
            print(
                "Filmot: ignoring {}={!r}; using {}.".format(name, raw, default),
                file=sys.stderr,
            )
        return default
    return value
