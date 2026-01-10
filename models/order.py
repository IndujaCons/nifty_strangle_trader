"""
Order model for tracking trades.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from enum import Enum


class OrderStatus(Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class TransactionType(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


@dataclass
class Order:
    """Represents a trading order."""
    symbol: str
    strike: float
    expiry: str
    option_type: str  # "CE" or "PE"
    transaction_type: TransactionType
    quantity: int  # Number of lots
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None

    # Order tracking
    order_id: Optional[str] = None
    status: OrderStatus = OrderStatus.PENDING
    fill_price: Optional[float] = None
    filled_quantity: int = 0
    placed_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None

    @property
    def trading_symbol(self) -> str:
        """Generate Kite trading symbol format."""
        expiry_date = datetime.strptime(self.expiry, "%Y-%m-%d")
        expiry_str = expiry_date.strftime("%y%m%d")
        return f"{self.symbol}{expiry_str}{int(self.strike)}{self.option_type}"

    @property
    def is_filled(self) -> bool:
        return self.status == OrderStatus.COMPLETE

    @property
    def is_pending(self) -> bool:
        return self.status in [OrderStatus.PENDING, OrderStatus.OPEN]

    def mark_filled(self, fill_price: float, order_id: str = None):
        """Mark order as filled."""
        self.fill_price = fill_price
        self.filled_quantity = self.quantity
        self.status = OrderStatus.COMPLETE
        self.filled_at = datetime.now()
        if order_id:
            self.order_id = order_id

    def mark_rejected(self, reason: str):
        """Mark order as rejected."""
        self.status = OrderStatus.REJECTED
        self.rejection_reason = reason
