from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class BalanceOut(BaseModel):
    user_id: int
    fake_xmr: float
    real_xmr: float
    updated_at: datetime

class BalanceSetRequest(BaseModel):
    fake_xmr: Optional[float] = None
    real_xmr: Optional[float] = None

class BalanceAdjustRequest(BaseModel):
    amount_xmr: float
    kind: Optional[str] = "fake"  # "fake" or "real"

class TransferCreate(BaseModel):
    from_user_id: int
    to_user_id: int
    amount_xmr: float

class TransferOut(BaseModel):
    id: int
    from_user_id: int
    to_user_id: int
    amount_xmr: float
    status: str
    created_at: datetime

# Trade (off-chain ledger) schemas for queued processing
class TradeCreate(BaseModel):
    seller_id: int
    buyer_id: int
    amount_xmr: float
    offer_id: Optional[str] = None  # optional context id from Offers service

class TradeQueued(BaseModel):
    seller_id: int
    buyer_id: int
    amount_xmr: float
    offer_id: Optional[str] = None
    queued: bool = True
    enqueued_at: datetime
    queue: str

# Withdraw (on-chain) schemas
class WithdrawRequest(BaseModel):
    to_address: str
    amount_xmr: float

class WithdrawResponse(BaseModel):
    to_address: str
    amount_xmr: float
    tx_hash: Optional[str] = None
    monero_result: Optional[dict] = None
