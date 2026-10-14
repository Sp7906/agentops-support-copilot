"""SQLite storage: business data (orders, approvals) and observability data (traces, spans)."""
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders(
  order_id TEXT PRIMARY KEY, customer TEXT, item TEXT, amount REAL, status TEXT,
  delivered_on TEXT, eta TEXT, refunded INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS approvals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT, order_id TEXT, amount REAL,
  status TEXT DEFAULT 'pending', created_at REAL, decided_at REAL);
CREATE TABLE IF NOT EXISTS traces(
  id TEXT PRIMARY KEY, created_at REAL, message TEXT, route TEXT, status TEXT, total_ms REAL,
  input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, simulated INTEGER);
CREATE TABLE IF NOT EXISTS spans(
  trace_id TEXT, span_id TEXT, parent TEXT, name TEXT, kind TEXT, start_ms REAL, duration_ms REAL,
  status TEXT, input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, detail TEXT);
"""


def connect() -> sqlite3.Connection:
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _seed(con: sqlite3.Connection) -> None:
    today = date.today()
    d = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
    con.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)", [
        ("ORD-1001", "Asha", "Bluetooth Headphones", 1200, "delivered", d(-5), None, 0),
        ("ORD-1002", "Ravi", "Wireless Mouse", 3400, "shipped", None, d(2), 0),
        ("ORD-1003", "Asha", "Office Chair Cushion", 2000, "delivered", d(-45), None, 0),
        ("ORD-1004", "Meera", "4K Monitor", 18500, "delivered", d(-10), None, 0),
        ("ORD-1005", "Ravi", "USB-C Hub", 799, "processing", None, d(5), 0),
        ("ORD-1006", "Meera", "Phone Case", 950, "delivered", d(-3), None, 1),
        ("ORD-1007", "Asha", "Keyboard", 2500, "cancelled", None, None, 0),
    ])


def init(reset: bool = False) -> None:
    if reset and Path(config.DB_PATH).exists():
        os.remove(config.DB_PATH)
    con = connect()
    con.executescript(SCHEMA)
    if con.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0:
        _seed(con)
    con.commit()
    con.close()
