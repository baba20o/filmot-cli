"""Invocation-scoped investigation identity, independent of library topics."""

from contextvars import ContextVar
from functools import wraps
from typing import Optional

import click
from click.core import ParameterSource


_session: ContextVar[Optional[str]] = ContextVar("filmot_session", default=None)


def current_session() -> Optional[str]:
    """Return the selected investigation for this command and its nested work."""
    return _session.get()


def _validate_session(ctx, param, value):
    if value is not None and not value.strip():
        raise click.BadParameter("Session name must not be blank.", ctx, param)
    return value


def session_option(function):
    """Select a session only after parsing has succeeded and cleanup is owned."""
    @wraps(function)
    def invoke(*args, **kwargs):
        value = kwargs.pop("_filmot_session", None)
        if value is not None:
            ctx = click.get_current_context()
            # Click does not close contexts that fail during argument parsing.
            # Defer the context-variable mutation until callback invocation,
            # when a surrounding context scope guarantees its cleanup.
            inherited_default = (
                ctx.get_parameter_source("_filmot_session") == ParameterSource.ENVIRONMENT
                and current_session() is not None
            )
            if not inherited_default:
                token = _session.set(value)
                ctx.call_on_close(lambda: _session.reset(token))
        return function(*args, **kwargs)

    return click.option(
        "--session", "_filmot_session", default=None,
        envvar="FILMOT_SESSION", show_envvar=True, callback=_validate_session,
        help="Group activity in a named investigation without changing library topics.",
    )(invoke)
