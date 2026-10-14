"""MCP server: the same guarded business tools, usable from any MCP client.

Run:  python -m app.mcp_server      (needs: pip install -r requirements-mcp.txt)
"""
from mcp.server.fastmcp import FastMCP

from . import db, tools

db.init()
mcp = FastMCP("support-tools")


@mcp.tool()
def get_order(order_id: str) -> dict:
    """Look up an order by ID (for example ORD-1001)."""
    return tools.get_order(order_id)


@mcp.tool()
def check_refund_eligibility(order_id: str) -> dict:
    """Check whether an order can be refunded under the 30-day policy."""
    return tools.check_refund_eligibility(order_id)


@mcp.tool()
def create_refund(order_id: str) -> dict:
    """Refund an order. Amounts above the limit are queued for human approval, not executed."""
    return tools.create_refund(order_id)


@mcp.tool()
def search_policy(query: str) -> dict:
    """Search store policies (returns, shipping, warranty, payments)."""
    return tools.search_policy(query)


if __name__ == "__main__":
    mcp.run()
