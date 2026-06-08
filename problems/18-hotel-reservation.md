# 18. Hotel Reservation System

> **Difficulty**: Medium | **Asked by**: Airbnb, Booking.com, Expedia, Amazon, Google

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
1. Search hotels by location, dates, guests, filters
2. View hotel details, room types, photos, reviews
3. Check room availability for date range
4. Book rooms with payment
5. Cancel/modify reservations
6. Hotel management portal (manage rooms, rates, availability)

### Non-Functional Requirements
1. **No Double-Booking**: Same room never booked twice for same dates
2. **High Availability**: 99.99% (bookings = revenue)
3. **Low Latency**: Search < 500ms, booking < 2s
4. **Scalability**: 5,000 cities, 1M hotels, 100M rooms
5. **Consistency**: Strong for reservations; eventual for search/reviews

---

## Capacity Estimation

```
Hotels: 1M
Rooms: 100M total
Daily searches: 500M
Daily bookings: 5M
Booking QPS: ~58 (avg), 500 (peak)
Search QPS: ~5,800 (avg), 20K (peak)
Average stay: 3 nights
Room-nights/day: 15M

Storage:
  Hotels: 1M × 10KB = 10 GB
  Rooms: 100M × 1KB = 100 GB
  Reservations: 5M/day × 2KB = 10 GB/day (3.6 TB/year)
  Availability: 100M rooms × 365 days × 20B = 730 GB
```

---

## High-Level Design

### Architecture Overview

```mermaid
graph TB
    User[User/Guest] --> CDN[CDN<br/>Images, static]
    User --> GW[API Gateway]
    
    HotelMgr[Hotel Manager] --> AdminGW[Admin API]
    
    GW --> SearchSvc[Search Service]
    GW --> HotelSvc[Hotel Service]
    GW --> RoomSvc[Room/Availability Service]
    GW --> BookingSvc[Booking Service]
    GW --> PaySvc[Payment Service]
    GW --> ReviewSvc[Review Service]
    
    SearchSvc --> ES[(Elasticsearch<br/>Hotel Search Index)]
    SearchSvc --> AvailCache[(Availability Cache<br/>Redis)]
    
    HotelSvc --> HotelDB[(Hotel DB<br/>PostgreSQL)]
    
    RoomSvc --> AvailDB[(Availability DB<br/>PostgreSQL)]
    RoomSvc --> AvailCache
    
    BookingSvc --> BookingDB[(Booking DB<br/>PostgreSQL)]
    BookingSvc --> RoomSvc
    BookingSvc --> PaySvc
    
    PaySvc --> PayGW[Payment Gateway]
    
    subgraph "Event Bus"
        Kafka[(Kafka)]
    end
    
    BookingSvc --> Kafka
    Kafka --> SearchSvc
    Kafka --> NotifSvc[Notification Service]
```

### Search & Booking Flow

```mermaid
sequenceDiagram
    participant U as User
    participant Search as Search Service
    participant ES as Elasticsearch
    participant Avail as Availability Svc
    participant Book as Booking Service
    participant Pay as Payment Service
    participant DB as Booking DB
    
    U->>Search: Search hotels (NYC, Oct 1-3, 2 guests)
    Search->>ES: Full-text + geo search
    ES-->>Search: 200 matching hotels
    Search->>Avail: Check availability (batch)
    Avail-->>Search: Hotels with available rooms
    Search-->>U: 150 available hotels (sorted by relevance)
    
    U->>Avail: View rooms for Hotel X, Oct 1-3
    Avail->>Avail: Query availability for date range
    Avail-->>U: [Standard: $150/night, Deluxe: $250/night]
    
    U->>Book: Book Deluxe, Oct 1-3
    Book->>Avail: Reserve room for dates
    
    alt Room available
        Avail-->>Book: Reserved (10 min hold)
        Book->>Pay: Charge $750
        Pay-->>Book: Payment success
        Book->>DB: Create reservation
        Book->>Avail: Confirm reservation
        Book-->>U: Booking confirmed! ✅
    else Room unavailable
        Avail-->>Book: No availability
        Book-->>U: Room no longer available 😞
    end
```

---

## Low-Level Design

### Data Models

```mermaid
erDiagram
    HOTEL {
        bigint id PK
        varchar name
        text description
        varchar address
        varchar city
        varchar country
        point location "lat, lng"
        float star_rating
        float avg_review_score
        jsonb amenities "pool, wifi, parking"
        jsonb images
    }
    
    ROOM_TYPE {
        bigint id PK
        bigint hotel_id FK
        varchar name "Standard, Deluxe, Suite"
        int max_guests
        text description
        jsonb amenities
        int total_rooms "how many physical rooms of this type"
    }
    
    ROOM_AVAILABILITY {
        bigint id PK
        bigint room_type_id FK
        bigint hotel_id FK
        date date
        int total_inventory
        int available_count
        decimal price
        int version "optimistic locking"
    }
    
    RESERVATION {
        bigint id PK
        bigint user_id FK
        bigint hotel_id FK
        bigint room_type_id FK
        date check_in
        date check_out
        int num_guests
        decimal total_price
        varchar status "pending|confirmed|cancelled|checked_in|checked_out"
        varchar payment_id
        timestamp created_at
    }
    
    HOTEL ||--|{ ROOM_TYPE : has
    ROOM_TYPE ||--|{ ROOM_AVAILABILITY : daily_inventory
    RESERVATION }o--|| ROOM_TYPE : books
    RESERVATION }o--|| HOTEL : at
```

### Availability Check Strategy

```mermaid
flowchart TD
    Query["Check: Hotel X, Deluxe, Oct 1-3"] --> DateRange["Query availability<br/>for each date in range"]
    
    DateRange --> D1["Oct 1: available=5, price=$250"]
    DateRange --> D2["Oct 2: available=3, price=$250"]
    DateRange --> D3["Oct 3: available=0, price=$280"]
    
    D1 & D2 & D3 --> Min{Min available<br/>across all dates}
    
    Min -->|"min = 0"| Unavailable["❌ Not available"]
    Min -->|"min > 0"| Available["✅ Available<br/>Total = sum of daily prices"]
```

### Reservation with Optimistic Locking

```mermaid
flowchart TD
    Book["Book Deluxe Oct 1-3"] --> ReadVer["FOR EACH date:<br/>Read availability + version"]
    
    ReadVer --> Check{All dates<br/>available > 0?}
    Check -->|No| Fail["❌ Reservation failed"]
    Check -->|Yes| Update["FOR EACH date:<br/>UPDATE availability<br/>SET available -= 1<br/>WHERE version = read_version"]
    
    Update --> Result{All updates<br/>affected 1 row?}
    Result -->|Yes| Create["Create reservation<br/>Process payment"]
    Result -->|No| Conflict["⚠️ Concurrent conflict<br/>Rollback & retry (max 3)"]
    
    Conflict --> ReadVer
```

### Search Ranking

```mermaid
flowchart TD
    Query[Search Query] --> Filters["Apply Filters:<br/>location, dates, guests,<br/>price range, rating, amenities"]
    
    Filters --> Score["Score each hotel:"]
    
    Score --> Relevance["Relevance (30%)<br/>Name/location match"]
    Score --> Price["Price (25%)<br/>Competitive pricing"]
    Score --> Rating["Rating (20%)<br/>Average review score"]
    Score --> Availability["Availability (10%)<br/>More rooms = higher"]
    Score --> Freshness["Freshness (5%)<br/>Recently updated listing"]
    Score --> Sponsored["Commission (10%)<br/>Higher commission = boost"]
    
    Relevance & Price & Rating & Availability & Freshness & Sponsored --> Final["Weighted final score"]
    Final --> Sort["Sort & paginate"]
```

### Rate Management

```mermaid
graph TD
    subgraph "Dynamic Pricing"
        Base["Base Rate: $200/night"] --> Factors{Apply Factors}
        
        Factors --> Demand["Demand multiplier<br/>High occupancy: 1.3x"]
        Factors --> Season["Seasonal: Summer 1.2x<br/>Off-peak: 0.8x"]
        Factors --> DayOfWeek["Weekend: 1.15x<br/>Weekday: 1.0x"]
        Factors --> Advance["Advance booking<br/>Last minute: 1.2x<br/>60+ days: 0.9x"]
        Factors --> Events["Local events<br/>Concert/conference: 1.5x"]
        
        Demand & Season & DayOfWeek & Advance & Events --> Final2["Final: $200 × 1.3 × 1.2 × 1.15 = $358/night"]
    end
```

---

## Implementation

### Booking Service

```python
from decimal import Decimal
from datetime import date, timedelta
from typing import Optional
import uuid

class BookingService:
    """Handles hotel room reservations with consistency guarantees."""
    
    MAX_RETRIES = 3
    
    def __init__(self, db, availability_svc, payment_svc, 
                 notification_svc, event_bus):
        self.db = db
        self.availability = availability_svc
        self.payment = payment_svc
        self.notify = notification_svc
        self.events = event_bus
    
    async def create_reservation(self, user_id: int, hotel_id: int,
                                  room_type_id: int, check_in: date,
                                  check_out: date, num_guests: int,
                                  payment_info: dict) -> dict:
        """Create a reservation with optimistic locking."""
        
        reservation_id = str(uuid.uuid4())
        
        for attempt in range(self.MAX_RETRIES):
            try:
                # 1. Get availability and prices for date range
                dates = self._get_date_range(check_in, check_out)
                availability = await self.availability.get_for_dates(
                    hotel_id, room_type_id, dates
                )
                
                # 2. Check all dates have availability
                total_price = Decimal("0")
                versions = {}
                for day_avail in availability:
                    if day_avail["available_count"] <= 0:
                        return {"status": "unavailable",
                                "date": str(day_avail["date"])}
                    total_price += day_avail["price"]
                    versions[day_avail["date"]] = day_avail["version"]
                
                # 3. Reserve rooms (optimistic locking)
                async with self.db.transaction() as tx:
                    all_reserved = True
                    for d in dates:
                        rows = await tx.execute("""
                            UPDATE room_availability 
                            SET available_count = available_count - 1,
                                version = version + 1
                            WHERE hotel_id = %s AND room_type_id = %s
                              AND date = %s AND version = %s
                              AND available_count > 0
                        """, (hotel_id, room_type_id, d, versions[d]))
                        
                        if rows == 0:
                            all_reserved = False
                            break
                    
                    if not all_reserved:
                        # Rollback transaction, retry
                        raise OptimisticLockException()
                    
                    # 4. Process payment
                    payment = await self.payment.charge(
                        user_id=user_id,
                        amount=total_price,
                        idempotency_key=f"booking-{reservation_id}",
                        **payment_info
                    )
                    
                    if not payment["success"]:
                        raise PaymentFailedException(payment["reason"])
                    
                    # 5. Create reservation record
                    await tx.execute("""
                        INSERT INTO reservations 
                        (id, user_id, hotel_id, room_type_id, check_in,
                         check_out, num_guests, total_price, status, payment_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'confirmed', %s)
                    """, (reservation_id, user_id, hotel_id, room_type_id,
                          check_in, check_out, num_guests, total_price,
                          payment["payment_id"]))
                
                # 6. Publish event & notify
                await self.events.publish("reservation.confirmed", {
                    "reservation_id": reservation_id,
                    "hotel_id": hotel_id
                })
                await self.notify.send_confirmation(user_id, reservation_id)
                
                return {
                    "status": "confirmed",
                    "reservation_id": reservation_id,
                    "total_price": str(total_price)
                }
            
            except OptimisticLockException:
                if attempt == self.MAX_RETRIES - 1:
                    return {"status": "conflict",
                            "message": "Room no longer available"}
                continue  # Retry
    
    async def cancel_reservation(self, reservation_id: str, 
                                  user_id: int) -> dict:
        """Cancel reservation and release inventory."""
        reservation = await self.db.get_reservation(reservation_id)
        
        if reservation["user_id"] != user_id:
            return {"status": "unauthorized"}
        
        if reservation["status"] != "confirmed":
            return {"status": "cannot_cancel"}
        
        # Calculate refund based on cancellation policy
        refund_amount = self._calculate_refund(reservation)
        
        async with self.db.transaction() as tx:
            # Release inventory
            dates = self._get_date_range(
                reservation["check_in"], reservation["check_out"]
            )
            for d in dates:
                await tx.execute("""
                    UPDATE room_availability 
                    SET available_count = available_count + 1
                    WHERE hotel_id = %s AND room_type_id = %s AND date = %s
                """, (reservation["hotel_id"], 
                      reservation["room_type_id"], d))
            
            # Update reservation
            await tx.execute("""
                UPDATE reservations SET status = 'cancelled' 
                WHERE id = %s
            """, (reservation_id,))
        
        # Process refund
        if refund_amount > 0:
            await self.payment.refund(
                reservation["payment_id"], refund_amount
            )
        
        return {"status": "cancelled", "refund_amount": str(refund_amount)}
    
    def _get_date_range(self, check_in: date, check_out: date):
        dates = []
        current = check_in
        while current < check_out:
            dates.append(current)
            current += timedelta(days=1)
        return dates
    
    def _calculate_refund(self, reservation) -> Decimal:
        days_until_checkin = (reservation["check_in"] - date.today()).days
        if days_until_checkin > 7:
            return reservation["total_price"]  # Full refund
        elif days_until_checkin > 2:
            return reservation["total_price"] * Decimal("0.5")  # 50%
        else:
            return Decimal("0")  # No refund
```

---

## Limitations & Improvements

### Current Limitations

| Limitation | Impact | Severity |
|-----------|--------|----------|
| Optimistic locking conflicts during flash sales | Failed bookings for users | High |
| Availability cache staleness | Ghost availability | Medium |
| No overbooking support | Lost opportunity vs airlines | Low |
| Single-currency pricing | Complex for international users | Medium |
| Static cancellation policies | One-size-fits-all | Low |

### Improvement Areas

1. **Controlled Overbooking** — Allow 5% overbooking (airline model) with fallback hotels
2. **Real-time Pricing** — ML-based dynamic pricing considering demand, competition
3. **Geo-distributed** — Regional DB replicas for lower search latency
4. **Loyalty Program** — Points, tier-based pricing, exclusive inventory
5. **Multi-property Packages** — Flight + hotel bundles with distributed transactions

---

## Key Interview Discussion Points

1. **How to prevent double-booking?** Optimistic locking (version column) + DB constraint
2. **Pessimistic vs optimistic for bookings?** Optimistic: better throughput, retry on conflict
3. **How to handle availability cache inconsistency?** Short TTL + event-driven invalidation
4. **What about overbooking?** Hotels commonly overbook 5-10%; system tracks committed vs physical
5. **How to scale search?** Elasticsearch with geo queries + Redis cache for availability counts
