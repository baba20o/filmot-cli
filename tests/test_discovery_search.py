"""Paging, export parsing and merging, and filters for Filmot discovery searches."""

import io
import csv
import json
import zipfile
from unittest.mock import patch

import pytest

from filmot.api import ExportBody
from filmot.discovery_search import (
    ExportFormatError,
    channel_filter,
    export_format,
    export_rows,
    fetch_export_pages,
    fetch_pages,
    merge_export_pages,
    result_rows,
    scoped,
    write_atomically,
)


def test_channel_filters_include_or_exclude_and_set_the_price():
    assert channel_filter(["UC1", "UC2"], []) == "UC1,UC2"
    assert channel_filter([], ["UC1", "UC2"]) == "-UC1,UC2"
    assert channel_filter([], []) is None
    assert scoped(["UC1"] * 3) and scoped([f"UC{n}" for n in range(5)])
    assert not scoped([]) and not scoped([f"UC{n}" for n in range(6)])


@pytest.mark.parametrize("output,requested,expected", [
    ("matches.CSV", None, "csv"),
    ("matches.zip", None, "zip"),
    ("matches.json", None, "json"),
    ("matches.ndjson", None, "jsonl"),
    ("matches.txt", None, "jsonl"),
    ("matches.csv", "jsonl", "jsonl"),
])
def test_the_export_format_follows_the_option_then_the_extension(output, requested, expected):
    assert export_format(output, requested) == expected


# ------------------------------------------------------------------ paging


def pages_of(*pages):
    queue = list(pages)
    calls = []

    def fetch(page):
        calls.append(page)
        return queue.pop(0)

    return fetch, calls


def test_paging_stops_at_an_empty_page_not_a_short_one():
    fetch, calls = pages_of({"result": [{"id": "a"}]}, {"result": [{"id": "b"}]}, {"result": []})

    listing = fetch_pages(fetch, result_rows, first_page=1, pages=5)

    assert [row["id"] for row in listing["rows"]] == ["a", "b"]
    assert calls == [1, 2, 3]
    assert (listing["exhausted"], listing["next_page"], listing["last_page_reached"]) == (True, None, False)


def test_paging_stops_at_the_last_page_the_api_allows():
    fetch, calls = pages_of({"result": [{"id": "a"}]}, {"result": [{"id": "b"}]})

    listing = fetch_pages(fetch, result_rows, first_page=499, pages=5, last_page=500)

    assert calls == [499, 500]
    assert (listing["exhausted"], listing["next_page"], listing["last_page_reached"]) == (True, None, True)


def test_a_failed_page_keeps_earlier_rows_and_points_at_itself():
    failure = {"error": "busy", "error_type": "FilmotServiceError"}
    fetch, _ = pages_of({"result": [{"id": "a"}]}, failure)

    listing = fetch_pages(fetch, result_rows, first_page=2, pages=3)

    assert [row["id"] for row in listing["rows"]] == ["a"]
    assert listing["error"] == failure
    assert (listing["pages_fetched"], listing["next_page"]) == (1, 3)


# ------------------------------------------------------------- export rows


def zip_of(metadata, **files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if metadata is not None:
            archive.writestr("metadata.jsonl", metadata)
        for name, content in files.items():
            archive.writestr(name.replace("__", "/"), content)
    return buffer.getvalue()


CSV_PAGE = "\ufeffid,title\r\nabc,One\r\ndef,\"Two, with comma\"\r\n".encode("utf-8")


@pytest.mark.parametrize("fmt,content,rows", [
    ("jsonl", b'{"id": "a"}\n{"id": "b"}\n\n', 2),
    ("json", b'{"result": [{"id": "a"}]}', 1),
    ("csv", CSV_PAGE, 2),
    ("csv", "\ufeffid,title\r\n".encode("utf-8"), 0),
    ("csv", b"", 0),
    # A column Filmot adds later does not invalidate the page.
    ("csv", b"id,title,futurecolumn\nabc,One,x\n", 1),
    ("zip", zip_of('{"id": "a"}\n{"id": "b"}\n', subs__a_en_srt="1"), 2),
    ("zip", zip_of(""), 0),
])
def test_rows_are_counted_per_format(fmt, content, rows):
    assert export_rows(fmt, content) == rows


@pytest.mark.parametrize("fmt,content", [
    ("jsonl", b'{"id": "a"}\nnot json\n'),
    ("jsonl", b'["a"]\n'),
    ("jsonl", b'{"error": "No credits left", "error_type": "FilmotQuotaExhausted"}\n'),
    ("json", b'{"rows": []}'),
    ("json", b'{"result": [{"error": "busy"}]}'),
    ("csv", b"<!DOCTYPE html>\n<html><body>Service unavailable</body></html>\n"),
    ("csv", b'{"error": "No credits left"}\n'),
    ("csv", b"foo,bar\nabc,3\n"),
    ("zip", b"not a zip"),
    ("zip", zip_of(None, subs__a_en_srt="1")),
])
def test_malformed_export_pages_are_typed_errors(fmt, content):
    with pytest.raises(ExportFormatError):
        export_rows(fmt, content)


# ---------------------------------------------------------------- merging


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085"], ids=["LS", "PS", "NEL"])
def test_unicode_line_separators_inside_records_do_not_split_them(separator):
    rows = [{"id": "a", "title": "one{}two".format(separator)}, {"id": "b", "title": "three"}]
    page = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8")
    assert separator.encode("utf-8") in page

    assert export_rows("jsonl", page) == 2
    assert export_rows("zip", zip_of(page.decode("utf-8"))) == 2


def test_jsonl_pages_concatenate_line_by_line():
    assert merge_export_pages("jsonl", [b'{"id": "a"}', b'{"id": "b"}\n']) == b'{"id": "a"}\n{"id": "b"}\n'


def test_jsonl_merging_keeps_a_bom_only_at_the_start():
    bom = "\ufeff".encode("utf-8")

    merged = merge_export_pages("jsonl", [bom + b'{"id": "a"}\n', bom + b'{"id": "b"}\n'])

    assert merged == bom + b'{"id": "a"}\n{"id": "b"}\n'
    assert export_rows("jsonl", merged) == 2


def test_json_pages_merge_their_results():
    merged = json.loads(merge_export_pages("json", [b'{"result": [{"id": "a"}]}', b'{"result": [{"id": "b"}]}']))

    assert merged == {"result": [{"id": "a"}, {"id": "b"}]}


def test_csv_pages_keep_one_header_and_bom():
    second = "\ufeffid,title\r\nghi,Three\r\n".encode("utf-8")

    merged = merge_export_pages("csv", [CSV_PAGE, second])

    text = merged.decode("utf-8-sig")
    assert text.count("id,title") == 1 and '\ufeff' not in text
    assert export_rows("csv", merged) == 3


def test_zip_metadata_merging_keeps_a_bom_only_at_the_start():
    bom = "\ufeff"
    pages = [zip_of(bom + '{"id": "a"}\n'), zip_of(bom + '{"id": "b"}\n'), zip_of("")]

    merged = merge_export_pages("zip", pages)

    with zipfile.ZipFile(io.BytesIO(merged)) as archive:
        assert archive.read("metadata.jsonl") == (bom + '{"id": "a"}\n{"id": "b"}\n').encode("utf-8")
    assert export_rows("zip", merged) == 2


def test_zip_pages_merge_metadata_and_keep_each_file_once():
    first = zip_of('{"id": "a"}\n', subs__a_en_srt="first")
    second = zip_of('{"id": "b"}', subs__b_en_srt="second", subs__a_en_srt="duplicate")

    with zipfile.ZipFile(io.BytesIO(merge_export_pages("zip", [first, second]))) as merged:
        assert merged.read("metadata.jsonl") == b'{"id": "a"}\n{"id": "b"}\n'
        assert merged.read("subs/a_en_srt") == b"first"
        assert sorted(merged.namelist()) == ["metadata.jsonl", "subs/a_en_srt", "subs/b_en_srt"]


def test_export_paging_ends_at_a_short_page_and_keeps_pages_before_a_failure():
    page = b'{"id": "a"}\n{"id": "b"}\n'
    seen = []
    fetch, calls = pages_of(ExportBody(page, "application/x-ndjson"), ExportBody(b'{"id": "c"}\n', ""))

    short = fetch_export_pages(fetch, "jsonl", per_page=2, first_page=1, pages=5, on_page=lambda *a: seen.append(a))
    failure = {"error": "No credits left", "error_type": "FilmotQuotaExhausted"}
    fetch, _ = pages_of(ExportBody(page, ""), failure)
    failed = fetch_export_pages(fetch, "jsonl", per_page=2, first_page=4, pages=3)

    assert (short["rows"], short["pages_fetched"], short["exhausted"], short["next_page"]) == (3, 2, True, None)
    assert calls == [1, 2] and seen == [(1, 2), (2, 1)]
    assert (failed["rows"], failed["bodies"], failed["next_page"]) == (2, [page], 5)
    assert failed["error"] == failure


def test_malformed_export_page_preserves_valid_pages_and_retry_cursor():
    first = b'{"id":"a"}\n'
    fetch, calls = pages_of(ExportBody(first, ""), ExportBody(b"not-json", ""))
    result = fetch_export_pages(fetch, "jsonl", per_page=1, first_page=4, pages=3)
    assert calls == [4, 5]
    assert result["bodies"] == [first] and result["rows"] == 1
    assert result["pages_fetched"] == 1 and result["next_page"] == 5
    assert result["error"]["error_type"] == "ExportFormatError"
    assert not result["exhausted"]


def test_metadata_export_waits_for_empty_page_or_last_page():
    body = ExportBody(b'{"id":"a"}\n', "")
    fetch, calls = pages_of(body, body)
    result = fetch_export_pages(fetch, "jsonl", per_page=10, first_page=499, pages=4,
                                stop_on_short=False, last_page=500)
    assert calls == [499, 500]
    assert result["rows"] == 2 and result["last_page_reached"]
    assert result["exhausted"] and result["next_page"] is None


def test_metadata_csv_supports_selected_metadata_fields():
    assert export_rows("csv", b"dislikecount\n0\n-1\n") == 2


def test_csv_long_fields_restore_the_process_limit_even_after_parse_failure():
    original = csv.field_size_limit()
    field = b"x" * (original + 100)
    assert export_rows("csv", b'id,subtitles\na,"' + field + b'"\n') == 1
    assert csv.field_size_limit() == original
    with pytest.raises(ExportFormatError):
        export_rows("csv", b'id,subtitles\na,"' + field)
    assert csv.field_size_limit() == original


def test_csv_header_drift_is_rejected_before_adding_the_failed_page():
    first = b"id,title\na,First\n"
    changed = b"title,id\nSecond,b\n"
    fetch, calls = pages_of(ExportBody(first, ""), ExportBody(changed, ""))
    result = fetch_export_pages(fetch, "csv", per_page=1, first_page=4, pages=3)
    assert calls == [4, 5]
    assert result["bodies"] == [first] and result["rows"] == 1
    assert result["next_page"] == 5 and result["pages_fetched"] == 1
    assert result["error"]["error_type"] == "ExportFormatError"
    with pytest.raises(ExportFormatError, match="columns changed"):
        merge_export_pages("csv", [first, changed])


def test_csv_merge_handles_cr_records_and_empty_final_pages():
    merged = merge_export_pages("csv", [b'id,title\ra,"First\rline"\r', b'id,title\rb,Second\r', b""])
    assert list(csv.reader(io.StringIO(merged.decode(), newline=""))) == [
        ["id", "title"], ["a", "First\rline"], ["b", "Second"],
    ]


def test_json_merging_retains_page_metadata_in_source_order_and_escaped_surrogates():
    first = b'{"result":[{"channelid":"a"}],"page":2,"perpagecount":1,"maxresults":10000}'
    second = b'{"result":[{"channelid":"b","name":"\\ud800"}],"page":3,"perpagecount":1,"maxresults":10000}'
    assert export_rows("json", second) == 1
    assert merge_export_pages("json", [first]) == first
    merged = json.loads(merge_export_pages("json", [first, second]))
    assert [row["channelid"] for row in merged["result"]] == ["a", "b"]
    assert merged["result"][1]["name"] == "\ud800"
    assert merged["pages"] == [
        {"page": 2, "perpagecount": 1, "maxresults": 10000},
        {"page": 3, "perpagecount": 1, "maxresults": 10000},
    ]
    assert "page" not in merged


def test_duplicate_zip_metadata_and_unreadable_caption_entries_are_rejected():
    duplicate = io.BytesIO()
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("metadata.jsonl", '{"id":"a"}\n')
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("metadata.jsonl", '{"id":"b"}\n')
    with pytest.raises(ExportFormatError, match="exactly one"):
        export_rows("zip", duplicate.getvalue())

    damaged = bytearray(zip_of('{"id":"a"}\n', subs__a="caption payload"))
    damaged[damaged.index(b"caption payload")] = ord("X")
    first = zip_of('{"id":"b"}\n')
    fetch, _ = pages_of(ExportBody(first, ""), ExportBody(bytes(damaged), ""))
    result = fetch_export_pages(fetch, "zip", per_page=1, first_page=1, pages=2)
    assert result["bodies"] == [first] and result["next_page"] == 2
    assert result["error"]["error_type"] == "ExportFormatError"


# ------------------------------------------------------------------ files


def test_files_are_replaced_whole_and_failures_leave_no_temporary(tmp_path):
    target = tmp_path / "out" / "matches.csv"
    write_atomically(target, b"first")
    write_atomically(target, b"second")

    with patch("filmot.discovery_search.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            write_atomically(target, b"third")

    assert target.read_bytes() == b"second"
    assert [path.name for path in target.parent.iterdir()] == ["matches.csv"]
