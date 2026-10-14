import pytest

from app import db, tools
from app.tools import ToolError


def test_refund_window_and_duplicates():
    assert tools.check_refund_eligibility("ORD-1001")["eligible"]
    assert "30-day" in tools.check_refund_eligibility("ORD-1003")["reason"]
    assert "already" in tools.check_refund_eligibility("ORD-1006")["reason"]
    assert not tools.check_refund_eligibility("ORD-1002")["eligible"]  # not delivered


def test_small_refund_executes_once():
    assert tools.create_refund("ORD-1001")["status"] == "refunded"
    assert tools.create_refund("ORD-1001")["status"] == "rejected"  # cannot refund twice


def test_large_refund_needs_approval_then_executes():
    r = tools.create_refund("ORD-1004")
    assert r["status"] == "pending_approval"
    con = db.connect()
    assert con.execute("SELECT refunded FROM orders WHERE order_id='ORD-1004'").fetchone()[0] == 0
    con.close()
    tools.decide_approval(r["approval_id"], True)
    con = db.connect()
    assert con.execute("SELECT refunded FROM orders WHERE order_id='ORD-1004'").fetchone()[0] == 1
    con.close()
    with pytest.raises(ToolError):
        tools.decide_approval(r["approval_id"], True)  # already decided


def test_unknown_order_and_policy_search():
    with pytest.raises(ToolError):
        tools.get_order("ORD-0000")
    assert "3-5" in tools.search_policy("how long does shipping take")["text"]
    assert tools.search_policy("zzz qqq")["found"] is False
