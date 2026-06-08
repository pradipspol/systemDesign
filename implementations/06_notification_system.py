"""
=============================================================
  6. Notification System — Multi-Channel Dispatcher
  Run: python 06_notification_system.py
  Implements push/SMS/email channels, priority queue,
  template engine, rate limiting, and retry logic.
=============================================================
"""
import time
import uuid
import heapq
import threading
from enum import Enum
from dataclasses import dataclass, field
from collections import defaultdict
from typing import Optional


class Channel(Enum):
    PUSH = "push"
    SMS = "sms"
    EMAIL = "email"


class Priority(Enum):
    CRITICAL = 0   # security alerts, OTP
    HIGH = 1       # order updates
    NORMAL = 2     # social notifications
    LOW = 3        # marketing


@dataclass(order=True)
class Notification:
    priority: int
    created_at: float = field(compare=True)
    notification_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8], compare=False)
    user_id: str = field(default="", compare=False)
    channel: Channel = field(default=Channel.PUSH, compare=False)
    title: str = field(default="", compare=False)
    body: str = field(default="", compare=False)
    template_id: Optional[str] = field(default=None, compare=False)
    template_vars: dict = field(default_factory=dict, compare=False)
    retries: int = field(default=0, compare=False)
    max_retries: int = field(default=3, compare=False)
    status: str = field(default="pending", compare=False)


# ===================================================================
# Template Engine
# ===================================================================
class TemplateEngine:
    def __init__(self):
        self.templates: dict[str, dict] = {}

    def register(self, template_id: str, title: str, body: str, channels: list[Channel]):
        self.templates[template_id] = {
            "title": title,
            "body": body,
            "channels": channels,
        }

    def render(self, template_id: str, variables: dict) -> tuple[str, str]:
        tpl = self.templates.get(template_id)
        if not tpl:
            return ("Unknown", "Template not found")
        title = tpl["title"]
        body = tpl["body"]
        for key, val in variables.items():
            title = title.replace(f"{{{{{key}}}}}", str(val))
            body = body.replace(f"{{{{{key}}}}}", str(val))
        return title, body


# ===================================================================
# Channel Handlers (simulated)
# ===================================================================
class PushHandler:
    def __init__(self, fail_rate: float = 0.1):
        self.fail_rate = fail_rate
        self.sent = []

    def send(self, notification: Notification) -> bool:
        # Simulate occasional failures
        if hash(notification.notification_id) % 10 < int(self.fail_rate * 10):
            return False
        self.sent.append(notification)
        return True


class SMSHandler:
    def __init__(self):
        self.sent = []

    def send(self, notification: Notification) -> bool:
        self.sent.append(notification)
        return True


class EmailHandler:
    def __init__(self):
        self.sent = []

    def send(self, notification: Notification) -> bool:
        self.sent.append(notification)
        return True


# ===================================================================
# Rate Limiter per user per channel
# ===================================================================
class NotificationRateLimiter:
    def __init__(self):
        # channel -> max per hour
        self.limits = {
            Channel.PUSH: 50,
            Channel.SMS: 5,
            Channel.EMAIL: 20,
        }
        self._counts: dict[str, dict[Channel, list[float]]] = defaultdict(lambda: defaultdict(list))

    def allow(self, user_id: str, channel: Channel) -> bool:
        now = time.time()
        window = self._counts[user_id][channel]
        # Remove entries older than 1 hour
        self._counts[user_id][channel] = [t for t in window if now - t < 3600]
        if len(self._counts[user_id][channel]) >= self.limits[channel]:
            return False
        self._counts[user_id][channel].append(now)
        return True


# ===================================================================
# Notification Service
# ===================================================================
class NotificationService:
    def __init__(self):
        self.queue: list[Notification] = []
        self.template_engine = TemplateEngine()
        self.rate_limiter = NotificationRateLimiter()
        self.handlers = {
            Channel.PUSH: PushHandler(fail_rate=0.2),
            Channel.SMS: SMSHandler(),
            Channel.EMAIL: EmailHandler(),
        }
        self.user_preferences: dict[str, dict] = {}  # user_id -> {channels, quiet_hours}
        self.retry_queue: list[Notification] = []
        self._stats = {"sent": 0, "failed": 0, "rate_limited": 0, "retried": 0}
        self._lock = threading.Lock()

    def set_user_preferences(self, user_id: str, channels: list[Channel],
                             quiet_start: int = 22, quiet_end: int = 7):
        self.user_preferences[user_id] = {
            "channels": channels,
            "quiet_start": quiet_start,
            "quiet_end": quiet_end,
        }

    def enqueue(self, user_id: str, channel: Channel, title: str = "", body: str = "",
                priority: Priority = Priority.NORMAL,
                template_id: Optional[str] = None, template_vars: Optional[dict] = None):
        if template_id:
            title, body = self.template_engine.render(template_id, template_vars or {})

        notif = Notification(
            priority=priority.value,
            created_at=time.time(),
            user_id=user_id,
            channel=channel,
            title=title,
            body=body,
            template_id=template_id,
            template_vars=template_vars or {},
        )
        with self._lock:
            heapq.heappush(self.queue, notif)

    def process_queue(self, max_batch: int = 100) -> dict:
        """Process notifications from priority queue."""
        processed = 0
        with self._lock:
            batch = []
            while self.queue and len(batch) < max_batch:
                batch.append(heapq.heappop(self.queue))

        for notif in batch:
            # Rate limiting
            if not self.rate_limiter.allow(notif.user_id, notif.channel):
                notif.status = "rate_limited"
                self._stats["rate_limited"] += 1
                continue

            handler = self.handlers.get(notif.channel)
            if handler and handler.send(notif):
                notif.status = "sent"
                self._stats["sent"] += 1
            else:
                notif.retries += 1
                if notif.retries < notif.max_retries:
                    notif.status = "retry"
                    self.retry_queue.append(notif)
                    self._stats["retried"] += 1
                else:
                    notif.status = "failed"
                    self._stats["failed"] += 1
            processed += 1

        return {"processed": processed, "queue_remaining": len(self.queue)}

    def process_retries(self):
        pending = self.retry_queue[:]
        self.retry_queue.clear()
        for notif in pending:
            handler = self.handlers.get(notif.channel)
            if handler and handler.send(notif):
                notif.status = "sent"
                self._stats["sent"] += 1
            else:
                notif.retries += 1
                if notif.retries < notif.max_retries:
                    self.retry_queue.append(notif)
                else:
                    notif.status = "failed"
                    self._stats["failed"] += 1

    def stats(self) -> dict:
        return {
            **self._stats,
            "queue_size": len(self.queue),
            "retry_queue_size": len(self.retry_queue),
            "channels": {
                ch.value: len(h.sent)
                for ch, h in self.handlers.items()
            },
        }


# ===================================================================
# Demo
# ===================================================================
if __name__ == "__main__":
    print("=" * 65)
    print("  Notification System — Multi-Channel Dispatcher")
    print("=" * 65)

    svc = NotificationService()

    # Register templates
    svc.template_engine.register("order_shipped", "Your order is on the way!",
        "Hi {{name}}, order #{{order_id}} shipped via {{carrier}}.",
        [Channel.PUSH, Channel.EMAIL])

    svc.template_engine.register("otp", "Verification Code",
        "Your code is {{code}}. Expires in {{expiry}} minutes.",
        [Channel.SMS])

    svc.template_engine.register("promo", "Special Offer!",
        "{{name}}, get {{discount}}% off with code {{promo_code}}!",
        [Channel.EMAIL])

    # Set user preferences
    svc.set_user_preferences("user_1", [Channel.PUSH, Channel.EMAIL, Channel.SMS])
    svc.set_user_preferences("user_2", [Channel.PUSH, Channel.EMAIL])

    # Send critical OTP
    print("\n  Sending critical OTP notification...")
    svc.enqueue("user_1", Channel.SMS, priority=Priority.CRITICAL,
                template_id="otp", template_vars={"code": "482917", "expiry": "5"})

    # Send order notifications
    print("  Sending 10 order shipped notifications...")
    for i in range(10):
        svc.enqueue(f"user_{i % 3}", Channel.PUSH, priority=Priority.HIGH,
                    template_id="order_shipped",
                    template_vars={"name": f"User{i}", "order_id": f"ORD-{i}", "carrier": "FedEx"})

    # Send promotional bulk
    print("  Sending 20 promo notifications...")
    for i in range(20):
        svc.enqueue(f"user_{i % 5}", Channel.EMAIL, priority=Priority.LOW,
                    template_id="promo",
                    template_vars={"name": f"User{i}", "discount": "30", "promo_code": "SAVE30"})

    # Process
    print("\n  Processing queue...")
    result = svc.process_queue(max_batch=50)
    print(f"  Processed: {result}")

    # Retry failed
    print(f"\n  Retry queue size: {len(svc.retry_queue)}")
    if svc.retry_queue:
        print("  Processing retries...")
        svc.process_retries()

    # Template rendering
    print("\n  Template rendering examples:")
    title, body = svc.template_engine.render("order_shipped",
        {"name": "Alice", "order_id": "ORD-1234", "carrier": "UPS"})
    print(f"    Title: {title}")
    print(f"    Body:  {body}")

    title, body = svc.template_engine.render("otp", {"code": "123456", "expiry": "10"})
    print(f"    Title: {title}")
    print(f"    Body:  {body}")

    # Final stats
    print(f"\n  Stats: {svc.stats()}")
    print("\nDone.")
