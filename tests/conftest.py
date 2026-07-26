"""Shared test fixtures for Filmot CLI tests."""

import os
import pytest
import tempfile
import shutil
from pathlib import Path

# Ensure tests run isolated from the developer's real Webshare credentials.
# Individual tests opt back in by setting these or patching ``filmot.proxy_pool.get_pool``.
os.environ.pop("WEBSHARE_API_TOKEN", None)
os.environ["FILMOT_PROXY_MODE"] = "direct-only"

from filmot.library import TranscriptLibrary
import filmot.proxy_pool as proxy_pool
import filmot.transcript as transcript_module


@pytest.fixture(autouse=True)
def _isolated_proxy_state(monkeypatch, tmp_path):
    """Keep machine-global runtime state inside each test's temp directory."""
    monkeypatch.setenv("FILMOT_CONFIG_DIR", str(tmp_path / "user-config"))
    monkeypatch.setenv("FILMOT_STATE_DIR", str(tmp_path / "user-state"))
    monkeypatch.setenv("FILMOT_CACHE_DIR", str(tmp_path / "user-cache"))
    monkeypatch.setenv(
        "FILMOT_RATE_LIMIT_DB",
        str(tmp_path / "user-state" / "rate_limit.db"),
    )
    monkeypatch.delenv("WEBSHARE_SESSION_FILE", raising=False)
    proxy_pool.reset_pool()
    transcript_module._initialized = False
    yield
    proxy_pool.reset_pool()
    transcript_module._initialized = False


@pytest.fixture
def tmp_dir(tmp_path):
    """Provide a clean temporary directory."""
    return tmp_path


@pytest.fixture
def library(tmp_path):
    """Provide a TranscriptLibrary backed by a temp directory."""
    data_dir = tmp_path / ".filmot_data"
    return TranscriptLibrary(data_dir=str(data_dir))


@pytest.fixture
def populated_library(library):
    """Library pre-loaded with sample transcripts."""
    library.save(
        video_id="abc12345678",
        topic="test-topic",
        transcript_text="Hello world this is a test transcript about machine learning and AI.",
        metadata={
            "title": "Test Video 1",
            "channel": "Test Channel",
            "language": "en",
            "is_generated": True,
            "duration_seconds": 120,
        },
    )
    library.save(
        video_id="def12345678",
        topic="test-topic",
        transcript_text="Another transcript covering neural networks and deep learning concepts.",
        metadata={
            "title": "Test Video 2",
            "channel": "AI Channel",
            "language": "en",
            "is_generated": False,
            "duration_seconds": 300,
        },
    )
    library.save(
        video_id="ghi12345678",
        topic="other-topic",
        transcript_text="This video is about cooking pasta and Italian recipes.",
        metadata={
            "title": "Pasta Tutorial",
            "channel": "Cooking Channel",
            "language": "en",
            "is_generated": True,
            "duration_seconds": 600,
        },
    )
    return library
