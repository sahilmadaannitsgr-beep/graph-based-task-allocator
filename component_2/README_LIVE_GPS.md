# Live GPS receiver quick start

This guide is retained as a short reference for the Component 1 → Component 2
GPS stream. For the complete setup—including the vehicle-side IoT registration
agent, capability registry, task registration API and dynamic integration—
follow the top-level `README.md`.

1. Start Component 1's existing SSE server.
2. Start Component 2's receiver from this directory:

   ```bash
   python stream_receiver.py --config receiver_config.json
   ```

3. The receiver validates the exact GPS observation schema:
   `device_id`, `timestamp`, `latitude`, `longitude`.
4. It archives the raw stream to `data/received/x/received_gps.csv`.
5. It sends observations to the sliding-window processor, which writes
   `data/processed/vehicle_states.jsonl`.

Default SSE URL: `http://127.0.0.1:8765/gps/stream`.
