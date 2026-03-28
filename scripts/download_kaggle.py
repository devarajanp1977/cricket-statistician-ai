"""Download Kaggle Test matches dataset via kagglehub and copy to data/raw/kaggle/."""
import os
import shutil
import kagglehub

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KAGGLE_DIR = os.path.join(BASE_DIR, "data", "raw", "kaggle")

print("[kaggle] Downloading dataset via kagglehub ...")
path = kagglehub.dataset_download("bhuvaneshprasad/test-matches-dataset-1877-2023")
print(f"[kaggle] Downloaded to: {path}")

# List files
files = [f for f in os.listdir(path) if f.endswith(".csv")]
print(f"[kaggle] Found {len(files)} CSV files: {files}")

# Copy to our data/raw/kaggle/ directory
os.makedirs(KAGGLE_DIR, exist_ok=True)
for f in files:
    src = os.path.join(path, f)
    dst = os.path.join(KAGGLE_DIR, f)
    shutil.copy2(src, dst)
    size_kb = os.path.getsize(dst) / 1024
    print(f"[kaggle] Copied {f} ({size_kb:.0f} KB)")

print(f"\n[kaggle] All CSV files copied to {KAGGLE_DIR}")
