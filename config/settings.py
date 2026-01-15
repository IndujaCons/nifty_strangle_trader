"""
Configuration settings for the Nifty Strangle Automation System.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data_store"
LOG_DIR = BASE_DIR / "logs"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# Kite Connect Configuration
KITE_CONFIG = {
    "api_key": os.getenv("KITE_API_KEY", ""),
    "api_secret": os.getenv("KITE_API_SECRET", ""),
    "access_token": os.getenv("KITE_ACCESS_TOKEN", ""),
    "redirect_url": "http://127.0.0.1:8080/",
}

# Trading Mode
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
TOTAL_CAPITAL = float(os.getenv("TOTAL_CAPITAL", "100000"))
LOT_QUANTITY = int(os.getenv("LOT_QUANTITY", "1"))  # Number of lots per trade

# Strategy Parameters
STRATEGY_CONFIG = {
    # Delta targets for strike selection
    "target_delta_lower": 0.06,  # Minimum delta (6 delta)
    "target_delta_upper": 0.08,  # Maximum delta (8 delta)
    "target_delta": 0.07,        # Target delta (7 delta)

    # Days to Expiry
    "entry_dte": 14,             # Enter 14 DTE options
    "exit_dte": 7,               # Optional exit at 7 DTE (not mandatory)
    "dte_tolerance": 2,          # +/- days tolerance for DTE matching

    # Capital Management
    "total_parts": 6,            # Divide capital into 6 equal parts
    "max_entries_per_day": 2,    # Maximum strangle entries per day

    # Exit Rules
    "profit_target_pct": 0.50,   # Exit at 50% of max profit
    "move_decay_threshold": float(os.getenv("MOVE_DECAY_THRESHOLD", "0.60")),  # Move position when decayed > 60%

    # Entry Condition
    "entry_signal": "straddle_gt_vwap",  # Enter when straddle > VWAP
    "signal_duration_seconds": 300,      # Must be above VWAP for 5 minutes (300s)
}

# Trading Windows Configuration
TRADING_WINDOWS = {
    "no_trade_minutes": 15,              # No trade first 15 mins (start after 9:30)
    "morning_window": {
        "start": "09:30",
        "end": "13:15",
        "max_trades": 1,
    },
    "afternoon_window": {
        "start": "13:15",
        "end": "15:15",
        "max_trades": 1,
    },
}

# Market Hours (IST)
MARKET_CONFIG = {
    "timezone": "Asia/Kolkata",
    "market_open": "09:15",
    "market_close": "15:30",
    "strategy_interval_seconds": 60,  # Check every 60 seconds
}

# Nifty Contract Specifications
NIFTY_CONFIG = {
    "symbol": "NIFTY",
    "exchange": "NFO",
    "lot_size": 65,              # Nifty lot size (updated Jan 2026)
    "tick_size": 0.05,
    "strike_interval": 50,       # Strike prices are in 50 intervals
}

# Greeks Calculation
GREEKS_CONFIG = {
    "risk_free_rate": 0.07,      # 7% annualized (approximate RBI rate)
    "dividend_yield": 0.0,       # 0 for index options
    "trading_days_per_year": 252,
}

# Database
DATABASE_CONFIG = {
    "url": f"sqlite:///{(DATA_DIR / 'trades.db').as_posix()}",
}

# Logging
LOGGING_CONFIG = {
    "level": "INFO",
    "format": "{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
    "rotation": "1 day",
    "retention": "30 days",
}

# Telegram Alerts (optional)
TELEGRAM_CONFIG = {
    "bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
    "chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
    "enabled": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
}
