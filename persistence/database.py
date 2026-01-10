"""
Database persistence for trades and positions.
"""
from datetime import datetime
from typing import List, Optional, Dict
from loguru import logger

from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, Enum as SQLEnum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

from config.settings import DATABASE_CONFIG
from models.strangle import Strangle, PositionStatus

Base = declarative_base()


class StrangleRecord(Base):
    """SQLAlchemy model for strangle positions."""
    __tablename__ = "strangles"

    id = Column(String, primary_key=True)
    call_strike = Column(Float, nullable=False)
    put_strike = Column(Float, nullable=False)
    expiry = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)

    entry_call_premium = Column(Float, nullable=False)
    entry_put_premium = Column(Float, nullable=False)
    entry_time = Column(DateTime, nullable=False)
    entry_spot = Column(Float, default=0.0)

    exit_call_premium = Column(Float, nullable=True)
    exit_put_premium = Column(Float, nullable=True)
    exit_time = Column(DateTime, nullable=True)
    exit_reason = Column(String, nullable=True)

    status = Column(String, default="OPEN")
    capital_part = Column(Integer, default=0)
    realized_pnl = Column(Float, nullable=True)


class VWAPRecord(Base):
    """SQLAlchemy model for VWAP history."""
    __tablename__ = "vwap_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    straddle_price = Column(Float, nullable=False)
    volume = Column(Integer, default=1)
    vwap = Column(Float, nullable=False)


class TradeLog(Base):
    """SQLAlchemy model for trade log."""
    __tablename__ = "trade_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False)
    action = Column(String, nullable=False)  # ENTRY, EXIT, SIGNAL
    strangle_id = Column(String, nullable=True)
    details = Column(String, nullable=True)
    pnl = Column(Float, nullable=True)


class DatabaseManager:
    """Database manager for trade persistence."""

    def __init__(self, db_url: str = None):
        self.db_url = db_url or DATABASE_CONFIG["url"]
        self.engine = create_engine(self.db_url, echo=False)
        self.SessionLocal = sessionmaker(bind=self.engine)

        # Create tables
        Base.metadata.create_all(self.engine)
        logger.info(f"Database initialized: {self.db_url}")

    def get_session(self) -> Session:
        """Get a database session."""
        return self.SessionLocal()

    def save_strangle(self, strangle: Strangle):
        """Save or update a strangle position."""
        session = self.get_session()
        try:
            record = session.query(StrangleRecord).filter_by(id=strangle.id).first()

            if record:
                # Update existing
                record.exit_call_premium = strangle.exit_call_premium
                record.exit_put_premium = strangle.exit_put_premium
                record.exit_time = strangle.exit_time
                record.exit_reason = strangle.exit_reason
                record.status = strangle.status.value
                record.realized_pnl = strangle.realized_pnl
            else:
                # Create new
                record = StrangleRecord(
                    id=strangle.id,
                    call_strike=strangle.call_strike,
                    put_strike=strangle.put_strike,
                    expiry=strangle.expiry,
                    quantity=strangle.quantity,
                    entry_call_premium=strangle.entry_call_premium,
                    entry_put_premium=strangle.entry_put_premium,
                    entry_time=strangle.entry_time,
                    entry_spot=strangle.entry_spot,
                    status=strangle.status.value,
                    capital_part=strangle.capital_part
                )
                session.add(record)

            session.commit()
            logger.debug(f"Strangle saved: {strangle.id}")

        except Exception as e:
            session.rollback()
            logger.error(f"Error saving strangle: {e}")
        finally:
            session.close()

    def load_strangle(self, strangle_id: str) -> Optional[Strangle]:
        """Load a strangle from database."""
        session = self.get_session()
        try:
            record = session.query(StrangleRecord).filter_by(id=strangle_id).first()
            if record:
                return self._record_to_strangle(record)
            return None
        finally:
            session.close()

    def load_open_strangles(self) -> List[Strangle]:
        """Load all open strangles."""
        session = self.get_session()
        try:
            records = session.query(StrangleRecord).filter_by(status="OPEN").all()
            return [self._record_to_strangle(r) for r in records]
        finally:
            session.close()

    def load_all_strangles(self) -> List[Strangle]:
        """Load all strangles."""
        session = self.get_session()
        try:
            records = session.query(StrangleRecord).all()
            return [self._record_to_strangle(r) for r in records]
        finally:
            session.close()

    def _record_to_strangle(self, record: StrangleRecord) -> Strangle:
        """Convert database record to Strangle object."""
        strangle = Strangle(
            id=record.id,
            call_strike=record.call_strike,
            put_strike=record.put_strike,
            expiry=record.expiry,
            quantity=record.quantity,
            entry_call_premium=record.entry_call_premium,
            entry_put_premium=record.entry_put_premium,
            entry_time=record.entry_time,
            entry_spot=record.entry_spot,
            exit_call_premium=record.exit_call_premium,
            exit_put_premium=record.exit_put_premium,
            exit_time=record.exit_time,
            exit_reason=record.exit_reason,
            status=PositionStatus(record.status),
            capital_part=record.capital_part
        )
        return strangle

    def log_trade(self, action: str, strangle_id: str = None, details: str = None, pnl: float = None):
        """Log a trade event."""
        session = self.get_session()
        try:
            record = TradeLog(
                timestamp=datetime.now(),
                action=action,
                strangle_id=strangle_id,
                details=details,
                pnl=pnl
            )
            session.add(record)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Error logging trade: {e}")
        finally:
            session.close()

    def save_vwap_point(self, straddle_price: float, volume: int, vwap: float):
        """Save VWAP data point."""
        session = self.get_session()
        try:
            record = VWAPRecord(
                date=datetime.now().strftime("%Y-%m-%d"),
                timestamp=datetime.now(),
                straddle_price=straddle_price,
                volume=volume,
                vwap=vwap
            )
            session.add(record)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving VWAP: {e}")
        finally:
            session.close()

    def get_trade_history(self, limit: int = 100) -> List[Dict]:
        """Get recent trade history."""
        session = self.get_session()
        try:
            records = session.query(TradeLog).order_by(TradeLog.timestamp.desc()).limit(limit).all()
            return [
                {
                    "timestamp": r.timestamp,
                    "action": r.action,
                    "strangle_id": r.strangle_id,
                    "details": r.details,
                    "pnl": r.pnl
                }
                for r in records
            ]
        finally:
            session.close()

    def get_pnl_summary(self) -> Dict:
        """Get P&L summary."""
        session = self.get_session()
        try:
            closed = session.query(StrangleRecord).filter_by(status="CLOSED").all()
            total_pnl = sum(r.realized_pnl or 0 for r in closed)
            winners = [r for r in closed if (r.realized_pnl or 0) > 0]
            losers = [r for r in closed if (r.realized_pnl or 0) <= 0]

            return {
                "total_trades": len(closed),
                "winners": len(winners),
                "losers": len(losers),
                "win_rate": len(winners) / len(closed) if closed else 0,
                "total_pnl": total_pnl,
                "avg_pnl": total_pnl / len(closed) if closed else 0,
                "max_win": max((r.realized_pnl or 0 for r in closed), default=0),
                "max_loss": min((r.realized_pnl or 0 for r in closed), default=0)
            }
        finally:
            session.close()
