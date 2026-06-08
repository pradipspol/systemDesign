# Running Implementations

Each system design problem has a **self-contained, runnable Python implementation** demonstrating the core concepts.

## Setup

```bash
# Create virtual environment
python -m venv venv

# Activate (Windows)
.\venv\Scripts\activate

# Activate (macOS/Linux)
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Run Any Implementation

```bash
# From the implementations/ directory
python 01_url_shortener.py
python 02_rate_limiter.py
python 03_consistent_hashing.py
# ... etc

# Some are Flask/FastAPI web servers:
python 01_url_shortener.py     # → http://localhost:5001
python 08_chat_system.py       # → ws://localhost:8765
python 13_twitter.py           # → http://localhost:5013
```

## Implementation Index

| # | File | Type | Description |
|---|------|------|-------------|
| 1 | `01_url_shortener.py` | Flask API | URL shortening service with Base62, analytics |
| 2 | `02_rate_limiter.py` | Script + API | Token Bucket, Sliding Window, Fixed Window demos |
| 3 | `03_consistent_hashing.py` | Script | Hash ring with virtual nodes, rebalancing demo |
| 4 | `04_key_value_store.py` | Script | LSM-tree based KV store with MemTable + SSTable |
| 5 | `05_message_queue.py` | Script | In-process Pub/Sub message queue with consumer groups |
| 6 | `06_notification_system.py` | Script | Multi-channel notification dispatcher |
| 7 | `07_news_feed.py` | Script | Fan-out on write/read hybrid feed system |
| 8 | `08_chat_system.py` | WebSocket | Real-time chat server with rooms and presence |
| 9 | `09_search_autocomplete.py` | Script | Trie with top-K caching and fuzzy match |
| 10 | `10_video_streaming.py` | Script | Simulated transcoding pipeline with HLS manifest |
| 11 | `11_google_drive.py` | Script | File sync with block chunking and dedup |
| 12 | `12_web_crawler.py` | Async Script | Async web crawler with BFS, Bloom filter, politeness |
| 13 | `13_twitter.py` | Flask API | Tweet posting, timeline fan-out, trending topics |
| 14 | `14_ride_sharing.py` | Script | Geospatial matching with QuadTree and surge pricing |
| 15 | `15_ticketmaster.py` | Flask API | Seat reservation with distributed locking |
| 16 | `16_e_commerce.py` | Script | Order processing with saga pattern |
| 17 | `17_payment_system.py` | Flask API | Payments with idempotency and double-entry ledger |
| 18 | `18_hotel_reservation.py` | Script | Hotel booking with optimistic locking |
| 19 | `19_distributed_cache.py` | Script | LRU/LFU cache with TTL, eviction, stampede prevention |

## Notes

- All implementations use **in-memory** storage (no external DB/Redis needed)
- Flask-based servers can be tested with `curl` or any HTTP client
- Each file is **self-contained** — run it directly to see output
- Production equivalents would replace in-memory stores with Redis, PostgreSQL, Kafka, etc.
