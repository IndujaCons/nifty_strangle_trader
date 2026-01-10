#!/usr/bin/env python3
"""
Nifty 50 Strangle Selling Automation

Main entry point for the trading automation system.

Usage:
    python main.py                 # Run with paper trading
    python main.py --live          # Run with Kite Connect (live)
    python main.py --test          # Run single tick for testing
    python main.py --login         # Get Kite login URL
    python main.py --status        # Show current status
"""
import argparse
import sys
from loguru import logger

from config.settings import PAPER_TRADING, KITE_CONFIG, LOG_DIR, LOGGING_CONFIG
from broker.paper_broker import PaperBroker
from broker.kite_broker import KiteBroker
from data.nse_data_provider import NSEDataProvider
from core.strategy_engine import StrangleStrategy
from core.scheduler import TradingScheduler, SimpleScheduler
from core.capital_manager import CapitalManager
from core.position_manager import PositionManager
from persistence.database import DatabaseManager


def setup_logging():
    """Configure logging."""
    logger.remove()  # Remove default handler

    # Console logging
    logger.add(
        sys.stdout,
        format=LOGGING_CONFIG["format"],
        level=LOGGING_CONFIG["level"]
    )

    # File logging
    logger.add(
        LOG_DIR / "trading_{time:YYYY-MM-DD}.log",
        format=LOGGING_CONFIG["format"],
        level=LOGGING_CONFIG["level"],
        rotation=LOGGING_CONFIG["rotation"],
        retention=LOGGING_CONFIG["retention"]
    )


def create_paper_broker() -> PaperBroker:
    """Create paper trading broker."""
    data_provider = NSEDataProvider()
    broker = PaperBroker(data_provider=data_provider)
    broker.connect()
    return broker


def create_kite_broker() -> KiteBroker:
    """Create Kite Connect broker."""
    data_provider = NSEDataProvider()
    broker = KiteBroker(data_provider=data_provider)

    if not KITE_CONFIG["access_token"]:
        logger.warning("No access token found. Please login first using --login")
        logger.info(f"Login URL: {broker.get_login_url()}")
        return None

    if not broker.connect():
        logger.error("Failed to connect to Kite")
        return None

    return broker


def create_strategy(broker) -> StrangleStrategy:
    """Create strategy engine with all components."""
    data_provider = NSEDataProvider()
    capital_manager = CapitalManager()
    position_manager = PositionManager(broker)

    strategy = StrangleStrategy(
        broker=broker,
        data_provider=data_provider,
        capital_manager=capital_manager,
        position_manager=position_manager
    )

    return strategy


def run_live(use_paper: bool = True):
    """Run the trading automation with summary table display."""
    setup_logging()
    from utils.display import print_summary_table

    logger.info("=" * 50)
    logger.info("Nifty Strangle Automation Starting")
    logger.info(f"Mode: {'Paper Trading' if use_paper else 'Live Trading'}")
    logger.info("=" * 50)

    # Create broker
    if use_paper:
        broker = create_paper_broker()
    else:
        broker = create_kite_broker()
        if not broker:
            sys.exit(1)

    # Create strategy
    strategy = create_strategy(broker)

    # Create database manager
    db = DatabaseManager()

    # Create scheduler
    scheduler = TradingScheduler(
        on_market_open=strategy.on_market_open,
        on_market_close=strategy.on_market_close,
        on_strategy_tick=strategy.run_strategy_tick
    )

    scheduler.setup_schedule()

    logger.info("Starting scheduler...")
    scheduler.start()

    try:
        # Keep main thread alive
        import time
        import os
        while True:
            time.sleep(60)

            # Clear screen and show summary table
            os.system('clear' if os.name == 'posix' else 'cls')

            status = strategy.get_status()
            cap = status['capital_status']
            port = status['portfolio']

            print_summary_table(
                vwap=status['vwap_stats'].get('vwap', 0),
                signal_status=strategy.vwap_calculator.get_signal_status(),
                positions=port['open_positions'],
                entries_today=cap['entries_today']
            )

    except KeyboardInterrupt:
        logger.info("Shutdown requested...")
    finally:
        scheduler.stop()
        logger.info("Scheduler stopped")


def run_test():
    """Run single tick for testing with summary table."""
    setup_logging()
    from utils.display import print_summary_table

    logger.info("Running test tick...")

    broker = create_paper_broker()
    strategy = create_strategy(broker)

    # Trigger market open
    strategy.on_market_open()

    # Run single tick
    strategy.run_strategy_tick()

    # Get status
    status = strategy.get_status()
    cap = status['capital_status']
    port = status['portfolio']

    # Print the summary table
    print_summary_table(
        vwap=status['vwap_stats'].get('vwap', 0),
        signal_status=strategy.vwap_calculator.get_signal_status(),
        positions=port['open_positions'],
        entries_today=cap['entries_today']
    )


def show_login_url():
    """Show Kite login URL."""
    setup_logging()
    broker = KiteBroker()
    login_url = broker.get_login_url()

    print("\n" + "=" * 60)
    print("KITE CONNECT LOGIN")
    print("=" * 60)
    print(f"\n1. Open this URL in browser:\n   {login_url}")
    print("\n2. Login with your Zerodha credentials")
    print("\n3. After login, you'll be redirected to:")
    print("   http://127.0.0.1:5000/?request_token=XXXXX")
    print("\n4. Copy the request_token value and add it to .env:")
    print("   KITE_ACCESS_TOKEN=<use generate_session script>")
    print("\n" + "=" * 60)


def generate_session(request_token: str):
    """Generate session from request token."""
    setup_logging()
    broker = KiteBroker()
    broker.kite = broker.kite or __import__('kiteconnect').KiteConnect(api_key=KITE_CONFIG["api_key"])

    access_token = broker.generate_session(request_token)
    if access_token:
        print(f"\nAccess Token: {access_token}")
        print("\nAdd this to your .env file:")
        print(f"KITE_ACCESS_TOKEN={access_token}")
        print("\nNote: This token expires daily. You'll need to regenerate it each day.")
    else:
        print("Failed to generate session")


def show_status():
    """Show current strategy status with summary table."""
    setup_logging()
    from utils.display import print_summary_table

    broker = create_paper_broker()
    strategy = create_strategy(broker)
    db = DatabaseManager()

    # Get status
    status = strategy.get_status()
    cap = status['capital_status']
    port = status['portfolio']

    # Print the summary table
    print_summary_table(
        vwap=status['vwap_stats'].get('vwap', 0),
        signal_status=strategy.vwap_calculator.get_signal_status(),
        positions=port['open_positions'],
        entries_today=cap['entries_today']
    )

    # P&L summary from DB
    pnl = db.get_pnl_summary()
    print("┌─────────────────────────────────────────────────────────────┐")
    print("│                 HISTORICAL PERFORMANCE                      │")
    print("├─────────────────────────────────────────────────────────────┤")
    print(f"│ Total Trades        │ {pnl['total_trades']:>38} │")
    print(f"│ Win Rate            │ {pnl['win_rate']*100:>37.1f}% │")
    print(f"│ Total P&L           │ ₹{pnl['total_pnl']:>38,.2f} │")
    print("└─────────────────────────────────────────────────────────────┘")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Nifty 50 Strangle Selling Automation"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run with Kite Connect (live trading)"
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run single tick for testing"
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Show Kite login URL"
    )
    parser.add_argument(
        "--token",
        type=str,
        help="Generate session with request token"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current status"
    )

    args = parser.parse_args()

    if args.login:
        show_login_url()
    elif args.token:
        generate_session(args.token)
    elif args.test:
        run_test()
    elif args.status:
        show_status()
    else:
        use_paper = not args.live
        run_live(use_paper=use_paper)


if __name__ == "__main__":
    main()
