"""Multi-agent orchestrator: guardrail -> planner -> specialist agents (tools) -> composer.

Specialist agents own tools. Every step is traced. Safety rules live in the tools, not the planner."""
import json
import re

from . import config, llm, tools
from .tools import ToolError
from .tracing import Tracer, est_tokens

TOOLS = {
    "get_order": tools.get_order,
    "check_refund_eligibility": tools.check_refund_eligibility,
    "create_refund": tools.create_refund,
    "search_policy": tools.search_policy,
    "escalate": tools.escalate,
}
OWNER = {"get_order": "OrderAgent", "check_refund_eligibility": "RefundAgent", "create_refund": "RefundAgent",
         "search_policy": "PolicyAgent", "escalate": "SupportAgent"}
# A step runs only if this earlier result is truthy ("tool.field").
REQUIRES = {"check_refund_eligibility": "get_order.order_id", "create_refund": "check_refund_eligibility.eligible"}
ROUTES = {
    "order_status": ["get_order"],
    "refund": ["get_order", "check_refund_eligibility", "create_refund"],
    "policy": ["search_policy"],
    "escalation": ["escalate"],
    "needs_info": [],
}

INJECTION = re.compile(
    r"ignore (all |any |the )?(previous|prior|above) (instructions|rules)|system prompt|"
    r"reveal your (prompt|instructions)|you are now|developer mode|disregard .*(rules|instructions)", re.I)
ORDER_ID = re.compile(r"ORD-\d{4}", re.I)

PLANNER_PROMPT = (
    "You route customer-support messages. Return JSON {\"route\": one of "
    f"{sorted(ROUTES)}, \"tools\": [tool names in order]}}. Tools: {sorted(TOOLS)}. "
    "Use needs_info when an order ID is required but missing.")


def plan_rule_based(message: str) -> str:
    low = message.lower()
    has_id = bool(ORDER_ID.search(message))
    if re.search(r"\b(lawyer|legal|sue|fire|injur\w*|hospital|fraud|scam)\b", low):
        return "escalation"
    refund = re.search(r"\b(refund\w*|money back|reimburse\w*)\b", low)
    status = re.search(r"\b(where|track\w*|status|delivery|deliver\w*|arriv\w*|eta)\b", low)
    policy = re.search(r"\b(policy|how long|warranty|shipping|payment|accept|cancel\w*)\b", low)
    if refund:
        return "refund" if has_id else "needs_info"
    if status:
        return "order_status" if has_id else "needs_info"
    if policy:
        return "policy"
    return "escalation"


def build_steps(route: str, message: str, tool_names: list[str] | None = None) -> list[dict]:
    """Arguments and prerequisites are set by code, never taken from an LLM."""
    m = ORDER_ID.search(message)
    steps = []
    for t in (tool_names if tool_names is not None else ROUTES[route]):
        args: dict = {}
        if t in ("get_order", "check_refund_eligibility", "create_refund"):
            args["order_id"] = m.group(0).upper() if m else ""
        elif t == "search_policy":
            args["query"] = message
        elif t == "escalate":
            args["reason"] = message
        steps.append({"tool": t, "args": args, "requires": REQUIRES.get(t)})
    return steps


def make_plan(message: str, tr: Tracer, sp: dict) -> dict:
    if llm.enabled():
        try:
            text, t_in, t_out = llm.chat([{"role": "system", "content": PLANNER_PROMPT},
                                          {"role": "user", "content": message}], json_mode=True)
            data = json.loads(text)
            route = data["route"]
            names = [t for t in data.get("tools", []) if t in TOOLS]
            if route not in ROUTES:
                raise ValueError("unknown route")
            tr.llm_usage(sp, t_in, t_out, False)
            sp["detail"]["source"] = "llm"
            return {"route": route, "steps": build_steps(route, message, names)}
        except Exception as e:  # noqa: BLE001 - fall back to rules
            sp["detail"]["llm_error"] = str(e)[:200]
    route = plan_rule_based(message)
    plan = {"route": route, "steps": build_steps(route, message)}
    tr.llm_usage(sp, est_tokens(PLANNER_PROMPT + message), est_tokens(json.dumps(plan)), True)
    sp["detail"]["source"] = "rules"
    return plan


def _ready(step: dict, ctx: dict) -> bool:
    if not step["requires"]:
        return True
    tool, field = step["requires"].split(".")
    return bool(ctx.get(tool, {}).get(field))


def _money(x: float) -> str:
    return f"₹{x:,.0f}"


def compose_rule(route: str, status: str, ctx: dict) -> str:
    if status == "blocked":
        return "Sorry, I can't help with that. I can help with order status, refunds and store policies."
    if status == "needs_info":
        return "Could you share your order ID (for example ORD-1001)?"
    if status == "escalated":
        return f"I've passed this to a human agent (ticket {ctx['escalate']['ticket_id']}). They will contact you shortly."
    if status == "not_found":
        return "I couldn't find an order with that ID. Please check it and try again."
    if status == "error":
        return "Sorry, something went wrong on our side. Please try again."
    if route == "order_status":
        o = ctx["get_order"]
        phrase = {
            "shipped": f"has been shipped and is expected by {o['eta']}",
            "processing": f"is being processed (status: processing) and is expected by {o['eta']}",
            "delivered": f"was delivered on {o['delivered_on']}",
            "cancelled": "was cancelled",
        }.get(o["status"], f"is {o['status']}")
        return f"Order {o['order_id']} ({o['item']}) {phrase}."
    if route == "refund":
        r = ctx.get("create_refund")
        if status == "pending_approval":
            return (f"Your refund of {_money(r['amount'])} is above {_money(config.REFUND_LIMIT)}, so I've sent it to a "
                    f"human agent for approval (request #{r['approval_id']}). You'll hear back soon.")
        if r and r["status"] == "refunded":
            return (f"Done! Your refund of {_money(r['amount'])} for {ctx['get_order']['order_id']} has been issued. "
                    "It will reach your original payment method within 7 business days.")
        return f"I can't refund {ctx['get_order']['order_id']}: {ctx['check_refund_eligibility']['reason']}"
    if route == "policy":
        p = ctx["search_policy"]
        return p["text"] if p.get("found") else "I couldn't find that in our policies. Let me connect you with a human agent."
    return "How can I help?"


def compose(route: str, status: str, ctx: dict, message: str, tr: Tracer, sp: dict) -> str:
    reply = compose_rule(route, status, ctx)
    if status != "answered":
        sp["detail"]["templated"] = True  # fixed wording for safety-critical outcomes, no LLM tokens
        return reply
    if llm.enabled():
        try:
            text, t_in, t_out = llm.chat([
                {"role": "system", "content": "Write a short, friendly customer reply using ONLY the facts in the "
                                              "JSON. Do not promise anything not in the JSON."},
                {"role": "user", "content": json.dumps({"question": message, "facts": ctx, "draft": reply},
                                                       default=str)}])
            tr.llm_usage(sp, t_in, t_out, False)
            return text
        except Exception as e:  # noqa: BLE001
            sp["detail"]["llm_error"] = str(e)[:200]
    tr.llm_usage(sp, est_tokens(json.dumps(ctx, default=str) + message), est_tokens(reply), True)
    return reply


def handle(message: str) -> dict:
    tr = Tracer(message)
    ctx: dict = {}
    steps_log: list[dict] = []
    route, status, reply, approval_id = "unknown", "answered", "", None
    try:
        with tr.span("input_guard", "guardrail") as g:
            blocked = bool(INJECTION.search(message))
            g["detail"]["blocked"] = blocked
        if blocked:
            route, status = "blocked", "blocked"
        else:
            with tr.span("planner", "llm") as sp:
                plan = make_plan(message, tr, sp)
            route = plan["route"]
            if route == "needs_info":
                status = "needs_info"
            for step in plan["steps"]:
                if not _ready(step, ctx):
                    continue
                name = step["tool"]
                with tr.span(OWNER[name], "agent"):
                    with tr.span(name, "tool", args=step["args"]) as ts:
                        try:
                            out = TOOLS[name](**step["args"], **({"trace_id": tr.id} if name == "create_refund" else {}))
                        except ToolError as e:  # expected business error, not a system failure
                            out = {"error": str(e)}
                            ts["detail"]["business_error"] = str(e)
                ctx[name] = out
                steps_log.append({"agent": OWNER[name], "tool": name, "ok": "error" not in out,
                                  "output": json.dumps(out, default=str)[:300]})
            if "escalate" in ctx:
                status = "escalated"
            elif ctx.get("get_order", {}).get("error"):
                status = "not_found"
            elif ctx.get("create_refund", {}).get("status") == "pending_approval":
                status = "pending_approval"
                approval_id = ctx["create_refund"]["approval_id"]
        with tr.span("composer", "llm") as sp:
            reply = compose(route, status, ctx, message, tr, sp)
    except Exception:  # noqa: BLE001 - the span already recorded the error
        status, reply = "error", compose_rule(route, "error", ctx)
    summary = tr.finish(route, status)
    return {**summary, "reply": reply, "route": route, "status": status, "approval_id": approval_id,
            "steps": steps_log, "mode": "azure" if llm.enabled() else "offline"}
