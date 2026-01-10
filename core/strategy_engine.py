"""
Main strategy engine for the strangle selling automation.
"""
from datetime import datetime
from typing import Optional
from loguru import logger

from broker.base_broker import BaseBroker
from data.nse_data_provider import NSEDataProvider
from data.vwap_calculator import StradleVWAPCalculator
from greeks.delta_calculator import DeltaStrikeSelector
from core.capital_manager import CapitalManager
from core.position_manager import PositionManager
from config.settings import STRATEGY_CONFIG, NIFTY_CONFIG
from utils.date_utils import get_expiry_for_dte, is_market_open


class StrangleStrategy:
    """
    Main strategy engine for bi-weekly strangle selling.

    Entry Rules:
    - 14 DTE options
    - Enter when straddle > VWAP
    - 6-8 delta strangles
    - Max 2 entries per day
    - Capital divided into 6 parts

    Exit Rules:
    - Exit at 50% of max profit
    - Optional exit at 7 DTE (not mandatory)
    - No stop loss
    """

    def __init__(
        self,
        broker: BaseBroker,
        data_provider: NSEDataProvider = None,
        capital_manager: CapitalManager = None,
        position_manager: PositionManager = None
    ):
        self.broker = broker
        self.data_provider = data_provider or NSEDataProvider()
        self.capital_manager = capital_manager or CapitalManager()
        self.position_manager = position_manager or PositionManager(broker)

        signal_duration = STRATEGY_CONFIG.get("signal_duration_seconds", 120)
        self.vwap_calculator = StradleVWAPCalculator(signal_duration_seconds=signal_duration)
        self.delta_selector = DeltaStrikeSelector()

        # Strategy parameters
        self.target_dte = STRATEGY_CONFIG["entry_dte"]
        self.target_delta = STRATEGY_CONFIG["target_delta"]
        self.symbol = NIFTY_CONFIG["symbol"]

        # State
        self._last_tick_time: Optional[datetime] = None
        self._is_running = False

    def on_market_open(self):
        """Called at market open - reset daily counters."""
        logger.info("Market open - resetting daily counters")
        self.vwap_calculator.reset_for_new_day()
        self.capital_manager.reset_daily_counter()
        self._is_running = True

    def on_market_close(self):
        """Called at market close."""
        logger.info("Market close")
        self._is_running = False

        # Log daily summary
        summary = self.position_manager.get_portfolio_summary()
        logger.info(f"Daily Summary: {summary}")

    def run_strategy_tick(self):
        """
        Main strategy loop - called at regular intervals.

        1. Update VWAP with current straddle price
        2. Check exit conditions for existing positions
        3. Check entry conditions for new positions
        """
        if not is_market_open():
            logger.debug("Market is closed, skipping tick")
            return

        self._last_tick_time = datetime.now()

        try:
            # 1. Get market data
            spot_price = self.data_provider.get_spot_price(self.symbol)
            if spot_price <= 0:
                logger.warning("Invalid spot price, skipping tick")
                return

            # Get target expiry (14 DTE)
            target_expiry = get_expiry_for_dte(self.target_dte)
            if not target_expiry:
                logger.warning("No suitable expiry found for target DTE")
                return

            # Get ATM straddle price
            straddle_price, _, atm_strike = self.data_provider.get_atm_straddle_price(
                target_expiry, self.symbol
            )
            straddle_volume = self.data_provider.get_straddle_volume(
                target_expiry, self.symbol
            )

            # 2. Update VWAP
            self.vwap_calculator.add_price_point(straddle_price, straddle_volume)

            # Log current state
            vwap = self.vwap_calculator.get_vwap()
            logger.debug(
                f"Tick: Spot={spot_price:.2f}, ATM={atm_strike}, "
                f"Straddle={straddle_price:.2f}, VWAP={vwap:.2f}"
            )

            # 3. Check exits for existing positions
            self._check_and_execute_exits()

            # 4. Check entry conditions
            if self._should_enter(straddle_price):
                self._execute_entry(spot_price, target_expiry)

        except Exception as e:
            logger.error(f"Error in strategy tick: {e}")

    def _should_enter(self, current_straddle_price: float) -> bool:
        """Check if entry conditions are met."""
        # Check capital availability
        if not self.capital_manager.can_enter():
            logger.debug("Cannot enter: capital or daily limit")
            return False

        # Check VWAP signal
        if not self.vwap_calculator.is_entry_signal(current_straddle_price):
            logger.debug("No entry signal: straddle <= VWAP")
            return False

        logger.info(
            f"Entry signal triggered: Straddle {current_straddle_price:.2f} > "
            f"VWAP {self.vwap_calculator.get_vwap():.2f}"
        )
        return True

    def _execute_entry(self, spot_price: float, expiry: str):
        """Execute strangle entry."""
        logger.info(f"Executing strangle entry at spot {spot_price:.2f}")

        try:
            # Get IV from data provider (using ATM IV as proxy)
            iv = self.data_provider.get_india_vix()
            if iv <= 0:
                iv = 0.15  # Default 15%
            logger.debug(f"Using IV: {iv:.2%}")

            # Calculate DTE
            from utils.date_utils import calculate_dte
            dte = calculate_dte(expiry)

            # Select strikes based on delta
            call_strike, put_strike, details = self.delta_selector.select_strangle_strikes(
                spot_price=spot_price,
                expiry_days=dte,
                iv=iv,
                target_delta=self.target_delta
            )

            logger.info(
                f"Selected strikes: CE {call_strike} (delta={details['call_delta']:.4f}), "
                f"PE {put_strike} (delta={details['put_delta']:.4f})"
            )

            # Get position size
            quantity = self.capital_manager.get_position_size()

            # Execute via broker
            strangle = self.broker.sell_strangle(
                call_strike=call_strike,
                put_strike=put_strike,
                expiry=expiry,
                quantity=quantity,
                spot_price=spot_price
            )

            if strangle:
                # Allocate capital
                part = self.capital_manager.allocate_capital(strangle.id)
                strangle.capital_part = part

                # Add to position manager
                self.position_manager.add_position(strangle)

                logger.info(
                    f"Strangle entered successfully: {strangle.id}, "
                    f"Premium collected: {strangle.entry_premium:.2f}, "
                    f"Max profit: {strangle.max_profit:.2f}"
                )
            else:
                logger.error("Failed to execute strangle entry")

        except Exception as e:
            logger.error(f"Error executing entry: {e}")

    def _check_and_execute_exits(self):
        """Check and execute exits for positions meeting exit criteria."""
        positions_to_exit = self.position_manager.get_positions_to_exit()

        for strangle, reason in positions_to_exit:
            logger.info(f"Exiting position {strangle.id}: {reason}")

            success = self.position_manager.close_position(strangle, reason)

            if success:
                # Release capital
                self.capital_manager.release_capital(strangle.id)
                logger.info(
                    f"Position {strangle.id} closed. "
                    f"Realized P&L: {strangle.realized_pnl:.2f}"
                )

    def force_exit_position(self, strangle_id: str, reason: str = "Manual exit") -> bool:
        """Manually exit a position."""
        strangle = self.position_manager.get_position(strangle_id)
        if not strangle:
            logger.error(f"Position {strangle_id} not found")
            return False

        success = self.position_manager.close_position(strangle, reason)
        if success:
            self.capital_manager.release_capital(strangle_id)
        return success

    def get_status(self) -> dict:
        """Get current strategy status."""
        return {
            "is_running": self._is_running,
            "last_tick": self._last_tick_time.isoformat() if self._last_tick_time else None,
            "vwap_stats": self.vwap_calculator.get_statistics(),
            "capital_status": self.capital_manager.get_status(),
            "portfolio": self.position_manager.get_portfolio_summary()
        }

    def get_vwap_info(self) -> dict:
        """Get current VWAP information."""
        return self.vwap_calculator.get_statistics()
