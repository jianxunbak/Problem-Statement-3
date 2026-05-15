# Agent A — How to Discover Agent D's Port

> Drop this file into Agent A's repo (or share it with whoever owns Agent A). Self-contained — anyone reading only this file can update Agent A's side to talk to Agent D's bridge without reading Agent D's source.

---

## 1. Why This Changed

Agent D's bridge used to listen on a hard-coded port: `http://127.0.0.1:8101`. On some Windows machines, port 8101 is already claimed by another local service (or by the OS itself for HTTP.SYS reservations), so Agent D could never bind to it.

To work around that, Agent D now does the following at startup:

1. Tries to bind to **8101** first (preferred — keep this as the default in Agent A's config).
2. If 8101 is busy, walks up the range **8101 → 8110** and uses the first free port.
3. Writes the chosen port to a small discovery file: **`%TEMP%\agentd_bridge.port`**.

Agent A needs to read that file before each request so it always hits the right URL. If the file is missing, Agent D's bridge isn't running — surface the existing *"couldn't reach Data Agent"* error.

---

## 2. The Discovery File — Exact Contract

| Field | Value |
|---|---|
| Path | `%TEMP%\agentd_bridge.port` (on Windows, expand `%TEMP%` via `os.environ["TEMP"]` or `tempfile.gettempdir()`) |
| Encoding | Plain UTF-8, no BOM |
| Content | A single integer as ASCII digits. No trailing newline guaranteed. Example: `8104` |
| Lifecycle | Written once when the bridge starts. **NOT** deleted when Revit closes (file may go stale; see §4). |
| Writer | Agent D's `Bridge.pushbutton/script.py` |
| Reader | Agent A (you) |

There is no JSON, no extra metadata — just the port number as text. If you ever need richer metadata (version, model, hostname), the right move is to bump this to a JSON file at the same path with a `.json` extension; do not extend the plain-text format.

---

## 3. Reference Implementation (Python 3, what Agent A should do)

```python
import os
import tempfile
import http.client
import json

DEFAULT_PORT = 8101
PORT_FILE = os.path.join(tempfile.gettempdir(), "agentd_bridge.port")


def _read_agentd_port():
    """Return the port Agent D's bridge is listening on, or None if unknown."""
    try:
        with open(PORT_FILE, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        port = int(raw)
        if 1 <= port <= 65535:
            return port
    except (OSError, ValueError):
        pass
    return None


def call_agent_d(action, schedule_name, parameter_name, api_key=None):
    port = _read_agentd_port() or DEFAULT_PORT
    body = {
        "action": action,
        "schedule_name": schedule_name,
        "parameter_name": parameter_name,
    }
    if api_key:
        body["api_key"] = api_key

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=600)
    try:
        conn.request("POST", "/run",
                     body=json.dumps(body),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        return json.loads(resp.read().decode("utf-8"))
    finally:
        conn.close()
```

Notes:

- **Re-read on every request.** Don't cache the port across calls — the user may restart Revit and land on a different port within a single Agent A session.
- **Always have a default fallback.** If the file is missing (bridge has never run since machine reboot), try 8101 anyway. The request will fail fast if no listener is there, and that error message is more useful than "discovery file missing."
- **Don't watch the file.** A `FileSystemWatcher` is overkill — just stat-and-read at request time.

---

## 4. Failure Modes & How To Handle Them

### 4.1 File present, but the listed port is dead

This happens when Revit was closed cleanly and reopened, OR when Agent D's listener crashed. The file is stale.

**Symptom:** the TCP connect or HTTP request fails (`ConnectionRefusedError`, `socket.timeout`, etc.).

**Handling:** surface this as the existing *"couldn't reach Data Agent — is the AgentD extension loaded?"* error. Don't auto-retry on other ports — that would hit unrelated services.

### 4.2 File missing entirely

Means Agent D has never started its bridge on this machine (or someone wiped `%TEMP%`). Try the default port 8101 once, fail fast on that, then surface the same error.

### 4.3 File contains garbage

Treat as if missing (use default port + fail). The bridge writes the file atomically enough for our needs; corruption almost always means a different process wrote there. Don't try to recover — let the user know.

### 4.4 Wrong port written by an attacker / local malware

Out of scope. This is a localhost-only integration; if a hostile process can write to your `%TEMP%`, you already have bigger problems.

---

## 5. Update `external_agents.json`

Wherever Agent A's `external_agents.json` currently pins Agent D to a port, change it from a fixed value to one of:

- A default port (8101) that gets *overridden* by the discovery file at call time, OR
- A flag like `"port_discovery": "tempfile:agentd_bridge.port"` so Agent A's config explicitly says "look this up."

Example (option A — recommended, minimal change):

```json
{
  "agents": [
    {
      "name": "agent_d",
      "base_url": "http://127.0.0.1:8101",
      "port_discovery_file": "agentd_bridge.port",
      "actions": ["audit_data", "fill_data", "start_pipeline"]
    }
  ]
}
```

When `port_discovery_file` is present, the client reads `%TEMP%\<that filename>` to override the port in `base_url`. Falls back to the hard-coded port if the file is missing.

---

## 6. Testing

### 6.1 Happy path

1. Open Revit. Wait for AgentD's autostart to log `Bridge listening on http://127.0.0.1:8101`.
2. Confirm `%TEMP%\agentd_bridge.port` exists and contains `8101`:
   ```powershell
   Get-Content $env:TEMP\agentd_bridge.port
   ```
3. From Agent A, send any action. Expect a normal response.

### 6.2 Port fallback

1. Before opening Revit, occupy 8101 (PowerShell):
   ```powershell
   $a = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 8101)
   $a.Start()
   ```
2. Open Revit. Expect `Bridge listening on http://127.0.0.1:8102 (preferred port 8101 was busy)`.
3. `Get-Content $env:TEMP\agentd_bridge.port` → `8102`.
4. Agent A makes a request — should reach the bridge on 8102 transparently.
5. Stop the PowerShell occupier: `$a.Stop()`.

### 6.3 Stale file

1. Close Revit.
2. Without reopening, ask Agent A to call Agent D.
3. Expect a clean "couldn't reach Data Agent" error — not a hang, not a stack trace.
4. Reopen Revit. Retry from Agent A. Expect success.

### 6.4 Full saturation

1. Occupy 8101 through 8110 (run ten `TcpListener` instances in PowerShell).
2. Open Revit.
3. Expect `[AgentD startup] bridge auto-start returned False` and *no* port file. Agent A surfaces "couldn't reach Data Agent." AgentD ribbon still loads — Check/Fill/Start still clickable.

---

## 7. Things NOT To Do

- Don't read the port file once at Agent A's startup and cache it forever. The user may restart Revit between requests and end up on a different port.
- Don't watch the file with a filesystem watcher just to update an in-memory variable. Stat-and-read on each request is cheap and avoids stale state.
- Don't try ports 8102+ from Agent A's side if 8101 fails to connect. Only trust the port file. Probing arbitrary ports could hit unrelated local services.
- Don't write the port file from Agent A. Only Agent D writes it; Agent A reads only.
- Don't delete the port file from Agent A. Agent D owns its lifecycle.

---

## 8. Quick Reference

| Thing | Value |
|---|---|
| Discovery file | `%TEMP%\agentd_bridge.port` |
| File contents | Plain ASCII integer (port number) |
| Preferred port | `8101` |
| Fallback range | `8101` through `8110` inclusive |
| Endpoint path | `POST /run` |
| Request/response | JSON, same as `integration_AgentD_bridge_spec.md` §4 |
| Reader cadence | Re-read on every request, no caching |
| File present, port dead | Treat as "bridge offline," show normal unreachable error |
| File missing | Try 8101 once, then show unreachable error |
