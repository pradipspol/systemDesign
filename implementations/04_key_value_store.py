"""
=============================================================
  4. Key-Value Store — LSM-Tree Based Storage Engine
  Run: python 04_key_value_store.py
  Implements MemTable (sorted), SSTable flush, Bloom filter,
  WAL (write-ahead log), and basic compaction.
=============================================================
"""
import os
import json
import time
import hashlib
import threading
from math import log, ceil
from collections import OrderedDict
from typing import Optional


# ===================================================================
# Bloom Filter
# ===================================================================
class BloomFilter:
    def __init__(self, expected_items: int = 10000, fp_rate: float = 0.01):
        self.size = self._optimal_size(expected_items, fp_rate)
        self.num_hashes = self._optimal_hashes(self.size, expected_items)
        self.bit_array = bytearray(self.size)

    @staticmethod
    def _optimal_size(n: int, p: float) -> int:
        return max(8, int(-n * log(p) / (log(2) ** 2)))

    @staticmethod
    def _optimal_hashes(m: int, n: int) -> int:
        return max(1, int((m / n) * log(2)))

    def _hashes(self, key: str):
        h1 = int(hashlib.md5(key.encode()).hexdigest(), 16)
        h2 = int(hashlib.sha1(key.encode()).hexdigest(), 16)
        for i in range(self.num_hashes):
            yield (h1 + i * h2) % self.size

    def add(self, key: str):
        for pos in self._hashes(key):
            self.bit_array[pos] = 1

    def might_contain(self, key: str) -> bool:
        return all(self.bit_array[pos] for pos in self._hashes(key))


# ===================================================================
# MemTable (Sorted in-memory table)
# ===================================================================
class MemTable:
    def __init__(self, max_size: int = 1000):
        self.data: dict[str, tuple[str, float]] = {}  # key -> (value, timestamp)
        self.max_size = max_size
        self._lock = threading.Lock()

    def put(self, key: str, value: str) -> bool:
        """Returns True if memtable should be flushed."""
        with self._lock:
            self.data[key] = (value, time.time())
            return len(self.data) >= self.max_size

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            entry = self.data.get(key)
            return entry[0] if entry else None

    def delete(self, key: str):
        with self._lock:
            self.data[key] = ("__TOMBSTONE__", time.time())

    def to_sorted_items(self) -> list[tuple[str, str, float]]:
        with self._lock:
            return sorted(
                [(k, v, ts) for k, (v, ts) in self.data.items()],
                key=lambda x: x[0],
            )

    def size(self) -> int:
        return len(self.data)

    def clear(self):
        with self._lock:
            self.data.clear()


# ===================================================================
# SSTable (Sorted String Table on disk)
# ===================================================================
class SSTable:
    """Immutable sorted key-value file + sparse index + bloom filter."""

    def __init__(self, table_id: int, data_dir: str = ".kvstore"):
        self.table_id = table_id
        self.data_dir = data_dir
        self.filepath = os.path.join(data_dir, f"sst_{table_id:06d}.json")
        self.bloom = BloomFilter(expected_items=2000)
        self.sparse_index: dict[str, int] = {}
        self.entries: list[tuple[str, str, float]] = []

    def write(self, sorted_items: list[tuple[str, str, float]]):
        """Write sorted items to disk."""
        os.makedirs(self.data_dir, exist_ok=True)
        self.entries = sorted_items
        for i, (k, v, ts) in enumerate(sorted_items):
            self.bloom.add(k)
            if i % 16 == 0:  # sparse index every 16 entries
                self.sparse_index[k] = i
        with open(self.filepath, "w") as f:
            json.dump([(k, v, ts) for k, v, ts in sorted_items], f)

    def get(self, key: str) -> Optional[tuple[str, float]]:
        """Binary search for key."""
        if not self.bloom.might_contain(key):
            return None
        lo, hi = 0, len(self.entries) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.entries[mid][0] == key:
                return (self.entries[mid][1], self.entries[mid][2])
            elif self.entries[mid][0] < key:
                lo = mid + 1
            else:
                hi = mid - 1
        return None


# ===================================================================
# Write-Ahead Log
# ===================================================================
class WAL:
    def __init__(self, data_dir: str = ".kvstore"):
        self.data_dir = data_dir
        self.filepath = os.path.join(data_dir, "wal.log")
        os.makedirs(data_dir, exist_ok=True)

    def append(self, op: str, key: str, value: str = ""):
        with open(self.filepath, "a") as f:
            f.write(json.dumps({"op": op, "key": key, "value": value, "ts": time.time()}) + "\n")

    def replay(self) -> list[dict]:
        entries = []
        if os.path.exists(self.filepath):
            with open(self.filepath, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        return entries

    def clear(self):
        if os.path.exists(self.filepath):
            os.remove(self.filepath)


# ===================================================================
# LSM-Tree Key-Value Store
# ===================================================================
class LSMKeyValueStore:
    def __init__(self, data_dir: str = ".kvstore", memtable_size: int = 100):
        self.data_dir = data_dir
        self.memtable = MemTable(max_size=memtable_size)
        self.wal = WAL(data_dir)
        self.sstables: list[SSTable] = []
        self.sst_counter = 0
        self._lock = threading.Lock()
        self._stats = {"reads": 0, "writes": 0, "memtable_hits": 0, "sstable_hits": 0, "misses": 0, "flushes": 0}

    def put(self, key: str, value: str):
        self._stats["writes"] += 1
        self.wal.append("PUT", key, value)
        should_flush = self.memtable.put(key, value)
        if should_flush:
            self._flush()

    def get(self, key: str) -> Optional[str]:
        self._stats["reads"] += 1

        # 1. Check MemTable
        val = self.memtable.get(key)
        if val is not None:
            if val == "__TOMBSTONE__":
                self._stats["misses"] += 1
                return None
            self._stats["memtable_hits"] += 1
            return val

        # 2. Check SSTables (newest first)
        for sst in reversed(self.sstables):
            result = sst.get(key)
            if result is not None:
                val, ts = result
                if val == "__TOMBSTONE__":
                    self._stats["misses"] += 1
                    return None
                self._stats["sstable_hits"] += 1
                return val

        self._stats["misses"] += 1
        return None

    def delete(self, key: str):
        self.wal.append("DELETE", key)
        self.memtable.delete(key)

    def _flush(self):
        """Flush memtable to a new SSTable."""
        with self._lock:
            sorted_items = self.memtable.to_sorted_items()
            if not sorted_items:
                return
            sst = SSTable(self.sst_counter, self.data_dir)
            sst.write(sorted_items)
            self.sstables.append(sst)
            self.sst_counter += 1
            self.memtable.clear()
            self.wal.clear()
            self._stats["flushes"] += 1

    def compact(self):
        """Simple compaction: merge all SSTables into one."""
        if len(self.sstables) < 2:
            return
        merged: dict[str, tuple[str, float]] = {}
        for sst in self.sstables:
            for k, v, ts in sst.entries:
                if k not in merged or ts > merged[k][1]:
                    merged[k] = (v, ts)

        # Remove tombstones
        merged = {k: (v, ts) for k, (v, ts) in merged.items() if v != "__TOMBSTONE__"}
        sorted_items = sorted([(k, v, ts) for k, (v, ts) in merged.items()], key=lambda x: x[0])

        new_sst = SSTable(self.sst_counter, self.data_dir)
        new_sst.write(sorted_items)
        self.sst_counter += 1
        self.sstables = [new_sst]

    def stats(self) -> dict:
        return {
            **self._stats,
            "memtable_size": self.memtable.size(),
            "sstable_count": len(self.sstables),
        }


# ===================================================================
# Demo
# ===================================================================
if __name__ == "__main__":
    import shutil

    DATA_DIR = ".kvstore_demo"
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR)

    print("=" * 65)
    print("  Key-Value Store — LSM-Tree Implementation")
    print("=" * 65)

    store = LSMKeyValueStore(data_dir=DATA_DIR, memtable_size=50)

    # Write 200 keys to trigger multiple flushes
    print("\n  Writing 200 keys (memtable flushes at 50)...")
    for i in range(200):
        store.put(f"user:{i:04d}", json.dumps({"name": f"User {i}", "score": i * 10}))
    print(f"  Stats after writes: {store.stats()}")

    # Read some keys
    print("\n  Reading keys:")
    for key in ["user:0001", "user:0099", "user:0150", "user:9999"]:
        val = store.get(key)
        print(f"    {key} → {val}")

    # Delete and verify
    print("\n  Deleting user:0050...")
    store.delete("user:0050")
    print(f"    user:0050 → {store.get('user:0050')}")

    # Update a key
    print("\n  Updating user:0001...")
    store.put("user:0001", json.dumps({"name": "User 1 UPDATED", "score": 9999}))
    print(f"    user:0001 → {store.get('user:0001')}")

    # Compaction
    print(f"\n  Before compaction: {store.stats()}")
    store.compact()
    print(f"  After compaction:  {store.stats()}")

    # Verify data still readable after compaction
    print(f"\n  After compaction read: user:0001 → {store.get('user:0001')}")
    print(f"  After compaction read: user:0050 → {store.get('user:0050')} (deleted)")

    # Bloom filter test
    print("\n  Bloom Filter Stats:")
    bf = BloomFilter(expected_items=1000, fp_rate=0.01)
    for i in range(1000):
        bf.add(f"key_{i}")
    fp = sum(1 for i in range(1000, 2000) if bf.might_contain(f"key_{i}"))
    print(f"    1000 keys inserted, false positives on 1000 absent keys: {fp} ({fp/10:.1f}%)")

    # Cleanup
    shutil.rmtree(DATA_DIR, ignore_errors=True)
    print("\nDone.")
