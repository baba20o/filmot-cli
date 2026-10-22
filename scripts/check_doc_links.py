#!/usr/bin/env python3
"""Check that relative links in the repository's Markdown files resolve.

Scans tracked and untracked-but-not-ignored ``*.md`` files for inline links,
images, and reference definitions outside fenced code and code spans. External
URLs and pure ``#fragment`` links are skipped; fragments are not validated.
A missing target under a Git-ignored path (local research captures, for
example) is reported as local-only instead of failing, so the check behaves
the same in a clean clone. Targets outside the repository are reported, not
checked. Indented code blocks and raw HTML links are not interpreted.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import subprocess
from pathlib import Path
from typing import Iterator, List, Optional, Tuple
from urllib.parse import unquote


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_REFERENCE = re.compile(r"^ {0,3}\[([^\]]+)\]:[ \t]*(.*)$")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_BLANK_LINE = re.compile(r"\n[ \t]*\n")


def _repo_root() -> Path:
    output = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return Path(output)


def _markdown_files(root: Path) -> List[str]:
    output = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.md"],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout
    return sorted({name for name in output.split("\0") if name})


def _mask_fences(lines: List[str]) -> List[str]:
    """Blank fenced code lines; a fence closes only with its own character and length."""
    masked = []
    fence: Optional[Tuple[str, int]] = None
    for line in lines:
        match = _FENCE.match(line)
        if fence is None:
            if match and not (match.group(1)[0] == "`" and "`" in match.group(2)):
                fence = (match.group(1)[0], len(match.group(1)))
                masked.append("")
            else:
                masked.append(line)
        else:
            if (match and match.group(1)[0] == fence[0]
                    and len(match.group(1)) >= fence[1] and not match.group(2).strip()):
                fence = None
            masked.append("")
    return masked


def _mask_code_spans(text: str) -> str:
    """Blank code spans, which may cross line breaks but not blank lines."""
    out = []
    i = 0
    while i < len(text):
        if text[i] != "`" or (i and text[i - 1] == "\\"):
            out.append(text[i])
            i += 1
            continue
        run_end = i
        while run_end < len(text) and text[run_end] == "`":
            run_end += 1
        width = run_end - i
        closing = re.compile(r"(?<!`)`{%d}(?!`)" % width).search(text, run_end)
        if closing and not _BLANK_LINE.search(text, run_end, closing.start()):
            span = text[i:closing.end()]
            out.append("".join("\n" if char == "\n" else " " for char in span))
            i = closing.end()
        else:
            out.append(text[i:run_end])
            i = run_end
    return "".join(out)


def _destination(text: str, start: int) -> Optional[str]:
    """Parse a link destination at ``start``, allowing <...> and balanced parentheses."""
    while start < len(text) and text[start] in " \t":
        start += 1
    if text.startswith("<", start):
        end = text.find(">", start + 1)
        if end == -1 or "\n" in text[start:end]:
            return None
        return text[start + 1:end]
    depth = 0
    end = start
    while end < len(text):
        char = text[end]
        if char == "\\" and end + 1 < len(text):
            end += 2
            continue
        if char.isspace() or (char == ")" and depth == 0):
            break
        depth += {"(": 1, ")": -1}.get(char, 0)
        end += 1
    return text[start:end] or None


def _link_targets(text: str) -> Iterator[Tuple[int, str]]:
    """Yield (line number, raw target) for links outside fenced and inline code."""
    lines = _mask_fences(text.splitlines())
    prose = _mask_code_spans("\n".join(lines))
    position = prose.find("](")
    while position != -1:
        target = _destination(prose, position + 2)
        if target:
            yield prose.count("\n", 0, position) + 1, target
        position = prose.find("](", position + 2)
    lines = prose.split("\n")
    for index, line in enumerate(lines):
        match = _REFERENCE.match(line)
        if not match or match.group(1).startswith("^"):
            continue
        rest = match.group(2)
        if not rest.strip() and index + 1 < len(lines):
            rest = lines[index + 1]
        target = _destination(rest, 0)
        if target:
            yield index + 1, target


def _local_path(source: str, target: str) -> Optional[str]:
    """Return the repository-relative path a link points to, or None to skip it."""
    if not target or target.startswith(("#", "//")) or _SCHEME.match(target):
        return None
    path = unquote(re.split(r"[#?]", target, maxsplit=1)[0])
    if not path:
        return None
    if path.startswith("/"):
        return posixpath.normpath(path.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(source), path))


def _is_ignored(root: Path, path: str) -> bool:
    return subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", "--", path],
        cwd=root, capture_output=True,
    ).returncode == 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--quiet", action="store_true",
        help="Print only broken links and the summary line",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = _repo_root()
    files = _markdown_files(root)
    checked = 0
    broken: List[str] = []
    notes: List[str] = []
    for source in files:
        text = (root / source).read_text(encoding="utf-8")
        for number, target in _link_targets(text):
            path = _local_path(source, target)
            if path is None:
                continue
            entry = "{}:{}: {}".format(source, number, target)
            if path == ".." or path.startswith("../"):
                notes.append("outside repository (not checked): " + entry)
                continue
            checked += 1
            if (root / path).exists():
                continue
            if _is_ignored(root, path):
                notes.append("local-only (git-ignored target): " + entry)
            else:
                broken.append(entry)
    if not args.quiet:
        for note in notes:
            print(note)
    for entry in broken:
        print("BROKEN: " + entry)
    print("{} markdown files, {} relative links checked, {} broken, {} notes".format(
        len(files), checked, len(broken), len(notes),
    ))
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
