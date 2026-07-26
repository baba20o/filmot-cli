"""Configuration and API client setup.

The process environment has highest priority. A per-user configuration file is
loaded next so machine-wide proxy settings are shared across projects, followed
by an explicit ``.env`` in the invocation directory for backwards
compatibility and project-specific API credentials.
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

# API Configuration
API_KEY = os.getenv("RAPIDAPI_KEY", "")
API_HOST = os.getenv("RAPIDAPI_HOST", "filmot-tube-metadata-archive.p.rapidapi.com")

BASE_URL = f"https://{API_HOST}"

def get_headers():
    """Return headers required for API requests."""
    return {
        "x-rapidapi-key": API_KEY,
        "x-rapidapi-host": API_HOST
    }

def validate_config():
    """Validate that API credentials are configured."""
    if not API_KEY or API_KEY == "":
        raise ValueError("Missing x-rapidapi-key in Filmot configuration")
    if not API_HOST or API_HOST == "":
        raise ValueError("Missing x-rapidapi-host in Filmot configuration")
    return True
