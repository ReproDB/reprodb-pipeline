"""Tests for src.utils.cache – disk-based cache utility."""

import json
import time

from src.utils.io.cache import (
    _MISSING,
    cache_path,
    read_cache,
    read_cache_entry,
    refresh_cache_ts,
    write_cache,
)


class TestCachePath:
    def test_deterministic(self, tmp_path):
        p1 = cache_path(str(tmp_path), "key1")
        p2 = cache_path(str(tmp_path), "key1")
        assert p1 == p2

    def test_different_keys(self, tmp_path):
        p1 = cache_path(str(tmp_path), "key1")
        p2 = cache_path(str(tmp_path), "key2")
        assert p1 != p2

    def test_namespace(self, tmp_path):
        p1 = cache_path(str(tmp_path), "key1", "ns1")
        p2 = cache_path(str(tmp_path), "key1", "ns2")
        assert p1 != p2
        assert "ns1" in p1
        assert "ns2" in p2


class TestWriteAndReadCache:
    def test_round_trip(self, tmp_path):
        d = str(tmp_path)
        write_cache(d, "k", "hello")
        assert read_cache(d, "k", ttl=60) == "hello"

    def test_expired(self, tmp_path):
        d = str(tmp_path)
        write_cache(d, "k", "hello")
        # Manually backdate the timestamp
        path = cache_path(d, "k")
        with open(path) as f:
            entry = json.load(f)
        entry["ts"] = time.time() - 120
        with open(path, "w") as f:
            json.dump(entry, f)
        assert read_cache(d, "k", ttl=60) is _MISSING

    def test_miss(self, tmp_path):
        assert read_cache(str(tmp_path), "nonexistent", ttl=60) is _MISSING

    def test_etag_stored(self, tmp_path):
        d = str(tmp_path)
        write_cache(d, "k", "body", etag="abc123")
        entry = read_cache_entry(d, "k")
        assert entry["etag"] == "abc123"
        assert entry["body"] == "body"


class TestReadCacheEntry:
    def test_returns_full_entry(self, tmp_path):
        d = str(tmp_path)
        write_cache(d, "k", "val")
        entry = read_cache_entry(d, "k")
        assert "ts" in entry
        assert entry["body"] == "val"

    def test_miss_returns_none(self, tmp_path):
        assert read_cache_entry(str(tmp_path), "nope") is None

    def test_corrupted_returns_none(self, tmp_path):
        d = str(tmp_path)
        write_cache(d, "k", "val")
        path = cache_path(d, "k")
        with open(path, "w") as f:
            f.write("not json{{{")
        assert read_cache_entry(d, "k") is None


class TestRefreshCacheTs:
    def test_updates_timestamp(self, tmp_path):
        d = str(tmp_path)
        write_cache(d, "k", "val")
        path = cache_path(d, "k")
        with open(path) as f:
            old_ts = json.load(f)["ts"]
        # Backdate then refresh
        with open(path) as f:
            entry = json.load(f)
        entry["ts"] = old_ts - 1000
        with open(path, "w") as f:
            json.dump(entry, f)
        refresh_cache_ts(d, "k")
        with open(path) as f:
            new_ts = json.load(f)["ts"]
        assert new_ts > old_ts - 1000

    def test_no_crash_on_missing(self, tmp_path):
        # Should not raise
        refresh_cache_ts(str(tmp_path), "nonexistent")
