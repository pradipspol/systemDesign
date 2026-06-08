"""
=============================================================
  16. E-Commerce — Order Service with Saga Pattern
  Run: python 16_e_commerce.py
  Implements catalog, cart, inventory reservation, order
  processing with saga pattern, and compensation on failure.
=============================================================
"""
import time
import uuid
import threading
from enum import Enum
from dataclasses import dataclass, field
from collections import defaultdict
from typing import Optional


# ===================================================================
# Data Models
# ===================================================================
class OrderStatus(Enum):
    CREATED = "created"
    INVENTORY_RESERVED = "inventory_reserved"
    PAYMENT_PROCESSED = "payment_processed"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class Product:
    product_id: str
    name: str
    price: float
    stock: int
    reserved: int = 0
    version: int = 0  # for optimistic locking
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def available(self) -> int:
        return self.stock - self.reserved


@dataclass
class CartItem:
    product_id: str
    quantity: int
    price: float


@dataclass
class Order:
    order_id: str
    user_id: str
    items: list[CartItem]
    status: OrderStatus = OrderStatus.CREATED
    total: float = 0.0
    payment_id: str = ""
    shipping_id: str = ""
    created_at: float = field(default_factory=time.time)
    saga_log: list[str] = field(default_factory=list)


# ===================================================================
# Inventory Service (optimistic locking)
# ===================================================================
class InventoryService:
    def __init__(self):
        self.products: dict[str, Product] = {}
        self._lock = threading.Lock()

    def add_product(self, product: Product):
        self.products[product.product_id] = product

    def reserve(self, product_id: str, quantity: int) -> bool:
        product = self.products.get(product_id)
        if not product:
            return False
        with product._lock:
            if product.available >= quantity:
                product.reserved += quantity
                product.version += 1
                return True
            return False

    def release(self, product_id: str, quantity: int):
        product = self.products.get(product_id)
        if product:
            with product._lock:
                product.reserved = max(0, product.reserved - quantity)
                product.version += 1

    def confirm(self, product_id: str, quantity: int):
        product = self.products.get(product_id)
        if product:
            with product._lock:
                product.stock -= quantity
                product.reserved -= quantity
                product.version += 1

    def get_stock(self, product_id: str) -> dict:
        p = self.products.get(product_id)
        if not p:
            return {}
        return {"stock": p.stock, "reserved": p.reserved, "available": p.available, "version": p.version}


# ===================================================================
# Payment Service (simulated)
# ===================================================================
class PaymentService:
    def __init__(self, fail_rate: float = 0.0):
        self.fail_rate = fail_rate
        self.payments: dict[str, dict] = {}

    def charge(self, user_id: str, amount: float, order_id: str) -> tuple[bool, str]:
        payment_id = f"pay_{uuid.uuid4().hex[:8]}"
        # Simulate occasional failure
        if hash(order_id) % 100 < int(self.fail_rate * 100):
            return False, ""
        self.payments[payment_id] = {
            "user_id": user_id, "amount": amount,
            "order_id": order_id, "status": "charged",
        }
        return True, payment_id

    def refund(self, payment_id: str) -> bool:
        if payment_id in self.payments:
            self.payments[payment_id]["status"] = "refunded"
            return True
        return False


# ===================================================================
# Shipping Service (simulated)
# ===================================================================
class ShippingService:
    def __init__(self):
        self.shipments: dict[str, dict] = {}

    def create_shipment(self, order_id: str, address: str = "123 Main St") -> str:
        ship_id = f"ship_{uuid.uuid4().hex[:8]}"
        self.shipments[ship_id] = {
            "order_id": order_id, "address": address, "status": "created",
        }
        return ship_id


# ===================================================================
# Saga Orchestrator
# ===================================================================
class OrderSaga:
    """
    Saga steps:
    1. Reserve inventory
    2. Process payment
    3. Create shipment
    Compensation (reverse order):
    3c. Cancel shipment
    2c. Refund payment
    1c. Release inventory
    """

    def __init__(self, inventory: InventoryService, payment: PaymentService,
                 shipping: ShippingService):
        self.inventory = inventory
        self.payment = payment
        self.shipping = shipping

    def execute(self, order: Order) -> bool:
        reserved_items = []

        # Step 1: Reserve inventory
        order.saga_log.append("STEP 1: Reserve inventory")
        for item in order.items:
            if self.inventory.reserve(item.product_id, item.quantity):
                reserved_items.append(item)
                order.saga_log.append(f"  Reserved {item.quantity}x {item.product_id}")
            else:
                order.saga_log.append(f"  FAILED: {item.product_id} out of stock")
                self._compensate_inventory(reserved_items)
                order.status = OrderStatus.FAILED
                order.saga_log.append("COMPENSATED: Released all reserved inventory")
                return False
        order.status = OrderStatus.INVENTORY_RESERVED

        # Step 2: Process payment
        order.saga_log.append("STEP 2: Process payment")
        success, payment_id = self.payment.charge(order.user_id, order.total, order.order_id)
        if not success:
            order.saga_log.append("  FAILED: Payment declined")
            self._compensate_inventory(reserved_items)
            order.status = OrderStatus.FAILED
            order.saga_log.append("COMPENSATED: Released inventory")
            return False
        order.payment_id = payment_id
        order.status = OrderStatus.PAYMENT_PROCESSED
        order.saga_log.append(f"  Payment {payment_id} charged ${order.total:.2f}")

        # Step 3: Create shipment
        order.saga_log.append("STEP 3: Create shipment")
        ship_id = self.shipping.create_shipment(order.order_id)
        order.shipping_id = ship_id
        order.status = OrderStatus.CONFIRMED
        order.saga_log.append(f"  Shipment {ship_id} created")

        # Confirm inventory (reduce stock permanently)
        for item in order.items:
            self.inventory.confirm(item.product_id, item.quantity)

        order.saga_log.append("ORDER CONFIRMED ✓")
        return True

    def _compensate_inventory(self, items: list[CartItem]):
        for item in items:
            self.inventory.release(item.product_id, item.quantity)


# ===================================================================
# E-Commerce Service
# ===================================================================
class ECommerceService:
    def __init__(self):
        self.inventory = InventoryService()
        self.payment = PaymentService(fail_rate=0.0)
        self.shipping = ShippingService()
        self.saga = OrderSaga(self.inventory, self.payment, self.shipping)
        self.orders: dict[str, Order] = {}
        self.carts: dict[str, list[CartItem]] = defaultdict(list)

    def add_product(self, pid: str, name: str, price: float, stock: int):
        self.inventory.add_product(Product(pid, name, price, stock))

    def add_to_cart(self, user_id: str, product_id: str, quantity: int) -> dict:
        product = self.inventory.products.get(product_id)
        if not product:
            return {"error": "Product not found"}
        self.carts[user_id].append(CartItem(product_id, quantity, product.price))
        return {"cart_size": len(self.carts[user_id])}

    def checkout(self, user_id: str) -> Order:
        items = self.carts.get(user_id, [])
        if not items:
            return Order(order_id="", user_id=user_id, items=[], status=OrderStatus.FAILED)

        order = Order(
            order_id=f"ord_{uuid.uuid4().hex[:8]}",
            user_id=user_id,
            items=items[:],
            total=sum(item.price * item.quantity for item in items),
        )
        self.orders[order.order_id] = order

        success = self.saga.execute(order)
        if success:
            self.carts[user_id].clear()
        return order


# ===================================================================
# Demo
# ===================================================================
if __name__ == "__main__":
    print("=" * 65)
    print("  E-Commerce — Order Saga Pattern")
    print("=" * 65)

    svc = ECommerceService()

    # Add products
    products = [
        ("p1", "MacBook Pro 16\"", 2499.99, 10),
        ("p2", "iPhone 15 Pro", 1199.99, 25),
        ("p3", "AirPods Pro", 249.99, 50),
        ("p4", "iPad Air", 599.99, 15),
        ("p5", "Limited Edition Watch", 999.99, 2),
    ]
    for pid, name, price, stock in products:
        svc.add_product(pid, name, price, stock)

    print("\n  Products:")
    for pid, p in svc.inventory.products.items():
        print(f"    {p.name:25s} ${p.price:>8.2f}  stock={p.stock}")

    # == Successful order ==
    print("\n  === Successful Order ===")
    svc.add_to_cart("alice", "p1", 1)
    svc.add_to_cart("alice", "p3", 2)
    order1 = svc.checkout("alice")
    print(f"  Order {order1.order_id}: {order1.status.value}")
    print(f"  Total: ${order1.total:.2f}")
    for log in order1.saga_log:
        print(f"    {log}")

    # == Out of stock order ==
    print("\n  === Out of Stock Order ===")
    svc.add_to_cart("bob", "p5", 3)  # only 2 in stock
    order2 = svc.checkout("bob")
    print(f"  Order {order2.order_id}: {order2.status.value}")
    for log in order2.saga_log:
        print(f"    {log}")

    # == Payment failure scenario ==
    print("\n  === Payment Failure (simulated) ===")
    svc.payment.fail_rate = 1.0  # Force failure
    svc.add_to_cart("charlie", "p2", 1)
    order3 = svc.checkout("charlie")
    print(f"  Order {order3.order_id}: {order3.status.value}")
    for log in order3.saga_log:
        print(f"    {log}")
    svc.payment.fail_rate = 0.0

    # == Concurrent orders for limited stock ==
    print("\n  === Concurrent Orders (2 items, 5 buyers) ===")
    results = []
    def try_buy(user_id):
        svc.add_to_cart(user_id, "p5", 1)
        o = svc.checkout(user_id)
        results.append((user_id, o.status.value))

    threads = [threading.Thread(target=try_buy, args=(f"buyer_{i}",)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for uid, status in results:
        print(f"    {uid}: {status}")

    # Inventory state
    print("\n  Final Inventory:")
    for pid, p in svc.inventory.products.items():
        print(f"    {p.name:25s} stock={p.stock:3d} reserved={p.reserved:3d} "
              f"available={p.available:3d} v{p.version}")

    print("\nDone.")
