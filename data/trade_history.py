"""
Trade History Manager - CSV-based persistent storage for trade history.

Stores closed position P&L by expiry so history works even after market hours.
"""
import csv
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


class TradeHistoryManager:
    """Manages trade history with CSV persistence."""

    def __init__(self, csv_path: Optional[str] = None):
        """Initialize with CSV file path."""
        if csv_path is None:
            base_dir = Path(__file__).parent.parent
            csv_path = base_dir / "data_store" / "trade_history.csv"

        self.csv_path = Path(csv_path)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)

        # Manual profits CSV path
        self.manual_csv_path = self.csv_path.parent / "manual_profits.csv"

        # Ensure files exist with headers
        if not self.csv_path.exists():
            self._create_csv()
        if not self.manual_csv_path.exists():
            self._create_manual_csv()

    def _create_csv(self):
        """Create CSV file with headers."""
        with open(self.csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'date', 'expiry', 'symbol', 'option_type', 'strike',
                'quantity', 'entry_price', 'exit_price', 'pnl', 'status'
            ])

    def _create_manual_csv(self):
        """Create manual profits CSV file with headers."""
        with open(self.manual_csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['expiry', 'manual_profit', 'updated_at'])

    def add_trade(self, trade_data: Dict) -> bool:
        """Add a new trade entry."""
        try:
            with open(self.csv_path, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    trade_data.get('date', datetime.now().strftime('%Y-%m-%d')),
                    trade_data.get('expiry', ''),
                    trade_data.get('symbol', ''),
                    trade_data.get('option_type', ''),
                    trade_data.get('strike', 0),
                    trade_data.get('quantity', 0),
                    trade_data.get('entry_price', 0),
                    trade_data.get('exit_price', 0),
                    trade_data.get('pnl', 0),
                    trade_data.get('status', 'closed')
                ])
            return True
        except Exception as e:
            print(f"Error adding trade: {e}")
            return False

    def update_from_positions(self, positions: List[Dict]) -> int:
        """
        Update history from Zerodha positions.
        Adds closed positions (qty=0) that aren't already in history.
        Returns count of new entries added.
        """
        import re

        # Load existing entries
        existing = self._load_existing_symbols()
        added = 0

        for pos in positions:
            symbol = pos.get('tradingsymbol', '')

            # Only process closed NIFTY positions
            if not symbol.startswith('NIFTY'):
                continue
            if pos.get('quantity', 0) != 0:
                continue  # Still open

            # Check if already recorded
            if symbol in existing:
                continue

            # Extract expiry from symbol
            match = re.match(r'NIFTY(\d{2}[A-Z]{3}|\d{2}[A-Z]\d{2}|\d{5})', symbol)
            if not match:
                continue

            expiry_key = match.group(1)
            expiry_display = self._format_expiry(expiry_key)

            # Determine option type and strike
            option_type = 'CE' if 'CE' in symbol else 'PE'
            strike_match = re.search(r'(\d+)(CE|PE)', symbol)
            strike = int(strike_match.group(1)) if strike_match else 0

            # Get P&L (Zerodha uses 'pnl' for closed positions)
            pnl = pos.get('pnl', 0)

            # Add to history
            trade_data = {
                'date': datetime.now().strftime('%Y-%m-%d'),
                'expiry': expiry_display,
                'symbol': symbol,
                'option_type': option_type,
                'strike': strike,
                'quantity': pos.get('sell_quantity', 0) or pos.get('buy_quantity', 0),
                'entry_price': pos.get('sell_value', 0) / max(pos.get('sell_quantity', 1), 1) if pos.get('sell_quantity') else 0,
                'exit_price': pos.get('buy_value', 0) / max(pos.get('buy_quantity', 1), 1) if pos.get('buy_quantity') else 0,
                'pnl': pnl,
                'status': 'closed'
            }

            if self.add_trade(trade_data):
                added += 1
                existing.add(symbol)

        return added

    def _format_expiry(self, expiry_key: str) -> str:
        """Format expiry key to display format."""
        if len(expiry_key) == 5:
            # Format: YYMDD (e.g., 26120 = 2026-01-20)
            year = f"20{expiry_key[:2]}"
            month_char = expiry_key[2]
            day = expiry_key[3:5]

            if month_char.isdigit():
                month = f"{int(month_char):02d}"
            elif month_char == 'O':
                month = "10"
            elif month_char == 'N':
                month = "11"
            elif month_char == 'D':
                month = "12"
            else:
                month = month_char

            return f"{day}-{month}-{year}"
        else:
            # Format like 26JAN
            return expiry_key

    def _load_existing_symbols(self) -> set:
        """Load set of existing symbols from CSV."""
        existing = set()
        try:
            with open(self.csv_path, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    existing.add(row.get('symbol', ''))
        except Exception:
            pass
        return existing

    def get_history_by_expiry(self) -> Dict:
        """
        Get trade history grouped by expiry.
        Returns format compatible with /api/history endpoint.
        """
        expiry_data = {}

        try:
            with open(self.csv_path, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    expiry = row.get('expiry', 'Unknown')
                    pnl = float(row.get('pnl', 0))

                    if expiry not in expiry_data:
                        expiry_data[expiry] = {
                            'expiry': expiry,
                            'booked': 0,
                            'open': 0,
                            'closed_positions': 0
                        }

                    expiry_data[expiry]['booked'] += pnl
                    expiry_data[expiry]['closed_positions'] += 1
        except Exception as e:
            print(f"Error reading history: {e}")

        return expiry_data

    def get_summary(self) -> Dict:
        """Get summary statistics."""
        expiry_data = self.get_history_by_expiry()

        total_booked = sum(e['booked'] for e in expiry_data.values())

        by_expiry = []
        for expiry, data in sorted(expiry_data.items(), reverse=True):
            by_expiry.append({
                'expiry': data['expiry'],
                'booked': data['booked'],
                'open': 0,  # CSV only has closed positions
                'total_pnl': data['booked'],
                'open_positions': 0,
                'closed_positions': data['closed_positions']
            })

        return {
            'booked_profit': total_booked,
            'open_pnl': 0,
            'total': total_booked,
            'by_expiry': by_expiry
        }

    def clear_history(self):
        """Clear all history (recreate CSV)."""
        self._create_csv()

    def get_manual_profits(self) -> Dict[str, float]:
        """Get all manual profits keyed by expiry."""
        manual_profits = {}
        try:
            with open(self.manual_csv_path, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    expiry = row.get('expiry', '')
                    profit = float(row.get('manual_profit', 0))
                    if expiry:
                        manual_profits[expiry] = profit
        except Exception as e:
            print(f"Error reading manual profits: {e}")
        return manual_profits

    def set_manual_profit(self, expiry: str, profit: float) -> bool:
        """Set manual profit for a specific expiry."""
        try:
            # Load existing data
            manual_profits = self.get_manual_profits()
            manual_profits[expiry] = profit

            # Rewrite CSV with updated data
            with open(self.manual_csv_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['expiry', 'manual_profit', 'updated_at'])
                for exp, prof in manual_profits.items():
                    writer.writerow([exp, prof, datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
            return True
        except Exception as e:
            print(f"Error setting manual profit: {e}")
            return False

    def get_total_manual_profit(self) -> float:
        """Get sum of all manual profits."""
        return sum(self.get_manual_profits().values())


# Singleton instance
_history_manager = None

def get_history_manager() -> TradeHistoryManager:
    """Get singleton history manager instance."""
    global _history_manager
    if _history_manager is None:
        _history_manager = TradeHistoryManager()
    return _history_manager
