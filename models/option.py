"""
Option data model.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Option:
    """Represents a single option contract."""
    symbol: str
    strike: float
    expiry: str  # Format: YYYY-MM-DD
    option_type: str  # "CE" or "PE"
    ltp: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    oi: int = 0
    volume: int = 0
    iv: float = 0.0
    delta: float = 0.0
    theta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0

    @property
    def trading_symbol(self) -> str:
        """Generate Kite trading symbol format."""
        # Example: NIFTY2510916500CE
        expiry_date = datetime.strptime(self.expiry, "%Y-%m-%d")
        expiry_str = expiry_date.strftime("%y%m%d")
        return f"{self.symbol}{expiry_str}{int(self.strike)}{self.option_type}"

    @property
    def is_call(self) -> bool:
        return self.option_type == "CE"

    @property
    def is_put(self) -> bool:
        return self.option_type == "PE"


@dataclass
class OptionQuote:
    """Real-time quote for an option."""
    option: Option
    timestamp: datetime
    ltp: float
    bid: float
    ask: float
    volume: int
    oi: int
