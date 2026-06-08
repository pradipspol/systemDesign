"""
=============================================================
  5. Message Queue — In-Process Pub/Sub with Partitions
  Run: python 05_message_queue.py
  Implements topics, partitions, consumer groups, offset
  tracking, message retention, and dead-letter queue.
=============================================================
"""
import time
import uuid
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Message:
    key: Optional[str]
    value: str
    topic: str
    partition: int = 0
    offset: int = 0
    timestamp: float = field(default_factory=time.time)
    message_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    headers: dict = field(default_factory=dict)
    retries: int = 0


@dataclass
class Partition:
    id: int
    messages: list[Message] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def append(self, msg: Message) -> int:
        with self._lock:
            msg.offset = len(self.messages)
            msg.partition = self.id
            self.messages.append(msg)
            return msg.offset

    def read(self, offset: int, max_count: int = 10) -> list[Message]:
        with self._lock:
            return self.messages[offset: offset + max_count]


class Topic:
    def __init__(self, name: str, num_partitions: int = 3, retention_ms: int = 3600_000):
        self.name = name
        self.partitions = [Partition(id=i) for i in range(num_partitions)]
        self.retention_ms = retention_ms

    def get_partition(self, key: Optional[str]) -> Partition:
        if key:
            idx = hash(key) % len(self.partitions)
        else:
            idx = int(time.time() * 1000) % len(self.partitions)
        return self.partitions[idx]


class ConsumerGroup:
    def __init__(self, group_id: str, topic: Topic):
        self.group_id = group_id
        self.topic = topic
        # partition_id -> committed offset
        self.offsets: dict[int, int] = {p.id: 0 for p in topic.partitions}
        # partition_id -> assigned consumer_id
        self.assignments: dict[int, str] = {}
        self._lock = threading.Lock()

    def assign(self, consumer_ids: list[str]):
        """Round-robin partition assignment."""
        with self._lock:
            partitions = self.topic.partitions
            self.assignments = {}
            for i, p in enumerate(partitions):
                consumer = consumer_ids[i % len(consumer_ids)]
                self.assignments[p.id] = consumer

    def poll(self, consumer_id: str, max_messages: int = 10) -> list[Message]:
        """Poll messages from assigned partitions."""
        results = []
        with self._lock:
            for pid, assigned in self.assignments.items():
                if assigned != consumer_id:
                    continue
                offset = self.offsets.get(pid, 0)
                messages = self.topic.partitions[pid].read(offset, max_messages)
                results.extend(messages)
        return results

    def commit(self, consumer_id: str, partition_id: int, offset: int):
        with self._lock:
            if self.assignments.get(partition_id) == consumer_id:
                self.offsets[partition_id] = offset


class MessageBroker:
    def __init__(self):
        self.topics: dict[str, Topic] = {}
        self.consumer_groups: dict[str, ConsumerGroup] = {}
        self.dead_letter: list[Message] = []
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._stats = {"produced": 0, "consumed": 0, "dlq": 0}

    def create_topic(self, name: str, partitions: int = 3) -> Topic:
        if name not in self.topics:
            self.topics[name] = Topic(name, num_partitions=partitions)
        return self.topics[name]

    def produce(self, topic_name: str, value: str, key: Optional[str] = None,
                headers: Optional[dict] = None) -> Message:
        topic = self.topics.get(topic_name)
        if not topic:
            topic = self.create_topic(topic_name)

        msg = Message(key=key, value=value, topic=topic_name, headers=headers or {})
        partition = topic.get_partition(key)
        partition.append(msg)
        self._stats["produced"] += 1

        # Notify real-time subscribers
        for callback in self._subscribers.get(topic_name, []):
            try:
                callback(msg)
            except Exception:
                pass

        return msg

    def subscribe(self, topic_name: str, callback: Callable):
        self._subscribers[topic_name].append(callback)

    def create_consumer_group(self, group_id: str, topic_name: str) -> ConsumerGroup:
        topic = self.topics.get(topic_name)
        if not topic:
            topic = self.create_topic(topic_name)
        cg = ConsumerGroup(group_id, topic)
        self.consumer_groups[group_id] = cg
        return cg

    def send_to_dlq(self, msg: Message, error: str):
        msg.headers["dlq_error"] = error
        msg.headers["dlq_timestamp"] = str(time.time())
        self.dead_letter.append(msg)
        self._stats["dlq"] += 1

    def stats(self) -> dict:
        topic_stats = {}
        for name, topic in self.topics.items():
            topic_stats[name] = {
                "partitions": len(topic.partitions),
                "total_messages": sum(len(p.messages) for p in topic.partitions),
            }
        return {**self._stats, "topics": topic_stats}


# ===================================================================
# Demo
# ===================================================================
if __name__ == "__main__":
    print("=" * 65)
    print("  Message Queue — Pub/Sub with Partitions")
    print("=" * 65)

    broker = MessageBroker()

    # Create topics
    broker.create_topic("orders", partitions=3)
    broker.create_topic("notifications", partitions=2)

    # Real-time subscriber
    received = []
    broker.subscribe("orders", lambda msg: received.append(msg))

    # Produce messages
    print("\n  Producing 20 order messages...")
    for i in range(20):
        broker.produce("orders", f"Order #{i}: item_{i % 5}", key=f"user_{i % 4}")

    print(f"  Real-time subscriber received: {len(received)} messages")

    # Consumer group
    print("\n  Setting up consumer group 'order-processors' with 2 consumers...")
    cg = broker.create_consumer_group("order-processors", "orders")
    cg.assign(["consumer-1", "consumer-2"])

    print(f"  Partition assignments: {cg.assignments}")

    # Poll per consumer
    for cid in ["consumer-1", "consumer-2"]:
        msgs = cg.poll(cid, max_messages=20)
        print(f"  {cid} polled {len(msgs)} messages:")
        for m in msgs[:3]:
            print(f"    partition={m.partition} offset={m.offset} key={m.key} value={m.value[:40]}")
        if len(msgs) > 3:
            print(f"    ... and {len(msgs) - 3} more")
        # Commit offsets
        for m in msgs:
            cg.commit(cid, m.partition, m.offset + 1)

    # Key-based partitioning
    print("\n  Key partitioning (same key → same partition):")
    msgs_by_key = defaultdict(list)
    for m in received:
        msgs_by_key[m.key].append(m.partition)
    for key in sorted(msgs_by_key.keys()):
        parts = set(msgs_by_key[key])
        print(f"    {key}: always partition {parts}")

    # Dead letter queue
    print("\n  DLQ: Simulating 3 poison messages...")
    for i in range(3):
        msg = broker.produce("orders", f"bad_order_{i}")
        broker.send_to_dlq(msg, f"Processing failed: invalid format")
    print(f"  DLQ size: {len(broker.dead_letter)}")

    # Stats
    print(f"\n  Broker stats: {broker.stats()}")
    print("\nDone.")
