"""Configuration and API client setup.

The process environment has highest priority. A per-user configuration file is
loaded next so machine-wide proxy settings are shared across projects, followed
by an explicit ``.env`` in the invocation directory for backwards
compatibility and project-specific API credentials.

Two Filmot API providers are supported side by side: the direct API
(``FILMOT_API_KEY``) and the legacy RapidAPI gateway (``RAPIDAPI_KEY``).
``filmot.api_providers`` turns these settings into per-endpoint routes.
"""

import os
from dotenv import load_dotenv

from .paths import config_file


USER_CONFIG_FILE = config_file()
PROJECT_ENV_FILE = (os.getcwd() and os.path.join(os.getcwd(), ".env"))

# Do not use python-dotenv's implicit upward search: for an installed package it
# starts near site-packages, while an editable install starts in the repository.
# Explicit paths give wheel and editable installs the same behavior.
load_dotenv(dotenv_path=USER_CONFIG_FILE, override=False)
load_dotenv(dotenv_path=PROJECT_ENV_FILE, override=False)

DEFAULT_DIRECT_BASE_URL = "https://apiv1.filmot.com/api/v1"
DEFAULT_RAPIDAPI_HOST = "filmot-tube-metadata-archive.p.rapidapi.com"

# Direct Filmot API (recommended): X-API-Key authentication, credit accounting.
DIRECT_API_KEY = os.getenv("FILMOT_API_KEY", "")
DIRECT_BASE_URL = os.getenv("FILMOT_API_BASE_URL", DEFAULT_DIRECT_BASE_URL).rstrip("/")

# Legacy RapidAPI gateway.
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")
API_HOST = os.getenv("RAPIDAPI_HOST", DEFAULT_RAPIDAPI_HOST)
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
