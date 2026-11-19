"""In-process TTL cache with version-based invalidation.

Every project has a version number. Cached entries include it in their key, so bumping the version makes all old
entries for that project unreachable at once (they expire via TTL/LRU). The version is bumped by the process that
wrote the data (immediately) and by every other process when its event poller sees the change.
"""
import threading
import time
from collections import OrderedDict


class Versions:
    def __init__(self):
        self._v, self._lock = {}, threading.Lock()

    def get(self, project_id):
        return self._v.get(project_id, 0)

    def bump(self, project_id):
        with self._lock:
            self._v[project_id] = self._v.get(project_id, 0) + 1
            return self._v[project_id]


class TTLCache:
    def __init__(self, max_entries=5000, default_ttl=30):
        self.max_entries, self.default_ttl = max_entries, default_ttl
        self._data, self._lock = OrderedDict(), threading.Lock()
        self.hits = self.misses = 0

    def get(self, key):
        with self._lock:
            item = self._data.get(key)
            if item and item[0] > time.monotonic():
                self._data.move_to_end(key)
                self.hits += 1
                return item[1]
            if item:
                del self._data[key]
            self.misses += 1
            return None

    def set(self, key, value, ttl=None):
        with self._lock:
            self._data[key] = (time.monotonic() + (ttl or self.default_ttl), value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def stats(self):
        total = self.hits + self.misses
        return {"entries": len(self._data), "hits": self.hits, "misses": self.misses,
                "hit_ratio": round(self.hits / total, 3) if total else None}
