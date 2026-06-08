"""
=============================================================
  19. Distributed Cache — LRU/LFU + TTL + Stampede Prevention
  Run: python 19_distributed_cache.py
  Implements LRU cache, LFU cache, TTL expiration,
  single-flight (stampede prevention), write-behind,
  and cache cluster with consistent hashing.
=============================================================
"""
import time
import hashlib
import threading
import bisect
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from typing import Optional, Callable, Any


# ===================================================================
# 1. LRU Cache
# ===================================================================
class LRUCache:
    """Least Recently Used cache with O(1) get/put."""

    def __init__(self, capacity: int = 100):
        self.capacity = capacity
        self.cache: OrderedDict[str, Any] = OrderedDict()
        self.hits = 0
        self.misses = 0
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                self.hits += 1
                return self.cache[key]
            self.misses += 1
            return None

    def put(self, key: str, value: Any):
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                self.cache[key] = value
            else:
                if len(self.cache) >= self.capacity:
                    self.cache.popitem(last=False)
                self.cache[key] = value

    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self.cache:
                del self.cache[key]
                return True
            return False

    def size(self) -> int:
        return len(self.cache)

    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


# ===================================================================
# 2. LFU Cache
# ===================================================================
class LFUCache:
    """Least Frequently Used cache."""

    def __init__(self, capacity: int = 100):
        self.capacity = capacity
        self.cache: dict[str, Any] = {}
        self.freq: dict[str, int] = {}
        self.freq_buckets: dict[int, OrderedDict] = defaultdict(OrderedDict)
        self.min_freq = 0
        self.hits = 0
        self.misses = 0
        self._lock = threading.Lock()

    def _update_freq(self, key: str):
        f = self.freq[key]
        self.freq[key] = f + 1
        del self.freq_buckets[f][key]
        if not self.freq_buckets[f]:
            del self.freq_buckets[f]
            if self.min_freq == f:
                self.min_freq += 1
        self.freq_buckets[f + 1][key] = True

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self.cache:
                self._update_freq(key)
                self.hits += 1
                return self.cache[key]
            self.misses += 1
            return None

    def put(self, key: str, value: Any):
        with self._lock:
            if self.capacity <= 0:
                return
            if key in self.cache:
                self.cache[key] = value
                self._update_freq(key)
                return
            if len(self.cache) >= self.capacity:
                # Evict LFU
                bucket = self.freq_buckets[self.min_freq]
                evict_key, _ = bucket.popitem(last=False)
                if not bucket:
                    del self.freq_buckets[self.min_freq]
                del self.cache[evict_key]
                del self.freq[evict_key]

            self.cache[key] = value
            self.freq[key] = 1
            self.freq_buckets[1][key] = True
            self.min_freq = 1

    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


# ===================================================================
# 3. TTL Cache (LRU + expiration)
# ===================================================================
class TTLCache:
    def __init__(self, capacity: int = 100, default_ttl: float = 300.0):
        self.capacity = capacity
        self.default_ttl = default_ttl
        self.cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()  # key -> (value, expires_at)
        self.hits = 0
        self.misses = 0
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self.cache:
                value, expires_at = self.cache[key]
                if time.time() > expires_at:
                    del self.cache[key]
                    self.misses += 1
                    return None
                self.cache.move_to_end(key)
                self.hits += 1
                return value
            self.misses += 1
            return None

    def put(self, key: str, value: Any, ttl: float = None):
        if ttl is None:
            ttl = self.default_ttl
        with self._lock:
            expires_at = time.time() + ttl
            if key in self.cache:
                self.cache.move_to_end(key)
                self.cache[key] = (value, expires_at)
            else:
                self._evict_expired()
                if len(self.cache) >= self.capacity:
                    self.cache.popitem(last=False)
                self.cache[key] = (value, expires_at)

    def _evict_expired(self):
        now = time.time()
        expired = [k for k, (_, exp) in self.cache.items() if now > exp]
        for k in expired:
            del self.cache[k]

    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


# ===================================================================
# 4. Single-Flight (Cache Stampede Prevention)
# ===================================================================
class SingleFlight:
    """Ensures only one thread fetches a key at a time."""

    def __init__(self):
        self._inflight: dict[str, threading.Event] = {}
        self._results: dict[str, Any] = {}
        self._lock = threading.Lock()

    def do(self, key: str, fetch_fn: Callable[[], Any]) -> Any:
        is_leader = False
        with self._lock:
            if key in self._inflight:
                event = self._inflight[key]
            else:
                event = threading.Event()
                self._inflight[key] = event
                is_leader = True

        if not is_leader:
            event.wait(timeout=10)
            return self._results.get(key)

        # Leader fetches
        try:
            result = fetch_fn()
            self._results[key] = result
        finally:
            event.set()
            with self._lock:
                self._inflight.pop(key, None)

        return result


# ===================================================================
# 5. Cache Cluster (with consistent hashing)
# ===================================================================
class CacheNode:
    def __init__(self, node_id: str, capacity: int = 1000):
        self.node_id = node_id
        self.cache = TTLCache(capacity=capacity, default_ttl=300)
        self.is_healthy = True

    def get(self, key: str) -> Optional[Any]:
        return self.cache.get(key)

    def put(self, key: str, value: Any, ttl: float = None):
        self.cache.put(key, value, ttl)


class CacheCluster:
    def __init__(self, virtual_nodes: int = 50):
        self.virtual_nodes = virtual_nodes
        self.ring: list[int] = []
        self.hash_to_node: dict[int, str] = {}
        self.nodes: dict[str, CacheNode] = {}
        self.single_flight = SingleFlight()
        self._stats = {"gets": 0, "puts": 0, "hits": 0, "misses": 0, "stampede_prevented": 0}

    def _hash(self, key: str) -> int:
        return int(hashlib.md5(key.encode()).hexdigest(), 16)

    def add_node(self, node: CacheNode):
        self.nodes[node.node_id] = node
        for i in range(self.virtual_nodes):
            h = self._hash(f"{node.node_id}#vn{i}")
            bisect.insort(self.ring, h)
            self.hash_to_node[h] = node.node_id

    def _get_node(self, key: str) -> Optional[CacheNode]:
        if not self.ring:
            return None
        h = self._hash(key)
        idx = bisect.bisect_right(self.ring, h)
        if idx == len(self.ring):
            idx = 0
        node_id = self.hash_to_node[self.ring[idx]]
        node = self.nodes.get(node_id)
        if node and node.is_healthy:
            return node
        return None

    def get(self, key: str) -> Optional[Any]:
        self._stats["gets"] += 1
        node = self._get_node(key)
        if not node:
            return None
        result = node.get(key)
        if result is not None:
            self._stats["hits"] += 1
        else:
            self._stats["misses"] += 1
        return result

    def put(self, key: str, value: Any, ttl: float = None):
        self._stats["puts"] += 1
        node = self._get_node(key)
        if node:
            node.put(key, value, ttl)

    def get_or_fetch(self, key: str, fetch_fn: Callable[[], Any], ttl: float = None) -> Any:
        """Cache-aside with stampede prevention."""
        cached = self.get(key)
        if cached is not None:
            return cached

        # Single-flight: only one thread fetches
        result = self.single_flight.do(key, fetch_fn)
        if result is not None:
            self.put(key, result, ttl)
        return result

    def stats(self) -> dict:
        hit_rate = self._stats["hits"] / max(1, self._stats["gets"])
        return {
            **self._stats,
            "hit_rate": f"{hit_rate:.1%}",
            "nodes": len(self.nodes),
            "healthy_nodes": sum(1 for n in self.nodes.values() if n.is_healthy),
        }


# ===================================================================
# Demo
# ===================================================================
def simulated_db_fetch(key: str) -> str:
    """Simulate expensive DB query."""
    time.sleep(0.01)  # 10ms
    return f"db_value_for_{key}"


if __name__ == "__main__":
    print("=" * 65)
    print("  Distributed Cache — LRU/LFU/TTL + Cluster + Stampede Prevention")
    print("=" * 65)

    # --- LRU Cache ---
    print("\n  === LRU Cache (capacity=5) ===")
    lru = LRUCache(capacity=5)
    for i in range(7):
        lru.put(f"key_{i}", f"value_{i}")
    print(f"  After inserting 7 items: size={lru.size()} (cap=5)")
    print(f"  key_0 (evicted): {lru.get('key_0')}")
    print(f"  key_1 (evicted): {lru.get('key_1')}")
    print(f"  key_5 (present): {lru.get('key_5')}")
    print(f"  Hit rate: {lru.hit_rate():.1%}")

    # --- LFU Cache ---
    print("\n  === LFU Cache (capacity=3) ===")
    lfu = LFUCache(capacity=3)
    lfu.put("a", 1)
    lfu.put("b", 2)
    lfu.put("c", 3)
    lfu.get("a")  # freq(a)=2
    lfu.get("a")  # freq(a)=3
    lfu.get("b")  # freq(b)=2
    lfu.put("d", 4)  # evicts c (freq=1)
    print(f"  After a(3x), b(2x), c(1x), insert d → c evicted")
    print(f"  a={lfu.get('a')}  b={lfu.get('b')}  c={lfu.get('c')}  d={lfu.get('d')}")

    # --- TTL Cache ---
    print("\n  === TTL Cache ===")
    ttl_cache = TTLCache(capacity=10, default_ttl=0.5)
    ttl_cache.put("temp", "expires_in_500ms")
    print(f"  Immediately: {ttl_cache.get('temp')}")
    time.sleep(0.6)
    print(f"  After 600ms: {ttl_cache.get('temp')}")

    # --- Cache Cluster ---
    print("\n  === Cache Cluster (3 nodes) ===")
    cluster = CacheCluster(virtual_nodes=50)
    for i in range(3):
        cluster.add_node(CacheNode(f"cache-{i}", capacity=500))

    # Write keys
    for i in range(100):
        cluster.put(f"user:{i}", {"name": f"User {i}", "score": i * 10})

    # Read keys
    for i in range(100):
        cluster.get(f"user:{i}")

    # Check distribution
    node_counts = defaultdict(int)
    for i in range(1000):
        node = cluster._get_node(f"key_{i}")
        if node:
            node_counts[node.node_id] += 1
    print(f"  Key distribution across nodes:")
    for nid, count in sorted(node_counts.items()):
        print(f"    {nid}: {count} keys ({count/10:.1f}%)")

    print(f"\n  Cluster stats: {cluster.stats()}")

    # --- Stampede Prevention ---
    print("\n  === Stampede Prevention ===")
    fetch_count = {"count": 0}

    def slow_fetch():
        fetch_count["count"] += 1
        time.sleep(0.05)
        return "expensive_result"

    results = []
    def fetch_via_cluster(idx):
        r = cluster.get_or_fetch("hot_key", slow_fetch, ttl=60)
        results.append(r)

    # 10 threads requesting same key simultaneously
    threads = [threading.Thread(target=fetch_via_cluster, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print(f"  10 concurrent requests for 'hot_key':")
    print(f"    Actual DB fetches: {fetch_count['count']} (should be 1)")
    print(f"    All received result: {all(r == 'expensive_result' for r in results)}")

    # --- Comparison ---
    print("\n  === Algorithm Comparison (10000 Zipf-like accesses) ===")
    import random
    random.seed(42)

    # Generate Zipf-like access pattern
    keys = [f"item_{i}" for i in range(100)]
    accesses = []
    for _ in range(10000):
        # Power-law: first items accessed much more
        idx = int(abs(random.gauss(0, 15))) % 100
        accesses.append(keys[idx])

    for name, cache_obj in [("LRU(50)", LRUCache(50)), ("LFU(50)", LFUCache(50)), ("TTL(50)", TTLCache(50))]:
        for key in accesses:
            if cache_obj.get(key) is None:
                cache_obj.put(key, f"val_{key}")
        print(f"    {name:12s}  hit_rate={cache_obj.hit_rate():.1%}")

    print("\nDone.")
