# 15. Ticketmaster (Event Booking System)

> **Difficulty**: Hard | **Asked by**: Amazon, Microsoft, Google, Booking, Ticketmaster

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
1. View events and available seats
2. Select and temporarily hold seats
3. Complete booking within time limit (10 min)
4. Process payment and confirm booking
5. Support seat maps and different pricing tiers
6. Waitlist when sold out

### Non-Functional Requirements
1. **No Double-Booking**: Same seat never sold twice
2. **High Concurrency**: Handle 1M+ users for popular events
3. **Low Latency**: < 500ms for seat selection
4. **Fairness**: First-come-first-served for seat holds
5. **Availability**: 99.99% during on-sale events

---

## Capacity Estimation

```
Events/year: 100K
Seats per event: 50K average
Daily bookings: 500K
Peak: Major concert on-sale → 1M users in 60 seconds
Seat selection QPS: ~16,000 (peak)
Payment processing: ~8,000/min (peak)
Data per booking: ~1 KB
```

---

## High-Level Design

### Architecture Overview

```mermaid
graph TB
    Users[Users<br/>1M+ concurrent] --> Queue[Virtual Queue<br/>Waiting Room]
    Queue --> LB[Load Balancer]
    
    LB --> EventSvc[Event Service<br/>Browse & search]
    LB --> SeatSvc[Seat Service<br/>View & hold seats]
    LB --> BookingSvc[Booking Service<br/>Reserve & confirm]
    LB --> PaymentSvc[Payment Service<br/>Process payments]
    
    SeatSvc --> SeatDB[(Seat Inventory<br/>PostgreSQL)]
    SeatSvc --> SeatLock[(Distributed Lock<br/>Redis)]
    
    BookingSvc --> BookingDB[(Booking DB)]
    BookingSvc --> SeatDB
    
    PaymentSvc --> PayGW[Payment Gateway<br/>Stripe/Braintree]
    
    EventSvc --> EventDB[(Event DB<br/>+ Elasticsearch)]
    EventSvc --> CDN[CDN<br/>Event pages, images]
    
    subgraph "Background"
        Timer[Seat Hold Timer<br/>Release expired holds]
        Waitlist[Waitlist Service]
        Notif[Notification Service]
    end
    
    Timer --> SeatDB
    Waitlist --> SeatDB
    Timer --> Waitlist
```

### Booking Flow

```mermaid
sequenceDiagram
    participant U as User
    participant Q as Queue Service
    participant Seat as Seat Service
    participant Lock as Redis Lock
    participant Book as Booking Service
    participant Pay as Payment Service
    participant DB as Database
    
    U->>Q: Join queue for event
    Q-->>U: Your position: 5,234 (wait...)
    Q-->>U: Your turn! (redirect to booking page)
    
    U->>Seat: View available seats
    Seat->>DB: Get seat map
    DB-->>Seat: Available seats
    Seat-->>U: Seat map with availability
    
    U->>Seat: Select seats [A1, A2]
    Seat->>Lock: Try lock seats A1, A2 (10 min TTL)
    
    alt Seats available
        Lock-->>Seat: Locked ✅
        Seat->>DB: Mark seats as HELD
        Seat-->>U: Seats held for 10:00 minutes
        
        U->>Book: Confirm booking
        Book->>Pay: Process payment $200
        Pay-->>Book: Payment success
        Book->>DB: Mark seats as BOOKED
        Book->>Lock: Release locks
        Book-->>U: Booking confirmed! 🎉
    else Seats already taken
        Lock-->>Seat: Lock failed ❌
        Seat-->>U: Seats unavailable, choose others
    end
    
    Note over Lock: If 10 min expires without booking,<br/>auto-release seats back to available
```

---

## Low-Level Design

### Seat State Machine

```mermaid
stateDiagram-v2
    [*] --> AVAILABLE
    AVAILABLE --> HELD: User selects seat<br/>(set 10 min timer)
    HELD --> BOOKED: Payment confirmed
    HELD --> AVAILABLE: Timer expires<br/>or user cancels
    BOOKED --> AVAILABLE: Refund/cancellation
    BOOKED --> [*]: Event completed
```

### Data Models

```mermaid
erDiagram
    EVENT {
        bigint id PK
        varchar name
        varchar venue
        timestamp event_date
        varchar status "upcoming|on_sale|sold_out|completed"
        timestamp on_sale_date
        int total_seats
        int available_seats
    }
    
    SEAT {
        bigint id PK
        bigint event_id FK
        varchar section
        int row
        int number
        varchar tier "VIP|premium|standard"
        decimal price
        varchar status "available|held|booked"
        bigint held_by_user_id
        timestamp held_until
        bigint booking_id FK
    }
    
    BOOKING {
        bigint id PK
        bigint user_id FK
        bigint event_id FK
        varchar status "pending|confirmed|cancelled|refunded"
        decimal total_amount
        varchar payment_id
        timestamp created_at
        timestamp confirmed_at
    }
    
    BOOKING_SEAT {
        bigint booking_id PK
        bigint seat_id PK
    }
    
    EVENT ||--|{ SEAT : contains
    BOOKING ||--|{ BOOKING_SEAT : includes
    SEAT ||--o| BOOKING_SEAT : reserved_in
```

### Distributed Locking for Seat Reservation

```mermaid
flowchart TD
    Request["Select Seat A1, A2"] --> TryLock["Redis: SET seat:{event}:{A1} user_123 NX EX 600"]
    TryLock --> R1{Lock A1<br/>acquired?}
    R1 -->|Yes| TryA2["SET seat:{event}:{A2} user_123 NX EX 600"]
    R1 -->|No| Fail["❌ Seat A1 taken"]
    
    TryA2 --> R2{Lock A2<br/>acquired?}
    R2 -->|Yes| Success["✅ Both seats held<br/>Update DB status"]
    R2 -->|No| Rollback["Release A1 lock<br/>❌ Seat A2 taken"]
    
    subgraph "Auto-Release"
        Timer["Redis TTL: 600 seconds (10 min)"]
        Timer --> Expire["Lock expires → seat released"]
        Expire --> DBUpdate["Background: Update DB status<br/>to AVAILABLE"]
    end
```

### Virtual Queue Design

```mermaid
flowchart TD
    subgraph "Queue Mechanism"
        Enter["1M users arrive for on-sale"] --> Queue["Virtual Queue<br/>(Redis Sorted Set)"]
        Queue --> Position["Each user gets position<br/>ZADD queue timestamp user_id"]
        
        Batch["Release in batches<br/>500 users every 5 seconds"] --> Active["Active shopping pool<br/>Max 5,000 concurrent"]
        
        Active --> Timer2{Shopping time<br/>limit: 15 min}
        Timer2 -->|Expired| Remove["Remove from active pool<br/>Allow next in queue"]
        Timer2 -->|Booked| Remove2["Free slot for next user"]
    end
    
    subgraph "Queue Status Page"
        UserView["Your position: 5,234<br/>Estimated wait: 8 minutes<br/>Auto-refresh every 10s"]
    end
```

### Optimistic vs Pessimistic Locking

```mermaid
graph TD
    subgraph "Pessimistic Locking (Used Here ✅)"
        PL1["SELECT ... FOR UPDATE<br/>Lock rows in DB"]
        PL2["Hold lock during<br/>entire transaction"]
        PL3["✅ Prevents conflicts<br/>❌ Lower throughput"]
    end
    
    subgraph "Optimistic Locking (Alternative)"
        OL1["Read version number<br/>version = 5"]
        OL2["UPDATE ... WHERE version = 5<br/>SET version = 6"]
        OL3["If 0 rows affected → conflict<br/>Retry with new version"]
        OL4["✅ Higher throughput<br/>❌ Client retries needed"]
    end
```

---

## Implementation

### Seat Reservation Service

```python
import time
import uuid
import redis
from typing import List, Optional
from contextlib import contextmanager

class SeatReservationService:
    """Handles seat hold, booking, and release with distributed locks."""
    
    HOLD_DURATION = 600  # 10 minutes
    
    def __init__(self, redis_client: redis.Redis, db):
        self.redis = redis_client
        self.db = db
    
    def hold_seats(self, event_id: int, seat_ids: List[int],
                   user_id: int) -> dict:
        """Atomically hold multiple seats with distributed locks."""
        hold_id = str(uuid.uuid4())
        locked_seats = []
        
        try:
            # Try to lock each seat atomically
            for seat_id in seat_ids:
                lock_key = f"seat_lock:{event_id}:{seat_id}"
                acquired = self.redis.set(
                    lock_key, 
                    f"{user_id}:{hold_id}",
                    nx=True,  # Only if not exists
                    ex=self.HOLD_DURATION
                )
                
                if not acquired:
                    # Rollback: release already-locked seats
                    for locked_id in locked_seats:
                        self._release_lock(event_id, locked_id, user_id)
                    return {
                        "status": "failed",
                        "message": f"Seat {seat_id} is not available"
                    }
                
                locked_seats.append(seat_id)
            
            # All seats locked - update DB
            self.db.execute("""
                UPDATE seats 
                SET status = 'held', held_by_user_id = %s, 
                    held_until = NOW() + INTERVAL '10 minutes'
                WHERE id = ANY(%s) AND event_id = %s AND status = 'available'
            """, (user_id, seat_ids, event_id))
            
            return {
                "status": "held",
                "hold_id": hold_id,
                "seats": seat_ids,
                "expires_at": time.time() + self.HOLD_DURATION
            }
        except Exception as e:
            # Rollback on any error
            for locked_id in locked_seats:
                self._release_lock(event_id, locked_id, user_id)
            raise
    
    def confirm_booking(self, event_id: int, user_id: int,
                        seat_ids: List[int], payment_info: dict) -> dict:
        """Confirm booking after payment."""
        
        # 1. Verify seats are still held by this user
        for seat_id in seat_ids:
            lock_key = f"seat_lock:{event_id}:{seat_id}"
            lock_value = self.redis.get(lock_key)
            if not lock_value or not lock_value.decode().startswith(f"{user_id}:"):
                return {"status": "expired", "message": "Hold expired"}
        
        # 2. Process payment
        payment_result = self._process_payment(payment_info)
        if not payment_result["success"]:
            return {"status": "payment_failed"}
        
        # 3. Update DB atomically
        booking_id = self.db.execute("""
            WITH booking AS (
                INSERT INTO bookings (user_id, event_id, status, total_amount, payment_id)
                VALUES (%s, %s, 'confirmed', %s, %s)
                RETURNING id
            )
            UPDATE seats SET status = 'booked', booking_id = (SELECT id FROM booking)
            WHERE id = ANY(%s) AND event_id = %s AND held_by_user_id = %s
        """, (user_id, event_id, payment_info["amount"], 
              payment_result["payment_id"], seat_ids, event_id, user_id))
        
        # 4. Release locks
        for seat_id in seat_ids:
            self._release_lock(event_id, seat_id, user_id)
        
        return {"status": "confirmed", "booking_id": booking_id}
    
    def _release_lock(self, event_id: int, seat_id: int, user_id: int):
        """Release lock only if owned by this user (Lua script for atomicity)."""
        lua_script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        end
        return 0
        """
        lock_key = f"seat_lock:{event_id}:{seat_id}"
        self.redis.eval(lua_script, 1, lock_key, f"{user_id}")
    
    def _process_payment(self, payment_info):
        """Process payment via payment gateway."""
        return {"success": True, "payment_id": str(uuid.uuid4())}


class SeatHoldExpirationWorker:
    """Background worker to release expired seat holds."""
    
    def __init__(self, db, redis_client, waitlist_service):
        self.db = db
        self.redis = redis_client
        self.waitlist = waitlist_service
    
    def run(self):
        """Run periodically (every 30 seconds)."""
        expired = self.db.execute("""
            UPDATE seats SET status = 'available', 
                held_by_user_id = NULL, held_until = NULL
            WHERE status = 'held' AND held_until < NOW()
            RETURNING id, event_id
        """)
        
        for seat_id, event_id in expired:
            # Notify waitlist
            self.waitlist.notify_availability(event_id, seat_id)
```

---

## Limitations & Improvements

### Current Limitations

| Limitation | Impact | Severity |
|-----------|--------|----------|
| Thundering herd at on-sale time | System overload | Critical |
| Seat hold squatting (hold & abandon) | Inventory unavailable to real buyers | High |
| Single-event bottleneck | One popular event impacts all | High |
| Bot/scalper abuse | Unfair for real fans | Critical |
| Payment failure after hold | Complex cleanup | Medium |

### Improvement Areas

1. **Queue-Based Flow Control** — Virtual waiting room to throttle concurrent users
2. **Anti-Bot** — CAPTCHA, device fingerprinting, behavioral analysis
3. **Dynamic Hold Duration** — Shorter holds during high demand
4. **Reserved Inventory** — Pre-allocate seats to fan clubs, credit card holders
5. **Fallback to General Admission** — When seat selection overwhelms, offer "best available"

---

## Key Interview Discussion Points

1. **How to prevent double-booking?** Distributed lock (Redis NX) + DB constraint (UNIQUE on seat+event booking)
2. **Pessimistic vs optimistic locking?** Pessimistic for high-contention (seats); optimistic for low-contention
3. **How to handle 1M concurrent users?** Virtual queue → rate-limit active shoppers → handle in batches
4. **What if payment fails after hold?** Release seats back to available; retry payment; offer to hold again
5. **How to fight scalpers?** Verified fan programs, CAPTCHA, purchase limits, non-transferable tickets
