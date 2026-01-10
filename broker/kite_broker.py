"""
Kite Connect broker for live trading with Zerodha.
"""
import uuid
from datetime import datetime
from typing import List, Optional, Dict
from loguru import logger

try:
    from kiteconnect import KiteConnect
except ImportError:
    logger.warning("kiteconnect not installed. Install with: pip install kiteconnect")
    KiteConnect = None

from broker.base_broker import BaseBroker
from data.nse_data_provider import NSEDataProvider
from models.order import Order, OrderStatus, TransactionType, OrderType
from models.strangle import Strangle, PositionStatus
from config.settings import KITE_CONFIG, NIFTY_CONFIG


class KiteBroker(BaseBroker):
    """
    Live trading broker using Kite Connect API.

    Uses Kite Connect for order execution and NSE data provider for market data
    (since Personal tier doesn't include live quotes via WebSocket).
    """

    def __init__(
        self,
        api_key: str = None,
        api_secret: str = None,
        access_token: str = None,
        data_provider: NSEDataProvider = None
    ):
        self.api_key = api_key or KITE_CONFIG["api_key"]
        self.api_secret = api_secret or KITE_CONFIG["api_secret"]
        self.access_token = access_token or KITE_CONFIG["access_token"]
        self.data_provider = data_provider or NSEDataProvider()

        self.kite: Optional[KiteConnect] = None
        self.positions: Dict[str, Strangle] = {}
        self.orders: Dict[str, Order] = {}
        self._connected = False
        self.lot_size = NIFTY_CONFIG["lot_size"]

        # Instrument cache
        self._instruments: Dict[str, Dict] = {}

    def connect(self) -> bool:
        """Initialize Kite Connect with access token."""
        if KiteConnect is None:
            logger.error("kiteconnect package not installed")
            return False

        try:
            self.kite = KiteConnect(api_key=self.api_key)

            if self.access_token:
                self.kite.set_access_token(self.access_token)
                # Verify connection
                profile = self.kite.profile()
                logger.info(f"Connected to Kite as: {profile.get('user_name', 'Unknown')}")
                self._connected = True
                self._load_instruments()
                return True
            else:
                # Return login URL for manual authentication
                login_url = self.kite.login_url()
                logger.info(f"Please login at: {login_url}")
                logger.info("After login, copy the request_token from the redirect URL")
                return False

        except Exception as e:
            logger.error(f"Failed to connect to Kite: {e}")
            return False

    def generate_session(self, request_token: str) -> Optional[str]:
        """
        Generate session from request token after login.

        Call this after manual login to get access_token.
        """
        try:
            data = self.kite.generate_session(request_token, api_secret=self.api_secret)
            self.access_token = data["access_token"]
            self.kite.set_access_token(self.access_token)
            self._connected = True
            logger.info(f"Session generated. Access token: {self.access_token[:20]}...")
            return self.access_token
        except Exception as e:
            logger.error(f"Failed to generate session: {e}")
            return None

    def _load_instruments(self):
        """Load NFO instruments for symbol lookup."""
        try:
            instruments = self.kite.instruments("NFO")
            for inst in instruments:
                if inst["name"] == "NIFTY":
                    key = f"{inst['strike']}_{inst['expiry']}_{inst['instrument_type']}"
                    self._instruments[key] = inst
            logger.info(f"Loaded {len(self._instruments)} Nifty option instruments")
        except Exception as e:
            logger.error(f"Failed to load instruments: {e}")

    def _get_trading_symbol(self, strike: float, expiry: str, option_type: str) -> Optional[str]:
        """Get Kite trading symbol for an option."""
        # Convert expiry format
        expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date()
        key = f"{int(strike)}_{expiry_date}_{option_type}"

        inst = self._instruments.get(key)
        if inst:
            return inst["tradingsymbol"]

        # Fallback: construct symbol manually
        # Format: NIFTY25JAN23000CE
        month_map = {
            1: "JAN", 2: "FEB", 3: "MAR", 4: "APR",
            5: "MAY", 6: "JUN", 7: "JUL", 8: "AUG",
            9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC"
        }
        year = str(expiry_date.year)[2:]
        month = month_map[expiry_date.month]
        return f"NIFTY{year}{month}{int(strike)}{option_type}"

    def is_connected(self) -> bool:
        return self._connected

    def get_quote(
        self,
        symbol: str,
        strike: float,
        expiry: str,
        option_type: str
    ) -> Dict:
        """
        Get market quote.

        Note: Personal tier doesn't include live quotes via API.
        Using NSE data provider instead.
        """
        return self.data_provider.get_option_by_strike(strike, expiry, option_type, symbol).__dict__ if self.data_provider.get_option_by_strike(strike, expiry, option_type, symbol) else {}

    def place_order(self, order: Order) -> str:
        """Place order via Kite Connect."""
        if not self._connected:
            order.mark_rejected("Not connected to Kite")
            return ""

        try:
            trading_symbol = self._get_trading_symbol(
                order.strike,
                order.expiry,
                order.option_type
            )

            if not trading_symbol:
                order.mark_rejected("Unable to find trading symbol")
                return ""

            kite_order_type = (
                self.kite.ORDER_TYPE_MARKET if order.order_type == OrderType.MARKET
                else self.kite.ORDER_TYPE_LIMIT
            )

            kite_transaction = (
                self.kite.TRANSACTION_TYPE_SELL if order.transaction_type == TransactionType.SELL
                else self.kite.TRANSACTION_TYPE_BUY
            )

            order_params = {
                "variety": self.kite.VARIETY_REGULAR,
                "exchange": self.kite.EXCHANGE_NFO,
                "tradingsymbol": trading_symbol,
                "transaction_type": kite_transaction,
                "quantity": order.quantity * self.lot_size,
                "product": self.kite.PRODUCT_NRML,
                "order_type": kite_order_type,
            }

            if order.order_type == OrderType.LIMIT and order.limit_price:
                order_params["price"] = order.limit_price

            order_id = self.kite.place_order(**order_params)
            order.order_id = str(order_id)
            order.placed_at = datetime.now()
            order.status = OrderStatus.OPEN

            self.orders[order.order_id] = order

            logger.info(f"Order placed: {order.order_id} - {kite_transaction} {trading_symbol}")
            return order.order_id

        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            order.mark_rejected(str(e))
            return ""

    def get_order_status(self, order_id: str) -> OrderStatus:
        """Get status of placed order from Kite."""
        if not self._connected:
            return OrderStatus.REJECTED

        try:
            order_history = self.kite.order_history(order_id)
            if order_history:
                latest = order_history[-1]
                status = latest.get("status", "").upper()

                if status == "COMPLETE":
                    # Update local order with fill price
                    if order_id in self.orders:
                        self.orders[order_id].mark_filled(
                            latest.get("average_price", 0),
                            order_id
                        )
                    return OrderStatus.COMPLETE
                elif status in ["OPEN", "PENDING"]:
                    return OrderStatus.OPEN
                elif status == "CANCELLED":
                    return OrderStatus.CANCELLED
                elif status == "REJECTED":
                    return OrderStatus.REJECTED

            return OrderStatus.PENDING

        except Exception as e:
            logger.error(f"Failed to get order status: {e}")
            return OrderStatus.PENDING

    def cancel_order(self, order_id: str) -> bool:
        """Cancel pending order."""
        if not self._connected:
            return False

        try:
            self.kite.cancel_order(
                variety=self.kite.VARIETY_REGULAR,
                order_id=order_id
            )
            if order_id in self.orders:
                self.orders[order_id].status = OrderStatus.CANCELLED
            logger.info(f"Order cancelled: {order_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order: {e}")
            return False

    def get_positions(self) -> List[Dict]:
        """Get all open positions from Kite."""
        if not self._connected:
            return []

        try:
            positions = self.kite.positions()
            return positions.get("net", [])
        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return []

    def get_margin_available(self) -> float:
        """Get available margin from Kite."""
        if not self._connected:
            return 0.0

        try:
            margins = self.kite.margins()
            equity = margins.get("equity", {})
            return equity.get("available", {}).get("live_balance", 0.0)
        except Exception as e:
            logger.error(f"Failed to get margins: {e}")
            return 0.0

    def sell_strangle(
        self,
        call_strike: float,
        put_strike: float,
        expiry: str,
        quantity: int,
        spot_price: float
    ) -> Optional[Strangle]:
        """Execute strangle sell via Kite."""
        if not self._connected:
            logger.error("Not connected to Kite")
            return None

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

        if not call_order_id:
            logger.error("Failed to place call leg order")
            return None

        # Wait for fill and get status
        import time
        for _ in range(10):  # Wait up to 10 seconds
            status = self.get_order_status(call_order_id)
            if status == OrderStatus.COMPLETE:
                break
            time.sleep(1)

        if self.orders.get(call_order_id, Order("", 0, "", "", TransactionType.SELL, 0)).status != OrderStatus.COMPLETE:
            logger.error("Call order not filled")
            self.cancel_order(call_order_id)
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

        if not put_order_id:
            logger.error("Failed to place put leg order")
            # Should close call leg here
            return None

        # Wait for fill
        for _ in range(10):
            status = self.get_order_status(put_order_id)
            if status == OrderStatus.COMPLETE:
                break
            time.sleep(1)

        if self.orders.get(put_order_id, Order("", 0, "", "", TransactionType.SELL, 0)).status != OrderStatus.COMPLETE:
            logger.error("Put order not filled")
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

        logger.info(
            f"Strangle sold via Kite: CE {call_strike} @ {strangle.entry_call_premium:.2f}, "
            f"PE {put_strike} @ {strangle.entry_put_premium:.2f}"
        )

        return strangle

    def close_strangle(self, strangle: Strangle) -> bool:
        """Close an existing strangle position via Kite."""
        if not self._connected:
            return False

        if strangle.status != PositionStatus.OPEN:
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

        # Wait for fills
        import time
        for _ in range(10):
            call_status = self.get_order_status(call_order_id) if call_order_id else OrderStatus.REJECTED
            put_status = self.get_order_status(put_order_id) if put_order_id else OrderStatus.REJECTED
            if call_status == OrderStatus.COMPLETE and put_status == OrderStatus.COMPLETE:
                break
            time.sleep(1)

        exit_call = self.orders.get(call_order_id)
        exit_put = self.orders.get(put_order_id)

        if exit_call and exit_put and exit_call.is_filled and exit_put.is_filled:
            strangle.close(
                exit_call.fill_price,
                exit_put.fill_price,
                "Manual close"
            )
            logger.info(f"Strangle closed via Kite: P&L = {strangle.realized_pnl:.2f}")
            return True

        return False

    def get_strangle_pnl(self, strangle: Strangle) -> float:
        """Get current P&L for a strangle position."""
        if strangle.status != PositionStatus.OPEN:
            return strangle.realized_pnl or 0.0

        # Use NSE data provider for quotes
        option = self.data_provider.get_option_by_strike(
            strangle.call_strike, strangle.expiry, "CE"
        )
        call_ltp = option.ltp if option else 0

        option = self.data_provider.get_option_by_strike(
            strangle.put_strike, strangle.expiry, "PE"
        )
        put_ltp = option.ltp if option else 0

        return strangle.calculate_pnl(call_ltp, put_ltp)

    def get_login_url(self) -> str:
        """Get Kite login URL for manual authentication."""
        if self.kite is None:
            self.kite = KiteConnect(api_key=self.api_key)
        return self.kite.login_url()
