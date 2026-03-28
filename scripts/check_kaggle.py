"""Check Kaggle tables in DuckDB."""
import duckdb

con = duckdb.connect("data/db/cricket.duckdb", read_only=True)

# List all tables
print("=== All Tables ===")
tables = con.execute("SHOW TABLES").fetchall()
for t in tables:
    print(f"  {t[0]}")

# Check if kaggle tables exist
print("\n=== Kaggle Tables ===")
for t in [n[0] for n in tables]:
    if 'kaggle' in t.lower():
        count = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {count} rows")
        cols = con.execute(f"DESCRIBE {t}").fetchall()
        print(f"    Columns: {[c[0] for c in cols]}")

con.close()
