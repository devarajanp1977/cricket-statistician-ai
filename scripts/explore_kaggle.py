"""Explore Kaggle table schemas and sample data."""
import duckdb

con = duckdb.connect("data/db/cricket.duckdb", read_only=True)

for table in ["kaggle_matches", "kaggle_batting", "kaggle_bowling", "kaggle_fow", "kaggle_partnerships", "kaggle_players"]:
    print(f"\n=== {table} ===")
    cols = con.execute(f"DESCRIBE {table}").fetchall()
    for c in cols:
        print(f"  {c[0]:30s} {c[1]}")
    print(f"\n  Sample rows:")
    rows = con.execute(f"SELECT * FROM {table} LIMIT 2").fetchall()
    col_names = [c[0] for c in cols]
    for row in rows:
        for cn, val in zip(col_names, row):
            print(f"    {cn}: {val}")
        print("    ---")

# Check Sachin in kaggle_batting
print("\n=== Sachin in kaggle_batting ===")
rows = con.execute("""
    SELECT Batter, Runs, match_id FROM kaggle_batting
    WHERE Batter ILIKE '%tendulkar%' AND CAST(Runs AS VARCHAR) NOT LIKE '%*%'
    ORDER BY CAST(REGEXP_REPLACE(Runs, '[^0-9]', '', 'g') AS INT) DESC
    LIMIT 10
""").fetchall()
for r in rows:
    print(f"  {r}")

# Check centuries
print("\n=== Sachin Test Centuries (kaggle_batting) ===")
rows = con.execute("""
    SELECT COUNT(*) FROM kaggle_batting
    WHERE Batter ILIKE '%tendulkar%'
    AND CAST(REGEXP_REPLACE(Runs, '[^0-9]', '', 'g') AS INT) >= 100
""").fetchall()
print(f"  Total centuries: {rows[0][0]}")

con.close()
