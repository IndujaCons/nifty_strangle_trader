"""
Delta-based strike selection for strangles.

Uses synthetic futures price and strike-specific IV for accurate delta calculation.

Key methodology (matching StockMock/market makers):
1. Calculate Synthetic Futures = ATM Strike + ATM CE Price - ATM PE Price
2. Use strike-specific IV from option chain (not flat ATM IV)
3. Use r=0, q=0 in Black-Scholes (futures incorporate carry cost)
"""
from typing import Tuple, List, Optional, Dict
from loguru import logger

from greeks.black_scholes import BlackScholesCalculator
from config.settings import STRATEGY_CONFIG, NIFTY_CONFIG


def calculate_synthetic_futures(spot: float, atm_ce_price: float, atm_pe_price: float,
                                 atm_strike: float) -> float:
    """
    Calculate synthetic futures price from ATM options.

    Synth Fut = ATM Strike + ATM CE Price - ATM PE Price

    This is used as the underlying for delta calculations instead of spot.
    The synthetic futures price incorporates:
    - Cost of carry (interest rates)
    - Expected dividends
    - Market supply/demand imbalances

    Args:
        spot: Current spot price (for reference only)
        atm_ce_price: ATM Call premium
        atm_pe_price: ATM Put premium
        atm_strike: ATM strike price

    Returns:
        Synthetic futures price
    """
    synth_fut = atm_strike + atm_ce_price - atm_pe_price
    logger.debug(f"Synthetic Futures: {atm_strike} + {atm_ce_price} - {atm_pe_price} = {synth_fut:.2f}")
    return synth_fut


def get_atm_strike(spot: float, strike_interval: float = 50) -> float:
    """Get ATM strike for given spot price."""
    return round(spot / strike_interval) * strike_interval


class DeltaStrikeSelector:
    """
    Selects strikes for strangles based on target delta.

    For 6-8 delta strangles:
    - Call: OTM call with delta ~0.06-0.08
    - Put: OTM put with delta ~-0.06 to -0.08 (abs value 0.06-0.08)

    Uses futures-mode Black-Scholes (r=0, q=0) with synthetic futures
    as underlying for accurate delta calculation matching market makers.
    """

    def __init__(self, bs_calculator: BlackScholesCalculator = None, use_futures_mode: bool = True):
        """
        Initialize delta strike selector.

        Args:
            bs_calculator: Optional pre-configured BS calculator
            use_futures_mode: Use r=0, q=0 for futures-based delta (default True)
        """
        # Use futures mode by default for accurate delta calculation
        self.bs = bs_calculator or BlackScholesCalculator(use_futures_mode=use_futures_mode)
        self.strike_interval = NIFTY_CONFIG["strike_interval"]

    def _get_available_strikes(
        self,
        spot_price: float,
        num_strikes: int = 30
    ) -> List[float]:
        """
        Generate list of available strikes around spot price.

        Args:
            spot_price: Current spot price
            num_strikes: Number of strikes on each side of ATM

        Returns:
            Sorted list of strike prices
        """
        atm = self._round_to_strike(spot_price)
        strikes = []

        for i in range(-num_strikes, num_strikes + 1):
            strike = atm + (i * self.strike_interval)
            if strike > 0:
                strikes.append(strike)

        return sorted(strikes)

    def _round_to_strike(self, price: float) -> float:
        """Round price to nearest strike interval."""
        return round(price / self.strike_interval) * self.strike_interval

    def find_call_strike_for_delta(
        self,
        spot_price: float,
        target_delta: float,
        expiry_days: int,
        iv: float,
        available_strikes: List[float] = None
    ) -> Tuple[float, float]:
        """
        Find OTM call strike with delta closest to target.

        Args:
            spot_price: Current spot price
            target_delta: Target delta (e.g., 0.07 for 7 delta)
            expiry_days: Days to expiry
            iv: Implied volatility (decimal)
            available_strikes: Optional list of available strikes

        Returns:
            Tuple of (strike, actual_delta)
        """
        if available_strikes is None:
            available_strikes = self._get_available_strikes(spot_price)

        T = expiry_days / 365.0
        atm = self._round_to_strike(spot_price)

        # Only consider OTM calls (strike > spot)
        otm_strikes = [s for s in available_strikes if s > atm]

        best_strike = None
        best_delta = None
        min_diff = float('inf')

        for strike in otm_strikes:
            delta = self.bs.calculate_call_delta(spot_price, strike, T, iv)

            # Skip if delta is too high (closer to ATM)
            if delta > 0.20:
                continue

            diff = abs(delta - target_delta)
            if diff < min_diff:
                min_diff = diff
                best_strike = strike
                best_delta = delta

        if best_strike is None:
            # Fallback: use furthest OTM strike
            best_strike = max(otm_strikes) if otm_strikes else atm + self.strike_interval
            best_delta = self.bs.calculate_call_delta(spot_price, best_strike, T, iv)

        logger.debug(f"Call strike selected: {best_strike}, delta: {best_delta:.4f}")
        return best_strike, best_delta

    def find_put_strike_for_delta(
        self,
        spot_price: float,
        target_delta: float,
        expiry_days: int,
        iv: float,
        available_strikes: List[float] = None
    ) -> Tuple[float, float]:
        """
        Find OTM put strike with delta closest to target (absolute value).

        Args:
            spot_price: Current spot price
            target_delta: Target delta magnitude (e.g., 0.07 for -7 delta put)
            expiry_days: Days to expiry
            iv: Implied volatility (decimal)
            available_strikes: Optional list of available strikes

        Returns:
            Tuple of (strike, actual_delta)
        """
        if available_strikes is None:
            available_strikes = self._get_available_strikes(spot_price)

        T = expiry_days / 365.0
        atm = self._round_to_strike(spot_price)

        # Only consider OTM puts (strike < spot)
        otm_strikes = [s for s in available_strikes if s < atm]

        best_strike = None
        best_delta = None
        min_diff = float('inf')

        for strike in otm_strikes:
            delta = self.bs.calculate_put_delta(spot_price, strike, T, iv)
            abs_delta = abs(delta)

            # Skip if delta is too high (closer to ATM)
            if abs_delta > 0.20:
                continue

            diff = abs(abs_delta - target_delta)
            if diff < min_diff:
                min_diff = diff
                best_strike = strike
                best_delta = delta

        if best_strike is None:
            # Fallback: use furthest OTM strike
            best_strike = min(otm_strikes) if otm_strikes else atm - self.strike_interval
            best_delta = self.bs.calculate_put_delta(spot_price, best_strike, T, iv)

        logger.debug(f"Put strike selected: {best_strike}, delta: {best_delta:.4f}")
        return best_strike, best_delta

    def select_strangle_strikes(
        self,
        spot_price: float,
        expiry_days: int,
        iv: float,
        target_delta: float = None,
        available_strikes: List[float] = None,
        option_chain: dict = None,
        expiry: str = None
    ) -> Tuple[float, float, dict]:
        """
        Select both call and put strikes for a strangle.

        Args:
            spot_price: Current spot price
            expiry_days: Days to expiry
            iv: Implied volatility (decimal, e.g., 0.15 for 15%)
            target_delta: Target delta (default from config)
            available_strikes: Optional list of available strikes
            option_chain: Optional option chain data for actual premiums
            expiry: Expiry date string (required if option_chain provided)

        Returns:
            Tuple of (call_strike, put_strike, details_dict)
        """
        if target_delta is None:
            target_delta = STRATEGY_CONFIG["target_delta"]

        if available_strikes is None:
            available_strikes = self._get_available_strikes(spot_price)

        call_strike, call_delta = self.find_call_strike_for_delta(
            spot_price, target_delta, expiry_days, iv, available_strikes
        )

        put_strike, put_delta = self.find_put_strike_for_delta(
            spot_price, target_delta, expiry_days, iv, available_strikes
        )

        # Try to get actual premiums from option chain
        call_premium = 0.0
        put_premium = 0.0
        is_simulated = option_chain.get("simulated", False) if option_chain else False

        if option_chain and expiry:
            from utils.date_utils import format_expiry_for_nse
            nse_expiry = format_expiry_for_nse(expiry)
            options = option_chain.get("options", {}).get(nse_expiry, {})

            ce_option = options.get(call_strike, {}).get("CE")
            pe_option = options.get(put_strike, {}).get("PE")

            if ce_option and ce_option.ltp > 0:
                call_premium = ce_option.ltp
            if pe_option and pe_option.ltp > 0:
                put_premium = pe_option.ltp

        # Determine premium source
        if call_premium > 0 and put_premium > 0:
            premium_source = "simulated-bs" if is_simulated else "nse-live"
        else:
            # Fallback to Black-Scholes calculation
            T = expiry_days / 365.0
            if call_premium <= 0:
                call_premium = self.bs.calculate_call_price(spot_price, call_strike, T, iv)
            if put_premium <= 0:
                put_premium = self.bs.calculate_put_price(spot_price, put_strike, T, iv)
            premium_source = "black-scholes"

        total_premium = call_premium + put_premium

        details = {
            "spot_price": spot_price,
            "expiry_days": expiry_days,
            "iv": iv,
            "target_delta": target_delta,
            "call_strike": call_strike,
            "call_delta": call_delta,
            "call_premium": call_premium,
            "put_strike": put_strike,
            "put_delta": put_delta,
            "put_premium": put_premium,
            "total_premium": total_premium,
            "strangle_width": call_strike - put_strike,
            "premium_source": premium_source
        }

        logger.info(
            f"Strangle strikes: CE {call_strike} (delta={call_delta:.4f}), "
            f"PE {put_strike} (delta={put_delta:.4f}), "
            f"width={call_strike - put_strike}"
        )

        return call_strike, put_strike, details

    def is_delta_in_range(
        self,
        delta: float,
        delta_lower: float = None,
        delta_upper: float = None
    ) -> bool:
        """
        Check if delta is within acceptable range.

        Args:
            delta: Actual delta (can be negative for puts)
            delta_lower: Lower bound (default from config)
            delta_upper: Upper bound (default from config)

        Returns:
            True if delta is within range
        """
        if delta_lower is None:
            delta_lower = STRATEGY_CONFIG["target_delta_lower"]
        if delta_upper is None:
            delta_upper = STRATEGY_CONFIG["target_delta_upper"]

        abs_delta = abs(delta)
        return delta_lower <= abs_delta <= delta_upper

    def recalculate_greeks(
        self,
        spot_price: float,
        call_strike: float,
        put_strike: float,
        expiry_days: int,
        iv: float
    ) -> dict:
        """
        Recalculate Greeks for existing strangle position.

        Useful for monitoring position risk.
        """
        T = expiry_days / 365.0

        call_greeks = self.bs.calculate_all_greeks(spot_price, call_strike, T, iv, "CE")
        put_greeks = self.bs.calculate_all_greeks(spot_price, put_strike, T, iv, "PE")

        return {
            "call": call_greeks,
            "put": put_greeks,
            "net_delta": call_greeks["delta"] + put_greeks["delta"],
            "net_gamma": call_greeks["gamma"] + put_greeks["gamma"],
            "net_theta": call_greeks["theta"] + put_greeks["theta"],
            "net_vega": call_greeks["vega"] + put_greeks["vega"]
        }

    def find_strikes_from_option_chain(
        self,
        option_chain: Dict,
        expiry: str,
        expiry_days: int,
        target_delta: float = None
    ) -> Tuple[float, float, Dict]:
        """
        Find strangle strikes using option chain data with strike-specific IVs.

        This method mimics how StockMock/market makers calculate delta:
        1. Calculate synthetic futures from ATM options
        2. Use strike-specific IV from option chain
        3. Use r=0 in Black-Scholes (futures mode)

        Args:
            option_chain: Option chain dict with format:
                {
                    "spot_price": float,
                    "options": {
                        expiry: {
                            strike: {"CE": Option, "PE": Option},
                            ...
                        }
                    }
                }
            expiry: Expiry date string (e.g., "20-Jan-2026")
            expiry_days: Days to expiry
            target_delta: Target delta (default from config)

        Returns:
            Tuple of (call_strike, put_strike, details_dict)
        """
        if target_delta is None:
            target_delta = STRATEGY_CONFIG["target_delta"]

        spot = option_chain.get("spot_price", 0)
        if spot <= 0:
            logger.error("Invalid spot price in option chain")
            return 0, 0, {}

        # Get options for this expiry
        from utils.date_utils import format_expiry_for_nse
        nse_expiry = format_expiry_for_nse(expiry)
        options = option_chain.get("options", {}).get(nse_expiry, {})

        if not options:
            # Try with original expiry format
            options = option_chain.get("options", {}).get(expiry, {})

        if not options:
            logger.error(f"No options found for expiry: {expiry}")
            return 0, 0, {}

        # Step 1: Calculate synthetic futures from ATM options
        atm_strike = get_atm_strike(spot, self.strike_interval)
        atm_options = options.get(atm_strike, {})
        atm_ce = atm_options.get("CE")
        atm_pe = atm_options.get("PE")

        if not atm_ce or not atm_pe:
            logger.warning(f"ATM options not found at strike {atm_strike}, using spot as underlying")
            synthetic_futures = spot
        else:
            atm_ce_price = atm_ce.ltp if hasattr(atm_ce, 'ltp') else atm_ce.get('ltp', 0)
            atm_pe_price = atm_pe.ltp if hasattr(atm_pe, 'ltp') else atm_pe.get('ltp', 0)
            synthetic_futures = calculate_synthetic_futures(spot, atm_ce_price, atm_pe_price, atm_strike)

        logger.info(f"Spot: {spot}, ATM: {atm_strike}, Synthetic Futures: {synthetic_futures:.2f}")

        T = expiry_days / 365.0

        # Step 2: Find call strike with target delta using strike-specific IV
        best_call_strike = None
        best_call_delta = None
        best_call_iv = None
        min_call_diff = float('inf')

        # Step 3: Find put strike with target delta using strike-specific IV
        best_put_strike = None
        best_put_delta = None
        best_put_iv = None
        min_put_diff = float('inf')

        for strike, strike_options in options.items():
            # Process calls (OTM calls: strike > synthetic futures)
            ce = strike_options.get("CE")
            if ce and strike > synthetic_futures:
                ce_iv = ce.iv if hasattr(ce, 'iv') else ce.get('iv', 0)
                if ce_iv > 0:
                    # Use strike-specific IV with synthetic futures as underlying
                    delta = self.bs.calculate_call_delta(synthetic_futures, strike, T, ce_iv)

                    # Skip very high deltas (too close to ATM)
                    if delta > 0.20:
                        continue

                    diff = abs(delta - target_delta)
                    if diff < min_call_diff:
                        min_call_diff = diff
                        best_call_strike = strike
                        best_call_delta = delta
                        best_call_iv = ce_iv

            # Process puts (OTM puts: strike < synthetic futures)
            pe = strike_options.get("PE")
            if pe and strike < synthetic_futures:
                pe_iv = pe.iv if hasattr(pe, 'iv') else pe.get('iv', 0)
                if pe_iv > 0:
                    # Use strike-specific IV with synthetic futures as underlying
                    delta = self.bs.calculate_put_delta(synthetic_futures, strike, T, pe_iv)
                    abs_delta = abs(delta)

                    # Skip very high deltas (too close to ATM)
                    if abs_delta > 0.20:
                        continue

                    diff = abs(abs_delta - target_delta)
                    if diff < min_put_diff:
                        min_put_diff = diff
                        best_put_strike = strike
                        best_put_delta = delta
                        best_put_iv = pe_iv

        if best_call_strike is None or best_put_strike is None:
            logger.error("Could not find suitable strikes for target delta")
            return 0, 0, {}

        # Get premiums from option chain
        call_option = options.get(best_call_strike, {}).get("CE")
        put_option = options.get(best_put_strike, {}).get("PE")

        call_premium = call_option.ltp if hasattr(call_option, 'ltp') else call_option.get('ltp', 0) if call_option else 0
        put_premium = put_option.ltp if hasattr(put_option, 'ltp') else put_option.get('ltp', 0) if put_option else 0

        details = {
            "spot_price": spot,
            "synthetic_futures": synthetic_futures,
            "atm_strike": atm_strike,
            "expiry_days": expiry_days,
            "target_delta": target_delta,
            "call_strike": best_call_strike,
            "call_delta": best_call_delta,
            "call_iv": best_call_iv,
            "call_premium": call_premium,
            "put_strike": best_put_strike,
            "put_delta": best_put_delta,
            "put_iv": best_put_iv,
            "put_premium": put_premium,
            "total_premium": call_premium + put_premium,
            "strangle_width": best_call_strike - best_put_strike,
            "premium_source": "option-chain"
        }

        logger.info(
            f"Strangle (using synth fut {synthetic_futures:.2f}): "
            f"CE {best_call_strike} (Δ={best_call_delta:.4f}, IV={best_call_iv:.2%}), "
            f"PE {best_put_strike} (Δ={best_put_delta:.4f}, IV={best_put_iv:.2%})"
        )

        return best_call_strike, best_put_strike, details

    def calculate_delta_for_strike(
        self,
        synthetic_futures: float,
        strike: float,
        expiry_days: int,
        iv: float,
        option_type: str
    ) -> float:
        """
        Calculate delta for a specific strike using synthetic futures.

        Args:
            synthetic_futures: Synthetic futures price (ATM Strike + CE - PE)
            strike: Strike price
            expiry_days: Days to expiry
            iv: Strike-specific implied volatility (decimal)
            option_type: "CE" or "PE"

        Returns:
            Delta value
        """
        T = expiry_days / 365.0
        if option_type == "CE":
            return self.bs.calculate_call_delta(synthetic_futures, strike, T, iv)
        else:
            return self.bs.calculate_put_delta(synthetic_futures, strike, T, iv)
