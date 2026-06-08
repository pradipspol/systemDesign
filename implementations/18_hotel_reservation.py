"""
=============================================================
  18. Hotel Reservation — Booking with Optimistic Locking
  Run: python 18_hotel_reservation.py
  Implements room availability, optimistic-locking reservations,
  overbooking protection, cancellation, and dynamic pricing.
=============================================================
"""
import time
import uuid
import threading
from datetime import date, timedelta
from enum import Enum
from dataclasses import dataclass, field
from collections import defaultdict


# ===================================================================
# Data Models
# ===================================================================
class RoomType(Enum):
    STANDARD = "standard"
    DELUXE = "deluxe"
    SUITE = "suite"
    PENTHOUSE = "penthouse"


class BookingStatus(Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    CHECKED_IN = "checked_in"
    CHECKED_OUT = "checked_out"


@dataclass
class Room:
    room_id: str
    room_type: RoomType
    base_price: float
    floor: int
    capacity: int


@dataclass
class RoomAvailability:
    """Per-room, per-date availability with optimistic locking."""
    room_id: str
    date: str           # "YYYY-MM-DD"
    is_available: bool = True
    booking_id: str = ""
    version: int = 0    # for optimistic locking
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


@dataclass
class Booking:
    booking_id: str
    guest_id: str
    room_id: str
    room_type: RoomType
    check_in: str
    check_out: str
    total_price: float
    status: BookingStatus = BookingStatus.PENDING
    created_at: float = field(default_factory=time.time)
    nights: int = 0


# ===================================================================
# Dynamic Pricing Engine
# ===================================================================
class DynamicPricing:
    """Adjust price based on occupancy, day of week, and season."""

    WEEKEND_MULTIPLIER = 1.3
    HIGH_SEASON_MONTHS = {6, 7, 8, 12}
    HIGH_SEASON_MULTIPLIER = 1.5

    def calculate(self, base_price: float, dt: str, occupancy_rate: float) -> float:
        d = date.fromisoformat(dt)
        price = base_price

        # Weekend surcharge
        if d.weekday() >= 5:
            price *= self.WEEKEND_MULTIPLIER

        # High season
        if d.month in self.HIGH_SEASON_MONTHS:
            price *= self.HIGH_SEASON_MULTIPLIER

        # Demand-based (linear ramp)
        if occupancy_rate > 0.8:
            price *= 1.0 + (occupancy_rate - 0.8) * 2.5  # up to 1.5x at 100%
        elif occupancy_rate < 0.3:
            price *= 0.85  # discount for low occupancy

        return round(price, 2)


# ===================================================================
# Hotel Service
# ===================================================================
class HotelService:
    MAX_RETRIES = 3

    def __init__(self):
        self.rooms: dict[str, Room] = {}
        # (room_id, date_str) -> RoomAvailability
        self.availability: dict[tuple[str, str], RoomAvailability] = {}
        self.bookings: dict[str, Booking] = {}
        self.pricing = DynamicPricing()
        self._stats = {"bookings": 0, "cancellations": 0, "conflicts": 0}
        self._lock = threading.Lock()

    def add_room(self, room: Room):
        self.rooms[room.room_id] = room

    def _ensure_availability(self, room_id: str, date_str: str) -> RoomAvailability:
        key = (room_id, date_str)
        if key not in self.availability:
            self.availability[key] = RoomAvailability(room_id=room_id, date=date_str)
        return self.availability[key]

    def _date_range(self, check_in: str, check_out: str) -> list[str]:
        start = date.fromisoformat(check_in)
        end = date.fromisoformat(check_out)
        return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days)]

    def search_available(self, room_type: RoomType, check_in: str, check_out: str) -> list[dict]:
        dates = self._date_range(check_in, check_out)
        results = []
        for room in self.rooms.values():
            if room.room_type != room_type:
                continue
            available = True
            total = 0.0
            for d in dates:
                avail = self._ensure_availability(room.room_id, d)
                if not avail.is_available:
                    available = False
                    break
                occ_rate = self._occupancy_rate(d)
                total += self.pricing.calculate(room.base_price, d, occ_rate)
            if available:
                results.append({
                    "room_id": room.room_id,
                    "room_type": room.room_type.value,
                    "floor": room.floor,
                    "total_price": round(total, 2),
                    "per_night_avg": round(total / max(1, len(dates)), 2),
                    "nights": len(dates),
                })
        results.sort(key=lambda x: x["total_price"])
        return results

    def _occupancy_rate(self, date_str: str) -> float:
        total = len(self.rooms)
        if total == 0:
            return 0.0
        booked = sum(
            1 for rid in self.rooms
            if not self._ensure_availability(rid, date_str).is_available
        )
        return booked / total

    def book(self, guest_id: str, room_id: str, check_in: str, check_out: str) -> dict:
        """Reserve room with optimistic locking + retry."""
        room = self.rooms.get(room_id)
        if not room:
            return {"error": "Room not found"}

        dates = self._date_range(check_in, check_out)
        if not dates:
            return {"error": "Invalid date range"}

        for attempt in range(self.MAX_RETRIES):
            # Snapshot versions
            snapshots = {}
            for d in dates:
                avail = self._ensure_availability(room_id, d)
                if not avail.is_available:
                    return {"error": f"Room not available on {d}"}
                snapshots[d] = avail.version

            # Calculate price
            total = sum(
                self.pricing.calculate(room.base_price, d, self._occupancy_rate(d))
                for d in dates
            )

            # Optimistic lock: verify versions and book atomically
            booking_id = f"bk_{uuid.uuid4().hex[:8]}"
            success = True

            with self._lock:
                for d in dates:
                    avail = self.availability[(room_id, d)]
                    if avail.version != snapshots[d] or not avail.is_available:
                        success = False
                        self._stats["conflicts"] += 1
                        break

                if success:
                    for d in dates:
                        avail = self.availability[(room_id, d)]
                        avail.is_available = False
                        avail.booking_id = booking_id
                        avail.version += 1

            if success:
                booking = Booking(
                    booking_id=booking_id,
                    guest_id=guest_id,
                    room_id=room_id,
                    room_type=room.room_type,
                    check_in=check_in,
                    check_out=check_out,
                    total_price=round(total, 2),
                    status=BookingStatus.CONFIRMED,
                    nights=len(dates),
                )
                self.bookings[booking_id] = booking
                self._stats["bookings"] += 1
                return {
                    "status": "confirmed",
                    "booking_id": booking_id,
                    "room_id": room_id,
                    "total_price": round(total, 2),
                    "nights": len(dates),
                    "attempt": attempt + 1,
                }

        return {"error": "Booking failed after retries (concurrent conflict)"}

    def cancel(self, booking_id: str) -> dict:
        booking = self.bookings.get(booking_id)
        if not booking:
            return {"error": "Booking not found"}
        if booking.status == BookingStatus.CANCELLED:
            return {"error": "Already cancelled"}

        dates = self._date_range(booking.check_in, booking.check_out)
        with self._lock:
            for d in dates:
                key = (booking.room_id, d)
                if key in self.availability:
                    avail = self.availability[key]
                    avail.is_available = True
                    avail.booking_id = ""
                    avail.version += 1

        booking.status = BookingStatus.CANCELLED
        self._stats["cancellations"] += 1

        # Calculate refund (full if > 48h before check-in)
        hours_until = (date.fromisoformat(booking.check_in) - date.today()).days * 24
        refund = booking.total_price if hours_until > 48 else booking.total_price * 0.5
        return {"status": "cancelled", "refund": round(refund, 2)}

    def stats(self) -> dict:
        return {
            **self._stats,
            "total_rooms": len(self.rooms),
            "active_bookings": sum(1 for b in self.bookings.values()
                                    if b.status == BookingStatus.CONFIRMED),
        }


# ===================================================================
# Demo
# ===================================================================
if __name__ == "__main__":
    print("=" * 65)
    print("  Hotel Reservation — Optimistic Locking + Dynamic Pricing")
    print("=" * 65)

    svc = HotelService()

    # Add rooms
    room_configs = [
        ("101", RoomType.STANDARD, 100, 1, 2),
        ("102", RoomType.STANDARD, 100, 1, 2),
        ("201", RoomType.DELUXE, 180, 2, 2),
        ("202", RoomType.DELUXE, 180, 2, 3),
        ("301", RoomType.SUITE, 350, 3, 4),
        ("302", RoomType.SUITE, 350, 3, 4),
        ("401", RoomType.PENTHOUSE, 800, 4, 6),
    ]
    for rid, rtype, price, floor, cap in room_configs:
        svc.add_room(Room(rid, rtype, price, floor, cap))

    print(f"\n  Hotel has {len(svc.rooms)} rooms")

    # Search available rooms
    check_in = "2025-07-15"  # High season (July), Tuesday
    check_out = "2025-07-18"  # 3 nights, includes weekend

    print(f"\n  Searching Deluxe rooms: {check_in} to {check_out}")
    results = svc.search_available(RoomType.DELUXE, check_in, check_out)
    for r in results:
        print(f"    Room {r['room_id']}: ${r['total_price']:.2f} total "
              f"(${r['per_night_avg']:.2f}/night, {r['nights']} nights)")

    # Book a room
    print(f"\n  Booking Deluxe room 201...")
    booking = svc.book("guest_alice", "201", check_in, check_out)
    print(f"    Result: {booking}")

    # Try to book same room (conflict)
    print(f"\n  Another guest trying same room...")
    conflict = svc.book("guest_bob", "201", "2025-07-16", "2025-07-19")
    print(f"    Result: {conflict}")

    # Book other rooms
    svc.book("guest_bob", "202", "2025-07-15", "2025-07-17")
    svc.book("guest_charlie", "301", "2025-07-15", "2025-07-20")

    # Dynamic pricing: show price changes with occupancy
    print(f"\n  Dynamic Pricing Examples (base=$100 Standard):")
    test_dates = [
        ("2025-03-15", "Low season weekday"),      # Low season, weekday
        ("2025-03-16", "Low season weekend"),       # Low season, weekend
        ("2025-07-15", "High season Tuesday"),      # High season, weekday
        ("2025-07-19", "High season Saturday"),     # High season, weekend
        ("2025-12-25", "Christmas"),                # December
    ]
    pricing = DynamicPricing()
    for dt, desc in test_dates:
        for occ in [0.2, 0.5, 0.9]:
            price = pricing.calculate(100.0, dt, occ)
            print(f"    {desc:25s} occ={occ:.0%}  →  ${price:>7.2f}")

    # Cancel a booking
    if "booking_id" in booking:
        print(f"\n  Cancelling booking {booking['booking_id']}...")
        cancel_result = svc.cancel(booking["booking_id"])
        print(f"    Result: {cancel_result}")

    # Concurrent booking test
    print(f"\n  Concurrent booking test (5 guests for 1 room):")
    results = []
    def try_book(guest_id):
        r = svc.book(guest_id, "101", "2025-08-01", "2025-08-03")
        results.append((guest_id, r))

    threads = [threading.Thread(target=try_book, args=(f"guest_{i}",)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for gid, r in results:
        status = r.get("status", r.get("error", "unknown"))
        print(f"    {gid}: {status}")

    print(f"\n  Stats: {svc.stats()}")
    print("\nDone.")
