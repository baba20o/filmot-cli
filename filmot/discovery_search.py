"""Discovery searches on the direct Filmot API.

``/getmetadatasearch`` finds videos by title and metadata,
``/getadvancedchannelsearch`` filters Filmot's index of notable channels, and
``/getsubtitlesearch`` exports subtitle-search matches as CSV, JSON, JSON
Lines, or ZIP. This module maps CLI options to API parameters, pages through
results, and merges export pages into one file.
"""

from __future__ import annotations

import codecs
import csv
import io
import json
import os
import re
import threading
import uuid
import zipfile
import zlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence


TITLE_SORTS = (
    "id", "viewcount", "likecount", "dislikecount", "uploaddate", "duration",
    "avg_ratio", "videofetcheddate",
)
TITLE_PAGE_LIMIT = 200
TITLE_LAST_PAGE = 500
TITLE_SUBTITLE_PAGE_LIMIT = 10
TITLE_SUBTITLE_LAST_PAGE = 5000
VIDEO_BASE_COLUMNS = ("id", "title", "duration", "uploaddate", "channelid", "channelname")
TITLE_COLUMNS = (
    "category", "channelid", "channelname", "id", "title", "uploaddate", "autosubs",
    "manualsubs", "duration", "viewcount", "likecount", "dislikecount", "isunlisted",
    "islivestream", "ispartialdata", "license", "channelsubcount", "channelcountryname",
    "lastfetchdate", "subtitles", "description",
)
DEFAULT_TITLE_COLUMNS = tuple(column for column in TITLE_COLUMNS if column != "subtitles")

CHANNEL_SORTS = (
    "joindate", "subcount", "totalviewcount", "avgduration", "maxvideocount",
    "countryname", "vidavgviewcount", "vidminuploaddate", "gainedviewcount",
    "gainedsubcount", "semantic_score",
)
CHANNEL_PAGE_LIMIT = 300
# Only the first 10,000 matching channels can be paged to.
CHANNEL_RESULT_WINDOW = 10_000
GROWTH_WINDOWS = ("28", "90", "180", "365")
CHANNEL_COLUMNS = (
    "channelid", "name", "handle", "thumbnailurl", "country", "joindate", "firstvideodate",
    "subcount", "totalviewcount", "videocount", "avgdurationseconds", "avgvideoviewcount",
    "autolanguages", "growth", "description", "topiccloud", "matchscore", "counts",
    "channelname", "channelsubcount", "channelcountryname",
)

EXPORT_SORTS = (
    "id", "viewcount", "likecount", "uploaddate", "duration", "chanrank",
    "channelvariety", "bm25match",
)
EXPORT_FORMATS = ("jsonl", "json", "csv", "zip")
EXPORT_PAGE_LIMIT = 500
# The server's default export columns, in its order; --subtitles appends one.
DEFAULT_EXPORT_COLUMNS = (
    "category", "channelid", "channelname", "id", "title", "uploaddate", "autosubs",
    "manualsubs", "duration", "viewcount", "likecount", "isunlisted", "license",
    "channelsubcount", "channelcountryname", "lang", "hits",
)
EXPORT_COLUMNS = DEFAULT_EXPORT_COLUMNS + ("subtitles", "description")
_EXPORT_EXTENSIONS = {".jsonl": "jsonl", ".ndjson": "jsonl", ".json": "json", ".csv": "csv", ".zip": "zip"}
# A channel filter may name many channels; only 1-5 included ones lower the price.
FILTER_CHANNEL_LIMIT = 100
SCOPED_CHANNEL_LIMIT = 5


def drop_unset(params: Dict[str, Any]) -> Dict[str, Any]:
    """Parameters without the options left unset (None or an empty list)."""
    return {key: value for key, value in params.items() if value not in (None, [], ())}


def channel_filter(included: Sequence[str], excluded: Sequence[str]) -> Optional[str]:
    """The ``channelID`` value: included IDs, or excluded IDs behind one ``-``."""
    if included:
        return ",".join(included)
    if excluded:
        return "-" + ",".join(excluded)
    return None


def scoped(included: Sequence[str]) -> bool:
    """Whether a search gets the lower price for one to five included channels."""
    return 1 <= len(set(included)) <= SCOPED_CHANNEL_LIMIT


def export_format(output: str, requested: Optional[str]) -> str:
    """``requested``, else the format the output file's extension names, else JSONL."""
    if requested:
        return requested
    return _EXPORT_EXTENSIONS.get(Path(output).suffix.casefold(), "jsonl")


def fetch_pages(
    fetch: Callable[[int], Any],
    rows_of: Callable[[Any], List[Dict[str, Any]]],
    *,
    first_page: int,
    pages: int,
    last_page: Optional[int] = None,
) -> Dict[str, Any]:
    """Up to ``pages`` consecutive pages from ``first_page``.

    Paging stops at the first empty page, because a short page is not a
    reliable end signal (a channel search that hits its time limit returns
    fewer rows), and at ``last_page`` when the API allows no later page.
    Returns ``rows``, the last ``response``, ``pages_fetched``, ``exhausted``,
    ``next_page``, ``last_page_reached`` (paging stopped at ``last_page``), and,
    when a page failed, ``error`` with ``next_page`` set to that page so
    earlier pages are kept.
    """
    rows: List[Dict[str, Any]] = []
    page = first_page
    fetched = 0
    exhausted = False
    response: Any = None
    error = None
    while fetched < pages and (last_page is None or page <= last_page):
        response = fetch(page)
        if isinstance(response, dict) and response.get("error"):
            error = response
            break
        found = rows_of(response)
        fetched += 1
        if not found:
            exhausted = True
            break
        rows.extend(found)
        page += 1
    last_page_reached = (
        not exhausted and error is None and last_page is not None and page > last_page
    )
    result: Dict[str, Any] = {
        "rows": rows,
        "response": response,
        "pages_fetched": fetched,
        "exhausted": exhausted or last_page_reached,
        "next_page": None if exhausted or last_page_reached else page,
        "last_page_reached": last_page_reached,
    }
    if error is not None:
        result["error"] = error
    return result


def result_rows(response: Any) -> List[Dict[str, Any]]:
    """The objects in a ``{"result": [...]}`` envelope or a bare list."""
    if isinstance(response, dict):
        response = response.get("result")
    if isinstance(response, list):
        return [row for row in response if isinstance(row, dict)]
    return []


# ------------------------------------------------------------------ exports


_BOM = codecs.BOM_UTF8
_COLUMN_NAME = re.compile(r"[a-z][a-z0-9_]*")
_CSV_LIMIT_LOCK = threading.RLock()


class ExportFormatError(ValueError):
    """An export page did not have the structure of its format."""


def _csv_records(content: bytes) -> List[List[str]]:
    """Strict CSV records, allowing fields as large as the received page.

    The csv module's field limit is process-global. Serialize our adjustments
    and restore the previous limit even when parsing fails.
    """
    try:
        decoded = content.decode("utf-8-sig")
        with _CSV_LIMIT_LOCK:
            previous_limit = csv.field_size_limit()
            try:
                csv.field_size_limit(max(previous_limit, len(decoded)))
                rows = [row for row in csv.reader(io.StringIO(decoded, newline=""), strict=True) if row]
            finally:
                csv.field_size_limit(previous_limit)
    except (csv.Error, UnicodeDecodeError) as error:
        raise ExportFormatError("the export page is not valid csv: {}".format(error)) from None
    # Allow new field names alongside known columns, but refuse HTML/error text.
    if rows and not (
        all(_COLUMN_NAME.fullmatch(name) for name in rows[0])
        and set(rows[0]) & set(EXPORT_COLUMNS + TITLE_COLUMNS + CHANNEL_COLUMNS)
    ):
        raise ExportFormatError("a CSV export page must start with a header of export columns")
    return rows


def _matching_csv_header(expected, rows):
    if not rows:
        return expected
    header = tuple(rows[0])
    if expected is not None and header != expected:
        raise ExportFormatError("CSV export columns changed between pages")
    return header


def export_rows(fmt: str, content: bytes) -> int:
    """Records on one export page, after checking it has its format's shape.

    The checks are light but catch what must never be written as an export:
    an HTML or error page served as CSV (its header must name export
    columns), an error object in JSON Lines, or a ZIP without its metadata.
    """
    try:
        if fmt == "zip":
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if archive.namelist().count("metadata.jsonl") != 1:
                    raise ExportFormatError("a ZIP export page must contain exactly one metadata.jsonl")
                metadata = b""
                for entry in archive.infolist():
                    value = archive.read(entry)
                    if entry.filename == "metadata.jsonl":
                        metadata = value
                return _jsonl_rows(metadata)
        if fmt == "jsonl":
            return _jsonl_rows(content)
        if fmt == "json":
            payload = json.loads(content.decode("utf-8-sig") or "{}")
            rows = payload.get("result") if isinstance(payload, dict) else payload
            if not isinstance(rows, list):
                raise ExportFormatError("a JSON export page must hold a result array")
            for row in rows:
                _check_row(row)
            return len(rows)
        rows = _csv_records(content)
        return max(len(rows) - 1, 0)
    except (ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError, zlib.error) as error:
        if isinstance(error, ExportFormatError):
            raise
        raise ExportFormatError("the export page is not valid {}: {}".format(fmt, error)) from None


def _check_row(row: Any) -> None:
    if not isinstance(row, dict):
        raise ExportFormatError("an export row must be an object")
    if "error" in row:
        # No export column is named error; this is an error object.
        raise ExportFormatError("the export page holds an error object, not matches")


def _jsonl_rows(content: bytes) -> int:
    count = 0
    # Records end at "\n" only; str.splitlines() would also split on U+2028,
    # U+2029, and U+0085, which JSON strings may contain unescaped.
    for line in content.decode("utf-8-sig").split("\n"):
        if line.strip():
            _check_row(json.loads(line))
            count += 1
    return count


def merge_export_pages(fmt: str, pages: Sequence[bytes]) -> bytes:
    """One file's bytes from consecutive export pages of ``fmt``."""
    if len(pages) == 1:
        return pages[0]
    if fmt == "jsonl":
        return _join_jsonl(pages)
    if fmt == "json":
        rows: List[Any] = []
        envelopes: List[Dict[str, Any]] = []
        for page in pages:
            payload = json.loads(page.decode("utf-8-sig") or "{}")
            rows.extend(payload.get("result", []) if isinstance(payload, dict) else payload)
            envelopes.append({key: value for key, value in payload.items() if key != "result"}
                             if isinstance(payload, dict) else {})
        combined: Dict[str, Any] = {"result": rows}
        if any(envelopes):
            # An aggregate is not a single API page. Keep each page's envelope
            # in request order without assigning it to the combined results.
            combined["pages"] = envelopes
        return (json.dumps(combined, ensure_ascii=True) + "\n").encode("utf-8")
    if fmt == "csv":
        merged = io.StringIO(newline="")
        writer = csv.writer(merged)
        header = None
        for page in pages:
            records = _csv_records(page)
            matching_header = _matching_csv_header(header, records)
            if records:
                if header is None:
                    writer.writerow(records[0])
                writer.writerows(records[1:])
            header = matching_header
        prefix = _BOM if pages and pages[0].startswith(_BOM) else b""
        return prefix + merged.getvalue().encode("utf-8")
    return _merge_zips(pages)


def _join_jsonl(pages: Sequence[bytes]) -> bytes:
    """JSON Lines pages as one file: a BOM only at its start, whole lines throughout."""
    joined = []
    for index, page in enumerate(pages):
        if index and page.startswith(_BOM):
            page = page[len(_BOM):]
        joined.append(page if not page or page.endswith(b"\n") else page + b"\n")
    return b"".join(joined)


def _merge_zips(pages: Sequence[bytes]) -> bytes:
    metadata: List[bytes] = []
    entries: Dict[str, bytes] = {}
    for page in pages:
        with zipfile.ZipFile(io.BytesIO(page)) as archive:
            for name in archive.namelist():
                if name == "metadata.jsonl":
                    metadata.append(archive.read(name))
                elif name not in entries:
                    entries[name] = archive.read(name)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as merged:
        merged.writestr("metadata.jsonl", _join_jsonl(metadata))
        for name, content in entries.items():
            merged.writestr(name, content)
    return buffer.getvalue()


def fetch_export_pages(
    fetch: Callable[[int], Any],
    fmt: str,
    *,
    per_page: int,
    first_page: int,
    pages: int,
    on_page: Optional[Callable[[int, int], None]] = None,
    stop_on_short: bool = True,
    last_page: Optional[int] = None,
) -> Dict[str, Any]:
    """Up to ``pages`` export pages from ``first_page``.

    Subtitle exports end at a short page. Metadata and channel exports pass
    ``stop_on_short=False`` and their ``last_page``: short pages can be followed
    by more results. Returns the page ``bodies``, ``rows``,
    ``pages_fetched``, ``exhausted``, and ``next_page``; a failed page adds the
    API's ``error`` and keeps the pages before it. A malformed later page
    likewise preserves prior pages and points ``next_page`` at the failed page.
    """
    bodies: List[bytes] = []
    rows = 0
    page = first_page
    exhausted = False
    error = None
    csv_header = None
    while len(bodies) < pages and (last_page is None or page <= last_page):
        body = fetch(page)
        if isinstance(body, dict):
            error = body
            break
        try:
            if fmt == "csv":
                records = _csv_records(body.content)
                csv_header = _matching_csv_header(csv_header, records)
                count = max(len(records) - 1, 0)
            else:
                count = export_rows(fmt, body.content)
        except ExportFormatError as failure:
            if not bodies:
                raise
            error = {"error": str(failure), "error_type": "ExportFormatError", "stage": "invalid-response"}
            break
        bodies.append(body.content)
        rows += count
        if on_page is not None:
            on_page(page, count)
        page += 1
        if count == 0 or (stop_on_short and count < per_page):
            exhausted = True
            break
    last_page_reached = (
        not exhausted and error is None and last_page is not None and page > last_page
    )
    result: Dict[str, Any] = {
        "bodies": bodies,
        "rows": rows,
        "pages_fetched": len(bodies),
        "exhausted": exhausted or last_page_reached,
        "next_page": None if exhausted or last_page_reached else page,
        "last_page_reached": last_page_reached,
    }
    if error is not None:
        result["error"] = error
    return result


def write_atomically(path: Path, content: bytes) -> None:
    """Replace ``path`` with ``content`` so a failure never leaves a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".{}.{}.tmp".format(path.name, uuid.uuid4().hex))
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
