# encrypt_datasets.py
import os
from dotenv import load_dotenv

load_dotenv()
from cryptography.fernet import Fernet

# Read key from environment
DATA_FERNET_KEY = os.environ.get("DATA_FERNET_KEY")
if not DATA_FERNET_KEY:
    raise RuntimeError("DATA_FERNET_KEY env var is required to encrypt datasets")

fernet = Fernet(DATA_FERNET_KEY.encode("utf-8"))

DATA_DIR = "."          # folder where your current CSV/JSON files are
OUT_DIR = "encrypted"   # folder where encrypted copies will be saved

os.makedirs(OUT_DIR, exist_ok=True)


def encrypt_file(src_path: str, dst_path: str):
    with open(src_path, "rb") as f:
        raw = f.read()
    enc = fernet.encrypt(raw)
    with open(dst_path, "wb") as f:
        f.write(enc)
    print(f"Encrypted {src_path} -> {dst_path}")


# 1) Encrypt CSV files
csv_files = [
    "station_loc.csv",
    "station_day.csv",
    "station_env_factors.csv",
]

for name in csv_files:
    src = os.path.join(DATA_DIR, name)
    if not os.path.exists(src):
        print("⚠ Missing CSV:", src)
        continue
    dst = os.path.join(OUT_DIR, name + ".enc")
    encrypt_file(src, dst)

# 2) Encrypt JSON mapping
json_name = "station_id.json"
src_json = os.path.join(DATA_DIR, json_name)
if os.path.exists(src_json):
    dst_json = os.path.join(OUT_DIR, json_name + ".enc")
    encrypt_file(src_json, dst_json)
else:
    print("⚠ Missing JSON:", src_json)
