"""
Abstract base class for broker implementations.
"""
from abc import ABC, abstractmethod
from typing import List, Optional, Dict
from models.order import Order, OrderStatus
from models.strangle import Strangle


class BaseBroker(ABC):
    """
    Abstract base class for broker implementations.

    Provides interface for:
    - Paper trading (PaperBroker)
    - Live trading via Kite Connect (KiteBroker)
    """

    @abstractmethod
    def connect(self) -> bool:
        """
        Establish connection to broker.

        Returns:
            True if connection successful
        """
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected to broker."""
        pass

    @abstractmethod
    def get_quote(
        self,
        symbol: str,
        strike: float,
        expiry: str,
        option_type: str
    ) -> Dict:
        """
        Get current quote for option.

        Returns:
            Dict with ltp, bid, ask, volume, oi
        """
        pass

    @abstractmethod
    def place_order(self, order: Order) -> str:
        """
        Place order and return order ID.

        Args:
            order: Order object with details

        Returns:
            Order ID string
        """
        pass

    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderStatus:
        """Get status of placed order."""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel pending order."""
        pass

    @abstractmethod
    def get_positions(self) -> List[Dict]:
        """Get all open positions."""
        pass

    @abstractmethod
    def get_margin_available(self) -> float:
        """Get available margin for trading."""
        pass

    @abstractmethod
    def sell_strangle(
        self,
        call_strike: float,
        put_strike: float,
        expiry: str,
        quantity: int,
        spot_price: float
    ) -> Optional[Strangle]:
        """
        Sell a strangle (short CE + short PE).

        Args:
            call_strike: Call option strike
            put_strike: Put option strike
            expiry: Expiry date (YYYY-MM-DD)
            quantity: Number of lots
            spot_price: Current spot price

        Returns:
            Strangle object if successful, None otherwise
        """
        pass

    @abstractmethod
    def close_strangle(self, strangle: Strangle) -> bool:
        """
        Close an existing strangle position.

        Args:
            strangle: Strangle position to close

        Returns:
            True if closed successfully
        """
        pass

    @abstractmethod
    def get_strangle_pnl(self, strangle: Strangle) -> float:
        """
        Get current P&L for a strangle position.

        Args:
            strangle: Strangle position

        Returns:
            Current unrealized P&L
        """
        pass
