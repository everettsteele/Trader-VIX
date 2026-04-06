"""
Trader-VIX — Database Layer
SQLite schema. Both strategies share one DB.
Bot always reconciles from DB + broker on startup — never trusts memory alone.
"""
import os
import sqlite3
import logging
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)


def get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DB_PATH) if os.path.dirname(config.DB_PATH) else ".", exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS swing_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, short_strike REAL NOT NULL, long_strike REAL NOT NULL,
            expiration TEXT NOT NULL, dte_at_open INTEGER,
            credit_received REAL NOT NULL, margin_held REAL NOT NULL,
            num_contracts INTEGER DEFAULT 1, opened_date TEXT NOT NULL,
            order_id TEXT DEFAULT '', status TEXT DEFAULT 'open',
            vix_at_open REAL, spy_at_open REAL,
            close_date TEXT, close_mark REAL, close_reason TEXT, pnl_dollars REAL,
            mode TEXT DEFAULT 'PAPER'
        );
        CREATE TABLE IF NOT EXISTS zedte_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, put_short REAL NOT NULL, put_long REAL NOT NULL,
            call_short REAL NOT NULL, call_long REAL NOT NULL,
            expiration TEXT NOT NULL, credit_received REAL NOT NULL, margin_held REAL NOT NULL,
            num_contracts INTEGER DEFAULT 1, opened_date TEXT NOT NULL, open_time TEXT,
            order_id TEXT DEFAULT '', profit_order_id TEXT DEFAULT '', loss_order_id TEXT DEFAULT '',
            status TEXT DEFAULT 'open', vix_at_open REAL, spy_at_open REAL,
            close_time TEXT, close_mark REAL, close_reason TEXT, pnl_dollars REAL,
            mode TEXT DEFAULT 'PAPER'
        );
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
            net_liquidating_value REAL, cash REAL, buying_power REAL,
            open_swing_positions INTEGER DEFAULT 0, open_zedte_positions INTEGER DEFAULT 0,
            mode TEXT DEFAULT 'PAPER'
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
            subject TEXT, body TEXT, sent INTEGER DEFAULT 0
        );
    """)
    conn.commit()


def get_open_swing_positions(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM swing_positions WHERE status='open'").fetchall()]


def insert_swing_position(conn, pos):
    cur = conn.execute(
        """INSERT INTO swing_positions (symbol,short_strike,long_strike,expiration,dte_at_open,
           credit_received,margin_held,num_contracts,opened_date,order_id,vix_at_open,spy_at_open,mode)
           VALUES (:symbol,:short_strike,:long_strike,:expiration,:dte_at_open,
                   :credit_received,:margin_held,:num_contracts,:opened_date,:order_id,:vix_at_open,:spy_at_open,:mode)""",
        pos)
    conn.commit()
    return cur.lastrowid


def close_swing_position(conn, pos_id, close_date, close_mark, close_reason, pnl):
    conn.execute("UPDATE swing_positions SET status='closed',close_date=?,close_mark=?,close_reason=?,pnl_dollars=? WHERE id=?",
                 (close_date, close_mark, close_reason, pnl, pos_id))
    conn.commit()


def get_open_zedte_positions(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM zedte_positions WHERE status='open'").fetchall()]


def insert_zedte_position(conn, pos):
    cur = conn.execute(
        """INSERT INTO zedte_positions (symbol,put_short,put_long,call_short,call_long,expiration,
           credit_received,margin_held,num_contracts,opened_date,open_time,
           order_id,profit_order_id,loss_order_id,vix_at_open,spy_at_open,mode)
           VALUES (:symbol,:put_short,:put_long,:call_short,:call_long,:expiration,
                   :credit_received,:margin_held,:num_contracts,:opened_date,:open_time,
                   :order_id,:profit_order_id,:loss_order_id,:vix_at_open,:spy_at_open,:mode)""",
        pos)
    conn.commit()
    return cur.lastrowid


def close_zedte_position(conn, pos_id, close_time, close_mark, close_reason, pnl):
    conn.execute("UPDATE zedte_positions SET status='closed',close_time=?,close_mark=?,close_reason=?,pnl_dollars=? WHERE id=?",
                 (close_time, close_mark, close_reason, pnl, pos_id))
    conn.commit()


def save_snapshot(conn, snap):
    conn.execute(
        """INSERT INTO portfolio_snapshots (ts,net_liquidating_value,cash,buying_power,
           open_swing_positions,open_zedte_positions,mode)
           VALUES (:ts,:net_liquidating_value,:cash,:buying_power,:open_swing_positions,:open_zedte_positions,:mode)""",
        snap)
    conn.commit()
