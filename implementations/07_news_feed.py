"""
=============================================================
  7. News Feed System — Hybrid Fan-Out with Ranking
  Run: python 07_news_feed.py
  Implements fan-out on write for normal users,
  fan-out on read for celebrities, and feed ranking.
=============================================================
"""
import time
import uuid
import heapq
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


# ===================================================================
# Data Models
# ===================================================================
@dataclass
class Post:
    post_id: str
    author_id: str
    content: str
    media_urls: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    likes: int = 0
    comments: int = 0
    shares: int = 0


@dataclass
class User:
    user_id: str
    name: str
    followers: set = field(default_factory=set)
    following: set = field(default_factory=set)
    is_celebrity: bool = False  # True if > CELEBRITY_THRESHOLD followers


CELEBRITY_THRESHOLD = 5  # Low for demo; production: ~10000


# ===================================================================
# Feed Cache (per-user timeline)
# ===================================================================
class FeedCache:
    def __init__(self, max_size: int = 200):
        self.cache: dict[str, list[str]] = defaultdict(list)  # user_id -> [post_ids]
        self.max_size = max_size

    def push(self, user_id: str, post_id: str):
        self.cache[user_id].insert(0, post_id)
        if len(self.cache[user_id]) > self.max_size:
            self.cache[user_id] = self.cache[user_id][:self.max_size]

    def get(self, user_id: str, offset: int = 0, limit: int = 20) -> list[str]:
        return self.cache[user_id][offset: offset + limit]


# ===================================================================
# Ranking Engine
# ===================================================================
class FeedRanker:
    """Score posts by engagement + recency + affinity."""

    def __init__(self):
        # user_id -> {other_user: interaction_count}
        self.interactions: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    def record_interaction(self, user_id: str, author_id: str):
        self.interactions[user_id][author_id] += 1

    def score(self, post: Post, viewer_id: str) -> float:
        # Time decay (half-life = 6 hours)
        age_hours = (time.time() - post.created_at) / 3600
        recency = 1.0 / (1.0 + age_hours / 6.0)

        # Engagement score
        engagement = (post.likes * 1.0 + post.comments * 2.0 + post.shares * 3.0) / 100.0

        # Affinity: how much viewer interacts with author
        affinity = min(1.0, self.interactions[viewer_id].get(post.author_id, 0) / 10.0)

        return recency * 0.4 + engagement * 0.3 + affinity * 0.3

    def rank(self, posts: list[Post], viewer_id: str) -> list[Post]:
        scored = [(self.score(p, viewer_id), p) for p in posts]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored]


# ===================================================================
# News Feed Service
# ===================================================================
class NewsFeedService:
    def __init__(self):
        self.users: dict[str, User] = {}
        self.posts: dict[str, Post] = {}
        self.feed_cache = FeedCache()
        self.ranker = FeedRanker()
        self._stats = {"fan_out_write": 0, "fan_out_read": 0, "posts_created": 0}

    def create_user(self, user_id: str, name: str) -> User:
        user = User(user_id=user_id, name=name)
        self.users[user_id] = user
        return user

    def follow(self, follower_id: str, followee_id: str):
        follower = self.users.get(follower_id)
        followee = self.users.get(followee_id)
        if not follower or not followee:
            return
        follower.following.add(followee_id)
        followee.followers.add(follower_id)
        followee.is_celebrity = len(followee.followers) >= CELEBRITY_THRESHOLD

    def create_post(self, author_id: str, content: str, media_urls: list[str] = None) -> Post:
        post = Post(
            post_id=str(uuid.uuid4())[:8],
            author_id=author_id,
            content=content,
            media_urls=media_urls or [],
        )
        self.posts[post.post_id] = post
        self._stats["posts_created"] += 1

        author = self.users.get(author_id)
        if not author:
            return post

        if author.is_celebrity:
            # Fan-out on read: don't pre-compute, pull at read time
            self._stats["fan_out_read"] += 1
        else:
            # Fan-out on write: push to all followers' caches
            for follower_id in author.followers:
                self.feed_cache.push(follower_id, post.post_id)
                self._stats["fan_out_write"] += 1

        return post

    def get_feed(self, user_id: str, offset: int = 0, limit: int = 20) -> list[Post]:
        """Hybrid approach: cached feed + pull from celebrity follows."""
        user = self.users.get(user_id)
        if not user:
            return []

        # 1. Get cached post IDs (from fan-out on write)
        cached_ids = set(self.feed_cache.get(user_id, 0, 500))

        # 2. Pull recent posts from celebrities user follows (fan-out on read)
        celebrity_posts = []
        for following_id in user.following:
            followee = self.users.get(following_id)
            if followee and followee.is_celebrity:
                for pid, post in self.posts.items():
                    if post.author_id == following_id:
                        celebrity_posts.append(post)

        # 3. Merge
        all_posts = []
        for pid in cached_ids:
            post = self.posts.get(pid)
            if post:
                all_posts.append(post)
        all_posts.extend(celebrity_posts)

        # 4. Deduplicate
        seen = set()
        unique = []
        for p in all_posts:
            if p.post_id not in seen:
                seen.add(p.post_id)
                unique.append(p)

        # 5. Rank
        ranked = self.ranker.rank(unique, user_id)

        return ranked[offset: offset + limit]

    def like_post(self, user_id: str, post_id: str):
        post = self.posts.get(post_id)
        if post:
            post.likes += 1
            self.ranker.record_interaction(user_id, post.author_id)

    def comment_on_post(self, user_id: str, post_id: str):
        post = self.posts.get(post_id)
        if post:
            post.comments += 1
            self.ranker.record_interaction(user_id, post.author_id)

    def stats(self) -> dict:
        return {
            **self._stats,
            "total_users": len(self.users),
            "total_posts": len(self.posts),
            "celebrities": [u.user_id for u in self.users.values() if u.is_celebrity],
        }


# ===================================================================
# Demo
# ===================================================================
if __name__ == "__main__":
    print("=" * 65)
    print("  News Feed — Hybrid Fan-Out System")
    print(f"  Celebrity threshold: {CELEBRITY_THRESHOLD} followers")
    print("=" * 65)

    svc = NewsFeedService()

    # Create users
    alice = svc.create_user("alice", "Alice")
    bob = svc.create_user("bob", "Bob")
    charlie = svc.create_user("charlie", "Charlie")
    celeb = svc.create_user("celebrity", "CelebStar")
    users = [svc.create_user(f"user_{i}", f"User {i}") for i in range(10)]

    # Build follow graph — make 'celebrity' a celebrity
    for u in [alice, bob, charlie] + users:
        svc.follow(u.user_id, celeb.user_id)  # everyone follows celeb

    svc.follow("alice", "bob")
    svc.follow("bob", "alice")
    svc.follow("charlie", "alice")
    svc.follow("charlie", "bob")

    print(f"\n  Celebrity status: celebrity.is_celebrity={celeb.is_celebrity} "
          f"(followers={len(celeb.followers)})")
    print(f"  Bob's follower count: {len(bob.followers)} (is_celebrity={bob.is_celebrity})")

    # Create posts
    print("\n  Creating posts...")
    p1 = svc.create_post("bob", "Just had great coffee! ☕")
    p2 = svc.create_post("alice", "Working on system design 💻")
    p3 = svc.create_post("celebrity", "New album dropping next week! 🎵")
    p4 = svc.create_post("celebrity", "Thanks for 1M streams! 🎉")
    p5 = svc.create_post("bob", "Check out this sunset 🌅", ["sunset.jpg"])

    # Simulate engagement
    svc.like_post("alice", p1.post_id)
    svc.like_post("charlie", p1.post_id)
    svc.like_post("alice", p3.post_id)
    for u in users[:7]:
        svc.like_post(u.user_id, p3.post_id)
    svc.comment_on_post("alice", p1.post_id)

    p1.likes = 15
    p1.comments = 5
    p3.likes = 150
    p3.comments = 30
    p3.shares = 20

    # Get feeds
    print("\n  Alice's Feed (follows Bob, followed by Charlie):")
    feed = svc.get_feed("alice", limit=10)
    for i, post in enumerate(feed, 1):
        author = svc.users[post.author_id].name
        score = svc.ranker.score(post, "alice")
        print(f"    {i}. [{author}] {post.content[:50]} "
              f"(likes={post.likes}, score={score:.3f})")

    print("\n  Charlie's Feed (follows Alice + Bob + Celebrity):")
    feed = svc.get_feed("charlie", limit=10)
    for i, post in enumerate(feed, 1):
        author = svc.users[post.author_id].name
        score = svc.ranker.score(post, "charlie")
        print(f"    {i}. [{author}] {post.content[:50]} "
              f"(likes={post.likes}, score={score:.3f})")

    # Stats
    print(f"\n  Stats: {svc.stats()}")
    print("\nDone.")
