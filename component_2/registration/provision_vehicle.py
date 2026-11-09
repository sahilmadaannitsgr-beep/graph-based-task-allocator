"""Create a unique vehicle bearer credential; prints the token only once."""
import argparse, hashlib, json, secrets
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("vehicle_id")
args = p.parse_args()
path = Path(__file__).resolve().parent / "data" / "vehicle_tokens.json"
path.parent.mkdir(parents=True, exist_ok=True)
tokens = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
token = secrets.token_urlsafe(32)
tokens[hashlib.sha256(token.encode()).hexdigest()] = args.vehicle_id
path.write_text(json.dumps(tokens, indent=2) + "\n", encoding="utf-8")
print(f"Vehicle: {args.vehicle_id}")
print("Copy this token into that vehicle's device_config.json. It cannot be recovered later:")
print(token)
