# 19. Distributed Cache (Redis/Memcached-like)

> **Difficulty**: Hard | **Asked by**: Amazon, Google, Netflix, Meta, Twitter

## Table of Contents
- [Requirements](#requirements)
- [Capacity Estimation](#capacity-estimation)
- [High-Level Design](#high-level-design)
- [Low-Level Design](#low-level-design)
- [Implementation](#implementation)
- [Limitations & Improvements](#limitations--improvements)

---

## Requirements

### Functional Requirements
1. `GET(key)` — Retrieve cached value by key
2. `SET(key, value, TTL)` — Store key-value with optional expiration
3. `DELETE(key)` — Remove a key
4. Support data structures (strings, lists, sets, hashes)
5. Atomic operations (increment, CAS)
6. Pub/Sub for cache invalidation

### Non-Functional Requirements
1. **Ultra-Low Latency**: < 1ms for reads, < 2ms for writes
2. **High Throughput**: 1M+ operations/second per node
3. **High Availability**: 99.99%, automatic failover
4. **Scalability**: Linear scaling with more nodes
5. **Tunable Consistency**: Strong within partition, eventual across replicas

---

## Capacity Estimation

```
Cached entries: 10 Billion key-value pairs
Average key size: 100 bytes
Average value size: 1 KB
Total memory: 10B × 1.1 KB ≈ 11 TB
Operations: 10M ops/sec cluster-wide
Nodes (64 GB each): 11 TB / 64 GB ≈ 172 nodes + replicas
With replication (factor 2): ~344 nodes
Network: 10M × 1KB = 10 GB/sec
```

---

## High-Level Design

### Architecture Overview

```mermaid
graph TB
    subgraph "Application Layer"
        App1[App Server 1]
        App2[App Server 2]
        App3[App Server N]
    end
    
    App1 & App2 & App3 --> Client[Cache Client Library<br/>Consistent Hashing + Connection Pool]
    
    Client --> N1[Cache Node 1<br/>Shard A]
    Client --> N2[Cache Node 2<br/>Shard B]
    Client --> N3[Cache Node 3<br/>Shard C]
    Client --> NN[Cache Node N<br/>Shard N]
    
    N1 --> R1[Replica 1-A]
    N2 --> R2[Replica 2-A]
    N3 --> R3[Replica 3-A]
    
    CM[Cluster Manager<br/>ZooKeeper/etcd] --> N1 & N2 & N3 & NN
    
    subgraph "Each Cache Node"
        direction TB
        HashTable["Hash Table<br/>(In-Memory)"]
        ExpHeap["Expiration Heap<br/>(Min-Heap by TTL)"]
        EvictList["LRU Eviction List<br/>(Doubly Linked List)"]
    end
```

### Cache-Aside Pattern (Most Common)

```mermaid
sequenceDiagram
    participant App as Application
    participant Cache as Cache
    participant DB as Database
    
    App->>Cache: GET user:123
    
    alt Cache Hit (99% of time)
        Cache-->>App: User data ✅
    else Cache Miss
        Cache-->>App: null
        App->>DB: SELECT * FROM users WHERE id=123
        DB-->>App: User data
        App->>Cache: SET user:123 (TTL: 1h)
        App->>App: Return data
    end
```

### Write Strategies

```mermaid
graph TD
    subgraph "Write-Through"
        WT1[App writes to Cache] --> WT2[Cache writes to DB]
        WT2 --> WT3["✅ Cache always consistent<br/>❌ Higher write latency"]
    end
    
    subgraph "Write-Behind (Write-Back)"
        WB1[App writes to Cache] --> WB2[Cache ACKs immediately]
        WB2 --> WB3[Cache async writes to DB<br/>batched, delayed]
        WB3 --> WB4["✅ Fast writes<br/>❌ Risk of data loss"]
    end
    
    subgraph "Write-Around"
        WA1[App writes to DB directly] --> WA2[Cache NOT updated]
        WA2 --> WA3["Next read: cache miss → reload<br/>✅ No cache pollution<br/>❌ Higher read latency initially"]
    end
    
    subgraph "Cache-Aside (lazy) ✅"
        CA1[Read: Check cache → miss → DB → fill cache]
        CA2[Write: Update DB → invalidate cache]
        CA3["✅ Simple, widely used<br/>❌ Cache stampede possible"]
    end
```

---

## Low-Level Design

### Hash Table Design

```mermaid
graph TD
    subgraph "Core Data Structure"
        HT["Hash Table<br/>Open Addressing or Chaining"]
        
        HT --> B0["Bucket 0 → {key: 'session:abc', value: ..., next}"]
        HT --> B1["Bucket 1 → {key: 'user:123', value: ..., next}"]
        HT --> BN["Bucket N → null"]
        
        B0 --> Chain0["→ {key: 'token:xyz', value: ...}"]
    end
    
    subgraph "Entry Structure"
        Entry["CacheEntry {<br/>  key: bytes<br/>  value: bytes<br/>  hash: uint64<br/>  ttl: timestamp<br/>  lru_prev: *Entry<br/>  lru_next: *Entry<br/>  expire_heap_idx: int<br/>}"]
    end
```

### Eviction Policies

```mermaid
graph TD
    subgraph "LRU (Least Recently Used) ✅"
        LRU1["Doubly Linked List + HashMap"]
        LRU2["Access → move to head"]
        LRU3["Evict → remove from tail"]
        LRU4["O(1) for all operations"]
    end
    
    subgraph "LFU (Least Frequently Used)"
        LFU1["Frequency counter per key"]
        LFU2["Evict key with lowest frequency"]
        LFU3["Better for skewed workloads"]
        LFU4["More memory overhead"]
    end
    
    subgraph "TTL-Based"
        TTL1["Min-heap sorted by expiration"]
        TTL2["Background thread checks heap"]
        TTL3["Lazy deletion on access"]
    end
    
    subgraph "W-TinyLFU (Caffeine) ✅"
        WL1["Admission filter + LRU window"]
        WL2["Best hit rate among all policies"]
        WL3["Used by Caffeine (Java)"]
    end
```

### LRU + Hash Table Implementation

```mermaid
graph LR
    subgraph "HashMap"
        H["key → Node pointer"]
    end
    
    subgraph "Doubly Linked List (LRU Order)"
        Head["HEAD<br/>(most recent)"] <--> A["Key: user:1<br/>Value: ..."]
        A <--> B["Key: sess:2<br/>Value: ..."]
        B <--> C["Key: data:3<br/>Value: ..."]
        C <--> Tail["TAIL<br/>(least recent)<br/>← evict here"]
    end
    
    H --> A
    H --> B
    H --> C
```

### Cluster Topology

```mermaid
graph TB
    subgraph "Hash Slot Distribution (Redis Cluster Style)"
        Slots["16384 hash slots"]
        
        N1["Node 1<br/>Slots 0-5460<br/>Master"]
        N2["Node 2<br/>Slots 5461-10922<br/>Master"]
        N3["Node 3<br/>Slots 10923-16383<br/>Master"]
        
        R1["Replica 1<br/>Copy of Node 1"]
        R2["Replica 2<br/>Copy of Node 2"]
        R3["Replica 3<br/>Copy of Node 3"]
        
        N1 --> R1
        N2 --> R2
        N3 --> R3
    end
    
    subgraph "Failover"
        N1 -->|Fails| Detect["Cluster detects failure<br/>(ping/pong gossip)"]
        Detect --> Promote["Promote Replica 1<br/>to new Master"]
        Promote --> Reconfig["Cluster reconfigures<br/>slot ownership"]
    end
```

### Cache Stampede Prevention

```mermaid
flowchart TD
    subgraph "Problem: Cache Stampede"
        Expire["Popular key expires"] --> Miss1["Thread 1: cache miss → query DB"]
        Expire --> Miss2["Thread 2: cache miss → query DB"]
        Expire --> Miss3["Thread 1000: cache miss → query DB"]
        Miss1 & Miss2 & Miss3 --> Overload["DB overloaded! 💀"]
    end
    
    subgraph "Solution 1: Lock (Single Flight)"
        S1["First thread: acquire lock → query DB → fill cache"]
        S2["Other threads: wait for lock → read cache"]
    end
    
    subgraph "Solution 2: Probabilistic Early Expiry"
        S3["Refresh before actual expiry<br/>TTL - random(0, buffer)"]
    end
    
    subgraph "Solution 3: Background Refresh"
        S4["Background thread refreshes<br/>before expiry"]
    end
```

---

## Implementation

### LRU Cache with TTL

```python
import time
import threading
import heapq
from collections import OrderedDict
from typing import Optional, Any

class LRUCache:
    """Thread-safe LRU cache with TTL support."""
    
    def __init__(self, max_size: int = 10000, default_ttl: int = 3600):
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.cache = OrderedDict()  # key → (value, expire_at)
        self.lock = threading.RLock()
        self.stats = {"hits": 0, "misses": 0, "evictions": 0}
    
    def get(self, key: str) -> Optional[Any]:
        """Get value by key. O(1)."""
        with self.lock:
            if key not in self.cache:
                self.stats["misses"] += 1
                return None
            
            value, expire_at = self.cache[key]
            
            # Check TTL
            if expire_at and time.time() > expire_at:
                del self.cache[key]
                self.stats["misses"] += 1
                return None
            
            # Move to end (most recently used)
            self.cache.move_to_end(key)
            self.stats["hits"] += 1
            return value
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """Set key-value with optional TTL. O(1)."""
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            
            expire_at = time.time() + (ttl or self.default_ttl) if (ttl or self.default_ttl) else None
            self.cache[key] = (value, expire_at)
            
            # Evict if over capacity
            while len(self.cache) > self.max_size:
                evicted_key, _ = self.cache.popitem(last=False)
                self.stats["evictions"] += 1
    
    def delete(self, key: str) -> bool:
        """Delete a key. O(1)."""
        with self.lock:
            if key in self.cache:
                del self.cache[key]
                return True
            return False
    
    def get_or_set(self, key: str, loader, ttl: Optional[int] = None):
        """Get from cache or load from source (prevents stampede)."""
        value = self.get(key)
        if value is not None:
            return value
        
        # Single-flight: only one thread loads
        with self.lock:
            # Double-check after acquiring lock
            value = self.get(key)
            if value is not None:
                return value
            
            value = loader()
            self.set(key, value, ttl)
            return value


class DistributedCache:
    """Client-side distributed cache with consistent hashing."""
    
    def __init__(self, nodes: list, virtual_nodes: int = 150):
        from consistent_hashing import ConsistentHashRing
        self.ring = ConsistentHashRing(virtual_nodes)
        self.connections = {}
        
        for node in nodes:
            self.ring.add_node(node)
            self.connections[node] = self._connect(node)
    
    def get(self, key: str) -> Optional[Any]:
        node = self.ring.get_node(key)
        conn = self.connections[node]
        return conn.get(key)
    
    def set(self, key: str, value: Any, ttl: int = 3600):
        node = self.ring.get_node(key)
        conn = self.connections[node]
        conn.set(key, value, ex=ttl)
    
    def delete(self, key: str):
        node = self.ring.get_node(key)
        conn = self.connections[node]
        conn.delete(key)
    
    def get_multi(self, keys: list) -> dict:
        """Batch get from multiple nodes."""
        # Group keys by node
        node_keys = {}
        for key in keys:
            node = self.ring.get_node(key)
            node_keys.setdefault(node, []).append(key)
        
        # Parallel fetch from each node
        results = {}
        for node, node_key_list in node_keys.items():
            conn = self.connections[node]
            values = conn.mget(node_key_list)
            for k, v in zip(node_key_list, values):
                if v is not None:
                    results[k] = v
        
        return results
    
    def _connect(self, node):
        import redis
        host, port = node.split(":")
        return redis.Redis(host=host, port=int(port), 
                          decode_responses=True,
                          socket_connect_timeout=2)
```

---

## Limitations & Improvements

### Current Limitations

| Limitation | Impact | Severity |
|-----------|--------|----------|
| Memory-only (volatile) | Data lost on restart | High |
| Cold start after failure | Cache miss storm hits DB | High |
| Cross-datacenter consistency | Stale data in remote regions | Medium |
| Hot key problem | Single node overloaded | High |
| Large values degrade performance | Network + serialization overhead | Medium |

### Improvement Areas

1. **Persistence** — AOF (Append-Only File) + RDB snapshots for recovery
2. **Hot Key Mitigation** — Client-side local cache + key replication across nodes
3. **Multi-tier Cache** — L1 (local process) → L2 (Redis) → L3 (database)
4. **Cache Warming** — Pre-populate cache on deployment using previous traffic patterns
5. **Compression** — Compress large values (zstd/snappy) to reduce memory and network

---

## Key Interview Discussion Points

1. **When NOT to cache?** Write-heavy data, highly dynamic data, large objects, data requiring strong consistency
2. **LRU vs LFU?** LRU for general workloads; LFU when few keys are extremely popular
3. **How to handle cache invalidation?** TTL-based (simple) + event-driven (accurate) + versioned keys
4. **Redis vs Memcached?** Redis: data structures, persistence, replication; Memcached: simpler, multi-threaded
5. **How to prevent thundering herd?** Locking (single-flight), probabilistic early refresh, stale-while-revalidate
