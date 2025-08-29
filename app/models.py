from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field

from sqlalchemy.sql import func
class UserBalance(SQLModel, table=True):
    __tablename__ = "userbalance"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    fake_xmr: float = 0.0
    real_xmr: float = 0.0
    updated_at: datetime = Field(default_factory=datetime.utcnow, sa_column_kwargs={"server_default": func.current_timestamp(), "onupdate": func.current_timestamp()}, index=True)

class LedgerTx(SQLModel, table=True):
    __tablename__ = "ledgertx"
    id: Optional[int] = Field(default=None, primary_key=True)
    from_user_id: int = Field(index=True)
    to_user_id: int = Field(index=True)
    amount_xmr: float
    status: str = Field(default="completed", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, sa_column_kwargs={"server_default": func.current_timestamp()}, index=True)
