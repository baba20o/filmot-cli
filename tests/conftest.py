"""Shared test fixtures for Filmot CLI tests."""

import inspect
import os
import pytest
import tempfile
import shutil
from pathlib import Path

from click.testing import CliRunner

# Ensure tests run isolated from the developer's real Webshare credentials.
# Individual tests opt back in by setting these or patching ``filmot.proxy_pool.get_pool``.
os.environ.pop("WEBSHARE_API_TOKEN", None)
os.environ["FILMOT_PROXY_MODE"] = "direct-only"

from filmot.library import TranscriptLibrary
import filmot.proxy_pool as proxy_pool
import filmot.transcript as transcript_module


@pytest.fixture(autouse=True)
def _isolated_proxy_state(monkeypatch, tmp_path):
    """Keep machine-global and project-local state inside each test's temp directory."""
    # Project-local research data (including legacy proxy state such as
    # ``.filmot_data/webshare_pool_file.json``) resolves from the working
    # directory, so a developer checkout must never leak into a test.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FILMOT_CONFIG_DIR", str(tmp_path / "user-config"))
    monkeypatch.setenv("FILMOT_STATE_DIR", str(tmp_path / "user-state"))
    monkeypatch.setenv("FILMOT_CACHE_DIR", str(tmp_path / "user-cache"))
    monkeypatch.setenv(
        "FILMOT_RATE_LIMIT_DB",
        str(tmp_path / "user-state" / "rate_limit.db"),
    )
    monkeypatch.delenv("WEBSHARE_SESSION_FILE", raising=False)
    monkeypatch.delenv("FILMOT_SESSION", raising=False)
    proxy_pool.reset_pool()
    transcript_module._initialized = False
    yield
    proxy_pool.reset_pool()
    transcript_module._initialized = False


@pytest.fixture(autouse=True)
def _isolated_api_providers(monkeypatch):
    """Never let a test reach a live Filmot API with the developer's real keys.

    ``filmot.config`` loads the working directory's ``.env`` at import, so real
    credentials may be in the environment. Fake keys on reserved ``.invalid``
    hosts mean an accidentally unmocked request fails without spending credits.
    The guard below covers only these Filmot hosts; it does not block other
    network access.
    """
    import requests

    import filmot.config as app_config
    from filmot import api_usage

    monkeypatch.setattr(app_config, "DIRECT_API_KEY", "test-direct-key")
    monkeypatch.setattr(app_config, "DIRECT_BASE_URL", "https://direct.filmot.invalid/api/v1")
    monkeypatch.setattr(app_config, "RAPIDAPI_KEY", "test-rapidapi-key")
    monkeypatch.setattr(app_config, "API_HOST", "rapidapi.filmot.invalid")
    monkeypatch.setattr(app_config, "RAPIDAPI_BASE_URL", "https://rapidapi.filmot.invalid")
    monkeypatch.setattr(app_config, "API_PROVIDER", "auto")
    monkeypatch.delenv("FILMOT_API_PROVIDER", raising=False)

    # Tests that mock a client's session never reach this guard; any request
    # that does is an unmocked Filmot API call and fails the test.
    unmocked = []
    real_request = requests.sessions.Session.request

    def guarded_request(self, method, url, *args, **kwargs):
        if ".filmot.invalid" in str(url):
            unmocked.append(str(url).split("?", 1)[0])
            raise requests.exceptions.ConnectionError("unmocked Filmot API request in tests")
        return real_request(self, method, url, *args, **kwargs)

    monkeypatch.setattr(requests.sessions.Session, "request", guarded_request)
    api_usage.reset()
    yield
    api_usage.reset()
    assert not unmocked, "Test made unmocked Filmot API requests: {}".format(unmocked)


@pytest.fixture
def tmp_dir(tmp_path):
    """Provide a clean temporary directory."""
    return tmp_path


@pytest.fixture
def stream_runner():
    """CliRunner whose results keep stdout and stderr apart on every supported Click.

    Click 8.2+ always captures the streams separately (``output`` interleaves
    both). Click 8.1, the newest release for Python 3.9, mixes stderr into
    ``result.stdout`` unless ``mix_stderr=False`` is passed, an argument 8.2
    removed; ``result.output`` then omits stderr. Use this runner for tests that
    parse stdout or inspect stderr.
    """
    if "mix_stderr" in inspect.signature(CliRunner.__init__).parameters:
        return CliRunner(mix_stderr=False)
    return CliRunner()


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
