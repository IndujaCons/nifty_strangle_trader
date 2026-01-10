"""
Trading scheduler for market hours automation.
"""
from datetime import datetime, time
from typing import Callable, Optional
from loguru import logger
import pytz

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config.settings import MARKET_CONFIG


class TradingScheduler:
    """
    Scheduler for running strategy during market hours.

    Handles:
    - Market open/close events
    - Regular strategy ticks during trading hours
    - Graceful shutdown
    """

    def __init__(
        self,
        on_market_open: Callable = None,
        on_market_close: Callable = None,
        on_strategy_tick: Callable = None
    ):
        self.ist = pytz.timezone(MARKET_CONFIG["timezone"])
        self.scheduler = BackgroundScheduler(timezone=self.ist)

        self.on_market_open = on_market_open
        self.on_market_close = on_market_close
        self.on_strategy_tick = on_strategy_tick

        self._is_running = False

    def setup_schedule(self):
        """Setup trading schedule for IST market hours."""
        # Parse market hours
        open_hour, open_minute = map(int, MARKET_CONFIG["market_open"].split(":"))
        close_hour, close_minute = map(int, MARKET_CONFIG["market_close"].split(":"))
        interval_seconds = MARKET_CONFIG["strategy_interval_seconds"]

        # Market open handler - 9:15 AM IST, Mon-Fri
        if self.on_market_open:
            self.scheduler.add_job(
                self._safe_execute(self.on_market_open),
                CronTrigger(
                    day_of_week="mon-fri",
                    hour=open_hour,
                    minute=open_minute,
                    timezone=self.ist
                ),
                id="market_open",
                name="Market Open Handler",
                replace_existing=True
            )
            logger.info(f"Scheduled market open handler at {open_hour}:{open_minute:02d} IST")

        # Market close handler - 3:30 PM IST, Mon-Fri
        if self.on_market_close:
            self.scheduler.add_job(
                self._safe_execute(self.on_market_close),
                CronTrigger(
                    day_of_week="mon-fri",
                    hour=close_hour,
                    minute=close_minute,
                    timezone=self.ist
                ),
                id="market_close",
                name="Market Close Handler",
                replace_existing=True
            )
            logger.info(f"Scheduled market close handler at {close_hour}:{close_minute:02d} IST")

        # Strategy tick - every N seconds during market hours
        if self.on_strategy_tick:
            self.scheduler.add_job(
                self._safe_execute(self._run_tick_if_market_open),
                IntervalTrigger(seconds=interval_seconds),
                id="strategy_tick",
                name="Strategy Tick",
                replace_existing=True,
                misfire_grace_time=30
            )
            logger.info(f"Scheduled strategy tick every {interval_seconds} seconds")

    def _safe_execute(self, func: Callable) -> Callable:
        """Wrap function with error handling."""
        def wrapper():
            try:
                func()
            except Exception as e:
                logger.error(f"Error executing {func.__name__}: {e}")
        return wrapper

    def _run_tick_if_market_open(self):
        """Run strategy tick only if market is open."""
        if self._is_market_hours():
            self.on_strategy_tick()

    def _is_market_hours(self) -> bool:
        """Check if current time is within market hours."""
        now = datetime.now(self.ist)

        # Check if weekday (Monday = 0, Sunday = 6)
        if now.weekday() > 4:
            return False

        open_hour, open_minute = map(int, MARKET_CONFIG["market_open"].split(":"))
        close_hour, close_minute = map(int, MARKET_CONFIG["market_close"].split(":"))

        market_open = now.replace(hour=open_hour, minute=open_minute, second=0, microsecond=0)
        market_close = now.replace(hour=close_hour, minute=close_minute, second=0, microsecond=0)

        return market_open <= now <= market_close

    def start(self):
        """Start the scheduler."""
        if not self._is_running:
            self.scheduler.start()
            self._is_running = True
            logger.info("Trading scheduler started")

            # If market is currently open, trigger market open handler
            if self._is_market_hours() and self.on_market_open:
                logger.info("Market is open, triggering market open handler")
                self.on_market_open()

    def stop(self):
        """Stop the scheduler gracefully."""
        if self._is_running:
            self.scheduler.shutdown(wait=True)
            self._is_running = False
            logger.info("Trading scheduler stopped")

    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._is_running

    def get_next_run_times(self) -> dict:
        """Get next scheduled run times for all jobs."""
        jobs = {}
        for job in self.scheduler.get_jobs():
            next_run = job.next_run_time
            jobs[job.id] = next_run.isoformat() if next_run else None
        return jobs

    def trigger_manual_tick(self):
        """Manually trigger a strategy tick (for testing)."""
        if self.on_strategy_tick:
            logger.info("Manual strategy tick triggered")
            self.on_strategy_tick()


class SimpleScheduler:
    """
    Simple scheduler without APScheduler dependency.
    Uses a basic loop with sleep for testing.
    """

    def __init__(
        self,
        on_market_open: Callable = None,
        on_market_close: Callable = None,
        on_strategy_tick: Callable = None
    ):
        self.on_market_open = on_market_open
        self.on_market_close = on_market_close
        self.on_strategy_tick = on_strategy_tick
        self._is_running = False

    def run_once(self):
        """Run strategy tick once (for testing)."""
        if self.on_strategy_tick:
            self.on_strategy_tick()

    def run_loop(self, interval_seconds: int = 60):
        """Run continuous loop (blocking)."""
        import time
        from utils.date_utils import is_market_open

        self._is_running = True
        market_was_open = False

        logger.info(f"Starting simple scheduler loop (interval: {interval_seconds}s)")

        try:
            while self._is_running:
                market_open = is_market_open()

                # Trigger market open
                if market_open and not market_was_open:
                    if self.on_market_open:
                        self.on_market_open()

                # Trigger market close
                if not market_open and market_was_open:
                    if self.on_market_close:
                        self.on_market_close()

                # Run strategy tick during market hours
                if market_open and self.on_strategy_tick:
                    self.on_strategy_tick()

                market_was_open = market_open
                time.sleep(interval_seconds)

        except KeyboardInterrupt:
            logger.info("Scheduler interrupted by user")
        finally:
            self._is_running = False

    def stop(self):
        """Stop the scheduler loop."""
        self._is_running = False
