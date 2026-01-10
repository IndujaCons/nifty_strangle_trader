"""
Position manager for tracking strangle positions.
"""
from typing import List, Optional, Dict
from datetime import datetime
from loguru import logger

from models.strangle import Strangle, PositionStatus
from broker.base_broker import BaseBroker
from config.settings import STRATEGY_CONFIG


class PositionManager:
    """
    Manages strangle positions and monitors exit conditions.
    """

    def __init__(self, broker: BaseBroker):
        self.broker = broker
        self.positions: Dict[str, Strangle] = {}
        self.profit_target_pct = STRATEGY_CONFIG["profit_target_pct"]
        self.exit_dte = STRATEGY_CONFIG["exit_dte"]

    def add_position(self, strangle: Strangle):
        """Add new position to tracking."""
        self.positions[strangle.id] = strangle
        logger.info(
            f"Position added: {strangle.id} - "
            f"CE {strangle.call_strike} / PE {strangle.put_strike}"
        )

    def remove_position(self, strangle_id: str):
        """Remove position from tracking."""
        if strangle_id in self.positions:
            del self.positions[strangle_id]
            logger.info(f"Position removed: {strangle_id}")

    def get_open_positions(self) -> List[Strangle]:
        """Get all open positions."""
        return [
            pos for pos in self.positions.values()
            if pos.status == PositionStatus.OPEN
        ]

    def get_closed_positions(self) -> List[Strangle]:
        """Get all closed positions."""
        return [
            pos for pos in self.positions.values()
            if pos.status == PositionStatus.CLOSED
        ]

    def get_position(self, strangle_id: str) -> Optional[Strangle]:
        """Get position by ID."""
        return self.positions.get(strangle_id)

    def check_profit_target(self, strangle: Strangle) -> bool:
        """
        Check if position has reached profit target.

        Exit at 50% of max profit.
        """
        if strangle.status != PositionStatus.OPEN:
            return False

        pnl_pct = self.broker.get_strangle_pnl(strangle) / strangle.max_profit

        if pnl_pct >= self.profit_target_pct:
            logger.info(
                f"Profit target reached for {strangle.id}: "
                f"{pnl_pct:.1%} >= {self.profit_target_pct:.1%}"
            )
            return True

        return False

    def check_dte_exit(self, strangle: Strangle) -> bool:
        """
        Check if position should exit based on DTE.

        Note: 7 DTE exit is optional, not mandatory.
        Returns True to indicate the condition is met, but exit is optional.
        """
        if strangle.status != PositionStatus.OPEN:
            return False

        dte = strangle.days_to_expiry

        if dte <= self.exit_dte:
            logger.info(f"DTE exit condition met for {strangle.id}: {dte} DTE")
            return True

        return False

    def get_positions_to_exit(self) -> List[tuple]:
        """
        Check all positions and return those that should be exited.

        Returns:
            List of (strangle, exit_reason) tuples
        """
        to_exit = []

        for strangle in self.get_open_positions():
            # Check profit target (mandatory exit)
            if self.check_profit_target(strangle):
                to_exit.append((strangle, "50% profit target"))
                continue

            # DTE exit is optional - just log it
            if self.check_dte_exit(strangle):
                # Not adding to exit list since it's optional
                pass

        return to_exit

    def close_position(self, strangle: Strangle, reason: str) -> bool:
        """
        Close a position via broker.

        Args:
            strangle: Position to close
            reason: Exit reason

        Returns:
            True if closed successfully
        """
        if strangle.status != PositionStatus.OPEN:
            logger.warning(f"Position {strangle.id} is not open")
            return False

        success = self.broker.close_strangle(strangle)

        if success:
            strangle.exit_reason = reason
            logger.info(
                f"Position closed: {strangle.id}, reason: {reason}, "
                f"P&L: {strangle.realized_pnl:.2f}"
            )
        else:
            logger.error(f"Failed to close position {strangle.id}")

        return success

    def get_total_unrealized_pnl(self) -> float:
        """Get total unrealized P&L across all open positions."""
        return sum(
            self.broker.get_strangle_pnl(pos)
            for pos in self.get_open_positions()
        )

    def get_total_realized_pnl(self) -> float:
        """Get total realized P&L from closed positions."""
        return sum(
            pos.realized_pnl or 0
            for pos in self.get_closed_positions()
        )

    def get_portfolio_summary(self) -> Dict:
        """Get portfolio summary."""
        open_positions = self.get_open_positions()
        closed_positions = self.get_closed_positions()

        return {
            "open_positions": len(open_positions),
            "closed_positions": len(closed_positions),
            "unrealized_pnl": self.get_total_unrealized_pnl(),
            "realized_pnl": self.get_total_realized_pnl(),
            "total_pnl": self.get_total_unrealized_pnl() + self.get_total_realized_pnl(),
            "positions": [
                {
                    "id": pos.id,
                    "call_strike": pos.call_strike,
                    "put_strike": pos.put_strike,
                    "expiry": pos.expiry,
                    "entry_premium": pos.entry_premium,
                    "current_pnl": self.broker.get_strangle_pnl(pos),
                    "pnl_pct": self.broker.get_strangle_pnl(pos) / pos.max_profit if pos.max_profit > 0 else 0,
                    "dte": pos.days_to_expiry
                }
                for pos in open_positions
            ]
        }
