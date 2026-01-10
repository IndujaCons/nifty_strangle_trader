# NIFTY 7-Delta Strangle Trading System

A semi-automated options trading system for NIFTY index using 7-delta strangle strategy with Zerodha Kite Connect integration.

## Overview

This system helps traders execute and manage short strangle positions on NIFTY options. It identifies 7-delta strikes (approximately 7% probability of being ITM), monitors entry signals based on straddle premium vs VWAP, and provides tools for position management with profit targets.

### Trading Strategy

The system implements a **7-Delta Short Strangle** strategy:

1. **Strike Selection**: Sells both Call and Put options with delta close to 0.07 (7 delta)
2. **Entry Signal**: Enters when ATM straddle price > straddle VWAP for 5 minutes
3. **Expiry Selection**: Targets options with ~14 days to expiry (DTE)
4. **Exit Target**: 50% of maximum profit (premium collected)
5. **Position Sizing**: Configurable lots per trade

### Why 7-Delta?

- 7-delta options have approximately 93% probability of expiring worthless
- Provides good balance between premium collection and risk
- Strikes are typically far enough from spot to withstand normal market movements

## Features

### Market Monitor
- Real-time NIFTY spot price and synthetic futures
- ATM straddle price and VWAP comparison
- Entry signal detection with 5-minute confirmation timer
- 7-delta strangle strikes with live premiums and Greeks
- Margin requirement calculation (with span benefit)

### Position Management
- Current open positions with P&L tracking
- Trade history grouped by expiry
- Manual profit entry for adjustments
- Max profit and 50% target calculation per expiry

### Exit Management
- Visual alert when 50% profit target reached (green highlight)
- One-click "Exit All Positions" button per expiry
- Market orders for quick execution

### Account Info
- Available margin display (auto-refreshes every minute)
- Connection status with Zerodha

## Prerequisites

- Python 3.9 or higher
- Zerodha trading account with Kite Connect API access
- API Key and Secret from [Kite Connect Developer Console](https://developers.kite.trade/)

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/IndujaCons/nifty_strangle_trader.git
cd nifty_strangle_trader
```

### 2. Create Virtual Environment

**macOS/Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
pip install flask  # For web UI
```

### 4. Configure Environment

Create a `.env` file in the project root:

```env
# Zerodha Kite Connect Credentials
KITE_API_KEY=your_api_key_here
KITE_API_SECRET=your_api_secret_here
KITE_ACCESS_TOKEN=

# Trading Configuration
PAPER_TRADING=true
LOT_QUANTITY=1
TOTAL_CAPITAL=100000

# Optional: Telegram Alerts
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

## Usage

### Starting the Application

**macOS/Linux:**
```bash
python run.py --ui
```

**Windows:**
Double-click `run_ui.bat` or run:
```bash
python run.py --ui
```

The web UI will be available at: **http://localhost:8080**

### First-Time Login

1. Open http://localhost:8080 in your browser
2. Click "Get Login URL" in the Login & Settings panel
3. Complete Zerodha login and authorize the app
4. Copy the `request_token` from the redirect URL
5. Paste it in the app and click "Complete Login"
6. Your access token is saved for the session

### Placing a Trade

1. **Check Market Monitor**: Wait for "Entry Signal: Active" (straddle > VWAP for 5 mins)
2. **Review Strangle**: Check the 7-delta strikes, premiums, and margin required
3. **Adjust if needed**: Use +/- buttons to modify strikes
4. **Click "Place Trade"**: Confirm the trade details in the modal
5. **Execute**: Click "Confirm Trade" to place market orders

### Monitoring Positions

- **Positions Tab**: View current open positions with live P&L
- **History Tab**: Track profit by expiry with max profit and 50% target

### Exiting Positions

When profit reaches 50% of max profit:
- The expiry card turns green with "50% TARGET" badge
- Click the green "EXIT ALL" button to close all positions for that expiry
- Confirm to place market BUY orders

## Configuration Options

Edit `config/settings.py` or `.env` file:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `PAPER_TRADING` | true | Enable paper trading mode (no real orders) |
| `LOT_QUANTITY` | 1 | Number of lots per trade |
| `target_delta` | 0.07 | Target delta for strike selection |
| `entry_dte` | 14 | Days to expiry for entry |
| `profit_target_pct` | 0.50 | Exit at 50% of max profit |
| `signal_duration_seconds` | 300 | Signal confirmation time (5 mins) |

## Project Structure

```
nifty_strangle_trader/
├── ui/                     # Web UI (Flask)
│   ├── app.py             # API endpoints
│   └── templates/         # HTML templates
├── data/                   # Data providers
│   └── kite_data_provider.py
├── config/                 # Configuration
│   └── settings.py
├── core/                   # Core trading logic
│   ├── strategy_engine.py
│   └── position_manager.py
├── greeks/                 # Options Greeks calculation
├── models/                 # Data models
├── broker/                 # Broker integration
├── data_store/            # Local data persistence
├── run.py                 # Application entry point
└── requirements.txt
```

## Important Notes

### Risk Warning

**Options trading involves substantial risk of loss and is not suitable for all investors.**

- This system sells naked options which have unlimited risk potential
- Past performance does not guarantee future results
- Always use paper trading mode first to understand the system
- Never trade with money you cannot afford to lose

### Paper Trading

The system starts in `PAPER_TRADING=true` mode by default. In this mode:
- No real orders are placed
- All trades are simulated
- Use this to test and understand the system

To enable live trading, set `PAPER_TRADING=false` in `.env` file.

### Access Token Expiry

Zerodha access tokens expire daily at midnight. You need to re-login each trading day.

### Market Hours

The system is designed for NSE market hours:
- Market Open: 9:15 AM IST
- Market Close: 3:30 PM IST
- No trades in first 15 minutes (9:15 - 9:30)

## Troubleshooting

### Port Already in Use

The application automatically kills any existing process on port 8080 when starting.

### Connection Issues

1. Verify your API key and secret in `.env`
2. Re-login if access token has expired
3. Check Zerodha API status

### Margin Calculation Shows 0

Ensure you're logged in and have a valid access token. The margin API requires authentication.

## License

This project is for educational purposes only. Use at your own risk.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Support

For issues and feature requests, please open an issue on GitHub.

---

**Disclaimer**: This software is provided "as is" without warranty of any kind. The authors are not responsible for any financial losses incurred through the use of this system. Always do your own research and consult with a qualified financial advisor before trading.
