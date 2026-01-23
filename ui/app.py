#!/usr/bin/env python3
"""
Flask Web UI for NIFTY Strangle Trading System

Run with: python ui/app.py
Open: http://localhost:5000
"""
import sys
import os
import time
import requests
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv, set_key
from pathlib import Path

from data.kite_data_provider import KiteDataProvider
from data.trade_history import get_history_manager
from data.pcr_history import get_pcr_manager
from core.signal_tracker import SignalTracker
from config.settings import NIFTY_CONFIG, MARKET_CONFIG, STRATEGY_CONFIG

app = Flask(__name__)

# Global state
ENV_FILE = Path(__file__).parent.parent / ".env"
load_dotenv(ENV_FILE)

provider = None
tracker = SignalTracker()
last_data = {}
auto_sync_date = None  # Track last auto-sync date

# Auto-trade tracking (prevents duplicate executions)
auto_trade_state = {
    "last_entry_date": None,      # Date of last auto-entry
    "last_entry_window": None,    # Window of last auto-entry (morning/afternoon)
    "last_entry_expiry": None,    # Expiry of last auto-entry
    "last_exit_date": None,       # Date of last auto-exit
    "last_exit_expiry": None,     # Expiry of last auto-exit
    "entry_premium": 0,           # Premium collected at entry (for 50% target)
}

# PCR cache
pcr_cache = {"pcr": None, "timestamp": 0, "max_pain": None}

# OI Tracker for ATM straddle analysis
class OITracker:
    """Track OI and price changes for multiple strikes around ATM to generate directional signals.

    Tracks 3 strikes around 100s ATM (ATM-100, ATM, ATM+100) to maintain OI history
    even when ATM moves. This prevents losing data on volatile days.
    """

    def __init__(self, max_history_minutes=120):
        self.history = {}  # Dict keyed by strike: {25200: [{timestamp, ce_oi, ce_price, pe_oi, pe_price}, ...], ...}
        self.max_history = max_history_minutes * 60  # Convert to seconds

    def add_data(self, strike, ce_oi, ce_price, pe_oi, pe_price):
        """Add data for a specific strike."""
        now = time.time()
        if strike not in self.history:
            self.history[strike] = []
        self.history[strike].append({
            "timestamp": now,
            "ce_oi": ce_oi,
            "ce_price": ce_price,
            "pe_oi": pe_oi,
            "pe_price": pe_price
        })
        # Cleanup old data for this strike
        cutoff = now - self.max_history
        self.history[strike] = [h for h in self.history[strike] if h["timestamp"] > cutoff]

    def get_analysis(self, atm_strike, interval_minutes=5):
        """Get OI change analysis for specific ATM strike (rounded to 100)."""
        # Round ATM to nearest 100 for stable tracking
        atm_100 = round(atm_strike / 100) * 100

        if atm_100 not in self.history or len(self.history[atm_100]) < 2:
            print(f"[OI Analysis] Not enough data for strike {atm_100}: {len(self.history.get(atm_100, []))} points")
            return {"error": f"Collecting data for ATM {atm_100}..."}

        strike_history = self.history[atm_100]
        now = time.time()
        interval_seconds = interval_minutes * 60
        cutoff = now - interval_seconds

        # Find the oldest data point within the interval
        old_data = None
        for h in strike_history:
            if h["timestamp"] >= cutoff:
                old_data = h
                break

        if not old_data:
            # Use oldest available if no data in interval
            old_data = strike_history[0]

        current = strike_history[-1]

        # Calculate changes
        ce_oi_change = current["ce_oi"] - old_data["ce_oi"]
        pe_oi_change = current["pe_oi"] - old_data["pe_oi"]
        ce_price_change = current["ce_price"] - old_data["ce_price"]
        pe_price_change = current["pe_price"] - old_data["pe_price"]

        # Calculate percentage changes
        ce_oi_pct = (ce_oi_change / old_data["ce_oi"] * 100) if old_data["ce_oi"] > 0 else 0
        pe_oi_pct = (pe_oi_change / old_data["pe_oi"] * 100) if old_data["pe_oi"] > 0 else 0
        ce_price_pct = (ce_price_change / old_data["ce_price"] * 100) if old_data["ce_price"] > 0 else 0
        pe_price_pct = (pe_price_change / old_data["pe_price"] * 100) if old_data["pe_price"] > 0 else 0

        # Determine signals (10% threshold for significant OI change)
        oi_threshold = 10
        ce_oi_up = ce_oi_pct > oi_threshold
        ce_oi_down = ce_oi_pct < -oi_threshold
        pe_oi_up = pe_oi_pct > oi_threshold
        pe_oi_down = pe_oi_pct < -oi_threshold
        ce_price_up = ce_price_change > 0
        pe_price_up = pe_price_change > 0

        # Generate signal based on combinations
        signal = "NEUTRAL"
        confidence = "Low"
        reason = ""

        # Strong Bullish: CE price up + OI up, PE price down + OI up (put writing)
        if ce_price_up and ce_oi_up and not pe_price_up and pe_oi_up:
            signal = "BULLISH"
            confidence = "High"
            reason = "CE longs building + PE writing"
        # Strong Bearish: CE price down + OI up, PE price up + OI up
        elif not ce_price_up and ce_oi_up and pe_price_up and pe_oi_up:
            signal = "BEARISH"
            confidence = "High"
            reason = "CE shorts building + PE longs building"
        # Bullish: CE price up + OI up
        elif ce_price_up and ce_oi_up:
            signal = "BULLISH"
            confidence = "Medium"
            reason = "New CE longs entering"
        # Bullish: PE price down + OI up (put writing)
        elif not pe_price_up and pe_oi_up:
            signal = "BULLISH"
            confidence = "Medium"
            reason = "Put writing (PE shorts)"
        # Bearish: PE price up + OI up
        elif pe_price_up and pe_oi_up:
            signal = "BEARISH"
            confidence = "Medium"
            reason = "New PE longs entering"
        # Bearish: CE price down + OI up (call writing)
        elif not ce_price_up and ce_oi_up:
            signal = "BEARISH"
            confidence = "Medium"
            reason = "Call writing (CE shorts)"
        # Mild signals based on unwinding
        elif ce_price_up and ce_oi_down:
            signal = "BULLISH"
            confidence = "Low"
            reason = "CE short covering"
        elif pe_price_up and pe_oi_down:
            signal = "BEARISH"
            confidence = "Low"
            reason = "PE short covering"
        elif not ce_price_up and ce_oi_down:
            signal = "BEARISH"
            confidence = "Low"
            reason = "CE long unwinding"
        elif not pe_price_up and pe_oi_down:
            signal = "BULLISH"
            confidence = "Low"
            reason = "PE long unwinding"

        return {
            "atm_strike": atm_100,
            "interval_minutes": interval_minutes,
            "ce_oi_old": old_data["ce_oi"],
            "ce_oi_new": current["ce_oi"],
            "ce_oi_change": ce_oi_change,
            "ce_oi_pct": round(ce_oi_pct, 1),
            "ce_price_old": old_data["ce_price"],
            "ce_price_new": current["ce_price"],
            "ce_price_change": round(ce_price_change, 2),
            "ce_price_pct": round(ce_price_pct, 1),
            "pe_oi_old": old_data["pe_oi"],
            "pe_oi_new": current["pe_oi"],
            "pe_oi_change": pe_oi_change,
            "pe_oi_pct": round(pe_oi_pct, 1),
            "pe_price_old": old_data["pe_price"],
            "pe_price_new": current["pe_price"],
            "pe_price_change": round(pe_price_change, 2),
            "pe_price_pct": round(pe_price_pct, 1),
            "signal": signal,
            "confidence": confidence,
            "reason": reason,
            "data_age_seconds": int(now - old_data["timestamp"])
        }

oi_tracker = OITracker()


def format_expiry_key(expiry_key: str) -> str:
    """Format expiry key to display format (DD-MM-YYYY)."""
    import calendar

    month_map = {
        'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04',
        'MAY': '05', 'JUN': '06', 'JUL': '07', 'AUG': '08',
        'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
    }

    # Format: YYMMMDD (e.g., 26JAN27 = 27-01-2026) - weekly with day
    if len(expiry_key) == 7 and expiry_key[2:5].isalpha():
        year = f"20{expiry_key[:2]}"
        month = month_map.get(expiry_key[2:5].upper(), '01')
        day = expiry_key[5:7]
        return f"{day}-{month}-{year}"

    # Format: YYMMM (e.g., 26JAN = 27-01-2026) - monthly, find last Tuesday
    if len(expiry_key) == 5 and expiry_key[2:5].isalpha():
        year = f"20{expiry_key[:2]}"
        month = month_map.get(expiry_key[2:5].upper(), '01')
        year_num = int(year)
        month_num = int(month)
        last_day = calendar.monthrange(year_num, month_num)[1]
        # Find last Tuesday (NSE changed from Thursday to Tuesday)
        d = date(year_num, month_num, last_day)
        while d.weekday() != 1:  # Tuesday
            d = d.replace(day=d.day - 1)
        return f"{d.day:02d}-{month}-{year}"

    # Format: YYMDD (e.g., 26127 = 27-01-2026) - weekly compact
    if len(expiry_key) == 5 and expiry_key[:2].isdigit():
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
            month = "01"
        return f"{day}-{month}-{year}"

    return expiry_key


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
        if isinstance(expiry_date, str):
            expiry_date = date.fromisoformat(expiry_date)

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

        # Calculate OI totals and track data for multiple strikes (OI analysis)
        total_ce_oi = 0
        total_pe_oi = 0

        # Round ATM to nearest 100 for OI tracking (more stable, better liquidity)
        atm_100 = round(atm_strike / 100) * 100
        # Track 3 strikes: ATM-100, ATM, ATM+100
        tracked_strikes = [atm_100 - 100, atm_100, atm_100 + 100]
        strike_data = {s: {"ce_oi": 0, "ce_price": 0, "pe_oi": 0, "pe_price": 0} for s in tracked_strikes}

        for opt in relevant_options:
            symbol = f"NFO:{opt['tradingsymbol']}"
            quote = all_quotes.get(symbol, {})
            oi = quote.get('oi', 0)
            ltp = quote.get('last_price', 0)
            strike = opt['strike']

            if opt['instrument_type'] == 'CE':
                total_ce_oi += oi
                # Track CE for monitored strikes
                if strike in tracked_strikes:
                    strike_data[strike]["ce_oi"] = oi
                    strike_data[strike]["ce_price"] = ltp
            else:
                total_pe_oi += oi
                # Track PE for monitored strikes
                if strike in tracked_strikes:
                    strike_data[strike]["pe_oi"] = oi
                    strike_data[strike]["pe_price"] = ltp

        # Update OI tracker for all 3 strikes
        tracked_count = 0
        for track_strike in tracked_strikes:
            data = strike_data[track_strike]
            if data["ce_oi"] > 0 and data["pe_oi"] > 0:
                oi_tracker.add_data(track_strike, data["ce_oi"], data["ce_price"], data["pe_oi"], data["pe_price"])
                tracked_count += 1
        if tracked_count > 0:
            print(f"[OI Tracker] Added: Strikes={tracked_strikes[0]}/{tracked_strikes[1]}/{tracked_strikes[2]} (100s ATM={atm_100}, actual ATM={atm_strike})")

        # Calculate PCR
        pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 0

        # Use 100s ATM data for cache (more stable reference)
        atm_100_data = strike_data[atm_100]
        pcr_cache = {
            "pcr": pcr,
            "ce_oi": total_ce_oi,
            "pe_oi": total_pe_oi,
            "atm_strike": atm_strike,
            "atm_100": atm_100,
            "atm_ce_oi": atm_100_data["ce_oi"],
            "atm_ce_price": atm_100_data["ce_price"],
            "atm_pe_oi": atm_100_data["pe_oi"],
            "atm_pe_price": atm_100_data["pe_price"],
            "timestamp": time.time()
        }
        print(f"PCR: {pcr}, 100s ATM: {atm_100} (actual: {atm_strike}), CE OI: {atm_100_data['ce_oi']:,} @ {atm_100_data['ce_price']}, PE OI: {atm_100_data['pe_oi']:,} @ {atm_100_data['pe_price']}")
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
        "auto_trade": os.getenv("AUTO_TRADE", "false").lower() == "true",
        "auto_exit": os.getenv("AUTO_EXIT", "true").lower() == "true",
        "exit_target_pct": int(float(os.getenv("EXIT_TARGET_PCT", "0.50")) * 100),
        "lot_quantity": int(os.getenv("LOT_QUANTITY", "1")),
        "lot_size": NIFTY_CONFIG["lot_size"],
        "decay_threshold": int(float(os.getenv("MOVE_DECAY_THRESHOLD", "0.60")) * 100),  # As percentage
        "target_delta": int(float(os.getenv("TARGET_DELTA", "0.07")) * 100),  # As percentage (7 = 0.07)
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

        # Get available and used margin from Zerodha
        available_margin = 0
        used_margin = 0
        try:
            margins = provider.kite.margins()
            equity = margins.get("equity", {})
            available_margin = equity.get("net", 0)
            # Used margin is the 'debits' field in utilised
            utilised = equity.get("utilised", {})
            used_margin = utilised.get("debits", 0)
        except:
            pass

        return jsonify({
            "connected": True,
            "user": profile["user_name"],
            "email": profile.get("email", ""),
            "available_margin": available_margin,
            "used_margin": used_margin,
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
                            exp_date = date(year, month, int(dd))
                            if exp_date not in position_expiries:
                                position_expiries.append(exp_date)
                        except:
                            pass
        except Exception as e:
            print(f"Error getting position expiries: {e}")

        expiries = provider.get_available_expiries(count=4, min_dte=0, position_expiries=position_expiries)
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

        # Pre-market or Closed: Only fetch spot price, keep other fields blank
        if market_status in ("pre-market", "closed"):
            try:
                spot = provider.get_spot_price()
                window_label = "pre-market" if market_status == "pre-market" else "closed"
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
                        "current_window": window_label,
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
            expiry_date = date.fromisoformat(selected_expiry)

        # Get target delta from config (stored as decimal like 0.07)
        target_delta = float(os.getenv("TARGET_DELTA", "0.07"))
        print(f"[Market Data] expiry={selected_expiry}, TARGET_DELTA={target_delta}")

        # Get strangle data with configurable delta
        data = provider.find_strangle(expiry=expiry_date, target_delta=target_delta)

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

        # Auto-entry: Execute trade when entry_ready and auto_trade enabled
        global auto_trade_state
        if (config.get("auto_trade") and
            signal_info.get("entry_ready") and
            not skip_signal):

            current_window = signal_info.get("current_window")
            today = date.today()

            # Check if we already auto-traded for this window today
            already_traded = (
                auto_trade_state["last_entry_date"] == today and
                auto_trade_state["last_entry_window"] == current_window
            )

            if not already_traded:
                try:
                    # Execute the trade
                    result = provider.place_strangle_order(
                        expiry=data.expiry,
                        call_strike=data.call_strike,
                        put_strike=data.put_strike,
                    )

                    if result.get("success"):
                        # Record trade and update tracking state
                        tracker.record_trade(current_window)
                        auto_trade_state["last_entry_date"] = today
                        auto_trade_state["last_entry_window"] = current_window
                        auto_trade_state["last_entry_expiry"] = str(data.expiry)
                        auto_trade_state["entry_premium"] = total_premium
                        print(f"[Auto-Trade] Entry executed: {data.call_strike}CE/{data.put_strike}PE, Premium: {total_premium:.2f}")
                    else:
                        print(f"[Auto-Trade] Entry failed: {result.get('error', 'Unknown error')}")
                except Exception as e:
                    print(f"[Auto-Trade] Entry error: {e}")

        # Auto-exit: Exit positions when profit target is reached (PER EXPIRY)
        # Works for ALL trades (manual or auto) based on actual position data
        if config.get("auto_exit") and not skip_signal:
            try:
                import re

                # Get current positions
                positions = provider.kite.positions()
                net_positions = positions.get('net', [])

                # Filter NIFTY options with open positions
                nifty_positions = [p for p in net_positions
                                   if p['tradingsymbol'].startswith('NIFTY') and p['quantity'] != 0]

                if nifty_positions:
                    # Group positions by expiry
                    # Symbol format: NIFTY2512023500CE -> expiry pattern is 251202 (YYMMDD for weekly)
                    expiry_groups = {}

                    for pos in nifty_positions:
                        symbol = pos['tradingsymbol']
                        # Extract expiry pattern from symbol (e.g., "25120" or "25JAN")
                        match = re.match(r'NIFTY(\d{2}[A-Z0-9]\d{2}|\d{2}[A-Z]{3})', symbol)
                        if match:
                            expiry_key = match.group(1)
                            if expiry_key not in expiry_groups:
                                expiry_groups[expiry_key] = []
                            expiry_groups[expiry_key].append(pos)

                    # Check each expiry separately
                    today = date.today()
                    exited_expiries = auto_trade_state.get("exited_expiries_today", set())

                    # Reset exited expiries if it's a new day
                    if auto_trade_state.get("last_exit_date") != today:
                        exited_expiries = set()
                        auto_trade_state["exited_expiries_today"] = exited_expiries

                    for expiry_key, positions_list in expiry_groups.items():
                        # Skip if already exited this expiry today
                        if expiry_key in exited_expiries:
                            continue

                        # Calculate collected premium and current value for this expiry
                        expiry_collected = 0
                        expiry_current_value = 0

                        for pos in positions_list:
                            qty = pos['quantity']
                            avg_price = pos.get('average_price', 0)
                            ltp = pos.get('last_price', 0)

                            if qty < 0:  # Short position (sold options)
                                expiry_collected += avg_price * abs(qty)
                                expiry_current_value += ltp * abs(qty)

                        if expiry_collected > 0:
                            # Profit = what we collected - what it costs to buy back
                            expiry_profit = expiry_collected - expiry_current_value
                            exit_pct = float(os.getenv("EXIT_TARGET_PCT", "0.50"))
                            profit_target = expiry_collected * exit_pct

                            if expiry_profit >= profit_target:
                                print(f"[Auto-Trade] Expiry {expiry_key}: {int(exit_pct * 100)}% target reached! Profit: {expiry_profit:.2f}, Target: {profit_target:.2f}")

                                orders_placed = []
                                paper_trading = os.getenv("PAPER_TRADING", "false").lower() == "true"

                                for pos in positions_list:
                                    symbol = pos['tradingsymbol']
                                    qty = pos['quantity']

                                    if qty == 0:
                                        continue

                                    transaction_type = "BUY" if qty < 0 else "SELL"
                                    exit_qty = abs(qty)

                                    if paper_trading:
                                        orders_placed.append({"symbol": symbol, "qty": exit_qty, "paper": True})
                                    else:
                                        order_id = provider.kite.place_order(
                                            variety="regular",
                                            exchange="NFO",
                                            tradingsymbol=symbol,
                                            transaction_type=transaction_type,
                                            quantity=exit_qty,
                                            order_type="MARKET",
                                            product="NRML"
                                        )
                                        orders_placed.append({"symbol": symbol, "order_id": order_id})

                                if orders_placed:
                                    exited_expiries.add(expiry_key)
                                    auto_trade_state["last_exit_date"] = today
                                    auto_trade_state["exited_expiries_today"] = exited_expiries
                                    print(f"[Auto-Trade] Expiry {expiry_key}: Exit complete, {len(orders_placed)} orders placed")

            except Exception as e:
                print(f"[Auto-Trade] Exit check error: {e}")

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
            if pcr_value is not None:
                saved = pcr_manager.save_pcr(
                    pcr=pcr_value,
                    max_pain=0,  # Deprecated
                    ce_oi=pcr_data.get("ce_oi", 0),
                    pe_oi=pcr_data.get("pe_oi", 0),
                    spot=data.spot,
                    expiry=data.expiry
                )
                if saved:
                    print(f"PCR saved to history: {pcr_value}")

        # Get OI analysis (default 5 min interval, can be overridden by query param)
        oi_interval = int(request.args.get('oi_interval', 5))
        oi_analysis = oi_tracker.get_analysis(atm_strike=data.atm_strike, interval_minutes=oi_interval)

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
            "oi_analysis": oi_analysis,
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

        # Auto-sync at 3:25 PM (backend-side, runs even if frontend is inactive)
        global auto_sync_date
        now = datetime.now()
        if now.hour == 15 and 25 <= now.minute <= 30 and auto_sync_date != date.today():
            try:
                history_manager = get_history_manager()
                positions = provider.kite.positions()
                net_positions = positions.get('net', [])
                nifty_positions = [p for p in net_positions if p['tradingsymbol'].startswith('NIFTY')]
                added = history_manager.update_from_positions(nifty_positions)
                auto_sync_date = date.today()
                if added > 0:
                    print(f"[Auto-sync] Synced {added} closed positions to history")
            except Exception as sync_err:
                print(f"[Auto-sync] Error: {sync_err}")

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

        # Get request data including expiry
        req_data = request.json or {}
        expiry_str = req_data.get("expiry")

        # Parse expiry from request (format: YYYY-MM-DD)
        expiry = None
        if expiry_str:
            try:
                expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            except ValueError:
                return jsonify({"success": False, "error": f"Invalid expiry format: {expiry_str}"})

        # Get target delta from config
        target_delta = float(os.getenv("TARGET_DELTA", "0.07"))

        # Get strangle data for the specified expiry with configurable delta
        data = provider.find_strangle(expiry=expiry, target_delta=target_delta)
        if not data:
            return jsonify({"success": False, "error": "Could not fetch strangle data"})

        # Get signal info
        signal_info = tracker.update_signal(data.straddle_price, data.straddle_vwap)

        # Check for custom strikes from request
        call_strike = req_data.get("call_strike") or data.call_strike
        put_strike = req_data.get("put_strike") or data.put_strike

        # Place order with specified expiry and potentially custom strikes
        result = provider.place_strangle_order(
            expiry=data.expiry,  # Use expiry from strangle data (validated)
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

                # Try different expiry patterns
                # Monthly YYMMM must be checked BEFORE weekly YYMMMDD to avoid false matches
                match = re.match(r'NIFTY(\d{2}[A-Z]{3})(\d{5,})(CE|PE)', symbol)  # Monthly YYMMM (26JAN)
                if not match:
                    match = re.match(r'NIFTY(\d{2}[A-Z]{3}\d{2})(\d{5,})(CE|PE)', symbol)  # Weekly YYMMMDD (26JAN27)
                if not match:
                    match = re.match(r'NIFTY(\d{2}[A-Z0-9]\d{2})(\d+)(CE|PE)', symbol)  # Weekly YYMDD (26120) - [A-Z0-9] for months 1-9 and O/N/D
                if not match:
                    continue

                expiry_key = match.group(1)
                expiry_display = format_expiry_key(expiry_key)

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
                'exit_triggered': exit_triggered,
                'status': 'open' if data['open_positions'] > 0 else 'closed'
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


@app.route("/api/position/move/preview", methods=["POST"])
def move_position_preview():
    """
    Preview move operation - get details of what will happen without executing.
    """
    global provider
    import re

    data = request.json
    symbol = data.get("symbol")

    if not symbol:
        return jsonify({"success": False, "error": "Symbol required"})

    if provider is None:
        init_provider()

    try:
        access_token = os.getenv("KITE_ACCESS_TOKEN", "")
        if not access_token:
            return jsonify({"success": False, "error": "Not logged in"})

        provider.kite.set_access_token(access_token)

        # Get current position details
        positions = provider.kite.positions()
        net_positions = positions.get('net', [])

        target_pos = None
        for pos in net_positions:
            if pos['tradingsymbol'] == symbol and pos['quantity'] != 0:
                target_pos = pos
                break

        if not target_pos:
            return jsonify({"success": False, "error": f"Position {symbol} not found"})

        qty = target_pos['quantity']
        abs_qty = abs(qty)
        avg_price = target_pos['average_price']

        # Get current LTP
        try:
            quote = provider.kite.quote([f"NFO:{symbol}"])
            current_ltp = quote.get(f"NFO:{symbol}", {}).get('last_price', 0)
        except:
            current_ltp = target_pos.get('last_price', 0)

        # Parse the symbol - try different formats
        # Monthly YYMMM must be checked BEFORE weekly YYMMMDD to avoid false matches
        # Format 1: NIFTY26JAN25000PE (monthly YYMMM)
        match = re.match(r'NIFTY(\d{2}[A-Z]{3})(\d{5,})(CE|PE)', symbol)
        # Format 2: NIFTY26JAN2725000PE (weekly YYMMMDD)
        if not match:
            match = re.match(r'NIFTY(\d{2}[A-Z]{3}\d{2})(\d{5,})(CE|PE)', symbol)
        # Format 3: NIFTY2612025000PE (weekly compact YYMDD)
        if not match:
            match = re.match(r'NIFTY(\d{2}[A-Z0-9]\d{2})(\d+)(CE|PE)', symbol)

        if not match:
            return jsonify({"success": False, "error": f"Cannot parse symbol: {symbol}"})

        expiry_code = match.group(1)
        old_strike = int(match.group(2))
        option_type = match.group(3)

        # Convert expiry code to date
        import calendar
        month_name_map = {'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
                         'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12}
        month_char_map = {'1': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6,
                         '7': 7, '8': 8, '9': 9, 'O': 10, 'N': 11, 'D': 12}

        if len(expiry_code) == 7 and expiry_code[2:5].isalpha():
            # YYMMMDD format (e.g., 26JAN27)
            yy = int(expiry_code[:2])
            mm = month_name_map.get(expiry_code[2:5].upper(), 1)
            dd = int(expiry_code[5:7])
            expiry_date = date(2000 + yy, mm, dd)
        elif len(expiry_code) == 5 and expiry_code[2:5].isalpha():
            # YYMMM format (e.g., 26JAN) - monthly, find last Tuesday
            yy = int(expiry_code[:2])
            mm = month_name_map.get(expiry_code[2:5].upper(), 1)
            last_day = calendar.monthrange(2000 + yy, mm)[1]
            d = date(2000 + yy, mm, last_day)
            while d.weekday() != 1:  # Tuesday (NSE changed from Thursday)
                d = d.replace(day=d.day - 1)
            expiry_date = d
        elif len(expiry_code) == 5:
            # YYMDD format (e.g., 26127)
            yy = int(expiry_code[:2])
            month_char = expiry_code[2]
            dd = int(expiry_code[3:5])
            mm = month_char_map.get(month_char, int(month_char) if month_char.isdigit() else 1)
            expiry_date = date(2000 + yy, mm, dd)
        else:
            return jsonify({"success": False, "error": f"Cannot parse expiry: {expiry_code}"})

        # Get target delta strike as default
        target_delta = float(os.getenv("TARGET_DELTA", "0.07"))
        strangle_data = provider.find_strangle(expiry=expiry_date, target_delta=target_delta)
        if not strangle_data:
            return jsonify({"success": False, "error": "Cannot fetch target delta strike data"})

        if option_type == "CE":
            default_strike = strangle_data.call_strike
        else:
            default_strike = strangle_data.put_strike

        # Check if custom target strike was provided
        target_strike = data.get("target_strike")
        if target_strike:
            new_strike = int(target_strike)
        else:
            new_strike = default_strike

        # Get LTP and delta for the target strike
        new_symbol = provider.get_trading_symbol(expiry_date, new_strike, option_type)
        if not new_symbol:
            return jsonify({"success": False, "error": f"Cannot find instrument for strike {new_strike}"})

        # Fetch LTP for the new strike
        try:
            new_quote = provider.kite.quote([f"NFO:{new_symbol}"])
            new_ltp = new_quote.get(f"NFO:{new_symbol}", {}).get('last_price', 0)
        except:
            new_ltp = 0

        # Calculate delta for the new strike
        try:
            spot = strangle_data.spot_price if strangle_data else 25000
            dte = (expiry_date - date.today()).days
            time_to_expiry = max(dte / 365.0, 0.001)
            from data.option_greeks import calculate_delta
            new_delta = calculate_delta(spot, new_strike, time_to_expiry, option_type)
        except:
            new_delta = 0.07 if new_strike == default_strike else 0

        return jsonify({
            "success": True,
            "current": {
                "symbol": symbol,
                "strike": old_strike,
                "option_type": option_type,
                "avg_price": avg_price,
                "ltp": current_ltp,
                "quantity": qty,
            },
            "new": {
                "symbol": new_symbol,
                "strike": new_strike,
                "option_type": option_type,
                "ltp": new_ltp,
                "delta": round(abs(new_delta), 4),
                "quantity": abs_qty,
            },
            "expiry": expiry_date.strftime("%d-%b-%Y"),
            "expiry_date": expiry_date.strftime("%Y-%m-%d"),
            "default_strike": default_strike,  # 7-delta strike for reference
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/position/move", methods=["POST"])
def move_position():
    """
    Move a decayed position to 7-delta strike.

    1. Square off the existing position
    2. Find the 7-delta strike for same expiry and option type
    3. Sell at the new 7-delta strike with same quantity
    """
    global provider
    import re

    data = request.json
    symbol = data.get("symbol")  # e.g., "NIFTY26120CE26000"

    if not symbol:
        return jsonify({"success": False, "error": "Symbol required"})

    if provider is None:
        init_provider()

    try:
        access_token = os.getenv("KITE_ACCESS_TOKEN", "")
        if not access_token:
            return jsonify({"success": False, "error": "Not logged in"})

        provider.kite.set_access_token(access_token)

        # Get current position details
        positions = provider.kite.positions()
        net_positions = positions.get('net', [])

        target_pos = None
        for pos in net_positions:
            if pos['tradingsymbol'] == symbol and pos['quantity'] != 0:
                target_pos = pos
                break

        if not target_pos:
            return jsonify({"success": False, "error": f"Position {symbol} not found or already closed"})

        qty = target_pos['quantity']
        abs_qty = abs(qty)

        # Parse the symbol to get expiry and option type
        # Monthly YYMMM must be checked BEFORE weekly YYMMMDD to avoid false matches
        # Format 1: NIFTY26JAN25000PE (monthly YYMMM)
        match = re.match(r'NIFTY(\d{2}[A-Z]{3})(\d{5,})(CE|PE)', symbol)
        # Format 2: NIFTY26JAN2725000PE (weekly YYMMMDD)
        if not match:
            match = re.match(r'NIFTY(\d{2}[A-Z]{3}\d{2})(\d{5,})(CE|PE)', symbol)
        # Format 3: NIFTY2612025000PE (weekly compact YYMDD)
        if not match:
            match = re.match(r'NIFTY(\d{2}[A-Z0-9]\d{2})(\d+)(CE|PE)', symbol)

        if not match:
            return jsonify({"success": False, "error": f"Cannot parse symbol: {symbol}"})

        expiry_code = match.group(1)
        old_strike = int(match.group(2))
        option_type = match.group(3)

        # Convert expiry code to date
        import calendar
        month_name_map = {'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
                         'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12}
        month_char_map = {'1': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6,
                         '7': 7, '8': 8, '9': 9, 'O': 10, 'N': 11, 'D': 12}

        if len(expiry_code) == 7 and expiry_code[2:5].isalpha():
            # YYMMMDD format (e.g., 26JAN27)
            yy = int(expiry_code[:2])
            mm = month_name_map.get(expiry_code[2:5].upper(), 1)
            dd = int(expiry_code[5:7])
            expiry_date = date(2000 + yy, mm, dd)
        elif len(expiry_code) == 5 and expiry_code[2:5].isalpha():
            # YYMMM format (e.g., 26JAN) - monthly, find last Tuesday
            yy = int(expiry_code[:2])
            mm = month_name_map.get(expiry_code[2:5].upper(), 1)
            last_day = calendar.monthrange(2000 + yy, mm)[1]
            d = date(2000 + yy, mm, last_day)
            while d.weekday() != 1:  # Tuesday (NSE changed from Thursday)
                d = d.replace(day=d.day - 1)
            expiry_date = d
        elif len(expiry_code) == 5:
            # YYMDD format (e.g., 26120)
            yy = int(expiry_code[:2])
            month_char = expiry_code[2]
            dd = int(expiry_code[3:5])
            mm = month_char_map.get(month_char, int(month_char) if month_char.isdigit() else 1)
            expiry_date = date(2000 + yy, mm, dd)
        else:
            return jsonify({"success": False, "error": f"Cannot parse expiry from: {expiry_code}"})

        # Check if custom target strike was provided, otherwise use 7-delta
        target_strike = data.get("target_strike")

        if target_strike:
            new_strike = int(target_strike)
        else:
            # Get target delta strike for this expiry and option type
            target_delta = float(os.getenv("TARGET_DELTA", "0.07"))
            strangle_data = provider.find_strangle(expiry=expiry_date, target_delta=target_delta)
            if not strangle_data:
                return jsonify({"success": False, "error": "Cannot fetch strangle data for target delta strike"})

            if option_type == "CE":
                new_strike = strangle_data.call_strike
            else:
                new_strike = strangle_data.put_strike

        # Get new symbol
        new_symbol = provider.get_trading_symbol(expiry_date, new_strike, option_type)
        if not new_symbol:
            return jsonify({"success": False, "error": f"Cannot find instrument for {new_strike} {option_type}"})

        # Fetch LTP and delta for the target strike
        try:
            new_quote = provider.kite.quote([f"NFO:{new_symbol}"])
            new_ltp = new_quote.get(f"NFO:{new_symbol}", {}).get('last_price', 0)
        except:
            new_ltp = 0

        try:
            strangle_data = provider.find_strangle(expiry=expiry_date) if not target_strike else None
            spot = strangle_data.spot_price if strangle_data else 25000
            dte = (expiry_date - date.today()).days
            time_to_expiry = max(dte / 365.0, 0.001)
            from data.option_greeks import calculate_delta
            new_delta = abs(calculate_delta(spot, new_strike, time_to_expiry, option_type))
        except:
            new_delta = 0.07

        paper_trading = os.getenv("PAPER_TRADING", "false").lower() == "true"

        orders_result = {
            "square_off": None,
            "new_position": None,
        }

        if paper_trading:
            # Simulate orders
            orders_result["square_off"] = {
                "symbol": symbol,
                "type": "BUY" if qty < 0 else "SELL",
                "qty": abs_qty,
                "status": "PAPER_TRADE"
            }
            orders_result["new_position"] = {
                "symbol": new_symbol,
                "type": "SELL",
                "qty": abs_qty,
                "strike": new_strike,
                "delta": new_delta,
                "ltp": new_ltp,
                "status": "PAPER_TRADE"
            }
        else:
            # Place real orders
            try:
                # 1. Square off existing position
                square_off_type = "BUY" if qty < 0 else "SELL"
                order1_id = provider.kite.place_order(
                    variety="regular",
                    exchange="NFO",
                    tradingsymbol=symbol,
                    transaction_type=square_off_type,
                    quantity=abs_qty,
                    product="NRML",
                    order_type="MARKET"
                )
                orders_result["square_off"] = {
                    "order_id": order1_id,
                    "symbol": symbol,
                    "type": square_off_type,
                    "qty": abs_qty,
                }

                # 2. Sell new position at 7-delta
                order2_id = provider.kite.place_order(
                    variety="regular",
                    exchange="NFO",
                    tradingsymbol=new_symbol,
                    transaction_type="SELL",
                    quantity=abs_qty,
                    product="NRML",
                    order_type="MARKET"
                )
                orders_result["new_position"] = {
                    "order_id": order2_id,
                    "symbol": new_symbol,
                    "type": "SELL",
                    "qty": abs_qty,
                    "strike": new_strike,
                    "delta": new_delta,
                    "ltp": new_ltp,
                }
            except Exception as e:
                return jsonify({
                    "success": False,
                    "error": str(e),
                    "partial_result": orders_result
                })

        return jsonify({
            "success": True,
            "old_symbol": symbol,
            "old_strike": old_strike,
            "new_symbol": new_symbol,
            "new_strike": new_strike,
            "new_delta": round(new_delta, 4),
            "option_type": option_type,
            "quantity": abs_qty,
            "orders": orders_result,
            "paper_trading": paper_trading
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


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

    if "auto_trade" in data:
        value = "true" if data["auto_trade"] else "false"
        set_key(str(ENV_FILE), "AUTO_TRADE", value)
        os.environ["AUTO_TRADE"] = value

    if "auto_exit" in data:
        value = "true" if data["auto_exit"] else "false"
        set_key(str(ENV_FILE), "AUTO_EXIT", value)
        os.environ["AUTO_EXIT"] = value

    if "exit_target_pct" in data:
        value = str(int(data["exit_target_pct"]) / 100)  # 50 → "0.50"
        set_key(str(ENV_FILE), "EXIT_TARGET_PCT", value)
        os.environ["EXIT_TARGET_PCT"] = value

    if "lot_quantity" in data:
        value = str(int(data["lot_quantity"]))
        set_key(str(ENV_FILE), "LOT_QUANTITY", value)
        os.environ["LOT_QUANTITY"] = value

    if "decay_threshold" in data:
        # Convert percentage (e.g., 60) to decimal (0.60)
        value = str(int(data["decay_threshold"]) / 100)
        set_key(str(ENV_FILE), "MOVE_DECAY_THRESHOLD", value)
        os.environ["MOVE_DECAY_THRESHOLD"] = value

    if "target_delta" in data:
        # Convert percentage (e.g., 7) to decimal (0.07)
        value = str(int(data["target_delta"]) / 100)
        set_key(str(ENV_FILE), "TARGET_DELTA", value)
        os.environ["TARGET_DELTA"] = value
        print(f"[Settings] TARGET_DELTA saved: {value}")

    return jsonify({"success": True})


shutdown_timer = None
shutdown_lock = None

@app.route("/api/shutdown", methods=["POST"])
def shutdown_server():
    """Schedule server shutdown (can be cancelled by /api/shutdown/cancel)."""
    global shutdown_timer, shutdown_lock
    import os
    import signal
    import threading

    if shutdown_lock is None:
        shutdown_lock = threading.Lock()

    def do_shutdown():
        os.kill(os.getpid(), signal.SIGTERM)

    with shutdown_lock:
        # Cancel any existing timer
        if shutdown_timer:
            shutdown_timer.cancel()

        # Schedule shutdown after 2 seconds (allows time for cancel on refresh)
        shutdown_timer = threading.Timer(2.0, do_shutdown)
        shutdown_timer.start()

    return jsonify({"success": True, "message": "Server will shutdown in 2 seconds..."})


@app.route("/api/shutdown/cancel", methods=["POST"])
def cancel_shutdown():
    """Cancel pending shutdown (called on page load after refresh)."""
    global shutdown_timer, shutdown_lock
    import threading

    if shutdown_lock is None:
        shutdown_lock = threading.Lock()

    with shutdown_lock:
        if shutdown_timer:
            shutdown_timer.cancel()
            shutdown_timer = None
            return jsonify({"success": True, "message": "Shutdown cancelled"})

    return jsonify({"success": True, "message": "No pending shutdown"})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8080)
