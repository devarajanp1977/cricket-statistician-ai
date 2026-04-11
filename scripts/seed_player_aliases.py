"""Seed the player_aliases DuckDB table from player_aliases.json + auto-generated entries.

Creates a table:
    player_aliases (
        alias          VARCHAR,     -- the alias text (lowercased for matching)
        canonical_name VARCHAR,     -- full player name from kaggle_players
        cricsheet_name VARCHAR,     -- Cricsheet-format name (e.g. "SR Tendulkar")
        team           VARCHAR,     -- team name (if known)
        alias_type     VARCHAR      -- 'full_name', 'cricsheet', 'surname', 'first_name', 'nickname', 'auto_full', 'auto_surname'
    )

Usage:
    python scripts/seed_player_aliases.py
"""

import os
import json
import duckdb

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "db", "cricket.duckdb")
ALIASES_JSON = os.path.join(BASE_DIR, "data", "player_aliases.json")


def seed():
    con = duckdb.connect(DB_PATH)

    # Drop and recreate
    con.execute("DROP TABLE IF EXISTS player_aliases")
    con.execute("""
        CREATE TABLE player_aliases (
            alias          VARCHAR NOT NULL,
            canonical_name VARCHAR NOT NULL,
            cricsheet_name VARCHAR,
            team           VARCHAR,
            alias_type     VARCHAR NOT NULL
        )
    """)

    rows = []

    # ── 1. Load curated aliases from JSON ──
    if os.path.exists(ALIASES_JSON):
        with open(ALIASES_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        for p in data.get("players", []):
            canon = p["canonical_name"]
            cs_name = p.get("cricsheet_name", "")
            team = p.get("team", "")

            # Full name as alias
            rows.append((canon.lower(), canon, cs_name, team, "full_name"))

            # Cricsheet name
            if cs_name:
                rows.append((cs_name.lower(), canon, cs_name, team, "cricsheet"))

            # Surname (last word of canonical name)
            parts = canon.split()
            if len(parts) > 1:
                surname = parts[-1]
                if len(surname) > 2:  # skip very short surnames
                    rows.append((surname.lower(), canon, cs_name, team, "surname"))

            # First name
            if len(parts) > 1:
                first = parts[0]
                if len(first) > 2:
                    rows.append((first.lower(), canon, cs_name, team, "first_name"))

            # Nicknames
            for alias in p.get("aliases", []):
                rows.append((alias.lower(), canon, cs_name, team, "nickname"))

    # ── 2. Auto-generate from kaggle_players (for players NOT in JSON) ──
    curated_names = {r[1].lower() for r in rows}  # canonical names already covered

    kaggle_rows = con.execute("""
        SELECT kp.player_id, kp.player_name,
               pm.cricsheet_name
        FROM kaggle_players kp
        LEFT JOIN player_map pm ON kp.player_id = pm.kaggle_player_id
        WHERE kp.player_name IS NOT NULL
    """).fetchall()

    for player_id, full_name, cs_name in kaggle_rows:
        if full_name.lower() in curated_names:
            continue  # already covered by JSON

        cs = cs_name or ""
        # Full name
        rows.append((full_name.lower(), full_name, cs, "", "auto_full"))

        # Cricsheet name (if available)
        if cs:
            rows.append((cs.lower(), full_name, cs, "", "auto_cricsheet"))

        # Surname
        parts = full_name.split()
        if len(parts) > 1:
            surname = parts[-1]
            if len(surname) > 2:
                rows.append((surname.lower(), full_name, cs, "", "auto_surname"))

        # First name (extract from canonical full name, skip initials)
        if len(parts) > 1:
            first = parts[0]
            if len(first) > 2:  # skip initials like "K" or "AB"
                rows.append((first.lower(), full_name, cs, "", "auto_first_name"))

    # ── 3. Also add Cricsheet-only players from deliveries ──
    # These are players who appear in deliveries but may not have a kaggle mapping
    try:
        cricsheet_batters = con.execute("""
            SELECT DISTINCT batter FROM deliveries
            WHERE batter NOT IN (SELECT COALESCE(cricsheet_name,'') FROM player_map WHERE cricsheet_name IS NOT NULL)
            LIMIT 500
        """).fetchall()
        for (name,) in cricsheet_batters:
            if name and name.lower() not in curated_names:
                rows.append((name.lower(), name, name, "", "cricsheet_only"))
    except Exception:
        pass  # deliveries table may not exist in all setups

    # ── 4. Bulk insert ──
    if rows:
        con.executemany(
            "INSERT INTO player_aliases (alias, canonical_name, cricsheet_name, team, alias_type) VALUES (?, ?, ?, ?, ?)",
            rows,
        )

    count = con.execute("SELECT COUNT(*) FROM player_aliases").fetchone()[0]
    unique_players = con.execute("SELECT COUNT(DISTINCT canonical_name) FROM player_aliases").fetchone()[0]
    print(f"Seeded player_aliases: {count} alias rows for {unique_players} unique players")

    # Verify some lookups
    for test_alias in ["sachin", "master blaster", "viru", "bumrah", "ABD", "universe boss"]:
        matches = con.execute(
            "SELECT canonical_name, alias_type FROM player_aliases WHERE alias ILIKE ? LIMIT 3",
            [f"%{test_alias}%"]
        ).fetchall()
        if matches:
            names = [f"{m[0]} ({m[1]})" for m in matches]
            print(f"  '{test_alias}' → {', '.join(names)}")
        else:
            print(f"  '{test_alias}' → NO MATCH")

    con.close()


if __name__ == "__main__":
    seed()
