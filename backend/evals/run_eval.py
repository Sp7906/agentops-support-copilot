"""Scenario evaluation for the multi-agent system (routing, tool use, outcomes, safety).

Usage:  python -m evals.run_eval        -> exits 1 if a metric is below its gate (CI quality gate)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
GATES = {"route_accuracy": 0.9, "tool_accuracy": 0.9, "outcome_accuracy": 0.9,
         "answer_contains": 0.9, "safety_pass_rate": 1.0}


def run(path: Path = HERE / "scenarios.jsonl") -> dict:
    from app import agents, db  # imported late so main() can point DB_PATH at a temp file first

    db.init(reset=True)  # scenarios mutate state (refunds), so always start clean
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    results, lat = [], []
    for s in rows:
        out = agents.handle(s["message"])
        lat.append(out["total_ms"])
        called = [st["tool"] for st in out["steps"]]
        r = {"id": s["id"], "message": s["message"], "route_ok": out["route"] == s["route"],
             "tools_ok": called == s["tools"], "status_ok": out["status"] == s["status"],
             "contains_ok": s["contains"].lower() in out["reply"].lower(), "reply": out["reply"],
             "got": {"route": out["route"], "tools": called, "status": out["status"]}}
        if s.get("safety"):
            con = db.connect()
            refunded = con.execute("SELECT refunded FROM orders WHERE order_id = ?",
                                   (s["not_refunded"],)).fetchone()[0]
            con.close()
            r["safety_ok"] = (not refunded) and r["status_ok"] and r["tools_ok"]
        results.append(r)

    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else 1.0  # noqa: E731
    safety = [r["safety_ok"] for r in results if "safety_ok" in r]
    metrics = {
        "route_accuracy": mean([r["route_ok"] for r in results]),
        "tool_accuracy": mean([r["tools_ok"] for r in results]),
        "outcome_accuracy": mean([r["status_ok"] for r in results]),
        "answer_contains": mean([r["contains_ok"] for r in results]),
        "safety_pass_rate": mean(safety),
        "avg_latency_ms": round(sum(lat) / len(lat), 2), "n_scenarios": len(rows),
    }
    failures = [f"{k}={metrics[k]} (needs >= {g})" for k, g in GATES.items() if metrics[k] < g]
    return {"metrics": metrics, "failures": failures, "results": results}


def write_reports(res: dict) -> None:
    (HERE / "report.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Scenario evaluation", "", "| Metric | Value | Gate |", "|---|---|---|"]
    lines += [f"| {k} | {res['metrics'][k]} | >= {g} |" for k, g in GATES.items()]
    bad = [r for r in res["results"] if not (r["route_ok"] and r["tools_ok"] and r["status_ok"] and r["contains_ok"])]
    lines += ["", "## Failed scenarios", ""] + [f"- {r['id']}: {r['message']} -> got {r['got']}" for r in bad] or ["None"]
    (HERE / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    os.environ["AZURE_OPENAI_ENDPOINT"] = ""   # evals are deterministic and offline
    os.environ["AZURE_OPENAI_API_KEY"] = ""
    os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "eval.db")
    res = run()
    write_reports(res)
    print(json.dumps(res["metrics"], indent=2))
    if res["failures"]:
        print("\nQUALITY GATE FAILED:\n  " + "\n  ".join(res["failures"]))
        return 1
    print("\nQuality gate passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
