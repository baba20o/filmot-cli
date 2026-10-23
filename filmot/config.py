"""Configuration and API client setup.

The process environment has highest priority. A per-user configuration file is
loaded next so machine-wide proxy settings are shared across projects, followed
by an explicit ``.env`` in the invocation directory for backwards
compatibility and project-specific API credentials. The project ``.env`` is
read literally (no ``${VAR}`` expansion) and only its allowed values are
exported: never ``PROTECTED_SETTINGS``, which decide where keys are sent, where
the trusted per-user config is found, and how TLS is verified or logged.

Two Filmot API providers are supported side by side: the direct API
(``FILMOT_API_KEY``) and the legacy RapidAPI gateway (``RAPIDAPI_KEY``).
``filmot.api_providers`` turns these settings into per-endpoint routes.
"""

import json
import os
import re
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from .environment import PROJECT_ENV_NAMES, record_project_env_names
from .paths import config_file


PROJECT_ENV_FILE = (os.getcwd() and os.path.join(os.getcwd(), ".env"))

DEFAULT_DIRECT_BASE_URL = "https://apiv1.filmot.com/api/v1"
DEFAULT_RAPIDAPI_HOST = "filmot-tube-metadata-archive.p.rapidapi.com"

# Where API keys are sent.
ENDPOINT_OVERRIDES = {
    "FILMOT_API_BASE_URL": DEFAULT_DIRECT_BASE_URL,
    "RAPIDAPI_HOST": DEFAULT_RAPIDAPI_HOST,
}
# Every input ``paths.config_file`` reads to locate the trusted per-user config,
# Windows home-directory fallbacks included.
CONFIG_LOCATION_SETTINGS = (
    "FILMOT_CONFIG_FILE", "FILMOT_CONFIG_DIR", "XDG_CONFIG_HOME", "APPDATA",
    "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "USERNAME",
)
# The per-user config path the first import in a process tree resolved, while
# only trusted values existed, with the explicit location settings it saw. A
# reload or child process reuses it instead of expanding the location again in
# an environment that may now hold project values. Only a parent's change to a
# literal absolute FILMOT_CONFIG_FILE or FILMOT_CONFIG_DIR, which needs no
# expansion, points a child elsewhere.
TRUSTED_CONFIG_PIN = "FILMOT_TRUSTED_CONFIG"
# A project .env (in a cloned repository, say) cannot set these: the endpoints,
# the config location, TLS trust and key logging, which with a proxy would
# expose request headers, and the record of names it did set.
PROTECTED_SETTINGS = (
    *ENDPOINT_OVERRIDES,
    *CONFIG_LOCATION_SETTINGS,
    TRUSTED_CONFIG_PIN,
    PROJECT_ENV_NAMES,
    "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "SSLKEYLOGFILE",
)
# Nor any PYTHON* variable: those steer every Python child process (the
# transcript worker included), e.g. PYTHONPATH could inject modules.
PROTECTED_PREFIXES = ("PYTHON",)
# Windows environment names are case-insensitive.
_PROTECTED_NAMES = frozenset(name.upper() for name in PROTECTED_SETTINGS)
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _endpoint(value, default):
    return (value or "").strip().rstrip("/") or default


def _literal_absolute(value):
    """The path itself if it is absolute and has nothing to expand, else None."""
    if not value or "$" in value or "%" in value or value.startswith("~"):
        return None
    path = Path(value)
    return path if path.is_absolute() else None


_EXPLICIT_LOCATION = ("FILMOT_CONFIG_FILE", "FILMOT_CONFIG_DIR")


def _trusted_config_file():
    """Locate the per-user config without expanding anything after startup."""
    try:
        pin = json.loads(os.environ.get(TRUSTED_CONFIG_PIN) or "null")
    except ValueError:
        pin = None
    if not (isinstance(pin, dict) and isinstance(pin.get("path"), str)):
        # First import in this process tree: only trusted values exist yet.
        return config_file()
    pinned = Path(pin["path"])
    config_file_value, config_dir_value = (os.environ.get(name) for name in _EXPLICIT_LOCATION)
    if (config_file_value, config_dir_value) == tuple(pin.get(name) for name in _EXPLICIT_LOCATION):
        return pinned
    # A parent changed the explicit location; honor it only if no expansion
    # is needed, keeping config_file's precedence of the file over the dir.
    if config_file_value:
        return _literal_absolute(config_file_value) or pinned
    directory = _literal_absolute(config_dir_value)
    return directory / "config.env" if directory is not None else pinned


def _protected(name):
    upper = name.upper()
    return upper in _PROTECTED_NAMES or upper.startswith(PROTECTED_PREFIXES)


def _apply_project_env(path):
    """Export the project .env's allowed values; return protected ones it tried.

    Values are read literally, so none can embed a key loaded from elsewhere,
    and a protected value is never written, not even briefly. Names that are
    not plain identifiers are skipped. The exported names are recorded so
    path settings never expand them (see ``filmot.environment``).
    """
    ignored = []
    exported = []
    for name, value in dotenv_values(path, interpolate=False).items():
        if value is None or not _ENV_NAME.fullmatch(name) or name in os.environ:
            continue
        if not _protected(name):
            os.environ[name] = value
            exported.append(name)
            continue
        default = ENDPOINT_OVERRIDES.get(name.upper())
        # The documented default endpoint is harmless; anything else is not.
        if default is None or _endpoint(value, default) != default:
            ignored.append(name.upper())
    record_project_env_names(exported)
    return tuple(dict.fromkeys(ignored))


USER_CONFIG_FILE = _trusted_config_file()
# Do not use python-dotenv's implicit upward search: for an installed package it
# starts near site-packages, while an editable install starts in the repository.
# Explicit paths give wheel and editable installs the same behavior.
load_dotenv(dotenv_path=USER_CONFIG_FILE, override=False)
os.environ[TRUSTED_CONFIG_PIN] = json.dumps({
    "path": str(USER_CONFIG_FILE),
    **{name: os.environ.get(name) for name in _EXPLICIT_LOCATION},
})
_trusted_settings = {name: os.environ.get(name) for name in PROTECTED_SETTINGS}
# Protected settings a project .env tried to set; never applied.
IGNORED_PROJECT_SETTINGS = _apply_project_env(PROJECT_ENV_FILE)

# Direct Filmot API (recommended): X-API-Key authentication, credit accounting.
DIRECT_API_KEY = os.getenv("FILMOT_API_KEY", "")
DIRECT_BASE_URL = _endpoint(
    _trusted_settings["FILMOT_API_BASE_URL"], DEFAULT_DIRECT_BASE_URL,
)

# Legacy RapidAPI gateway.
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")
API_HOST = _endpoint(_trusted_settings["RAPIDAPI_HOST"], DEFAULT_RAPIDAPI_HOST)
RAPIDAPI_BASE_URL = f"https://{API_HOST}"

# auto (cost-aware with failover), direct, or rapidapi.
API_PROVIDER = os.getenv("FILMOT_API_PROVIDER", "auto")

# Backward-compatible names from when RapidAPI was the only provider.
API_KEY = RAPIDAPI_KEY
BASE_URL = RAPIDAPI_BASE_URL


def get_headers():
    """Return RapidAPI request headers (legacy helper; prefer api_providers)."""
    return {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": API_HOST
    }


def validate_config():
    """Validate that at least one usable Filmot API provider is configured."""
    from .api_providers import configured_providers

    configured_providers(require=True)
    return True
