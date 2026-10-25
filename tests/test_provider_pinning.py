"""An explicitly pinned provider is independent of unused provider settings."""

import pytest

from filmot import config
from filmot.api_providers import ProviderConfigurationError, providers_for


@pytest.mark.parametrize("policy,broken,endpoint", [
    ("direct", {"API_HOST": "https://unused.invalid"}, "/getchannels"),
    ("rapidapi", {"DIRECT_BASE_URL": "http://unused.invalid"}, "/getvideos"),
])
def test_pinning_ignores_invalid_unused_provider(monkeypatch, policy, broken, endpoint):
    monkeypatch.setattr(config, "API_PROVIDER", policy)
    for name, value in broken.items():
        monkeypatch.setattr(config, name, value)
    assert [provider.name for provider in providers_for(endpoint)] == [policy]


@pytest.mark.parametrize("policy,broken", [
    ("direct", {"DIRECT_BASE_URL": "http://selected.invalid"}),
    ("rapidapi", {"API_HOST": "https://selected.invalid"}),
    ("auto", {"API_HOST": "https://selected.invalid"}),
])
def test_invalid_selected_provider_is_still_rejected(monkeypatch, policy, broken):
    monkeypatch.setattr(config, "API_PROVIDER", policy)
    for name, value in broken.items():
        monkeypatch.setattr(config, name, value)
    with pytest.raises(ProviderConfigurationError):
        providers_for("/getvideos")
