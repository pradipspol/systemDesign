# 16. E-Commerce Platform (Amazon)

> **Difficulty**: Hard | **Asked by**: Amazon, Walmart, Shopify, Alibaba, Flipkart

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
1. Product catalog (search, browse, filter)
2. Shopping cart management
3. Order placement and tracking
4. Inventory management
5. User reviews and ratings
6. Recommendation engine
7. Seller management (multi-vendor marketplace)

### Non-Functional Requirements
1. **High Availability**: 99.99% (downtime = lost revenue)
2. **Low Latency**: Product page < 200ms, search < 300ms
3. **Scalability**: 500M products, 100M DAU, 10M orders/day
4. **Consistency**: Inventory - strong; catalog - eventual
5. **Peak Handling**: 10x traffic during sales (Black Friday, Prime Day)

---

## Capacity Estimation

```
DAU: 100M users
Products: 500M SKUs
Orders/day: 10M (peak: 100M during Black Friday)
Product page views/day: 5B
Search queries/day: 1B
Cart operations/day: 500M
Average order: 3 items, $50
Payment transactions: 10M/day

Storage:
  Product data: 500M × 2 KB = 1 TB
  Images: 500M × 5 images × 500 KB = 1.25 PB
  Order history: 3.6B orders/year × 2 KB = 7.2 TB/year
```

---

## High-Level Design

### Architecture Overview (Microservices)

```mermaid
graph TB
    Client[Web/Mobile Client] --> CDN[CDN<br/>Static assets, images]
    Client --> GW[API Gateway<br/>Authentication, Rate Limiting]
    
    GW --> ProductSvc[Product Service]
    GW --> SearchSvc[Search Service]
    GW --> CartSvc[Cart Service]
    GW --> OrderSvc[Order Service]
    GW --> UserSvc[User Service]
    GW --> RecSvc[Recommendation Svc]
    
    ProductSvc --> ProductDB[(Product DB<br/>PostgreSQL)]
    ProductSvc --> ProductCache[(Product Cache<br/>Redis)]
    
    SearchSvc --> ES[(Elasticsearch<br/>Search Index)]
    
    CartSvc --> CartDB[(Cart Store<br/>Redis + DynamoDB)]
    
    OrderSvc --> OrderDB[(Order DB<br/>PostgreSQL)]
    OrderSvc --> InvSvc[Inventory Service]
    InvSvc --> InvDB[(Inventory DB<br/>PostgreSQL)]
    
    OrderSvc --> PaySvc[Payment Service]
    OrderSvc --> ShipSvc[Shipping Service]
    OrderSvc --> NotifSvc[Notification Service]
    
    subgraph "Event Bus"
        Kafka[(Kafka)]
    end
    
    OrderSvc --> Kafka
    InvSvc --> Kafka
    Kafka --> SearchSvc
    Kafka --> RecSvc
    Kafka --> Analytics[Analytics]
```

### Order Flow

```mermaid
sequenceDiagram
    participant U as User
    participant Cart as Cart Service
    participant Order as Order Service
    participant Inv as Inventory
    participant Pay as Payment
    participant Ship as Shipping
    participant Notif as Notification
    
    U->>Cart: Add items to cart
    U->>Order: Place order
    
    Order->>Inv: Reserve inventory (3 items)
    
    alt Inventory available
        Inv-->>Order: Reserved ✅
        Order->>Pay: Charge $150
        
        alt Payment success
            Pay-->>Order: Charged ✅
            Order->>Order: Create order (status: confirmed)
            Order->>Notif: Send confirmation email
            Order->>Ship: Create shipment
            Notif-->>U: Order confirmed! 🎉
            
            Ship->>Ship: Pick, pack, ship
            Ship->>Notif: Tracking update
            Notif-->>U: Your order shipped! 📦
        else Payment failed
            Pay-->>Order: Failed ❌
            Order->>Inv: Release inventory
            Order-->>U: Payment failed
        end
    else Out of stock
        Inv-->>Order: Insufficient ❌
        Order-->>U: Item out of stock
    end
```

---

## Low-Level Design

### Data Models

```mermaid
erDiagram
    PRODUCT {
        bigint id PK
        varchar sku UK
        varchar name
        text description
        bigint seller_id FK
        bigint category_id FK
        decimal price
        decimal sale_price
        jsonb attributes "color, size, weight"
        varchar status "active|inactive|deleted"
        float avg_rating
        int review_count
    }
    
    INVENTORY {
        bigint id PK
        bigint product_id FK
        bigint warehouse_id FK
        int quantity_available
        int quantity_reserved
        int reorder_level
        int version "optimistic locking"
    }
    
    ORDER {
        bigint id PK
        bigint user_id FK
        decimal subtotal
        decimal tax
        decimal shipping_fee
        decimal total
        varchar status "pending|confirmed|shipped|delivered|cancelled"
        varchar payment_id
        bigint shipping_address_id
        timestamp created_at
    }
    
    ORDER_ITEM {
        bigint id PK
        bigint order_id FK
        bigint product_id FK
        int quantity
        decimal unit_price
        decimal subtotal
    }
    
    CART {
        bigint user_id PK
        jsonb items "list of product_id, quantity, added_at"
        timestamp updated_at
    }
    
    PRODUCT ||--o{ INVENTORY : stored_in
    ORDER ||--|{ ORDER_ITEM : contains
    ORDER_ITEM }o--|| PRODUCT : references
```

### Inventory Management

```mermaid
flowchart TD
    subgraph "Inventory State"
        Total["Total: 100 units"]
        Available["Available: 70"]
        Reserved["Reserved: 25<br/>(pending orders)"]
        Shipped["Shipped: 5<br/>(in transit)"]
    end
    
    subgraph "Operations"
        Reserve["Reserve(qty)<br/>available -= qty<br/>reserved += qty"]
        Confirm["Confirm(qty)<br/>reserved -= qty<br/>shipped += qty"]
        Release["Release(qty)<br/>reserved -= qty<br/>available += qty"]
        Restock["Restock(qty)<br/>available += qty"]
    end
    
    subgraph "Consistency: Optimistic Locking"
        Read["Read: quantity=70, version=5"]
        Write["UPDATE inventory<br/>SET quantity=69, version=6<br/>WHERE id=X AND version=5"]
        Result{Rows affected?}
        Result -->|1| Success["✅ Updated"]
        Result -->|0| Retry["Retry (version changed)"]
    end
```

### Shopping Cart Design

```mermaid
graph TD
    subgraph "Cart Storage Strategy"
        Guest["Guest User"] --> Cookie["Browser LocalStorage<br/>or Cookie"]
        Logged["Logged-in User"] --> Redis["Redis (fast access)<br/>+ DynamoDB (persistence)"]
        
        Login["User logs in"] --> Merge["Merge guest cart<br/>with server cart"]
    end
    
    subgraph "Cart Operations"
        Add["Add item<br/>HSET cart:user:123 product:456 qty:2"]
        Remove["Remove item<br/>HDEL cart:user:123 product:456"]
        Update["Update quantity<br/>HSET cart:user:123 product:456 qty:3"]
        Get["Get cart<br/>HGETALL cart:user:123"]
    end
```

### Search Architecture

```mermaid
graph TD
    Query["User: 'wireless bluetooth headphones'"] --> API[Search API]
    API --> Parse["Query Parser<br/>tokenize, spell-correct,<br/>synonym expansion"]
    Parse --> ES["Elasticsearch"]
    
    subgraph "Elasticsearch"
        Index["Product Index"]
        Filter["Filters:<br/>price range, brand,<br/>rating, Prime eligible"]
        Sort["Sort by:<br/>relevance, price,<br/>rating, bestseller"]
        Facets["Facets:<br/>categories, brands,<br/>price ranges"]
    end
    
    ES --> Results["Search Results"]
    Results --> Personalize["Personalize ranking<br/>based on user history"]
    Personalize --> Ads["Inject sponsored<br/>products"]
    Ads --> Response["Final results<br/>+ facets + suggestions"]
```

### Saga Pattern for Order Processing

```mermaid
graph LR
    subgraph "Order Saga (Choreography)"
        O1["1. Create Order<br/>(PENDING)"] --> O2["2. Reserve Inventory"]
        O2 --> O3["3. Process Payment"]
        O3 --> O4["4. Confirm Order"]
        O4 --> O5["5. Send Notification"]
    end
    
    subgraph "Compensating Transactions"
        O3 -->|"Payment fails"| C1["Release Inventory"]
        C1 --> C2["Cancel Order"]
        
        O2 -->|"Out of stock"| C3["Cancel Order"]
    end
```

---

## Implementation

### Order Service with Saga

```python
from enum import Enum
from dataclasses import dataclass
from typing import List
import uuid

class OrderStatus(Enum):
    PENDING = "pending"
    INVENTORY_RESERVED = "inventory_reserved"
    PAYMENT_PROCESSING = "payment_processing" 
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"

@dataclass
class OrderItem:
    product_id: int
    quantity: int
    unit_price: float

class OrderService:
    """Orchestrates order creation with saga pattern."""
    
    def __init__(self, order_db, inventory_svc, payment_svc, 
                 notification_svc, event_bus):
        self.db = order_db
        self.inventory = inventory_svc
        self.payment = payment_svc
        self.notify = notification_svc
        self.events = event_bus
    
    async def place_order(self, user_id: int, items: List[OrderItem],
                          payment_info: dict, shipping_address: dict) -> dict:
        """Place order with saga-based transaction management."""
        order_id = str(uuid.uuid4())
        
        try:
            # Step 1: Create order record
            total = sum(i.unit_price * i.quantity for i in items)
            await self.db.create_order(order_id, user_id, items, total,
                                        OrderStatus.PENDING)
            
            # Step 2: Reserve inventory
            for item in items:
                reserved = await self.inventory.reserve(
                    item.product_id, item.quantity, order_id
                )
                if not reserved:
                    # Compensate: release already reserved items
                    await self._compensate_inventory(order_id, items)
                    await self.db.update_status(order_id, OrderStatus.CANCELLED)
                    return {"status": "failed", "reason": "out_of_stock",
                            "product_id": item.product_id}
            
            await self.db.update_status(order_id, OrderStatus.INVENTORY_RESERVED)
            
            # Step 3: Process payment
            payment = await self.payment.charge(
                user_id=user_id,
                amount=total,
                order_id=order_id,
                idempotency_key=f"order-{order_id}",
                **payment_info
            )
            
            if not payment["success"]:
                # Compensate: release inventory
                await self._compensate_inventory(order_id, items)
                await self.db.update_status(order_id, OrderStatus.CANCELLED)
                return {"status": "failed", "reason": "payment_failed"}
            
            # Step 4: Confirm order
            await self.db.update_status(order_id, OrderStatus.CONFIRMED)
            
            # Step 5: Publish events
            await self.events.publish("order.confirmed", {
                "order_id": order_id,
                "user_id": user_id,
                "items": [(i.product_id, i.quantity) for i in items]
            })
            
            # Step 6: Notify user
            await self.notify.send_order_confirmation(user_id, order_id)
            
            return {"status": "confirmed", "order_id": order_id}
        
        except Exception as e:
            # Full compensation
            await self._compensate_inventory(order_id, items)
            await self.db.update_status(order_id, OrderStatus.CANCELLED)
            raise
    
    async def _compensate_inventory(self, order_id: str, 
                                     items: List[OrderItem]):
        """Release reserved inventory."""
        for item in items:
            await self.inventory.release(item.product_id, item.quantity,
                                          order_id)
```

---

## Limitations & Improvements

### Current Limitations

| Limitation | Impact | Severity |
|-----------|--------|----------|
| Inventory oversell during flash sales | Negative customer experience | Critical |
| Saga compensation complexity | Partial failures hard to handle | High |
| Search relevance cold start | New products hard to rank | Medium |
| Payment gateway timeouts | Order stuck in pending | High |
| Cart abandonment (70%) | Lost revenue | Medium |

### Improvement Areas

1. **Event Sourcing** — Full audit trail for orders, undo/replay capability
2. **ML Demand Forecasting** — Predict inventory needs per warehouse
3. **Personalized Pricing** — Dynamic pricing based on user behavior
4. **Multi-Warehouse Routing** — Route order to nearest warehouse with stock
5. **Real-time Inventory** — Event-driven inventory updates with Kafka instead of polling

---

## Key Interview Discussion Points

1. **How to prevent overselling?** Optimistic locking + atomic decrement + compensating transactions
2. **Saga vs 2PC?** Saga for microservices (eventual consistency); 2PC for monolith (ACID)
3. **How to handle flash sales?** Pre-warm cache, rate limit, queue-based ordering, pre-deduct inventory
4. **Cart: Redis vs DB?** Redis for speed + DB for persistence; merge on login
5. **How does Amazon handle Prime Day?** Throttling, cell-based architecture, pre-provisioned capacity
