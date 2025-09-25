from fastapi import FastAPI, Depends, HTTPException, Body
from sqlmodel import Session, select
from typing import Optional
import os, sys
import httpx
import logging, json
import pika
import urllib.parse
from datetime import datetime
from .database import get_session

from .schemas import BalanceOut, BalanceSetRequest, BalanceAdjustRequest, TransferCreate, TransferOut, WithdrawRequest, WithdrawResponse, TradeCreate, TradeQueued
from .models import UserBalance, LedgerTx

app = FastAPI(title="Pupero Transactions Service")

# JSON logger
logger = logging.getLogger("pupero_transactions")
if not logger.handlers:
    _h = logging.StreamHandler()
    logger.setLevel(logging.INFO)
    logger.addHandler(_h)

# Base URL for Monero Wallet Manager (through API Manager or direct service)

def _normalize_monero_base(val: str | None) -> str:
    default = "http://monero:8004"
    if not val:
        return default
    v = val.strip().rstrip("/")
    if "://" in v:
        return v
    name = v
    if name in {"api-manager", "pupero-api-manager"}:
        return f"http://{name}:8000/monero"
    if name in {"monero", "pupero-WalletManager"}:
        return f"http://{name}:8004"
    return default

_MONERO_BASE = _normalize_monero_base(os.getenv("MONERO_SERVICE_URL"))

# RabbitMQ configuration
_RABBIT_URL = os.getenv("RABBITMQ_URL")
_RABBIT_QUEUE = os.getenv("RABBITMQ_QUEUE", "monero.transactions")  # withdrawals default
_RABBIT_TRADE_QUEUE = os.getenv("RABBITMQ_TRADE_QUEUE", "wallet.trades")

def _publish_queue(msg: dict, queue_name: str):
    if not _RABBIT_URL:
        raise HTTPException(status_code=500, detail="RabbitMQ is not configured (RABBITMQ_URL)")
    try:
        params = pika.URLParameters(_RABBIT_URL)
        connection = pika.BlockingConnection(params)
        ch = connection.channel()
        ch.queue_declare(queue=queue_name, durable=True)
        body = json.dumps(msg).encode("utf-8")
        ch.basic_publish(
            exchange="",
            routing_key=queue_name,
            body=body,
            properties=pika.BasicProperties(delivery_mode=2)
        )
        connection.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to enqueue message to {queue_name}: {e}")

def _publish_withdraw(msg: dict):
    _publish_queue(msg, _RABBIT_QUEUE)


def _ensure_balance(session: Session, user_id: int) -> UserBalance:
    stmt = select(UserBalance).where(UserBalance.user_id == user_id)
    bal = session.exec(stmt).first()
    if not bal:
        bal = UserBalance(user_id=user_id, fake_xmr=0.0, real_xmr=0.0)
        session.add(bal)
        session.commit()
        session.refresh(bal)
    return bal


def _fetch_real_xmr(user_id: int) -> float | None:
    """Fetch user's real XMR by querying MoneroWalletManager via API.
    Behavior:
      - Query /addresses?user_id.
      - If none, auto-provision one via POST /addresses and retry once.
      - Sum unlocked_balance_xmr across all subaddresses and return.
      - Return None only on connectivity/errors (so caller can keep existing DB value).
    """
    base = _MONERO_BASE.rstrip("/")
    try:
        with httpx.Client(timeout=10.0) as client:
            # 1) Fetch mapped addresses
            r = client.get(f"{base}/addresses", params={"user_id": user_id})
            if r.status_code != 200:
                logger.info(json.dumps({"event": "monero_addresses_failed", "user_id": user_id, "status": r.status_code}))
                return None
            addresses = r.json() or []
            # 2) Auto-provision a subaddress if missing, then retry once
            if not addresses:
                label = f"user_{user_id}"
                try:
                    cr = client.post(f"{base}/addresses", json={"user_id": user_id, "label": label})
                    logger.info(json.dumps({"event": "monero_address_create_attempt", "user_id": user_id, "status": cr.status_code}))
                except Exception as e:
                    logger.warning(json.dumps({"event": "monero_address_create_error", "user_id": user_id, "error": str(e)}))
                # retry fetch
                r2 = client.get(f"{base}/addresses", params={"user_id": user_id})
                if r2.status_code == 200:
                    addresses = r2.json() or []
                else:
                    logger.info(json.dumps({"event": "monero_addresses_retry_failed", "user_id": user_id, "status": r2.status_code}))
                    return None
            # 3) Sum unlocked balances
            total = 0.0
            any_found = False
            for a in addresses:
                addr = a.get("address")
                if not addr:
                    continue
                rb = client.get(f"{base}/balance/{addr}")
                if rb.status_code != 200:
                    logger.info(json.dumps({"event": "monero_balance_fetch_failed", "user_id": user_id, "address": addr, "status": rb.status_code}))
                    continue
                data = rb.json() or {}
                val = data.get("unlocked_balance_xmr")
                try:
                    v = float(val)
                except Exception:
                    logger.info(json.dumps({"event": "monero_balance_parse_error", "user_id": user_id, "address": addr, "val": val}))
                    continue
                total += v
                any_found = True
            logger.info(json.dumps({"event": "monero_balance_total", "user_id": user_id, "addresses": len(addresses), "total_unlocked_xmr": total}))
            return total if any_found else 0.0
    except Exception as e:
        logger.warning(json.dumps({"event": "monero_fetch_exception", "user_id": user_id, "error": str(e)}))
        return None


def _to_balance_out(b: UserBalance) -> BalanceOut:
    return BalanceOut(user_id=b.user_id, fake_xmr=b.fake_xmr, real_xmr=b.real_xmr, updated_at=b.updated_at)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/balance/{user_id}", response_model=BalanceOut)
def get_balance(user_id: int, session: Session = Depends(get_session)):
    bal = _ensure_balance(session, user_id)
    # Try to refresh real_xmr from Monero wallet manager
    real = _fetch_real_xmr(user_id)
    if real is not None and abs((bal.real_xmr or 0.0) - real) > 1e-12:
        bal.real_xmr = real
        session.add(bal)
        session.commit()
        session.refresh(bal)
    return _to_balance_out(bal)


@app.post("/balance/{user_id}/set", response_model=BalanceOut)
def set_balance(user_id: int, payload: BalanceSetRequest, session: Session = Depends(get_session)):
    bal = _ensure_balance(session, user_id)
    changed = False
    if payload.fake_xmr is not None:
        bal.fake_xmr = float(payload.fake_xmr)
        changed = True
    if payload.real_xmr is not None:
        bal.real_xmr = float(payload.real_xmr)
        changed = True
    if changed:
        session.add(bal)
        session.commit()
        session.refresh(bal)
    return _to_balance_out(bal)


@app.post("/balance/{user_id}/increase", response_model=BalanceOut)
def increase_balance(user_id: int, payload: BalanceAdjustRequest, session: Session = Depends(get_session)):
    if payload.amount_xmr <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")
    bal = _ensure_balance(session, user_id)
    if (payload.kind or "fake") == "real":
        bal.real_xmr += float(payload.amount_xmr)
    else:
        bal.fake_xmr += float(payload.amount_xmr)
    session.add(bal)
    session.commit()
    session.refresh(bal)
    return _to_balance_out(bal)


@app.post("/balance/{user_id}/decrease", response_model=BalanceOut)
def decrease_balance(user_id: int, payload: BalanceAdjustRequest, session: Session = Depends(get_session)):
    if payload.amount_xmr <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")
    bal = _ensure_balance(session, user_id)
    if (payload.kind or "fake") == "real":
        if bal.real_xmr < payload.amount_xmr:
            raise HTTPException(status_code=400, detail="Insufficient real balance")
        bal.real_xmr -= float(payload.amount_xmr)
    else:
        if bal.fake_xmr < payload.amount_xmr:
            raise HTTPException(status_code=400, detail="Insufficient fake balance")
        bal.fake_xmr -= float(payload.amount_xmr)
    session.add(bal)
    session.commit()
    session.refresh(bal)
    return _to_balance_out(bal)


@app.post("/transactions/transfer", response_model=TransferOut)
def create_transfer(payload: TransferCreate = Body(...), session: Session = Depends(get_session)):
    if payload.amount_xmr <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")
    # Ensure balances exist
    from_bal = _ensure_balance(session, payload.from_user_id)
    to_bal = _ensure_balance(session, payload.to_user_id)
    # Validate
    if from_bal.fake_xmr < payload.amount_xmr:
        raise HTTPException(status_code=400, detail="Insufficient fake balance")
    # Apply transfer instantly (local ledger transfer)
    from_bal.fake_xmr -= payload.amount_xmr
    to_bal.fake_xmr += payload.amount_xmr
    session.add(from_bal)
    session.add(to_bal)
    # Record ledger
    tx = LedgerTx(from_user_id=payload.from_user_id, to_user_id=payload.to_user_id, amount_xmr=payload.amount_xmr, status="completed")
    session.add(tx)
    session.commit()
    session.refresh(tx)
    # Return
    return TransferOut(id=tx.id, from_user_id=tx.from_user_id, to_user_id=tx.to_user_id, amount_xmr=tx.amount_xmr, status=tx.status, created_at=tx.created_at)

# New trading endpoint: enqueue trade to RabbitMQ only (no immediate balance mutation)
@app.post("/transactions/trade", response_model=TradeQueued)
def create_trade(payload: TradeCreate = Body(...)):
    if payload.amount_xmr <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")
    message = {
        "type": "trade",
        "seller_id": payload.seller_id,
        "buyer_id": payload.buyer_id,
        "amount_xmr": float(payload.amount_xmr),
        "offer_id": payload.offer_id,
        "requested_at": datetime.utcnow().isoformat() + "Z",
    }
    _publish_queue(message, _RABBIT_TRADE_QUEUE)
    return TradeQueued(
        seller_id=payload.seller_id,
        buyer_id=payload.buyer_id,
        amount_xmr=float(payload.amount_xmr),
        offer_id=payload.offer_id,
        queued=True,
        enqueued_at=datetime.utcnow(),
        queue=_RABBIT_TRADE_QUEUE,
    )



@app.get("/balance/{user_id}/refresh", response_model=BalanceOut)
def refresh_balance(user_id: int, session: Session = Depends(get_session)):
    """Force refresh of real_xmr from Monero and persist it.
    If Monero is unreachable, returns existing stored value without changes.
    """
    bal = _ensure_balance(session, user_id)
    real = _fetch_real_xmr(user_id)
    if real is not None:
        if abs((bal.real_xmr or 0.0) - real) > 1e-12:
            bal.real_xmr = real
            session.add(bal)
            session.commit()
            session.refresh(bal)
        logger.info(json.dumps({"event": "balance_refresh", "user_id": user_id, "real_xmr": real}))
    else:
        logger.info(json.dumps({"event": "balance_refresh_no_update", "user_id": user_id}))
    return _to_balance_out(bal)


# --- Withdrawal endpoint ---
@app.post("/withdraw/{user_id}", response_model=WithdrawResponse)
def withdraw(user_id: int, payload: WithdrawRequest = Body(...), session: Session = Depends(get_session)):
    # Validate amount
    if payload.amount_xmr is None or payload.amount_xmr <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")

    # Ensure balance row exists and refresh real_xmr
    bal = _ensure_balance(session, user_id)
    real = _fetch_real_xmr(user_id)
    if real is not None and abs((bal.real_xmr or 0.0) - real) > 1e-12:
        bal.real_xmr = real
        session.add(bal)
        session.commit()
        session.refresh(bal)

    total_available = float(bal.fake_xmr or 0.0) + float(bal.real_xmr or 0.0)
    amt = float(payload.amount_xmr)
    if amt - total_available > 1e-12:
        # Not enough combined funds
        raise HTTPException(status_code=400, detail="Insufficient total balance (fake + real)")

    # Prepare Monero transfer call
    base = _MONERO_BASE.rstrip("/")
    transfer_payload = {"to_address": payload.to_address, "amount_xmr": amt}

    # Try to provide a specific from_address (user subaddress) with sufficient funds
    from_addr = None
    chosen_unlocked = 0.0
    try:
        with httpx.Client(timeout=20.0) as client:
            ar = client.get(f"{base}/addresses", params={"user_id": user_id})
            if ar.status_code == 200:
                addr_rows = ar.json() or []
                # Inspect balances: prefer one that covers amount; otherwise take the highest unlocked
                cover_addr = None
                cover_unlocked = 0.0
                best_addr = None
                best_unlocked = 0.0
                for row in addr_rows:
                    addr = row.get("address")
                    if not addr:
                        continue
                    rb = client.get(f"{base}/balance/{addr}")
                    if rb.status_code != 200:
                        logger.info(json.dumps({"event": "withdraw_balance_fetch_failed", "user_id": user_id, "address": addr, "status": rb.status_code}))
                        continue
                    data = rb.json() or {}
                    try:
                        unlocked = float(data.get("unlocked_balance_xmr", 0.0))
                    except Exception:
                        unlocked = 0.0
                    # Track best overall
                    if unlocked > best_unlocked:
                        best_unlocked = unlocked
                        best_addr = addr
                    # Track best that covers the amount
                    if unlocked >= amt and unlocked > cover_unlocked:
                        cover_unlocked = unlocked
                        cover_addr = addr
                from_addr = cover_addr or best_addr
                chosen_unlocked = cover_unlocked if cover_addr else best_unlocked
                logger.info(json.dumps({"event": "withdraw_source_selected", "user_id": user_id, "from_address": from_addr, "unlocked_xmr": chosen_unlocked}))
    except Exception as e:
        logger.info(json.dumps({"event": "withdraw_addresses_fetch_error", "user_id": user_id, "error": str(e)}))

    if from_addr:
        transfer_payload["from_address"] = from_addr

    # Enqueue withdrawal request to RabbitMQ
    message = {
        "type": "withdraw",
        "user_id": user_id,
        "to_address": payload.to_address,
        "amount_xmr": amt,
        "from_address": from_addr,
        "requested_at": datetime.utcnow().isoformat() + "Z",
    }
    _publish_withdraw(message)
    try:
        logger.info(json.dumps({
            "event": "withdraw_enqueued",
            "user_id": user_id,
            "to": payload.to_address,
            "amount_xmr": amt,
            "from_address": from_addr
        }))
    except Exception:
        pass

    # Return queued status without immediate on-chain execution
    return WithdrawResponse(to_address=payload.to_address, amount_xmr=amt, tx_hash=None, monero_result=None)
