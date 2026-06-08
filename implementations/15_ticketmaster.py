"""
=============================================================
  15. Ticketmaster — Seat Reservation with Distributed Locks
  Run: python 15_ticketmaster.py
  Test: curl http://localhost:5015/api/v1/events
        curl -X POST http://localhost:5015/api/v1/reserve -H "Content-Type: application/json" -d "{\"event_id\":\"evt_1\",\"user_id\":\"user_1\",\"seat_ids\":[\"A1\",\"A2\"]}"
=============================================================
"""
import time
import uuid
import threading
from enum import Enum
from dataclasses import dataclass, field
from collections import defaultdict
from flask import Flask, request, jsonify

app = Flask(__name__)


# ===================================================================
# Data Models
# ===================================================================
class SeatStatus(Enum):
    AVAILABLE = "available"
    HELD = "held"         # temporarily held during checkout
    RESERVED = "reserved" # payment confirmed
    SOLD = "sold"


@dataclass
class Seat:
    seat_id: str
    section: str
    row: str
    number: int
    price: float
    status: SeatStatus = SeatStatus.AVAILABLE
    held_by: str = ""
    held_until: float = 0.0
    reserved_by: str = ""


@dataclass
class Event:
    event_id: str
    name: str
    venue: str
    date: str
    total_seats: int = 0
    seats: dict[str, Seat] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


# ===================================================================
# Virtual Queue (for high-demand events)
# ===================================================================
class VirtualQueue:
    def __init__(self, max_active: int = 100):
        self.max_active = max_active
        self.waiting: list[tuple[float, str]] = []  # (join_time, user_id)
        self.active: set[str] = set()
        self._lock = threading.Lock()

    def join(self, user_id: str) -> dict:
        with self._lock:
            if user_id in self.active:
                return {"status": "active", "position": 0}
            for i, (_, uid) in enumerate(self.waiting):
                if uid == user_id:
                    return {"status": "waiting", "position": i + 1}
            self.waiting.append((time.time(), user_id))
            self._promote()
            if user_id in self.active:
                return {"status": "active", "position": 0}
            pos = next(i for i, (_, uid) in enumerate(self.waiting) if uid == user_id) + 1
            return {"status": "waiting", "position": pos, "estimated_wait_s": pos * 30}

    def _promote(self):
        while self.waiting and len(self.active) < self.max_active:
            _, uid = self.waiting.pop(0)
            self.active.add(uid)

    def release(self, user_id: str):
        with self._lock:
            self.active.discard(user_id)
            self._promote()

    def is_active(self, user_id: str) -> bool:
        return user_id in self.active


# ===================================================================
# Lock Manager (simulates Redis SETNX for seat holding)
# ===================================================================
class SeatLockManager:
    HOLD_DURATION = 300  # 5 minutes

    def __init__(self):
        self._locks: dict[str, tuple[str, float]] = {}  # seat_key -> (user_id, expires)
        self._lock = threading.Lock()

    def acquire(self, seat_key: str, user_id: str) -> bool:
        with self._lock:
            now = time.time()
            existing = self._locks.get(seat_key)
            if existing:
                holder, expires = existing
                if expires > now and holder != user_id:
                    return False  # someone else holds
                # Expired or same user
            self._locks[seat_key] = (user_id, now + self.HOLD_DURATION)
            return True

    def release(self, seat_key: str, user_id: str):
        with self._lock:
            existing = self._locks.get(seat_key)
            if existing and existing[0] == user_id:
                del self._locks[seat_key]

    def is_held(self, seat_key: str) -> bool:
        existing = self._locks.get(seat_key)
        if existing:
            return existing[1] > time.time()
        return False

    def cleanup_expired(self) -> int:
        with self._lock:
            now = time.time()
            expired = [k for k, (_, exp) in self._locks.items() if exp <= now]
            for k in expired:
                del self._locks[k]
            return len(expired)


# ===================================================================
# Booking Service
# ===================================================================
class BookingService:
    def __init__(self):
        self.events: dict[str, Event] = {}
        self.queues: dict[str, VirtualQueue] = {}
        self.lock_manager = SeatLockManager()
        self._stats = {"reservations": 0, "holds": 0, "expired_holds": 0, "conflicts": 0}

    def create_event(self, event_id: str, name: str, venue: str, date: str,
                     sections: dict[str, dict]) -> Event:
        """Create event with sections. sections = {section: {rows: {row: {count, price}}} }"""
        event = Event(event_id=event_id, name=name, venue=venue, date=date)
        for section, config in sections.items():
            for row, row_cfg in config.items():
                for num in range(1, row_cfg["count"] + 1):
                    seat_id = f"{row}{num}"
                    seat = Seat(
                        seat_id=seat_id, section=section,
                        row=row, number=num, price=row_cfg["price"]
                    )
                    event.seats[seat_id] = seat
                    event.total_seats += 1
        self.events[event_id] = event
        self.queues[event_id] = VirtualQueue(max_active=200)
        return event

    def get_available_seats(self, event_id: str, section: str = None) -> list[dict]:
        event = self.events.get(event_id)
        if not event:
            return []
        self.lock_manager.cleanup_expired()
        result = []
        for sid, seat in event.seats.items():
            if seat.status != SeatStatus.AVAILABLE:
                continue
            if section and seat.section != section:
                continue
            if self.lock_manager.is_held(f"{event_id}:{sid}"):
                continue
            result.append({
                "seat_id": sid, "section": seat.section,
                "row": seat.row, "number": seat.number, "price": seat.price,
            })
        return result

    def hold_seats(self, event_id: str, user_id: str, seat_ids: list[str]) -> dict:
        event = self.events.get(event_id)
        if not event:
            return {"error": "Event not found"}

        # All-or-nothing: try to lock all seats
        locked = []
        for sid in seat_ids:
            seat = event.seats.get(sid)
            if not seat or seat.status != SeatStatus.AVAILABLE:
                # Rollback
                for prev_sid in locked:
                    self.lock_manager.release(f"{event_id}:{prev_sid}", user_id)
                self._stats["conflicts"] += 1
                return {"error": f"Seat {sid} not available"}

            if not self.lock_manager.acquire(f"{event_id}:{sid}", user_id):
                for prev_sid in locked:
                    self.lock_manager.release(f"{event_id}:{prev_sid}", user_id)
                self._stats["conflicts"] += 1
                return {"error": f"Seat {sid} held by another user"}
            locked.append(sid)

        # Mark seats as held
        for sid in seat_ids:
            event.seats[sid].status = SeatStatus.HELD
            event.seats[sid].held_by = user_id
            event.seats[sid].held_until = time.time() + SeatLockManager.HOLD_DURATION

        self._stats["holds"] += 1
        total = sum(event.seats[sid].price for sid in seat_ids)
        return {
            "status": "held",
            "seats": seat_ids,
            "total_price": total,
            "expires_in_seconds": SeatLockManager.HOLD_DURATION,
            "hold_id": str(uuid.uuid4())[:8],
        }

    def confirm_reservation(self, event_id: str, user_id: str, seat_ids: list[str]) -> dict:
        event = self.events.get(event_id)
        if not event:
            return {"error": "Event not found"}

        for sid in seat_ids:
            seat = event.seats.get(sid)
            if not seat or seat.held_by != user_id:
                return {"error": f"Seat {sid} not held by you"}

        total = 0
        for sid in seat_ids:
            seat = event.seats[sid]
            seat.status = SeatStatus.RESERVED
            seat.reserved_by = user_id
            self.lock_manager.release(f"{event_id}:{sid}", user_id)
            total += seat.price

        self._stats["reservations"] += 1
        return {
            "status": "confirmed",
            "reservation_id": str(uuid.uuid4())[:8],
            "seats": seat_ids,
            "total_charged": total,
        }

    def event_summary(self, event_id: str) -> dict:
        event = self.events.get(event_id)
        if not event:
            return {}
        status_counts = defaultdict(int)
        for seat in event.seats.values():
            status_counts[seat.status.value] += 1
        return {
            "event_id": event_id,
            "name": event.name,
            "total_seats": event.total_seats,
            **dict(status_counts),
        }


# ===================================================================
# Singleton service
# ===================================================================
svc = BookingService()


# ===================================================================
# Flask Routes
# ===================================================================
@app.route("/api/v1/events")
def list_events():
    return jsonify([svc.event_summary(eid) for eid in svc.events])


@app.route("/api/v1/events/<event_id>/seats")
def get_seats(event_id):
    section = request.args.get("section")
    return jsonify(svc.get_available_seats(event_id, section))


@app.route("/api/v1/reserve", methods=["POST"])
def reserve():
    data = request.get_json(force=True)
    event_id = data["event_id"]
    user_id = data["user_id"]
    seat_ids = data["seat_ids"]

    # Hold first
    hold = svc.hold_seats(event_id, user_id, seat_ids)
    if "error" in hold:
        return jsonify(hold), 409

    # Immediately confirm (in production, wait for payment)
    confirm = svc.confirm_reservation(event_id, user_id, seat_ids)
    return jsonify(confirm), 201


@app.route("/api/v1/queue/<event_id>/join", methods=["POST"])
def join_queue(event_id):
    data = request.get_json(force=True)
    result = svc.queues[event_id].join(data["user_id"])
    return jsonify(result)


# ===================================================================
# Demo
# ===================================================================
def init_demo():
    svc.create_event("evt_1", "Taylor Swift Concert", "MetLife Stadium", "2025-08-15", {
        "VIP": {"A": {"count": 10, "price": 500.0}, "B": {"count": 10, "price": 400.0}},
        "Floor": {"C": {"count": 20, "price": 250.0}, "D": {"count": 20, "price": 200.0}},
        "Upper": {"E": {"count": 30, "price": 100.0}, "F": {"count": 30, "price": 75.0}},
    })
    svc.create_event("evt_2", "NBA Finals Game 7", "Chase Center", "2025-06-20", {
        "Courtside": {"A": {"count": 5, "price": 5000.0}},
        "Lower": {"B": {"count": 20, "price": 800.0}, "C": {"count": 20, "price": 600.0}},
        "Upper": {"D": {"count": 40, "price": 200.0}, "E": {"count": 40, "price": 150.0}},
    })


if __name__ == "__main__":
    init_demo()

    print("=" * 60)
    print("  Ticketmaster — Seat Reservation System")
    print("  http://localhost:5015")
    print()

    # Demo reservation flow
    print("  Demo: Reservation flow")
    summary = svc.event_summary("evt_1")
    print(f"  Event: {summary['name']} — {summary['total_seats']} seats")

    # Virtual queue
    for i in range(5):
        result = svc.queues["evt_1"].join(f"user_{i}")
        if i < 2:
            print(f"  Queue: user_{i} → {result}")

    # Hold seats
    hold = svc.hold_seats("evt_1", "user_0", ["A1", "A2"])
    print(f"\n  Hold: {hold}")

    # Try conflict
    conflict = svc.hold_seats("evt_1", "user_1", ["A1", "A3"])
    print(f"  Conflict: {conflict}")

    # Confirm
    confirm = svc.confirm_reservation("evt_1", "user_0", ["A1", "A2"])
    print(f"  Confirm: {confirm}")

    # Available seats
    avail = svc.get_available_seats("evt_1", "VIP")
    print(f"\n  VIP seats remaining: {len(avail)}")
    print(f"  Event summary: {svc.event_summary('evt_1')}")

    # Concurrent test
    print("\n  Concurrent reservation test (10 threads for same seat):")
    results = []
    def try_reserve(user_id):
        r = svc.hold_seats("evt_2", user_id, ["A1"])
        results.append((user_id, r))

    threads = [threading.Thread(target=try_reserve, args=(f"t_user_{i}",)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    won = [uid for uid, r in results if "error" not in r]
    lost = [uid for uid, r in results if "error" in r]
    print(f"    Winner: {won}  Conflicts: {len(lost)}")

    print(f"\n  Stats: {svc._stats}")
    print("=" * 60)
    app.run(port=5015, debug=True)
