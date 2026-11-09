"""Vehicle-side inventory registration agent."""
from __future__ import annotations
import argparse, json, logging, platform, socket, time
from datetime import datetime, timezone
from pathlib import Path
import requests

def discover_devices(config: dict) -> list[dict]:
    """Configured inventory adapter; replace with real hardware adapters.

    No universal detector exists for all automotive IoT hardware. Add
    platform/vendor-specific checks here and only report devices that are
    confirmed installed and available. The default reads the vehicle's
    explicit inventory config so it is testable on any laptop.
    """
    devices = []
    for item in config.get("devices", []):
        if item.get("enabled", False):
            devices.append({
                "type": item["type"],
                "source": item.get("source", "configured"),
                "hardware_id": item.get("hardware_id"),
                "metadata": item.get("metadata", {})
            })
    return devices

def register(config: dict, session: requests.Session):
    payload = {
        "vehicle_id": config["vehicle_id"],
        "agent_version": "1.0.0",
        "reported_at": datetime.now(timezone.utc).isoformat(),
        "client": {"hostname": socket.gethostname(),
                   "platform": platform.platform(),
                   "python_version": platform.python_version()},
        "devices": discover_devices(config)
    }
    response = session.post(
        config["server_url"].rstrip("/") + "/api/v1/vehicles/register",
        json=payload,
        headers={"Authorization": "Bearer " + config["api_token"]},
        timeout=15
    )
    response.raise_for_status()
    return response.json()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(Path(__file__).with_name("device_config.json")))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    required = ("vehicle_id", "server_url", "api_token")
    if any(not config.get(k) for k in required):
        raise SystemExit("vehicle_id, server_url and api_token must be configured")
    interval = max(30, int(config.get("heartbeat_interval_s", 300)))
    with requests.Session() as session:
        while True:
            try:
                result = register(config, session)
                logging.info("Registered %s: %s devices, mask=%s (0b%s)",
                    result["vehicle_id"], result["device_count"],
                    result["capability_mask"], result["capability_mask_binary"])
            except requests.RequestException:
                logging.exception("Registration failed")
                if args.once:
                    raise
            if args.once:
                break
            time.sleep(interval)

if __name__ == "__main__":
    main()
