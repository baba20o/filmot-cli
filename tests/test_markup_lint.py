"""No provider or user text reaches Rich markup unescaped (guards audit F05).

Rich reads "[...]" in a printed string as a style tag, so a caption's
"[music]" vanishes and a title's "[/bold]" raises MarkupError. Text from
Filmot, YouTube, transcripts, or the user must pass through
rich.markup.escape() or be passed as a rich.text.Text object.

This test reads filmot's source. It flags a value that reaches a markup sink
(console.print, Panel, Table titles and cells, status lines, progress
descriptions, and lists of diagnostics printed later) without escape() when
the value is provider-shaped: its name, attribute, or dictionary key is one of
PROVIDER_FIELDS, alone or as the last part of a snake_case name (title_text,
display_query). It is a heuristic. It cannot follow every data flow, and text
held under other names (or appended with +=) escapes it, so it complements
review rather than replacing it.

To fix a failure, wrap the value in escape(...), pass a Text object, or print
with markup=False. A value inside a ``[link=...]`` tag is always flagged,
because escaping cannot protect it there: build the link with
Text and Style(link=...) instead. Name a variable that holds markup you built or escaped
``*_markup``. If a value is internal (an enum, a path we built), add it to
ALLOWED with the reason.
"""

import ast
import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "filmot"

PROVIDER_FIELDS = {
    "title", "channel", "channelname", "channelcountryname", "countryname",
    "category", "description", "text", "excerpt", "context", "snippet",
    "ctx_before", "ctx_after", "token", "query", "term", "name", "handle",
    "note", "statement", "message", "error", "detail", "topic", "language",
    "lang", "save_to", "error_display", "country", "countries", "country_code",
    # Usually a validated 11-character ID, but some paths print user input
    # that the extractor returned unchanged.
    "video_id", "channel_id", "playlist_id",
}
# Helpers whose result is error or provider text, whatever the argument is
# called (an exception named ``e`` included).
PROVIDER_CALLS = {
    "_whole_word_summary", "whole_word_summary", "_transcript_failure_detail",
    "redact_sensitive_text", "redact_diagnostic", "_redact_diagnostic",
}

# (path relative to filmot/, enclosing function, expression) -> why the value
# is not provider text.
ALLOWED = {
    ("cli_support.py", "diagnostic", "message"):
        "forwards markup its callers built; callers of diagnostic are checked as sinks",
    ("cli_support.py", "status_context", "message"):
        "forwards markup its callers built; callers of status_context and its aliases "
        "are checked as sinks",
    ("commands/channel.py", "_video_table", "title"):
        "the table heading; both callers pass fixed text",
}

# Calls whose string arguments Rich parses as markup, and the keyword
# arguments that are markup too.
SINK_METHODS = {"print", "log", "rule", "status", "add_row", "add_task"}
SINK_FUNCTIONS = {
    "rprint", "Panel", "diagnostic", "_diagnostic",
    # status_context and the names commands import it under.
    "status_context", "_search_status", "_status",
}
MARKUP_KEYWORDS = {"title", "subtitle", "caption", "description"}
# A sink's main input, when it is passed by keyword instead of position.
INPUT_KEYWORDS = {
    "Panel": {"renderable"},
    "from_markup": {"text"},
    "status": {"status"},
    "diagnostic": {"message"},
    "_diagnostic": {"message"},
    "status_context": {"message"},
    "_search_status": {"message"},
    "_status": {"message"},
}
# Lists of markup lines that are printed later; appending to one is a sink.
MARKUP_LIST_SUFFIX = "diagnostics"
# Renderables Rich draws without parsing markup, when passed as objects.
RENDERABLES = {"Text", "Table", "Panel", "Markdown", "Group", "Columns", "Tree"}


def _provider_shaped_name(name: str) -> bool:
    lowered = name.lower()
    return lowered in PROVIDER_FIELDS or lowered.rsplit("_", 1)[-1] in PROVIDER_FIELDS


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_renderable(call: ast.Call) -> bool:
    receiver = call.func.value if isinstance(call.func, ast.Attribute) else None
    return _call_name(call) in RENDERABLES or (
        isinstance(receiver, ast.Name) and receiver.id == "Text"
    )


def _unsafe(expr: ast.AST, stringified: bool = True) -> bool:
    """Whether ``expr`` yields provider-shaped text that is not escaped.

    ``stringified`` is False only for a value handed to Rich as an object; a
    renderable such as Text is safe there, but its str() is the raw text.
    """
    if isinstance(expr, ast.Call):
        name = _call_name(expr)
        if name == "escape":
            return False
        if name in PROVIDER_CALLS:
            return True
        if _is_renderable(expr):
            if not stringified:
                return False
            return any(_unsafe(value) for value in _format_values(expr))
        if name == "get" and expr.args and isinstance(expr.args[0], ast.Constant):
            key = expr.args[0].value
            return isinstance(key, str) and _provider_shaped_name(key)
        if name == "str" and expr.args:
            return _unsafe(expr.args[0])
        if name == "join" and expr.args:
            return _unsafe(expr.args[0])
        if name == "format" and isinstance(expr.func, ast.Attribute):
            # The template itself can be provider text, as well as its values.
            return _unsafe(expr.func.value) or any(_unsafe(value) for value in _format_values(expr))
        if isinstance(expr.func, ast.Attribute):
            # Methods such as .strip() or .replace() keep their receiver's text.
            return _unsafe(expr.func.value)
        return False
    if isinstance(expr, ast.Subscript):
        key = expr.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return _provider_shaped_name(key.value)
        return _unsafe(expr.value)  # a slice keeps the value's text
    if isinstance(expr, ast.Name):
        return _provider_shaped_name(expr.id)
    if isinstance(expr, ast.Attribute):
        return _provider_shaped_name(expr.attr)
    if isinstance(expr, ast.BoolOp):
        return any(_unsafe(value, stringified) for value in expr.values)
    if isinstance(expr, ast.IfExp):
        return _unsafe(expr.body, stringified) or _unsafe(expr.orelse, stringified)
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, (ast.Add, ast.Mod)):
        return _unsafe(expr.left) or _unsafe(expr.right)
    if isinstance(expr, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
        return _unsafe(expr.elt)
    if isinstance(expr, (ast.List, ast.Tuple, ast.Set)):
        return any(_unsafe(element) for element in expr.elts)
    if isinstance(expr, ast.JoinedStr):
        return any(
            _unsafe(value.value) for value in expr.values if isinstance(value, ast.FormattedValue)
        )
    return False


def _format_values(call: ast.Call):
    yield from call.args
    yield from (keyword.value for keyword in call.keywords)


def _markup_values(arg: ast.AST):
    """(value, stringified) pairs for what ends up inside a markup argument."""
    if isinstance(arg, ast.JoinedStr):
        for value in arg.values:
            if isinstance(value, ast.FormattedValue):
                yield value.value, True
    elif (
        isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Attribute)
        and arg.func.attr == "format"
        and isinstance(arg.func.value, ast.Constant)
    ):
        for value in _format_values(arg):
            yield value, True
    elif isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
        for side in (arg.left, arg.right):
            for value, _ in _markup_values(side):
                yield value, True
    elif isinstance(arg, ast.IfExp):
        yield from _markup_values(arg.body)
        yield from _markup_values(arg.orelse)
    elif not isinstance(arg, ast.Constant):
        yield arg, False  # a bare value printed or used as a cell is parsed too


def _is_sink(call: ast.Call) -> bool:
    if isinstance(call.func, ast.Name):
        return call.func.id in SINK_FUNCTIONS
    if isinstance(call.func, ast.Attribute):
        # The builtin print is a Name; Rich consoles reach print as attributes.
        return call.func.attr in SINK_METHODS or call.func.attr == "from_markup"
    return False


def _markup_list_arguments(call: ast.Call):
    """Lines appended to a list of diagnostics, which is printed as markup."""
    if not isinstance(call.func, ast.Attribute) or call.func.attr not in {"append", "insert"}:
        return []
    receiver = call.func.value
    name = receiver.id if isinstance(receiver, ast.Name) else (
        receiver.attr if isinstance(receiver, ast.Attribute) else ""
    )
    if not name.endswith(MARKUP_LIST_SUFFIX):
        return []
    return call.args[-1:]


def _link_targets(arg: ast.AST):
    """Values interpolated into a ``[link=...]`` tag. Escaping cannot make
    those safe, so any value there is flagged: build the link with
    Text and Style(link=...) instead."""
    if isinstance(arg, ast.JoinedStr):
        inside_link = False
        for part in arg.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                start = part.value.rfind("[link=")
                if start != -1:
                    inside_link = "]" not in part.value[start:]
                elif "]" in part.value:
                    inside_link = False
            elif isinstance(part, ast.FormattedValue) and inside_link:
                yield part.value
    elif (
        isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Attribute)
        and arg.func.attr == "format"
        and isinstance(arg.func.value, ast.Constant)
        # A placeholder anywhere inside the tag, prefixed targets included.
        and re.search(r"\[link=[^\]]*\{", str(arg.func.value.value))
    ):
        yield arg


def _call_violations(node: ast.Call):
    """Provider-shaped values that one call passes to Rich as markup."""
    markup_off = any(
        keyword.arg == "markup" and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is False
        for keyword in node.keywords
    )
    arguments = list(_markup_list_arguments(node))
    if _is_sink(node) and not markup_off:
        arguments.extend(node.args)
        inputs = INPUT_KEYWORDS.get(_call_name(node), set())
        arguments.extend(keyword.value for keyword in node.keywords if keyword.arg in inputs)
    if _call_name(node) in {"Panel", "Table", "update", "add_task"} or _is_sink(node):
        arguments.extend(
            keyword.value for keyword in node.keywords if keyword.arg in MARKUP_KEYWORDS
        )
    for argument in arguments:
        for value, stringified in _markup_values(argument):
            if _unsafe(value, stringified):
                yield value


def find_violations(source: str, filename: str):
    """(enclosing function, line, expression) for each provider-shaped value
    reaching markup."""
    found = []

    def record(function: str, value: ast.AST) -> None:
        entry = (function, value.lineno, ast.unparse(value))
        if entry not in found:
            found.append(entry)

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if isinstance(node, ast.Call):
            for value in _call_violations(node):
                record(function, value)
        # A [link=...] string exists only for Rich, so check it wherever it
        # is built, not just where it is printed.
        if isinstance(node, (ast.JoinedStr, ast.Call)):
            for value in _link_targets(node):
                record(function, value)
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(ast.parse(source, filename=filename), "<module>")
    return found


def _package_findings():
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE).as_posix()
        for function, line, expression in find_violations(path.read_text(encoding="utf-8"), relative):
            yield relative, function, line, expression


def test_no_provider_text_reaches_rich_markup_unescaped():
    violations = [
        "filmot/{}:{}: {}".format(relative, line, expression)
        for relative, function, line, expression in _package_findings()
        if (relative, function, expression) not in ALLOWED
    ]
    assert not violations, (
        "Provider or user text reaches Rich markup unescaped; wrap it in "
        "escape(...), pass a Text object, or print with markup=False:\n  "
        + "\n  ".join(violations)
    )


def test_every_allowance_is_still_needed():
    found = {(relative, function, expression) for relative, function, _, expression in _package_findings()}
    assert set(ALLOWED) <= found, "stale ALLOWED entries: {}".format(sorted(set(ALLOWED) - found))


@pytest.mark.parametrize("snippet", [
    "console.print(f\"[bold]{video['title']}[/bold]\")",
    "console.print(f\"[dim]{row.get('channel', 'Unknown')}[/dim]\")",
    "console.print(\"[cyan]{}[/cyan]\".format(error.message))",
    "table.add_row(str(index), title_text[:40])",
    "console.print(Panel(f\"Query: {display_query}\", title=name))",
    "console.status(f\"Searching for '{query}'...\")",
    "progress.update(task, description=f\"[cyan]{v['title'][:50]}...\")",
    "console.print(f\"  {match.get('excerpt') or 'none'}\")",
    # Review of this test found these; each once slipped through.
    "_search_status(f\"Searching '{query}'...\", raw=raw)",
    "deferred_diagnostics.append(f\"[green]Saved to {save_to}[/green]\")",
    "diagnostics.insert(0, \"[yellow]{}[/yellow]\".format(message))",
    "console.print(f\"{Text(title)}\")",
    "console.print(str(Text(title)))",
    "console.print(f\"{'prefix ' + title}\")",
    "console.print(f\"{', '.join([title, 'x'])}\")",
    "console.print(f\"{'{} - {}'.format(title, 1)}\")",
    "console.print(str(Text(text=title)))",
    "console.print(f\"{Text.styled(text=title, style='bold')}\")",
    "console.print(title.format())",
    "console.print(Panel(renderable=title))",
    "console.print(Text.from_markup(text=title))",
    "console.status(status=title)",
    "_search_status(message=f\"Searching '{query}'\", raw=raw)",
    # Review found these in proxy.py: an exception summary and a setting.
    "console.print(f\"[red]Rotation failed: {_whole_word_summary(e)}[/red]\")",
    "console.print(f\"Countries: {', '.join(snap['countries'])}\")",
    "console.print(f\"[bold]Probing against {video_id}[/bold]\")",
    "console.print(f\"[dim]testing ({sess.country_code or 'unknown'})[/dim]\")",
    # A link target is parsed as markup whatever it is called.
    "console.print(f\"   [link={video_url}]{video_url}[/link]\")",
    "console.print(f\"      [[link={line_link}]{line_ts}[/link]]\", highlighted)",
    "console.print(\"   [link={0}]{0}[/link]\".format(url))",
    "locator = \"[[link={url}]{ts}[/link]] \".format(url=match['url'], ts=ts)",
    "console.print(\"[link=https://youtu.be/{}]watch[/link]\".format(vid))",
    "console.print(f\"[link=https://youtu.be/{vid}]watch[/link]\")",
])
def test_the_lint_catches_unescaped_provider_text(snippet):
    assert find_violations(snippet, "snippet.py")


@pytest.mark.parametrize("snippet", [
    "console.print(f\"[bold]{escape(video['title'])}[/bold]\")",
    "console.print(f\"[dim]{escape(str(row.get('channel', 'Unknown')))}[/dim]\")",
    "table.add_row(str(index), Text(title_text))",
    "console.print(Text(title))",
    "console.print(Text(text=title, style='bold'))",
    "console.print(Panel(renderable=Text(title), title='Fixed'))",
    "console.print(Text.assemble(('Fail ', 'red'), row.get('title')))",
    "console.print(transcript['text'], markup=False)",
    "console.print(f\"Found {len(rows)} rows in {elapsed:.1f}s\")",
    "console.print('[green]done[/green]')",
    "console.print(f\"[cyan]{title_markup}[/cyan]\")",
    "deferred_diagnostics.append(f\"[green]Saved to {escape(save_to)}[/green]\")",
    "outcome.warnings.append(message)",
    "console.print(f\"[red]Rotation failed: {escape(_whole_word_summary(e))}[/red]\")",
    "console.print(Text.assemble('   ', (url, Style(link=url))))",
    "console.print(f\"[bold]{count}[/bold] links shown\")",
])
def test_the_lint_accepts_escaped_or_internal_values(snippet):
    assert not find_violations(snippet, "snippet.py")
