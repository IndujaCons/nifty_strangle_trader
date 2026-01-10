"""
Date utilities for expiry calculations.
"""
from datetime import datetime, timedelta
from typing import List, Optional
import pytz

IST = pytz.timezone("Asia/Kolkata")


def get_current_ist_time() -> datetime:
    """Get current time in IST."""
    return datetime.now(IST)


def get_next_tuesday(from_date: datetime = None) -> datetime:
    """Get the next Tuesday from given date (Nifty weekly expiry day)."""
    if from_date is None:
        from_date = datetime.now()

    days_ahead = 1 - from_date.weekday()  # Tuesday is weekday 1
    if days_ahead <= 0:
        days_ahead += 7
    return from_date + timedelta(days=days_ahead)


def get_weekly_expiries(num_weeks: int = 4, from_date: datetime = None) -> List[datetime]:
    """Get list of upcoming weekly expiry dates (Tuesdays - new Nifty expiry day)."""
    if from_date is None:
        from_date = datetime.now()

    expiries = []
    current = from_date

    for _ in range(num_weeks):
        next_exp = get_next_tuesday(current)
        expiries.append(next_exp)
        current = next_exp + timedelta(days=1)

    return expiries


def get_expiry_for_dte(target_dte: int, tolerance: int = 2) -> Optional[str]:
    """
    Find expiry date that matches target DTE within tolerance.

    Args:
        target_dte: Target days to expiry (e.g., 14)
        tolerance: Acceptable range +/- days

    Returns:
        Expiry date string in YYYY-MM-DD format, or None if not found
    """
    today = datetime.now().date()
    expiries = get_weekly_expiries(num_weeks=5)

    for expiry in expiries:
        expiry_date = expiry.date()
        dte = (expiry_date - today).days

        if target_dte - tolerance <= dte <= target_dte + tolerance:
            return expiry_date.strftime("%Y-%m-%d")

    return None


def calculate_dte(expiry_str: str) -> int:
    """Calculate days to expiry from expiry string."""
    expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
    today = datetime.now().date()
    return (expiry_date - today).days


def format_expiry_for_nse(expiry_str: str) -> str:
    """
    Format expiry string for NSE API.
    Input: 2025-01-09 or 09-Jan-2025 or 20-Jan-2026
    Output: 09-Jan-2025
    """
    # Try different input formats
    formats_to_try = [
        "%Y-%m-%d",     # 2025-01-09
        "%d-%b-%Y",     # 09-Jan-2025
        "%d-%B-%Y",     # 09-January-2025
    ]

    expiry_date = None
    for fmt in formats_to_try:
        try:
            expiry_date = datetime.strptime(expiry_str, fmt)
            break
        except ValueError:
            continue

    if expiry_date is None:
        # Return as-is if already in correct format
        return expiry_str

    return expiry_date.strftime("%d-%b-%Y")


def format_expiry_for_kite(expiry_str: str) -> str:
    """
    Format expiry string for Kite trading symbol.
    Input: 2025-01-09
    Output: 250109
    """
    expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d")
    return expiry_date.strftime("%y%m%d")


def is_market_open() -> bool:
    """Check if market is currently open (9:15 AM - 3:30 PM IST, Mon-Fri)."""
    now = get_current_ist_time()

    # Check if weekday (Monday = 0, Sunday = 6)
    if now.weekday() > 4:
        return False

    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)

    return market_open <= now <= market_close


def get_time_to_market_open() -> Optional[timedelta]:
    """Get time remaining until market opens. Returns None if market is open."""
    if is_market_open():
        return None

    now = get_current_ist_time()
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)

    if now > market_open:
        # Market closed for today, calculate time to tomorrow's open
        tomorrow = now + timedelta(days=1)
        # Skip weekends
        while tomorrow.weekday() > 4:
            tomorrow += timedelta(days=1)
        market_open = tomorrow.replace(hour=9, minute=15, second=0, microsecond=0)

    return market_open - now
