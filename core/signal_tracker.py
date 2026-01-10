"""
Signal tracker for monitoring entry conditions.

Tracks:
- Straddle > VWAP duration (5 minutes required)
- Trading window state (morning/afternoon)
- Trades executed per window
"""
from datetime import datetime, time
from typing import Optional
from dataclasses import dataclass
from loguru import logger

from config.settings import TRADING_WINDOWS, STRATEGY_CONFIG


@dataclass
class TradingWindowState:
    """Track trades per window."""
    morning_trades: int = 0
    afternoon_trades: int = 0
    last_trade_time: Optional[datetime] = None

    def reset_for_new_day(self):
        """Reset counters for a new trading day."""
        self.morning_trades = 0
        self.afternoon_trades = 0
        self.last_trade_time = None


@dataclass
class SignalState:
    """Track signal duration."""
    signal_start: Optional[datetime] = None
    is_active: bool = False

    def reset(self):
        """Reset signal state."""
        self.signal_start = None
        self.is_active = False


class SignalTracker:
    """
    Tracks entry signal conditions.

    Entry requires:
    1. Straddle price > VWAP for 5 consecutive minutes
    2. Within valid trading window
    3. Not exceeded max trades for current window
    """

    def __init__(self):
        self.signal_state = SignalState()
        self.window_state = TradingWindowState()
        self._last_check_date: Optional[datetime] = None

    def _parse_time(self, time_str: str) -> time:
        """Parse time string to time object."""
        return datetime.strptime(time_str, "%H:%M").time()

    def _get_current_window(self, now: datetime) -> Optional[str]:
        """
        Determine which trading window we're in.

        Returns:
            'morning', 'afternoon', or None if outside windows
        """
        current_time = now.time()

        # Check if before trading starts (first 15 mins)
        no_trade_end = self._parse_time("09:30")  # 9:15 + 15 mins
        if current_time < no_trade_end:
            return None

        # Morning window: 9:30 - 13:15
        morning_start = self._parse_time(TRADING_WINDOWS["morning_window"]["start"])
        morning_end = self._parse_time(TRADING_WINDOWS["morning_window"]["end"])
        if morning_start <= current_time < morning_end:
            return "morning"

        # Afternoon window: 13:15 - 15:15
        afternoon_start = self._parse_time(TRADING_WINDOWS["afternoon_window"]["start"])
        afternoon_end = self._parse_time(TRADING_WINDOWS["afternoon_window"]["end"])
        if afternoon_start <= current_time < afternoon_end:
            return "afternoon"

        return None

    def _can_trade_in_window(self, window: str) -> bool:
        """Check if we can still trade in the given window."""
        if window == "morning":
            max_trades = TRADING_WINDOWS["morning_window"]["max_trades"]
            return self.window_state.morning_trades < max_trades
        elif window == "afternoon":
            max_trades = TRADING_WINDOWS["afternoon_window"]["max_trades"]
            return self.window_state.afternoon_trades < max_trades
        return False

    def update_signal(self, straddle_price: float, vwap: float) -> dict:
        """
        Update signal state based on current prices.

        Args:
            straddle_price: Current ATM straddle price
            vwap: Current rolling VWAP

        Returns:
            dict with signal status info
        """
        now = datetime.now()

        # Reset for new day
        if self._last_check_date is None or self._last_check_date.date() != now.date():
            self.window_state.reset_for_new_day()
            self.signal_state.reset()
            self._last_check_date = now

        # Check trading window
        current_window = self._get_current_window(now)
        can_trade = current_window is not None and self._can_trade_in_window(current_window)

        # Check signal condition
        signal_met = straddle_price > vwap

        if signal_met:
            if not self.signal_state.is_active:
                # Signal just started
                self.signal_state.signal_start = now
                self.signal_state.is_active = True
                logger.info(f"Signal started: Straddle {straddle_price:.2f} > VWAP {vwap:.2f}")
        else:
            # Signal broken, reset
            if self.signal_state.is_active:
                logger.info(f"Signal broken: Straddle {straddle_price:.2f} <= VWAP {vwap:.2f}")
            self.signal_state.reset()

        # Calculate duration
        duration_seconds = 0
        if self.signal_state.is_active and self.signal_state.signal_start:
            duration_seconds = (now - self.signal_state.signal_start).total_seconds()

        # Check if entry conditions fully met
        required_duration = STRATEGY_CONFIG["signal_duration_seconds"]
        entry_ready = (
            self.signal_state.is_active and
            duration_seconds >= required_duration and
            can_trade
        )

        return {
            "signal_active": self.signal_state.is_active,
            "duration_seconds": duration_seconds,
            "required_seconds": required_duration,
            "current_window": current_window,
            "can_trade": can_trade,
            "entry_ready": entry_ready,
            "morning_trades": self.window_state.morning_trades,
            "afternoon_trades": self.window_state.afternoon_trades,
            "straddle_price": straddle_price,
            "vwap": vwap,
        }

    def record_trade(self, window: str):
        """Record that a trade was executed in the given window."""
        if window == "morning":
            self.window_state.morning_trades += 1
        elif window == "afternoon":
            self.window_state.afternoon_trades += 1
        self.window_state.last_trade_time = datetime.now()
        self.signal_state.reset()
        logger.info(f"Trade recorded in {window} window")

    def get_next_window_time(self) -> Optional[str]:
        """Get the start time of the next available trading window."""
        now = datetime.now()
        current_time = now.time()

        morning_start = self._parse_time(TRADING_WINDOWS["morning_window"]["start"])
        afternoon_start = self._parse_time(TRADING_WINDOWS["afternoon_window"]["start"])

        # If before morning window
        if current_time < morning_start:
            return TRADING_WINDOWS["morning_window"]["start"]

        # If in morning window but max trades reached, return afternoon
        if self._get_current_window(now) == "morning" and not self._can_trade_in_window("morning"):
            return TRADING_WINDOWS["afternoon_window"]["start"]

        # If before afternoon window
        if current_time < afternoon_start:
            return TRADING_WINDOWS["afternoon_window"]["start"]

        return None

    def format_duration(self, seconds: float) -> str:
        """Format duration as Xm Ys."""
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}m {secs}s"
