Pupero-Transactions (WalletManagerDB)

Overview
- This service acts as the Pupero Transactions service.
- It exposes endpoints to: 
  - Manage user balances (fake_xmr/real_xmr)
  - Enqueue withdrawals to RabbitMQ for asynchronous on-chain processing
  - Enqueue trades (seller -> buyer transfers) to RabbitMQ for asynchronous processing (no immediate balance mutation)

Why here?
- The repository component Pupero-WalletManagerDB already contained the balance and ledger logic and a RabbitMQ publisher for withdrawals.
- We extended it with a Pupero-transactions trade-queuing endpoint, reusing the same service to minimize changes across the stack.

Endpoints
1) POST /transactions/trade
- Purpose: Queue a trade event (seller gives X to buyer) for asynchronous processing. Does not mutate balances now.
- Request body (JSON):
  {
    "seller_id": 123,
    "buyer_id": 456,
    "amount_xmr": 1.23,
    "offer_id": "optional-offer-public-id"
  }
- Response (200):
  {
    "seller_id": 123,
    "buyer_id": 456,
    "amount_xmr": 1.23,
    "offer_id": "optional-offer-public-id",
    "queued": true,
    "enqueued_at": "2025-01-01T00:00:00Z",
    "queue": "wallet.trades"
  }
- Behavior: Only publishes a message to RabbitMQ. The consumer will later debit the seller and credit the buyer.

2) POST /withdraw/{user_id}
- Purpose: Queue an on-chain Monero withdrawal for asynchronous processing.
- Request body (JSON):
  {
    "to_address": "4...",
    "amount_xmr": 0.5
  }
- Response (200):
  {
    "to_address": "4...",
    "amount_xmr": 0.5,
    "tx_hash": null,
    "monero_result": null
  }
- Behavior: Only publishes a message to RabbitMQ (no immediate RPC is performed here).

3) Existing endpoints for balances and immediate local transfers remain intact (used mainly for testing and system ops):
- GET /balance/{user_id}
- POST /balance/{user_id}/set
- POST /balance/{user_id}/increase
- POST /balance/{user_id}/decrease
- POST /transactions/transfer (immediate local ledger move, not queued)

RabbitMQ
- Env vars:
  - RABBITMQ_URL: connection string, e.g. amqp://guest:guest@rabbitmq:5672//
  - RABBITMQ_QUEUE: queue for withdrawals (default: monero.transactions)
  - RABBITMQ_TRADE_QUEUE: queue for trades (default: wallet.trades)
- Message formats:
  - Trade:
    {
      "type": "trade",
      "seller_id": 123,
      "buyer_id": 456,
      "amount_xmr": 1.23,
      "offer_id": "optional-offer-public-id",
      "requested_at": "2025-01-01T00:00:00Z"
    }
  - Withdraw:
    {
      "type": "withdraw",
      "user_id": 123,
      "to_address": "4...",
      "amount_xmr": 0.5,
      "from_address": "<optional user subaddress chosen>",
      "requested_at": "2025-01-01T00:00:00Z"
    }

Monero Integration
- The service can discover real XMR balances by talking to the Monero Wallet Manager via HTTP (MONERO_SERVICE_URL).
- For withdrawals, the service tries to choose a suitable user subaddress with enough unlocked balance and includes it in the queued message where possible.

Configuration
- MONERO_SERVICE_URL: either service name (monero or api-manager) or full URL. Examples:
  - monero -> http://monero:8004
  - api-manager -> http://api-manager:8000/monero
  - explicit URL -> http://host:port

Examples
- Enqueue a trade:
  curl -X POST http://localhost:8000/transactions/trade \
    -H 'Content-Type: application/json' \
    -d '{"seller_id": 1, "buyer_id": 2, "amount_xmr": 0.75, "offer_id": "OF-123"}'

- Enqueue a withdrawal:
  curl -X POST http://localhost:8000/withdraw/1 \
    -H 'Content-Type: application/json' \
    -d '{"to_address": "44...", "amount_xmr": 0.2}'

Notes
- Per current requirement: trading and withdrawals only enqueue messages; the actual effects are applied by downstream consumers.
