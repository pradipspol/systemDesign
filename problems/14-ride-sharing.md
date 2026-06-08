# 14. Uber / Ride Sharing Platform

> **Difficulty**: Hard | **Asked by**: Uber, Lyft, Google, Amazon, Grab, Didi

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
1. Riders request a ride by specifying pickup and destination
2. Match riders with nearby available drivers
3. Real-time driver location tracking
4. ETA calculation
5. Dynamic pricing (surge pricing)
6. Trip tracking and payment processing
7. Ratings and reviews

### Non-Functional Requirements
1. **Low Latency Matching**: < 5 seconds to find a driver
2. **Real-time Location**: Location updates every 3-5 seconds
3. **High Availability**: 99.99% uptime
4. **Scalability**: 10M concurrent rides, 1M drivers online
5. **Geospatial Queries**: Efficient nearest-driver search

---

## Capacity Estimation

```
Active drivers: 1M concurrent
Active riders: 10M concurrent
Rides/day: 20M
Location updates: 1M drivers × 1 update/4 sec = 250K updates/sec
Matching requests: ~230/sec (20M/day)
Average trip duration: 15 minutes
GPS data per update: ~100 bytes
Location data/day: 250K × 100B × 86400 = 2.16 TB
```

---

## High-Level Design

### Architecture Overview

```mermaid
graph TB
    subgraph "Clients"
        Rider[Rider App]
        Driver[Driver App]
    end
    
    Rider & Driver --> LB[Load Balancer / API Gateway]
    
    LB --> RideSvc[Ride Service<br/>Request & manage rides]
    LB --> MatchSvc[Matching Service<br/>Find nearest driver]
    LB --> LocationSvc[Location Service<br/>Track driver positions]
    LB --> PricingSvc[Pricing Service<br/>Surge & fare calculation]
    LB --> PaymentSvc[Payment Service]
    LB --> ETASvc[ETA Service<br/>Route & time estimation]
    
    LocationSvc --> LocationDB[(Location Store<br/>Redis/QuadTree)]
    MatchSvc --> LocationDB
    
    RideSvc --> RideDB[(Ride DB<br/>PostgreSQL)]
    PricingSvc --> PriceDB[(Pricing DB)]
    
    LocationSvc --> Kafka[(Kafka<br/>Location Events)]
    Kafka --> Analytics[Analytics Pipeline]
    
    ETASvc --> MapSvc[Map Service<br/>OSRM/Google Maps]
```

### Ride Request Flow

```mermaid
sequenceDiagram
    participant R as Rider
    participant API as API Gateway
    participant Ride as Ride Service
    participant Price as Pricing Service
    participant Match as Matching Service
    participant Loc as Location Service
    participant ETA as ETA Service
    participant D as Driver
    
    R->>API: Request ride (pickup, destination)
    API->>Price: Get fare estimate
    Price-->>API: Fare: $25 (surge: 1.5x)
    API-->>R: Show fare estimate
    
    R->>API: Confirm ride request
    API->>Ride: Create ride request
    Ride->>Match: Find nearby drivers
    Match->>Loc: Get drivers near pickup
    Loc-->>Match: [Driver A (0.5km), B (1.2km), C (2.0km)]
    
    Match->>ETA: Get ETA for each driver
    ETA-->>Match: [A: 3min, B: 5min, C: 8min]
    
    Match->>Match: Rank by ETA + rating + acceptance rate
    Match->>D: Send ride offer to Driver A
    
    alt Driver Accepts
        D-->>Match: Accept
        Match->>Ride: Update: matched
        Ride-->>R: Driver matched! ETA: 3 min
    else Driver Declines/Timeout
        Match->>D: Offer to Driver B
    end
```

### Real-Time Location Tracking

```mermaid
sequenceDiagram
    participant D as Driver App
    participant GW as WebSocket Gateway
    participant Loc as Location Service
    participant Redis as Location Store
    participant R as Rider App
    
    loop Every 4 seconds
        D->>GW: Location update (lat, lng, heading, speed)
        GW->>Loc: Process location
        Loc->>Redis: GEOADD drivers lat lng driver_id
        
        opt Active ride
            Loc->>R: Push driver location to rider
        end
    end
    
    Note over Loc,Redis: Location indexed in<br/>geospatial structure<br/>for nearest-neighbor queries
```

---

## Low-Level Design

### Geospatial Indexing

```mermaid
graph TD
    subgraph "Option 1: Geohash + Redis"
        G1["Geohash: 9q8yy → grid cell"]
        G2["Redis GEOADD drivers lng lat driver_id"]
        G3["GEORADIUS drivers lng lat 5 km"]
        G4["✅ Simple, fast, built-in Redis support"]
    end
    
    subgraph "Option 2: QuadTree"
        Q1["Recursively divide space"]
        Q2["Each leaf: list of drivers"]
        Q3["Dynamic: split when > threshold"]
        Q4["✅ Efficient for uneven distribution"]
    end
    
    subgraph "Option 3: S2 Geometry (Google)"
        S1["Project Earth onto cube cells"]
        S2["Hierarchical cell IDs"]
        S3["✅ Used by Uber (H3 variant)"]
    end
```

### QuadTree Structure

```mermaid
graph TD
    Root["World<br/>(lat: -90 to 90, lng: -180 to 180)"] --> NW["NW Quadrant"]
    Root --> NE["NE Quadrant"]
    Root --> SW["SW Quadrant"]
    Root --> SE["SE Quadrant"]
    
    NE --> NE_NW["Subdivide further..."]
    NE --> NE_NE["City area<br/>50 drivers"]
    NE --> NE_SW["Split again<br/>(>100 drivers)"]
    NE --> NE_SE["Rural area<br/>5 drivers"]
    
    NE_SW --> A["Block A<br/>30 drivers"]
    NE_SW --> B["Block B<br/>45 drivers"]
    NE_SW --> C["Block C<br/>20 drivers"]
    NE_SW --> D["Block D<br/>15 drivers"]
    
    style NE_NE fill:#90EE90
    style A fill:#90EE90
    style B fill:#90EE90
```

### H3 Hexagonal Grid (Uber's Approach)

```
Resolution 7: ~5.16 km² hexagons (city level)
Resolution 9: ~0.11 km² hexagons (neighborhood)
Resolution 11: ~0.002 km² hexagons (block level)

Advantages:
- Uniform distance to all neighbors (unlike square grids)
- Natural for proximity search (k-ring neighbors)
- Hierarchical: zoom in/out for different scales
```

### Matching Algorithm

```mermaid
flowchart TD
    Request[Ride Request<br/>pickup: lat, lng] --> Search["Search radius: 5km<br/>Find available drivers"]
    Search --> Candidates["Candidates:<br/>Driver A: 1km, rating 4.8<br/>Driver B: 2km, rating 4.9<br/>Driver C: 0.5km, rating 4.2"]
    
    Candidates --> Score["Score each driver"]
    
    subgraph "Scoring Function"
        ETA["ETA to pickup<br/>(weight: 0.5)"]
        Rating["Driver rating<br/>(weight: 0.2)"]
        Accept["Acceptance rate<br/>(weight: 0.15)"]
        Cancel["Cancellation rate<br/>(weight: 0.1)"]
        Type["Vehicle type match<br/>(weight: 0.05)"]
    end
    
    ETA & Rating & Accept & Cancel & Type --> Score
    Score --> Rank["Rank and offer<br/>to top driver"]
    Rank --> Timeout{Accept within<br/>15 seconds?}
    Timeout -->|Yes| Matched[✅ Ride Matched]
    Timeout -->|No| Next[Offer to next driver]
```

### Surge Pricing Model

```mermaid
flowchart TD
    subgraph "Supply & Demand"
        Demand["Ride requests<br/>in area (5 min window)"]
        Supply["Available drivers<br/>in area"]
        Ratio["D/S Ratio =<br/>demand / supply"]
    end
    
    Ratio --> Multiplier{Ratio value}
    Multiplier -->|"< 1.0"| Normal["1.0x (normal price)"]
    Multiplier -->|"1.0 - 1.5"| Low["1.2x - 1.5x"]
    Multiplier -->|"1.5 - 2.5"| Medium["1.5x - 2.0x"]
    Multiplier -->|"> 2.5"| High["2.0x - 3.0x (cap)"]
    
    Normal & Low & Medium & High --> Base["Base fare × surge"]
    
    subgraph "Fare Calculation"
        Base --> Total["Total = Base + ($/mile × distance)<br/>+ ($/min × duration)<br/>+ booking fee<br/>× surge multiplier"]
    end
```

### Data Models

```mermaid
erDiagram
    RIDE {
        uuid id PK
        bigint rider_id FK
        bigint driver_id FK
        point pickup_location
        point destination
        varchar status "requested|matched|en_route|in_progress|completed|cancelled"
        float fare_amount
        float surge_multiplier
        float distance_miles
        int duration_minutes
        varchar payment_method
        timestamp requested_at
        timestamp matched_at
        timestamp started_at
        timestamp completed_at
    }
    
    DRIVER {
        bigint id PK
        varchar name
        varchar vehicle_type "economy|premium|xl"
        varchar license_plate
        float rating
        boolean is_available
        point current_location
        varchar current_h3_cell "H3 cell ID"
    }
    
    DRIVER_LOCATION_LOG {
        bigint id PK
        bigint driver_id FK
        point location
        float heading
        float speed
        timestamp recorded_at
    }
    
    RIDE }|--|| DRIVER : assigned_to
    DRIVER ||--o{ DRIVER_LOCATION_LOG : tracks
```

---

## Implementation

### Location Service

```python
import redis
import time
from typing import List, Tuple
from dataclasses import dataclass

@dataclass
class DriverLocation:
    driver_id: int
    latitude: float
    longitude: float
    heading: float
    speed: float
    timestamp: float

class LocationService:
    """Manages real-time driver locations using Redis Geo."""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.geo_key = "driver_locations"
    
    def update_location(self, loc: DriverLocation):
        """Update driver's position in geospatial index."""
        # Store in Redis geo set
        self.redis.geoadd(
            self.geo_key,
            (loc.longitude, loc.latitude, str(loc.driver_id))
        )
        
        # Store additional metadata (heading, speed)
        self.redis.hset(
            f"driver:{loc.driver_id}:meta",
            mapping={
                "heading": loc.heading,
                "speed": loc.speed,
                "last_update": loc.timestamp,
                "available": "1"
            }
        )
        
        # Publish for real-time tracking
        self.redis.publish(
            f"driver_location:{loc.driver_id}",
            f"{loc.latitude},{loc.longitude}"
        )
    
    def find_nearby_drivers(self, lat: float, lng: float,
                            radius_km: float = 5.0,
                            limit: int = 20) -> List[dict]:
        """Find available drivers within radius."""
        results = self.redis.georadius(
            self.geo_key, lng, lat,
            radius=radius_km, unit='km',
            withcoord=True, withdist=True,
            count=limit, sort='ASC'
        )
        
        drivers = []
        for driver_id, dist, (d_lng, d_lat) in results:
            driver_id = driver_id.decode()
            meta = self.redis.hgetall(f"driver:{driver_id}:meta")
            
            if meta.get(b"available") == b"1":
                drivers.append({
                    "driver_id": int(driver_id),
                    "distance_km": dist,
                    "latitude": d_lat,
                    "longitude": d_lng,
                    "heading": float(meta.get(b"heading", 0)),
                })
        
        return drivers


class MatchingService:
    """Matches riders with optimal nearby drivers."""
    
    def __init__(self, location_service, eta_service, driver_service):
        self.location = location_service
        self.eta = eta_service
        self.drivers = driver_service
    
    async def find_driver(self, pickup_lat: float, pickup_lng: float,
                          vehicle_type: str = "economy") -> dict:
        """Find and assign the best available driver."""
        
        # 1. Find nearby drivers (expanding radius)
        for radius in [3, 5, 8, 12]:
            candidates = self.location.find_nearby_drivers(
                pickup_lat, pickup_lng, radius_km=radius
            )
            if candidates:
                break
        
        if not candidates:
            return {"status": "no_drivers_available"}
        
        # 2. Get ETA for each candidate
        for driver in candidates:
            driver["eta_minutes"] = await self.eta.calculate(
                driver["latitude"], driver["longitude"],
                pickup_lat, pickup_lng
            )
        
        # 3. Score and rank
        scored = []
        for driver in candidates:
            info = await self.drivers.get(driver["driver_id"])
            score = self._calculate_score(driver, info)
            scored.append((score, driver))
        
        scored.sort(key=lambda x: -x[0])
        
        # 4. Offer to best driver
        for score, driver in scored:
            accepted = await self._offer_ride(driver["driver_id"])
            if accepted:
                return {"status": "matched", "driver": driver}
        
        return {"status": "no_driver_accepted"}
    
    def _calculate_score(self, driver, info) -> float:
        """Score a driver based on multiple factors."""
        eta_score = max(0, 15 - driver["eta_minutes"]) / 15  # 0-1
        rating_score = (info.get("rating", 4.0) - 3.0) / 2.0  # 0-1
        accept_score = info.get("acceptance_rate", 0.8)
        
        return (
            eta_score * 0.5 +
            rating_score * 0.2 +
            accept_score * 0.2 +
            (1 - info.get("cancel_rate", 0.1)) * 0.1
        )
```

---

## Limitations & Improvements

### Current Limitations

| Limitation | Impact | Severity |
|-----------|--------|----------|
| GPS inaccuracy in cities (urban canyons) | Wrong pickup location | Medium |
| Matching timeout for low-supply areas | Long wait times | High |
| Surge pricing fairness | User frustration | Medium |
| Real-time location at scale | 250K updates/sec overhead | Medium |
| ETA accuracy in traffic | Unreliable time estimates | Medium |

### Improvement Areas

1. **ML-Based Demand Prediction** — Predict demand to pre-position drivers
2. **Carpooling Optimization** — Match multiple riders going similar direction
3. **Autonomous Vehicles** — Self-driving fleet integration
4. **Map Matching** — Snap GPS to road network for accurate positioning
5. **Multi-Modal** — Combine ride-sharing with bike/scooter/transit

---

## Key Interview Discussion Points

1. **Why H3/Geohash over QuadTree?** Hexagonal grids have uniform neighbor distance; QuadTree is dynamic
2. **How to handle supply-demand imbalance?** Surge pricing + driver incentives + demand prediction
3. **Why WebSocket for location?** Bidirectional real-time; also supports ride updates
4. **How to scale location updates?** Sharding by geohash region + Kafka for async processing
5. **Consistency requirements?** Ride state must be strongly consistent; location can be eventually consistent
