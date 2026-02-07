"""Shared test fixtures for Filmot CLI tests."""

import pytest
import tempfile
import shutil
from pathlib import Path

from filmot.library import TranscriptLibrary


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
