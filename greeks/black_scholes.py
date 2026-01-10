"""
Black-Scholes model implementation for options Greeks calculation.

Includes implied volatility calculation using Newton-Raphson method.
"""
import math
from typing import Tuple, Optional
from scipy.stats import norm
from scipy.optimize import brentq
from loguru import logger

from config.settings import GREEKS_CONFIG


class BlackScholesCalculator:
    """
    Black-Scholes model for calculating option Greeks.

    Used for delta-based strike selection for strangles.

    For accurate delta calculation matching market makers:
    - Use synthetic futures as underlying (not spot)
    - Use strike-specific IV from option chain
    - Set r=0, q=0 when using futures (carry cost built into futures price)
    """

    def __init__(
        self,
        risk_free_rate: float = None,
        dividend_yield: float = None,
        use_futures_mode: bool = False
    ):
        """
        Initialize Black-Scholes calculator.

        Args:
            risk_free_rate: Risk-free rate (default from config)
            dividend_yield: Dividend yield (default from config)
            use_futures_mode: If True, sets r=0, q=0 for futures-based calculation
        """
        if use_futures_mode:
            self.r = 0.0
            self.q = 0.0
        else:
            self.r = risk_free_rate if risk_free_rate is not None else GREEKS_CONFIG["risk_free_rate"]
            self.q = dividend_yield if dividend_yield is not None else GREEKS_CONFIG["dividend_yield"]

    def _calculate_d1_d2(
        self,
        S: float,
        K: float,
        T: float,
        sigma: float
    ) -> Tuple[float, float]:
        """
        Calculate d1 and d2 components of Black-Scholes.

        Args:
            S: Spot price
            K: Strike price
            T: Time to expiry (in years)
            sigma: Volatility (decimal, e.g., 0.15 for 15%)

        Returns:
            Tuple of (d1, d2)
        """
        if T <= 0 or sigma <= 0:
            return 0.0, 0.0

        try:
            d1 = (math.log(S / K) + (self.r - self.q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
            d2 = d1 - sigma * math.sqrt(T)
            return d1, d2
        except (ValueError, ZeroDivisionError) as e:
            logger.error(f"Error calculating d1/d2: {e}")
            return 0.0, 0.0

    def calculate_call_delta(
        self,
        S: float,
        K: float,
        T: float,
        sigma: float
    ) -> float:
        """
        Calculate delta for call option.

        Args:
            S: Spot price
            K: Strike price
            T: Time to expiry (in years)
            sigma: Volatility (decimal)

        Returns:
            Call delta (0 to 1)
        """
        d1, _ = self._calculate_d1_d2(S, K, T, sigma)
        if d1 == 0:
            return 0.0
        return math.exp(-self.q * T) * norm.cdf(d1)

    def calculate_put_delta(
        self,
        S: float,
        K: float,
        T: float,
        sigma: float
    ) -> float:
        """
        Calculate delta for put option.

        Args:
            S: Spot price
            K: Strike price
            T: Time to expiry (in years)
            sigma: Volatility (decimal)

        Returns:
            Put delta (-1 to 0)
        """
        d1, _ = self._calculate_d1_d2(S, K, T, sigma)
        if d1 == 0:
            return 0.0
        return math.exp(-self.q * T) * (norm.cdf(d1) - 1)

    def calculate_call_price(
        self,
        S: float,
        K: float,
        T: float,
        sigma: float
    ) -> float:
        """Calculate theoretical call option price."""
        d1, d2 = self._calculate_d1_d2(S, K, T, sigma)
        if d1 == 0:
            return max(0, S - K)

        call_price = (
            S * math.exp(-self.q * T) * norm.cdf(d1) -
            K * math.exp(-self.r * T) * norm.cdf(d2)
        )
        return max(0, call_price)

    def calculate_put_price(
        self,
        S: float,
        K: float,
        T: float,
        sigma: float
    ) -> float:
        """Calculate theoretical put option price."""
        d1, d2 = self._calculate_d1_d2(S, K, T, sigma)
        if d1 == 0:
            return max(0, K - S)

        put_price = (
            K * math.exp(-self.r * T) * norm.cdf(-d2) -
            S * math.exp(-self.q * T) * norm.cdf(-d1)
        )
        return max(0, put_price)

    def calculate_gamma(
        self,
        S: float,
        K: float,
        T: float,
        sigma: float
    ) -> float:
        """Calculate gamma (same for call and put)."""
        d1, _ = self._calculate_d1_d2(S, K, T, sigma)
        if d1 == 0 or T <= 0 or sigma <= 0:
            return 0.0

        gamma = (
            math.exp(-self.q * T) * norm.pdf(d1) /
            (S * sigma * math.sqrt(T))
        )
        return gamma

    def calculate_theta_call(
        self,
        S: float,
        K: float,
        T: float,
        sigma: float
    ) -> float:
        """Calculate theta for call option (per day)."""
        d1, d2 = self._calculate_d1_d2(S, K, T, sigma)
        if d1 == 0 or T <= 0:
            return 0.0

        term1 = -(S * sigma * math.exp(-self.q * T) * norm.pdf(d1)) / (2 * math.sqrt(T))
        term2 = self.q * S * math.exp(-self.q * T) * norm.cdf(d1)
        term3 = self.r * K * math.exp(-self.r * T) * norm.cdf(d2)

        theta = (term1 + term2 - term3) / 365  # Per day
        return theta

    def calculate_theta_put(
        self,
        S: float,
        K: float,
        T: float,
        sigma: float
    ) -> float:
        """Calculate theta for put option (per day)."""
        d1, d2 = self._calculate_d1_d2(S, K, T, sigma)
        if d1 == 0 or T <= 0:
            return 0.0

        term1 = -(S * sigma * math.exp(-self.q * T) * norm.pdf(d1)) / (2 * math.sqrt(T))
        term2 = -self.q * S * math.exp(-self.q * T) * norm.cdf(-d1)
        term3 = self.r * K * math.exp(-self.r * T) * norm.cdf(-d2)

        theta = (term1 + term2 + term3) / 365  # Per day
        return theta

    def calculate_vega(
        self,
        S: float,
        K: float,
        T: float,
        sigma: float
    ) -> float:
        """Calculate vega (same for call and put, per 1% IV change)."""
        d1, _ = self._calculate_d1_d2(S, K, T, sigma)
        if d1 == 0 or T <= 0:
            return 0.0

        vega = S * math.exp(-self.q * T) * math.sqrt(T) * norm.pdf(d1) / 100
        return vega

    def calculate_all_greeks(
        self,
        S: float,
        K: float,
        T: float,
        sigma: float,
        option_type: str
    ) -> dict:
        """
        Calculate all Greeks for an option.

        Args:
            S: Spot price
            K: Strike price
            T: Time to expiry (in years)
            sigma: Volatility (decimal)
            option_type: "CE" for call, "PE" for put

        Returns:
            Dictionary with all Greeks
        """
        is_call = option_type == "CE"

        return {
            "delta": self.calculate_call_delta(S, K, T, sigma) if is_call else self.calculate_put_delta(S, K, T, sigma),
            "gamma": self.calculate_gamma(S, K, T, sigma),
            "theta": self.calculate_theta_call(S, K, T, sigma) if is_call else self.calculate_theta_put(S, K, T, sigma),
            "vega": self.calculate_vega(S, K, T, sigma),
            "price": self.calculate_call_price(S, K, T, sigma) if is_call else self.calculate_put_price(S, K, T, sigma)
        }

    def calculate_implied_volatility(
        self,
        S: float,
        K: float,
        T: float,
        market_price: float,
        option_type: str,
        precision: float = 0.0001,
        max_iterations: int = 100
    ) -> Optional[float]:
        """
        Calculate implied volatility from market price using Brent's method.

        This is the inverse of the pricing function - given an option's market price,
        find the volatility that would produce that price.

        Args:
            S: Underlying price (use synthetic futures for accurate delta)
            K: Strike price
            T: Time to expiry (in years)
            market_price: Current market price of the option
            option_type: "CE" for call, "PE" for put
            precision: Desired precision for IV (default 0.01%)
            max_iterations: Maximum iterations (default 100)

        Returns:
            Implied volatility as decimal (e.g., 0.15 for 15%), or None if failed
        """
        if market_price <= 0 or T <= 0:
            return None

        is_call = option_type == "CE"

        # Calculate intrinsic value
        if is_call:
            intrinsic = max(0, S - K)
        else:
            intrinsic = max(0, K - S)

        # If market price is less than intrinsic, IV calculation won't work
        if market_price < intrinsic:
            logger.warning(f"Market price {market_price} < intrinsic {intrinsic}")
            return None

        # Define the objective function (difference between model and market price)
        def objective(sigma):
            if is_call:
                model_price = self.calculate_call_price(S, K, T, sigma)
            else:
                model_price = self.calculate_put_price(S, K, T, sigma)
            return model_price - market_price

        try:
            # Use Brent's method to find IV between 0.01% and 500%
            iv = brentq(objective, 0.0001, 5.0, xtol=precision, maxiter=max_iterations)
            return iv
        except ValueError as e:
            # Brent's method failed (likely no solution in range)
            logger.debug(f"IV calculation failed for {option_type} {K}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in IV calculation: {e}")
            return None

    def calculate_iv_newton_raphson(
        self,
        S: float,
        K: float,
        T: float,
        market_price: float,
        option_type: str,
        initial_guess: float = 0.20,
        precision: float = 0.0001,
        max_iterations: int = 100
    ) -> Optional[float]:
        """
        Calculate implied volatility using Newton-Raphson method.

        Faster than Brent's method when vega is well-behaved.

        Args:
            S: Underlying price
            K: Strike price
            T: Time to expiry (in years)
            market_price: Market price of option
            option_type: "CE" or "PE"
            initial_guess: Starting IV estimate (default 20%)
            precision: Convergence threshold
            max_iterations: Maximum iterations

        Returns:
            Implied volatility as decimal, or None if failed
        """
        if market_price <= 0 or T <= 0:
            return None

        is_call = option_type == "CE"
        sigma = initial_guess

        for i in range(max_iterations):
            # Calculate model price and vega
            if is_call:
                price = self.calculate_call_price(S, K, T, sigma)
            else:
                price = self.calculate_put_price(S, K, T, sigma)

            vega = self.calculate_vega(S, K, T, sigma) * 100  # Vega per 1 point, not per 1%

            if vega < 1e-10:
                # Vega too small, can't converge
                break

            # Newton-Raphson update
            price_diff = price - market_price
            sigma_new = sigma - price_diff / vega

            # Check convergence
            if abs(sigma_new - sigma) < precision:
                return max(0.0001, sigma_new)  # Ensure positive

            sigma = sigma_new

            # Bound sigma to reasonable range
            sigma = max(0.0001, min(5.0, sigma))

        # Fallback to Brent's method if Newton-Raphson fails
        return self.calculate_implied_volatility(S, K, T, market_price, option_type)


def calculate_iv_for_option_chain(
    option_chain: dict,
    underlying: float,
    expiry_days: int,
    use_futures_mode: bool = True
) -> dict:
    """
    Calculate IV for all options in chain that don't have IV.

    Args:
        option_chain: Option chain dict with options
        underlying: Underlying price (synthetic futures recommended)
        expiry_days: Days to expiry
        use_futures_mode: Use r=0 for futures-based calculation

    Returns:
        Updated option chain with calculated IVs
    """
    bs = BlackScholesCalculator(use_futures_mode=use_futures_mode)
    T = expiry_days / 365.0

    for expiry_key, strikes in option_chain.get("options", {}).items():
        for strike, options in strikes.items():
            for opt_type in ["CE", "PE"]:
                opt = options.get(opt_type)
                if opt is None:
                    continue

                # Get current IV and LTP
                current_iv = opt.iv if hasattr(opt, 'iv') else opt.get('iv', 0)
                ltp = opt.ltp if hasattr(opt, 'ltp') else opt.get('ltp', 0)

                # Calculate IV if not present or zero
                if current_iv <= 0 and ltp > 0:
                    calculated_iv = bs.calculate_implied_volatility(
                        underlying, strike, T, ltp, opt_type
                    )
                    if calculated_iv:
                        if hasattr(opt, 'iv'):
                            opt.iv = calculated_iv
                        else:
                            opt['iv'] = calculated_iv
                        logger.debug(f"Calculated IV for {opt_type} {strike}: {calculated_iv:.4f}")

    return option_chain
