#!/usr/bin/env python3
"""
NIFTY 7-Delta Strangle Trading Tool

Semi-automated trading with:
- Continuous monitoring every 60 seconds
- 5-minute VWAP signal confirmation
- Trading window management (morning/afternoon)
- User confirmation before placing trades
- Position display after successful trades

Usage:
    python tools/trade_strangle.py --monitor          # Monitor only
    python tools/trade_strangle.py --monitor --trade  # Monitor + trade capability
"""
import sys
import os
import time
import argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from data.kite_data_provider import KiteDataProvider, StrangleData
from core.signal_tracker import SignalTracker
from config.settings import NIFTY_CONFIG, MARKET_CONFIG, PAPER_TRADING, LOT_QUANTITY

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")


def clear_line():
    """Clear current line for live updates."""
    print("\r" + " " * 80 + "\r", end="", flush=True)


def display_status(data: StrangleData, signal_info: dict, tracker: SignalTracker):
    """Display current monitoring status."""
    now = datetime.now().strftime("%H:%M:%S")

    # Signal status
    if signal_info["signal_active"]:
        duration = tracker.format_duration(signal_info["duration_seconds"])
        required = tracker.format_duration(signal_info["required_seconds"])
        signal_str = f"Signal: {duration} / {required}"
    else:
        signal_str = "Signal: INACTIVE"

    # Window status
    window = signal_info["current_window"] or "CLOSED"
    trades_str = f"M:{signal_info['morning_trades']} A:{signal_info['afternoon_trades']}"

    # Prices
    diff = data.straddle_price - data.straddle_vwap
    diff_str = f"+{diff:.1f}" if diff > 0 else f"{diff:.1f}"

    print(f"[{now}] Straddle: {data.straddle_price:.1f} | VWAP: {data.straddle_vwap:.1f} ({diff_str}) | {signal_str} | Window: {window} | Trades: {trades_str}")


def display_entry_alert(data: StrangleData):
    """Display entry alert with strangle details."""
    lot_size = NIFTY_CONFIG["lot_size"]
    total_qty = lot_size * LOT_QUANTITY
    total_premium = data.per_lot * LOT_QUANTITY

    print(f"""
{'=' * 60}
  ENTRY SIGNAL CONFIRMED (5 minutes)
{'=' * 60}

  7-DELTA STRANGLE
  ----------------
  SELL CE {data.call_strike:.0f} @ {data.call_ltp:>8.2f}  (Delta: {data.call_delta:.4f})
  SELL PE {data.put_strike:.0f} @ {data.put_ltp:>8.2f}  (Delta: {data.put_delta:.4f})

  Lots:           {LOT_QUANTITY:>10} ({total_qty} qty)
  Premium/Lot:    {data.per_lot:>10,.2f}
  Total Premium:  {total_premium:>10,.2f}
  Width:          {data.width:>10.0f} pts

  Expiry: {data.expiry} ({data.dte} DTE)
{'=' * 60}
""")


def confirm_trade() -> bool:
    """Ask user for trade confirmation."""
    try:
        response = input("\nPlace this trade? [Y/N]: ").strip().upper()
        return response == 'Y'
    except (EOFError, KeyboardInterrupt):
        return False


def execute_trade(provider: KiteDataProvider, data: StrangleData, tracker: SignalTracker, window: str) -> bool:
    """Execute the strangle trade and display results."""
    print("\nPlacing orders...")

    result = provider.place_strangle_order(
        expiry=data.expiry,
        call_strike=data.call_strike,
        put_strike=data.put_strike,
        quantity=1
    )

    if result["success"]:
        mode = "[PAPER]" if result["paper_trading"] else "[LIVE]"
        print(f"\n{mode} Trade Successful!")
        print(f"  CE {data.call_strike:.0f} SELL: Order {result['call_order']['order_id']} - {result['call_order']['status']}")
        print(f"  PE {data.put_strike:.0f} SELL: Order {result['put_order']['order_id']} - {result['put_order']['status']}")

        # Record trade in tracker
        tracker.record_trade(window)

        # Display current positions
        print(provider.display_positions())

        # Show next window info
        next_window = tracker.get_next_window_time()
        if next_window:
            print(f"\nNext trade window: {next_window}")
        else:
            print("\nNo more trading windows today")

        return True
    else:
        print(f"\nTrade FAILED: {result.get('error', 'Unknown error')}")
        return False


def run_monitor(trade_enabled: bool = False):
    """Main monitoring loop."""
    print("Connecting to Zerodha...")
    provider = KiteDataProvider()

    if not provider.connect():
        print("Failed to connect. Please check your access token.")
        print(f"Login URL: {provider.get_login_url()}")
        return

    tracker = SignalTracker()
    interval = MARKET_CONFIG["strategy_interval_seconds"]
    position_update_interval = 300  # 5 minutes
    last_position_update = 0

    mode = "MONITOR + TRADE" if trade_enabled else "MONITOR ONLY"
    paper = " [PAPER TRADING]" if PAPER_TRADING else ""
    print(f"\nStarting {mode}{paper}")
    print(f"Checking every {interval} seconds. Position updates every 5 mins.")
    print(f"Press Ctrl+C to stop.\n")

    try:
        while True:
            # Check market hours
            now = datetime.now()
            current_time = now.time()
            market_open = datetime.strptime(MARKET_CONFIG["market_open"], "%H:%M").time()
            market_close = datetime.strptime(MARKET_CONFIG["market_close"], "%H:%M").time()

            if current_time < market_open or current_time > market_close:
                print(f"[{now.strftime('%H:%M:%S')}] Market closed. Waiting...")
                time.sleep(60)
                continue

            # Fetch market data
            try:
                data = provider.find_strangle()
            except Exception as e:
                print(f"[{now.strftime('%H:%M:%S')}] Error fetching data: {e}")
                time.sleep(interval)
                continue

            if not data:
                print(f"[{now.strftime('%H:%M:%S')}] Could not find strangle data")
                time.sleep(interval)
                continue

            # Update signal tracker
            signal_info = tracker.update_signal(data.straddle_price, data.straddle_vwap)

            # Display status
            display_status(data, signal_info, tracker)

            # Check for position P&L update every 5 minutes
            current_time_secs = time.time()
            if current_time_secs - last_position_update >= position_update_interval:
                pos_data = provider.get_positions()
                if pos_data["success"] and pos_data["positions"]:
                    print(provider.display_positions())
                last_position_update = current_time_secs

            # Check for entry signal
            if signal_info["entry_ready"]:
                display_entry_alert(data)

                if trade_enabled:
                    if confirm_trade():
                        success = execute_trade(provider, data, tracker, signal_info["current_window"])
                        if success:
                            print("\nResuming monitoring...\n")
                    else:
                        print("\nTrade skipped. Resuming monitoring...\n")
                        # Reset signal so we don't keep alerting
                        tracker.signal_state.reset()
                else:
                    print("Trade mode not enabled. Use --trade flag to enable.")
                    tracker.signal_state.reset()

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\n\nMonitoring stopped by user.")


def main():
    parser = argparse.ArgumentParser(description="NIFTY 7-Delta Strangle Trading Tool")
    parser.add_argument("--monitor", action="store_true", help="Start monitoring mode")
    parser.add_argument("--trade", action="store_true", help="Enable trade execution (requires confirmation)")
    parser.add_argument("--positions", action="store_true", help="Show current positions and exit")

    args = parser.parse_args()

    if args.positions:
        provider = KiteDataProvider()
        if provider.connect():
            print(provider.display_positions())
        else:
            print("Failed to connect")
        return

    if args.monitor:
        run_monitor(trade_enabled=args.trade)
    else:
        # Default: show current strangle (like find_strangle.py)
        from tools.find_strangle import main as find_main
        find_main()


if __name__ == "__main__":
    main()
