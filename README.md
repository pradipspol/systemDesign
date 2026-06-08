# System Design - HLD & LLD

A comprehensive collection of the **Top 20 System Design Problems** most frequently asked by leading tech companies (FAANG, Microsoft, Uber, Stripe, etc.).

Each problem includes:
- **High-Level Design (HLD)** with architecture diagrams
- **Low-Level Design (LLD)** with component-level diagrams
- **Required Implementations** (APIs, data models, algorithms)
- **Limitations & Improvement Areas**

## Problem Index

| # | Problem | Difficulty | Key Concepts | File |
|---|---------|-----------|--------------|------|
| 1 | [URL Shortener (TinyURL)](problems/01-url-shortener.md) | Medium | Hashing, NoSQL, Redirection | `01-url-shortener.md` |
| 2 | [Rate Limiter](problems/02-rate-limiter.md) | Medium | Token Bucket, Sliding Window, Redis | `02-rate-limiter.md` |
| 3 | [Consistent Hashing](problems/03-consistent-hashing.md) | Hard | Hash Ring, Virtual Nodes, Partitioning | `03-consistent-hashing.md` |
| 4 | [Key-Value Store](problems/04-key-value-store.md) | Hard | LSM Tree, SSTable, Replication | `04-key-value-store.md` |
| 5 | [Distributed Message Queue](problems/05-message-queue.md) | Hard | Pub/Sub, Partitioning, Consumer Groups | `05-message-queue.md` |
| 6 | [Notification System](problems/06-notification-system.md) | Medium | Push/Pull, Priority Queue, Templates | `06-notification-system.md` |
| 7 | [News Feed System](problems/07-news-feed.md) | Hard | Fan-out, Ranking, Caching | `07-news-feed.md` |
| 8 | [Chat System (WhatsApp)](problems/08-chat-system.md) | Hard | WebSocket, Message Queue, E2E Encryption | `08-chat-system.md` |
| 9 | [Search Autocomplete](problems/09-search-autocomplete.md) | Medium | Trie, Prefix Tree, Ranking | `09-search-autocomplete.md` |
| 10 | [Video Streaming (YouTube/Netflix)](problems/10-video-streaming.md) | Hard | CDN, Transcoding, Adaptive Bitrate | `10-video-streaming.md` |
| 11 | [Google Drive / Dropbox](problems/11-google-drive.md) | Hard | File Sync, Chunking, Conflict Resolution | `11-google-drive.md` |
| 12 | [Web Crawler](problems/12-web-crawler.md) | Medium | BFS, URL Frontier, Politeness | `12-web-crawler.md` |
| 13 | [Twitter](problems/13-twitter.md) | Hard | Fan-out, Timeline, Trending | `13-twitter.md` |
| 14 | [Uber / Ride Sharing](problems/14-ride-sharing.md) | Hard | Geospatial Index, Matching, ETA | `14-ride-sharing.md` |
| 15 | [Ticketmaster](problems/15-ticketmaster.md) | Hard | Seat Reservation, Concurrency, Distributed Lock | `15-ticketmaster.md` |
| 16 | [E-Commerce Platform (Amazon)](problems/16-e-commerce.md) | Hard | Inventory, Cart, Recommendations | `16-e-commerce.md` |
| 17 | [Payment System](problems/17-payment-system.md) | Hard | Idempotency, Ledger, Reconciliation | `17-payment-system.md` |
| 18 | [Hotel Reservation System](problems/18-hotel-reservation.md) | Medium | Booking, Overbooking, Concurrency | `18-hotel-reservation.md` |
| 19 | [Distributed Cache](problems/19-distributed-cache.md) | Hard | Eviction, Consistency, Replication | `19-distributed-cache.md` |

## How to Use

1. Each file is self-contained with full analysis
2. Diagrams use **Mermaid** syntax (renders on GitHub and VS Code with extensions)
3. Start with HLD to understand the big picture, then dive into LLD
4. Review limitations to prepare for follow-up interview questions

## Companies That Ask These Problems

| Company | Commonly Asked Problems |
|---------|----------------------|
| Google | URL Shortener, Web Crawler, Search Autocomplete, YouTube, Key-Value Store |
| Meta | News Feed, Chat System, Twitter, Notification System |
| Amazon | E-Commerce, Payment System, Rate Limiter, Distributed Cache |
| Microsoft | Google Drive, Chat System, Video Streaming, Hotel Reservation |
| Uber | Ride Sharing, Rate Limiter, Notification System, Message Queue |
| Netflix | Video Streaming, Distributed Cache, Rate Limiter |
| Stripe | Payment System, Rate Limiter, Idempotency |
| Airbnb | Hotel Reservation, Search, Notification System |
| Twitter/X | Twitter, News Feed, Consistent Hashing |

## Design Framework

For each problem, we follow this structured approach:

```
1. Requirements Clarification (Functional + Non-Functional)
2. Capacity Estimation (Traffic, Storage, Bandwidth)
3. High-Level Design (Architecture + API Design)
4. Low-Level Design (Data Models + Component Details)
5. Implementation Details (Key Algorithms + Code)
6. Scalability & Performance
7. Limitations & Improvements
```
