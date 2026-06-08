"""
=============================================================
  2. Rate Limiter — Multiple Algorithms Demo
  Run: python 02_rate_limiter.py
  Demonstrates Token Bucket, Sliding Window Counter,
  Fixed Window Counter, and Leaking Bucket.
=============================================================
"""
import time
import threading
from collections import defaultdict


# ===================================================================
# 1. Token Bucket
# ===================================================================
class TokenBucket:
    """Classic token bucket: allows bursts up to bucket capacity."""

    def __init__(self, capacity: int = 10, refill_rate: float = 1.0):
        self.capacity = capacity
        self.refill_rate = refill_rate  # tokens per second
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.last_refill = now

            if self.tokens >= 1:
                self.tokens -= 1
                return True
            return False


# ===================================================================
# 2. Sliding Window Counter
# ===================================================================
class SlidingWindowCounter:
    """Weighted count from previous + current fixed window."""

    def __init__(self, limit: int = 10, window_seconds: float = 60.0):
        self.limit = limit
        self.window = window_seconds
        self._counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self._lock = threading.Lock()

    def allow(self, client_id: str = "default") -> bool:
        with self._lock:
            now = time.monotonic()
            current_window = int(now // self.window)
            prev_window = current_window - 1

            prev_count = self._counts[client_id].get(prev_window, 0)
            curr_count = self._counts[client_id].get(current_window, 0)

            elapsed = now - current_window * self.window
            weight = 1.0 - (elapsed / self.window)
            estimated = prev_count * weight + curr_count

            if estimated < self.limit:
                self._counts[client_id][current_window] += 1
                return True
            return False


# ===================================================================
# 3. Fixed Window Counter
# ===================================================================
class FixedWindowCounter:
    """Simple fixed window counter with boundary issue."""

    def __init__(self, limit: int = 10, window_seconds: float = 60.0):
        self.limit = limit
        self.window = window_seconds
        self._counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self._lock = threading.Lock()

    def allow(self, client_id: str = "default") -> bool:
        with self._lock:
            now = time.monotonic()
            window_key = int(now // self.window)
            count = self._counts[client_id][window_key]
            if count < self.limit:
                self._counts[client_id][window_key] += 1
                return True
            return False


# ===================================================================
# 4. Leaking Bucket
# ===================================================================
class LeakingBucket:
    """FIFO queue that processes at a fixed rate."""

    def __init__(self, capacity: int = 10, leak_rate: float = 1.0):
        self.capacity = capacity
        self.leak_rate = leak_rate  # requests drained per second
        self.water = 0.0
        self.last_check = time.monotonic()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_check
            self.water = max(0, self.water - elapsed * self.leak_rate)
            self.last_check = now

            if self.water < self.capacity:
                self.water += 1
                return True
            return False


# ===================================================================
# Demo runner
# ===================================================================
def demo(name: str, limiter, total: int = 25, delay: float = 0.05):
    allowed = 0
    denied = 0
    for i in range(total):
        if hasattr(limiter, "allow"):
            ok = limiter.allow("user1") if "client_id" in limiter.allow.__code__.co_varnames else limiter.allow()
        else:
            ok = False
        if ok:
            allowed += 1
        else:
            denied += 1
        time.sleep(delay)

    print(f"  {name:30s}  allowed={allowed:3d}  denied={denied:3d}  (of {total})")


if __name__ == "__main__":
    print("=" * 70)
    print("  Rate Limiter — Algorithm Comparison")
    print("  Sending 25 requests with 50ms delay (~1.25s total)")
    print("  Each limiter: capacity=10, ~2 tokens/sec refill")
    print("=" * 70)
    print()

    # Token Bucket: 10 capacity, 2/sec refill → allows burst + steady
    demo("Token Bucket (cap=10, 2/s)", TokenBucket(capacity=10, refill_rate=2.0))

    # Sliding Window: 10/60s window
    demo("Sliding Window (10/5s)", SlidingWindowCounter(limit=10, window_seconds=5.0))

    # Fixed Window: 10/60s window
    demo("Fixed Window (10/5s)", FixedWindowCounter(limit=10, window_seconds=5.0))

    # Leaking Bucket: capacity 10, 2/sec drain
    demo("Leaking Bucket (cap=10, 2/s)", LeakingBucket(capacity=10, leak_rate=2.0))

    print()
    print("-" * 70)

    # Burst test
    print()
    print("  Burst Test: 15 requests instantly, then 10 after 2 seconds")
    print()

    for name, limiter in [
        ("Token Bucket", TokenBucket(capacity=10, refill_rate=2.0)),
        ("Leaking Bucket", LeakingBucket(capacity=10, leak_rate=2.0)),
    ]:
        burst_ok, burst_no = 0, 0
        for _ in range(15):
            if limiter.allow():
                burst_ok += 1
            else:
                burst_no += 1
        time.sleep(2)
        after_ok, after_no = 0, 0
        for _ in range(10):
            if limiter.allow():
                after_ok += 1
            else:
                after_no += 1
        print(f"  {name:30s}  burst: {burst_ok}/15 allowed   after 2s wait: {after_ok}/10 allowed")

    print()
    print("Done.")
