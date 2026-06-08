"""
=============================================================
  3. Consistent Hashing — Hash Ring with Virtual Nodes
  Run: python 03_consistent_hashing.py
  Demonstrates adding/removing nodes, key distribution,
  and load balancing statistics.
=============================================================
"""
import hashlib
import bisect
from collections import defaultdict


class ConsistentHashRing:
    """
    Consistent hashing ring with virtual nodes.
    Uses MD5 for hash distribution (not security-critical).
    """

    def __init__(self, virtual_nodes: int = 150):
        self.virtual_nodes = virtual_nodes
        self.ring: list[int] = []                  # sorted hash positions
        self.hash_to_node: dict[int, str] = {}     # hash position -> physical node
        self.nodes: set[str] = set()

    def _hash(self, key: str) -> int:
        return int(hashlib.md5(key.encode()).hexdigest(), 16)

    def add_node(self, node: str) -> list[int]:
        """Add a node with its virtual nodes. Returns list of hash positions."""
        if node in self.nodes:
            return []
        self.nodes.add(node)
        positions = []
        for i in range(self.virtual_nodes):
            vnode_key = f"{node}#vn{i}"
            h = self._hash(vnode_key)
            bisect.insort(self.ring, h)
            self.hash_to_node[h] = node
            positions.append(h)
        return positions

    def remove_node(self, node: str) -> list[int]:
        """Remove a node and all its virtual nodes."""
        if node not in self.nodes:
            return []
        self.nodes.discard(node)
        removed = []
        for i in range(self.virtual_nodes):
            vnode_key = f"{node}#vn{i}"
            h = self._hash(vnode_key)
            self.ring.remove(h)
            del self.hash_to_node[h]
            removed.append(h)
        return removed

    def get_node(self, key: str) -> str | None:
        """Find the node responsible for the given key."""
        if not self.ring:
            return None
        h = self._hash(key)
        idx = bisect.bisect_right(self.ring, h)
        if idx == len(self.ring):
            idx = 0
        return self.hash_to_node[self.ring[idx]]

    def get_replicas(self, key: str, count: int = 3) -> list[str]:
        """Get N distinct replica nodes for a key (for replication)."""
        if not self.ring:
            return []
        h = self._hash(key)
        idx = bisect.bisect_right(self.ring, h)
        replicas = []
        seen = set()
        for i in range(len(self.ring)):
            pos = (idx + i) % len(self.ring)
            node = self.hash_to_node[self.ring[pos]]
            if node not in seen:
                seen.add(node)
                replicas.append(node)
                if len(replicas) >= count:
                    break
        return replicas

    def distribution_stats(self, num_keys: int = 10000) -> dict[str, int]:
        """Simulate key distribution to measure load balancing."""
        counts: dict[str, int] = defaultdict(int)
        for i in range(num_keys):
            node = self.get_node(f"key_{i}")
            if node:
                counts[node] += 1
        return dict(counts)


# ===================================================================
# Demo
# ===================================================================
def print_distribution(ring: ConsistentHashRing, label: str, num_keys: int = 10000):
    stats = ring.distribution_stats(num_keys)
    total = sum(stats.values())
    ideal = total / len(stats) if stats else 0

    print(f"\n  {label}")
    print(f"  {'Node':20s} {'Keys':>8s} {'Share':>8s} {'Deviation':>10s}")
    print(f"  {'-'*20} {'-'*8} {'-'*8} {'-'*10}")

    for node in sorted(stats.keys()):
        count = stats[node]
        share = count / total * 100
        dev = (count - ideal) / ideal * 100
        print(f"  {node:20s} {count:8d} {share:7.1f}% {dev:+9.1f}%")

    max_count = max(stats.values())
    min_count = min(stats.values())
    print(f"  Spread: min={min_count}, max={max_count}, ratio={max_count/max(min_count,1):.2f}x")


def demo_key_migration(ring: ConsistentHashRing, keys: list[str], new_node: str):
    """Show how few keys migrate when a node is added."""
    before = {k: ring.get_node(k) for k in keys}
    ring.add_node(new_node)
    after = {k: ring.get_node(k) for k in keys}
    migrated = sum(1 for k in keys if before[k] != after[k])
    print(f"  Added '{new_node}': {migrated}/{len(keys)} keys migrated "
          f"({migrated/len(keys)*100:.1f}%)")
    return migrated


if __name__ == "__main__":
    print("=" * 65)
    print("  Consistent Hashing — Hash Ring Demo")
    print("=" * 65)

    # ----- Basic ring with 5 nodes -----
    ring = ConsistentHashRing(virtual_nodes=150)
    servers = ["server-A", "server-B", "server-C", "server-D", "server-E"]
    for s in servers:
        ring.add_node(s)

    print_distribution(ring, "Initial: 5 servers, 150 vnodes each")

    # ----- Key lookup -----
    print("\n  Key -> Node mapping examples:")
    test_keys = ["user:1001", "user:1002", "session:abc", "cache:home_page", "order:9999"]
    for k in test_keys:
        node = ring.get_node(k)
        replicas = ring.get_replicas(k, 3)
        print(f"    {k:25s} → primary={node}, replicas={replicas}")

    # ----- Add a node: measure migration -----
    print("\n  --- Node Addition (key migration test) ---")
    all_keys = [f"key_{i}" for i in range(10000)]
    demo_key_migration(ring, all_keys, "server-F")
    print_distribution(ring, "After adding server-F (6 servers)")

    # ----- Remove a node: measure migration -----
    print("\n  --- Node Removal ---")
    before = {k: ring.get_node(k) for k in all_keys}
    ring.remove_node("server-C")
    after = {k: ring.get_node(k) for k in all_keys}
    migrated = sum(1 for k in all_keys if before[k] != after[k])
    print(f"  Removed 'server-C': {migrated}/{len(all_keys)} keys migrated "
          f"({migrated/len(all_keys)*100:.1f}%)")
    print_distribution(ring, "After removing server-C (5 servers)")

    # ----- Virtual node count comparison -----
    print("\n  --- Virtual Node Count Impact ---")
    for vn in [1, 10, 50, 150, 500]:
        r = ConsistentHashRing(virtual_nodes=vn)
        for s in servers:
            r.add_node(s)
        stats = r.distribution_stats(10000)
        max_c = max(stats.values())
        min_c = min(stats.values())
        print(f"  vnodes={vn:4d}  spread ratio={max_c/max(min_c,1):.2f}x  "
              f"(min={min_c}, max={max_c})")

    print("\nDone.")
