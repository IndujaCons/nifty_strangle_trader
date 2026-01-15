"""
Kite Connect data provider.

Provides:
- Live option quotes
- Historical data for VWAP calculation
- Rolling ATM straddle VWAP
- Delta-based strike selection
"""
from typing import Dict, List, Optional, Tuple
from datetime import datetime, date, timedelta
from dataclasses import dataclass
import os

from kiteconnect import KiteConnect
from loguru import logger
from dotenv import load_dotenv

from greeks.black_scholes import BlackScholesCalculator
from greeks.delta_calculator import calculate_synthetic_futures, get_atm_strike
from config.settings import NIFTY_CONFIG, PAPER_TRADING, LOT_QUANTITY


@dataclass
class StrangleData:
    """Container for strangle analysis data."""
    spot: float
    synthetic_futures: float
    atm_strike: float
    expiry: date
    dte: int

    # Straddle
    straddle_price: float
    straddle_vwap: float

    # Call leg
    call_strike: float
    call_ltp: float
    call_iv: float
    call_delta: float
    call_oi: int

    # Put leg
    put_strike: float
    put_ltp: float
    put_iv: float
    put_delta: float
    put_oi: int

    @property
    def entry_signal(self) -> bool:
        """Check if entry conditions are met."""
        return self.straddle_price > self.straddle_vwap

    @property
    def total_premium(self) -> float:
        return self.call_ltp + self.put_ltp

    @property
    def width(self) -> float:
        return self.call_strike - self.put_strike

    @property
    def per_lot(self) -> float:
        return self.total_premium * NIFTY_CONFIG["lot_size"]


class KiteDataProvider:
    """
    Unified data provider using Kite Connect.

    Requires Connect subscription (Rs.500/month) for:
    - Live quotes
    - Historical data
    - WebSocket streaming
    """

    NIFTY_TOKEN = 256265  # NIFTY 50 index token

    def __init__(self):
        load_dotenv()
        self.api_key = os.getenv("KITE_API_KEY")
        self.api_secret = os.getenv("KITE_API_SECRET")
        self.access_token = os.getenv("KITE_ACCESS_TOKEN")

        self.kite = KiteConnect(api_key=self.api_key)
        if self.access_token:
            self.kite.set_access_token(self.access_token)

        self._instruments_cache: Dict = {}
        self._instruments_date: Optional[date] = None

        self.bs = BlackScholesCalculator(use_futures_mode=True)

    def connect(self) -> bool:
        """Verify connection to Kite."""
        try:
            profile = self.kite.profile()
            logger.info(f"Connected to Kite as: {profile['user_name']}")
            return True
        except Exception as e:
            logger.error(f"Kite connection failed: {e}")
            return False

    def get_login_url(self) -> str:
        """Get Kite login URL for authentication."""
        return f"https://kite.zerodha.com/connect/login?api_key={self.api_key}&v=3"

    def generate_session(self, request_token: str) -> str:
        """Generate access token from request token."""
        data = self.kite.generate_session(request_token, api_secret=self.api_secret)
        self.access_token = data["access_token"]
        self.kite.set_access_token(self.access_token)
        return self.access_token

    def get_nifty_options(self, expiry: date = None) -> Dict:
        """
        Get NIFTY options instruments.

        Returns dict: {(strike, type): instrument}
        """
        today = date.today()

        if self._instruments_date != today:
            instruments = self.kite.instruments("NFO")
            self._instruments_cache = {}
            for inst in instruments:
                if inst['name'] == 'NIFTY' and inst['instrument_type'] in ['CE', 'PE']:
                    key = (inst['expiry'], inst['strike'], inst['instrument_type'])
                    self._instruments_cache[key] = inst
            self._instruments_date = today
            logger.info(f"Loaded {len(self._instruments_cache)} NIFTY option instruments")

        if expiry:
            return {k: v for k, v in self._instruments_cache.items() if k[0] == expiry}
        return self._instruments_cache

    def get_expiries(self) -> List[date]:
        """Get available expiry dates."""
        options = self.get_nifty_options()
        expiries = sorted(set(k[0] for k in options.keys()))
        return expiries

    def get_target_expiry(self, target_dte: int = 14) -> Optional[date]:
        """
        Find appropriate expiry for trading.

        Logic: Use nearest expiry if DTE <= 14, otherwise use next expiry.
        This ensures we don't jump to next expiry too early (e.g., at 15 DTE).
        """
        today = date.today()
        expiries = self.get_expiries()

        if not expiries:
            return None

        # Get nearest expiry
        nearest = expiries[0]
        nearest_dte = (nearest - today).days

        # Use nearest expiry if it's <= 14 DTE
        if nearest_dte <= target_dte:
            return nearest

        # Otherwise use next expiry if available (for fresh entries)
        return expiries[1] if len(expiries) > 1 else nearest

    def get_available_expiries(self, count: int = 2, min_dte: int = 3, position_expiries: List[date] = None) -> List[dict]:
        """
        Get the nearest N expiries with their DTE for dropdown selection.

        Args:
            count: Number of expiries to return
            min_dte: Minimum DTE to include (skip very near-term expiries)
            position_expiries: List of expiries that have open positions (always include these)

        Returns: List of {expiry: date, dte: int, label: str}
        """
        today = date.today()
        expiries = self.get_expiries()
        position_expiries = position_expiries or []

        result = []
        for exp in expiries:
            dte = (exp - today).days
            # Include if: has open positions OR (DTE >= min_dte AND haven't reached count yet)
            has_positions = exp in position_expiries
            if has_positions or (dte >= min_dte and len(result) < count):
                result.append({
                    'expiry': exp.isoformat(),
                    'dte': dte,
                    'label': f"{exp.strftime('%d-%b-%Y')} ({dte} DTE)"
                })
            # Stop if we have enough expiries (but always include position expiries)
            if len(result) >= count and not any(pe not in [date.fromisoformat(r['expiry']) for r in result] for pe in position_expiries):
                break

        return result[:count + len(position_expiries)]  # Allow extra for position expiries

    def get_spot_price(self) -> float:
        """Get current NIFTY spot price."""
        quote = self.kite.quote(["NSE:NIFTY 50"])
        return quote["NSE:NIFTY 50"]["last_price"]

    def get_option_quotes(self, expiry: date, strikes: List[float]) -> Dict:
        """
        Get quotes for multiple strikes.

        Returns: {strike: {CE: quote, PE: quote}}
        """
        options = self.get_nifty_options(expiry)
        symbols = []
        token_map = {}

        for strike in strikes:
            for opt_type in ['CE', 'PE']:
                inst = options.get((expiry, strike, opt_type))
                if inst:
                    symbols.append(f"NFO:{inst['tradingsymbol']}")
                    token_map[inst['instrument_token']] = (strike, opt_type)

        if not symbols:
            return {}

        quotes = self.kite.quote(symbols)

        result = {}
        for symbol, data in quotes.items():
            token = data['instrument_token']
            if token in token_map:
                strike, opt_type = token_map[token]
                if strike not in result:
                    result[strike] = {}
                result[strike][opt_type] = {
                    'ltp': data['last_price'],
                    'oi': data.get('oi', 0),
                    'volume': data.get('volume', 0),
                    'bid': data.get('depth', {}).get('buy', [{}])[0].get('price', 0),
                    'ask': data.get('depth', {}).get('sell', [{}])[0].get('price', 0),
                }

        return result

    def calculate_rolling_vwap(self, expiry: date) -> float:
        """
        Calculate rolling ATM straddle VWAP for today.

        Uses minute candles and adjusts ATM as spot moves.
        """
        today = date.today()
        from_time = datetime.combine(today, datetime.strptime("09:15", "%H:%M").time())
        to_time = datetime.now()

        # Get NIFTY spot candles
        nifty_candles = self.kite.historical_data(
            self.NIFTY_TOKEN, from_time, to_time, "minute"
        )

        if not nifty_candles:
            logger.warning("No NIFTY candles available for VWAP")
            return 0

        # Find all ATM strikes used during the day
        atm_strikes = set(round(c['close'] / 50) * 50 for c in nifty_candles)

        # Fetch historical data for all ATM strikes
        options = self.get_nifty_options(expiry)
        strike_data = {}

        for strike in atm_strikes:
            for opt_type in ['CE', 'PE']:
                inst = options.get((expiry, strike, opt_type))
                if inst:
                    try:
                        candles = self.kite.historical_data(
                            inst['instrument_token'], from_time, to_time, "minute"
                        )
                        strike_data[(strike, opt_type)] = {c['date']: c for c in candles}
                    except Exception as e:
                        logger.debug(f"Could not fetch {strike} {opt_type}: {e}")

        # Calculate rolling straddle VWAP (time-weighted for simplicity)
        sum_straddle = 0
        count = 0

        for candle in nifty_candles:
            timestamp = candle['date']
            atm = round(candle['close'] / 50) * 50

            ce_data = strike_data.get((atm, 'CE'), {}).get(timestamp)
            pe_data = strike_data.get((atm, 'PE'), {}).get(timestamp)

            if ce_data and pe_data:
                straddle = ce_data['close'] + pe_data['close']
                sum_straddle += straddle
                count += 1

        vwap = sum_straddle / count if count > 0 else 0
        logger.info(f"Rolling VWAP calculated from {count} data points: {vwap:.2f}")

        return vwap

    def find_strangle(
        self,
        expiry: date = None,
        target_delta: float = 0.07
    ) -> Optional[StrangleData]:
        """
        Find 7-delta strangle with all market data.

        Args:
            expiry: Target expiry (default: ~14 DTE)
            target_delta: Target delta for strikes (default: 0.07)

        Returns:
            StrangleData with complete analysis
        """
        # Get expiry
        if expiry is None:
            expiry = self.get_target_expiry()

        if not expiry:
            logger.error("No suitable expiry found")
            return None

        today = date.today()
        dte = (expiry - today).days

        # Get spot and ATM
        spot = self.get_spot_price()
        atm_strike = get_atm_strike(spot)

        # Get strikes around ATM - wider range for longer DTE
        # For 7 DTE: ~750 pts OTM, for 14 DTE: ~1500 pts OTM
        strike_range = max(20, dte * 2)  # More strikes for longer DTE
        strikes = [atm_strike + (i * 50) for i in range(-strike_range, strike_range + 1)]
        quotes = self.get_option_quotes(expiry, strikes)

        if atm_strike not in quotes:
            logger.error(f"ATM strike {atm_strike} not in quotes")
            return None

        # Calculate synthetic futures
        atm_ce = quotes[atm_strike].get('CE', {}).get('ltp', 0)
        atm_pe = quotes[atm_strike].get('PE', {}).get('ltp', 0)
        synth_fut = calculate_synthetic_futures(spot, atm_ce, atm_pe, atm_strike)

        # Calculate VWAP
        vwap = self.calculate_rolling_vwap(expiry)

        # Calculate IV and delta for all options
        T = dte / 365.0
        analyzed = []

        for strike, opts in quotes.items():
            for opt_type in ['CE', 'PE']:
                opt = opts.get(opt_type)
                if opt and opt['ltp'] > 0:
                    iv = self.bs.calculate_implied_volatility(
                        synth_fut, strike, T, opt['ltp'], opt_type
                    )
                    if iv:
                        if opt_type == 'CE':
                            delta = self.bs.calculate_call_delta(synth_fut, strike, T, iv)
                        else:
                            delta = self.bs.calculate_put_delta(synth_fut, strike, T, iv)

                        analyzed.append({
                            'strike': strike,
                            'type': opt_type,
                            'ltp': opt['ltp'],
                            'iv': iv,
                            'delta': delta,
                            'oi': opt['oi']
                        })

        # Find best call (OTM, delta closest to target)
        calls = [a for a in analyzed if a['type'] == 'CE' and a['strike'] > synth_fut and 0.03 < a['delta'] < 0.15]
        best_call = min(calls, key=lambda x: abs(x['delta'] - target_delta)) if calls else None

        # Find best put (OTM, |delta| closest to target)
        puts = [a for a in analyzed if a['type'] == 'PE' and a['strike'] < synth_fut and 0.03 < abs(a['delta']) < 0.15]
        best_put = min(puts, key=lambda x: abs(abs(x['delta']) - target_delta)) if puts else None

        if not best_call or not best_put:
            logger.error("Could not find suitable call/put strikes")
            return None

        return StrangleData(
            spot=spot,
            synthetic_futures=synth_fut,
            atm_strike=atm_strike,
            expiry=expiry,
            dte=dte,
            straddle_price=atm_ce + atm_pe,
            straddle_vwap=vwap,
            call_strike=best_call['strike'],
            call_ltp=best_call['ltp'],
            call_iv=best_call['iv'],
            call_delta=best_call['delta'],
            call_oi=best_call['oi'],
            put_strike=best_put['strike'],
            put_ltp=best_put['ltp'],
            put_iv=best_put['iv'],
            put_delta=best_put['delta'],
            put_oi=best_put['oi'],
        )

    def get_trading_symbol(self, expiry: date, strike: float, opt_type: str) -> Optional[str]:
        """Get trading symbol for an option."""
        options = self.get_nifty_options(expiry)
        inst = options.get((expiry, strike, opt_type))
        return inst['tradingsymbol'] if inst else None

    def place_strangle_order(
        self,
        expiry: date,
        call_strike: float,
        put_strike: float,
        quantity: int = None
    ) -> dict:
        """
        Place a strangle sell order (both legs).

        Args:
            expiry: Option expiry date
            call_strike: Call strike to sell
            put_strike: Put strike to sell
            quantity: Number of lots (default: LOT_QUANTITY from .env)

        Returns:
            dict with order details and status
        """
        lot_size = NIFTY_CONFIG["lot_size"]
        num_lots = quantity if quantity is not None else LOT_QUANTITY
        total_qty = lot_size * num_lots

        # Get trading symbols
        call_symbol = self.get_trading_symbol(expiry, call_strike, 'CE')
        put_symbol = self.get_trading_symbol(expiry, put_strike, 'PE')

        if not call_symbol or not put_symbol:
            return {
                "success": False,
                "error": "Could not find trading symbols",
                "call_symbol": call_symbol,
                "put_symbol": put_symbol,
            }

        result = {
            "success": False,
            "call_order": None,
            "put_order": None,
            "call_symbol": call_symbol,
            "put_symbol": put_symbol,
            "quantity": total_qty,
            "paper_trading": PAPER_TRADING,
        }

        if PAPER_TRADING:
            # Simulate order placement
            logger.info(f"[PAPER] Selling {call_symbol} x {total_qty}")
            logger.info(f"[PAPER] Selling {put_symbol} x {total_qty}")
            result["success"] = True
            result["call_order"] = {"order_id": "PAPER_CE_001", "status": "COMPLETE"}
            result["put_order"] = {"order_id": "PAPER_PE_001", "status": "COMPLETE"}
            return result

        try:
            # Place CALL sell order
            call_order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=self.kite.EXCHANGE_NFO,
                tradingsymbol=call_symbol,
                transaction_type=self.kite.TRANSACTION_TYPE_SELL,
                quantity=total_qty,
                product=self.kite.PRODUCT_NRML,
                order_type=self.kite.ORDER_TYPE_MARKET,
            )
            logger.info(f"Call order placed: {call_order_id}")
            result["call_order"] = {"order_id": call_order_id, "status": "PLACED"}

            # Place PUT sell order
            put_order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=self.kite.EXCHANGE_NFO,
                tradingsymbol=put_symbol,
                transaction_type=self.kite.TRANSACTION_TYPE_SELL,
                quantity=total_qty,
                product=self.kite.PRODUCT_NRML,
                order_type=self.kite.ORDER_TYPE_MARKET,
            )
            logger.info(f"Put order placed: {put_order_id}")
            result["put_order"] = {"order_id": put_order_id, "status": "PLACED"}

            result["success"] = True

        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            result["error"] = str(e)

        return result

    def get_order_status(self, order_id: str) -> dict:
        """Get status of a specific order."""
        try:
            orders = self.kite.orders()
            for order in orders:
                if order['order_id'] == order_id:
                    return {
                        "order_id": order_id,
                        "status": order['status'],
                        "filled_quantity": order.get('filled_quantity', 0),
                        "average_price": order.get('average_price', 0),
                        "tradingsymbol": order.get('tradingsymbol'),
                    }
            return {"order_id": order_id, "status": "NOT_FOUND"}
        except Exception as e:
            logger.error(f"Failed to get order status: {e}")
            return {"order_id": order_id, "status": "ERROR", "error": str(e)}

    def get_positions(self) -> dict:
        """
        Get current positions from Zerodha with real-time P&L.

        Returns:
            dict with net and day positions
        """
        try:
            positions = self.kite.positions()

            # Filter for NIFTY options only
            net_positions = positions.get('net', [])
            nifty_raw = [p for p in net_positions if p['tradingsymbol'].startswith('NIFTY') and p['quantity'] != 0]

            # Fetch live quotes for real-time P&L calculation
            live_quotes = {}
            if nifty_raw:
                symbols = [f"NFO:{p['tradingsymbol']}" for p in nifty_raw]
                try:
                    quotes = self.kite.quote(symbols)
                    for key, val in quotes.items():
                        symbol = key.replace("NFO:", "")
                        live_quotes[symbol] = val.get('last_price', 0)
                except Exception as e:
                    logger.warning(f"Failed to fetch live quotes: {e}")

            nifty_positions = []
            for pos in nifty_raw:
                symbol = pos['tradingsymbol']
                quantity = pos['quantity']
                avg_price = pos['average_price']
                # Use live quote if available, else fall back to position's last_price
                current_ltp = live_quotes.get(symbol, pos['last_price'])

                # Calculate real-time P&L
                if quantity < 0:  # Short position
                    calculated_pnl = (avg_price - current_ltp) * abs(quantity)
                else:  # Long position
                    calculated_pnl = (current_ltp - avg_price) * quantity

                # Calculate decay percentage for short positions
                # Decay = how much premium has eroded from entry price
                decay_pct = 0.0
                if quantity < 0 and avg_price > 0:  # Short position
                    decay_pct = (avg_price - current_ltp) / avg_price
                elif quantity > 0 and avg_price > 0:  # Long position (loss = decay)
                    decay_pct = (avg_price - current_ltp) / avg_price

                nifty_positions.append({
                    'symbol': symbol,
                    'quantity': quantity,
                    'average_price': avg_price,
                    'ltp': current_ltp,
                    'pnl': calculated_pnl,
                    'unrealised': calculated_pnl,
                    'realised': pos.get('realised', 0),
                    'decay_pct': round(decay_pct * 100, 1),  # As percentage
                })

            total_pnl = sum(p['pnl'] for p in nifty_positions)

            return {
                "success": True,
                "positions": nifty_positions,
                "total_pnl": total_pnl,
                "count": len(nifty_positions),
            }

        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return {"success": False, "error": str(e), "positions": []}

    def display_positions(self) -> str:
        """Get formatted position display string."""
        pos_data = self.get_positions()

        if not pos_data["success"]:
            return f"Error fetching positions: {pos_data.get('error')}"

        if not pos_data["positions"]:
            return "No open NIFTY positions"

        lines = [
            "",
            "=" * 70,
            "  CURRENT POSITIONS",
            "=" * 70,
            "",
            f"{'Symbol':<25} {'Qty':>8} {'Avg':>10} {'LTP':>10} {'P&L':>12}",
            "-" * 70,
        ]

        for pos in pos_data["positions"]:
            pnl_str = f"{pos['pnl']:>+12,.2f}"
            lines.append(
                f"{pos['symbol']:<25} {pos['quantity']:>8} "
                f"{pos['average_price']:>10.2f} {pos['ltp']:>10.2f} {pnl_str}"
            )

        lines.append("-" * 70)
        lines.append(f"{'Total P&L:':<55} {pos_data['total_pnl']:>+12,.2f}")
        lines.append("=" * 70)

        return "\n".join(lines)
