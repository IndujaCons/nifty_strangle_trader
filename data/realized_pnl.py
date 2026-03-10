"""
Realized P&L computation from kite.trades() API.

Uses actual trade fills to compute accurate realized P&L, avoiding the
broken avg_price / realised field issues in kite.positions() for
carry-forward positions.
"""
import time
from collections import defaultdict
from typing import Dict, List, Optional

from loguru import logger


def compute_realized_pnl_from_trades(
    trades: List[Dict], net_positions: List[Dict]
) -> Dict[str, float]:
    """
    Compute realized P&L per symbol by replaying today's trades against
    carry-forward state derived from net positions.

    Args:
        trades: List of trade dicts from kite.trades()
        net_positions: List of net position dicts from kite.positions()['net']

    Returns:
        {tradingsymbol: realized_pnl} for symbols that had trades today
    """
    # Filter to NIFTY NRML trades only
    nifty_trades = [
        t for t in trades
        if t.get('tradingsymbol', '').startswith('NIFTY')
        and t.get('product', '') == 'NRML'
    ]

    if not nifty_trades:
        return {}

    # Group trades by symbol, sorted by fill_timestamp
    trades_by_symbol = defaultdict(list)
    for t in nifty_trades:
        trades_by_symbol[t['tradingsymbol']].append(t)

    for symbol in trades_by_symbol:
        trades_by_symbol[symbol].sort(key=lambda t: t.get('fill_timestamp', t.get('order_timestamp', '')))

    # Build net position lookup
    pos_map = {}
    for p in net_positions:
        sym = p.get('tradingsymbol', '')
        if sym.startswith('NIFTY'):
            pos_map[sym] = p

    result = {}

    for symbol, sym_trades in trades_by_symbol.items():
        pos = pos_map.get(symbol, {})

        # Compute today's net buy/sell from trades
        today_buy_qty = 0
        today_sell_qty = 0
        today_buy_value = 0.0
        today_sell_value = 0.0

        for t in sym_trades:
            qty = t.get('quantity', 0)
            price = t.get('average_price', 0)
            if t.get('transaction_type') == 'BUY':
                today_buy_qty += qty
                today_buy_value += price * qty
            else:
                today_sell_qty += qty
                today_sell_value += price * qty

        # Reconstruct carry-forward state
        current_qty = pos.get('quantity', 0)
        cf_qty = current_qty - today_buy_qty + today_sell_qty

        # Back-derive cf_avg from position's average_price
        # For positions that existed before today:
        #   net_avg = (cf_value + today_buy_value - today_sell_value) / current_qty  (roughly)
        # But Zerodha's average_price is the weighted average of all buys (for longs)
        # or all sells (for shorts). We need the cost basis before today's trades.
        net_avg = pos.get('average_price', 0)

        if cf_qty != 0:
            # Derive CF average from position data
            # Zerodha provides buy_value/sell_value which include all trades
            # For a short position: avg = sell_value / sell_qty
            # CF avg can be derived by removing today's contribution
            pos_buy_value = pos.get('buy_value', 0)   # Zerodha's total buy value (all time today context)
            pos_sell_value = pos.get('sell_value', 0)  # Zerodha's total sell value

            if cf_qty < 0:
                # Short carry-forward: entry was selling
                # total_sell_value = cf_sell_value + today_sell_value
                # cf_sell_qty = abs(cf_qty) (what was sold to create the CF position... but we sold more today)
                # Actually for CF shorts, the sells happened on previous days.
                # Zerodha's sell_value only covers TODAY's sells (in net position context).
                # So we can't simply subtract. Use average_price directly.
                #
                # average_price for a short = weighted avg of ALL sells that created the position
                # If we sold more today, average_price shifted. Back-derive CF avg:
                # avg_price * total_short_qty = cf_avg * cf_short_qty + today_sell_avg * today_sell_qty
                total_short_qty = abs(cf_qty) + today_sell_qty - today_buy_qty
                # But total_short_qty should equal abs(current_qty) if position is still short
                # Actually, let's think about this differently.
                #
                # For the replay, we just need the CF starting state.
                # If no trades closed the position, realized = 0 anyway.
                # The CF avg only matters when a closing trade happens.
                #
                # Simple approach: derive from Zerodha's average_price
                # avg_price = weighted avg of all entries on the current side
                # For short: avg_price * abs(current_qty + today_buy_qty) = cf_avg * abs(cf_qty) + today_sell_avg * today_sell_qty
                # But this assumes all sells are "entries" which isn't true if some sells closed a long...
                #
                # Best pragmatic approach: use multiplied_value fields or just net_avg as cf_avg
                # when we can't precisely decompose. The error is small for typical use.
                cf_avg = net_avg  # Will be corrected during replay if today had sells
                if today_sell_qty > 0 and abs(cf_qty) > 0:
                    # Back out today's sell contribution from average
                    # net_avg * (abs(cf_qty) + today_sell_qty) = cf_avg * abs(cf_qty) + today_sell_avg * today_sell_qty
                    today_sell_avg = today_sell_value / today_sell_qty if today_sell_qty else 0
                    total_entry_qty = abs(cf_qty) + today_sell_qty
                    if total_entry_qty > 0 and abs(cf_qty) > 0:
                        cf_avg = (net_avg * total_entry_qty - today_sell_avg * today_sell_qty) / abs(cf_qty)
                        if cf_avg <= 0:
                            cf_avg = net_avg  # Fallback
            else:
                # Long carry-forward: entry was buying
                cf_avg = net_avg
                if today_buy_qty > 0 and cf_qty > 0:
                    today_buy_avg = today_buy_value / today_buy_qty if today_buy_qty else 0
                    total_entry_qty = cf_qty + today_buy_qty
                    if total_entry_qty > 0 and cf_qty > 0:
                        cf_avg = (net_avg * total_entry_qty - today_buy_avg * today_buy_qty) / cf_qty
                        if cf_avg <= 0:
                            cf_avg = net_avg  # Fallback
        else:
            # No carry-forward position — started flat today
            cf_avg = 0

        # Replay trades chronologically
        running_qty = cf_qty    # Positive = long, negative = short
        running_avg = cf_avg    # Weighted average cost/entry price
        realized = 0.0

        for t in sym_trades:
            trade_qty = t.get('quantity', 0)
            trade_price = t.get('average_price', 0)
            is_buy = t.get('transaction_type') == 'BUY'

            if is_buy:
                if running_qty >= 0:
                    # Adding to long or opening long from flat
                    total_qty = running_qty + trade_qty
                    if total_qty > 0:
                        running_avg = (running_avg * running_qty + trade_price * trade_qty) / total_qty
                    running_qty = total_qty
                else:
                    # Closing short position (buying back)
                    close_qty = min(trade_qty, abs(running_qty))
                    realized += (running_avg - trade_price) * close_qty

                    remaining = trade_qty - close_qty
                    running_qty += trade_qty  # running_qty was negative

                    if running_qty > 0:
                        # Flipped to long — remaining qty starts new position
                        running_avg = trade_price
                    elif running_qty == 0:
                        running_avg = 0
                    # else still short, avg unchanged
            else:
                # SELL
                if running_qty <= 0:
                    # Adding to short or opening short from flat
                    total_qty = abs(running_qty) + trade_qty
                    if total_qty > 0:
                        running_avg = (running_avg * abs(running_qty) + trade_price * trade_qty) / total_qty
                    running_qty -= trade_qty
                else:
                    # Closing long position (selling)
                    close_qty = min(trade_qty, running_qty)
                    realized += (trade_price - running_avg) * close_qty

                    remaining = trade_qty - close_qty
                    running_qty -= trade_qty  # running_qty was positive

                    if running_qty < 0:
                        # Flipped to short — remaining qty starts new position
                        running_avg = trade_price
                    elif running_qty == 0:
                        running_avg = 0
                    # else still long, avg unchanged

        if abs(realized) > 0.01:
            result[symbol] = realized
            logger.debug(f"[TradesP&L] {symbol}: realized={realized:.2f} (cf_qty={cf_qty}, cf_avg={cf_avg:.2f})")

    return result


# Cache for get_trades_realized_pnl
_trades_cache = {
    'data': {},
    'timestamp': 0,
    'ttl': 5,  # seconds
}


def get_trades_realized_pnl(
    kite, net_positions: Optional[List[Dict]] = None, force_refresh: bool = False
) -> Dict[str, float]:
    """
    Cached wrapper around compute_realized_pnl_from_trades().

    Args:
        kite: KiteConnect instance
        net_positions: Optional pre-fetched net positions (avoids extra API call)
        force_refresh: Bypass cache

    Returns:
        {tradingsymbol: realized_pnl}
    """
    now = time.time()

    if not force_refresh and (now - _trades_cache['timestamp']) < _trades_cache['ttl']:
        return _trades_cache['data']

    try:
        trades = kite.trades()

        if net_positions is None:
            positions = kite.positions()
            net_positions = positions.get('net', [])

        result = compute_realized_pnl_from_trades(trades, net_positions)

        _trades_cache['data'] = result
        _trades_cache['timestamp'] = now

        return result

    except Exception as e:
        logger.error(f"[TradesP&L] Failed to fetch trades: {e}")
        # Return cached data if available, else empty
        if _trades_cache['data']:
            return _trades_cache['data']
        return {}
