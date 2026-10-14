"""Minimal LLM-observability layer: traces made of spans (guardrail / llm / agent / tool)."""
import json
import time
import uuid
from contextlib import contextmanager

from . import config, db


def est_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def cost_usd(tokens_in: int, tokens_out: int) -> float:
    return (tokens_in * config.PRICE_IN_PER_M + tokens_out * config.PRICE_OUT_PER_M) / 1_000_000


class Tracer:
    def __init__(self, message: str):
        self.id = uuid.uuid4().hex[:12]
        self.message = message
        self.created = time.time()
        self._t0 = time.perf_counter()
        self.spans: list[dict] = []
        self._stack: list[str] = []

    def _now(self) -> float:
        return (time.perf_counter() - self._t0) * 1000

    @contextmanager
    def span(self, name: str, kind: str, **detail):
        rec = {"span_id": f"s{len(self.spans) + 1}", "parent": self._stack[-1] if self._stack else None,
               "name": name, "kind": kind, "start_ms": self._now(), "duration_ms": 0.0, "status": "ok",
               "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "detail": dict(detail)}
        self.spans.append(rec)
        self._stack.append(rec["span_id"])
        try:
            yield rec
        except Exception as e:  # noqa: BLE001 - record then re-raise
            rec["status"] = "error"
            rec["detail"]["error"] = str(e)[:300]
            raise
        finally:
            rec["duration_ms"] = self._now() - rec["start_ms"]
            self._stack.pop()

    @staticmethod
    def llm_usage(rec: dict, tokens_in: int, tokens_out: int, simulated: bool) -> None:
        rec.update(input_tokens=tokens_in, output_tokens=tokens_out, cost_usd=cost_usd(tokens_in, tokens_out))
        rec["detail"]["simulated"] = simulated

    def finish(self, route: str, status: str) -> dict:
        total = self._now()
        t_in = sum(s["input_tokens"] for s in self.spans)
        t_out = sum(s["output_tokens"] for s in self.spans)
        cost = sum(s["cost_usd"] for s in self.spans)
        simulated = int(any(s["detail"].get("simulated") for s in self.spans))
        con = db.connect()
        con.execute("INSERT INTO traces VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (self.id, self.created, self.message, route, status, total, t_in, t_out, cost, simulated))
        con.executemany(
            "INSERT INTO spans VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(self.id, s["span_id"], s["parent"], s["name"], s["kind"], s["start_ms"], s["duration_ms"],
              s["status"], s["input_tokens"], s["output_tokens"], s["cost_usd"],
              json.dumps(s["detail"], default=str)) for s in self.spans])
        con.commit()
        con.close()
        return {"trace_id": self.id, "total_ms": round(total, 2), "input_tokens": t_in,
                "output_tokens": t_out, "cost_usd": round(cost, 6), "simulated": bool(simulated)}


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    return round(v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))], 2)


def metrics() -> dict:
    con = db.connect()
    traces = [dict(r) for r in con.execute("SELECT * FROM traces")]
    n = len(traces)
    lat = [t["total_ms"] for t in traces]
    errors = sum(t["status"] == "error" for t in traces)

    def breakdown(kind: str) -> list[dict]:
        rows = con.execute(
            "SELECT name, COUNT(*) calls, AVG(duration_ms) avg_ms, SUM(status='error') errors "
            "FROM spans WHERE kind = ? GROUP BY name ORDER BY calls DESC", (kind,)).fetchall()
        return [{"name": r["name"], "calls": r["calls"], "avg_ms": round(r["avg_ms"], 3), "errors": r["errors"]}
                for r in rows]

    def count_by(col: str) -> dict:
        out: dict = {}
        for t in traces:
            out[t[col]] = out.get(t[col], 0) + 1
        return out

    pending = con.execute("SELECT COUNT(*) FROM approvals WHERE status = 'pending'").fetchone()[0]
    by_agent, by_tool = breakdown("agent"), breakdown("tool")  # must run before the connection closes
    con.close()
    return {
        "traces": n,
        "success_rate": round(1 - errors / n, 4) if n else 1.0,
        "error_rate": round(errors / n, 4) if n else 0.0,
        "latency_ms": {"avg": round(sum(lat) / n, 2) if n else 0.0, "p50": _pct(lat, 50), "p95": _pct(lat, 95)},
        "tokens": {"input": sum(t["input_tokens"] for t in traces), "output": sum(t["output_tokens"] for t in traces)},
        "cost_usd": round(sum(t["cost_usd"] for t in traces), 6),
        "simulated_share": round(sum(t["simulated"] for t in traces) / n, 2) if n else 0.0,
        "by_route": count_by("route"), "by_status": count_by("status"),
        "by_agent": by_agent, "by_tool": by_tool, "pending_approvals": pending,
    }


def recent_traces(limit: int = 15) -> list[dict]:
    con = db.connect()
    rows = [dict(r) for r in con.execute("SELECT * FROM traces ORDER BY created_at DESC LIMIT ?", (limit,))]
    con.close()
    return rows


def get_trace(trace_id: str) -> dict | None:
    con = db.connect()
    t = con.execute("SELECT * FROM traces WHERE id = ?", (trace_id,)).fetchone()
    if not t:
        con.close()
        return None
    spans = [dict(r) for r in con.execute("SELECT * FROM spans WHERE trace_id = ? ORDER BY start_ms", (trace_id,))]
    con.close()
    for s in spans:
        s["detail"] = json.loads(s["detail"])
    return {**dict(t), "spans": spans}
