#!/usr/bin/env python3
"""
Flask Web UI for NIFTY Strangle Trading System

Run with: python ui/app.py
Open: http://localhost:5000
"""
import sys
import os
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, jsonify, request, redirect
from dotenv import load_dotenv, set_key
from pathlib import Path

from data.kite_data_provider import KiteDataProvider
from data.trade_history import get_history_manager
from core.signal_tracker import SignalTracker
from config.settings import NIFTY_CONFIG, MARKET_CONFIG, TRADING_WINDOWS

app = Flask(__name__)

# Global state
ENV_FILE = Path(__file__).parent.parent / ".env"
load_dotenv(ENV_FILE)

provider = None
tracker = SignalTracker()
monitor_thread = None
monitor_running = False
last_data = {}
trade_pending = None


def get_config():
    """Get current configuration."""
    load_dotenv(ENV_FILE, override=True)
    return {
        "api_key": os.getenv("KITE_API_KEY", ""),
        "paper_trading": os.getenv("PAPER_TRADING", "true").lower() == "true",
        "lot_quantity": int(os.getenv("LOT_QUANTITY", "1")),
        "lot_size": NIFTY_CONFIG["lot_size"],
    }


def init_provider():
    """Initialize or reinitialize the provider."""
    global provider
    load_dotenv(ENV_FILE, override=True)
    provider = KiteDataProvider()
    return provider


@app.route("/")
def index():
    """Main UI page - also handles Zerodha callback."""
    # Check if this is a Zerodha callback with request_token
    request_token = request.args.get('request_token')
    login_success = False
    login_error = None
    user_name = None

    if request_token:
        # Auto-process the token
        try:
            global provider
            if provider is None:
                init_provider()

            api_secret = os.getenv("KITE_API_SECRET", "")
            session_data = provider.kite.generate_session(request_token, api_secret=api_secret)
            access_token = session_data["access_token"]

            # Save to .env
            set_key(str(ENV_FILE), "KITE_ACCESS_TOKEN", access_token)
            os.environ["KITE_ACCESS_TOKEN"] = access_token

            # Reinitialize provider
            provider.kite.set_access_token(access_token)
            profile = provider.kite.profile()
            user_name = profile.get("user_name", "User")
            login_success = True

        except Exception as e:
            login_error = str(e)

    return render_template("index.html", login_success=login_success, login_error=login_error, user_name=user_name)


@app.route("/api/config")
def api_config():
    """Get current configuration."""
    return jsonify(get_config())


@app.route("/api/connection/status")
def connection_status():
    """Check connection status."""
    global provider
    if provider is None:
        init_provider()

    try:
        access_token = os.getenv("KITE_ACCESS_TOKEN", "")
        if not access_token:
            return jsonify({"connected": False, "user": None, "error": "No access token"})

        provider.kite.set_access_token(access_token)
        profile = provider.kite.profile()
        return jsonify({
            "connected": True,
            "user": profile["user_name"],
            "email": profile.get("email", ""),
        })
    except Exception as e:
        return jsonify({"connected": False, "user": None, "error": str(e)})


@app.route("/api/login/url")
def login_url():
    """Get Kite login URL."""
    api_key = os.getenv("KITE_API_KEY", "")
    url = f"https://kite.zerodha.com/connect/login?api_key={api_key}&v=3"
    return jsonify({"url": url})


@app.route("/api/login/token", methods=["POST"])
def login_token():
    """Generate access token from request token."""
    global provider
    data = request.json
    request_token = data.get("request_token", "").strip()

    # Extract token from URL if needed
    if "request_token=" in request_token:
        import re
        match = re.search(r'request_token=([^&]+)', request_token)
        if match:
            request_token = match.group(1)

    if not request_token:
        return jsonify({"success": False, "error": "No request token provided"})

    try:
        if provider is None:
            init_provider()

        api_secret = os.getenv("KITE_API_SECRET", "")
        session_data = provider.kite.generate_session(request_token, api_secret=api_secret)
        access_token = session_data["access_token"]

        # Save to .env
        set_key(str(ENV_FILE), "KITE_ACCESS_TOKEN", access_token)
        os.environ["KITE_ACCESS_TOKEN"] = access_token

        # Reinitialize provider
        provider.kite.set_access_token(access_token)
        profile = provider.kite.profile()

        return jsonify({
            "success": True,
            "user": profile["user_name"],
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/market/data")
def market_data():
    """Get current market data."""
    global provider, tracker, last_data

    if provider is None:
        init_provider()

    try:
        # Check if connected
        access_token = os.getenv("KITE_ACCESS_TOKEN", "")
        if not access_token:
            return jsonify({"error": "Not connected"})

        provider.kite.set_access_token(access_token)

        # Check market hours
        now = datetime.now()
        current_time = now.time()
        market_open = datetime.strptime(MARKET_CONFIG["market_open"], "%H:%M").time()
        market_close = datetime.strptime(MARKET_CONFIG["market_close"], "%H:%M").time()

        market_status = "open" if market_open <= current_time <= market_close else "closed"

        # Get strangle data
        data = provider.find_strangle()

        if not data:
            return jsonify({
                "market_status": market_status,
                "error": "Could not fetch strangle data"
            })

        # Update signal tracker
        signal_info = tracker.update_signal(data.straddle_price, data.straddle_vwap)

        config = get_config()
        total_qty = config["lot_size"] * config["lot_quantity"]
        total_premium = data.per_lot * config["lot_quantity"]

        last_data = {
            "timestamp": now.strftime("%H:%M:%S"),
            "market_status": market_status,
            "spot": data.spot,
            "synthetic_futures": data.synthetic_futures,
            "atm_strike": data.atm_strike,
            "straddle_price": data.straddle_price,
            "straddle_vwap": data.straddle_vwap,
            "vwap_diff": data.straddle_price - data.straddle_vwap,
            "expiry": str(data.expiry),
            "dte": data.dte,
            "call": {
                "strike": data.call_strike,
                "ltp": data.call_ltp,
                "iv": data.call_iv * 100,
                "delta": data.call_delta,
            },
            "put": {
                "strike": data.put_strike,
                "ltp": data.put_ltp,
                "iv": data.put_iv * 100,
                "delta": data.put_delta,
            },
            "total_premium": data.total_premium,
            "per_lot": data.per_lot,
            "width": data.width,
            "lots": config["lot_quantity"],
            "total_qty": total_qty,
            "total_premium_all_lots": total_premium,
            "signal": {
                "active": signal_info["signal_active"],
                "duration": signal_info["duration_seconds"],
                "required": signal_info["required_seconds"],
                "entry_ready": signal_info["entry_ready"],
                "current_window": signal_info["current_window"],
                "can_trade": signal_info["can_trade"],
                "morning_trades": signal_info["morning_trades"],
                "afternoon_trades": signal_info["afternoon_trades"],
            }
        }

        return jsonify(last_data)

    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/positions")
def positions():
    """Get current positions."""
    global provider

    if provider is None:
        init_provider()

    try:
        access_token = os.getenv("KITE_ACCESS_TOKEN", "")
        if not access_token:
            return jsonify({"error": "Not connected", "positions": []})

        provider.kite.set_access_token(access_token)
        pos_data = provider.get_positions()

        return jsonify(pos_data)

    except Exception as e:
        return jsonify({"error": str(e), "positions": []})


@app.route("/api/option/quote")
def option_quote():
    """Get quote for a specific option strike."""
    global provider

    if provider is None:
        init_provider()

    try:
        access_token = os.getenv("KITE_ACCESS_TOKEN", "")
        if not access_token:
            return jsonify({"error": "Not connected"})

        provider.kite.set_access_token(access_token)

        strike = int(request.args.get("strike", 0))
        option_type = request.args.get("type", "CE").upper()  # CE or PE
        expiry_str = request.args.get("expiry", "")

        if not strike or not expiry_str:
            return jsonify({"error": "Missing strike or expiry"})

        # Get the option quote
        from datetime import date as date_type
        expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()

        # Get trading symbol using provider method
        symbol = provider.get_trading_symbol(expiry, strike, option_type)

        if not symbol:
            return jsonify({"error": f"Instrument not found for strike {strike}"})

        # Get quote
        quote = provider.kite.quote([f"NFO:{symbol}"])
        quote_data = quote.get(f"NFO:{symbol}", {})
        ltp = quote_data.get("last_price", 0)

        # Calculate delta
        spot_quote = provider.kite.quote(["NSE:NIFTY 50"])
        spot = spot_quote.get("NSE:NIFTY 50", {}).get("last_price", 0)

        # Calculate delta using Black-Scholes
        from greeks.black_scholes import BlackScholesCalculator

        days_to_expiry = (expiry - datetime.now().date()).days
        time_to_expiry = max(days_to_expiry, 1) / 365.0

        # Use synthetic futures (approximate)
        synthetic_futures = spot * 1.001  # Small adjustment

        bs = BlackScholesCalculator(risk_free_rate=0.07, dividend_yield=0.0)

        # First calculate IV from the option price
        iv = bs.calculate_implied_volatility(
            S=synthetic_futures,
            K=strike,
            T=time_to_expiry,
            market_price=ltp,
            option_type=option_type
        )

        # Then calculate delta using the IV
        if option_type == "CE":
            delta = bs.calculate_call_delta(synthetic_futures, strike, time_to_expiry, iv)
        else:
            delta = bs.calculate_put_delta(synthetic_futures, strike, time_to_expiry, iv)

        return jsonify({
            "strike": strike,
            "type": option_type,
            "ltp": ltp,
            "delta": abs(delta) if delta else 0,
            "iv": iv * 100 if iv else 0,  # IV as percentage
            "symbol": symbol
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)})


@app.route("/api/trade/execute", methods=["POST"])
def execute_trade():
    """Execute a strangle trade."""
    global provider, tracker, last_data

    if provider is None:
        init_provider()

    try:
        access_token = os.getenv("KITE_ACCESS_TOKEN", "")
        if not access_token:
            return jsonify({"success": False, "error": "Not connected"})

        provider.kite.set_access_token(access_token)

        # Get current strangle data
        data = provider.find_strangle()
        if not data:
            return jsonify({"success": False, "error": "Could not fetch strangle data"})

        # Get signal info
        signal_info = tracker.update_signal(data.straddle_price, data.straddle_vwap)

        # Check for custom strikes from request
        req_data = request.json or {}
        call_strike = req_data.get("call_strike") or data.call_strike
        put_strike = req_data.get("put_strike") or data.put_strike

        # Place order with potentially custom strikes
        result = provider.place_strangle_order(
            expiry=data.expiry,
            call_strike=call_strike,
            put_strike=put_strike,
        )

        if result["success"]:
            # Record trade
            if signal_info["current_window"]:
                tracker.record_trade(signal_info["current_window"])

        return jsonify(result)

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/history")
def history():
    """Get trade history grouped by expiry including closed positions.

    Uses CSV-based persistence for history that works after market hours.
    Merges live Zerodha data with persisted CSV history.
    """
    global provider
    import re

    history_manager = get_history_manager()

    # First, try to get live data from Zerodha and sync to CSV
    live_expiry_data = {}
    zerodha_connected = False

    if provider is None:
        init_provider()

    try:
        access_token = os.getenv("KITE_ACCESS_TOKEN", "")
        if access_token:
            provider.kite.set_access_token(access_token)
            positions = provider.kite.positions()
            net_positions = positions.get('net', [])
            zerodha_connected = True

            # Filter NIFTY options
            nifty_positions = [p for p in net_positions if p['tradingsymbol'].startswith('NIFTY')]

            # Sync closed positions to CSV
            added = history_manager.update_from_positions(nifty_positions)
            if added > 0:
                print(f"Added {added} closed positions to history CSV")

            # Process live positions for current open P&L
            for pos in nifty_positions:
                symbol = pos['tradingsymbol']
                match = re.match(r'NIFTY(\d{2}[A-Z]{3}|\d{2}[A-Z]\d{2}|\d{5})', symbol)
                if not match:
                    continue

                expiry_key = match.group(1)

                # Format expiry nicely
                if len(expiry_key) == 5:
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

                    expiry_display = f"{day}-{month}-{year}"
                else:
                    expiry_display = expiry_key

                if expiry_key not in live_expiry_data:
                    live_expiry_data[expiry_key] = {
                        'expiry': expiry_display,
                        'booked': 0,
                        'open': 0,
                        'open_positions': 0,
                        'closed_positions': 0
                    }

                # Get P&L values
                realised = pos.get('realised', 0)
                unrealised = pos.get('unrealised', 0)
                pnl = pos.get('pnl', 0)

                if pos['quantity'] != 0:
                    # Open position
                    live_expiry_data[expiry_key]['open'] += unrealised
                    live_expiry_data[expiry_key]['booked'] += realised
                    live_expiry_data[expiry_key]['open_positions'] += 1
                else:
                    # Closed position - add to booked
                    live_expiry_data[expiry_key]['booked'] += pnl
                    live_expiry_data[expiry_key]['closed_positions'] += 1

    except Exception as e:
        print(f"Error fetching live positions: {e}")

    # Get persisted history from CSV
    csv_history = history_manager.get_history_by_expiry()

    # Merge live data with CSV history
    # Live data takes precedence for current day, CSV provides historical context
    merged_data = {}

    # Add CSV history first
    for expiry, data in csv_history.items():
        merged_data[expiry] = {
            'expiry': data['expiry'],
            'booked': data['booked'],
            'open': 0,
            'open_positions': 0,
            'closed_positions': data['closed_positions']
        }

    # Overlay live data (current open positions)
    for expiry_key, data in live_expiry_data.items():
        expiry_display = data['expiry']

        if expiry_display in merged_data:
            # Add live open P&L to existing entry
            merged_data[expiry_display]['open'] = data['open']
            merged_data[expiry_display]['open_positions'] = data['open_positions']
            # Update booked if we have realised profit from open positions today
            if data['booked'] > 0 and data['open_positions'] > 0:
                # This is realised profit from partial closing - handled separately
                pass
        else:
            # New expiry from live data
            merged_data[expiry_display] = data

    # Calculate totals
    total_booked = sum(e['booked'] for e in merged_data.values())
    total_open = sum(e['open'] for e in merged_data.values())

    # Format response - sort by expiry descending
    by_expiry = []
    for expiry, data in sorted(merged_data.items(), key=lambda x: x[0], reverse=True):
        # Only include if there's any P&L
        if data['booked'] != 0 or data['open'] != 0:
            by_expiry.append({
                'expiry': data['expiry'],
                'booked': data['booked'],
                'open': data['open'],
                'total_pnl': data['booked'] + data['open'],
                'open_positions': data['open_positions'],
                'closed_positions': data['closed_positions']
            })

    # Get manual profits
    manual_profits = history_manager.get_manual_profits()
    total_manual = sum(manual_profits.values())

    return jsonify({
        'booked_profit': total_booked,
        'open_pnl': total_open,
        'manual_profits': manual_profits,
        'total_manual': total_manual,
        'total': total_manual + total_booked + total_open,
        'by_expiry': by_expiry,
        'source': 'live+csv' if zerodha_connected else 'csv_only'
    })


@app.route("/api/history/add", methods=["POST"])
def add_history_entry():
    """Manually add a trade entry to history."""
    history_manager = get_history_manager()
    data = request.json

    trade_data = {
        'date': data.get('date', datetime.now().strftime('%Y-%m-%d')),
        'expiry': data.get('expiry', ''),
        'symbol': data.get('symbol', ''),
        'option_type': data.get('option_type', ''),
        'strike': data.get('strike', 0),
        'quantity': data.get('quantity', 0),
        'entry_price': data.get('entry_price', 0),
        'exit_price': data.get('exit_price', 0),
        'pnl': data.get('pnl', 0),
        'status': 'closed'
    }

    success = history_manager.add_trade(trade_data)
    return jsonify({"success": success})


@app.route("/api/history/sync", methods=["POST"])
def sync_history():
    """Force sync of closed positions from Zerodha to CSV."""
    global provider
    history_manager = get_history_manager()

    if provider is None:
        init_provider()

    try:
        access_token = os.getenv("KITE_ACCESS_TOKEN", "")
        if not access_token:
            return jsonify({"success": False, "error": "Not connected"})

        provider.kite.set_access_token(access_token)
        positions = provider.kite.positions()
        net_positions = positions.get('net', [])

        nifty_positions = [p for p in net_positions if p['tradingsymbol'].startswith('NIFTY')]
        added = history_manager.update_from_positions(nifty_positions)

        return jsonify({
            "success": True,
            "added": added,
            "message": f"Synced {added} closed positions to history"
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/history/manual", methods=["GET"])
def get_manual_profits():
    """Get all manual profits per expiry."""
    history_manager = get_history_manager()
    return jsonify(history_manager.get_manual_profits())


@app.route("/api/history/manual", methods=["POST"])
def set_manual_profit():
    """Set manual profit for an expiry."""
    history_manager = get_history_manager()
    data = request.json
    expiry = data.get("expiry")
    profit = float(data.get("profit", 0))

    if not expiry:
        return jsonify({"success": False, "error": "Expiry required"})

    success = history_manager.set_manual_profit(expiry, profit)
    return jsonify({"success": success})


@app.route("/api/settings", methods=["POST"])
def update_settings():
    """Update settings."""
    data = request.json

    if "paper_trading" in data:
        value = "true" if data["paper_trading"] else "false"
        set_key(str(ENV_FILE), "PAPER_TRADING", value)
        os.environ["PAPER_TRADING"] = value

    if "lot_quantity" in data:
        value = str(int(data["lot_quantity"]))
        set_key(str(ENV_FILE), "LOT_QUANTITY", value)
        os.environ["LOT_QUANTITY"] = value

    return jsonify({"success": True})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8080)
