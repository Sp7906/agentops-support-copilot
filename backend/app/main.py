from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import agents, db, demo, tools, tracing


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    yield


app = FastAPI(title="AgentOps Support Copilot", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class Chat(BaseModel):
    message: str = Field(min_length=2, max_length=1000)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat")
def chat(body: Chat):
    return agents.handle(body.message)


@app.get("/metrics")
def metrics():
    return tracing.metrics()


@app.get("/traces")
def traces(limit: int = 15):
    return tracing.recent_traces(min(limit, 100))


@app.get("/traces/{trace_id}")
def trace(trace_id: str):
    t = tracing.get_trace(trace_id)
    if not t:
        raise HTTPException(404, "Trace not found")
    return t


@app.get("/orders")
def orders():
    con = db.connect()
    rows = [dict(r) for r in con.execute("SELECT * FROM orders")]
    con.close()
    return rows


@app.get("/approvals")
def approvals():
    con = db.connect()
    rows = [dict(r) for r in con.execute("SELECT * FROM approvals ORDER BY id DESC")]
    con.close()
    return rows


@app.post("/approvals/{approval_id}/{action}")
def decide(approval_id: int, action: str):
    if action not in ("approve", "reject"):
        raise HTTPException(400, "action must be approve or reject")
    try:
        return tools.decide_approval(approval_id, action == "approve")
    except tools.ToolError as e:
        raise HTTPException(409, str(e)) from e


@app.post("/demo/traffic")
def traffic(n: int = 20):
    return {"generated": demo.run(min(n, 100))}
