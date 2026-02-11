"""
Signal History Manager - JSON-based storage for signal timing events.

Tracks signal start/end times throughout the day to help tune timing parameters.
Stores 5 days of history with auto-pruning.
"""
import json
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from loguru import logger


# File path for signal history
SIGNAL_HISTORY_FILE = Path(__file__).parent.parent / "data_store" / "signal_history.json"


class SignalHistoryManager:
    """Manages signal event history with JSON persistence."""

    def __init__(self, json_path: Optional[str] = None):
        """Initialize with JSON file path."""
        if json_path is None:
            json_path = SIGNAL_HISTORY_FILE

        self.json_path = Path(json_path)
        self.json_path.parent.mkdir(parents=True, exist_ok=True)

        # In-memory tracking for current signal
        self._current_signal_start: Optional[datetime] = None
        self._max_duration_before_break: float = 0

        # Load existing data
        self._data = self._load()

    def _load(self) -> Dict:
        """Load signal history from JSON file."""
        try:
            if self.json_path.exists():
                with open(self.json_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading signal history: {e}")
        return {}

    def _save(self):
        """Save signal history to JSON file."""
        try:
            with open(self.json_path, 'w') as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving signal history: {e}")

    def _prune_old_data(self):
        """Remove data older than 5 days."""
        cutoff = (date.today() - timedelta(days=5)).isoformat()
        keys_to_remove = [k for k in self._data.keys() if k < cutoff]
        for key in keys_to_remove:
            del self._data[key]

    def signal_started(self):
        """Record that a signal has started."""
        self._current_signal_start = datetime.now()
        self._max_duration_before_break = 0
        logger.debug(f"Signal history: tracking start at {self._current_signal_start.strftime('%H:%M:%S')}")

    def signal_ended(self, reason: str, duration_seconds: float, required_seconds: float = 300):
        """
        Record that a signal has ended.

        Args:
            reason: "broke" | "entry_ready" | "window_closed" | "trade_executed"
            duration_seconds: How long the signal lasted
            required_seconds: Duration required for entry (default 300 = 5 min)
        """
        if self._current_signal_start is None:
            return

        today = date.today().isoformat()
        if today not in self._data:
            self._data[today] = []

        event = {
            "start": self._current_signal_start.strftime("%H:%M:%S"),
            "end": datetime.now().strftime("%H:%M:%S"),
            "duration": int(duration_seconds),
            "reason": reason,
            "reached_threshold": duration_seconds >= required_seconds
        }

        self._data[today].append(event)
        self._prune_old_data()
        self._save()

        logger.info(f"Signal history: recorded {reason} after {int(duration_seconds)}s (threshold: {event['reached_threshold']})")

        # Reset tracking
        self._current_signal_start = None
        self._max_duration_before_break = 0

    def update_duration(self, duration_seconds: float):
        """Track max duration reached (for partial signals that break)."""
        if duration_seconds > self._max_duration_before_break:
            self._max_duration_before_break = duration_seconds

    def is_tracking(self) -> bool:
        """Check if we're currently tracking a signal."""
        return self._current_signal_start is not None

    def get_today_signals(self) -> List[Dict]:
        """Get all signal events for today."""
        today = date.today().isoformat()
        return self._data.get(today, [])

    def get_history(self, days: int = 5) -> Dict[str, List[Dict]]:
        """Get signal history for the last N days."""
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        return {k: v for k, v in self._data.items() if k >= cutoff}

    def get_summary(self) -> Dict:
        """Get summary statistics for display."""
        history = self.get_history(5)

        daily_stats = []
        for date_str in sorted(history.keys(), reverse=True):
            signals = history[date_str]
            if not signals:
                continue

            total_signals = len(signals)
            total_duration = sum(s['duration'] for s in signals)
            avg_duration = total_duration / total_signals if total_signals > 0 else 0
            reached_threshold = sum(1 for s in signals if s.get('reached_threshold', False))

            daily_stats.append({
                "date": date_str,
                "signals": total_signals,
                "avg_duration": int(avg_duration),
                "reached_threshold": reached_threshold
            })

        return {
            "today": self.get_today_signals(),
            "daily_stats": daily_stats
        }

    def format_duration(self, seconds: int) -> str:
        """Format duration as Xm Ys."""
        mins = seconds // 60
        secs = seconds % 60
        return f"{mins}m {secs}s"


# Singleton instance
_signal_history_manager: Optional[SignalHistoryManager] = None


def get_signal_history_manager() -> SignalHistoryManager:
    """Get singleton signal history manager instance."""
    global _signal_history_manager
    if _signal_history_manager is None:
        _signal_history_manager = SignalHistoryManager()
    return _signal_history_manager
