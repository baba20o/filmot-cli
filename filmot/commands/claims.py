"""Commands for a durable, human-authored claim/evidence register."""

from __future__ import annotations

import math
import re
from typing import Optional

import click
from rich.panel import Panel
from rich.table import Table

from ..claims import (
    CLAIM_CONFIDENCE,
    CLAIM_RELATIONS,
    CLAIM_VERDICTS,
    SOURCE_INDEPENDENCE,
    SOURCE_KINDS,
    get_claim_store,
    validate_youtube_video_id,
)
from ..session_context import session_option
from ..cli_support import (
    command_error as _command_error,
    console,
    emit_raw_result as _emit_raw_result,
    prepare_raw_result as _prepare_raw_result,
)
from ..schemas import CLAIM_SCHEMA, ClaimResultData, CommandResult, ResultStatus


_CITATION_TIMESTAMP_RE = re.compile(
    r"(?:(?P<hours>[0-9]+):(?P<hour_minutes>[0-5][0-9]):|"
    r"(?P<minutes>[0-9]+):)(?P<seconds>[0-5][0-9])\Z"
)


def _parse_citation_timestamp(value: str) -> float:
    """Parse CLI citation seconds or a displayed ``M:SS``/``H:MM:SS`` value."""
    candidate = value.strip()
    if ":" not in candidate:
        try:
            seconds = float(candidate)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "must be finite non-negative seconds or M:SS/H:MM:SS"
            ) from error
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError(
                "must be finite non-negative seconds or M:SS/H:MM:SS"
            )
        return 0.0 if seconds == 0 else seconds

    match = _CITATION_TIMESTAMP_RE.fullmatch(candidate)
    if match is None:
        raise ValueError(
            "must be finite non-negative seconds or M:SS/H:MM:SS"
        )
    hours = float(match.group("hours") or 0)
    minutes = float(match.group("hour_minutes") or match.group("minutes"))
    seconds = float(match.group("seconds"))
    total = hours * 3600 + minutes * 60 + seconds
    if not math.isfinite(total):
        raise ValueError(
            "must be finite non-negative seconds or M:SS/H:MM:SS"
        )
    return total


def _emit_raw(outcome: CommandResult[ClaimResultData]) -> None:
    _emit_raw_result(outcome, indent=2)


def _mutation_result(
    action: str,
    topic: str,
    claim: dict,
    *,
    created: bool,
    extra: Optional[dict] = None,
) -> CommandResult[ClaimResultData]:
    data: ClaimResultData = {
        "claim_schema": CLAIM_SCHEMA,
        "topic": topic,
        "claim_id": str(claim["claim_id"]),
        "created": created,
        "rows": [claim],
        "summary": {"action": action},
    }
    if extra:
        data["summary"].update(extra)
    return CommandResult(
        command="claims-{}".format(action),
        status=ResultStatus.COMPLETED if created else ResultStatus.SKIPPED,
        data=data,
    )


def _log_mutation(outcome: CommandResult[ClaimResultData], topic: str) -> None:
    """Log only identifiers and counts; claim text and excerpts stay private."""
    from ..ledger import log_result

    summary = outcome.data.get("summary") or {}
    log_result(
        outcome.command.replace("-", "_"),
        outcome,
        topic=topic,
        data={
            "claim_id": outcome.data.get("claim_id"),
            "action": summary.get("action"),
            "created": outcome.data.get("created"),
            "relation": summary.get("relation"),
            "verdict": summary.get("verdict"),
        },
    )


def _render_mutation(outcome: CommandResult[ClaimResultData]) -> None:
    data = outcome.data
    summary = data.get("summary") or {}
    action = summary.get("action")
    claim_id = data.get("claim_id", "")
    created = bool(data.get("created"))
    if action == "add":
        message = "Created" if created else "Already present"
        console.print("[green]{} claim {}[/green]".format(message, claim_id))
    elif action == "cite":
        message = "Added" if created else "Already attached"
        console.print(
            "[green]{} {} evidence for {}[/green]".format(
                message,
                summary.get("relation", ""),
                claim_id,
            )
        )
    elif action == "assess":
        message = "Updated" if created else "Assessment unchanged for"
        console.print(
            "[green]{} {}: {} / {}[/green]".format(
                message,
                claim_id,
                summary.get("verdict", "open"),
                summary.get("confidence", "unknown"),
            )
        )


def _render_claims(outcome: CommandResult[ClaimResultData]) -> None:
    rows = outcome.data.get("rows") or []
    topic = outcome.data.get("topic", "")
    if not rows:
        console.print("[yellow]No claims recorded for '{}'.[/yellow]".format(topic))
        return

    if len(rows) == 1 and outcome.data.get("claim_id"):
        claim = rows[0]
        summary = claim.get("summary") or {}
        console.print(
            Panel(
                "[bold]{}[/bold]\n\nVerdict: [cyan]{}[/cyan]  "
                "Confidence: [cyan]{}[/cyan]\nEvidence: {} across {} sources".format(
                    claim.get("text", ""),
                    claim.get("verdict", "open"),
                    claim.get("confidence", "unknown"),
                    summary.get("evidence", 0),
                    summary.get("sources", 0),
                ),
                title=str(claim.get("claim_id", "Claim")),
            )
        )
        if claim.get("assessment_note"):
            console.print("[dim]Assessment:[/dim] {}".format(claim["assessment_note"]))
        for evidence in claim.get("evidence") or []:
            locator = evidence.get("locator")
            if evidence.get("video_id") and evidence.get("start_seconds") is not None:
                locator = "{}s".format(evidence["start_seconds"])
            console.print(
                "\n  [bold cyan]{}[/bold cyan] {}{}".format(
                    evidence.get("relation", ""),
                    evidence.get("deep_link") or evidence.get("source", ""),
                    " ({})".format(locator) if locator else "",
                )
            )
            if evidence.get("excerpt"):
                console.print("    [dim]{}[/dim]".format(evidence["excerpt"]))
            if evidence.get("note"):
                console.print("    Note: {}".format(evidence["note"]))
        return

    table = Table(title="Claims: {}".format(topic))
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Verdict")
    table.add_column("Confidence")
    table.add_column("Evidence", justify="right")
    table.add_column("Statement")
    for claim in rows:
        summary = claim.get("summary") or {}
        table.add_row(
            str(claim.get("claim_id", "")),
            str(claim.get("verdict", "open")),
            str(claim.get("confidence", "unknown")),
            str(summary.get("evidence", 0)),
            str(claim.get("text", "")),
        )
    console.print(table)


@click.group()
@session_option
def claims():
    """Track claims, citations, contradictions, and human assessments.

    Claim data is strict and append-only. Filmot records relationships but
    never infers stance, independence, confidence, or truth.
    If publication-temporary cleanup fails, the error names that append's
    exact temporary path and states whether the immutable event is already
    durable; do not assume a reported cleanup failure rolled the mutation back.
    """
    pass


@claims.command("add")
@session_option
@click.argument("topic")
@click.argument("text")
@click.option("--id", "claim_id", default=None, help="Optional stable claim ID")
@click.option("--raw", is_flag=True, help="Output one versioned JSON result")
def claims_add(topic: str, text: str, claim_id: Optional[str], raw: bool):
    """Declare an exact claim statement under TOPIC."""
    try:
        claim, created = get_claim_store().add_claim(topic, text, claim_id=claim_id)
    except Exception as error:
        _command_error(
            str(error),
            command="claims-add",
            raw=raw,
            error_type=type(error).__name__,
            stage="persist-claim",
        )
    outcome = _mutation_result("add", topic, claim, created=created)
    if raw:
        outcome = _prepare_raw_result(outcome)
    _log_mutation(outcome, topic)
    if raw:
        _emit_raw(outcome)
    else:
        _render_mutation(outcome)


@claims.command("cite")
@session_option
@click.argument("topic")
@click.argument("claim_id")
@click.option(
    "--source",
    default=None,
    help="Source URL, DOI, patent ID, or local reference; mutually exclusive with --video",
)
@click.option("--video", "video_id", default=None, help="YouTube video ID; classifies this source as video and is mutually exclusive with --source")
@click.option(
    "--at",
    "start_seconds",
    default=None,
    help="Video timestamp as seconds, M:SS, or H:MM:SS",
)
@click.option("--relation", "relation", required=True, type=click.Choice(CLAIM_RELATIONS), help="How the source relates to the claim")
@click.option("--source-kind", type=click.Choice(SOURCE_KINDS), default=None, help="Source medium; inferred as video/web when omitted")
@click.option("--locator", default=None, help="Page, table, figure, claim, or section locator")
@click.option("--excerpt", default=None, help="Short exact source excerpt")
@click.option("--note", default=None, help="Analyst note, kept separate from the excerpt")
@click.option("--primary/--secondary", default=None, help="Explicit primary-source classification")
@click.option("--independence", type=click.Choice(SOURCE_INDEPENDENCE), default="unknown", show_default=True)
@click.option("--lineage-group", default=None, help="Human-assigned shared-origin or echo group")
@click.option("--raw", is_flag=True, help="Output one versioned JSON result")
def claims_cite(
    topic: str,
    claim_id: str,
    source: Optional[str],
    video_id: Optional[str],
    start_seconds: Optional[str],
    relation: str,
    source_kind: Optional[str],
    locator: Optional[str],
    excerpt: Optional[str],
    note: Optional[str],
    primary: Optional[bool],
    independence: str,
    lineage_group: Optional[str],
    raw: bool,
):
    """Attach one explicitly classified evidence item to CLAIM_ID."""
    if start_seconds is not None:
        try:
            start_seconds = _parse_citation_timestamp(start_seconds)
        except ValueError as error:
            if raw:
                _command_error(
                    "Invalid --at value: {}".format(error),
                    command="claims-cite",
                    raw=True,
                    error_type="InvalidOptionValue",
                    stage="validate-evidence",
                )
            raise click.BadParameter(str(error), param_hint="--at")
    if not source and not video_id:
        _command_error(
            "Provide --source or --video",
            command="claims-cite",
            raw=raw,
            stage="validate-evidence",
        )
    if start_seconds is not None and not video_id:
        _command_error(
            "--at requires --video",
            command="claims-cite",
            raw=raw,
            stage="validate-evidence",
        )
    if video_id and source_kind not in (None, "video"):
        _command_error(
            "--video cannot be combined with a non-video --source-kind",
            command="claims-cite",
            raw=raw,
            stage="validate-evidence",
        )
    if source and video_id:
        _command_error(
            "--source and --video are mutually exclusive; --video supplies "
            "the canonical YouTube source",
            command="claims-cite",
            raw=raw,
            stage="validate-evidence",
        )
    if video_id:
        try:
            video_id = validate_youtube_video_id(video_id)
        except ValueError as error:
            _command_error(
                str(error),
                command="claims-cite",
                raw=raw,
                error_type=type(error).__name__,
                stage="validate-evidence",
            )

    title = channel = research_run_id = None
    resolved_source = source
    resolved_kind = source_kind
    if video_id:
        resolved_source = resolved_source or "https://youtube.com/watch?v={}".format(video_id)
        resolved_kind = resolved_kind or "video"
        try:
            from ..library import get_library

            saved = get_library().get(video_id, topic)
            if saved:
                metadata = saved.get("metadata") or {}
                title_value = metadata.get("title")
                channel_value = metadata.get("channel")
                run_value = metadata.get("research_run_id")
                title = title_value if isinstance(title_value, str) else None
                channel = channel_value if isinstance(channel_value, str) else None
                research_run_id = run_value if isinstance(run_value, str) else None
        except Exception:
            # Enrichment is optional; durable evidence persistence remains strict.
            pass
    resolved_kind = resolved_kind or (
        "web" if str(resolved_source).startswith(("http://", "https://")) else "other"
    )

    try:
        claim, evidence, created = get_claim_store().add_evidence(
            topic,
            claim_id,
            relation=relation,
            source=str(resolved_source),
            source_kind=resolved_kind,
            video_id=video_id,
            start_seconds=start_seconds,
            locator=locator,
            excerpt=excerpt,
            note=note,
            primary=primary,
            independence=independence,
            lineage_group=lineage_group,
            title=title,
            channel=channel,
            research_run_id=research_run_id,
        )
    except Exception as error:
        _command_error(
            str(error),
            command="claims-cite",
            raw=raw,
            error_type=type(error).__name__,
            stage="persist-evidence",
        )
    outcome = _mutation_result(
        "cite",
        topic,
        claim,
        created=created,
        extra={"relation": relation, "evidence_id": evidence["evidence_id"]},
    )
    if raw:
        outcome = _prepare_raw_result(outcome)
    _log_mutation(outcome, topic)
    if raw:
        _emit_raw(outcome)
    else:
        _render_mutation(outcome)


@claims.command("assess")
@session_option
@click.argument("topic")
@click.argument("claim_id")
@click.option("--verdict", required=True, type=click.Choice(CLAIM_VERDICTS))
@click.option("--confidence", type=click.Choice(CLAIM_CONFIDENCE), default="unknown", show_default=True)
@click.option("--note", default="", help="Human rationale for the assessment")
@click.option("--raw", is_flag=True, help="Output one versioned JSON result")
def claims_assess(
    topic: str,
    claim_id: str,
    verdict: str,
    confidence: str,
    note: str,
    raw: bool,
):
    """Append a human verdict that supersedes the prior assessment."""
    try:
        claim, created = get_claim_store().assess(
            topic,
            claim_id,
            verdict=verdict,
            confidence=confidence,
            note=note,
        )
    except Exception as error:
        _command_error(
            str(error),
            command="claims-assess",
            raw=raw,
            error_type=type(error).__name__,
            stage="persist-assessment",
        )
    outcome = _mutation_result(
        "assess",
        topic,
        claim,
        created=created,
        extra={"verdict": verdict, "confidence": confidence},
    )
    if raw:
        outcome = _prepare_raw_result(outcome)
    _log_mutation(outcome, topic)
    if raw:
        _emit_raw(outcome)
    else:
        _render_mutation(outcome)


@claims.command("show")
@click.argument("topic")
@click.argument("claim_id", required=False)
@click.option("--raw", is_flag=True, help="Output one versioned JSON result")
def claims_show(topic: str, claim_id: Optional[str], raw: bool):
    """Show the folded claim register or one claim without writing state."""
    try:
        if claim_id:
            claim = get_claim_store().get_claim(topic, claim_id)
            rows = [claim] if claim else []
        else:
            rows = get_claim_store().list_claims(topic)
    except Exception as error:
        _command_error(
            str(error),
            command="claims-show",
            raw=raw,
            error_type=type(error).__name__,
            stage="read-claims",
        )
    data: ClaimResultData = {
        "claim_schema": CLAIM_SCHEMA,
        "topic": topic,
        "rows": rows,
        "summary": {
            "claims": len(rows),
            "evidence": sum(
                int((row.get("summary") or {}).get("evidence", 0)) for row in rows
            ),
        },
    }
    if claim_id:
        data["claim_id"] = claim_id
    outcome = CommandResult(
        command="claims-show",
        status=ResultStatus.COMPLETED if rows else ResultStatus.EMPTY,
        data=data,
    )
    if raw:
        _emit_raw(outcome)
    else:
        _render_claims(outcome)
