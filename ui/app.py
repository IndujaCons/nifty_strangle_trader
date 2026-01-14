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
import requests
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, jsonify, request, redirect
from dotenv import load_dotenv, set_key
from pathlib import Path

from data.kite_data_provider import KiteDataProvider
from data.trade_history import get_history_manager
from data.pcr_history import get_pcr_manager
from core.signal_tracker import SignalTracker
from config.settings import NIFTY_CONFIG, MARKET_CONFIG, TRADING_WINDOWS, STRATEGY_CONFIG

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

# PCR cache
pcr_cache = {"pcr": None, "timestamp": 0, "max_pain": None}


def fetch_pcr_from_zerodha(kite_provider, expiry_date=None):
    """Fetch PCR and max pain from Zerodha option chain."""
    global pcr_cache

    # Return cached value if less than 1 minute old
    if time.time() - pcr_cache["timestamp"] < 60 and pcr_cache["pcr"] is not None:
        return pcr_cache

    try:
        if kite_provider is None:
            return pcr_cache

        # Get spot price for ATM calculation
        spot_quote = kite_provider.kite.quote(["NSE:NIFTY 50"])
        spot = spot_quote.get("NSE:NIFTY 50", {}).get("last_price", 0)
        if spot == 0:
            return pcr_cache

        atm_strike = round(spot / 50) * 50

        # Get expiry if not provided
        if expiry_date is None:
            expiry_date = kite_provider.get_target_expiry()

        if expiry_date is None:
            return pcr_cache

        # Get instruments for this expiry
        instruments = kite_provider.kite.instruments("NFO")

        # Ensure expiry_date is a date object for comparison
        from datetime import date as date_class
        if isinstance(expiry_date, str):
            expiry_date = date_class.fromisoformat(expiry_date)

        nifty_options = []
        for i in instruments:
            if i['name'] != 'NIFTY':
                continue
            if i['instrument_type'] not in ['CE', 'PE']:
                continue
            # Handle both datetime and date expiry formats from Kite
            inst_expiry = i['expiry'].date() if hasattr(i['expiry'], 'date') else i['expiry']
            if inst_expiry == expiry_date:
                nifty_options.append(i)

        if not nifty_options:
            print(f"PCR: No options found for expiry {expiry_date}")
            return pcr_cache

        # Filter strikes around ATM (+/- 1500 points = 30 strikes each side)
        strike_range = 1500
        relevant_options = [
            i for i in nifty_options
            if atm_strike - strike_range <= i['strike'] <= atm_strike + strike_range
        ]

        # Build symbols for quote request (max 500 at a time)
        symbols = [f"NFO:{i['tradingsymbol']}" for i in relevant_options]

        # Fetch quotes in batches if needed
        all_quotes = {}
        batch_size = 200
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            quotes = kite_provider.kite.quote(batch)
            all_quotes.update(quotes)

        # Calculate OI totals
        total_ce_oi = 0
        total_pe_oi = 0
        strike_oi = {}

        for opt in relevant_options:
            symbol = f"NFO:{opt['tradingsymbol']}"
            quote = all_quotes.get(symbol, {})
            oi = quote.get('oi', 0)
            strike = opt['strike']

            if opt['instrument_type'] == 'CE':
                total_ce_oi += oi
            else:
                total_pe_oi += oi

            # Accumulate OI per strike for max pain
            if strike not in strike_oi:
                strike_oi[strike] = 0
            strike_oi[strike] += oi

        # Calculate PCR
        pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 0

        # Calculate max pain (strike with highest total OI)
        max_pain = max(strike_oi, key=strike_oi.get) if strike_oi else 0

        pcr_cache = {
            "pcr": pcr,
            "max_pain": max_pain,
            "ce_oi": total_ce_oi,
            "pe_oi": total_pe_oi,
            "timestamp": time.time()
        }
        print(f"PCR: {pcr}, Max Pain: {max_pain}, CE OI: {total_ce_oi:,}, PE OI: {total_pe_oi:,}")
        return pcr_cache

    except Exception as e:
        print(f"Error fetching PCR from Zerodha: {e}")

    return pcr_cache


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

        # Get available margin (equity.net = Available Margin in Zerodha)
        available_margin = 0
        try:
            margins = provider.kite.margins()
            equity = margins.get("equity", {})
            available_margin = equity.get("net", 0)
        except:
            pass

        return jsonify({
            "connected": True,
            "user": profile["user_name"],
            "email": profile.get("email", ""),
            "available_margin": available_margin,
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


@app.route("/api/expiries")
def get_expiries():
    """Get available expiries for dropdown selection."""
    global provider
    import re
    from datetime import date as date_class

    if provider is None:
        init_provider()

    try:
        access_token = os.getenv("KITE_ACCESS_TOKEN", "")
        if not access_token:
            return jsonify({"expiries": []})

        provider.kite.set_access_token(access_token)

        # Get expiries from open positions
        position_expiries = []
        try:
            positions = provider.kite.positions()
            net_positions = positions.get('net', [])
            for pos in net_positions:
                if pos['tradingsymbol'].startswith('NIFTY') and pos['quantity'] != 0:
                    # Extract expiry from symbol like NIFTY26113 or NIFTY26JAN
                    symbol = pos['tradingsymbol']
                    match = re.match(r'NIFTY(\d{2})(\d|[A-Z])(\d{2})', symbol)
                    if match:
                        yy, m, dd = match.groups()
                        year = 2000 + int(yy)
                        # Month mapping
                        month_map = {'1': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6,
                                    '7': 7, '8': 8, '9': 9, 'O': 10, 'N': 11, 'D': 12}
                        month = month_map.get(m, int(m) if m.isdigit() else 1)
                        try:
                            exp_date = date_class(year, month, int(dd))
                            if exp_date not in position_expiries:
                                position_expiries.append(exp_date)
                        except:
                            pass
        except Exception as e:
            print(f"Error getting position expiries: {e}")

        expiries = provider.get_available_expiries(count=2, position_expiries=position_expiries)
        return jsonify({"expiries": expiries})
    except Exception as e:
        return jsonify({"expiries": [], "error": str(e)})


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
        premarket_open = datetime.strptime("09:00", "%H:%M").time()
        market_open = datetime.strptime(MARKET_CONFIG["market_open"], "%H:%M").time()
        market_close = datetime.strptime(MARKET_CONFIG["market_close"], "%H:%M").time()

        # Determine market status
        if market_open <= current_time <= market_close:
            market_status = "open"
        elif premarket_open <= current_time < market_open:
            market_status = "pre-market"
        else:
            market_status = "closed"

        # Pre-market: Only fetch spot price, keep other fields blank
        if market_status == "pre-market":
            try:
                spot = provider.get_spot_price()
                return jsonify({
                    "timestamp": now.strftime("%H:%M:%S"),
                    "market_status": market_status,
                    "spot": spot,
                    "synthetic_futures": None,
                    "atm_strike": None,
                    "straddle_price": None,
                    "straddle_vwap": None,
                    "vwap_diff": None,
                    "expiry": None,
                    "dte": None,
                    "call": None,
                    "put": None,
                    "total_premium": None,
                    "per_lot": None,
                    "width": None,
                    "lots": None,
                    "total_qty": None,
                    "total_premium_all_lots": None,
                    "margin_required": None,
                    "pcr": None,
                    "max_pain": None,
                    "sip_alert": False,
                    "signal": {
                        "active": False,
                        "duration": 0,
                        "required": 300,
                        "entry_ready": False,
                        "current_window": "pre-market",
                        "can_trade": False,
                        "morning_trades": 0,
                        "afternoon_trades": 0,
                    }
                })
            except Exception as e:
                return jsonify({"market_status": market_status, "error": str(e)})

        # Get selected expiry from query param (if provided)
        selected_expiry = request.args.get('expiry')
        expiry_date = None
        if selected_expiry:
            from datetime import date as date_class
            expiry_date = date_class.fromisoformat(selected_expiry)

        # Get strangle data
        data = provider.find_strangle(expiry=expiry_date)

        if not data:
            return jsonify({
                "market_status": market_status,
                "error": "Could not fetch strangle data"
            })

        # Update signal tracker (skip if requested - e.g., when only updating margin)
        skip_signal = request.args.get('skip_signal', 'false').lower() == 'true'
        if skip_signal:
            # Return current signal state without updating
            signal_info = {
                "signal_active": tracker.signal_state.is_active,
                "duration_seconds": (datetime.now() - tracker.signal_state.signal_start).total_seconds() if tracker.signal_state.signal_start else 0,
                "required_seconds": STRATEGY_CONFIG["signal_duration_seconds"],
                "current_window": tracker._get_current_window(datetime.now()),
                "can_trade": tracker._get_current_window(datetime.now()) is not None and tracker._can_trade_in_window(tracker._get_current_window(datetime.now()) or ""),
                "entry_ready": False,  # Will be recalculated below
                "morning_trades": tracker.window_state.morning_trades,
                "afternoon_trades": tracker.window_state.afternoon_trades,
            }
            # Recalculate entry_ready
            required_duration = STRATEGY_CONFIG["signal_duration_seconds"]
            signal_info["entry_ready"] = (
                signal_info["signal_active"] and
                signal_info["duration_seconds"] >= required_duration and
                signal_info["can_trade"]
            )
        else:
            signal_info = tracker.update_signal(data.straddle_price, data.straddle_vwap)

        config = get_config()
        total_qty = config["lot_size"] * config["lot_quantity"]
        total_premium = data.per_lot * config["lot_quantity"]

        # Calculate margin required using Kite's margins API
        total_margin = 0
        try:
            ce_symbol = provider.get_trading_symbol(data.expiry, data.call_strike, "CE")
            pe_symbol = provider.get_trading_symbol(data.expiry, data.put_strike, "PE")

            margin_params = [
                {
                    "exchange": "NFO",
                    "tradingsymbol": ce_symbol,
                    "transaction_type": "SELL",
                    "variety": "regular",
                    "product": "NRML",
                    "order_type": "MARKET",
                    "quantity": total_qty
                },
                {
                    "exchange": "NFO",
                    "tradingsymbol": pe_symbol,
                    "transaction_type": "SELL",
                    "variety": "regular",
                    "product": "NRML",
                    "order_type": "MARKET",
                    "quantity": total_qty
                }
            ]

            # Try basket_margins first (for combined margin with span benefit)
            if hasattr(provider.kite, 'basket_margins'):
                margin_response = provider.kite.basket_margins(margin_params)
                total_margin = margin_response.get('final', {}).get('total', 0)
            else:
                # Fall back to direct API call for basket margins
                import requests
                headers = {
                    "Authorization": f"token {provider.api_key}:{provider.kite.access_token}",
                    "Content-Type": "application/json"
                }
                response = requests.post(
                    "https://api.kite.trade/margins/basket",
                    json=margin_params,
                    headers=headers
                )
                if response.status_code == 200:
                    result = response.json()
                    final_data = result.get('data', {}).get('final', {})
                    total_margin = final_data.get('total', 0)
                    print(f"Basket margin: ₹{total_margin:,.0f}")
        except Exception as e:
            print(f"Margin calculation error: {e}")

        # Fetch PCR from Zerodha
        pcr_data = fetch_pcr_from_zerodha(provider, data.expiry)
        pcr_value = pcr_data.get("pcr")

        # PCR History Manager - SIP alert and auto-save
        pcr_manager = get_pcr_manager()
        current_time_str = now.strftime("%H:%M")

        # Check if SIP alert should be shown (12:30-12:55 PM, PCR < 0.7)
        sip_alert = False
        if pcr_value is not None and pcr_manager.should_show_sip_alert(pcr_value, threshold=0.7):
            sip_alert = True

        # Auto-save PCR at 3:25 PM (before market close)
        if "15:20" <= current_time_str <= "15:30":
            if pcr_value is not None and pcr_data.get("max_pain"):
                saved = pcr_manager.save_pcr(
                    pcr=pcr_value,
                    max_pain=pcr_data.get("max_pain", 0),
                    ce_oi=pcr_data.get("ce_oi", 0),
                    pe_oi=pcr_data.get("pe_oi", 0),
                    spot=data.spot,
                    expiry=data.expiry
                )
                if saved:
                    print(f"PCR saved to history: {pcr_value}")

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
            "margin_required": total_margin,
            "pcr": pcr_value,
            "max_pain": pcr_data.get("max_pain"),
            "sip_alert": sip_alert,
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


@app.route("/api/sip-alert/dismiss", methods=["POST"])
def dismiss_sip_alert():
    """Mark SIP alert as shown for today."""
    pcr_manager = get_pcr_manager()
    pcr_manager.mark_alert_shown()
    return jsonify({"success": True})


@app.route("/api/pcr/history")
def pcr_history():
    """Get PCR history."""
    pcr_manager = get_pcr_manager()
    history = pcr_manager.get_history(days=30)
    return jsonify({"history": history})


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

        response = jsonify(pos_data)
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

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

            # Fetch live quotes for open positions to calculate real-time P&L
            open_symbols = [f"NFO:{p['tradingsymbol']}" for p in nifty_positions if p['quantity'] != 0]
            live_quotes = {}
            if open_symbols:
                try:
                    quotes = provider.kite.quote(open_symbols)
                    for key, val in quotes.items():
                        symbol = key.replace("NFO:", "")
                        live_quotes[symbol] = val.get('last_price', 0)
                except Exception as e:
                    print(f"Error fetching live quotes: {e}")

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
                        'closed_positions': 0,
                        'max_profit': 0
                    }

                # Get P&L values
                realised = pos.get('realised', 0)
                pnl = pos.get('pnl', 0)
                quantity = pos['quantity']

                if quantity != 0:
                    # Open position - calculate P&L using live quotes
                    avg_price = pos.get('average_price', 0)
                    current_ltp = live_quotes.get(symbol, pos.get('last_price', avg_price))

                    # For short positions (qty < 0): profit = (avg - ltp) * abs(qty)
                    # For long positions (qty > 0): profit = (ltp - avg) * qty
                    if quantity < 0:
                        calculated_pnl = (avg_price - current_ltp) * abs(quantity)
                    else:
                        calculated_pnl = (current_ltp - avg_price) * quantity

                    live_expiry_data[expiry_key]['open'] += calculated_pnl
                    live_expiry_data[expiry_key]['booked'] += realised
                    live_expiry_data[expiry_key]['open_positions'] += 1
                    # Max profit for sold options = premium collected = average_price × abs(quantity)
                    if quantity < 0:  # Sold position
                        max_profit_for_position = avg_price * abs(quantity)
                        live_expiry_data[expiry_key]['max_profit'] += max_profit_for_position
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
            'closed_positions': data['closed_positions'],
            'max_profit': 0
        }

    # Overlay live data (current open positions)
    for expiry_key, data in live_expiry_data.items():
        expiry_display = data['expiry']

        if expiry_display in merged_data:
            # Add live open P&L to existing entry
            merged_data[expiry_display]['open'] = data['open']
            merged_data[expiry_display]['open_positions'] = data['open_positions']
            merged_data[expiry_display]['max_profit'] = data['max_profit']
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
    total_max_profit = sum(e['max_profit'] for e in merged_data.values())

    # Get manual profits first (needed for profit % calculation)
    manual_profits = history_manager.get_manual_profits()
    total_manual = sum(manual_profits.values())

    # Format response - sort by expiry descending
    by_expiry = []
    for expiry, data in sorted(merged_data.items(), key=lambda x: x[0], reverse=True):
        # Only include if there's any P&L or max_profit
        if data['booked'] != 0 or data['open'] != 0 or data['max_profit'] != 0:
            manual_val = manual_profits.get(data['expiry'], 0)
            # Max profit = open positions max + booked + manual
            total_max_profit_expiry = data['max_profit'] + data['booked'] + manual_val
            # Current P&L = booked + open + manual
            current_pnl = data['booked'] + data['open'] + manual_val
            # Profit percentage
            profit_pct = (current_pnl / total_max_profit_expiry * 100) if total_max_profit_expiry > 0 else 0
            # Trigger at 50%
            exit_triggered = profit_pct >= 50 and data['open_positions'] > 0

            by_expiry.append({
                'expiry': data['expiry'],
                'booked': data['booked'],
                'open': data['open'],
                'total_pnl': data['booked'] + data['open'],
                'open_positions': data['open_positions'],
                'closed_positions': data['closed_positions'],
                'max_profit': data['max_profit'],
                'total_max_profit': total_max_profit_expiry,
                'current_pnl': current_pnl,
                'profit_pct': round(profit_pct, 1),
                'exit_triggered': exit_triggered
            })

    response = jsonify({
        'booked_profit': total_booked,
        'open_pnl': total_open,
        'max_profit': total_max_profit,
        'manual_profits': manual_profits,
        'total_manual': total_manual,
        'total': total_manual + total_booked + total_open,
        'by_expiry': by_expiry,
        'source': 'live+csv' if zerodha_connected else 'csv_only'
    })
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


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


@app.route("/api/positions/exit-expiry", methods=["POST"])
def exit_expiry_positions():
    """Exit all open positions for a given expiry."""
    global provider
    import re

    data = request.json
    expiry = data.get("expiry")  # Format: "20-01-2026"

    if not expiry:
        return jsonify({"success": False, "error": "Expiry required"})

    if provider is None:
        init_provider()

    try:
        access_token = os.getenv("KITE_ACCESS_TOKEN", "")
        if not access_token:
            return jsonify({"success": False, "error": "Not logged in"})

        provider.kite.set_access_token(access_token)
        positions = provider.kite.positions()
        net_positions = positions.get('net', [])

        # Convert expiry format "20-01-2026" to match symbol pattern
        # Symbol format: NIFTY26120 (YY M DD) or NIFTY26JAN (YY MON DD)
        expiry_parts = expiry.split('-')
        if len(expiry_parts) == 3:
            day, month, year = expiry_parts
            # Create pattern like "26120" or "261" for matching
            yy = year[2:4]
            # Month mapping for weekly expiries
            month_map = {'01': '1', '02': '2', '03': '3', '04': '4', '05': '5', '06': '6',
                        '07': '7', '08': '8', '09': '9', '10': 'O', '11': 'N', '12': 'D'}
            m = month_map.get(month, month)
            expiry_pattern = f"{yy}{m}{day}"

        orders_placed = []
        errors = []

        for pos in net_positions:
            symbol = pos['tradingsymbol']
            qty = pos['quantity']

            # Skip if not NIFTY option or no open position
            if not symbol.startswith('NIFTY') or qty == 0:
                continue

            # Check if this position matches the expiry
            match = re.match(r'NIFTY(\d{2}[A-Z0-9]\d{2})', symbol)
            if not match:
                continue

            pos_expiry = match.group(1)
            if pos_expiry != expiry_pattern:
                continue

            # Place exit order (BUY to close SELL, or SELL to close BUY)
            transaction_type = "BUY" if qty < 0 else "SELL"
            exit_qty = abs(qty)

            try:
                # Check if paper trading
                paper_trading = os.getenv("PAPER_TRADING", "false").lower() == "true"

                if paper_trading:
                    orders_placed.append({
                        "symbol": symbol,
                        "qty": exit_qty,
                        "type": transaction_type,
                        "status": "PAPER_TRADE"
                    })
                else:
                    order_id = provider.kite.place_order(
                        variety="regular",
                        exchange="NFO",
                        tradingsymbol=symbol,
                        transaction_type=transaction_type,
                        quantity=exit_qty,
                        product="NRML",
                        order_type="MARKET"
                    )
                    orders_placed.append({
                        "symbol": symbol,
                        "qty": exit_qty,
                        "type": transaction_type,
                        "order_id": order_id
                    })
            except Exception as e:
                errors.append({"symbol": symbol, "error": str(e)})

        return jsonify({
            "success": len(errors) == 0,
            "expiry": expiry,
            "orders_placed": orders_placed,
            "errors": errors,
            "message": f"Placed {len(orders_placed)} exit orders" + (f", {len(errors)} errors" if errors else "")
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


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
