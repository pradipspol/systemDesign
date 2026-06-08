# 17. Payment System

> **Difficulty**: Hard | **Asked by**: Stripe, PayPal, Amazon, Google, Square, Visa

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
1. Accept payments (credit card, debit, bank transfer, wallets)
2. Process refunds (full and partial)
3. Multi-currency support
4. Payment status tracking
5. Reconciliation with external processors
6. Webhook notifications for payment events

### Non-Functional Requirements
1. **Reliability**: 99.999% — zero payment loss
2. **Idempotency**: Same request processed exactly once
3. **Consistency**: Strong consistency (money can't be duplicated)
4. **Security**: PCI DSS compliant, encrypted at rest and in transit
5. **Audit Trail**: Complete, immutable transaction history
6. **Low Latency**: < 2 seconds for payment processing

---

## Capacity Estimation

```
Transactions/day: 10M (peak: 100M on Black Friday)
Average transaction: $50
Daily volume: $500M
TPS: ~115 (average), ~1,200 (peak)
Storage per transaction: 2 KB (metadata + audit)
Annual storage: 3.6B × 2 KB = 7.2 TB
Reconciliation records: same volume, separate store
```

---

## High-Level Design

### Architecture Overview

```mermaid
graph TB
    Client[Merchant / Client] --> GW[API Gateway<br/>TLS, Auth, Rate Limit]
    
    GW --> PayAPI[Payment API Service]
    PayAPI --> IdemSvc[Idempotency Service<br/>Redis]
    PayAPI --> RiskSvc[Risk/Fraud Service]
    
    PayAPI --> PayEngine[Payment Engine<br/>Orchestrator]
    
    PayEngine --> Ledger[Double-Entry Ledger<br/>PostgreSQL]
    PayEngine --> PSP{Payment Service Provider}
    
    PSP --> Visa[Visa Network]
    PSP --> MC[Mastercard]
    PSP --> Stripe[Stripe]
    PSP --> PayPal[PayPal]
    
    PayEngine --> Kafka[(Kafka<br/>Payment Events)]
    
    Kafka --> Webhook[Webhook Service<br/>Notify merchants]
    Kafka --> Recon[Reconciliation Service]
    Kafka --> Analytics[Analytics & Reporting]
    
    subgraph "Data Stores"
        Ledger
        PayDB[(Payment DB<br/>PostgreSQL)]
        AuditLog[(Audit Log<br/>Append-only)]
    end
```

### Payment Flow

```mermaid
sequenceDiagram
    participant M as Merchant
    participant API as Payment API
    participant Idem as Idempotency
    participant Risk as Fraud Check
    participant Engine as Payment Engine
    participant Ledger as Ledger
    participant PSP as Payment Processor
    participant Webhook as Webhook
    
    M->>API: POST /payments (idempotency-key: abc123)
    API->>Idem: Check idempotency key
    
    alt Already processed
        Idem-->>API: Return cached result
        API-->>M: Previous response
    else New request
        API->>Risk: Fraud check (amount, card, user)
        
        alt High risk
            Risk-->>API: Blocked ❌
            API-->>M: Payment declined (fraud)
        else Approved
            API->>Engine: Process payment
            Engine->>Ledger: Create pending ledger entry
            Engine->>PSP: Charge card/account
            
            alt PSP Success
                PSP-->>Engine: Authorized
                Engine->>Ledger: Update: completed
                Engine->>Idem: Cache result
                Engine-->>API: Payment successful
                API-->>M: 200 OK {payment_id, status: success}
                Engine->>Webhook: Payment.completed event
            else PSP Failure
                PSP-->>Engine: Declined
                Engine->>Ledger: Update: failed
                Engine-->>API: Payment failed
                API-->>M: 402 {reason: insufficient_funds}
            end
        end
    end
```

### Refund Flow

```mermaid
sequenceDiagram
    participant M as Merchant
    participant API as Refund API
    participant Engine as Payment Engine
    participant Ledger as Ledger
    participant PSP as Payment Processor
    
    M->>API: POST /refunds {payment_id, amount: $25}
    API->>Engine: Process refund
    
    Engine->>Engine: Validate: amount ≤ original - already_refunded
    Engine->>Ledger: Create refund ledger entry (pending)
    Engine->>PSP: Initiate refund
    
    PSP-->>Engine: Refund initiated (async)
    Engine->>Ledger: Update: processing
    Engine-->>M: 202 Accepted {refund_id, status: processing}
    
    Note over PSP: 3-5 business days
    
    PSP->>Engine: Webhook: refund completed
    Engine->>Ledger: Update: completed
    Engine->>M: Webhook: refund.completed
```

---

## Low-Level Design

### Double-Entry Ledger

```mermaid
graph TD
    subgraph "Double-Entry Bookkeeping"
        Tx["Transaction: Customer pays $100"]
        
        Debit["DEBIT: Customer Account -$100<br/>(decrease asset)"]
        Credit["CREDIT: Merchant Account +$100<br/>(increase liability)"]
        
        Tx --> Debit
        Tx --> Credit
        
        Rule["Rule: Total Debits = Total Credits<br/>Always balanced ✅"]
    end
    
    subgraph "Ledger Entries"
        L1["Entry 1: payment_123<br/>Account: customer_A | Debit: $100 | Credit: $0"]
        L2["Entry 2: payment_123<br/>Account: merchant_B | Debit: $0 | Credit: $97"]
        L3["Entry 3: payment_123<br/>Account: platform_fee | Debit: $0 | Credit: $3"]
    end
```

### Data Models

```mermaid
erDiagram
    PAYMENT {
        uuid id PK
        varchar idempotency_key UK
        bigint merchant_id FK
        decimal amount
        varchar currency "USD|EUR|GBP"
        varchar status "pending|authorized|captured|failed|refunded"
        varchar payment_method "card|bank|wallet"
        jsonb payment_method_details "encrypted"
        varchar psp_reference "external ID"
        varchar risk_score
        timestamp created_at
        timestamp updated_at
    }
    
    LEDGER_ENTRY {
        bigint id PK
        uuid payment_id FK
        varchar account_id "from or to account"
        varchar entry_type "debit|credit"
        decimal amount
        varchar currency
        decimal running_balance
        timestamp created_at
    }
    
    REFUND {
        uuid id PK
        uuid payment_id FK
        decimal amount
        varchar status "pending|processing|completed|failed"
        varchar reason
        timestamp created_at
    }
    
    AUDIT_LOG {
        bigint id PK
        uuid entity_id
        varchar entity_type "payment|refund"
        varchar action
        jsonb old_state
        jsonb new_state
        varchar actor
        timestamp created_at
    }
    
    PAYMENT ||--|{ LEDGER_ENTRY : records
    PAYMENT ||--o{ REFUND : may_have
    PAYMENT ||--|{ AUDIT_LOG : tracked_by
```

### Idempotency Implementation

```mermaid
flowchart TD
    Request["POST /payments<br/>Idempotency-Key: abc123"] --> Check{Key exists<br/>in Redis?}
    
    Check -->|Yes| Status{Stored status?}
    Status -->|Completed| Return["Return cached response<br/>(same result as before)"]
    Status -->|In-progress| Wait["Return 409 Conflict<br/>(processing in progress)"]
    
    Check -->|No| Lock["Set key in Redis<br/>SET abc123 'processing' NX EX 86400"]
    Lock --> Process["Process payment"]
    Process --> Store["Store result in Redis<br/>SET abc123 {response} EX 86400"]
    Store --> Respond["Return response"]
```

### Payment State Machine

```mermaid
stateDiagram-v2
    [*] --> CREATED: Payment initiated
    CREATED --> RISK_CHECK: Submit for fraud check
    RISK_CHECK --> DECLINED: High risk
    RISK_CHECK --> AUTHORIZED: Low risk, card authorized
    AUTHORIZED --> CAPTURED: Capture funds
    AUTHORIZED --> VOIDED: Cancel before capture
    CAPTURED --> PARTIALLY_REFUNDED: Partial refund
    CAPTURED --> FULLY_REFUNDED: Full refund
    PARTIALLY_REFUNDED --> FULLY_REFUNDED: Remaining refund
    DECLINED --> [*]
    VOIDED --> [*]
    FULLY_REFUNDED --> [*]
```

### Reconciliation Process

```mermaid
flowchart TD
    subgraph "Daily Reconciliation"
        Internal["Internal Ledger<br/>Our records"]
        External["PSP Settlement Report<br/>Stripe/Visa records"]
        
        Internal --> Compare["Compare records<br/>Match by PSP reference ID"]
        External --> Compare
        
        Compare --> Match{All match?}
        Match -->|Yes| Balanced["✅ Balanced"]
        Match -->|No| Discrepancy["⚠️ Discrepancy found"]
        
        Discrepancy --> Classify{Type?}
        Classify --> Missing["Missing in our records<br/>→ investigate"]
        Classify --> Extra["Extra in our records<br/>→ delayed PSP processing"]
        Classify --> AmtDiff["Amount mismatch<br/>→ currency/fee issue"]
        
        Missing & Extra & AmtDiff --> Alert["Alert Finance team"]
    end
```

---

## Implementation

### Payment Service Core

```python
import uuid
import json
from decimal import Decimal
from enum import Enum
from dataclasses import dataclass
from typing import Optional

class PaymentStatus(Enum):
    CREATED = "created"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    REFUNDED = "refunded"

@dataclass
class PaymentRequest:
    merchant_id: int
    amount: Decimal
    currency: str
    payment_method: dict  # {type: "card", card_token: "tok_xxx"}
    idempotency_key: str
    description: Optional[str] = None
    metadata: Optional[dict] = None

class PaymentService:
    """Core payment processing with idempotency and double-entry ledger."""
    
    def __init__(self, db, redis, psp_client, ledger, 
                 risk_service, event_bus):
        self.db = db
        self.redis = redis
        self.psp = psp_client
        self.ledger = ledger
        self.risk = risk_service
        self.events = event_bus
    
    async def process_payment(self, request: PaymentRequest) -> dict:
        """Process a payment with idempotency guarantee."""
        
        # 1. Idempotency check
        cached = await self._check_idempotency(request.idempotency_key)
        if cached:
            return cached
        
        payment_id = str(uuid.uuid4())
        
        try:
            # 2. Mark as processing
            await self._set_processing(request.idempotency_key, payment_id)
            
            # 3. Create payment record
            await self.db.create_payment({
                "id": payment_id,
                "merchant_id": request.merchant_id,
                "amount": request.amount,
                "currency": request.currency,
                "status": PaymentStatus.CREATED.value
            })
            
            # 4. Fraud/risk check
            risk_result = await self.risk.evaluate(request)
            if risk_result["action"] == "block":
                await self._fail_payment(payment_id, "fraud_detected")
                return {"payment_id": payment_id, "status": "declined",
                        "reason": "risk_check_failed"}
            
            # 5. Process with PSP
            psp_result = await self.psp.charge(
                amount=int(request.amount * 100),  # cents
                currency=request.currency,
                payment_method=request.payment_method,
                idempotency_key=request.idempotency_key
            )
            
            if psp_result["status"] == "succeeded":
                # 6. Update ledger (double-entry)
                await self.ledger.record_payment(
                    payment_id=payment_id,
                    customer_account=f"customer:{request.merchant_id}",
                    merchant_account=f"merchant:{request.merchant_id}",
                    amount=request.amount,
                    currency=request.currency
                )
                
                # 7. Update payment status
                await self.db.update_payment(payment_id, {
                    "status": PaymentStatus.CAPTURED.value,
                    "psp_reference": psp_result["id"]
                })
                
                result = {
                    "payment_id": payment_id,
                    "status": "succeeded",
                    "psp_reference": psp_result["id"]
                }
            else:
                await self._fail_payment(payment_id, psp_result.get("decline_reason"))
                result = {
                    "payment_id": payment_id,
                    "status": "failed",
                    "reason": psp_result.get("decline_reason")
                }
            
            # 8. Cache result for idempotency
            await self._cache_result(request.idempotency_key, result)
            
            # 9. Publish event
            await self.events.publish(f"payment.{result['status']}", result)
            
            return result
        
        except Exception as e:
            await self._fail_payment(payment_id, str(e))
            raise
    
    async def _check_idempotency(self, key: str) -> Optional[dict]:
        """Check if request was already processed."""
        result = self.redis.get(f"idem:{key}")
        if result:
            data = json.loads(result)
            if data.get("status") == "processing":
                raise Exception("Payment already in progress")
            return data
        return None
    
    async def _set_processing(self, key: str, payment_id: str):
        """Mark idempotency key as processing."""
        self.redis.set(
            f"idem:{key}",
            json.dumps({"status": "processing", "payment_id": payment_id}),
            nx=True, ex=86400  # 24h TTL
        )
    
    async def _cache_result(self, key: str, result: dict):
        self.redis.set(f"idem:{key}", json.dumps(result), ex=86400)
    
    async def _fail_payment(self, payment_id: str, reason: str):
        await self.db.update_payment(payment_id, {
            "status": PaymentStatus.FAILED.value,
            "failure_reason": reason
        })


class DoubleEntryLedger:
    """Immutable, balanced double-entry ledger."""
    
    def __init__(self, db):
        self.db = db
    
    async def record_payment(self, payment_id: str, customer_account: str,
                             merchant_account: str, amount: Decimal,
                             currency: str):
        """Record payment as balanced debit/credit entries."""
        # Single transaction ensures atomicity
        async with self.db.transaction() as tx:
            # Debit customer (money leaves)
            await tx.execute("""
                INSERT INTO ledger_entries 
                (payment_id, account_id, entry_type, amount, currency)
                VALUES (%s, %s, 'debit', %s, %s)
            """, (payment_id, customer_account, amount, currency))
            
            # Calculate fees
            platform_fee = amount * Decimal("0.029")  # 2.9%
            merchant_payout = amount - platform_fee
            
            # Credit merchant
            await tx.execute("""
                INSERT INTO ledger_entries 
                (payment_id, account_id, entry_type, amount, currency)
                VALUES (%s, %s, 'credit', %s, %s)
            """, (payment_id, merchant_account, merchant_payout, currency))
            
            # Credit platform fee account
            await tx.execute("""
                INSERT INTO ledger_entries 
                (payment_id, account_id, entry_type, amount, currency)
                VALUES (%s, %s, 'credit', %s, %s)
            """, (payment_id, "platform_fees", platform_fee, currency))
```

---

## Limitations & Improvements

### Current Limitations

| Limitation | Impact | Severity |
|-----------|--------|----------|
| PSP single point of failure | Payment processing down | Critical |
| Cross-border currency complexity | Exchange rate fluctuations | High |
| Reconciliation delay (T+1 or later) | Late discrepancy detection | Medium |
| PCI compliance overhead | Complex infrastructure requirements | High |
| Webhook delivery failures | Merchants miss events | Medium |

### Improvement Areas

1. **Multi-PSP Failover** — Route to backup PSP if primary is down
2. **Smart Routing** — Route to cheapest/fastest PSP per transaction type
3. **Real-time Fraud ML** — Neural network for fraud detection
4. **Ledger Sharding** — Partition by merchant for horizontal scaling
5. **Webhook Retry** — Exponential backoff with dead letter queue for failed webhooks

---

## Key Interview Discussion Points

1. **Why idempotency?** Network failures cause retries; without idempotency, customer charged twice
2. **Why double-entry ledger?** Self-verifying: sum of debits = sum of credits; catches bugs
3. **How to handle PSP timeout?** Polling + webhook; reconciliation catches mismatches
4. **PCI DSS compliance?** Tokenize card data; never store raw card numbers; use PSP tokens
5. **Exactly-once payment?** Idempotency key + PSP idempotency + DB unique constraints
