"""Transcript-library and research-session commands."""

import json as json_mod
from typing import Optional

import click
from rich.panel import Panel
from rich.table import Table

from ..cli_support import command_error as _command_error, console
from ..schemas import (
    CommandResult,
    ErrorDetail,
    LibraryResultData,
    ResultStatus,
    SessionResultData,
)
from .search import _format_duration


def _render_library_list(
    outcome: CommandResult[LibraryResultData],
) -> None:
    """Render a typed library inventory."""
    data = outcome.data
    rows = data.get("rows") or []
    topic = data.get("topic")
    if not rows:
        if topic:
            console.print(
                f"[yellow]No transcripts in topic: {topic}[/yellow]"
            )
        else:
            console.print(
                "[yellow]Library is empty. Use 'filmot transcript VIDEO_ID "
                "--save-to TOPIC' to add.[/yellow]"
            )
        return

    if topic:
        table = Table(title=f"Transcripts in '{topic}'")
        table.add_column("Video ID", style="cyan")
        table.add_column("Title", style="white", max_width=50)
        table.add_column("Channel", style="green")
        table.add_column("Size", style="dim")
        table.add_column("Saved", style="dim")
        for row in rows:
            title = str(row.get("title", "Unknown"))
            if len(title) > 47:
                title = title[:47] + "..."
            table.add_row(
                str(row.get("video_id", "")),
                title,
                str(row.get("channel", "Unknown")),
                f"{int(row.get('char_count', 0)):,} chars",
                (
                    str(row.get("saved_at"))[:10]
                    if row.get("saved_at")
                    else "Unknown"
                ),
            )
        console.print(table)
        console.print(f"\n[dim]Total: {len(rows)} transcripts[/dim]")
        return

    table = Table(title="Transcript Library")
    table.add_column("Topic", style="cyan")
    table.add_column("Transcripts", style="white", justify="right")
    for row in rows:
        table.add_row(str(row.get("topic", "")), str(row.get("count", 0)))
    console.print(table)
    total = sum(int(row.get("count", 0)) for row in rows)
    console.print(
        f"\n[dim]Total: {len(rows)} topics, {total} transcripts[/dim]"
    )


def _render_library_failure(
    outcome: CommandResult[LibraryResultData],
    prefix: str,
) -> None:
    """Render a structured library failure through Click's error boundary."""
    if outcome.status_value != ResultStatus.FAILED.value:
        return
    message = (
        outcome.errors[0].message
        if outcome.errors
        else "unknown library error"
    )
    _command_error(f"{prefix}: {message}")


def _render_library_search(
    outcome: CommandResult[LibraryResultData],
) -> None:
    """Render library-search output from its typed result."""
    _render_library_failure(outcome, "Library search failed")
    data = outcome.data
    query = str(data.get("query") or "")
    topic = data.get("topic")
    rows = data.get("rows") or []
    summary = data.get("summary") or {}

    if summary.get("fallback_used"):
        console.print(
            "[dim]No exact word matches. Showing substring matches "
            "(plurals/inflections):[/dim]"
        )

    if not rows:
        console.print(f"[yellow]No matches for '{query}'[/yellow]")
        if not topic:
            console.print(
                "[dim]Try searching within a specific topic: "
                "--topic NAME[/dim]"
            )
        return

    console.print(
        f"\n[bold]Found {int(summary.get('matches', 0))} matches across "
        f"{len(rows)} transcripts[/bold]\n"
    )
    for row in rows[:10]:
        console.print(
            f"[bold cyan]{row['video_id']}[/bold cyan] "
            f"({row['match_count']} matches)"
        )
        console.print(
            f"  Topic: [green]{row['topic']}[/green] | "
            f"{row.get('title', 'Unknown')} - "
            f"{row.get('channel', 'Unknown')}"
        )
        for match in row.get("matches", [])[:2]:
            console.print(f"  [dim]...{match}[/dim]")
        console.print()


@click.group()
def library():
    """Manage local transcript library.

    The library stores transcripts organized by topic/keyword for
    building curated knowledge bases.

    Examples:

    \b
        filmot library list                    # List all topics
        filmot library list prompt-injection   # List transcripts in topic
        filmot library search "attack"         # Search across all transcripts
        filmot library context prompt-injection # Get all text for LLM context
        filmot library stats                   # Show library statistics
    """
    pass


@library.command("list")
@click.argument("topic", required=False)
def library_list(topic: str):
    """List topics or transcripts in a topic.

    Without arguments, lists all topics.
    With a topic name, lists all transcripts in that topic.
    """
    from ..library import get_library
    from ..ledger import log_result
    lib = get_library()

    if topic:
        # List transcripts in topic
        transcripts = lib.list_transcripts(topic)
        data: LibraryResultData = {
            "topic": topic,
            "rows": transcripts,
            "summary": {"transcripts": len(transcripts)},
        }
        outcome = CommandResult(
            command="library-list",
            status=(
                ResultStatus.COMPLETED
                if transcripts
                else ResultStatus.EMPTY
            ),
            data=data,
        )
        log_result(
            "library_list",
            outcome,
            topic=topic,
            data={"scope": "topic", "transcripts": len(transcripts)},
        )
    else:
        # List all topics
        topics = lib.list_topics()
        total = sum(int(row.get("count", 0)) for row in topics)
        data = {
            "rows": topics,
            "summary": {
                "topics": len(topics),
                "transcripts": total,
            },
        }
        outcome = CommandResult(
            command="library-list",
            status=(
                ResultStatus.COMPLETED
                if topics
                else ResultStatus.EMPTY
            ),
            data=data,
        )
        log_result(
            "library_list",
            outcome,
            data={"scope": "all", "topics": len(topics)},
        )
    _render_library_list(outcome)


@library.command("search")
@click.argument("query")
@click.option("--topic", "-t", default=None, help="Limit search to specific topic")
@click.option("--substring", is_flag=True, help="Use substring matching instead of word-boundary matching")
def library_search(query: str, topic: str, substring: bool):
    """Search for text across saved transcripts.

    Uses word-boundary matching by default (searching "ore" won't match "more").
    Use --substring for the old behavior.
    """
    from ..library import get_library
    from ..ledger import log_result
    lib = get_library()

    used_substring = substring
    fallback_used = False
    try:
        with console.status(f"Searching library for '{query}'..."):
            results = lib.search(
                query,
                topic=topic,
                substring=substring,
            )

        # Auto-fallback catches plurals/inflections.
        if not results and not substring:
            results = lib.search(query, topic=topic, substring=True)
            if results:
                used_substring = True
                fallback_used = True
    except Exception as error:
        data: LibraryResultData = {
            "topic": topic,
            "query": query,
            "rows": [],
            "summary": {
                "substring": used_substring,
                "fallback_used": fallback_used,
                "sources": 0,
                "matches": 0,
            },
        }
        outcome = CommandResult.failed(
            "library-search",
            data,
            ErrorDetail.from_exception(
                error,
                stage="search",
                details={"topic": topic, "substring": used_substring},
            ),
        )
        log_result(
            "library_search",
            outcome,
            topic=topic,
            data={
                "query": query,
                "substring": used_substring,
                "sources": 0,
                "matches": 0,
            },
        )
        _render_library_search(outcome)
        raise click.exceptions.Exit(1)

    total_matches = sum(int(row["match_count"]) for row in results)
    data = {
        "topic": topic,
        "query": query,
        "rows": results,
        "summary": {
            "substring": used_substring,
            "fallback_used": fallback_used,
            "sources": len(results),
            "matches": total_matches,
        },
    }
    outcome = CommandResult(
        command="library-search",
        status=(
            ResultStatus.COMPLETED
            if results
            else ResultStatus.EMPTY
        ),
        data=data,
        warnings=(
            ["Used substring fallback after word-boundary search was empty."]
            if fallback_used
            else []
        ),
    )
    log_result(
        "library_search",
        outcome,
        topic=topic,
        data={
            "query": query,
            "substring": used_substring,
            "sources": len(results),
            "matches": total_matches,
        },
    )
    _render_library_search(outcome)


def _render_library_context(
    outcome: CommandResult[LibraryResultData],
) -> None:
    """Render context delivery from the same result written to the ledger."""
    if outcome.status_value == ResultStatus.FAILED.value:
        stage = outcome.errors[0].stage if outcome.errors else None
        prefix = (
            "Error saving file"
            if stage == "write-output"
            else "Error building context"
        )
        _render_library_failure(outcome, prefix)
        return

    data = outcome.data
    topic = str(data.get("topic") or "")
    rows = data.get("rows") or []
    summary = data.get("summary") or {}
    if outcome.status_value == ResultStatus.EMPTY.value:
        console.print(
            f"[yellow]No transcripts in topic: {topic}[/yellow]"
        )
        return

    output = data.get("output")
    if output:
        console.print(f"[green]✓ Saved context to: {output}[/green]")
        console.print(
            f"[dim]Size: {int(summary.get('chars', 0)):,} characters[/dim]"
        )
        return

    content = str(rows[0].get("content") or "") if rows else ""
    console.print(content)


@library.command("context")
@click.argument("topic")
@click.option("--max-chars", "-m", default=None, type=click.IntRange(1), help="Maximum total characters")
@click.option("--output", "-o", default=None, help="Save to file instead of printing")
@click.option("--format", "-f", "fmt", type=click.Choice(["text", "structured"]), default="text", help="Output format: text (plain) or structured (markdown with metadata)")
def library_context(topic: str, max_chars: int, output: str, fmt: str):
    """Get all transcripts in a topic as combined text.

    Useful for providing LLM context. Concatenates all transcripts
    with headers separating each video.

    Use --format structured for markdown with full metadata headers.
    """
    from ..library import get_library
    from ..ledger import log_result
    lib = get_library()

    try:
        with console.status(f"Building context from '{topic}'..."):
            if fmt == "structured":
                context = _build_structured_context(lib, topic, max_chars)
            else:
                context = lib.get_context(topic, max_chars=max_chars)
    except Exception as error:
        data: LibraryResultData = {
            "topic": topic,
            "rows": [],
            "summary": {
                "format": fmt,
                "max_chars": max_chars,
                "chars": 0,
                "delivery": "failed",
            },
        }
        outcome = CommandResult.failed(
            "library-context",
            data,
            ErrorDetail.from_exception(
                error,
                stage="build-context",
                details={"topic": topic, "format": fmt},
            ),
        )
        log_result(
            "library_context",
            outcome,
            topic=topic,
            data={
                "format": fmt,
                "max_chars": max_chars,
                "chars": 0,
                "delivery": "failed",
            },
        )
        _render_library_context(outcome)
        raise click.exceptions.Exit(1)

    if not context:
        data = {
            "topic": topic,
            "rows": [],
            "summary": {
                "format": fmt,
                "max_chars": max_chars,
                "chars": 0,
                "delivery": "empty",
            },
        }
        outcome = CommandResult(
            command="library-context",
            status=ResultStatus.EMPTY,
            data=data,
        )
        log_result(
            "library_context",
            outcome,
            topic=topic,
            data={
                "format": fmt,
                "max_chars": max_chars,
                "chars": 0,
                "delivery": "empty",
            },
        )
        _render_library_context(outcome)
        return

    # Structured output defaults to a file instead of dumping large markdown.
    if fmt == "structured" and not output:
        output = f"{topic}-context.md"

    if output:
        try:
            with open(output, "w", encoding="utf-8") as handle:
                handle.write(context)
        except Exception as error:
            data = {
                "topic": topic,
                "rows": [{"content": context}],
                "output": output,
                "summary": {
                    "format": fmt,
                    "max_chars": max_chars,
                    "chars": len(context),
                    "delivery": "failed",
                },
            }
            outcome = CommandResult.failed(
                "library-context",
                data,
                ErrorDetail.from_exception(
                    error,
                    stage="write-output",
                    details={"output": output},
                ),
            )
            log_result(
                "library_context",
                outcome,
                topic=topic,
                data={
                    "format": fmt,
                    "max_chars": max_chars,
                    "chars": len(context),
                    "output": output,
                    "delivery": "failed",
                },
            )
            _render_library_context(outcome)
            raise click.exceptions.Exit(1)
        delivery = "saved"
    else:
        delivery = "rendered"

    data = {
        "topic": topic,
        "rows": [{"content": context}],
        "summary": {
            "format": fmt,
            "max_chars": max_chars,
            "chars": len(context),
            "delivery": delivery,
        },
    }
    if output:
        data["output"] = output
    outcome = CommandResult.completed("library-context", data)
    log_result(
        "library_context",
        outcome,
        topic=topic,
        data={
            "format": fmt,
            "max_chars": max_chars,
            "chars": len(context),
            "output": output,
            "delivery": delivery,
        },
    )
    _render_library_context(outcome)


def _build_structured_context(lib, topic: str, max_chars=None) -> str:
    """Build markdown-formatted context with full metadata."""
    transcripts = lib.list_transcripts(topic)
    if not transcripts:
        return ""

    parts = [f"# Topic: {topic}\n"]
    total_chars = len(parts[0])

    for idx, t in enumerate(transcripts, 1):
        data = lib.get(t["video_id"], topic)
        if not data:
            continue

        metadata = data.get("metadata", {})
        duration = metadata.get("duration_seconds", 0)
        duration_str = _format_duration(int(duration)) if duration else "Unknown"
        views = metadata.get("views", 0)
        views_str = f"{views:,}" if views else "N/A"

        header = f"\n---\n\n## {idx}. {metadata.get('title', t['video_id'])}\n"
        header += f"- **Channel:** {metadata.get('channel', 'Unknown')}\n"
        header += f"- **Video ID:** {t['video_id']}\n"
        header += f"- **Duration:** {duration_str} | **Views:** {views_str}\n"
        header += f"- **Language:** {metadata.get('language', 'N/A')}"
        header += f" (auto-generated)" if metadata.get('is_generated') else ""
        header += f"\n- **Saved:** {t.get('saved_at', 'Unknown')[:10]}\n\n"

        transcript = data.get("transcript", "")

        content = header + transcript + "\n"

        if max_chars and total_chars + len(content) > max_chars:
            remaining = max_chars - total_chars
            if remaining > len(header) + 500:
                content = content[:remaining] + "\n\n[TRUNCATED]\n"
                parts.append(content)
            break

        parts.append(content)
        total_chars += len(content)

    return "\n".join(parts)


def _render_library_stats(
    outcome: CommandResult[LibraryResultData],
) -> None:
    """Render library statistics from a typed result."""
    _render_library_failure(outcome, "Library statistics failed")
    summary = outcome.data.get("summary") or {}
    rows = outcome.data.get("rows") or []
    console.print(
        Panel(
            f"[bold]Topics:[/bold] {summary.get('total_topics', 0)}\n"
            f"[bold]Transcripts:[/bold] "
            f"{summary.get('total_transcripts', 0)}\n"
            f"[bold]Total Size:[/bold] "
            f"{summary.get('total_size_mb', 0)} MB",
            title="Library Statistics",
        )
    )
    if rows:
        console.print("\n[bold]By Topic:[/bold]")
        for row in rows:
            console.print(
                f"  {row['topic']}: {row['count']} transcripts"
            )


@library.command("stats")
def library_stats():
    """Show library statistics."""
    from ..library import get_library
    from ..ledger import log_result
    lib = get_library()

    try:
        stats = lib.stats()
    except Exception as error:
        data: LibraryResultData = {
            "rows": [],
            "summary": {
                "total_topics": 0,
                "total_transcripts": 0,
                "total_size_bytes": 0,
                "total_size_mb": 0,
            },
        }
        outcome = CommandResult.failed(
            "library-stats",
            data,
            ErrorDetail.from_exception(error, stage="statistics"),
        )
        log_result(
            "library_stats",
            outcome,
            data={
                "topics": 0,
                "transcripts": 0,
                "size_bytes": 0,
            },
        )
        _render_library_stats(outcome)
        raise click.exceptions.Exit(1)

    rows = list(stats.get("topics") or [])
    data = {
        "rows": rows,
        "summary": {
            "total_topics": int(stats.get("total_topics", 0)),
            "total_transcripts": int(
                stats.get("total_transcripts", 0)
            ),
            "total_size_bytes": int(stats.get("total_size_bytes", 0)),
            "total_size_mb": stats.get("total_size_mb", 0),
        },
    }
    outcome = CommandResult(
        command="library-stats",
        status=(
            ResultStatus.COMPLETED
            if data["summary"]["total_topics"]
            else ResultStatus.EMPTY
        ),
        data=data,
    )
    log_result(
        "library_stats",
        outcome,
        data={
            "topics": data["summary"]["total_topics"],
            "transcripts": data["summary"]["total_transcripts"],
            "size_bytes": data["summary"]["total_size_bytes"],
        },
    )
    _render_library_stats(outcome)


def _render_library_delete(
    outcome: CommandResult[LibraryResultData],
) -> None:
    """Render a transcript/topic deletion outcome."""
    _render_library_failure(outcome, "Library delete failed")
    summary = outcome.data.get("summary") or {}
    target = str(summary.get("target") or "")
    scope = summary.get("scope")
    deleted = summary.get("deleted")

    if scope == "topic":
        count = int(deleted or 0)
        if count:
            console.print(
                f"[green]✓ Deleted topic '{target}' "
                f"({count} transcripts)[/green]"
            )
        else:
            console.print(
                f"[yellow]Topic not found or empty: {target}[/yellow]"
            )
        return

    if bool(deleted):
        topic = outcome.data.get("topic")
        scope_label = f"from {topic}" if topic else "from all topics"
        console.print(
            f"[green]✓ Deleted transcript {target} "
            f"{scope_label}[/green]"
        )
    else:
        console.print(
            f"[yellow]Transcript not found: {target}[/yellow]"
        )


@library.command("delete")
@click.argument("target")
@click.option("--topic", "-t", default=None, help="Delete from specific topic only")
@click.option("--all", "delete_all", is_flag=True, help="Delete entire topic (use with topic as TARGET)")
@click.confirmation_option(prompt="Are you sure you want to delete?")
def library_delete(target: str, topic: str, delete_all: bool):
    """Delete a transcript or entire topic.

    TARGET is either a video ID or topic name (with --all).

    Examples:

    \b
        filmot library delete VIDEO_ID              # Delete from all topics
        filmot library delete VIDEO_ID -t TOPIC    # Delete from specific topic
        filmot library delete TOPIC --all          # Delete entire topic
    """
    from ..library import get_library
    from ..ledger import log_result
    lib = get_library()

    if delete_all:
        try:
            deleted = lib.delete_topic(target)
        except Exception as error:
            data: LibraryResultData = {
                "topic": target,
                "rows": [],
                "summary": {
                    "target": target,
                    "scope": "topic",
                    "deleted": 0,
                },
            }
            outcome = CommandResult.failed(
                "library-delete",
                data,
                ErrorDetail.from_exception(
                    error,
                    stage="delete-topic",
                    details={"target": target},
                ),
            )
            log_result(
                "library_delete",
                outcome,
                topic=target,
                data={
                    "target": target,
                    "scope": "topic",
                    "deleted": 0,
                },
            )
            _render_library_delete(outcome)
            raise click.exceptions.Exit(1)
        result_topic = target
        scope = "topic"
    else:
        try:
            deleted = lib.delete(target, topic=topic)
        except Exception as error:
            data = {
                "topic": topic,
                "rows": [],
                "summary": {
                    "target": target,
                    "scope": "transcript",
                    "deleted": False,
                },
            }
            outcome = CommandResult.failed(
                "library-delete",
                data,
                ErrorDetail.from_exception(
                    error,
                    stage="delete-transcript",
                    details={"target": target, "topic": topic},
                ),
            )
            log_result(
                "library_delete",
                outcome,
                topic=topic,
                data={
                    "target": target,
                    "scope": "transcript",
                    "deleted": False,
                },
            )
            _render_library_delete(outcome)
            raise click.exceptions.Exit(1)
        result_topic = topic
        scope = "transcript"

    data = {
        "topic": result_topic,
        "rows": [],
        "summary": {
            "target": target,
            "scope": scope,
            "deleted": deleted,
        },
    }
    outcome = CommandResult(
        command="library-delete",
        status=(
            ResultStatus.COMPLETED
            if bool(deleted)
            else ResultStatus.EMPTY
        ),
        data=data,
    )
    log_result(
        "library_delete",
        outcome,
        topic=result_topic,
        data={
            "target": target,
            "scope": scope,
            "deleted": deleted,
        },
    )
    _render_library_delete(outcome)


def _render_library_migrate_topic(
    outcome: CommandResult[LibraryResultData],
) -> None:
    """Render the final state of an explicit legacy-topic migration."""
    _render_library_failure(outcome, "Topic migration failed")
    data = outcome.data
    summary = data.get("summary") or {}
    requested_topic = str(summary.get("requested_topic") or "")
    canonical_slug = str(
        summary.get("canonical_slug") or data.get("topic") or ""
    )
    legacy_slug = str(summary.get("legacy_slug") or "")
    reason = summary.get("reason")

    if reason == "already-canonical":
        console.print(
            f"[dim]No migration is needed: '{requested_topic}' already uses "
            f"'{canonical_slug}'.[/dim]"
        )
        return
    if reason == "no-source-files":
        console.print(
            f"[dim]No legacy transcript files found at "
            f"{summary.get('source')}.[/dim]"
        )
        return

    migrated = int(summary.get("migrated", 0))
    remaining = int(summary.get("remaining", 0))
    console.print(
        f"[green]Migrated {migrated} transcript file(s) to "
        f"'{canonical_slug}'.[/green]"
    )
    if remaining:
        console.print(
            f"[yellow]{remaining} file(s) remain in '{legacy_slug}', usually "
            "because the destination already exists or a source file is invalid."
            "[/yellow]"
        )


@library.command("migrate-topic")
@click.argument("topic")
@click.option(
    "--yes",
    is_flag=True,
    help="Confirm that the entire ambiguous legacy directory belongs to TOPIC",
)
def library_migrate_topic(topic: str, yes: bool):
    """Explicitly assign a pre-Unicode topic directory to TOPIC.

    Old Filmot versions discarded Unicode, so unrelated names could share a
    directory such as ``uncategorized`` or ``ai``. This command moves the
    entire derived legacy directory; it cannot infer or partition ownership.
    Review both paths and confirm only when every source file belongs to TOPIC.
    """
    from ..ledger import log_result
    from ..library import _legacy_normalize_topic, get_library

    try:
        lib = get_library()
        canonical_slug = lib._normalize_topic(topic)
        legacy_slug = _legacy_normalize_topic(topic)
    except Exception as error:
        data: LibraryResultData = {
            "topic": topic,
            "rows": [],
            "summary": {
                "requested_topic": topic,
                "canonical_slug": topic,
                "legacy_slug": "",
                "source_files": 0,
                "migrated": 0,
                "remaining": 0,
            },
        }
        outcome = CommandResult.failed(
            "library-migrate-topic",
            data,
            ErrorDetail.from_exception(
                error,
                stage="prepare-migration",
                details={"topic": topic},
            ),
        )
        log_result(
            "library_migrate_topic",
            outcome,
            topic=topic,
            data={
                "requested_topic": topic,
                "canonical_slug": topic,
                "legacy_slug": "",
                "source_files": 0,
                "migrated": 0,
                "remaining": 0,
            },
        )
        _render_library_migrate_topic(outcome)
        raise click.exceptions.Exit(1)

    if canonical_slug == legacy_slug:
        data = {
            "topic": canonical_slug,
            "rows": [],
            "summary": {
                "requested_topic": topic,
                "canonical_slug": canonical_slug,
                "legacy_slug": legacy_slug,
                "source_files": 0,
                "migrated": 0,
                "remaining": 0,
                "reason": "already-canonical",
            },
        }
        outcome = CommandResult(
            command="library-migrate-topic",
            status=ResultStatus.SKIPPED,
            data=data,
        )
        log_result(
            "library_migrate_topic",
            outcome,
            topic=canonical_slug,
            data=dict(data["summary"]),
        )
        _render_library_migrate_topic(outcome)
        raise click.exceptions.Exit(1)

    source = lib.transcripts_dir / legacy_slug
    destination = lib.transcripts_dir / canonical_slug
    source_files = sorted(source.glob("*.json")) if source.exists() else []
    if not source_files:
        data = {
            "topic": canonical_slug,
            "rows": [],
            "summary": {
                "requested_topic": topic,
                "canonical_slug": canonical_slug,
                "legacy_slug": legacy_slug,
                "source": str(source),
                "source_files": 0,
                "migrated": 0,
                "remaining": 0,
                "reason": "no-source-files",
            },
        }
        outcome = CommandResult(
            command="library-migrate-topic",
            status=ResultStatus.EMPTY,
            data=data,
        )
        log_result(
            "library_migrate_topic",
            outcome,
            topic=canonical_slug,
            data={
                key: value
                for key, value in data["summary"].items()
                if key != "source"
            },
        )
        _render_library_migrate_topic(outcome)
        return

    console.print(
        Panel(
            f"[bold yellow]Legacy source:[/bold yellow] {source}\n"
            f"[bold green]Destination:[/bold green] {destination}\n"
            f"[bold]Transcript files:[/bold] {len(source_files)}\n\n"
            "Old slugs can represent more than one original topic. This "
            "operation assigns the entire source directory to the destination; "
            "existing destination files are never overwritten.",
            title="Explicit legacy-topic migration",
            border_style="yellow",
        )
    )
    console.print(
        "[yellow]Warning: this assigns the entire source directory.[/yellow]"
    )
    if not yes:
        click.confirm(
            f"Assign all {len(source_files)} legacy files to '{canonical_slug}'?",
            abort=True,
        )

    try:
        migrated = lib.migrate_legacy_topic(topic)
    except Exception as error:
        data = {
            "topic": canonical_slug,
            "rows": [],
            "summary": {
                "requested_topic": topic,
                "legacy_slug": legacy_slug,
                "canonical_slug": canonical_slug,
                "source_files": len(source_files),
                "migrated": 0,
                "remaining": len(source_files),
            },
        }
        outcome = CommandResult.failed(
            "library-migrate-topic",
            data,
            ErrorDetail.from_exception(
                error,
                stage="move-files",
                details={
                    "source_files": len(source_files),
                    "legacy_slug": legacy_slug,
                    "canonical_slug": canonical_slug,
                },
            ),
        )
        log_result(
            "library_migrate_topic",
            outcome,
            topic=canonical_slug,
            data=dict(data["summary"]),
        )
        _render_library_migrate_topic(outcome)
        return

    remaining = (
        len(list(source.glob("*.json")))
        if source.exists()
        else 0
    )
    data = {
        "topic": canonical_slug,
        "rows": [],
        "summary": {
            "requested_topic": topic,
            "legacy_slug": legacy_slug,
            "canonical_slug": canonical_slug,
            "source_files": len(source_files),
            "migrated": migrated,
            "remaining": remaining,
        },
    }
    errors = (
        [
            ErrorDetail(
                type="MigrationIncomplete",
                message=(
                    f"{remaining} legacy transcript file(s) remain "
                    f"in '{legacy_slug}'."
                ),
                stage="move-files",
                details={
                    "remaining": remaining,
                    "migrated": migrated,
                },
            )
        ]
        if remaining
        else []
    )
    outcome = CommandResult(
        command="library-migrate-topic",
        status=(
            ResultStatus.PARTIAL
            if remaining
            else ResultStatus.COMPLETED
        ),
        data=data,
        errors=errors,
    )
    log_result(
        "library_migrate_topic",
        outcome,
        topic=canonical_slug,
        data=dict(data["summary"]),
    )
    _render_library_migrate_topic(outcome)
    if remaining:
        raise click.exceptions.Exit(2)


def _render_library_compare(
    outcome: CommandResult[LibraryResultData],
) -> None:
    """Render a concordance from its typed, precomputed source rows."""
    _render_library_failure(outcome, "Library comparison failed")
    data = outcome.data
    query = str(data.get("query") or "")
    rows = data.get("rows") or []
    summary = data.get("summary") or {}

    if not rows:
        console.print(
            f"[yellow]No sources mention '{query}'[/yellow]"
        )
        return

    if summary.get("fallback_used"):
        console.print(
            "[dim]No exact word matches. Showing substring matches "
            "(plurals/inflections):[/dim]"
        )
    console.print(
        Panel(
            f"[bold]'{query}' mentioned {summary.get('matches', 0)} times "
            f"across {len(rows)} sources[/bold]",
            title="Cross-Source Concordance",
        )
    )

    for row in rows:
        match_count = int(row.get("match_count", 0))
        mentions_label = "mention" if match_count == 1 else "mentions"
        density = row.get("density")
        density_str = (
            f" | [bold]{float(density):.1f}/min[/bold]"
            if density is not None
            else ""
        )
        console.print(
            f"\n[bold cyan]{row.get('title', 'Unknown')}[/bold cyan]"
        )
        console.print(
            f"  [dim]Channel:[/dim] {row.get('channel', 'Unknown')} | "
            f"[dim]Topic:[/dim] {row.get('topic', '')} | "
            f"[bold]{match_count} {mentions_label}{density_str}[/bold]"
        )
        console.print(
            f"  [dim]Video:[/dim] "
            f"https://youtube.com/watch?v={row.get('video_id', '')}"
        )
        for match in row.get("excerpts", [])[:3]:
            highlighted = str(match)
            for variant in (
                query,
                query.lower(),
                query.upper(),
                query.capitalize(),
            ):
                highlighted = highlighted.replace(
                    variant,
                    f"[bold yellow]{variant}[/bold yellow]",
                )
            console.print(f"  [dim]{highlighted}[/dim]")

    console.print(
        f"\n[dim]Sources sorted by {summary.get('sort_label')} "
        "(highest lexical occurrence first)[/dim]"
    )


@library.command("compare")
@click.argument("query")
@click.option("--topic", "-t", default=None, help="Limit to specific topic")
@click.option("--context", "-c", "context_chars", default=300, type=click.IntRange(0), help="Characters of context around matches (default: 300)")
@click.option("--sort", "sort_by", default="mentions", type=click.Choice(["mentions", "density"]), help="Sort by mention count (default) or density (mentions/min)")
def library_compare(query: str, topic: str, context_chars: int, sort_by: str):
    """Build a lexical concordance across sources for a term or phrase.

    This counts text matches and shows excerpts. It does not determine source
    independence, stance, agreement, contradiction, credibility, or truth.

    Examples:

    \b
        filmot library compare "dark oxygen" --topic deep-sea-mining
        filmot library compare "moratorium" --context 200
    """
    from ..library import get_library
    from ..ledger import log_result
    lib = get_library()

    used_substring = False
    stage = "search"
    try:
        with console.status(f"Comparing '{query}' across sources..."):
            results = lib.search(query, topic=topic)

        # Auto-fallback catches plurals/inflections.
        if not results:
            results = lib.search(query, topic=topic, substring=True)
            if results:
                used_substring = True

        stage = "build-concordance"
        import re as _re

        pattern = (
            _re.compile(_re.escape(query.lower()))
            if used_substring
            else _re.compile(
                r"(?<!\w)"
                + _re.escape(query.lower())
                + r"(?!\w)"
            )
        )
        rows = []
        for result in results:
            transcript_data = lib.get(
                result["video_id"],
                result["topic"],
            )
            duration = (
                transcript_data.get("metadata", {}).get(
                    "duration_seconds",
                    0,
                )
                if transcript_data
                else 0
            )
            density = (
                int(result["match_count"]) / (duration / 60)
                if duration and duration > 0
                else None
            )
            excerpts = (
                lib._find_matches(
                    transcript_data.get("transcript", ""),
                    query.lower(),
                    context_chars=context_chars,
                    pattern=pattern,
                    min_gap=context_chars,
                )
                if transcript_data
                else []
            )
            rows.append(
                {
                    "video_id": result["video_id"],
                    "topic": result["topic"],
                    "title": result.get("title", "Unknown"),
                    "channel": result.get("channel", "Unknown"),
                    "match_count": int(result["match_count"]),
                    "density": density,
                    "excerpts": excerpts[:3],
                }
            )
        if sort_by == "density":
            rows.sort(
                key=lambda row: float(row.get("density") or 0),
                reverse=True,
            )
    except Exception as error:
        data: LibraryResultData = {
            "topic": topic,
            "query": query,
            "rows": [],
            "summary": {
                "sort": sort_by,
                "sort_label": (
                    "density (mentions/min)"
                    if sort_by == "density"
                    else "mention count"
                ),
                "context": context_chars,
                "match_mode": (
                    "substring"
                    if used_substring
                    else "word_boundary"
                ),
                "fallback_used": used_substring,
                "sources": 0,
                "matches": 0,
            },
        }
        outcome = CommandResult.failed(
            "library-compare",
            data,
            ErrorDetail.from_exception(
                error,
                stage=stage,
                details={
                    "query": query,
                    "topic": topic,
                    "sort": sort_by,
                },
            ),
        )
        log_result(
            "library_compare",
            outcome,
            topic=topic,
            data={
                "query": query,
                "sort": sort_by,
                "context": context_chars,
                "match_mode": (
                    "substring"
                    if used_substring
                    else "word_boundary"
                ),
                "sources": 0,
                "matches": 0,
            },
        )
        _render_library_compare(outcome)
        raise click.exceptions.Exit(1)

    total_mentions = sum(int(row["match_count"]) for row in rows)
    sort_label = (
        "density (mentions/min)"
        if sort_by == "density"
        else "mention count"
    )
    data = {
        "topic": topic,
        "query": query,
        "rows": rows,
        "summary": {
            "sort": sort_by,
            "sort_label": sort_label,
            "context": context_chars,
            "match_mode": (
                "substring"
                if used_substring
                else "word_boundary"
            ),
            "fallback_used": used_substring,
            "sources": len(rows),
            "matches": total_mentions,
        },
    }
    outcome = CommandResult(
        command="library-compare",
        status=(
            ResultStatus.COMPLETED
            if rows
            else ResultStatus.EMPTY
        ),
        data=data,
        warnings=(
            ["Used substring fallback after word-boundary search was empty."]
            if used_substring
            else []
        ),
    )
    log_result(
        "library_compare",
        outcome,
        topic=topic,
        data={
            "query": query,
            "sort": sort_by,
            "context": context_chars,
            "match_mode": data["summary"]["match_mode"],
            "sources": len(rows),
            "matches": total_mentions,
        },
    )
    _render_library_compare(outcome)



def _render_sessions(
    outcome: CommandResult[SessionResultData],
) -> None:
    """Render a session inventory or replay from one typed result."""
    data = outcome.data
    name = data.get("name")
    if not name:
        rows = data.get("rows") or []
        if not rows:
            console.print(
                "[dim]No sessions logged yet. Run a search or research "
                "command first.[/dim]"
            )
            return
        table = Table(title="Research Sessions")
        table.add_column("Session", style="cyan")
        table.add_column("Events", justify="right")
        table.add_column("Last activity", style="dim")
        for row in rows:
            table.add_row(
                str(row["name"]),
                str(row["events"]),
                str(row["last_ts"]).replace("T", " "),
            )
        console.print(table)
        console.print(
            "\n[dim]Replay one with: filmot sessions <name>[/dim]"
        )
        return

    events = data.get("events") or []
    if not events:
        console.print(f"[yellow]No session found for '{name}'.[/yellow]")
        return

    console.print(f"[bold]Session: {name}[/bold] ({len(events)} events)\n")
    for event in events:
        ts = str(event.get("ts", "")).replace("T", " ")
        kind = event.get("kind", "?")
        event_data = (
            event.get("data")
            if isinstance(event.get("data"), dict)
            else {}
        )
        if kind == "search":
            detail = (
                f"\"{event_data.get('query','')}\" → "
                f"{event_data.get('results',0)}/"
                f"{event_data.get('total','?')} results"
            )
            if event_data.get("start_date") or event_data.get("end_date"):
                detail += (
                    f" [{event_data.get('start_date','')}.."
                    f"{event_data.get('end_date','')}]"
                )
        elif kind == "research":
            detail = (
                f"\"{event_data.get('query','')}\" → saved "
                f"{event_data.get('saved',0)}, probe "
                f"{event_data.get('probe',0)} "
                f"(scout {event_data.get('scout',0)})"
            )
        elif kind == "channel-search":
            detail = (
                f"{event_data.get('slug','')}: "
                f"\"{event_data.get('query','')}\" → "
                f"{event_data.get('videos',0)} vids / "
                f"{event_data.get('hits',0)} hits"
            )
        elif kind == "transcript":
            detail = str(event_data.get("video_id", "")) + (
                f" grep \"{event_data.get('grep')}\""
                if event_data.get("grep")
                else ""
            )
        else:
            detail = json_mod.dumps(event_data, ensure_ascii=False)
        console.print(
            f"  [dim]{ts}[/dim] [cyan]{kind}[/cyan] "
            f"[dim]{event.get('status', '')}[/dim]  {detail}"
        )


@click.command("sessions")
@click.argument("name", required=False, default=None)
@click.option("--raw", is_flag=True, help="Output one versioned JSON result")
def sessions(name: str, raw: bool):
    """Show the research session ledger so you can resume prior investigations.

    Every search, research run, and channel-search is logged to
    .filmot_data/sessions/. Without NAME, lists all sessions. With a NAME
    (topic slug or YYYY-MM-DD date), replays that session's events.

    \b
    Examples:
        filmot sessions                       # list all sessions
        filmot sessions fable-5-mythos        # replay a topic session
        filmot sessions 2026-06-10            # replay a day's ad-hoc queries
        filmot sessions 2026-06-10 --raw      # versioned JSON for piping
    """
    from ..ledger import list_sessions, read_events

    if not name:
        rows = list_sessions()
        result_data: SessionResultData = {"rows": rows}
    else:
        events = read_events(name)
        result_data = {
            "name": name,
            "events": events,
        }
    outcome = CommandResult(
        command="sessions",
        status=(
            ResultStatus.COMPLETED
            if (result_data.get("rows") or result_data.get("events"))
            else ResultStatus.EMPTY
        ),
        data=result_data,
    )
    if raw:
        click.echo(
            json_mod.dumps(
                outcome.to_raw_dict(),
                ensure_ascii=False,
            )
        )
        return
    _render_sessions(outcome)


# ========== DOWNLOAD (STDIN) ==========
