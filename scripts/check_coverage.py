"""Check data coverage in DuckDB."""
import duckdb

con = duckdb.connect("data/db/cricket.duckdb", read_only=True)

print("=== Match Date Range ===")
print(con.execute("SELECT MIN(date_start), MAX(date_start) FROM matches").fetchall())

print("\n=== Test Match Date Range ===")
print(con.execute("SELECT MIN(date_start), MAX(date_start) FROM matches WHERE match_type='Test'").fetchall())

print("\n=== Test Matches by Decade ===")
rows = con.execute("""
    SELECT (EXTRACT(YEAR FROM date_start)::INT / 10)*10 as decade, COUNT(*)
    FROM matches WHERE match_type='Test'
    GROUP BY decade ORDER BY decade
""").fetchall()
for r in rows:
    print(f"  {r[0]}s: {r[1]} matches")

print("\n=== Sachin in deliveries ===")
print(con.execute("SELECT COUNT(*) FROM deliveries WHERE batter ILIKE '%tendulkar%'").fetchall())

print("\n=== Sachin Centuries (100+ in an innings) ===")
rows = con.execute("""
    SELECT batter, m.match_type, m.date_start, SUM(runs_batter) as total
    FROM deliveries d JOIN matches m ON d.match_id = m.match_id
    WHERE batter ILIKE '%tendulkar%'
    GROUP BY d.match_id, d.innings_num, batter, m.match_type, m.date_start
    HAVING SUM(runs_batter) >= 100
    ORDER BY m.date_start
""").fetchall()
print(f"  Found {len(rows)} centuries")
for r in rows:
    print(f"  {r[0]} | {r[1]} | {r[2]} | {r[3]} runs")

print("\n=== Root Centuries for comparison ===")
rows = con.execute("""
    SELECT batter, m.match_type, COUNT(*) as centuries
    FROM (
        SELECT d.match_id, d.innings_num, d.batter, SUM(d.runs_batter) as runs
        FROM deliveries d
        WHERE batter ILIKE '%root%' AND batter ILIKE '%JE%'
        GROUP BY d.match_id, d.innings_num, d.batter
        HAVING SUM(d.runs_batter) >= 100
    ) sub
    JOIN matches m ON sub.match_id = m.match_id
    GROUP BY batter, m.match_type
""").fetchall()
for r in rows:
    print(f"  {r[0]} | {r[1]} | {r[2]} centuries")

con.close()
