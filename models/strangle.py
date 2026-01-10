"""
Strangle position model.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum


class PositionStatus(Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"


@dataclass
class Strangle:
    """Represents a strangle position (short CE + short PE)."""
    id: str
    call_strike: float
    put_strike: float
    expiry: str  # Format: YYYY-MM-DD
    quantity: int  # Number of lots

    # Entry details
    entry_call_premium: float
    entry_put_premium: float
    entry_time: datetime
    entry_spot: float = 0.0

    # Exit details
    exit_call_premium: Optional[float] = None
    exit_put_premium: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_reason: Optional[str] = None

    # Status
    status: PositionStatus = PositionStatus.OPEN

    # Capital allocation
    capital_part: int = 0  # Which part (1-6) of capital is used

    @property
    def max_profit(self) -> float:
        """Maximum profit = total premium collected."""
        return (self.entry_call_premium + self.entry_put_premium) * self.quantity * 25  # lot size

    @property
    def entry_premium(self) -> float:
        """Total premium collected at entry per lot."""
        return self.entry_call_premium + self.entry_put_premium

    @property
    def days_to_expiry(self) -> int:
        """Calculate days to expiry from now."""
        expiry_date = datetime.strptime(self.expiry, "%Y-%m-%d")
        return (expiry_date - datetime.now()).days

    def calculate_pnl(self, current_call_premium: float, current_put_premium: float) -> float:
        """
        Calculate current P&L.
        For short positions: profit = entry_premium - current_premium
        """
        entry_total = self.entry_call_premium + self.entry_put_premium
        current_total = current_call_premium + current_put_premium
        pnl_per_lot = (entry_total - current_total) * 25  # lot size
        return pnl_per_lot * self.quantity

    def calculate_pnl_percentage(self, current_call_premium: float, current_put_premium: float) -> float:
        """Calculate P&L as percentage of max profit."""
        pnl = self.calculate_pnl(current_call_premium, current_put_premium)
        if self.max_profit == 0:
            return 0.0
        return pnl / self.max_profit

    @property
    def realized_pnl(self) -> Optional[float]:
        """Calculate realized P&L for closed positions."""
        if self.status != PositionStatus.CLOSED:
            return None
        if self.exit_call_premium is None or self.exit_put_premium is None:
            return None
        return self.calculate_pnl(self.exit_call_premium, self.exit_put_premium)

    def close(self, call_premium: float, put_premium: float, reason: str):
        """Mark position as closed."""
        self.exit_call_premium = call_premium
        self.exit_put_premium = put_premium
        self.exit_time = datetime.now()
        self.exit_reason = reason
        self.status = PositionStatus.CLOSED
