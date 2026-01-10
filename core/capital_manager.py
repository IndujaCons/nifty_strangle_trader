"""
Capital manager for position sizing.
Divides capital into 6 equal parts for staged entries.
"""
from typing import Optional, List, Dict
from datetime import datetime
from loguru import logger

from config.settings import STRATEGY_CONFIG, TOTAL_CAPITAL


class CapitalManager:
    """
    Manages capital allocation for the strangle strategy.

    Divides total capital into 6 equal parts.
    Each strangle entry uses 1 part.
    Maximum 2 entries per day.
    """

    def __init__(self, total_capital: float = None, total_parts: int = None):
        self.total_capital = total_capital or TOTAL_CAPITAL
        self.total_parts = total_parts or STRATEGY_CONFIG["total_parts"]
        self.max_entries_per_day = STRATEGY_CONFIG["max_entries_per_day"]

        # Track allocations: part_number -> strangle_id or None
        self.allocations: Dict[int, Optional[str]] = {
            i: None for i in range(1, self.total_parts + 1)
        }

        # Daily tracking
        self.entries_today = 0
        self.current_date: Optional[datetime] = None

    @property
    def capital_per_part(self) -> float:
        """Capital allocated to each part."""
        return self.total_capital / self.total_parts

    def reset_daily_counter(self):
        """Reset daily entry counter. Call at market open."""
        today = datetime.now().date()
        if self.current_date != today:
            self.entries_today = 0
            self.current_date = today
            logger.info("Daily entry counter reset")

    def _check_day_change(self):
        """Check if day has changed and reset if needed."""
        today = datetime.now().date()
        if self.current_date is None or self.current_date != today:
            self.reset_daily_counter()

    def get_available_parts(self) -> List[int]:
        """Get list of unallocated capital parts."""
        return [part for part, strangle_id in self.allocations.items() if strangle_id is None]

    def get_allocated_parts(self) -> List[int]:
        """Get list of allocated capital parts."""
        return [part for part, strangle_id in self.allocations.items() if strangle_id is not None]

    def has_available_capital(self) -> bool:
        """Check if there's available capital for new entry."""
        self._check_day_change()
        return len(self.get_available_parts()) > 0

    def can_enter_today(self) -> bool:
        """Check if we can enter more positions today."""
        self._check_day_change()
        return self.entries_today < self.max_entries_per_day

    def can_enter(self) -> bool:
        """Check if we can enter a new position (both capital and daily limit)."""
        return self.has_available_capital() and self.can_enter_today()

    def allocate_capital(self, strangle_id: str) -> Optional[int]:
        """
        Allocate one part of capital to a strangle.

        Args:
            strangle_id: ID of the strangle position

        Returns:
            Part number allocated, or None if no capital available
        """
        self._check_day_change()

        available = self.get_available_parts()
        if not available:
            logger.warning("No capital parts available for allocation")
            return None

        if not self.can_enter_today():
            logger.warning(f"Max entries ({self.max_entries_per_day}) reached for today")
            return None

        # Allocate first available part
        part = available[0]
        self.allocations[part] = strangle_id
        self.entries_today += 1

        logger.info(
            f"Capital allocated: Part {part} to strangle {strangle_id}. "
            f"Entries today: {self.entries_today}/{self.max_entries_per_day}"
        )

        return part

    def release_capital(self, strangle_id: str) -> Optional[int]:
        """
        Release capital when strangle is closed.

        Args:
            strangle_id: ID of the closed strangle

        Returns:
            Part number released, or None if not found
        """
        for part, allocated_id in self.allocations.items():
            if allocated_id == strangle_id:
                self.allocations[part] = None
                logger.info(f"Capital released: Part {part} from strangle {strangle_id}")
                return part

        logger.warning(f"Strangle {strangle_id} not found in allocations")
        return None

    def get_position_size(self) -> int:
        """
        Get position size (number of lots) for new entry.

        Currently returns 1 lot per entry.
        Can be enhanced to calculate based on capital per part.
        """
        return 1

    def get_status(self) -> Dict:
        """Get capital allocation status."""
        return {
            "total_capital": self.total_capital,
            "capital_per_part": self.capital_per_part,
            "total_parts": self.total_parts,
            "available_parts": len(self.get_available_parts()),
            "allocated_parts": len(self.get_allocated_parts()),
            "entries_today": self.entries_today,
            "max_entries_per_day": self.max_entries_per_day,
            "can_enter": self.can_enter(),
            "allocations": dict(self.allocations)
        }
