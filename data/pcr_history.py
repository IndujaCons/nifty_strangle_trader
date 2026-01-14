"""
PCR History Manager - Store and retrieve daily PCR data.
"""
import csv
from datetime import date, datetime
from pathlib import Path
from typing import List, Dict, Optional

from config.settings import DATA_DIR


class PCRHistoryManager:
    """Manages daily PCR history storage and retrieval."""

    def __init__(self):
        self.file_path = DATA_DIR / "pcr_history.csv"
        self.alert_shown_today = False
        self._last_alert_date = None
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        """Create CSV file with headers if it doesn't exist."""
        if not self.file_path.exists():
            with open(self.file_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'date', 'expiry', 'pcr', 'max_pain',
                    'ce_oi', 'pe_oi', 'spot', 'timestamp'
                ])

    def save_pcr(
        self,
        pcr: float,
        max_pain: int,
        ce_oi: int,
        pe_oi: int,
        spot: float,
        expiry: date = None
    ) -> bool:
        """
        Save PCR data to history CSV.

        Returns:
            True if saved, False if already exists for today
        """
        today = date.today()

        # Check if already saved today
        if self.has_entry_for_date(today):
            return False

        expiry_str = str(expiry) if expiry else ""
        timestamp = datetime.now().strftime("%H:%M:%S")

        with open(self.file_path, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                today.isoformat(),
                expiry_str,
                round(pcr, 2),
                max_pain,
                ce_oi,
                pe_oi,
                round(spot, 2),
                timestamp
            ])

        return True

    def has_entry_for_date(self, check_date: date) -> bool:
        """Check if PCR entry exists for given date."""
        check_str = check_date.isoformat()

        try:
            with open(self.file_path, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row['date'] == check_str:
                        return True
        except Exception:
            pass

        return False

    def get_history(self, days: int = 30) -> List[Dict]:
        """
        Get PCR history for last N days.

        Returns:
            List of dicts with PCR data, most recent first
        """
        history = []

        try:
            with open(self.file_path, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    history.append({
                        'date': row['date'],
                        'expiry': row['expiry'],
                        'pcr': float(row['pcr']) if row['pcr'] else 0,
                        'max_pain': int(row['max_pain']) if row['max_pain'] else 0,
                        'ce_oi': int(row['ce_oi']) if row['ce_oi'] else 0,
                        'pe_oi': int(row['pe_oi']) if row['pe_oi'] else 0,
                        'spot': float(row['spot']) if row['spot'] else 0,
                        'timestamp': row['timestamp']
                    })
        except Exception:
            pass

        # Sort by date descending and limit
        history.sort(key=lambda x: x['date'], reverse=True)
        return history[:days]

    def should_show_sip_alert(self, pcr: float, threshold: float = 0.7) -> bool:
        """
        Check if SIP alert should be shown.

        Conditions:
        - PCR < threshold (default 0.7)
        - Time is between 12:30 and 12:55 PM
        - Alert not already shown today

        Returns:
            True if alert should be shown
        """
        now = datetime.now()
        today = now.date()

        # Reset alert flag for new day
        if self._last_alert_date != today:
            self.alert_shown_today = False
            self._last_alert_date = today

        # Check time window (12:30 - 12:55 PM)
        current_time = now.strftime("%H:%M")
        if not ("12:30" <= current_time <= "12:55"):
            return False

        # Check PCR threshold
        if pcr >= threshold:
            return False

        # Check if already shown today
        if self.alert_shown_today:
            return False

        return True

    def mark_alert_shown(self):
        """Mark that SIP alert has been shown today."""
        self.alert_shown_today = True
        self._last_alert_date = date.today()


# Singleton instance
_pcr_manager = None


def get_pcr_manager() -> PCRHistoryManager:
    """Get singleton PCR history manager instance."""
    global _pcr_manager
    if _pcr_manager is None:
        _pcr_manager = PCRHistoryManager()
    return _pcr_manager
