"""Batch research and export. Run from backend/: python -m app.cli ..."""
import argparse
import asyncio
import json
import shutil
from pathlib import Path
from .config import ROOT
from .dataset import APPS, resolve
from .db import init_db, SessionLocal, ResearchRun
from .research import configured
from .service import queue_run, execute_run, insights, verification_summary, latest_run, run_public

async def batch(apps, concurrency: int, refresh: bool):
    if not configured():
        raise SystemExit("Set TAVILY_API_KEY and GEMINI_API_KEY in .env before running research.")
    sem = asyncio.Semaphore(concurrency)
    async def one(app):
        async with sem:
            run_id, created = queue_run(app, refresh)
            if created:
                print(f"[{app['id']:03}] researching {app['name']} (run {run_id})", flush=True)
                await execute_run(run_id, app)
            else:
                print(f"[{app['id']:03}] reused {app['name']} (run {run_id})", flush=True)
    await asyncio.gather(*(one(app) for app in apps))

def export(path: Path):
    from .case_study import render_case_study
    with SessionLocal() as session:
        apps = [{**app, "run": run_public(session, latest_run(session, app["id"]))} for app in APPS]
    data = {"apps": apps, "insights": insights(), "verification": verification_summary()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    render_case_study(data, ROOT / "case-study" / "index.html")
    shutil.copyfile(ROOT / "case-study" / "index.html", ROOT / "index.html")
    (ROOT / "frontend" / "public").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "case-study" / "index.html", ROOT / "frontend" / "public" / "case-study.html")
    print(f"Exported {path} and case-study/index.html")

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Research a named app or the entire dataset")
    run.add_argument("--app")
    run.add_argument("--all", action="store_true")
    run.add_argument("--limit", type=int, default=100)
    run.add_argument("--concurrency", type=int, default=3)
    run.add_argument("--refresh", action="store_true")
    exp = sub.add_parser("export", help="Export saved results and generate case study")
    exp.add_argument("--output", type=Path, default=ROOT / "data" / "research-export.json")
    args = parser.parse_args()
    init_db()
    if args.command == "export":
        export(args.output)
        return
    if args.app:
        app = resolve(args.app)
        if not app:
            raise SystemExit(f"{args.app} is outside the 100-app dataset")
        apps = [app]
    elif args.all:
        apps = APPS[:args.limit]
    else:
        raise SystemExit("Choose --app NAME or --all")
    asyncio.run(batch(apps, max(1, args.concurrency), args.refresh))

if __name__ == "__main__":
    main()
