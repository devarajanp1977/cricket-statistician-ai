"""Cricket Statistician AI — FastAPI application."""

import os
import sys
import json
import asyncio
import re
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .engine import CricketQueryEngine

# Knowledge base path
KB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "knowledge_base.json")
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
APP_BASE_PATH = os.getenv("APP_BASE_PATH", "").strip()
if APP_BASE_PATH and not APP_BASE_PATH.startswith("/"):
    APP_BASE_PATH = "/" + APP_BASE_PATH
APP_BASE_PATH = APP_BASE_PATH.rstrip("/")


def _load_kb() -> dict:
    """Load the knowledge base JSON file."""
    if os.path.exists(KB_PATH):
        with open(KB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"facts": [], "pending": []}


def _save_kb(kb: dict):
    """Save the knowledge base JSON file."""
    with open(KB_PATH, "w", encoding="utf-8") as f:
        json.dump(kb, f, indent=2, ensure_ascii=False)


def _render_frontend_html(filename: str) -> HTMLResponse:
    """Serve frontend HTML with deployment-time placeholders replaced."""
    html_path = os.path.join(FRONTEND_DIR, filename)
    if not os.path.exists(html_path):
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace("__APP_BASE_PATH__", APP_BASE_PATH)
    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )

# Add scripts/ to path so we can import refresh helpers
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

app = FastAPI(
    title="Cricket Statistician AI",
    description="Ask natural-language questions about cricket — powered by GPT-4.1 + DuckDB",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = CricketQueryEngine()


class HistoryTurn(BaseModel):
    question: str
    context_summary: str = ""
    sql: str = ""


class AskRequest(BaseModel):
    question: str
    history: list[HistoryTurn] = []


class AskResponse(BaseModel):
    question: str
    sql: str | None
    columns: list[str]
    rows: list[list]
    answer: str
    error: str | None
    chart_config: dict | None = None
    context_summary: str | None = None
    display_hint: dict | None = None
    sections: dict | None = None
    model_used: str | None = None
    cached: bool = False
    candidates: list[dict] | None = None
    original_question: str | None = None
    profile: dict | None = None


@app.get("/")
async def root():
    """Serve the chat UI."""
    return _render_frontend_html("index.html")


@app.post("/api/ask", response_model=AskResponse)
async def ask(req: AskRequest):
    """Ask a cricket statistics question in natural language."""
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    history = [h.model_dump() for h in req.history[-10:]]  # Last 10 turns
    result = engine.ask(req.question, history=history)

    # If GPT discovered a new fact, add to pending queue
    if result.get("new_fact"):
        try:
            kb = _load_kb()
            # Sanitize: strip anything that looks like prompt injection, limit length
            fact_text = result["new_fact"][:200].strip()
            fact_text = re.sub(r'(?i)(ignore|forget|disregard|override|system|prompt)\s+(all|previous|above|instructions)', '', fact_text).strip()
            if len(fact_text) > 10:  # Only save meaningful facts
                next_id = max([f["id"] for f in kb["facts"]] + [f["id"] for f in kb["pending"]] + [0]) + 1
                kb["pending"].append({
                    "id": next_id,
                    "text": fact_text,
                    "category": "discovered",
                    "active": False,
                    "source": result["question"][:100]
                })
                _save_kb(kb)
        except Exception:
            pass  # Don't fail the response for KB issues

    # Remove internal-only field before returning
    result.pop("new_fact", None)
    return AskResponse(**result)


@app.get("/api/stats")
async def db_stats():
    """Return database table row counts."""
    try:
        return engine.get_db_stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/rate-limits")
async def rate_limits():
    """Return per-model rate limit status across all 4 models."""
    import time
    from .engine import DEFAULT_MODEL, FALLBACK_MODELS, _rate_limit_until, _rate_limit_remaining, _llm_call_count

    now = time.time()
    models = [DEFAULT_MODEL] + FALLBACK_MODELS
    result = []
    total_remaining = 0
    total_limit = 0
    has_header_data = False
    for model in models:
        blocked_until = _rate_limit_until.get(model, 0)
        is_blocked = now < blocked_until
        wait_secs = max(0, int(blocked_until - now)) if is_blocked else 0
        info = {
            "model": model,
            "available": not is_blocked,
            "wait_seconds": wait_secs,
        }
        # Include remaining call counts from headers if available
        header_info = _rate_limit_remaining.get(model)
        if header_info and header_info.get("remaining", -1) >= 0:
            has_header_data = True
            info["remaining"] = header_info["remaining"]
            info["limit"] = header_info.get("limit", -1)
            if not is_blocked:
                total_remaining += header_info["remaining"]
                total_limit += header_info.get("limit", 0)
        result.append(info)
    available_count = sum(1 for m in result if m["available"])
    resp = {"models": result, "available_count": available_count, "total_count": len(models), "llm_calls": _llm_call_count}
    if has_header_data:
        resp["total_remaining"] = total_remaining
        resp["total_limit"] = total_limit
    return resp


# ── Admin / Data Management endpoints ───────────────────────────────────────

@app.get("/admin")
async def admin_page():
    """Serve the data management UI."""
    return _render_frontend_html("admin.html")


@app.get("/api/admin/status")
async def admin_status():
    """Return detailed data status: table counts, date ranges, source info."""
    import duckdb
    db_path = engine.db_path
    con = duckdb.connect(db_path, read_only=True)
    try:
        status = {"tables": {}, "sources": {}}

        # All table counts
        tables = con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
        for (table,) in tables:
            count = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            status["tables"][table] = count

        # Kaggle Test match date range
        try:
            r = con.execute('''
                SELECT MIN("Match Start Date"), MAX("Match Start Date"), COUNT(*)
                FROM kaggle_matches
            ''').fetchone()
            status["sources"]["kaggle_tests"] = {
                "min_date": str(r[0]) if r[0] else None,
                "max_date": str(r[1]) if r[1] else None,
                "match_count": r[2],
            }
        except Exception:
            pass

        # Cricsheet match date range by type
        try:
            rows = con.execute('''
                SELECT match_type, MIN(date_start), MAX(date_start), COUNT(*)
                FROM matches
                GROUP BY match_type
                ORDER BY COUNT(*) DESC
            ''').fetchall()
            status["sources"]["cricsheet"] = [
                {"type": r[0], "min_date": str(r[1]), "max_date": str(r[2]), "count": r[3]}
                for r in rows
            ]
        except Exception:
            pass

        # Player map stats
        try:
            total = con.execute("SELECT COUNT(*) FROM player_map").fetchone()[0]
            mapped = con.execute(
                "SELECT COUNT(*) FROM player_map WHERE cricsheet_id IS NOT NULL AND kaggle_player_id IS NOT NULL"
            ).fetchone()[0]
            status["sources"]["player_map"] = {"total": total, "fully_mapped": mapped}
        except Exception:
            pass

        return status
    finally:
        con.close()


def _run_script_sync(func, *args, **kwargs) -> str:
    """Run a script function, capture its print output."""
    buf = StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            func(*args, **kwargs)
        return buf.getvalue()
    except Exception as e:
        return buf.getvalue() + f"\nERROR: {e}"


@app.post("/api/admin/refresh-cricsheet")
async def refresh_cricsheet():
    """Download latest Cricsheet data and reload into DB."""
    from download_data import download_cricsheet
    from load_cricsheet import load_cricsheet

    loop = asyncio.get_event_loop()
    log = await loop.run_in_executor(None, lambda: (
        _run_script_sync(download_cricsheet, force=True)
        + "\n"
        + _run_script_sync(load_cricsheet, force=True)
    ))
    return {"status": "ok", "log": log}


@app.post("/api/admin/refresh-kaggle")
async def refresh_kaggle():
    """Re-download Kaggle dataset and reload into DB."""
    from download_data import download_kaggle
    from load_kaggle import load_kaggle

    loop = asyncio.get_event_loop()
    log = await loop.run_in_executor(None, lambda: (
        _run_script_sync(download_kaggle)
        + "\n"
        + _run_script_sync(load_kaggle, force=True)
    ))
    return {"status": "ok", "log": log}


@app.post("/api/admin/backfill")
async def run_backfill():
    """Run backfill to sync missing Test matches from Cricsheet into Kaggle tables."""
    from backfill_tests import backfill

    loop = asyncio.get_event_loop()
    log = await loop.run_in_executor(None, lambda: _run_script_sync(backfill))
    return {"status": "ok", "log": log}


@app.post("/api/admin/full-refresh")
async def full_refresh():
    """Run the complete refresh pipeline: download both sources, load, backfill."""
    from download_data import download_cricsheet, download_kaggle
    from load_cricsheet import load_cricsheet
    from load_kaggle import load_kaggle
    from backfill_tests import backfill

    loop = asyncio.get_event_loop()
    log = await loop.run_in_executor(None, lambda: (
        _run_script_sync(download_cricsheet, force=True)
        + "\n" + _run_script_sync(download_kaggle)
        + "\n" + _run_script_sync(load_cricsheet, force=True)
        + "\n" + _run_script_sync(load_kaggle, force=True)
        + "\n" + _run_script_sync(backfill)
    ))
    return {"status": "ok", "log": log}


# ── Knowledge Base endpoints ────────────────────────────────────────────────

@app.get("/api/admin/knowledge")
async def get_knowledge():
    """Return all facts (active + inactive) and pending discoveries."""
    return _load_kb()


class FactCreate(BaseModel):
    text: str
    category: str = "general"


@app.post("/api/admin/knowledge")
async def add_fact(fact: FactCreate):
    """Add a new fact to the knowledge base."""
    text = fact.text.strip()[:200]
    if not text:
        raise HTTPException(status_code=400, detail="Fact text cannot be empty")
    kb = _load_kb()
    next_id = max([f["id"] for f in kb["facts"]] + [f["id"] for f in kb["pending"]] + [0]) + 1
    new_fact = {"id": next_id, "text": text, "category": fact.category, "active": True}
    kb["facts"].append(new_fact)
    _save_kb(kb)
    return new_fact


@app.put("/api/admin/knowledge/{fact_id}")
async def update_fact(fact_id: int, fact: FactCreate):
    """Update an existing fact's text and category."""
    kb = _load_kb()
    for f in kb["facts"]:
        if f["id"] == fact_id:
            f["text"] = fact.text.strip()[:200]
            f["category"] = fact.category
            _save_kb(kb)
            return f
    raise HTTPException(status_code=404, detail="Fact not found")


@app.patch("/api/admin/knowledge/{fact_id}/toggle")
async def toggle_fact(fact_id: int):
    """Toggle a fact's active status."""
    kb = _load_kb()
    for f in kb["facts"]:
        if f["id"] == fact_id:
            f["active"] = not f["active"]
            _save_kb(kb)
            return f
    raise HTTPException(status_code=404, detail="Fact not found")


@app.delete("/api/admin/knowledge/{fact_id}")
async def delete_fact(fact_id: int):
    """Delete a fact from the knowledge base."""
    kb = _load_kb()
    kb["facts"] = [f for f in kb["facts"] if f["id"] != fact_id]
    _save_kb(kb)
    return {"status": "ok"}


@app.post("/api/admin/knowledge/pending/{fact_id}/approve")
async def approve_pending(fact_id: int):
    """Approve a pending fact — move it to active facts."""
    kb = _load_kb()
    pending = None
    for i, f in enumerate(kb["pending"]):
        if f["id"] == fact_id:
            pending = kb["pending"].pop(i)
            break
    if not pending:
        raise HTTPException(status_code=404, detail="Pending fact not found")
    pending["active"] = True
    pending.pop("source", None)
    kb["facts"].append(pending)
    _save_kb(kb)
    return pending


@app.post("/api/admin/knowledge/pending/{fact_id}/reject")
async def reject_pending(fact_id: int):
    """Reject and delete a pending fact."""
    kb = _load_kb()
    kb["pending"] = [f for f in kb["pending"] if f["id"] != fact_id]
    _save_kb(kb)
    return {"status": "ok"}


# ── Query Cache endpoints ───────────────────────────────────────────────────

@app.get("/api/admin/cache-stats")
async def cache_stats():
    """Return query cache statistics."""
    return engine.get_cache_stats()


@app.post("/api/admin/cache-clear")
async def cache_clear():
    """Clear the query cache."""
    engine.clear_cache()
    return {"status": "ok"}


# ── Player Profile endpoints ────────────────────────────────────────────────

@app.post("/api/admin/seed-profiles")
async def seed_profiles():
    """Seed player_profiles table from ESPN Cricinfo (full seed)."""
    from seed_player_profiles import seed

    loop = asyncio.get_event_loop()
    log = await loop.run_in_executor(None, lambda: _run_script_sync(seed, refresh=False, report_only=False))
    return {"status": "ok", "log": log}


@app.post("/api/admin/refresh-profiles")
async def refresh_profiles():
    """Incremental refresh of player_profiles (new + stale active)."""
    from seed_player_profiles import seed

    loop = asyncio.get_event_loop()
    log = await loop.run_in_executor(None, lambda: _run_script_sync(seed, refresh=True, report_only=False))
    return {"status": "ok", "log": log}


@app.get("/api/admin/profile-status")
async def profile_status():
    """Return player_profiles coverage stats."""
    import duckdb
    db_path = engine.db_path
    con = duckdb.connect(db_path, read_only=True)
    try:
        # Check if table exists
        tables = [r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main' AND table_name='player_profiles'"
        ).fetchall()]
        if not tables:
            return {"exists": False, "total": 0}

        total = con.execute("SELECT COUNT(*) FROM player_profiles").fetchone()[0]
        fields = {}
        for col in ["batting_style", "bowling_style", "playing_role", "country", "dob",
                     "debut_year", "headshot_url", "major_teams", "is_active"]:
            filled = con.execute(
                f'SELECT COUNT(*) FROM player_profiles WHERE "{col}" IS NOT NULL'
            ).fetchone()[0]
            fields[col] = {"filled": filled, "pct": round(100 * filled / total, 1) if total else 0}

        # Cricsheet total for coverage calc
        cs_total = con.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        return {
            "exists": True,
            "total": total,
            "cricsheet_total": cs_total,
            "coverage_pct": round(100 * total / cs_total, 1) if cs_total else 0,
            "fields": fields,
        }
    finally:
        con.close()


@app.get("/api/player/{cricsheet_id}/profile")
async def get_player_profile(cricsheet_id: str):
    """Get a single player's profile by cricsheet_id."""
    import duckdb
    db_path = engine.db_path
    con = duckdb.connect(db_path, read_only=True)
    try:
        row = con.execute("""
            SELECT cricsheet_id, cricinfo_id, full_name, first_name, last_name, display_name,
                   batting_style, bowling_style, playing_role, country, dob, debut_year,
                   is_active, gender, birth_place, jersey_number, major_teams, headshot_url
            FROM player_profiles WHERE cricsheet_id = ?
        """, [cricsheet_id]).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Player profile not found")
        cols = ["cricsheet_id", "cricinfo_id", "full_name", "first_name", "last_name",
                "display_name", "batting_style", "bowling_style", "playing_role", "country",
                "dob", "debut_year", "is_active", "gender", "birth_place", "jersey_number",
                "major_teams", "headshot_url"]
        return {cols[i]: (str(row[i]) if row[i] is not None and cols[i] == "dob" else row[i]) for i in range(len(cols))}
    finally:
        con.close()
