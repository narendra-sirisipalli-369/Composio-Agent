import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
from sqlalchemy.orm import joinedload
from .config import GEMINI_MODEL, RESEARCH_TTL_HOURS
from .db import SessionLocal, ResearchRun, Source, Claim, Review, now
from .research import GRAPH, assess_buildability, configured

def latest_run(session, app_id: int):
    return session.scalar(select(ResearchRun).where(ResearchRun.app_id == app_id).order_by(ResearchRun.id.desc()).limit(1))

def run_public(session, run: ResearchRun | None):
    if not run:
        return None
    sources = session.scalars(select(Source).where(Source.run_id == run.id)).all()
    claims = session.scalars(select(Claim).where(Claim.run_id == run.id)).all()
    source_map = {source.id: source for source in sources}
    return {
        "id": run.id, "app_id": run.app_id, "status": run.status, "stage": run.stage,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "error": run.error, "buildability": run.buildability, "blocker": run.blocker,
        "sources": [{"id": s.id, "url": s.url, "title": s.title, "retrieved_at": s.retrieved_at.isoformat()} for s in sources],
        "claims": [{"id": c.id, "dimension": c.dimension, "tag": c.tag, "text": c.text,
                    "evidence": c.evidence, "status": c.status, "reason": c.reason,
                    "source_url": source_map[c.source_id].url if c.source_id in source_map else None} for c in claims],
    }

def recent(run):
    if not run or run.status != "completed" or not run.completed_at:
        return False
    completed = run.completed_at.replace(tzinfo=timezone.utc) if run.completed_at.tzinfo is None else run.completed_at
    return datetime.now(timezone.utc) - completed < timedelta(hours=RESEARCH_TTL_HOURS)

RUN_TIMEOUT_MINUTES = 10

def abandoned(run):
    if not run or run.status not in {"queued", "running"}:
        return False
    started = run.started_at.replace(tzinfo=timezone.utc) if run.started_at.tzinfo is None else run.started_at
    return datetime.now(timezone.utc) - started > timedelta(minutes=RUN_TIMEOUT_MINUTES)

def queue_run(app: dict, refresh: bool = False):
    with SessionLocal.begin() as session:
        old = latest_run(session, app["id"])
        if old and old.status in {"queued", "running"} and abandoned(old):
            old.status, old.stage = "failed", "Research failed"
            old.error = "Run abandoned: exceeded timeout without completing (likely interrupted by a server restart)"
            old.completed_at = now()
            old = None
        if old and (old.status in {"queued", "running"} or (not refresh and recent(old))):
            return old.id, False
        run = ResearchRun(app_id=app["id"], model=GEMINI_MODEL)
        session.add(run)
        session.flush()
        return run.id, True

async def execute_run(run_id: int, app: dict):
    with SessionLocal.begin() as session:
        run = session.get(ResearchRun, run_id)
        run.status, run.stage = "running", "Searching and extracting sources"
    try:
        if not configured():
            raise RuntimeError("TAVILY_API_KEY and GEMINI_API_KEY must be configured on the backend")
        result = {"sources": [], "claims": [], "verified": [], "errors": []}
        researched = set()
        async for update in GRAPH.astream({"app": app, **result}, config={"max_concurrency": 4}, stream_mode="updates"):
            stage = None
            for node, values in update.items():
                if node == "discover":
                    result["sources"] = values.get("sources", [])
                    stage = f'Extracted {len(result["sources"])} sources; researching four dimensions'
                elif node in {"auth", "credentials", "api", "mcp"}:
                    researched.add(node)
                    result["claims"].extend(values.get("claims", []))
                    stage = f"Researched {len(researched)} of 4 dimensions"
                elif node == "verify":
                    result["verified"] = values.get("verified", [])
                    result["sources"] = values.get("sources", result["sources"])
                    stage = "Verification complete"
                result["errors"].extend(values.get("errors", []))
            if stage:
                with SessionLocal.begin() as session:
                    session.get(ResearchRun, run_id).stage = stage
        sources = result.get("sources", [])
        if not sources:
            raise RuntimeError("No extractable documentation found. " + "; ".join(result.get("errors", [])))
        if not result.get("claims") and any(error.startswith(tuple(f"{dimension}:" for dimension in ("auth", "credentials", "api", "mcp"))) for error in result.get("errors", [])):
            raise RuntimeError("All research dimensions failed. " + "; ".join(result.get("errors", [])))
        with SessionLocal.begin() as session:
            run = session.get(ResearchRun, run_id)
            source_ids = {}
            for item in sources:
                source = Source(run_id=run_id, url=item["url"], title=item["title"], content=item["content"])
                session.add(source)
                session.flush()
                source_ids[item["url"]] = source.id
            for item in result.get("verified", []):
                session.add(Claim(run_id=run_id, source_id=source_ids.get(item["source_url"]),
                    dimension=item["dimension"], tag=item["tag"][:50], text=item["text"],
                    evidence=item["evidence"], status=item["status"], reason=item["reason"]))
            run.buildability, run.blocker = assess_buildability(result.get("verified", []))
            run.status = "completed"
            run.stage = "Verification complete"
            run.completed_at = now()
            if result.get("errors"):
                run.error = "; ".join(result["errors"])
    except Exception as exc:
        with SessionLocal.begin() as session:
            run = session.get(ResearchRun, run_id)
            run.status, run.stage = "failed", "Research failed"
            run.error = f"{type(exc).__name__}: {exc}"
            run.completed_at = now()

def insights():
    from .dataset import APPS
    with SessionLocal() as session:
        completed = session.scalars(select(ResearchRun).where(ResearchRun.status == "completed").order_by(ResearchRun.id.desc())).all()
        latest = {}
        for run in completed:
            latest.setdefault(run.app_id, run)
        runs = list(latest.values())
        tags = {}
        dimensions = {}
        verdicts = {}
        patterns = {dimension: {} for dimension in ("auth", "credentials", "api", "mcp")}
        coverage_by_category = {category: 0 for category in {a["category"] for a in APPS}}
        for run in runs:
            app = next(a for a in APPS if a["id"] == run.app_id)
            coverage_by_category[app["category"]] += 1
            verdicts[run.buildability] = verdicts.get(run.buildability, 0) + 1
            app_patterns = {dimension: set() for dimension in patterns}
            for claim in session.scalars(select(Claim).where(Claim.run_id == run.id, Claim.status == "supported")):
                tags[claim.tag] = tags.get(claim.tag, 0) + 1
                dimensions[claim.dimension] = dimensions.get(claim.dimension, 0) + 1
                if claim.dimension in app_patterns:
                    app_patterns[claim.dimension].add(claim.tag)
            for dimension, app_tags in app_patterns.items():
                for tag in app_tags:
                    patterns[dimension][tag] = patterns[dimension].get(tag, 0) + 1
        return {"total_apps": len(APPS), "researched_apps": len(runs), "unresearched_apps": len(APPS)-len(runs),
                "categories": {c: sum(a["category"] == c for a in APPS) for c in sorted({a["category"] for a in APPS})},
                "supported_claim_tags": tags, "supported_claim_dimensions": dimensions, "buildability": verdicts,
                "app_patterns": patterns, "coverage_by_category": coverage_by_category,
                "note": "Pattern values count distinct apps with at least one supported claim of that tag. Apps can have more than one tag; denominator is completed apps only."}

def verification_summary():
    with SessionLocal() as session:
        statuses = {status: count for status, count in session.execute(select(Claim.status, func.count()).group_by(Claim.status))}
        reviews = session.scalars(select(Review).order_by(Review.reviewed_at.desc(), Review.id.desc())).all()
        latest_reviews = {}
        for review in reviews:
            latest_reviews.setdefault(review.claim_id, review)
        judged = [review for review in latest_reviews.values() if review.verdict in {"correct", "incorrect"}]
        first_pass_hits = sum(review.verdict == "correct" for review in judged)
        final_hits = sum((review.claim.status == "supported") == (review.verdict == "correct") for review in judged)
        examples = session.scalars(select(Claim).where(Claim.status != "supported").order_by(Claim.id.desc()).limit(10)).all()
        return {"automatic": statuses, "human_reviews": len(latest_reviews), "audited_claims": len(judged),
                "first_pass_accuracy": round(first_pass_hits / len(judged) * 100, 1) if judged else None,
                "after_verification_accuracy": round(final_hits / len(judged) * 100, 1) if judged else None,
                "review_verdicts": {v: sum(r.verdict == v for r in latest_reviews.values()) for v in {r.verdict for r in latest_reviews.values()}},
                "examples": [{"id": c.id, "claim": c.text, "status": c.status, "reason": c.reason,
                              "evidence": c.evidence, "source_url": c.source.url if c.source else None} for c in examples],
                "note": "Accuracy is measured only on human-audited claims. First pass counts every generated claim; after verification counts only supported claims." if judged else "Human accuracy cannot be calculated until a sample has been reviewed against the original documentation."}
