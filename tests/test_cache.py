"""The response cache touches only files named like its own entries."""

import json
import time

from filmot.cache import Cache


def foreign_files(directory):
    files = {
        "notes.json": {"a": 1},
        "list.json": [1, 2, 3],
        # Hex-looking but not an MD5 digest name.
        "abc123.json": {"timestamp": 0, "data": {}},
    }
    for name, content in files.items():
        (directory / name).write_text(json.dumps(content), encoding="utf-8")
    (directory / "broken.json").write_text("{not json", encoding="utf-8")
    return set(files) | {"broken.json"}


def test_foreign_json_survives_every_cache_operation(tmp_path):
    names = foreign_files(tmp_path)

    cache = Cache(cache_dir=tmp_path, ttl=60)
    cache.clear_expired()
    stats = cache.stats()
    cache.clear()

    assert {path.name for path in tmp_path.iterdir()} == names
    assert stats["total_entries"] == 0


def test_cache_entries_are_purged_kept_and_counted_as_before(tmp_path):
    cache = Cache(cache_dir=tmp_path, ttl=60)
    cache.set("/getvideos", {"id": "fresh"}, {"result": 1})
    cache.set("/getvideos", {"id": "stale"}, {"result": 2})
    stale = cache._get_cache_path(cache._get_cache_key("/getvideos", {"id": "stale"}))
    entry = json.loads(stale.read_text(encoding="utf-8"))
    stale.write_text(json.dumps(dict(entry, timestamp=time.time() - 3600)), encoding="utf-8")
    corrupt = tmp_path / ("0" * 32 + ".json")
    corrupt.write_text("[1, 2]", encoding="utf-8")
    foreign_files(tmp_path)

    assert cache.stats()["total_entries"] == 3
    assert cache.get("/getvideos", {"id": "fresh"}) == {"result": 1}
    assert cache.get("/getvideos", {"id": "stale"}) is None

    reopened = Cache(cache_dir=tmp_path, ttl=60)

    assert not stale.exists() and not corrupt.exists()
    assert reopened.get("/getvideos", {"id": "fresh"}) == {"result": 1}
    assert reopened.clear() == 1
    assert (tmp_path / "notes.json").exists()


def test_a_non_object_entry_is_a_miss_not_a_crash(tmp_path):
    cache = Cache(cache_dir=tmp_path, ttl=60)
    path = cache._get_cache_path(cache._get_cache_key("/getvideos", {"id": "x"}))
    path.write_text("[1, 2, 3]", encoding="utf-8")

    assert cache.get("/getvideos", {"id": "x"}) is None


def test_entries_with_implausible_timestamps_are_treated_as_corrupt(tmp_path):
    cache = Cache(cache_dir=tmp_path, ttl=60)
    paths = []
    for index, timestamp in enumerate(("NaN", "Infinity", "1" + "0" * 400, "-5")):
        path = tmp_path / ("{:032x}.json".format(index + 1))
        path.write_text('{"timestamp": %s, "data": {}}' % timestamp, encoding="utf-8")
        paths.append(path)

    assert cache.stats()["valid_entries"] == 0
    assert cache.clear_expired() == 4
    assert not any(path.exists() for path in paths)
