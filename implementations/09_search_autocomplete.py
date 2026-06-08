"""
=============================================================
  9. Search Autocomplete — Trie with Top-K & Fuzzy Matching
  Run: python 09_search_autocomplete.py
  Implements Trie, top-K precomputation, trending queries,
  and edit-distance fuzzy matching.
=============================================================
"""
import time
import heapq
from collections import defaultdict
from dataclasses import dataclass, field


# ===================================================================
# Trie Node & Trie
# ===================================================================
class TrieNode:
    __slots__ = ["children", "is_end", "frequency", "top_k"]

    def __init__(self):
        self.children: dict[str, TrieNode] = {}
        self.is_end: bool = False
        self.frequency: int = 0
        self.top_k: list[tuple[int, str]] = []  # [(-freq, word), ...]


class Trie:
    def __init__(self, top_k_size: int = 10):
        self.root = TrieNode()
        self.top_k_size = top_k_size
        self.total_words = 0

    def insert(self, word: str, frequency: int = 1):
        """Insert word and propagate top-K updates along the path."""
        node = self.root
        for char in word.lower():
            if char not in node.children:
                node.children[char] = TrieNode()
            node = node.children[char]
            self._update_top_k(node, word.lower(), frequency)
        node.is_end = True
        node.frequency += frequency
        self.total_words += 1

    def _update_top_k(self, node: TrieNode, word: str, freq: int):
        # Remove old entry for this word if present
        node.top_k = [(f, w) for f, w in node.top_k if w != word]
        # Insert new
        heapq.heappush(node.top_k, (freq, word))
        # Keep only top K (largest frequencies → use nlargest)
        if len(node.top_k) > self.top_k_size:
            node.top_k = heapq.nlargest(self.top_k_size, node.top_k)
            heapq.heapify(node.top_k)

    def search_prefix(self, prefix: str, limit: int = 10) -> list[tuple[str, int]]:
        """Return top-K suggestions for prefix."""
        node = self.root
        for char in prefix.lower():
            if char not in node.children:
                return []
            node = node.children[char]
        # Return sorted by frequency descending
        results = sorted(node.top_k, key=lambda x: -x[0])
        return [(word, freq) for freq, word in results[:limit]]

    def exact_search(self, word: str) -> int:
        """Return frequency of exact word, 0 if not found."""
        node = self.root
        for char in word.lower():
            if char not in node.children:
                return 0
            node = node.children[char]
        return node.frequency if node.is_end else 0


# ===================================================================
# Fuzzy Matcher (edit distance)
# ===================================================================
def edit_distance(s1: str, s2: str) -> int:
    m, n = len(s1), len(s2)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            temp = dp[j]
            if s1[i - 1] == s2[j - 1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j - 1])
            prev = temp
    return dp[n]


class FuzzyMatcher:
    def __init__(self, max_distance: int = 2):
        self.max_distance = max_distance
        self.dictionary: list[tuple[str, int]] = []

    def add_word(self, word: str, frequency: int = 1):
        self.dictionary.append((word.lower(), frequency))

    def match(self, query: str, limit: int = 5) -> list[tuple[str, int, int]]:
        """Return (word, frequency, distance) sorted by distance then frequency."""
        query = query.lower()
        candidates = []
        for word, freq in self.dictionary:
            dist = edit_distance(query, word)
            if dist <= self.max_distance:
                candidates.append((word, freq, dist))
        candidates.sort(key=lambda x: (x[2], -x[1]))
        return candidates[:limit]


# ===================================================================
# Trending Queries (time-windowed)
# ===================================================================
class TrendingTracker:
    def __init__(self, window_seconds: float = 3600):
        self.window = window_seconds
        self.queries: list[tuple[float, str]] = []
        self._counts: dict[str, int] = defaultdict(int)

    def record(self, query: str):
        now = time.time()
        self.queries.append((now, query.lower()))
        self._counts[query.lower()] += 1
        self._cleanup(now)

    def _cleanup(self, now: float):
        cutoff = now - self.window
        while self.queries and self.queries[0][0] < cutoff:
            _, q = self.queries.pop(0)
            self._counts[q] -= 1
            if self._counts[q] <= 0:
                del self._counts[q]

    def top_trending(self, limit: int = 10) -> list[tuple[str, int]]:
        self._cleanup(time.time())
        return sorted(self._counts.items(), key=lambda x: -x[1])[:limit]


# ===================================================================
# Autocomplete Service
# ===================================================================
class AutocompleteService:
    def __init__(self):
        self.trie = Trie(top_k_size=10)
        self.fuzzy = FuzzyMatcher(max_distance=2)
        self.trending = TrendingTracker(window_seconds=60)  # 60s for demo
        self._stats = {"queries": 0, "trie_hits": 0, "fuzzy_fallbacks": 0}

    def index(self, word: str, frequency: int = 1):
        self.trie.insert(word, frequency)
        self.fuzzy.add_word(word, frequency)

    def suggest(self, prefix: str, limit: int = 10) -> dict:
        self._stats["queries"] += 1
        self.trending.record(prefix)

        # 1. Trie prefix search
        trie_results = self.trie.search_prefix(prefix, limit)
        if trie_results:
            self._stats["trie_hits"] += 1
            return {
                "suggestions": [{"text": w, "score": f} for w, f in trie_results],
                "source": "trie",
            }

        # 2. Fuzzy fallback
        self._stats["fuzzy_fallbacks"] += 1
        fuzzy_results = self.fuzzy.match(prefix, limit)
        return {
            "suggestions": [
                {"text": w, "score": f, "distance": d}
                for w, f, d in fuzzy_results
            ],
            "source": "fuzzy",
        }

    def get_trending(self, limit: int = 10) -> list[dict]:
        return [{"query": q, "count": c} for q, c in self.trending.top_trending(limit)]


# ===================================================================
# Demo
# ===================================================================
if __name__ == "__main__":
    print("=" * 65)
    print("  Search Autocomplete — Trie + Top-K + Fuzzy")
    print("=" * 65)

    svc = AutocompleteService()

    # Index popular queries with frequencies
    queries = [
        ("system design", 50000), ("system design interview", 30000),
        ("system design primer", 20000), ("system architecture", 15000),
        ("python tutorial", 45000), ("python for beginners", 25000),
        ("python web scraping", 12000), ("python machine learning", 28000),
        ("javascript async await", 20000), ("javascript promises", 18000),
        ("java spring boot", 22000), ("java collections", 16000),
        ("docker tutorial", 19000), ("docker compose", 17000),
        ("kubernetes deployment", 14000), ("kubernetes pods", 11000),
        ("react hooks", 35000), ("react tutorial", 30000),
        ("react native", 25000), ("redis cache", 13000),
        ("redis pub sub", 9000), ("rate limiter", 8000),
        ("distributed systems", 21000), ("microservices", 18000),
    ]
    for q, freq in queries:
        svc.index(q, freq)

    # Prefix search
    print("\n  Prefix Search:")
    for prefix in ["sys", "python", "java", "re", "docker"]:
        result = svc.suggest(prefix, limit=5)
        suggestions = result["suggestions"]
        print(f"\n    '{prefix}' → ({result['source']})")
        for s in suggestions:
            print(f"      {s['text']:35s} score={s['score']}")

    # Fuzzy matching (typos)
    print("\n  Fuzzy Matching (typos):")
    for typo in ["pythn", "systm design", "reacr hooks", "kuberntes"]:
        result = svc.suggest(typo, limit=3)
        suggestions = result["suggestions"]
        print(f"\n    '{typo}' → ({result['source']})")
        for s in suggestions:
            dist = s.get("distance", 0)
            print(f"      {s['text']:35s} score={s['score']}  distance={dist}")

    # Trending
    print("\n  Trending Queries (simulated):")
    for _ in range(15):
        svc.trending.record("system design")
    for _ in range(10):
        svc.trending.record("python")
    for _ in range(7):
        svc.trending.record("react hooks")
    for _ in range(3):
        svc.trending.record("docker")

    trending = svc.get_trending(5)
    for t in trending:
        print(f"    {t['query']:30s} count={t['count']}")

    # Stats
    print(f"\n  Stats: {svc._stats}")
    print(f"  Trie total words: {svc.trie.total_words}")
    print(f"  Exact lookup 'system design': freq={svc.trie.exact_search('system design')}")
    print("\nDone.")
