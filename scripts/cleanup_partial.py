"""Clean up partial backfill and re-run."""
import duckdb

DB_PATH = "data/db/cricket.duckdb"
con = duckdb.connect(DB_PATH)

# Find the max TEST Match No from original Kaggle (2537 rows = original count)
# The original max should be around 2537ish
# Let's find matches that were inserted by backfill (match IDs that are Cricsheet format)
print("=== Cleaning partial backfill ===")

# Count before
for t in ["kaggle_matches", "kaggle_batting", "kaggle_bowling", "kaggle_fow"]:
    c = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {c} rows")

# Remove backfilled kaggle_matches (those with TEST Match No > 2537)
# Original Kaggle had 2537 rows
deleted = con.execute("""
    DELETE FROM kaggle_matches WHERE "TEST Match No" > 2537
""").fetchone()
print(f"\n  Removed from kaggle_matches: {deleted}")

# The batting/bowling/fow inserts failed, so no cleanup needed there
# But let's verify
print("\n=== After cleanup ===")
for t in ["kaggle_matches", "kaggle_batting", "kaggle_bowling", "kaggle_fow"]:
    c = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {c} rows")

# Also drop player_map so it gets rebuilt
con.execute("DROP TABLE IF EXISTS player_map")
print("  Dropped player_map")

con.close()
print("\nDone. Now re-run backfill_tests.py")
