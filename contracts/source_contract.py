"""Shared contracts for the local MERIDIAN research and paper system."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class DataTrust(StrEnum):
    AVAILABLE = "available"
    UNVERIFIED = "unverified"
    MISSING = "missing"


class OrderStatus(StrEnum):
    CREATED = "created"
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    FILLED = "filled"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Signal:
    signal_id: str
    timestamp: str
    symbol: str
    side: str
    probability: float
    no_trade_probability: float
    expected_net_bps: float
    risk_score: float
    stop_price: float
    target_price: float
    explanation: list[str] = field(default_factory=list)
    data_trust: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PaperOrder:
    order_id: str
    idempotency_key: str
    signal_id: str
    symbol: str
    side: str
    quantity: int
    requested_at: str
    stop_price: float
    target_price: float
    status: OrderStatus = OrderStatus.CREATED
    rejection_reason: str | None = None


@dataclass(frozen=True)
class Fill:
    order_id: str
    timestamp: str
    price: float
    quantity: int
    slippage_bps: float


@dataclass(frozen=True)
class AuditEvent:
    timestamp: str
    event_type: str
    payload: dict[str, Any]

    @classmethod
    def now(cls, event_type: str, payload: dict[str, Any]) -> "AuditEvent":
        return cls(datetime.now().astimezone().isoformat(), event_type, payload)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
