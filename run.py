#!/usr/bin/env python3
"""
NIFTY Strangle Trading - Single Entry Point

Run with:
    python run.py        # CLI mode
    python run.py --ui   # Web UI mode

Handles:
1. Zerodha connection check
2. Request token prompt if needed
3. Access token generation
4. Starts monitoring with trade capability
"""
import os
import sys
import re
import argparse
from pathlib import Path

from dotenv import load_dotenv, set_key
from kiteconnect import KiteConnect
from loguru import logger

# Setup logging
logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {message}")

ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(ENV_FILE)


def get_kite_client():
    """Initialize Kite client."""
    api_key = os.getenv("KITE_API_KEY")
    api_secret = os.getenv("KITE_API_SECRET")
    access_token = os.getenv("KITE_ACCESS_TOKEN")

    if not api_key or not api_secret:
        print("ERROR: KITE_API_KEY and KITE_API_SECRET must be set in .env")
        sys.exit(1)

    kite = KiteConnect(api_key=api_key)
    return kite, api_key, api_secret, access_token


def check_connection(kite, access_token):
    """Check if current access token is valid."""
    if not access_token:
        return False, None

    try:
        kite.set_access_token(access_token)
        profile = kite.profile()
        return True, profile['user_name']
    except Exception:
        return False, None


def get_login_url(api_key):
    """Get Kite login URL."""
    return f"https://kite.zerodha.com/connect/login?api_key={api_key}&v=3"


def extract_request_token(url_or_token):
    """Extract request token from URL or direct input."""
    # If it's a URL, extract the request_token parameter
    if "request_token=" in url_or_token:
        match = re.search(r'request_token=([^&]+)', url_or_token)
        if match:
            return match.group(1)
    # Otherwise assume it's the token itself
    return url_or_token.strip()


def generate_access_token(kite, api_secret, request_token):
    """Generate access token from request token."""
    try:
        data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = data["access_token"]

        # Save to .env file
        set_key(str(ENV_FILE), "KITE_ACCESS_TOKEN", access_token)
        print(f"Access token saved to .env")

        return access_token
    except Exception as e:
        print(f"ERROR: Failed to generate access token: {e}")
        return None


def main():
    print("""
╔═══════════════════════════════════════════════════════════════╗
║           NIFTY 7-DELTA STRANGLE TRADING SYSTEM               ║
╚═══════════════════════════════════════════════════════════════╝
""")

    # Step 1: Initialize Kite client
    kite, api_key, api_secret, access_token = get_kite_client()

    # Step 2: Check if current token is valid
    print("Checking Zerodha connection...")
    is_valid, user_name = check_connection(kite, access_token)

    if is_valid:
        print(f"Connected as: {user_name}")
    else:
        print("Access token invalid or expired. Need to re-authenticate.\n")

        # Show login URL
        login_url = get_login_url(api_key)
        print("Step 1: Open this URL in browser and login:")
        print(f"\n  {login_url}\n")
        print("Step 2: After login, you'll be redirected. Copy the FULL URL or request_token.\n")

        # Get request token from user
        user_input = input("Paste redirect URL or request_token: ").strip()

        if not user_input:
            print("No input provided. Exiting.")
            sys.exit(1)

        request_token = extract_request_token(user_input)
        print(f"Request token: {request_token[:10]}...")

        # Generate access token
        print("\nGenerating access token...")
        access_token = generate_access_token(kite, api_secret, request_token)

        if not access_token:
            sys.exit(1)

        # Verify new token
        kite.set_access_token(access_token)
        is_valid, user_name = check_connection(kite, access_token)

        if is_valid:
            print(f"Connected as: {user_name}")
        else:
            print("ERROR: Connection still failed. Please try again.")
            sys.exit(1)

    # Step 3: Show current mode
    paper_trading = os.getenv("PAPER_TRADING", "true").lower() == "true"
    lot_quantity = int(os.getenv("LOT_QUANTITY", "1"))
    lot_size = 65  # NIFTY lot size
    mode = "PAPER TRADING" if paper_trading else "LIVE TRADING"
    print(f"\nMode: {mode}")
    print(f"Lots per trade: {lot_quantity} ({lot_quantity * lot_size} qty)")

    if not paper_trading:
        confirm = input("\n⚠️  LIVE TRADING enabled. Continue? [Y/N]: ").strip().upper()
        if confirm != 'Y':
            print("Aborted.")
            sys.exit(0)

    # Step 4: Start monitoring with trade capability
    print("\n" + "=" * 60)
    print("Starting strangle monitor with trade capability...")
    print("=" * 60 + "\n")

    # Import and run the monitor
    from tools.trade_strangle import run_monitor
    run_monitor(trade_enabled=True)


def kill_port(port):
    """Kill any process using the specified port."""
    import subprocess

    try:
        if sys.platform == "win32":
            # Windows
            result = subprocess.run(
                f'netstat -ano | findstr :{port} | findstr LISTENING',
                shell=True, capture_output=True, text=True
            )
            for line in result.stdout.strip().split('\n'):
                if line:
                    parts = line.split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        subprocess.run(f'taskkill /PID {pid} /F', shell=True,
                                     capture_output=True)
        else:
            # Mac/Linux
            subprocess.run(f'lsof -ti:{port} | xargs kill -9 2>/dev/null',
                         shell=True, capture_output=True)
        print(f"Cleared port {port}")
    except Exception as e:
        pass  # Ignore errors if no process found


def run_ui():
    """Launch the web UI."""
    print("""
╔═══════════════════════════════════════════════════════════════╗
║           NIFTY 7-DELTA STRANGLE TRADING SYSTEM               ║
║                        WEB UI MODE                            ║
╚═══════════════════════════════════════════════════════════════╝
""")
    # Kill any existing process on port 8080
    kill_port(8080)

    print("Starting web server...")
    print("Open in browser: http://localhost:8080")
    print("Press Ctrl+C to stop\n")

    from ui.app import app
    app.run(debug=False, host="0.0.0.0", port=8080)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NIFTY Strangle Trading System")
    parser.add_argument("--ui", action="store_true", help="Launch web UI instead of CLI")
    args = parser.parse_args()

    if args.ui:
        run_ui()
    else:
        main()
