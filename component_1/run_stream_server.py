"""Component 1 -> Component 2 real-time GPS SSE bridge (stdlib only)."""
from __future__ import annotations
import argparse, csv, gzip, json, queue, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from src.vehicle_sim import VehicleSimulation
from src.road_network import build_city
from src.sumo_scenario import load_tapas_scenario
from src.gps_contract import validate_observation

FIELDS = ["device_id", "timestamp", "latitude", "longitude"]

def make_sim(config, net_path=None, routes_path=None):
    cfg = json.loads(Path(config).read_text(encoding="utf-8"))
    if bool(net_path) != bool(routes_path):
        raise ValueError("Pass both --net and --routes, or neither.")
    if net_path:
        net, _ = load_tapas_scenario(net_path, routes_path)
        cfg["region"]["center"] = {"lat": net.geo_center[0], "lon": net.geo_center[1]}
    else:
        net = build_city(cfg["network"])
    return VehicleSimulation(cfg, net=net), cfg

class State:
    def __init__(self, sim, output, duration, batch_s, speed, queue_size):
        self.sim, self.output, self.duration = sim, Path(output), duration
        self.batch_s, self.speed, self.queue_size = batch_s, speed, queue_size
        self.clients, self.lock = set(), threading.Lock()
        self.running, self.done, self.error = True, False, None
        self.batch_id, self.total = 0, 0

    def subscribe(self):
        q = queue.Queue(self.queue_size)
        with self.lock: self.clients.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock: self.clients.discard(q)

    def publish(self, payload):
        msg = json.dumps(payload, separators=(",", ":"))
        with self.lock:
            slow = []
            for q in self.clients:
                try: q.put_nowait(msg)
                except queue.Full: slow.append(q)
            for q in slow: self.clients.discard(q)

    def flush(self, records, writer, f):
        if not records: return
        writer.writerows(records)
        f.flush()
        self.batch_id += 1
        self.total += len(records)
        self.publish({"type":"gps_batch", "batch_id":self.batch_id,
                      "sim_time_s":round(self.sim.t,3), "count":len(records),
                      "observations":records})

    def produce(self):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        opener = gzip.open if self.output.suffix == ".gz" else open
        buf, last_flush = [], time.monotonic()
        try:
            with opener(self.output, "wt", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS)
                writer.writeheader(); f.flush()
                while self.running and (self.duration <= 0 or self.sim.t < self.duration):
                    tick_start = time.monotonic()
                    buf.extend(validate_observation(x) for x in self.sim.step())
                    if buf and time.monotonic() - last_flush >= self.batch_s:
                        self.flush(buf, writer, f); buf = []; last_flush = time.monotonic()
                    wait = tick_start + self.sim.dt / self.speed - time.monotonic()
                    if wait > 0: time.sleep(wait)
                self.flush(buf, writer, f)
            self.publish({"type":"complete","batch_id":self.batch_id,"total_records":self.total})
        except Exception as exc:
            self.error = str(exc)
            self.publish({"type":"error","message":self.error,"batch_id":self.batch_id})
        finally: self.done = True

def handler_for(state):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/health":
                body = json.dumps({"status":"complete" if state.done else "running",
                    "gps_schema":FIELDS,"total_archived_records":state.total,
                    "last_batch_id":state.batch_id,"error":state.error}).encode()
                self.send_response(200); self.send_header("Content-Type","application/json")
                self.send_header("Content-Length",str(len(body))); self.end_headers()
                self.wfile.write(body); return
            if path != "/gps/stream": self.send_error(404); return
            q = state.subscribe()
            self.send_response(200); self.send_header("Content-Type","text/event-stream; charset=utf-8")
            self.send_header("Cache-Control","no-cache"); self.send_header("Connection","keep-alive")
            self.send_header("X-Accel-Buffering","no"); self.end_headers()
            try:
                self.wfile.write(b": connected\n\n"); self.wfile.flush()
                while not (state.done and q.empty()):
                    try:
                        msg = q.get(timeout=10)
                        self.wfile.write(("data: "+msg+"\n\n").encode()); self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": heartbeat\n\n"); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError): pass
            finally: state.unsubscribe(q)
        def log_message(self, fmt, *args): print("[gps-stream] "+fmt % args)
    return Handler

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sim_config.json")
    ap.add_argument("--net"); ap.add_argument("--routes")
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--duration", type=float, default=3600, help="0 runs until Ctrl+C")
    ap.add_argument("--output", default="data/live_gps_stream.csv.gz")
    ap.add_argument("--batch-interval", type=float); ap.add_argument("--sim-speed", type=float)
    a = ap.parse_args()
    sim, cfg = make_sim(a.config, a.net, a.routes)
    sc = cfg.get("stream", {})
    batch = a.batch_interval if a.batch_interval is not None else sc.get("batch_interval_s", 1.0)
    speed = a.sim_speed if a.sim_speed is not None else sc.get("simulation_speed", 1.0)
    qsize = int(sc.get("queue_batches", 120))
    if batch <= 0 or speed <= 0 or qsize < 1 or a.duration < 0: ap.error("timing, queue and duration values must be positive (duration may be 0)")
    state = State(sim, a.output, a.duration, batch, speed, qsize)
    httpd = ThreadingHTTPServer((a.host, a.port), handler_for(state))
    worker = threading.Thread(target=state.produce, daemon=True); worker.start()
    print(f"GPS stream: http://{a.host}:{a.port}/gps/stream")
    print(f"Health: http://{a.host}:{a.port}/health")
    print(f"Archive: {a.output}")
    print(f"sim.dt_s={sim.dt}s | gps.reporting_interval_s={cfg['gps'].get('reporting_interval_s',1)}s | batch_interval_s={batch}s | simulation_speed={speed}x")
    try: httpd.serve_forever()
    except KeyboardInterrupt: print("\nStopping...")
    finally:
        state.running = False; worker.join(timeout=10)
        httpd.shutdown(); httpd.server_close()

if __name__ == "__main__": main()
