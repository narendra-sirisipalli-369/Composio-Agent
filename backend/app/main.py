import asyncio
import re
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select
from .config import FRONTEND_ORIGIN
from .dataset import APPS, resolve, mentioned_app, explicit_subject, in_scope
from .db import init_db, SessionLocal, Conversation, ResearchRun, Review, Claim, now
from .research import configured
from .service import latest_run, run_public, queue_run, execute_run, insights, verification_summary

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield

app = FastAPI(title="Integration Researcher API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[FRONTEND_ORIGIN], allow_methods=["*"], allow_headers=["*"])
TASKS = set()

def launch(run_id, application):
    task = asyncio.create_task(execute_run(run_id, application))
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)

@app.get("/api/health")
def health():
    return {"ok": True, "research_configured": configured()}

@app.get("/api/apps")
def applications():
    with SessionLocal() as session:
        runs = {a["id"]: run_public(session, latest_run(session, a["id"])) for a in APPS}
    def row(a):
        run = runs[a["id"]]
        findings = {}
        if run and run["status"] == "completed":
            for dimension in ("overview", "auth", "credentials", "api", "mcp"):
                claims = [c for c in run["claims"] if c["dimension"] == dimension and c["status"] == "supported"]
                findings[dimension] = {"label": claims[0]["text"] if dimension == "overview" else claims[0]["tag"].replace("_", " "), "source_url": claims[0]["source_url"]} if claims else None
        return {**a, "research_status": run["status"] if run else "unresearched",
                "buildability": run["buildability"] if run and run["status"] == "completed" else None,
                "findings": findings}
    return [row(a) for a in APPS]

@app.get("/api/apps/{app_id}")
def application(app_id: int):
    found = next((a for a in APPS if a["id"] == app_id), None)
    if not found:
        raise HTTPException(404, "Application not in dataset")
    with SessionLocal() as session:
        return {**found, "run": run_public(session, latest_run(session, app_id))}

@app.get("/api/runs/{run_id}")
def run_detail(run_id: int):
    with SessionLocal() as session:
        result = run_public(session, session.get(ResearchRun, run_id))
    if not result:
        raise HTTPException(404, "Research run not found")
    return result

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    app_id: int | None = None
    refresh: bool = False

def question_focus(message: str) -> str:
    if re.search(r"\b(credentials?|self.serve|paid plan|partner|access|approval)\b", message, re.I):
        return "credentials"
    if re.search(r"\b(oauth|auth|authentication|token|api key|scope)\b", message, re.I):
        return "auth"
    if re.search(r"\b(mcp|model context protocol)\b", message, re.I):
        return "mcp"
    if re.search(r"\b(api|rest|graphql|webhook|sdk)\b", message, re.I):
        return "api"
    if re.search(r"\b(build|blocker|integrat|feasib)\w*", message, re.I):
        return "buildability"
    return "overview"

@app.post("/api/chat")
async def chat(body: ChatRequest):
    message = body.message.strip()
    if body.app_id and not any(a["id"] == body.app_id for a in APPS):
        raise HTTPException(404, "Application not in dataset")
    if not message and not body.app_id:
        raise HTTPException(400, "Message required")
    session_id = body.session_id or str(uuid.uuid4())
    with SessionLocal.begin() as session:
        conversation = session.get(Conversation, session_id)
        if not conversation:
            conversation = Conversation(id=session_id)
            session.add(conversation)
        selected = next((a for a in APPS if a["id"] == body.app_id), None) if body.app_id else mentioned_app(message)
        subject = explicit_subject(message)
        if subject and not selected:
            return {"session_id": session_id, "type": "not_found", "message": f"{subject} is outside the supplied 100-app research set."}
        if not selected and not in_scope(message):
            return {"session_id": session_id, "type": "out_of_scope", "message": "I research only the applications in the supplied 100-app integration dataset. Ask about an app's authentication, credentials, API, MCP, or buildability."}
        if not selected and conversation.active_app_id:
            selected = next((a for a in APPS if a["id"] == conversation.active_app_id), None)
        if not selected:
            return {"session_id": session_id, "type": "need_app", "message": "Which application in the 100-app dataset should I research?"}
        conversation.active_app_id = selected["id"]
        conversation.updated_at = now()
    if not configured():
        with SessionLocal() as session:
            run = run_public(session, latest_run(session, selected["id"]))
        return {"session_id": session_id, "type": "config_required", "app": selected, "run": run, "focus": question_focus(message),
                "message": "Live research requires TAVILY_API_KEY and GEMINI_API_KEY on the backend. Existing verified results remain available."}
    run_id, created = queue_run(selected, body.refresh)
    if created:
        launch(run_id, selected)
    with SessionLocal() as session:
        run = run_public(session, session.get(ResearchRun, run_id))
    return {"session_id": session_id, "type": "research", "app": selected, "run": run, "focus": question_focus(message), "message": "Researching the selected application."}

@app.get("/api/insights")
def get_insights():
    return insights()

@app.get("/api/verification")
def get_verification():
    return verification_summary()

class ReviewRequest(BaseModel):
    claim_id: int
    verdict: str
    note: str

@app.post("/api/reviews")
def add_review(body: ReviewRequest):
    if body.verdict not in {"correct", "incorrect", "uncertain"} or not body.note.strip():
        raise HTTPException(400, "Provide a verdict and audit note")
    with SessionLocal.begin() as session:
        if not session.get(Claim, body.claim_id):
            raise HTTPException(404, "Claim not found")
        review = Review(claim_id=body.claim_id, verdict=body.verdict, note=body.note.strip())
        session.add(review)
        session.flush()
        return {"id": review.id}
