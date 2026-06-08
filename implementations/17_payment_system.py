"""
=============================================================
  17. Payment System — Idempotent Payments + Double-Entry Ledger
  Run: python 17_payment_system.py
  Test: curl -X POST http://localhost:5017/api/v1/pay -H "Content-Type: application/json" -d "{\"payer\":\"alice\",\"payee\":\"merchant_1\",\"amount\":99.99,\"idempotency_key\":\"order_123\"}"
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
class PaymentStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


class LedgerEntryType(Enum):
    DEBIT = "debit"
    CREDIT = "credit"


@dataclass
class LedgerEntry:
    entry_id: str
    account_id: str
    entry_type: LedgerEntryType
    amount: float
    payment_id: str
    description: str
    balance_after: float = 0.0
    created_at: float = field(default_factory=time.time)


@dataclass
class Payment:
    payment_id: str
    idempotency_key: str
    payer: str
    payee: str
    amount: float
    currency: str = "USD"
    status: PaymentStatus = PaymentStatus.PENDING
    ledger_entries: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    error: str = ""


@dataclass
class Account:
    account_id: str
    balance: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


# ===================================================================
# Double-Entry Ledger
# ===================================================================
class Ledger:
    """Every transaction creates exactly 2 entries: debit + credit."""

    def __init__(self):
        self.entries: list[LedgerEntry] = []
        self.account_entries: dict[str, list[LedgerEntry]] = defaultdict(list)
        self._lock = threading.Lock()

    def record(self, debit_account: str, credit_account: str,
               amount: float, payment_id: str, description: str,
               accounts: dict[str, Account]) -> tuple[LedgerEntry, LedgerEntry]:
        with self._lock:
            # Debit entry (money goes out)
            debit_entry = LedgerEntry(
                entry_id=f"le_{uuid.uuid4().hex[:8]}",
                account_id=debit_account,
                entry_type=LedgerEntryType.DEBIT,
                amount=amount,
                payment_id=payment_id,
                description=f"DEBIT: {description}",
                balance_after=accounts[debit_account].balance,
            )

            # Credit entry (money comes in)
            credit_entry = LedgerEntry(
                entry_id=f"le_{uuid.uuid4().hex[:8]}",
                account_id=credit_account,
                entry_type=LedgerEntryType.CREDIT,
                amount=amount,
                payment_id=payment_id,
                description=f"CREDIT: {description}",
                balance_after=accounts[credit_account].balance,
            )

            self.entries.extend([debit_entry, credit_entry])
            self.account_entries[debit_account].append(debit_entry)
            self.account_entries[credit_account].append(credit_entry)

            return debit_entry, credit_entry

    def get_balance_proof(self) -> dict:
        """Verify all debits == all credits."""
        total_debits = sum(e.amount for e in self.entries if e.entry_type == LedgerEntryType.DEBIT)
        total_credits = sum(e.amount for e in self.entries if e.entry_type == LedgerEntryType.CREDIT)
        return {
            "total_debits": round(total_debits, 2),
            "total_credits": round(total_credits, 2),
            "balanced": abs(total_debits - total_credits) < 0.01,
            "total_entries": len(self.entries),
        }


# ===================================================================
# Idempotency Store
# ===================================================================
class IdempotencyStore:
    def __init__(self, ttl_seconds: float = 86400):
        self.store: dict[str, dict] = {}  # key -> {payment_id, response, timestamp}
        self.ttl = ttl_seconds
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[dict]:
        with self._lock:
            entry = self.store.get(key)
            if entry and time.time() - entry["timestamp"] < self.ttl:
                return entry
            return None

    def set(self, key: str, payment_id: str, response: dict):
        with self._lock:
            self.store[key] = {
                "payment_id": payment_id,
                "response": response,
                "timestamp": time.time(),
            }

    def exists(self, key: str) -> bool:
        return self.get(key) is not None


from typing import Optional


# ===================================================================
# Payment Service
# ===================================================================
class PaymentService:
    def __init__(self):
        self.accounts: dict[str, Account] = {}
        self.payments: dict[str, Payment] = {}
        self.ledger = Ledger()
        self.idempotency = IdempotencyStore()
        self._stats = {"processed": 0, "failed": 0, "refunded": 0, "idempotent_hits": 0}

    def create_account(self, account_id: str, initial_balance: float = 0.0) -> Account:
        account = Account(account_id=account_id, balance=initial_balance)
        self.accounts[account_id] = account
        return account

    def process_payment(self, payer: str, payee: str, amount: float,
                        idempotency_key: str, currency: str = "USD") -> dict:
        # 1. Idempotency check
        existing = self.idempotency.get(idempotency_key)
        if existing:
            self._stats["idempotent_hits"] += 1
            return {**existing["response"], "idempotent": True}

        # 2. Validation
        payer_acc = self.accounts.get(payer)
        payee_acc = self.accounts.get(payee)
        if not payer_acc or not payee_acc:
            return {"error": "Account not found", "status": "failed"}
        if amount <= 0:
            return {"error": "Invalid amount", "status": "failed"}

        # 3. Create payment
        payment = Payment(
            payment_id=f"txn_{uuid.uuid4().hex[:8]}",
            idempotency_key=idempotency_key,
            payer=payer,
            payee=payee,
            amount=amount,
            currency=currency,
            status=PaymentStatus.PROCESSING,
        )
        self.payments[payment.payment_id] = payment

        # 4. Check balance & transfer (atomic)
        with payer_acc._lock:
            if payer_acc.balance < amount:
                payment.status = PaymentStatus.FAILED
                payment.error = "Insufficient balance"
                self._stats["failed"] += 1
                response = {"error": "Insufficient balance", "status": "failed",
                           "payment_id": payment.payment_id}
                self.idempotency.set(idempotency_key, payment.payment_id, response)
                return response

            payer_acc.balance -= amount

        with payee_acc._lock:
            payee_acc.balance += amount

        # 5. Record in ledger
        debit, credit = self.ledger.record(
            payer, payee, amount, payment.payment_id,
            f"Payment from {payer} to {payee}",
            self.accounts,
        )
        payment.ledger_entries = [debit.entry_id, credit.entry_id]
        payment.status = PaymentStatus.COMPLETED
        payment.completed_at = time.time()
        self._stats["processed"] += 1

        response = {
            "status": "completed",
            "payment_id": payment.payment_id,
            "amount": amount,
            "currency": currency,
            "payer_balance": payer_acc.balance,
        }
        self.idempotency.set(idempotency_key, payment.payment_id, response)
        return response

    def refund(self, payment_id: str) -> dict:
        payment = self.payments.get(payment_id)
        if not payment:
            return {"error": "Payment not found"}
        if payment.status != PaymentStatus.COMPLETED:
            return {"error": f"Cannot refund {payment.status.value} payment"}

        # Reverse the payment
        result = self.process_payment(
            payer=payment.payee,
            payee=payment.payer,
            amount=payment.amount,
            idempotency_key=f"refund_{payment_id}",
        )

        if result.get("status") == "completed":
            payment.status = PaymentStatus.REFUNDED
            self._stats["refunded"] += 1
            return {"status": "refunded", "refund_payment_id": result["payment_id"]}
        return {"error": "Refund failed", "details": result}

    def get_statement(self, account_id: str) -> list[dict]:
        entries = self.ledger.account_entries.get(account_id, [])
        return [
            {
                "entry_id": e.entry_id,
                "type": e.entry_type.value,
                "amount": e.amount,
                "description": e.description,
                "payment_id": e.payment_id,
            }
            for e in entries
        ]

    def stats(self) -> dict:
        return {**self._stats, "ledger": self.ledger.get_balance_proof()}


# ===================================================================
# Singleton
# ===================================================================
svc = PaymentService()


# ===================================================================
# Flask Routes
# ===================================================================
@app.route("/api/v1/pay", methods=["POST"])
def pay():
    data = request.get_json(force=True)
    result = svc.process_payment(
        data["payer"], data["payee"], data["amount"],
        data["idempotency_key"], data.get("currency", "USD"),
    )
    code = 201 if result.get("status") == "completed" else 400
    return jsonify(result), code


@app.route("/api/v1/refund/<payment_id>", methods=["POST"])
def refund(payment_id):
    return jsonify(svc.refund(payment_id))


@app.route("/api/v1/balance/<account_id>")
def balance(account_id):
    acc = svc.accounts.get(account_id)
    if not acc:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"account": account_id, "balance": acc.balance})


@app.route("/api/v1/statement/<account_id>")
def statement(account_id):
    return jsonify(svc.get_statement(account_id))


@app.route("/api/v1/ledger/verify")
def verify_ledger():
    return jsonify(svc.ledger.get_balance_proof())


# ===================================================================
# Demo
# ===================================================================
def init_demo():
    svc.create_account("alice", 10000.0)
    svc.create_account("bob", 5000.0)
    svc.create_account("merchant_1", 0.0)
    svc.create_account("merchant_2", 0.0)


if __name__ == "__main__":
    init_demo()

    print("=" * 65)
    print("  Payment System — Idempotent + Double-Entry Ledger")
    print("  http://localhost:5017")
    print("=" * 65)

    # Normal payment
    print("\n  --- Normal Payments ---")
    r1 = svc.process_payment("alice", "merchant_1", 99.99, "order_001")
    print(f"  Payment 1: {r1}")
    r2 = svc.process_payment("alice", "merchant_2", 250.00, "order_002")
    print(f"  Payment 2: {r2}")

    # Idempotency test (same key)
    print("\n  --- Idempotency Test (same key) ---")
    r3 = svc.process_payment("alice", "merchant_1", 99.99, "order_001")
    print(f"  Retry: {r3}")
    print(f"  Was idempotent hit: {r3.get('idempotent', False)}")

    # Insufficient balance
    print("\n  --- Insufficient Balance ---")
    r4 = svc.process_payment("bob", "merchant_1", 999999.00, "order_003")
    print(f"  Failed: {r4}")

    # P2P transfer
    print("\n  --- P2P Transfer ---")
    r5 = svc.process_payment("bob", "alice", 500.00, "p2p_001")
    print(f"  Bob → Alice: {r5}")

    # Refund
    print("\n  --- Refund ---")
    refund = svc.refund(r1["payment_id"])
    print(f"  Refund: {refund}")

    # Balances
    print("\n  --- Balances ---")
    for aid in ["alice", "bob", "merchant_1", "merchant_2"]:
        acc = svc.accounts[aid]
        print(f"    {aid:15s}: ${acc.balance:>10.2f}")

    # Ledger verification
    print(f"\n  --- Ledger Verification ---")
    proof = svc.ledger.get_balance_proof()
    print(f"  {proof}")

    # Statement
    print(f"\n  --- Alice's Statement ---")
    for entry in svc.get_statement("alice"):
        print(f"    {entry['type']:6s} ${entry['amount']:>8.2f}  {entry['description'][:50]}")

    print(f"\n  Stats: {svc.stats()}")
    print("=" * 65)
    app.run(port=5017, debug=True)
