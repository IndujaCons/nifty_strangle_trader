"""
Display utilities for formatted output.
"""
from datetime import datetime
from typing import Optional

from data.nse_data_provider import NSEDataProvider
from greeks.delta_calculator import DeltaStrikeSelector
from utils.date_utils import is_market_open, get_expiry_for_dte, get_current_ist_time, calculate_dte
from config.settings import STRATEGY_CONFIG, NIFTY_CONFIG


def print_summary_table(
    spot: float = None,
    iv: float = None,
    straddle_price: float = None,
    vwap: float = None,
    signal_status: dict = None,
    positions: int = 0,
    entries_today: int = 0
):
    """
    Print formatted summary table with current market data and strangle setup.

    Args:
        spot: Current spot price (fetched if None)
        iv: Implied volatility (fetched if None)
        straddle_price: ATM straddle price (fetched if None)
        vwap: Current VWAP value
        signal_status: Signal tracking status dict
        positions: Number of open positions
        entries_today: Number of entries made today
    """
    # Initialize data provider if needed
    provider = NSEDataProvider(use_simulation=True)
    delta_selector = DeltaStrikeSelector()

    # Get option chain first (for both spot and premiums)
    option_chain = provider.get_option_chain('NIFTY')

    # Fetch data if not provided
    if spot is None:
        spot = option_chain.get("spot_price", 0)
        if spot <= 0:
            spot = provider.get_spot_price('NIFTY')
    if iv is None:
        iv = provider.get_india_vix()

    # Get target expiry
    target_expiry = get_expiry_for_dte(14)
    dte = calculate_dte(target_expiry) if target_expiry else 0

    # Get ATM straddle price if not provided
    if straddle_price is None and target_expiry:
        straddle_price, _, atm_strike = provider.get_atm_straddle_price(target_expiry, 'NIFTY')
    else:
        atm_strike = round(spot / 50) * 50

    # Calculate strangle strikes with option chain for actual premiums
    if dte > 0:
        call_strike, put_strike, details = delta_selector.select_strangle_strikes(
            spot_price=spot,
            expiry_days=dte,
            iv=iv,
            target_delta=0.07,
            option_chain=option_chain,
            expiry=target_expiry
        )
    else:
        details = {}

    # Current time
    ist_now = get_current_ist_time()
    market_status = "OPEN" if is_market_open() else "CLOSED"

    # Print header
    print()
    print("=" * 62)
    print("       NIFTY STRANGLE AUTOMATION - LIVE STATUS")
    print("=" * 62)
    print()

    # System status table
    print("┌─────────────────────────────────────────────────────────────┐")
    print(f"│ Time (IST)     │ {ist_now.strftime('%Y-%m-%d %H:%M:%S'):<42} │")
    print(f"│ Market Status  │ {market_status:<42} │")
    print(f"│ Mode           │ {'Paper Trading':<42} │")
    print("└─────────────────────────────────────────────────────────────┘")
    print()

    # Market data table
    print("┌─────────────────────────────────────────────────────────────┐")
    print("│                      MARKET DATA                           │")
    print("├─────────────────────────────────────────────────────────────┤")
    print(f"│ Spot Price          │ ₹{spot:>38,.2f} │")
    print(f"│ IV (VIX Proxy)      │ {iv*100:>37.2f}% │")
    print(f"│ ATM Strike          │ {int(atm_strike):>39,} │")
    print(f"│ ATM Straddle Price  │ ₹{straddle_price:>38,.2f} │")
    if vwap is not None and vwap > 0:
        print(f"│ Straddle VWAP       │ ₹{vwap:>38,.2f} │")
        diff = straddle_price - vwap
        diff_pct = (diff / vwap) * 100 if vwap > 0 else 0
        status = "ABOVE ↑" if diff > 0 else "BELOW ↓"
        print(f"│ vs VWAP             │ {status} {abs(diff):.2f} ({abs(diff_pct):.1f}%){' '*16} │")
    print("└─────────────────────────────────────────────────────────────┘")
    print()

    # Strangle selection table
    if details:
        premium_source = details.get('premium_source', 'unknown').upper()
        print("┌─────────────────────────────────────────────────────────────┐")
        print(f"│          STRANGLE SETUP ({dte} DTE - {target_expiry})          │")
        print(f"│          Premium Source: {premium_source:<34} │")
        print("├────────────────┬──────────┬──────────┬────────────────────┤")
        print("│ Leg            │ Strike   │ Delta    │ Premium            │")
        print("├────────────────┼──────────┼──────────┼────────────────────┤")
        print(f"│ Call (CE)      │ {int(details['call_strike']):>8,} │ {details['call_delta']:>8.4f} │ ₹{details['call_premium']:>17.2f} │")
        print(f"│ Put (PE)       │ {int(details['put_strike']):>8,} │ {details['put_delta']:>8.4f} │ ₹{details['put_premium']:>17.2f} │")
        print("├────────────────┴──────────┴──────────┼────────────────────┤")
        print(f"│ Combined Premium                     │ ₹{details['total_premium']:>17.2f} │")
        lot_size = NIFTY_CONFIG["lot_size"]
        print(f"│ Premium per Lot ({lot_size})               │ ₹{details['total_premium'] * lot_size:>17,.2f} │")
        print(f"│ Profit Target (50%)                  │ ₹{details['total_premium'] * lot_size * 0.5:>17,.2f} │")
        print("├──────────────────────────────────────┴────────────────────┤")
        be_upper = details['call_strike'] + details['total_premium']
        be_lower = details['put_strike'] - details['total_premium']
        print(f"│ Breakeven Range: {int(be_lower):,} - {int(be_upper):,}{' '*22} │")
        print(f"│ Strangle Width: {int(details['strangle_width']):,} points{' '*27} │")
        print("└─────────────────────────────────────────────────────────────┘")
        print()

    # Entry signal status
    print("┌─────────────────────────────────────────────────────────────┐")
    print("│                    ENTRY SIGNAL STATUS                      │")
    print("├─────────────────────────────────────────────────────────────┤")
    if signal_status:
        signal_active = signal_status.get('signal_active', False)
        elapsed = signal_status.get('elapsed_seconds', 0)
        required = signal_status.get('required_seconds', 120)
        ready = signal_status.get('ready_to_enter', False)

        if ready:
            print(f"│ Status: ✓ ENTRY SIGNAL CONFIRMED{' '*27} │")
        elif signal_active:
            remaining = max(0, required - elapsed)
            bar_len = int((elapsed / required) * 20)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            print(f"│ Signal Active: [{bar}] {elapsed:.0f}s / {required}s{' '*7} │")
        else:
            print(f"│ Status: Waiting for straddle > VWAP{' '*24} │")
    else:
        print(f"│ Status: Collecting VWAP data...{' '*28} │")

    print(f"│ Required Duration: {STRATEGY_CONFIG['signal_duration_seconds']}s{' '*37} │")
    print("└─────────────────────────────────────────────────────────────┘")
    print()

    # Position summary
    print("┌─────────────────────────────────────────────────────────────┐")
    print("│                    POSITION SUMMARY                         │")
    print("├─────────────────────────────────────────────────────────────┤")
    print(f"│ Open Positions      │ {positions:>38} │")
    print(f"│ Entries Today       │ {entries_today} / {STRATEGY_CONFIG['max_entries_per_day']}{' '*33} │")
    print(f"│ Capital Parts       │ {STRATEGY_CONFIG['total_parts'] - positions} / {STRATEGY_CONFIG['total_parts']} available{' '*23} │")
    print("└─────────────────────────────────────────────────────────────┘")
    print()
    print("=" * 62)
    print()


def print_trade_alert(action: str, strangle_id: str, details: dict):
    """Print formatted trade alert."""
    print()
    print("*" * 62)
    if action == "ENTRY":
        print(f"*  🔔 NEW STRANGLE ENTRY - {strangle_id}")
    else:
        print(f"*  🔔 STRANGLE EXIT - {strangle_id}")
    print("*" * 62)

    for key, value in details.items():
        if isinstance(value, float):
            print(f"*  {key}: ₹{value:,.2f}")
        else:
            print(f"*  {key}: {value}")

    print("*" * 62)
    print()
