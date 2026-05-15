# Agent D Bridge — Integration Spec

> Drop this file into the AgentD repo (suggested path: `AgentD.extension/AgentD.tab/Data Agent.panel/Bridge.pushbutton/README.md`, or `docs/bridge_spec.md` at the repo root). It is self-contained — anyone (or any AI agent) reading only this file should be able to implement Agent D's side of the integration without reading Agent A's source.

---

## 1. Why This Exists

Agent A (a separate pyRevit extension running in CPython 3.12) hosts a Gemini-powered chat window inside Revit. When a user types something like *"Audit the Door Schedule for missing Fire Rating values"*, Agent A's intent classifier needs to delegate that request to Agent D and stream the result back into its own chat window.

Agent A does not know anything Revit-specific about Agent D. All it knows is: *"there is an HTTP endpoint at `http://127.0.0.1:8101/run`, I POST a JSON payload, I get a JSON (or streamed JSON) response back."*

Your job in this repo: stand up that HTTP endpoint, and make it call into Agent D's existing Check / Fill / Start logic in a **headless** way (no `forms.SelectFromList`, no popups).

---

## 2. Hard Constraints

1. **Do not modify the existing `Check.pushbutton/`, `Fill.pushbutton/`, or `Start.pushbutton/` folders.** They must keep working exactly as today when a user clicks them in the ribbon. Reuse their logic by importing or by copying the core functions into a new module — your call.
2. **IronPython 2.7 only.** You cannot use `requests`, modern `anthropic` SDKs, `fastmcp`, `uvicorn`, `httpx`, `pydantic`, `asyncio`, f-strings, type hints, or `print()` with kwargs. Use `.format()`, `System.Net.WebRequest`, and `System.Net.HttpListener` — same patterns the existing code uses for the Anthropic API.
3. **Revit API thread rule.** All `Document` reads/writes must happen on Revit's main thread. The HTTP listener runs on a background thread, so you must marshal calls back to the main thread via `ExternalEvent` + `IExternalEventHandler`. Same pattern Agent A uses internally.
4. **No second process.** Everything lives inside Agent D's existing pyRevit Python environment, started by a new ribbon button.
5. **Listen on `127.0.0.1` only**, never `0.0.0.0`. This is a local-only integration; do not expose to the LAN.
6. **Port: 8101.** (Agent A uses 8001; future third agent will use 8201. Keep them separated by a hundred so they're easy to remember.)

---

## 3. Deliverables

Create one new pushbutton folder. Do not touch anything else.

```
AgentD.extension/AgentD.tab/Data Agent.panel/Bridge.pushbutton/
├── bundle.yaml              # pyRevit button metadata
├── icon.png                 # any 32x32 icon
├── script.py                # Ribbon entry — starts the HttpListener
├── agentd_headless.py       # 3 headless functions (no UI prompts)
└── README.md                # (optional) copy of this spec
```

### 3.1 `bundle.yaml`

```yaml
title: Start Bridge
tooltip: Start the Agent D HTTP bridge so Agent A's chat can call into this agent.
```

### 3.2 `script.py` — what it must do

1. On ribbon click, check whether port 8101 is already bound. If yes, show a `forms.alert("Bridge already running on 8101")` and exit cleanly (don't crash, don't double-bind).
2. Construct an `IExternalEventHandler` whose `Execute(uiapp)` method drains a thread-safe queue of `(callable, args, result_holder, done_event)` tuples. Each callable receives `doc = uiapp.ActiveUIDocument.Document` and returns a JSON-serializable dict. Mirrors Agent A's `bridge.py` pattern — keep it small (~40 lines).
3. Construct a helper `run_on_main_thread(fn, *args, timeout=600)`:
   - Push the tuple onto the queue
   - Call `external_event.Raise()`
   - Block on `done_event.wait(timeout)`
   - Return the result (or raise on timeout)
4. Start `System.Net.HttpListener` on `http://127.0.0.1:8101/` in a background thread:
   ```python
   from System.Net import HttpListener
   listener = HttpListener()
   listener.Prefixes.Add("http://127.0.0.1:8101/")
   listener.Start()
   ```
5. The background accept loop reads each request, parses the JSON body, dispatches by `action` field to one of the three headless functions via `run_on_main_thread(...)`, and writes the result back as JSON.
6. Surface friendly errors (`{"status":"error","reason":"..."}`) for: invalid JSON, unknown action, no active document, parameter not editable, listener already running, internal exception (include the message string).
7. Log a single line to `pyrevit`'s output: `Bridge listening on http://127.0.0.1:8101` so you can see it started.

The listener should keep running until Revit closes — pyRevit keeps the Python process alive, so the thread persists naturally. Do **not** call `listener.Stop()` from the user side; just let Revit exit clean it up. (If you want to be tidy, register an `__revit__.Application.ApplicationClosing` handler.)

### 3.3 `agentd_headless.py` — the 3 functions

These are the only functions the HTTP layer calls. They take a `doc` (Revit `Document`) and primitive args; they return a JSON-serializable dict. **No `forms.SelectFromList`, no `pyrevit.forms.alert`, no `os.startfile`, no HTML dashboard.** All inputs come from the HTTP request.

```python
def audit_data(doc, schedule_name, parameter_name):
    """
    Scans the named ViewSchedule for elements where `parameter_name` is missing,
    grouped by Category/Family/Type. Returns the same statistics structure that
    Check.pushbutton currently renders into HTML — just as a dict instead of HTML.
    """
    # Reuse the FilteredElementCollector + nested-dict logic from Check.pushbutton/script.py.
    return {
        "status": "success",
        "schedule": schedule_name,
        "parameter": parameter_name,
        "statistics": {
            "total_elements": 0,
            "missing": 0,
            "filled": 0,
            "by_category": { /* Category -> Family -> Type -> {missing, filled} */ }
        }
    }


def fill_data(doc, schedule_name, parameter_name, api_key):
    """
    For each element with a missing value, query Claude with the element's
    Category/Family/Type context, then write the predicted value inside a single
    Revit Transaction (rollback on error). Reuse Fill.pushbutton/script.py's
    core loop; just take api_key as a parameter instead of reading it from
    user_config.ini.

    If api_key is None or "", fall back to user_config.ini exactly as today.
    """
    return {
        "status": "success",
        "target_parameter": parameter_name,
        "statistics": {
            "total_elements": 0,
            "initially_missing": 0,
            "ai_filled_successfully": 0,
            "errors": 0
        }
    }


def start_pipeline(doc, schedule_name, parameter_name, api_key):
    """
    audit_data + fill_data + AI sanity-check pass. Same as Start.pushbutton today
    but returns the structured payload below instead of writing HTML to tempdir.
    """
    return {
        "status": "success",
        "target_parameter": parameter_name,
        "statistics": { /* same shape as fill_data */ },
        "ai_sanity_check_insights": [
            "Task 1: ...",
            "Task 2: ..."
        ]
    }
```

The output shapes above match what's already documented in `integration_AgentD.md` §5 — don't invent new shapes.

---

## 4. Wire Protocol — Exact Contract With Agent A

### 4.1 Request

```http
POST /run HTTP/1.1
Host: 127.0.0.1:8101
Content-Type: application/json

{
  "action": "fill_data",
  "schedule_name": "Door Schedule",
  "parameter_name": "Fire Rating",
  "api_key": "sk-ant-..."          // optional; if absent, read from user_config.ini
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `action` | string | yes | One of `audit_data`, `fill_data`, `start_pipeline`. Anything else → `{"status":"error","reason":"unknown_action"}`. |
| `schedule_name` | string | yes | Exact name of a ViewSchedule in the active doc. If not found → `{"status":"error","reason":"schedule_not_found","schedule":"..."}`. |
| `parameter_name` | string | yes | Exact parameter name. If not present on any element in the schedule → `{"status":"error","reason":"parameter_not_found"}`. |
| `api_key` | string | no | Anthropic API key. Fill/start only. If omitted/empty, use `user_config.ini` `[DataAgent]` section as today. |

### 4.2 Response — v1 (plain JSON, recommended for first cut)

```http
HTTP/1.1 200 OK
Content-Type: application/json

{ "status": "success", ... }
```

One JSON object, written when the operation completes. Agent A shows a spinner during the request and renders the final payload as markdown.

### 4.3 Response — v2 (streaming, optional upgrade later)

If you want progress to stream into Agent A's chat while a long `fill_data` is running, switch the response to `text/event-stream`:

```
HTTP/1.1 200 OK
Content-Type: text/event-stream
Cache-Control: no-cache

data: {"type":"status","text":"Scanning Door Schedule..."}

data: {"type":"status","text":"Found 45 missing values, querying Claude (12/45)..."}

data: {"type":"status","text":"Writing values inside Revit transaction..."}

data: {"type":"result","payload":{"status":"success","statistics":{...}}}

```

Rules for streaming:
- Each message is a single line `data: <json>\n\n` (blank line terminator).
- `{"type":"status","text":"..."}` updates the chat spinner. Prefix `text` with the literal bytes `\x00STATUS\x00` if you want it treated as transient (replace in place) rather than appended.
- Exactly one `{"type":"result","payload":...}` per request, sent last, then close the connection.
- If Agent A's TCP connection closes mid-stream, treat it as a cancel signal: abandon the loop, roll back any open Revit transaction, exit cleanly.

Ship v1 first. Upgrade to v2 only if the UX feels slow.

### 4.4 Error responses

Always return HTTP 200 with a JSON body — never 4xx/5xx. Agent A keys off the `status` field, not the HTTP code, so a `500` would just produce a generic "request failed" in the chat. Status enum:

| `status` | When |
|---|---|
| `success` | Operation completed (even if some sub-elements errored — surface those in `statistics.errors`). |
| `error` | Hard failure. Always include `reason` (machine-readable enum) and `message` (human string). |
| `cancelled` | Connection closed by caller mid-stream. Only relevant for v2. |

Reason enum (extend as needed): `no_active_document`, `schedule_not_found`, `parameter_not_found`, `parameter_read_only`, `api_key_missing`, `claude_api_error`, `transaction_failed`, `unknown_action`, `invalid_json`, `internal_error`.

---

## 5. Threading Model — Critical

The HTTP listener thread is **NOT** the Revit main thread. Reading any `Element`, `Parameter`, `Document` property, or calling `Transaction.Start()` from the listener thread will throw `Autodesk.Revit.Exceptions.InvalidOperationException`.

Pattern (the only one that works):

```
HTTP thread                          Revit main thread (UI)
─────────                            ──────────────────────
listener.GetContext()  ──► request
parse JSON
                                     [ExternalEvent.Raise scheduled]
queue.put((audit_data, args, evt))
ext_event.Raise()
done_event.wait()    ◄─── (blocks)
                                     IExternalEventHandler.Execute(uiapp):
                                       while not queue.empty():
                                         fn, args, result, evt = queue.get()
                                         try:
                                           result["data"] = fn(doc, *args)
                                         except Exception as e:
                                           result["error"] = str(e)
                                         evt.set()
read result, json.dumps,
write to response, close
```

Why not just `__revit__.ActiveUIDocument.Document` from the listener thread? Because reading `Document` is safe-ish, but the moment you touch a `Parameter` or start a `Transaction`, Revit will throw. Just route everything through `ExternalEvent` and stop worrying about which calls are "main-thread-required."

`ExternalEvent.Raise()` is cheap; raise once per request, drain the queue in the handler. Don't raise inside loops.

---

## 6. Reading API Key Without UI Prompts

Today, `Fill.pushbutton/script.py` reads the Anthropic key from `pyrevit.userconfig` and pops a UI prompt if missing. The bridge must NOT pop UI. Order of resolution:

1. If the HTTP request includes `api_key`, use that.
2. Else, read `pyrevit.userconfig.user_config` `[DataAgent]` section, same as today.
3. Else, return `{"status":"error","reason":"api_key_missing","message":"No Anthropic API key supplied via request or user_config.ini"}`. **Do not call `forms.ask_for_string`.** Agent A will surface the error to the user, who can then click the existing Fill button once to set the key, or pass it in the request.

---

## 7. Smoke Test (no Agent A required)

You can validate the bridge before Agent A is ready:

1. Open Revit, open a project with a schedule.
2. Click the new **Start Bridge** ribbon button. Check the pyRevit output panel for `Bridge listening on http://127.0.0.1:8101`.
3. From any other terminal on the same machine, run a tiny test:

   ```powershell
   $body = '{"action":"audit_data","schedule_name":"Door Schedule","parameter_name":"Fire Rating"}'
   Invoke-RestMethod -Uri http://127.0.0.1:8101/run -Method Post -Body $body -ContentType 'application/json'
   ```

4. Expected: a JSON object with `status: success` and statistics. No popups in Revit. Revit remains responsive (you can click around).
5. Run `fill_data` with a real API key. Expected: parameters get filled, the Revit undo stack shows ONE "Agent D — Fill" entry, the response JSON reports counts.
6. Kill the request mid-flight (Ctrl-C the PowerShell call). Open a normal `Fill.pushbutton` click in Revit. Expected: it still works exactly as before — confirms the existing button is unaffected.
7. Try `{"action":"banana"}` → expect `{"status":"error","reason":"unknown_action"}`.
8. Stop and restart the listener button → expect "already running" message, not a second listener on a different port, not a crash.

If all 8 pass, Agent D's side is done.

---

## 8. Things NOT To Do

- Don't call `os.startfile(...)` to launch the HTML dashboard. The dashboard is for the standalone button; the bridge returns JSON only.
- Don't write to `tempfile.gettempdir()` from the bridge. No side files.
- Don't add `pyrevit.forms.alert` or `forms.SelectFromList` in the headless path. Every input comes from the HTTP request body. If something is missing, return a structured error.
- Don't change `Check.pushbutton/script.py`, `Fill.pushbutton/script.py`, or `Start.pushbutton/script.py`. If you need a helper from them, copy the small function into `agentd_headless.py` rather than restructuring the existing scripts.
- Don't bump TLS or Anthropic-call code. Reuse the exact `System.Net.WebRequest` + `ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12` block the existing buttons use.
- Don't try to use Python's `http.server` or `requests`. They behave poorly under IronPython 2.7 inside Revit. Stick to `System.Net.HttpListener`.

---

## 9. Versioning / Future-Proofing

Agent A reads an `external_agents.json` registry on its side that lists the URL and supported actions. If you ever change the URL or add a new action:

- Tell whoever owns Agent A to update one line in `external_agents.json`.
- Don't break the existing `action` names (`audit_data`, `fill_data`, `start_pipeline`) — Agent A's intent classifier has been prompted with those names.
- New actions are additive; add them and document the request/response shape here.

---

## 10. Quick Reference Card

| Thing | Value |
|---|---|
| Listener address | `http://127.0.0.1:8101/run` |
| Method | `POST` |
| Request content-type | `application/json` |
| Response content-type | `application/json` (v1) or `text/event-stream` (v2) |
| Actions | `audit_data`, `fill_data`, `start_pipeline` |
| Threading | `System.Net.HttpListener` background thread → `ExternalEvent.Raise()` → Revit main thread |
| Touch existing buttons? | No |
| Process model | Same pyRevit Python process Agent D already runs in — no sidecar |
| Trigger | New "Start Bridge" ribbon button under `AgentD.tab / Data Agent.panel` |
