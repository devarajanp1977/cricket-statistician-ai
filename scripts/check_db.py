"""Quick check of DuckDB status."""
import os
import sys

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "db", "cricket.duckdb")

# File size check
if os.path.exists(DB_PATH):
    size_mb = os.path.getsize(DB_PATH) / 1e6
    print(f"DB file: {size_mb:.1f} MB")
else:
    print("DB file does not exist")
    sys.exit(1)

wal = DB_PATH + ".wal"
if os.path.exists(wal):
    wal_mb = os.path.getsize(wal) / 1e6
    print(f"WAL file: {wal_mb:.1f} MB")

# Try to open (will fail if locked)
try:
    import duckdb
    con = duckdb.connect(DB_PATH, read_only=True)
    for table in ("matches", "innings", "deliveries", "wickets", "players"):
        try:
            count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {count:,} rows")
        except Exception:
            print(f"  {table}: table not found")
    con.close()
except Exception as e:
    print(f"Cannot open DB (likely still loading): {e}")
