from fastapi import FastAPI, Depends, HTTPException, Body
from sqlmodel import Session, select
from typing import Optional
import os, sys
from .database import get_session

# Centralized schemas import from CreateDB with repo-root guard for local runs
_current_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.abspath(os.path.join(_current_dir, '..', '..'))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
from CreateDB.schemas import BalanceOut, BalanceSetRequest, BalanceAdjustRequest, TransferCreate, TransferOut
from CreateDB.models import UserBalance, LedgerTx

app = FastAPI(title="Pupero Transactions Service")


def _ensure_balance(session: Session, user_id: int) -> UserBalance:
    stmt = select(UserBalance).where(UserBalance.user_id == user_id)
    bal = session.exec(stmt).first()
    if not bal:
        bal = UserBalance(user_id=user_id, fake_xmr=0.0, real_xmr=0.0)
        session.add(bal)
        session.commit()
        session.refresh(bal)
    return bal


def _to_balance_out(b: UserBalance) -> BalanceOut:
    return BalanceOut(user_id=b.user_id, fake_xmr=b.fake_xmr, real_xmr=b.real_xmr, updated_at=b.updated_at)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/balance/{user_id}", response_model=BalanceOut)
def get_balance(user_id: int, session: Session = Depends(get_session)):
    bal = _ensure_balance(session, user_id)
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
    # Apply transfer
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
