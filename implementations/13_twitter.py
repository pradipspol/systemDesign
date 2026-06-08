"""
=============================================================
  13. Twitter — Tweet Service with Timeline & Trending
  Run: python 13_twitter.py
  Test: curl http://localhost:5013/api/v1/timeline/alice
        curl -X POST http://localhost:5013/api/v1/tweet -H "Content-Type: application/json" -d "{\"user_id\":\"alice\",\"content\":\"Hello!\"}"
=============================================================
"""
import time
import uuid
import threading
import heapq
from collections import defaultdict
from dataclasses import dataclass, field
from flask import Flask, request, jsonify

app = Flask(__name__)


# ===================================================================
# Snowflake ID Generator
# ===================================================================
class SnowflakeID:
    EPOCH = 1_700_000_000_000

    def __init__(self, machine_id: int = 1):
        self.machine_id = machine_id & 0x3FF
        self.sequence = 0
        self.last_ts = -1
        self._lock = threading.Lock()

    def next_id(self) -> int:
        with self._lock:
            ts = int(time.time() * 1000)
            if ts == self.last_ts:
                self.sequence = (self.sequence + 1) & 0xFFF
                if self.sequence == 0:
                    while ts <= self.last_ts:
                        ts = int(time.time() * 1000)
            else:
                self.sequence = 0
            self.last_ts = ts
            return ((ts - self.EPOCH) << 22) | (self.machine_id << 12) | self.sequence


# ===================================================================
# Data Models
# ===================================================================
@dataclass
class Tweet:
    tweet_id: str
    user_id: str
    content: str
    created_at: float = field(default_factory=time.time)
    likes: int = 0
    retweets: int = 0
    replies: int = 0
    reply_to: str = ""
    media_urls: list = field(default_factory=list)
    hashtags: list = field(default_factory=list)

    def to_dict(self):
        return {
            "tweet_id": self.tweet_id,
            "user_id": self.user_id,
            "content": self.content,
            "created_at": self.created_at,
            "likes": self.likes,
            "retweets": self.retweets,
            "replies": self.replies,
            "hashtags": self.hashtags,
        }


@dataclass
class User:
    user_id: str
    display_name: str
    followers: set = field(default_factory=set)
    following: set = field(default_factory=set)
    tweet_ids: list = field(default_factory=list)
    is_celebrity: bool = False


# ===================================================================
# Core Stores
# ===================================================================
CELEBRITY_THRESHOLD = 5  # Low for demo

id_gen = SnowflakeID()
users: dict[str, User] = {}
tweets: dict[str, Tweet] = {}
timelines: dict[str, list] = defaultdict(list)  # user_id -> [tweet_ids]
hashtag_index: dict[str, list] = defaultdict(list)  # hashtag -> [tweet_ids]


# ===================================================================
# Trending Algorithm
# ===================================================================
class TrendingTracker:
    def __init__(self, window_seconds: float = 3600):
        self.window = window_seconds
        self.counts: dict[str, list[float]] = defaultdict(list)

    def record(self, hashtag: str):
        self.counts[hashtag].append(time.time())

    def get_trending(self, limit: int = 10) -> list[dict]:
        now = time.time()
        cutoff = now - self.window
        scores = []
        for tag, timestamps in list(self.counts.items()):
            # Remove old
            recent = [t for t in timestamps if t > cutoff]
            self.counts[tag] = recent
            if recent:
                # Score = count * recency_boost
                velocity = len(recent) / max(1, (now - min(recent)) / 60)
                scores.append({"hashtag": tag, "count": len(recent), "velocity": round(velocity, 2)})
        scores.sort(key=lambda x: x["velocity"], reverse=True)
        return scores[:limit]

trending = TrendingTracker(window_seconds=300)


# ===================================================================
# Service Functions
# ===================================================================
def extract_hashtags(content: str) -> list[str]:
    import re
    return re.findall(r"#(\w+)", content)


def create_user(user_id: str, display_name: str) -> User:
    user = User(user_id=user_id, display_name=display_name)
    users[user_id] = user
    return user


def follow(follower_id: str, followee_id: str):
    if follower_id not in users or followee_id not in users:
        return
    users[follower_id].following.add(followee_id)
    users[followee_id].followers.add(follower_id)
    users[followee_id].is_celebrity = len(users[followee_id].followers) >= CELEBRITY_THRESHOLD


def post_tweet(user_id: str, content: str, reply_to: str = "") -> Tweet:
    tweet = Tweet(
        tweet_id=str(id_gen.next_id()),
        user_id=user_id,
        content=content,
        reply_to=reply_to,
        hashtags=extract_hashtags(content),
    )
    tweets[tweet.tweet_id] = tweet
    users[user_id].tweet_ids.append(tweet.tweet_id)

    # Index hashtags
    for tag in tweet.hashtags:
        hashtag_index[tag.lower()].append(tweet.tweet_id)
        trending.record(tag.lower())

    # Fan-out on write (for non-celebrities)
    author = users[user_id]
    if not author.is_celebrity:
        for fid in author.followers:
            timelines[fid].insert(0, tweet.tweet_id)
            if len(timelines[fid]) > 800:
                timelines[fid] = timelines[fid][:800]

    return tweet


def get_timeline(user_id: str, limit: int = 20) -> list[dict]:
    user = users.get(user_id)
    if not user:
        return []

    # Cached timeline + pull from celebrities
    cached_ids = set(timelines.get(user_id, [])[:200])
    celebrity_tweet_ids = []
    for fid in user.following:
        followee = users.get(fid)
        if followee and followee.is_celebrity:
            celebrity_tweet_ids.extend(followee.tweet_ids[-50:])

    all_ids = list(cached_ids) + celebrity_tweet_ids
    # Dedupe and sort
    seen = set()
    unique_tweets = []
    for tid in all_ids:
        if tid not in seen:
            seen.add(tid)
            t = tweets.get(tid)
            if t:
                unique_tweets.append(t)

    unique_tweets.sort(key=lambda t: t.created_at, reverse=True)
    return [t.to_dict() for t in unique_tweets[:limit]]


def search_hashtag(tag: str, limit: int = 20) -> list[dict]:
    tweet_ids = hashtag_index.get(tag.lower(), [])
    result_tweets = [tweets[tid].to_dict() for tid in reversed(tweet_ids[-limit:]) if tid in tweets]
    return result_tweets


# ===================================================================
# Flask Routes
# ===================================================================
@app.route("/api/v1/tweet", methods=["POST"])
def api_post_tweet():
    data = request.get_json(force=True)
    user_id = data.get("user_id")
    content = data.get("content")
    if not user_id or not content:
        return jsonify({"error": "user_id and content required"}), 400
    if user_id not in users:
        create_user(user_id, user_id.title())
    tweet = post_tweet(user_id, content, data.get("reply_to", ""))
    return jsonify(tweet.to_dict()), 201


@app.route("/api/v1/timeline/<user_id>")
def api_timeline(user_id: str):
    limit = request.args.get("limit", 20, type=int)
    return jsonify(get_timeline(user_id, limit))


@app.route("/api/v1/search/hashtag/<tag>")
def api_search_hashtag(tag: str):
    return jsonify(search_hashtag(tag))


@app.route("/api/v1/trending")
def api_trending():
    return jsonify(trending.get_trending(10))


@app.route("/api/v1/user/<user_id>")
def api_user(user_id: str):
    user = users.get(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({
        "user_id": user.user_id,
        "display_name": user.display_name,
        "followers": len(user.followers),
        "following": len(user.following),
        "tweets": len(user.tweet_ids),
        "is_celebrity": user.is_celebrity,
    })


@app.route("/api/v1/follow", methods=["POST"])
def api_follow():
    data = request.get_json(force=True)
    follow(data["follower_id"], data["followee_id"])
    return jsonify({"status": "ok"})


# ===================================================================
# Initialize demo data & run
# ===================================================================
def init_demo_data():
    # Create users
    for uid, name in [("alice", "Alice"), ("bob", "Bob"), ("charlie", "Charlie"),
                      ("celebrity", "CelebStar"), ("dev", "Dev Expert")]:
        create_user(uid, name)

    # Build follow graph
    for uid in ["alice", "bob", "charlie", "dev"]:
        follow(uid, "celebrity")
    follow("alice", "bob")
    follow("bob", "alice")
    follow("charlie", "alice")
    follow("charlie", "bob")
    for i in range(6):
        u = create_user(f"fan_{i}", f"Fan {i}")
        follow(u.user_id, "celebrity")

    # Post tweets
    post_tweet("alice", "Working on #systemdesign for the URL shortener! 💻")
    post_tweet("bob", "Just deployed a new #microservice with #docker 🚀")
    post_tweet("celebrity", "Excited to announce my new #systemdesign course! 🎉")
    post_tweet("alice", "The #ratelimiter chapter is fascinating. Token bucket vs sliding window!")
    post_tweet("dev", "New blog post: #distributedsystems and #CAP theorem explained")
    post_tweet("celebrity", "1M followers! Thank you all! #milestone #systemdesign")
    post_tweet("charlie", "Reading about #consistenthashing and virtual nodes #systemdesign")
    post_tweet("bob", "Great talk on #microservice architecture patterns #docker #kubernetes")

    # Like some tweets
    for tid in list(tweets.keys())[:3]:
        tweets[tid].likes += 42


if __name__ == "__main__":
    init_demo_data()

    print("=" * 60)
    print("  Twitter Clone — Tweet Service")
    print("  http://localhost:5013")
    print()
    print("  API Endpoints:")
    print("  POST /api/v1/tweet          {user_id, content}")
    print("  GET  /api/v1/timeline/:uid  Timeline feed")
    print("  GET  /api/v1/search/hashtag/:tag")
    print("  GET  /api/v1/trending       Top hashtags")
    print("  GET  /api/v1/user/:uid      User profile")
    print("  POST /api/v1/follow         {follower_id, followee_id}")
    print()

    # Print demo data summary
    print("  Demo data loaded:")
    print(f"    Users: {len(users)}")
    print(f"    Tweets: {len(tweets)}")
    print(f"    Celebrity: {users['celebrity'].display_name} "
          f"({len(users['celebrity'].followers)} followers)")
    print(f"\n  Alice's timeline:")
    for t in get_timeline("alice", 5):
        print(f"    @{t['user_id']}: {t['content'][:60]}")
    print(f"\n  Trending: {trending.get_trending(5)}")
    print("=" * 60)
    app.run(port=5013, debug=True)
