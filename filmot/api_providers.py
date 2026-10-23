"""Filmot API providers: the direct API and the legacy RapidAPI gateway.

The direct API (``FILMOT_API_KEY``, ``X-API-Key``) serves every endpoint and
charges credits from a monthly allowance. The RapidAPI gateway serves only the
legacy routes and counts requests against its own plan.

Policies:

* ``auto`` (default): legacy routes prefer RapidAPI when it is configured, so
  searches and lookups do not spend direct credits, and fail over to the direct
  API on authentication, quota, availability, or transport failures.
  Direct-only routes always use the direct API.
* ``direct`` / ``rapidapi``: pin one provider for every request, without
  failover. Direct-only routes fail clearly when RapidAPI is pinned.
"""

from __future__ import annotations

import os
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import click

from . import config


DIRECT = "direct"
RAPIDAPI = "rapidapi"
AUTO = "auto"
POLICIES = (AUTO, DIRECT, RAPIDAPI)

# Routes the RapidAPI gateway serves with the same contract as the direct API.
# ``getsubtitlesearch`` is deliberately absent: on RapidAPI that path silently
# aliases the legacy subtitle search instead of the direct export endpoint.
LEGACY_ENDPOINTS = frozenset({"getsearchsubtitles", "getvideos", "getsearchchannels"})

NO_CREDENTIALS_MESSAGE = (
    "No Filmot API credentials are configured. Set FILMOT_API_KEY (recommended; "
    "create a key at https://filmot.com/api-keys) or RAPIDAPI_KEY."
)

_policy_override: ContextVar[Optional[str]] = ContextVar(
    "filmot_api_provider", default=None,
)


class ProviderConfigurationError(ValueError):
    """No configured provider can serve the request; nothing was sent."""


@dataclass(frozen=True)
class Provider:
    """One authenticated route to the Filmot API. Credentials never appear in repr."""

    name: str
    label: str
    # A base-URL override may carry userinfo credentials.
    base_url: str = field(repr=False)
    key_env: str
    _headers: Tuple[Tuple[str, str], ...] = field(repr=False, compare=False)

    def headers(self) -> Dict[str, str]:
        return dict(self._headers)

    def url(self, endpoint: str) -> str:
        return "{}/{}".format(self.base_url, endpoint.lstrip("/"))


def endpoint_name(endpoint: str) -> str:
    """Normalize ``/getVideos?x=1`` to ``getvideos``."""
    return endpoint.strip().split("?", 1)[0].strip("/").casefold()


def selected_policy() -> str:
    value = _policy_override.get() or config.API_PROVIDER or AUTO
    policy = str(value).strip().casefold() or AUTO
    if policy not in POLICIES:
        raise ProviderConfigurationError(
            "FILMOT_API_PROVIDER must be auto, direct, or rapidapi (got {!r}).".format(value)
        )
    return policy


def policy_source() -> str:
    if _policy_override.get():
        return "--api-provider"
    if os.environ.get("FILMOT_API_PROVIDER"):
        return "FILMOT_API_PROVIDER"
    return "default"


def configured_secrets() -> Tuple[str, ...]:
    """Every configured credential value, whether or not its provider is usable.

    Redaction must not depend on routing: a RapidAPI key with a blank host is
    unusable but still secret. Longest values come first so that a key which
    contains another is masked whole instead of leaving fragments.
    """
    values = {
        value.strip()
        for value in (config.DIRECT_API_KEY, config.RAPIDAPI_KEY)
        if isinstance(value, str) and value.strip()
    }
    return tuple(sorted(values, key=len, reverse=True))


_HOST_PORT = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::([0-9]{1,5}))?")


def endpoint_problem(name: str) -> Optional[str]:
    """Why a provider's configured endpoint must not receive its key, if so."""
    if name == DIRECT:
        try:
            parsed = urlsplit(config.DIRECT_BASE_URL)
            # Reading .port validates it (numeric, 0-65535) or raises ValueError.
            valid = parsed.scheme == "https" and bool(parsed.hostname) and (
                parsed.port is None or parsed.port > 0
            )
        except ValueError:
            valid = False
        if not valid:
            return "FILMOT_API_BASE_URL must be an https:// URL; no request was sent."
        return None
    match = _HOST_PORT.fullmatch(config.API_HOST or "")
    if not match or (match.group(1) is not None and not 0 < int(match.group(1)) <= 65535):
        return "RAPIDAPI_HOST must be a host name with an optional port; no request was sent."
    return None


def configured_providers(require: bool = False) -> Dict[str, Provider]:
    """Return configured providers; with ``require``, enforce the selected policy.

    A provider whose endpoint fails ``endpoint_problem`` is left out, and
    ``require`` raises that problem, so its key is never sent anywhere.
    """
    providers: Dict[str, Provider] = {}
    direct_key = (config.DIRECT_API_KEY or "").strip()
    if direct_key:
        problem = endpoint_problem(DIRECT)
        if problem is None:
            providers[DIRECT] = Provider(
                name=DIRECT,
                label="direct Filmot API",
                base_url=config.DIRECT_BASE_URL.rstrip("/"),
                key_env="FILMOT_API_KEY",
                _headers=(("X-API-Key", direct_key),),
            )
        elif require:
            raise ProviderConfigurationError(problem)
    rapid_key = (config.RAPIDAPI_KEY or "").strip()
    host = (config.API_HOST or "").strip()
    if rapid_key and host:
        problem = endpoint_problem(RAPIDAPI)
        if problem is None:
            providers[RAPIDAPI] = Provider(
                name=RAPIDAPI,
                label="RapidAPI gateway",
                base_url=config.RAPIDAPI_BASE_URL.rstrip("/"),
                key_env="RAPIDAPI_KEY",
                _headers=(("x-rapidapi-key", rapid_key), ("x-rapidapi-host", host)),
            )
        elif require:
            raise ProviderConfigurationError(problem)
    if require:
        policy = selected_policy()
        if not providers:
            raise ProviderConfigurationError(NO_CREDENTIALS_MESSAGE)
        if policy == DIRECT and DIRECT not in providers:
            raise ProviderConfigurationError(
                "The direct provider is selected but FILMOT_API_KEY is not set."
            )
        if policy == RAPIDAPI and RAPIDAPI not in providers:
            raise ProviderConfigurationError(
                "The rapidapi provider is selected but RAPIDAPI_KEY is not set."
            )
    return providers


def providers_for(endpoint: str) -> List[Provider]:
    """Return the providers to try for ``endpoint``, in order."""
    name = endpoint_name(endpoint)
    policy = selected_policy()
    providers = configured_providers(require=True)
    direct_only = name not in LEGACY_ENDPOINTS
    if policy == RAPIDAPI:
        if direct_only:
            raise ProviderConfigurationError(
                "/{} is only available from the direct Filmot API, but the rapidapi "
                "provider is selected. Use --api-provider auto or direct.".format(name)
            )
        return [providers[RAPIDAPI]]
    if policy == DIRECT:
        return [providers[DIRECT]]
    if direct_only:
        if DIRECT not in providers:
            raise ProviderConfigurationError(
                "/{} is only available from the direct Filmot API; set FILMOT_API_KEY "
                "(create a key at https://filmot.com/api-keys).".format(name)
            )
        return [providers[DIRECT]]
    return [providers[key] for key in (RAPIDAPI, DIRECT) if key in providers]


def describe_configuration() -> List[Tuple[str, str]]:
    """Credential-free provider rows for ``filmot config``."""
    from .redaction import redact_sensitive_text

    providers = configured_providers()
    secrets = configured_secrets()

    def key_state(name: str, key: str) -> str:
        if name in providers:
            return "configured"
        return "set but unusable" if (key or "").strip() else "not configured"

    def endpoint(value: str, name: str) -> str:
        # A base-URL override may carry userinfo; never print it.
        shown = redact_sensitive_text(value, secrets)
        problem = endpoint_problem(name)
        return "{} (rejected: {})".format(shown, problem) if problem else shown

    rows = [
        ("Filmot API Key (direct)", key_state(DIRECT, config.DIRECT_API_KEY)),
        ("Direct API URL", endpoint(config.DIRECT_BASE_URL, DIRECT)),
        ("RapidAPI Key", key_state(RAPIDAPI, config.RAPIDAPI_KEY)),
        ("RapidAPI Host", endpoint(config.API_HOST, RAPIDAPI)),
    ]
    if config.IGNORED_PROJECT_SETTINGS:
        rows.append((
            "Ignored in project .env",
            "{} (these come only from the environment or the per-user "
            "config)".format(", ".join(config.IGNORED_PROJECT_SETTINGS)),
        ))
    try:
        policy = selected_policy()
    except ProviderConfigurationError as error:
        return [("API Provider", "invalid: {}".format(error))] + rows
    rows.insert(0, ("API Provider", "{} ({})".format(policy, policy_source())))
    for label, endpoint in (("Legacy routes", "getsearchsubtitles"), ("Direct-only routes", "getchannels")):
        try:
            route = " -> ".join(provider.name for provider in providers_for(endpoint))
        except ProviderConfigurationError as error:
            route = "unavailable: {}".format(error)
        rows.append((label, route))
    return rows


def _validate_policy(ctx, param, value):
    return value.casefold() if value else value


def api_provider_option(function):
    """Select a provider policy for one invocation once parsing has succeeded."""
    @wraps(function)
    def invoke(*args, **kwargs):
        value = kwargs.pop("_filmot_api_provider", None)
        if value is not None:
            ctx = click.get_current_context()
            # Mirror session_option: defer the context-variable mutation until a
            # surrounding context scope guarantees its reset.
            token = _policy_override.set(value)
            ctx.call_on_close(lambda: _policy_override.reset(token))
        return function(*args, **kwargs)

    return click.option(
        "--api-provider", "_filmot_api_provider", default=None,
        type=click.Choice(POLICIES, case_sensitive=False), callback=_validate_policy,
        help=(
            "Filmot API provider: auto (cost-aware routing with failover), direct, "
            "or rapidapi. Defaults to FILMOT_API_PROVIDER, then auto."
        ),
    )(invoke)
