"""Configuration and API client setup."""

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

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
        raise ValueError("Missing x-rapidapi-key in .env file")
    if not API_HOST or API_HOST == "":
        raise ValueError("Missing x-rapidapi-host in .env file")
    return True
