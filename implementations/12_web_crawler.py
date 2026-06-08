"""
=============================================================
  12. Web Crawler — Async BFS with Bloom Filter & SimHash
  Run: python 12_web_crawler.py
  Implements BFS URL frontier, Bloom filter dedup, SimHash
  near-duplicate detection, robots.txt politeness, and
  concurrent crawling. Uses simulated pages (no network).
=============================================================
"""
import hashlib
import time
import re
import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse, urljoin


# ===================================================================
# Bloom Filter (for URL dedup)
# ===================================================================
class BloomFilter:
    def __init__(self, size: int = 100000, num_hashes: int = 7):
        self.size = size
        self.num_hashes = num_hashes
        self.bits = bytearray(size)

    def _hashes(self, item: str):
        h1 = int(hashlib.md5(item.encode()).hexdigest(), 16)
        h2 = int(hashlib.sha1(item.encode()).hexdigest(), 16)
        for i in range(self.num_hashes):
            yield (h1 + i * h2) % self.size

    def add(self, item: str):
        for pos in self._hashes(item):
            self.bits[pos] = 1

    def contains(self, item: str) -> bool:
        return all(self.bits[pos] for pos in self._hashes(item))


# ===================================================================
# SimHash (near-duplicate detection)
# ===================================================================
class SimHash:
    def __init__(self, num_bits: int = 64):
        self.num_bits = num_bits

    def _token_hash(self, token: str) -> int:
        return int(hashlib.md5(token.encode()).hexdigest(), 16) % (2 ** self.num_bits)

    def compute(self, text: str) -> int:
        tokens = text.lower().split()
        vectors = [0] * self.num_bits
        for token in tokens:
            h = self._token_hash(token)
            for i in range(self.num_bits):
                if h & (1 << i):
                    vectors[i] += 1
                else:
                    vectors[i] -= 1
        fingerprint = 0
        for i in range(self.num_bits):
            if vectors[i] > 0:
                fingerprint |= (1 << i)
        return fingerprint

    @staticmethod
    def hamming_distance(h1: int, h2: int) -> int:
        diff = h1 ^ h2
        return bin(diff).count("1")

    def is_near_duplicate(self, h1: int, h2: int, threshold: int = 3) -> bool:
        return self.hamming_distance(h1, h2) <= threshold


# ===================================================================
# URL Frontier (priority BFS queue)
# ===================================================================
class URLFrontier:
    def __init__(self):
        self.queues: dict[str, deque] = defaultdict(deque)  # domain -> queue
        self.domain_last_access: dict[str, float] = {}
        self.politeness_delay = 1.0  # seconds between requests to same domain
        self._total = 0

    def add(self, url: str, priority: int = 0):
        domain = urlparse(url).netloc
        self.queues[domain].append((priority, url))
        self._total += 1

    def get_next(self) -> Optional[str]:
        now = time.time()
        for domain in list(self.queues.keys()):
            last = self.domain_last_access.get(domain, 0)
            if now - last >= self.politeness_delay:
                queue = self.queues[domain]
                if queue:
                    _, url = queue.popleft()
                    self.domain_last_access[domain] = now
                    self._total -= 1
                    if not queue:
                        del self.queues[domain]
                    return url
        return None

    def is_empty(self) -> bool:
        return self._total == 0


# ===================================================================
# Robots.txt Parser (simplified)
# ===================================================================
class RobotsParser:
    def __init__(self):
        self.rules: dict[str, list[str]] = {}  # domain -> [disallowed paths]

    def add_rules(self, domain: str, disallowed: list[str]):
        self.rules[domain] = disallowed

    def is_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        disallowed = self.rules.get(parsed.netloc, [])
        for path in disallowed:
            if parsed.path.startswith(path):
                return False
        return True


# ===================================================================
# Simulated Web (no actual network)
# ===================================================================
SIMULATED_WEB = {
    "https://example.com/": {
        "content": "Welcome to Example. System Design tutorials available here.",
        "links": ["https://example.com/tutorials", "https://example.com/about",
                  "https://example.com/blog"],
    },
    "https://example.com/tutorials": {
        "content": "Learn system design: URL shortener, rate limiter, cache, message queue.",
        "links": ["https://example.com/tutorials/url-shortener",
                  "https://example.com/tutorials/rate-limiter",
                  "https://example.com/"],
    },
    "https://example.com/tutorials/url-shortener": {
        "content": "URL Shortener design: Use Base62 encoding with distributed ID generation.",
        "links": ["https://example.com/tutorials"],
    },
    "https://example.com/tutorials/rate-limiter": {
        "content": "Rate Limiter: Token bucket and sliding window algorithms for API protection.",
        "links": ["https://example.com/tutorials"],
    },
    "https://example.com/about": {
        "content": "About us: We teach system design for software engineers at top companies.",
        "links": ["https://example.com/", "https://example.com/contact"],
    },
    "https://example.com/contact": {
        "content": "Contact us at hello@example.com for system design mentorship.",
        "links": ["https://example.com/"],
    },
    "https://example.com/blog": {
        "content": "Blog: Latest posts on distributed systems and microservices architecture.",
        "links": ["https://example.com/blog/post-1", "https://example.com/blog/post-2"],
    },
    "https://example.com/blog/post-1": {
        "content": "Distributed systems: CAP theorem, consistency models, and partition tolerance.",
        "links": ["https://example.com/blog"],
    },
    "https://example.com/blog/post-2": {
        # Near-duplicate of post-1 for SimHash testing
        "content": "Distributed systems: CAP theorem, consistency models, and partition handling.",
        "links": ["https://example.com/blog"],
    },
    "https://example.com/admin": {
        "content": "Admin panel (should be blocked by robots.txt)",
        "links": [],
    },
}


async def simulated_fetch(url: str) -> Optional[dict]:
    """Simulate network fetch with delay."""
    await asyncio.sleep(0.01)  # simulate network latency
    return SIMULATED_WEB.get(url)


# ===================================================================
# Web Crawler
# ===================================================================
class WebCrawler:
    def __init__(self, max_pages: int = 100, max_depth: int = 5):
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.frontier = URLFrontier()
        self.bloom = BloomFilter()
        self.simhash = SimHash()
        self.robots = RobotsParser()
        self.crawled: dict[str, dict] = {}
        self.fingerprints: dict[str, int] = {}  # url -> simhash
        self.near_duplicates: list[tuple[str, str]] = []
        self._stats = {
            "urls_discovered": 0, "urls_crawled": 0, "urls_filtered": 0,
            "robots_blocked": 0, "near_dupes_found": 0,
        }

    def add_seed(self, url: str):
        if not self.bloom.contains(url):
            self.bloom.add(url)
            self.frontier.add(url)
            self._stats["urls_discovered"] += 1

    async def crawl(self):
        """Main crawl loop."""
        while not self.frontier.is_empty() and self._stats["urls_crawled"] < self.max_pages:
            url = self.frontier.get_next()
            if not url:
                await asyncio.sleep(0.01)
                continue

            # Robots check
            if not self.robots.is_allowed(url):
                self._stats["robots_blocked"] += 1
                continue

            # Fetch page
            page = await simulated_fetch(url)
            if not page:
                continue

            content = page["content"]
            links = page.get("links", [])

            # SimHash dedup
            fp = self.simhash.compute(content)
            is_dupe = False
            for other_url, other_fp in self.fingerprints.items():
                if self.simhash.is_near_duplicate(fp, other_fp, threshold=3):
                    self.near_duplicates.append((url, other_url))
                    self._stats["near_dupes_found"] += 1
                    is_dupe = True
                    break

            self.fingerprints[url] = fp
            self.crawled[url] = {
                "content": content[:200],
                "links_found": len(links),
                "simhash": fp,
                "is_near_duplicate": is_dupe,
            }
            self._stats["urls_crawled"] += 1

            # Extract and enqueue links
            for link in links:
                if not self.bloom.contains(link):
                    self.bloom.add(link)
                    self.frontier.add(link)
                    self._stats["urls_discovered"] += 1
                else:
                    self._stats["urls_filtered"] += 1

    def stats(self) -> dict:
        return self._stats


# ===================================================================
# Demo
# ===================================================================
async def main():
    print("=" * 65)
    print("  Web Crawler — BFS + Bloom Filter + SimHash")
    print("=" * 65)

    crawler = WebCrawler(max_pages=50)

    # Set robots rules
    crawler.robots.add_rules("example.com", ["/admin"])
    print("\n  Robots rules: /admin blocked")

    # Seed URLs
    crawler.add_seed("https://example.com/")
    print("  Seed: https://example.com/")

    # Crawl
    print("\n  Crawling...")
    start = time.time()
    await crawler.crawl()
    elapsed = time.time() - start

    # Results
    print(f"\n  Crawled {len(crawler.crawled)} pages in {elapsed:.2f}s")
    print(f"\n  {'URL':55s} {'Links':>5s}  {'SimHash':>16s}  Dupe?")
    print(f"  {'-'*55} {'-'*5}  {'-'*16}  -----")
    for url, info in sorted(crawler.crawled.items()):
        dupe = "YES" if info["is_near_duplicate"] else ""
        print(f"  {url:55s} {info['links_found']:5d}  {info['simhash']:16d}  {dupe}")

    # Near duplicates
    if crawler.near_duplicates:
        print(f"\n  Near-duplicate pairs detected:")
        for url1, url2 in crawler.near_duplicates:
            h1, h2 = crawler.fingerprints[url1], crawler.fingerprints[url2]
            dist = SimHash.hamming_distance(h1, h2)
            print(f"    {url1}")
            print(f"    ↔ {url2}  (hamming distance={dist})")

    # Bloom filter check
    print(f"\n  Bloom filter checks:")
    print(f"    'https://example.com/' seen: {crawler.bloom.contains('https://example.com/')}")
    print(f"    'https://unknown.com/' seen: {crawler.bloom.contains('https://unknown.com/')}")

    # Stats
    print(f"\n  Crawler Stats: {crawler.stats()}")
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
