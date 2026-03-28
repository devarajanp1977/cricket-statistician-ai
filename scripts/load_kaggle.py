"""Load Kaggle Test matches CSV data into DuckDB.

Tables created:
  - kaggle_matches        (test_Matches_Data.csv)
  - kaggle_batting        (test_Batting_Card.csv)
  - kaggle_bowling        (test_Bowling_Card.csv)
  - kaggle_fow            (test_Fow_Card.csv)
  - kaggle_partnerships   (test_Partnership_Card.csv)
  - kaggle_players        (players_info.csv)
"""

import os
import glob
import duckdb

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KAGGLE_DIR = os.path.join(BASE_DIR, "data", "raw", "kaggle")
DB_PATH = os.path.join(BASE_DIR, "data", "db", "cricket.duckdb")

# Map CSV filename patterns to table names
CSV_TABLE_MAP = {
    "test_Matches_Data": "kaggle_matches",
    "test_Batting_Card": "kaggle_batting",
    "test_Bowling_Card": "kaggle_bowling",
    "test_Fow_Card": "kaggle_fow",
    "test_Partnership_Card": "kaggle_partnerships",
    "players_info": "kaggle_players",
}


def load_kaggle(force: bool = False):
    """Load all Kaggle CSV files into DuckDB tables."""
    if not os.path.isdir(KAGGLE_DIR):
        print(f"[kaggle-loader] CSV dir not found: {KAGGLE_DIR}")
        print("[kaggle-loader] Run: python scripts/download_data.py --source kaggle")
        return

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = duckdb.connect(DB_PATH)

    csv_files = glob.glob(os.path.join(KAGGLE_DIR, "*.csv"))
    if not csv_files:
        print("[kaggle-loader] No CSV files found.")
        con.close()
        return

    for csv_path in csv_files:
        basename = os.path.splitext(os.path.basename(csv_path))[0]
        table_name = CSV_TABLE_MAP.get(basename)
        if not table_name:
            print(f"[kaggle-loader] Skipping unrecognized file: {basename}.csv")
            continue

        existing = 0
        try:
            existing = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        except duckdb.CatalogException:
            pass  # table doesn't exist yet

        if existing > 0 and not force:
            print(f"[kaggle-loader] {table_name}: {existing:,} rows already loaded. Use --force to reload.")
            continue

        csv_safe = csv_path.replace(os.sep, "/")
        if force:
            con.execute(f"DROP TABLE IF EXISTS {table_name}")

        print(f"[kaggle-loader] Loading {basename}.csv -> {table_name} ...")
        con.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} AS
            SELECT * FROM read_csv_auto('{csv_safe}', header=true, ignore_errors=true)
        """)
        count = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        print(f"[kaggle-loader] {table_name}: {count:,} rows loaded.")

    # Summary
    print("\n[kaggle-loader] Summary:")
    for table_name in CSV_TABLE_MAP.values():
        try:
            count = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            cols = con.execute(f"SELECT COUNT(*) FROM information_schema.columns WHERE table_name = '{table_name}'").fetchone()[0]
            print(f"  {table_name}: {count:,} rows, {cols} columns")
        except duckdb.CatalogException:
            pass

    con.close()
    print(f"[kaggle-loader] Database: {DB_PATH}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Load Kaggle Test data into DuckDB")
    parser.add_argument("--force", action="store_true", help="Drop and reload all tables")
    args = parser.parse_args()
    load_kaggle(force=args.force)
