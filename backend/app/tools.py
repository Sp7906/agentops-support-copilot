"""Business tools. Each tool enforces its own rules, so the system stays safe even if a planner
(rules or LLM) asks for something wrong. These same functions are exposed over MCP."""
import re
import time
import uuid
from datetime import date

from . import config, db


class ToolError(Exception):
    """Expected business error (e.g. order not found). Not a system failure."""


POLICIES = [
    {"title": "Returns", "text": "Items can be returned within 30 days of delivery in original condition. "
     "Refunds go to the original payment method.", "kw": "return returns refund window condition policy"},
    {"title": "Refund timeline", "text": "Approved refunds reach your account within 7 business days.",
     "kw": "refund timeline days money account how long"},
    {"title": "Shipping", "text": "Standard shipping takes 3-5 business days. Express shipping takes 1-2 business days.",
     "kw": "shipping delivery standard express time take"},
    {"title": "Cancellation", "text": "Orders can be cancelled before they are shipped. After shipping, use the return process.",
     "kw": "cancel cancellation shipped"},
    {"title": "Warranty", "text": "Electronics carry a 1 year manufacturer warranty. Warranty claims need the order ID "
     "and proof of purchase.", "kw": "warranty electronics claim guarantee laptop"},
    {"title": "Payments", "text": "We accept UPI, cards and net banking. Cash on delivery is available for orders "
     "under ₹10,000.", "kw": "payment pay upi card cod cash accept"},
]
_STOP = {"what", "is", "your", "the", "a", "an", "of", "on", "my", "do", "you", "how", "does", "to", "for", "and", "s"}


def _words(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP}


def _order(order_id: str) -> dict:
    con = db.connect()
    row = con.execute("SELECT * FROM orders WHERE order_id = ?", (order_id.upper(),)).fetchone()
    con.close()
    if not row:
        raise ToolError(f"Order {order_id} not found.")
    return dict(row)


def get_order(order_id: str) -> dict:
    return _order(order_id)


def check_refund_eligibility(order_id: str) -> dict:
    o = _order(order_id)
    base = {"order_id": o["order_id"], "amount": o["amount"]}
    if o["refunded"]:
        return {**base, "eligible": False, "reason": "This order has already been refunded."}
    if o["status"] != "delivered":
        return {**base, "eligible": False,
                "reason": f"Only delivered orders can be refunded (current status: {o['status']})."}
    days = (date.today() - date.fromisoformat(o["delivered_on"])).days
    if days > config.REFUND_WINDOW_DAYS:
        return {**base, "eligible": False,
                "reason": f"The 30-day refund window has passed (delivered {days} days ago)."}
    return {**base, "eligible": True, "reason": "Eligible for refund.", "days_since_delivery": days}


def create_refund(order_id: str, trace_id: str = "") -> dict:
    check = check_refund_eligibility(order_id)  # never trust the caller: re-check here
    if not check["eligible"]:
        return {"status": "rejected", "reason": check["reason"], "amount": check["amount"]}
    amount = check["amount"]
    con = db.connect()
    try:
        if amount > config.REFUND_LIMIT:
            cur = con.execute("INSERT INTO approvals(trace_id, order_id, amount, created_at) VALUES (?,?,?,?)",
                              (trace_id, check["order_id"], amount, time.time()))
            con.commit()
            return {"status": "pending_approval", "approval_id": cur.lastrowid, "amount": amount}
        con.execute("UPDATE orders SET refunded = 1 WHERE order_id = ?", (check["order_id"],))
        con.commit()
        return {"status": "refunded", "amount": amount}
    finally:
        con.close()


def decide_approval(approval_id: int, approve: bool) -> dict:
    con = db.connect()
    try:
        row = con.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if not row:
            raise ToolError("Approval not found.")
        if row["status"] != "pending":
            raise ToolError(f"Approval already {row['status']}.")
        con.execute("UPDATE approvals SET status = ?, decided_at = ? WHERE id = ?",
                    ("approved" if approve else "rejected", time.time(), approval_id))
        if approve:
            con.execute("UPDATE orders SET refunded = 1 WHERE order_id = ?", (row["order_id"],))
        con.commit()
        return {"id": approval_id, "status": "approved" if approve else "rejected", "order_id": row["order_id"]}
    finally:
        con.close()


def search_policy(query: str) -> dict:
    q = _words(query)
    best, score = None, 0
    for p in POLICIES:
        s = len(q & _words(p["kw"] + " " + p["title"] + " " + p["text"]))
        if s > score:
            best, score = p, s
    if not best:
        return {"found": False}
    return {"found": True, "title": best["title"], "text": best["text"]}


def escalate(reason: str) -> dict:
    return {"escalated": True, "ticket_id": "HUM-" + uuid.uuid4().hex[:6].upper(), "reason": reason[:200]}
