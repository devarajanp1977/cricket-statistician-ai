"""Download cricket data from Cricsheet and Kaggle."""

import os
import zipfile
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(BASE_DIR, "data", "raw")
CRICSHEET_DIR = os.path.join(RAW_DIR, "cricsheet")
KAGGLE_DIR = os.path.join(RAW_DIR, "kaggle")

CRICSHEET_DOWNLOADS = {
    "all_json": "https://cricsheet.org/downloads/all_json.zip",
}

CRICSHEET_REGISTER = {
    "people": "https://cricsheet.org/register/people.csv",
    "names": "https://cricsheet.org/register/names.csv",
}

KAGGLE_DATASET = "bhuvaneshprasad/test-matches-dataset-1877-2023"


def _ensure_dirs():
    os.makedirs(CRICSHEET_DIR, exist_ok=True)
    os.makedirs(KAGGLE_DIR, exist_ok=True)


def download_cricsheet(force: bool = False):
    """Download and extract Cricsheet ball-by-ball JSON data."""
    _ensure_dirs()
    for name, url in CRICSHEET_DOWNLOADS.items():
        zip_path = os.path.join(CRICSHEET_DIR, f"{name}.zip")
        extract_dir = os.path.join(CRICSHEET_DIR, name)

        if os.path.exists(extract_dir) and not force:
            count = len([f for f in os.listdir(extract_dir) if f.endswith(".json")])
            print(f"[cricsheet] {name}: {count} JSON files already present. Use --force to re-download.")
            continue

        print(f"[cricsheet] Downloading {name} from {url} ...")
        resp = requests.get(url, stream=True, timeout=300)
        resp.raise_for_status()
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        size_mb = os.path.getsize(zip_path) / (1024 * 1024)
        print(f"[cricsheet] Downloaded {size_mb:.1f} MB -> {zip_path}")

        print(f"[cricsheet] Extracting to {extract_dir} ...")
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        count = len([f for f in os.listdir(extract_dir) if f.endswith(".json")])
        print(f"[cricsheet] Extracted {count} JSON match files.")

        os.remove(zip_path)

    # Download register CSVs
    for name, url in CRICSHEET_REGISTER.items():
        csv_path = os.path.join(CRICSHEET_DIR, f"{name}.csv")
        if os.path.exists(csv_path) and not force:
            print(f"[cricsheet] {name}.csv already present. Use --force to re-download.")
            continue
        print(f"[cricsheet] Downloading {name}.csv ...")
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        with open(csv_path, "wb") as f:
            f.write(resp.content)
        print(f"[cricsheet] Saved {name}.csv ({len(resp.content) / 1024:.0f} KB)")


def download_kaggle():
    """Download Kaggle Test matches dataset via kagglehub."""
    import shutil
    import kagglehub

    _ensure_dirs()
    if any(f.endswith(".csv") for f in os.listdir(KAGGLE_DIR)):
        print("[kaggle] CSV files already present. Delete data/raw/kaggle/ to re-download.")
        return

    print("[kaggle] Downloading dataset via kagglehub ...")
    path = kagglehub.dataset_download(KAGGLE_DATASET)
    print(f"[kaggle] Downloaded to: {path}")

    csv_files = [f for f in os.listdir(path) if f.endswith(".csv")]
    for f in csv_files:
        shutil.copy2(os.path.join(path, f), os.path.join(KAGGLE_DIR, f))
    print(f"[kaggle] Copied {len(csv_files)} CSV files to {KAGGLE_DIR}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Download cricket data")
    parser.add_argument("--force", action="store_true", help="Re-download even if data exists")
    parser.add_argument("--source", choices=["cricsheet", "kaggle", "all"], default="all")
    args = parser.parse_args()

    if args.source in ("cricsheet", "all"):
        download_cricsheet(force=args.force)
    if args.source in ("kaggle", "all"):
        download_kaggle()
