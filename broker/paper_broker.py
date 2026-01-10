"""
Paper trading broker for simulation using real NSE data.
"""
import uuid
from datetime import datetime
from typing import List, Optional, Dict
from loguru import logger

from broker.base_broker import BaseBroker
from data.nse_data_provider import NSEDataProvider
from models.order import Order, OrderStatus, TransactionType
from models.strangle import Strangle, PositionStatus
from config.settings import NIFTY_CONFIG, TOTAL_CAPITAL


class PaperBroker(BaseBroker):
    """
    Paper trading broker using real NSE data.

    Simulates order execution at current market prices.
    """

    def __init__(
        self,
        initial_capital: float = None,
        data_provider: NSEDataProvider = None
    ):
        self.capital = initial_capital or TOTAL_CAPITAL
        self.available_margin = self.capital
        self.data_provider = data_provider or NSEDataProvider()
        self.positions: Dict[str, Strangle] = {}
        self.orders: Dict[str, Order] = {}
        self.trade_history: List[Dict] = []
        self._connected = False
        self.lot_size = NIFTY_CONFIG["lot_size"]

    def connect(self) -> bool:
        """No connection needed for paper trading."""
        self._connected = True
        logger.info(f"Paper broker connected. Capital: {self.capital}")
        return True

    def is_connected(self) -> bool:
        return self._connected

    def get_quote(
        self,
        symbol: str,
        strike: float,
        expiry: str,
        option_type: str
    ) -> Dict:
        """Get real market quote from NSE."""
        option = self.data_provider.get_option_by_strike(
            strike, expiry, option_type, symbol
        )

        if option:
            return {
                "ltp": option.ltp,
                "bid": option.bid,
                "ask": option.ask,
                "volume": option.volume,
                "oi": option.oi,
                "iv": option.iv
            }
        return {
            "ltp": 0,
            "bid": 0,
            "ask": 0,
            "volume": 0,
            "oi": 0,
            "iv": 0
        }

    def place_order(self, order: Order) -> str:
        """Simulate order execution at current market price."""
        order_id = str(uuid.uuid4())[:8]

        # Get current market price
        quote = self.get_quote(
            order.symbol,
            order.strike,
            order.expiry,
            order.option_type
        )

        if quote.get("ltp", 0) <= 0:
            order.mark_rejected("No market data available")
            order.order_id = order_id
            self.orders[order_id] = order
            logger.warning(f"Order rejected: No market data for {order.trading_symbol}")
            return order_id

        # Simulate fill at current price (can add slippage later)
        fill_price = quote["ltp"]

        # Add small slippage for realism (0.05% adverse)
        if order.transaction_type == TransactionType.BUY:
            fill_price *= 1.0005  # Pay slightly more
        else:
            fill_price *= 0.9995  # Receive slightly less

        order.mark_filled(fill_price, order_id)
        order.placed_at = datetime.now()
        self.orders[order_id] = order

        # Update margin
        margin_impact = fill_price * order.quantity * self.lot_size
        if order.transaction_type == TransactionType.SELL:
            # Short options require margin
            self.available_margin -= margin_impact * 0.2  # ~20% margin requirement
        else:
            self.available_margin -= margin_impact

        logger.info(
            f"Order filled: {order.transaction_type.value} {order.quantity} lot "
            f"{order.trading_symbol} @ {fill_price:.2f}"
        )

        return order_id

    def get_order_status(self, order_id: str) -> OrderStatus:
        """Get status of placed order."""
        order = self.orders.get(order_id)
        return order.status if order else OrderStatus.REJECTED

    def cancel_order(self, order_id: str) -> bool:
        """Cancel pending order (all orders are instantly filled in paper trading)."""
        return False

    def get_positions(self) -> List[Dict]:
        """Get all open positions."""
        return [
            {
                "id": pos.id,
                "call_strike": pos.call_strike,
                "put_strike": pos.put_strike,
                "expiry": pos.expiry,
                "quantity": pos.quantity,
                "entry_premium": pos.entry_premium,
                "status": pos.status.value
            }
            for pos in self.positions.values()
            if pos.status == PositionStatus.OPEN
        ]

    def get_margin_available(self) -> float:
        """Get available margin for trading."""
        return self.available_margin

    def sell_strangle(
        self,
        call_strike: float,
        put_strike: float,
        expiry: str,
        quantity: int,
        spot_price: float
    ) -> Optional[Strangle]:
        """Execute strangle sell."""
        symbol = NIFTY_CONFIG["symbol"]

        # Place short call order
        call_order = Order(
            symbol=symbol,
            strike=call_strike,
            expiry=expiry,
            option_type="CE",
            transaction_type=TransactionType.SELL,
            quantity=quantity
        )
        call_order_id = self.place_order(call_order)

        if self.orders[call_order_id].status != OrderStatus.COMPLETE:
            logger.error("Failed to execute call leg of strangle")
            return None

        # Place short put order
        put_order = Order(
            symbol=symbol,
            strike=put_strike,
            expiry=expiry,
            option_type="PE",
            transaction_type=TransactionType.SELL,
            quantity=quantity
        )
        put_order_id = self.place_order(put_order)

        if self.orders[put_order_id].status != OrderStatus.COMPLETE:
            logger.error("Failed to execute put leg of strangle")
            # Should ideally close the call leg here
            return None

        # Create strangle position
        strangle = Strangle(
            id=str(uuid.uuid4())[:8],
            call_strike=call_strike,
            put_strike=put_strike,
            expiry=expiry,
            quantity=quantity,
            entry_call_premium=self.orders[call_order_id].fill_price,
            entry_put_premium=self.orders[put_order_id].fill_price,
            entry_time=datetime.now(),
            entry_spot=spot_price
        )

        self.positions[strangle.id] = strangle

        # Log trade
        self.trade_history.append({
            "timestamp": datetime.now(),
            "action": "ENTRY",
            "strangle_id": strangle.id,
            "call_strike": call_strike,
            "put_strike": put_strike,
            "expiry": expiry,
            "quantity": quantity,
            "call_premium": strangle.entry_call_premium,
            "put_premium": strangle.entry_put_premium,
            "total_premium": strangle.entry_premium,
            "spot_price": spot_price
        })

        logger.info(
            f"Strangle sold: CE {call_strike} @ {strangle.entry_call_premium:.2f}, "
            f"PE {put_strike} @ {strangle.entry_put_premium:.2f}, "
            f"Total premium: {strangle.entry_premium:.2f}"
        )

        return strangle

    def close_strangle(self, strangle: Strangle) -> bool:
        """Close an existing strangle position."""
        if strangle.status != PositionStatus.OPEN:
            logger.warning(f"Strangle {strangle.id} is not open")
            return False

        symbol = NIFTY_CONFIG["symbol"]

        # Buy back call
        call_order = Order(
            symbol=symbol,
            strike=strangle.call_strike,
            expiry=strangle.expiry,
            option_type="CE",
            transaction_type=TransactionType.BUY,
            quantity=strangle.quantity
        )
        call_order_id = self.place_order(call_order)

        # Buy back put
        put_order = Order(
            symbol=symbol,
            strike=strangle.put_strike,
            expiry=strangle.expiry,
            option_type="PE",
            transaction_type=TransactionType.BUY,
            quantity=strangle.quantity
        )
        put_order_id = self.place_order(put_order)

        exit_call_premium = self.orders[call_order_id].fill_price
        exit_put_premium = self.orders[put_order_id].fill_price

        # Calculate P&L
        pnl = strangle.calculate_pnl(exit_call_premium, exit_put_premium)

        # Close position
        strangle.close(exit_call_premium, exit_put_premium, "Manual close")

        # Update margin
        self.available_margin += pnl

        # Log trade
        self.trade_history.append({
            "timestamp": datetime.now(),
            "action": "EXIT",
            "strangle_id": strangle.id,
            "call_strike": strangle.call_strike,
            "put_strike": strangle.put_strike,
            "exit_call_premium": exit_call_premium,
            "exit_put_premium": exit_put_premium,
            "pnl": pnl,
            "exit_reason": strangle.exit_reason
        })

        logger.info(
            f"Strangle closed: CE @ {exit_call_premium:.2f}, "
            f"PE @ {exit_put_premium:.2f}, P&L: {pnl:.2f}"
        )

        return True

    def get_strangle_pnl(self, strangle: Strangle) -> float:
        """Get current P&L for a strangle position."""
        if strangle.status != PositionStatus.OPEN:
            return strangle.realized_pnl or 0.0

        # Get current prices
        call_quote = self.get_quote(
            NIFTY_CONFIG["symbol"],
            strangle.call_strike,
            strangle.expiry,
            "CE"
        )
        put_quote = self.get_quote(
            NIFTY_CONFIG["symbol"],
            strangle.put_strike,
            strangle.expiry,
            "PE"
        )

        return strangle.calculate_pnl(call_quote["ltp"], put_quote["ltp"])

    def get_strangle_pnl_pct(self, strangle: Strangle) -> float:
        """Get current P&L as percentage of max profit."""
        if strangle.status != PositionStatus.OPEN:
            return 0.0

        call_quote = self.get_quote(
            NIFTY_CONFIG["symbol"],
            strangle.call_strike,
            strangle.expiry,
            "CE"
        )
        put_quote = self.get_quote(
            NIFTY_CONFIG["symbol"],
            strangle.put_strike,
            strangle.expiry,
            "PE"
        )

        return strangle.calculate_pnl_percentage(call_quote["ltp"], put_quote["ltp"])

    def get_account_summary(self) -> Dict:
        """Get account summary."""
        total_pnl = sum(
            self.get_strangle_pnl(pos)
            for pos in self.positions.values()
            if pos.status == PositionStatus.OPEN
        )

        realized_pnl = sum(
            pos.realized_pnl or 0
            for pos in self.positions.values()
            if pos.status == PositionStatus.CLOSED
        )

        return {
            "initial_capital": self.capital,
            "available_margin": self.available_margin,
            "unrealized_pnl": total_pnl,
            "realized_pnl": realized_pnl,
            "total_pnl": total_pnl + realized_pnl,
            "open_positions": len([p for p in self.positions.values() if p.status == PositionStatus.OPEN]),
            "closed_positions": len([p for p in self.positions.values() if p.status == PositionStatus.CLOSED])
        }
