# 13. Twitter (Social Microblogging)

> **Difficulty**: Hard | **Asked by**: Twitter/X, Meta, Google, Amazon, LinkedIn

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
1. Post tweets (280 chars + media)
2. Follow/unfollow users
3. Home timeline (feed of followed users' tweets)
4. User timeline (all tweets by a specific user)
5. Search tweets
6. Trending topics
7. Like, retweet, reply

### Non-Functional Requirements
1. **Read-Heavy**: 100:1 read/write ratio
2. **Low Latency**: Timeline loads < 300ms
3. **Availability**: 99.99% (prefer availability over consistency)
4. **Scale**: 500M DAU, 500M tweets/day

---

## Capacity Estimation

```
DAU: 500M users
Tweets/day: 500M
Timeline reads/day: 50B (100:1 ratio)
Average tweet size: 300 bytes (text) + metadata
Media attachments: 20% of tweets, avg 500KB

Tweet storage/day: 500M × 1KB = 500 GB
Media storage/day: 100M × 500KB = 50 TB
Timeline QPS: ~580K reads/sec (peak: 1.5M)
Tweet QPS: ~5,800 writes/sec

Fan-out:
  Average followers: 200
  Celebrity followers: up to 100M
  Fan-out writes for normal tweet: 200 cache writes
  Fan-out writes for celebrity tweet: 0 (pull model)
```

---

## High-Level Design

### Architecture Overview

```mermaid
graph TB
    subgraph "Write Path"
        User1[User Posts Tweet] --> TweetSvc[Tweet Service]
        TweetSvc --> TweetDB[(Tweet Store<br/>MySQL Sharded)]
        TweetSvc --> MediaSvc[Media Service<br/>S3 + CDN]
        TweetSvc --> Fanout[Fan-out Service]
        TweetSvc --> SearchIdx[Search Indexer<br/>Elasticsearch]
        TweetSvc --> Trending[Trending Service]
    end
    
    subgraph "Fan-out"
        Fanout --> MQ[(Kafka)]
        MQ --> FW[Fan-out Workers]
        FW --> TLCache[(Timeline Cache<br/>Redis)]
        FW --> Graph[(Social Graph<br/>Followers)]
    end
    
    subgraph "Read Path"
        User2[User Opens Timeline] --> TLSvc[Timeline Service]
        TLSvc --> TLCache
        TLSvc --> TweetDB
        TLSvc --> User2
    end
    
    subgraph "Search & Discovery"
        SearchSvc[Search Service] --> ES[(Elasticsearch)]
        Trending --> TrendDB[(Trending DB<br/>Redis)]
    end
```

### Timeline Generation (Hybrid Fan-out)

```mermaid
flowchart TD
    NewTweet[New Tweet by User A] --> Check{A has > 10K<br/>followers?}
    
    Check -->|No: Normal User| Push["Fan-out on Write<br/>Push tweet_id to all<br/>followers' Redis timelines"]
    
    Check -->|Yes: Celebrity| Skip["Skip fan-out<br/>Mark as celebrity tweet"]
    
    subgraph "Timeline Read"
        Read[User B reads timeline] --> Merge[Merge:]
        Cache["Pre-computed timeline<br/>(from fan-out)"] --> Merge
        Celeb["Fetch celebrity tweets<br/>(real-time query)"] --> Merge
        Merge --> Rank["Rank by relevance<br/>+ chronological"]
        Rank --> Display[Display to user]
    end
```

### Tweet Lifecycle

```mermaid
sequenceDiagram
    participant U as User
    participant API as API Gateway
    participant Tweet as Tweet Service
    participant Media as Media Service
    participant DB as Tweet DB
    participant Kafka as Kafka
    participant FW as Fan-out Worker
    participant Redis as Timeline Cache
    participant ES as Elasticsearch
    
    U->>API: POST /tweet {text, media}
    API->>Tweet: Create tweet
    
    opt Has media
        Tweet->>Media: Upload media
        Media-->>Tweet: media_urls
    end
    
    Tweet->>DB: Store tweet
    Tweet->>Kafka: Publish: new_tweet event
    Tweet-->>U: 201 Created
    
    par Async processing
        Kafka->>FW: Fan-out to followers
        FW->>FW: Get follower list
        FW->>Redis: LPUSH to each follower timeline
        
        Kafka->>ES: Index tweet for search
        Kafka->>FW: Update trending counts
    end
```

---

## Low-Level Design

### Data Models

```mermaid
erDiagram
    TWEET {
        bigint id PK "Snowflake ID"
        bigint user_id FK
        varchar text "280 chars max"
        jsonb media_urls
        bigint reply_to_tweet_id "nullable"
        bigint retweet_of_id "nullable"
        int like_count
        int retweet_count
        int reply_count
        timestamp created_at
    }
    
    USER {
        bigint id PK
        varchar username UK
        varchar display_name
        text bio
        int follower_count
        int following_count
        boolean is_verified
        boolean is_celebrity "follower_count > 10K"
    }
    
    FOLLOW {
        bigint follower_id PK
        bigint followee_id PK
        timestamp created_at
    }
    
    LIKE {
        bigint user_id PK
        bigint tweet_id PK
        timestamp created_at
    }
    
    USER ||--o{ TWEET : posts
    USER ||--o{ FOLLOW : follows
    USER ||--o{ LIKE : likes
    TWEET ||--o{ TWEET : replies_to
```

### Timeline Cache (Redis)

```
# Each user has a sorted list of tweet IDs
Key: timeline:{user_id}
Type: Sorted Set (score = tweet creation timestamp)

# On fan-out write:
ZADD timeline:12345 1690000100 "tweet:99001"
ZADD timeline:12345 1690000200 "tweet:99002"

# Trim to latest 800 tweets
ZREMRANGEBYRANK timeline:12345 0 -801

# Read timeline (latest 20):
ZREVRANGE timeline:12345 0 19 WITHSCORES

# Cursor-based pagination:
ZREVRANGEBYSCORE timeline:12345 (last_score +inf LIMIT 0 20
```

### Trending Topics Algorithm

```mermaid
flowchart TD
    Tweets[Stream of tweets] --> Extract[Extract hashtags<br/>& mentioned topics]
    Extract --> Window["Sliding Window Counter<br/>(per 5-min window)"]
    Window --> Rate["Calculate acceleration<br/>rate = count_now / count_prev"]
    Rate --> Filter["Filter:<br/>- Min volume threshold<br/>- Not blocked topics<br/>- Deduplicate similar"]
    Filter --> Rank["Rank by:<br/>score = volume × acceleration"]
    Rank --> Geo["Geo-segment<br/>(trending per country/city)"]
    Geo --> Cache["Cache in Redis<br/>Update every minute"]
```

### Snowflake ID Generation

```
┌─────────────────────────────────────────────────────┐
│                 64-bit Snowflake ID                  │
├──────────┬──────────┬─────────────┬─────────────────┤
│ Sign (1) │ Time(41) │ Machine(10) │ Sequence(12)    │
│    0     │ ms since │  worker ID  │ per-ms counter  │
│          │ epoch    │  (0-1023)   │ (0-4095)        │
└──────────┴──────────┴─────────────┴─────────────────┘

- 41 bits timestamp: ~69 years from custom epoch
- 10 bits machine: 1024 workers
- 12 bits sequence: 4096 IDs per millisecond per worker
- Total: ~4M IDs/sec per worker
- Time-sortable: newer tweets have higher IDs
```

### Search Architecture

```mermaid
graph TD
    Query["Search: #systemdesign"] --> API[Search API]
    API --> ES["Elasticsearch Cluster<br/>(Inverted Index)"]
    
    subgraph "Elasticsearch Index"
        Shard1["Shard 1<br/>tweets 0-10M"]
        Shard2["Shard 2<br/>tweets 10M-20M"]
        ShardN["Shard N"]
    end
    
    ES --> Shard1 & Shard2 & ShardN
    
    subgraph "Index Structure"
        II["Inverted Index:<br/>'system' → [tweet_1, tweet_5, tweet_99]<br/>'design' → [tweet_1, tweet_3, tweet_42]"]
    end
    
    subgraph "Real-time Indexing"
        Kafka[Kafka] --> Logstash[Logstash/Flink]
        Logstash --> ES
    end
```

---

## Implementation

### Timeline Service

```python
from typing import List, Optional
from dataclasses import dataclass

@dataclass
class Tweet:
    id: int
    user_id: int
    text: str
    media_urls: list
    like_count: int
    retweet_count: int
    created_at: str

class TimelineService:
    """Manages user timelines with hybrid fan-out."""
    
    CELEBRITY_THRESHOLD = 10000
    TIMELINE_MAX_SIZE = 800
    PAGE_SIZE = 20
    
    def __init__(self, redis, tweet_store, graph_service):
        self.redis = redis
        self.tweets = tweet_store
        self.graph = graph_service
    
    async def get_home_timeline(self, user_id: int, 
                                 cursor: Optional[str] = None) -> dict:
        """Get user's home timeline (merged fan-out + celebrity tweets)."""
        
        # 1. Get pre-computed timeline from cache
        timeline_key = f"timeline:{user_id}"
        if cursor:
            score = float(cursor)
            cached_ids = self.redis.zrevrangebyscore(
                timeline_key, f"({score}", "-inf",
                start=0, num=self.PAGE_SIZE
            )
        else:
            cached_ids = self.redis.zrevrange(
                timeline_key, 0, self.PAGE_SIZE - 1, withscores=True
            )
        
        # 2. Fetch celebrity tweets (pull model)
        celebrity_followees = await self.graph.get_celebrity_followees(user_id)
        celebrity_tweets = []
        for celeb_id in celebrity_followees:
            recent = await self.tweets.get_recent_by_user(celeb_id, limit=5)
            celebrity_tweets.extend(recent)
        
        # 3. Merge and sort
        all_tweet_ids = set()
        for tid, score in cached_ids:
            all_tweet_ids.add(int(tid))
        for tweet in celebrity_tweets:
            all_tweet_ids.add(tweet.id)
        
        # 4. Fetch full tweet objects
        tweets = await self.tweets.batch_get(list(all_tweet_ids))
        tweets.sort(key=lambda t: t.created_at, reverse=True)
        
        page = tweets[:self.PAGE_SIZE]
        next_cursor = str(page[-1].created_at) if len(page) == self.PAGE_SIZE else None
        
        return {"tweets": page, "next_cursor": next_cursor}


class FanoutService:
    """Distributes tweets to followers' timeline caches."""
    
    CELEBRITY_THRESHOLD = 10000
    
    def __init__(self, redis, graph_service):
        self.redis = redis
        self.graph = graph_service
    
    async def fanout_tweet(self, tweet_id: int, author_id: int,
                            timestamp: float):
        """Fan-out tweet to followers' caches."""
        follower_count = await self.graph.get_follower_count(author_id)
        
        if follower_count > self.CELEBRITY_THRESHOLD:
            return  # Celebrity: skip fan-out, use pull model
        
        followers = await self.graph.get_followers(author_id)
        
        # Batch Redis writes
        pipe = self.redis.pipeline()
        for follower_id in followers:
            key = f"timeline:{follower_id}"
            pipe.zadd(key, {str(tweet_id): timestamp})
            pipe.zremrangebyrank(key, 0, -801)  # Keep latest 800
        pipe.execute()
```

---

## Limitations & Improvements

### Current Limitations

| Limitation | Impact | Severity |
|-----------|--------|----------|
| Celebrity fan-out problem | High write amplification for popular users | High |
| Timeline staleness | Cache may lag behind real-time | Low |
| Hot partition (viral tweets) | Uneven load on tweet DB shards | Medium |
| Trending manipulation | Bot-driven trending topics | High |
| Search relevance | Simple text matching insufficient | Medium |

### Improvement Areas

1. **ML-Ranked Timeline** — Engagement prediction model instead of chronological
2. **Real-time Event Processing** — Flink/Kafka Streams for instant trending
3. **GraphQL API** — Clients request exactly what they need, reducing over-fetching
4. **Spam/Bot Detection** — ML models for fake account and spam detection
5. **Spaces/Audio** — Real-time audio rooms using WebRTC

---

## Key Interview Discussion Points

1. **Fan-out on write vs read?** Hybrid approach: push for normal, pull for celebrities
2. **Why Snowflake IDs?** Time-sortable (chronological ordering), unique across distributed systems
3. **How to handle viral tweets?** Dedicated hot key cache, replicate across shards
4. **How does search work in real-time?** Kafka → Elasticsearch near-real-time indexing
5. **How to prevent trending manipulation?** Velocity-based scoring, bot detection, minimum unique users threshold
