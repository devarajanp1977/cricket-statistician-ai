"""Cricket Statistician AI — FastAPI application."""

import os
import sys
import json
import asyncio
import re
import time
import threading
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .engine import CricketQueryEngine
from .auth import (
    AUTH_ENABLED,
    AuthUser,
    get_current_user,
    require_user,
    supabase_rest,
)
from fastapi import Depends

# Knowledge base path
KB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "knowledge_base.json")
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
APP_BASE_PATH = os.getenv("APP_BASE_PATH", "").strip()
if APP_BASE_PATH and not APP_BASE_PATH.startswith("/"):
    APP_BASE_PATH = "/" + APP_BASE_PATH
APP_BASE_PATH = APP_BASE_PATH.rstrip("/")

_ADMIN_JOB_LOCK = threading.Lock()
_ADMIN_ACTIVE_JOB: str | None = None


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


# ── Auth + per-user endpoints (Supabase) ────────────────────────────────────

class ChatTurnIn(BaseModel):
    session_id: str
    role: str
    content: str
    metadata: dict | None = None


class BookmarkIn(BaseModel):
    title: str
    query: str
    answer: str | None = None
    tags: list[str] = []


@app.get("/api/auth/me")
async def auth_me(user: AuthUser | None = Depends(get_current_user)):
    """Return the current authenticated user, or null + auth_enabled flag."""
    if user is None:
        return {"user": None, "auth_enabled": AUTH_ENABLED}
    return {
        "user": {"id": user.id, "email": user.email, "role": user.role},
        "auth_enabled": AUTH_ENABLED,
    }


@app.get("/api/chat/history")
async def chat_history_list(
    session_id: str | None = None,
    limit: int = 100,
    user: AuthUser = Depends(require_user),
):
    rows = await supabase_rest.list_chat_history(user.id, session_id, limit=limit)
    return {"items": rows}


@app.post("/api/chat/history")
async def chat_history_add(
    turn: ChatTurnIn,
    user: AuthUser = Depends(require_user),
):
    saved = await supabase_rest.insert_chat_turn(
        user.id, turn.session_id, turn.role, turn.content, turn.metadata
    )
    return {"item": saved}


@app.get("/api/bookmarks")
async def bookmarks_list(user: AuthUser = Depends(require_user)):
    return {"items": await supabase_rest.list_bookmarks(user.id)}


@app.post("/api/bookmarks")
async def bookmarks_add(bm: BookmarkIn, user: AuthUser = Depends(require_user)):
    saved = await supabase_rest.add_bookmark(
        user.id, bm.title, bm.query, bm.answer, bm.tags
    )
    return {"item": saved}


@app.delete("/api/bookmarks/{bookmark_id}")
async def bookmarks_delete(
    bookmark_id: str, user: AuthUser = Depends(require_user)
):
    await supabase_rest.delete_bookmark(user.id, bookmark_id)
    return {"ok": True}


@app.on_event("shutdown")
async def _close_supabase():
    await supabase_rest.aclose()


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


def _run_script_step(name: str, func, *args, **kwargs) -> dict:
    """Run one admin step and capture structured success/failure details."""
    buf = StringIO()
    started = time.perf_counter()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            func(*args, **kwargs)
        ok = True
        error = None
    except Exception as exc:
        ok = False
        error = str(exc)
    duration = round(time.perf_counter() - started, 2)
    return {
        "name": name,
        "ok": ok,
        "error": error,
        "log": buf.getvalue().strip(),
        "duration_seconds": duration,
    }


def _format_admin_pipeline_log(step_results: list[dict], cache_result: dict | None = None) -> str:
    """Format structured admin step results into a readable log string."""
    parts: list[str] = []
    for step in step_results:
        status_label = "OK" if step.get("ok") else "ERROR"
        parts.append(f"[{status_label}] {step.get('name')} ({step.get('duration_seconds', 0):.2f}s)")
        if step.get("log"):
            parts.append(step["log"])
        if step.get("error"):
            parts.append(f"ERROR: {step['error']}")
        parts.append("")

    if cache_result is not None:
        if cache_result.get("ok"):
            parts.append(
                f"[CACHE] Invalidated query cache. data_version={cache_result.get('data_version')}"
            )
        else:
            parts.append(f"[CACHE] ERROR: {cache_result.get('error', 'cache invalidation failed')}")

    return "\n".join(parts).strip()


def _run_admin_pipeline(steps: list[tuple[str, object, dict]], invalidate_cache: bool = False) -> dict:
    """Run admin steps sequentially and return a structured status payload."""
    step_results: list[dict] = []
    successful_steps = 0

    for name, func, kwargs in steps:
        step_result = _run_script_step(name, func, **kwargs)
        step_results.append(step_result)
        if step_result.get("ok"):
            successful_steps += 1
            continue
        break

    cache_result = None
    if invalidate_cache and successful_steps > 0:
        cache_result = engine.invalidate_cache(clear_entries=True)

    has_step_failure = any(not step.get("ok") for step in step_results)
    cache_failure = cache_result is not None and not cache_result.get("ok")

    if has_step_failure or cache_failure:
        status = "partial" if successful_steps > 0 else "error"
    else:
        status = "ok"

    first_error = next((step.get("error") for step in step_results if step.get("error")), None)
    if not first_error and cache_failure:
        first_error = cache_result.get("error")

    return {
        "status": status,
        "steps": step_results,
        "log": _format_admin_pipeline_log(step_results, cache_result),
        "error": first_error,
        "cache_invalidated": bool(cache_result and cache_result.get("ok")),
        "data_version": cache_result.get("data_version") if cache_result and cache_result.get("ok") else None,
    }


def _admin_json_response(payload: dict) -> JSONResponse:
    """Convert structured admin payloads to success/error HTTP responses."""
    status_code = 200 if payload.get("status") == "ok" else 500
    return JSONResponse(status_code=status_code, content=payload)


def _admin_busy_response(requested_action: str) -> JSONResponse:
    """Return a conflict response when another admin mutation is already running."""
    active_action = _ADMIN_ACTIVE_JOB or "another admin action"
    payload = {
        "status": "error",
        "steps": [],
        "log": f"[BUSY] {requested_action} blocked while {active_action} is already running.",
        "error": f"Admin action already running: {active_action}",
        "cache_invalidated": False,
        "data_version": None,
    }
    return JSONResponse(status_code=409, content=payload)


async def _run_locked_admin_pipeline(
    action_name: str,
    steps: list[tuple[str, object, dict]],
    invalidate_cache: bool = False,
) -> JSONResponse:
    """Run a mutating admin pipeline only when no other admin mutation is active."""
    global _ADMIN_ACTIVE_JOB

    if not _ADMIN_JOB_LOCK.acquire(blocking=False):
        return _admin_busy_response(action_name)

    _ADMIN_ACTIVE_JOB = action_name
    try:
        loop = asyncio.get_running_loop()
        payload = await loop.run_in_executor(
            None,
            lambda: _run_admin_pipeline(steps, invalidate_cache=invalidate_cache),
        )
        return _admin_json_response(payload)
    finally:
        _ADMIN_ACTIVE_JOB = None
        _ADMIN_JOB_LOCK.release()


@app.post("/api/admin/refresh-cricsheet")
async def refresh_cricsheet():
    """Download latest Cricsheet data and reload into DB."""
    from download_data import download_cricsheet
    from load_cricsheet import load_cricsheet

    return await _run_locked_admin_pipeline("refresh-cricsheet", [
        ("download_cricsheet", download_cricsheet, {"force": True}),
        ("load_cricsheet", load_cricsheet, {"force": True}),
    ], invalidate_cache=True)


@app.post("/api/admin/refresh-kaggle")
async def refresh_kaggle():
    """Re-download Kaggle dataset and reload into DB."""
    from download_data import download_kaggle
    from load_kaggle import load_kaggle

    return await _run_locked_admin_pipeline("refresh-kaggle", [
        ("download_kaggle", download_kaggle, {}),
        ("load_kaggle", load_kaggle, {"force": True}),
    ], invalidate_cache=True)


@app.post("/api/admin/backfill")
async def run_backfill():
    """Run backfill to sync missing Test matches from Cricsheet into Kaggle tables."""
    from backfill_tests import backfill

    return await _run_locked_admin_pipeline("backfill", [
        ("backfill", backfill, {}),
    ], invalidate_cache=True)


@app.post("/api/admin/full-refresh")
async def full_refresh():
    """Run the complete refresh pipeline: download both sources, load, backfill."""
    from download_data import download_cricsheet, download_kaggle
    from load_cricsheet import load_cricsheet
    from load_kaggle import load_kaggle
    from backfill_tests import backfill

    return await _run_locked_admin_pipeline("full-refresh", [
        ("download_cricsheet", download_cricsheet, {"force": True}),
        ("download_kaggle", download_kaggle, {}),
        ("load_cricsheet", load_cricsheet, {"force": True}),
        ("load_kaggle", load_kaggle, {"force": True}),
        ("backfill", backfill, {}),
    ], invalidate_cache=True)


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
    cache_result = engine.invalidate_cache(clear_entries=True)
    payload = {
        "status": "ok" if cache_result.get("ok") else "error",
        "steps": [],
        "log": (
            f"[CACHE] Invalidated query cache. data_version={cache_result.get('data_version')}"
            if cache_result.get("ok")
            else f"[CACHE] ERROR: {cache_result.get('error', 'cache invalidation failed')}"
        ),
        "error": None if cache_result.get("ok") else cache_result.get("error"),
        "cache_invalidated": bool(cache_result.get("ok")),
        "data_version": cache_result.get("data_version") if cache_result.get("ok") else None,
    }
    return _admin_json_response(payload)


# ── Player Profile endpoints ────────────────────────────────────────────────

@app.post("/api/admin/seed-profiles")
async def seed_profiles():
    """Seed player_profiles table from ESPN Cricinfo (full seed)."""
    from seed_player_profiles import seed

    return await _run_locked_admin_pipeline("seed-profiles", [
        ("seed_player_profiles", seed, {"refresh": False, "report_only": False}),
    ], invalidate_cache=False)


@app.post("/api/admin/refresh-profiles")
async def refresh_profiles():
    """Incremental refresh of player_profiles (new + stale active)."""
    from seed_player_profiles import seed

    return await _run_locked_admin_pipeline("refresh-profiles", [
        ("refresh_player_profiles", seed, {"refresh": True, "report_only": False}),
    ], invalidate_cache=False)


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
