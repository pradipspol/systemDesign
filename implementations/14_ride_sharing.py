"""
=============================================================
  14. Ride Sharing — Geospatial Index + Matching + Surge
  Run: python 14_ride_sharing.py
  Implements QuadTree spatial index, driver matching,
  ETA estimation, surge pricing, and ride state machine.
=============================================================
"""
import math
import time
import uuid
import random
from enum import Enum
from dataclasses import dataclass, field
from collections import defaultdict
from typing import Optional


# ===================================================================
# Geometry Helpers
# ===================================================================
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * \
        math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ===================================================================
# QuadTree Spatial Index
# ===================================================================
@dataclass
class Point:
    lat: float
    lon: float
    data: dict = field(default_factory=dict)


@dataclass
class BoundingBox:
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float

    def contains(self, p: Point) -> bool:
        return (self.min_lat <= p.lat <= self.max_lat and
                self.min_lon <= p.lon <= self.max_lon)

    def intersects(self, other: "BoundingBox") -> bool:
        return not (other.min_lat > self.max_lat or other.max_lat < self.min_lat or
                    other.min_lon > self.max_lon or other.max_lon < self.min_lon)


class QuadTree:
    MAX_POINTS = 4
    MAX_DEPTH = 10

    def __init__(self, boundary: BoundingBox, depth: int = 0):
        self.boundary = boundary
        self.depth = depth
        self.points: list[Point] = []
        self.children: list[QuadTree] = []
        self.divided = False

    def insert(self, point: Point) -> bool:
        if not self.boundary.contains(point):
            return False
        if not self.divided and len(self.points) < self.MAX_POINTS:
            self.points.append(point)
            return True
        if not self.divided and self.depth < self.MAX_DEPTH:
            self._subdivide()
        for child in self.children:
            if child.insert(point):
                return True
        self.points.append(point)
        return True

    def _subdivide(self):
        b = self.boundary
        mid_lat = (b.min_lat + b.max_lat) / 2
        mid_lon = (b.min_lon + b.max_lon) / 2
        self.children = [
            QuadTree(BoundingBox(b.min_lat, b.min_lon, mid_lat, mid_lon), self.depth + 1),
            QuadTree(BoundingBox(mid_lat, b.min_lon, b.max_lat, mid_lon), self.depth + 1),
            QuadTree(BoundingBox(b.min_lat, mid_lon, mid_lat, b.max_lon), self.depth + 1),
            QuadTree(BoundingBox(mid_lat, mid_lon, b.max_lat, b.max_lon), self.depth + 1),
        ]
        self.divided = True
        # Redistribute existing points
        existing = self.points[:]
        self.points = []
        for p in existing:
            inserted = False
            for child in self.children:
                if child.insert(p):
                    inserted = True
                    break
            if not inserted:
                self.points.append(p)

    def query_range(self, bbox: BoundingBox) -> list[Point]:
        results = []
        if not self.boundary.intersects(bbox):
            return results
        for p in self.points:
            if bbox.contains(p):
                results.append(p)
        for child in self.children:
            results.extend(child.query_range(bbox))
        return results

    def query_radius(self, lat: float, lon: float, radius_km: float) -> list[tuple[Point, float]]:
        # Approximate bounding box
        delta_lat = radius_km / 111.0
        delta_lon = radius_km / (111.0 * max(0.01, math.cos(math.radians(lat))))
        bbox = BoundingBox(lat - delta_lat, lon - delta_lon, lat + delta_lat, lon + delta_lon)
        candidates = self.query_range(bbox)
        results = []
        for p in candidates:
            dist = haversine_km(lat, lon, p.lat, p.lon)
            if dist <= radius_km:
                results.append((p, dist))
        results.sort(key=lambda x: x[1])
        return results


# ===================================================================
# Data Models
# ===================================================================
class RideStatus(Enum):
    REQUESTED = "requested"
    MATCHED = "matched"
    DRIVER_ARRIVING = "driver_arriving"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class Driver:
    driver_id: str
    name: str
    lat: float
    lon: float
    rating: float = 4.8
    is_available: bool = True
    vehicle_type: str = "sedan"
    current_ride: str = ""


@dataclass
class Ride:
    ride_id: str
    rider_id: str
    driver_id: str = ""
    pickup_lat: float = 0.0
    pickup_lon: float = 0.0
    dropoff_lat: float = 0.0
    dropoff_lon: float = 0.0
    status: RideStatus = RideStatus.REQUESTED
    fare: float = 0.0
    surge_multiplier: float = 1.0
    distance_km: float = 0.0
    eta_minutes: float = 0.0
    created_at: float = field(default_factory=time.time)


# ===================================================================
# Surge Pricing
# ===================================================================
class SurgePricing:
    BASE_FARE = 2.50
    PER_KM = 1.50
    PER_MIN = 0.25

    def __init__(self):
        self.zone_demand: dict[str, int] = defaultdict(int)
        self.zone_supply: dict[str, int] = defaultdict(int)

    def _zone_key(self, lat: float, lon: float) -> str:
        return f"{int(lat * 100)},{int(lon * 100)}"

    def record_request(self, lat: float, lon: float):
        self.zone_demand[self._zone_key(lat, lon)] += 1

    def record_supply(self, lat: float, lon: float):
        self.zone_supply[self._zone_key(lat, lon)] += 1

    def get_multiplier(self, lat: float, lon: float) -> float:
        zone = self._zone_key(lat, lon)
        demand = self.zone_demand.get(zone, 1)
        supply = max(1, self.zone_supply.get(zone, 1))
        ratio = demand / supply
        if ratio > 3.0:
            return 2.5
        elif ratio > 2.0:
            return 2.0
        elif ratio > 1.5:
            return 1.5
        return 1.0

    def calculate_fare(self, distance_km: float, duration_min: float, surge: float) -> float:
        base = self.BASE_FARE + distance_km * self.PER_KM + duration_min * self.PER_MIN
        return round(base * surge, 2)


# ===================================================================
# Ride Sharing Service
# ===================================================================
class RideSharingService:
    def __init__(self):
        # SF Bay Area bounding box
        self.spatial_index = QuadTree(BoundingBox(37.0, -123.0, 38.0, -122.0))
        self.drivers: dict[str, Driver] = {}
        self.rides: dict[str, Ride] = {}
        self.surge = SurgePricing()
        self._stats = {"rides_completed": 0, "rides_cancelled": 0, "total_fare": 0.0}

    def register_driver(self, driver: Driver):
        self.drivers[driver.driver_id] = driver
        point = Point(driver.lat, driver.lon, {"driver_id": driver.driver_id})
        self.spatial_index.insert(point)
        self.surge.record_supply(driver.lat, driver.lon)

    def request_ride(self, rider_id: str, pickup_lat: float, pickup_lon: float,
                     dropoff_lat: float, dropoff_lon: float) -> Ride:
        self.surge.record_request(pickup_lat, pickup_lon)
        ride = Ride(
            ride_id=str(uuid.uuid4())[:8],
            rider_id=rider_id,
            pickup_lat=pickup_lat,
            pickup_lon=pickup_lon,
            dropoff_lat=dropoff_lat,
            dropoff_lon=dropoff_lon,
            distance_km=haversine_km(pickup_lat, pickup_lon, dropoff_lat, dropoff_lon),
            surge_multiplier=self.surge.get_multiplier(pickup_lat, pickup_lon),
        )
        self.rides[ride.ride_id] = ride

        # Auto-match
        self.match_driver(ride)
        return ride

    def match_driver(self, ride: Ride, max_radius_km: float = 5.0) -> bool:
        nearby = self.spatial_index.query_radius(ride.pickup_lat, ride.pickup_lon, max_radius_km)
        best_driver = None
        best_score = -1

        for point, dist_km in nearby:
            driver_id = point.data.get("driver_id")
            driver = self.drivers.get(driver_id)
            if not driver or not driver.is_available:
                continue
            # Score = rating * (1/distance) — closer + higher rating wins
            score = driver.rating / max(0.1, dist_km)
            if score > best_score:
                best_score = score
                best_driver = driver

        if best_driver:
            ride.driver_id = best_driver.driver_id
            ride.status = RideStatus.MATCHED
            ride.eta_minutes = round(
                haversine_km(best_driver.lat, best_driver.lon,
                            ride.pickup_lat, ride.pickup_lon) / 0.5, 1  # avg 30 km/h
            )
            ride.fare = self.surge.calculate_fare(
                ride.distance_km,
                ride.distance_km / 0.5,  # ~30 km/h
                ride.surge_multiplier,
            )
            best_driver.is_available = False
            best_driver.current_ride = ride.ride_id
            return True
        return False

    def complete_ride(self, ride_id: str):
        ride = self.rides.get(ride_id)
        if not ride:
            return
        ride.status = RideStatus.COMPLETED
        driver = self.drivers.get(ride.driver_id)
        if driver:
            driver.is_available = True
            driver.current_ride = ""
        self._stats["rides_completed"] += 1
        self._stats["total_fare"] += ride.fare

    def cancel_ride(self, ride_id: str):
        ride = self.rides.get(ride_id)
        if not ride:
            return
        ride.status = RideStatus.CANCELLED
        driver = self.drivers.get(ride.driver_id)
        if driver:
            driver.is_available = True
            driver.current_ride = ""
        self._stats["rides_cancelled"] += 1

    def stats(self) -> dict:
        return {
            **self._stats,
            "active_rides": sum(1 for r in self.rides.values()
                               if r.status in (RideStatus.MATCHED, RideStatus.IN_PROGRESS)),
            "available_drivers": sum(1 for d in self.drivers.values() if d.is_available),
        }


# ===================================================================
# Demo
# ===================================================================
if __name__ == "__main__":
    print("=" * 65)
    print("  Ride Sharing — QuadTree + Matching + Surge Pricing")
    print("=" * 65)

    svc = RideSharingService()

    # Register drivers around SF
    driver_data = [
        ("d1", "Alice Driver", 37.7749, -122.4194),  # Downtown SF
        ("d2", "Bob Driver",   37.7849, -122.4094),
        ("d3", "Charlie Driver", 37.7649, -122.4294),
        ("d4", "Diana Driver", 37.7549, -122.4394),
        ("d5", "Eve Driver",   37.7949, -122.3994),
        ("d6", "Frank Driver", 37.7450, -122.4500),
        ("d7", "Grace Driver", 37.8000, -122.4000),
        ("d8", "Hank Driver",  37.7700, -122.4100),
    ]
    for did, name, lat, lon in driver_data:
        svc.register_driver(Driver(driver_id=did, name=name, lat=lat, lon=lon,
                                    rating=round(random.uniform(4.2, 5.0), 1)))

    print(f"\n  Registered {len(svc.drivers)} drivers")

    # Spatial query test
    nearby = svc.spatial_index.query_radius(37.7749, -122.4194, 3.0)
    print(f"\n  Drivers within 3km of downtown SF: {len(nearby)}")
    for point, dist in nearby[:5]:
        d = svc.drivers[point.data["driver_id"]]
        print(f"    {d.name:20s} dist={dist:.2f}km  rating={d.rating}")

    # Request rides
    print("\n  Requesting rides:")
    rides = [
        svc.request_ride("rider_1", 37.7749, -122.4194, 37.7849, -122.4094),  # short
        svc.request_ride("rider_2", 37.7649, -122.4294, 37.8049, -122.3894),  # medium
        svc.request_ride("rider_3", 37.7549, -122.4394, 37.7949, -122.3994),  # medium
    ]

    for r in rides:
        driver = svc.drivers.get(r.driver_id, None)
        dname = driver.name if driver else "No match"
        print(f"    Ride {r.ride_id}: {r.status.value}  driver={dname:20s}  "
              f"dist={r.distance_km:.2f}km  fare=${r.fare:.2f}  "
              f"surge={r.surge_multiplier}x  ETA={r.eta_minutes}min")

    # Simulate surge
    print("\n  Simulating surge (20 requests in same zone):")
    for _ in range(20):
        svc.surge.record_request(37.7749, -122.4194)
    surge_mult = svc.surge.get_multiplier(37.7749, -122.4194)
    print(f"    Surge multiplier at downtown: {surge_mult}x")

    new_ride = svc.request_ride("rider_surge", 37.7749, -122.4194, 37.7949, -122.3994)
    print(f"    Surge ride fare: ${new_ride.fare:.2f} (multiplier={new_ride.surge_multiplier}x)")

    # Complete rides
    for r in rides:
        svc.complete_ride(r.ride_id)

    # Stats
    print(f"\n  Stats: {svc.stats()}")
    print("\nDone.")
