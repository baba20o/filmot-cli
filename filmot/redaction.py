"""Dependency-free credential redaction for every diagnostic boundary."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Iterable


_CREDENTIAL_QUERY_PARAM_NAMES = (
    "key",
    "apikey",
    "api_key",
    "access_token",
    "auth",
    "token",
    "secret",
    "password",
    "signature",
    "x-amz-signature",
    "x-amz-credential",
    "x-amz-security-token",
)

_CREDENTIAL_QUERY_PARAM_RE = re.compile(
    r"(?i)\b("
    + "|".join(re.escape(name) for name in _CREDENTIAL_QUERY_PARAM_NAMES)
    + r")=[^&#\s]+"
)

_CREDENTIAL_FIELD_NAMES = frozenset(
    {
        "key",
        "apikey",
        "youtubeapikey",
        "developerkey",
        "privatekey",
        "secretkey",
        "xgoogapikey",
        "authorization",
        "auth",
        "accesstoken",
        "refreshtoken",
        "token",
        "secret",
        "password",
        "signature",
        "credential",
        "credentials",
        "cookie",
        "setcookie",
    }
)


def _is_credential_field(value: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(value).lower())
    return normalized in _CREDENTIAL_FIELD_NAMES or normalized.endswith(
        (
            "apikey",
            "accesstoken",
            "refreshtoken",
            "authtoken",
            "token",
            "secret",
            "password",
            "credential",
            "credentials",
            "signature",
        )
    )


def redact_sensitive_text(value: object, secrets: Iterable[str] = ()) -> str:
    """Remove URL userinfo, query credentials, and caller-known secrets."""
    text = str(value)
    text = re.sub(
        r"(?i)(https?://)[^/\s@]+@",
        r"\1***:***@",
        text,
    )
    text = _CREDENTIAL_QUERY_PARAM_RE.sub(r"\1=***", text)
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "***")
    return text


def redact_sensitive_value(value: Any, secrets: Iterable[str] = ()) -> Any:
    """Recursively redact diagnostic-shaped containers without stringifying them."""
    known = tuple(str(secret) for secret in secrets if secret)
    if isinstance(value, Mapping):
        return {
            key: (
                "***"
                if _is_credential_field(key)
                else redact_sensitive_value(item, known)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_value(item, known) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_value(item, known) for item in value)
    if isinstance(value, (str, Exception)):
        return redact_sensitive_text(value, known)
    return value
