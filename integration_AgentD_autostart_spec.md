# Agent D — Auto-Start the Bridge at Revit Launch

> Drop this file into the AgentD repo (suggested: `docs/autostart_spec.md` or alongside the existing bridge spec). Self-contained — anyone reading only this can implement the change without referencing Agent A.

---

## 1. Why

Right now, the bridge listener at `http://127.0.0.1:8101/` only starts when the user clicks the **Start Bridge** ribbon button. If the user forgets, Agent A's chat shows *"I couldn't reach Data Agent"* until they remember.

Goal: the bridge starts **automatically** when Revit loads the AgentD extension. No clicks. The existing **Start Bridge** ribbon button stays in place as a manual re-start (in case the listener ever crashes mid-session).

## 2. The Mechanism — pyRevit's `startup.py`

pyRevit looks for a file named **`startup.py`** at the extension root (next to `extension.json`/`bundle.yaml`, NOT inside any `.tab` or `.panel` folder). If it exists, pyRevit runs it once, in IronPython, when the extension is loaded — which happens automatically when Revit boots.

**Hard rules for `startup.py`:**

1. It must live at the extension root: `AgentD.extension/startup.py`. Anywhere else and pyRevit ignores it.
2. **Any unhandled exception in `startup.py` will block the entire extension from loading.** The 3 existing buttons (Check / Fill / Start) will disappear from the ribbon. Wrap everything in a top-level `try/except` and log to pyRevit's output — never raise.
3. It runs at Revit startup, when the document is **not yet loaded**. Do not touch `__revit__.ActiveUIDocument.Document` at startup time — there is none. The listener itself doesn't need a document; it only needs the document at request time (when Agent A POSTs in), which is handled by your existing `ExternalEvent` pattern.
4. `__revit__` (the `UIApplication`) IS available at startup. You can pass it into your bridge bootstrap so the `ExternalEvent` machinery has a `UIApplication` reference.
5. It must not block. Whatever you do, return quickly so Revit's startup isn't delayed. The HTTP listener already runs on a background thread — that's fine; just don't `Thread.Join()` on it.

## 3. Required Refactor of `Bridge.pushbutton/script.py`

Right now, your `Bridge.pushbutton/script.py` is a monolithic ribbon entry point. It needs to be reshaped so the *bridge boot logic* can be called from either:

- A user clicking the ribbon button (existing behavior, kept for manual restart), OR
- `startup.py` at Revit launch (new automatic path)

### 3.1 Extract the boot logic into a function

In `Bridge.pushbutton/script.py`, refactor so the listener-start code lives inside a function rather than at module top level:

```python
# Bridge.pushbutton/script.py

# Make this module importable from startup.py
import sys, os
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from agentd_headless import audit_data, fill_data, start_pipeline

_LISTENER = None  # module-global so we don't double-bind on repeated calls

def start_bridge(uiapp):
    """
    Idempotent: safe to call multiple times. If already listening, returns immediately.
    Returns True on success, False on failure (with reason logged).

    uiapp: the Revit UIApplication. From a button click, this is `__revit__`.
           From startup.py, pass `__revit__` likewise.
    """
    global _LISTENER
    if _LISTENER is not None and _LISTENER.IsListening:
        # Already running — nothing to do
        return True

    try:
        # 1. Check port 8101 isn't already taken by something else
        # 2. Wire up the IExternalEventHandler + queue
        # 3. Start System.Net.HttpListener on http://127.0.0.1:8101/
        # 4. Spawn background thread that calls listener.GetContext() in a loop
        # 5. Each request: parse JSON, queue closure, raise ExternalEvent, return JSON
        # (all of this is exactly what your existing pushbutton code already does —
        #  just lifted into this function)
        ...
        _LISTENER = the_listener
        print("Bridge listening on http://127.0.0.1:8101")
        return True
    except Exception as e:
        print("Bridge failed to start: {}".format(e))
        _LISTENER = None
        return False


# When the user clicks the ribbon button, run start_bridge with the live uiapp
if __name__ == "__main__" or True:  # pyRevit pushbutton entrypoint
    start_bridge(__revit__)
```

The key change: **`start_bridge(uiapp)` is a callable function, idempotent, returns bool**. The ribbon button still calls it. `startup.py` will also call it.

### 3.2 Idempotency is non-negotiable

Both `startup.py` AND a user clicking **Start Bridge** will call `start_bridge()`. If the user opens Revit (startup runs → listener up) and then clicks the button manually, the second call must:

- Detect that `_LISTENER` is already listening
- Return `True` silently (or log "already running")
- **NOT** try to bind to 8101 again — that would throw `HttpListenerException: Address already in use`, and your existing button code would show the user an alarming error.

The `_LISTENER` module-global flag is the simplest way. Alternative: try-bind, catch the address-in-use exception specifically, treat it as success.

## 4. Create `startup.py`

Drop this file at `AgentD.extension/startup.py`:

```python
# -*- coding: utf-8 -*-
"""
AgentD extension startup hook.

Runs once when pyRevit loads the AgentD extension at Revit launch. Auto-starts
the HTTP bridge listener so Agent A can call us without the user clicking the
Start Bridge ribbon button first.

CRITICAL: any uncaught exception here will block the AgentD extension from
loading, hiding all our ribbon buttons. Every code path must be wrapped.
"""
import os
import sys
import traceback


def _log(msg):
    # pyRevit's print() routes to the output panel
    try:
        print("[AgentD startup] {}".format(msg))
    except Exception:
        pass


def _autostart_bridge():
    """Locate Bridge.pushbutton/script.py, import its module, call start_bridge()."""
    try:
        # Resolve <extension_root>/AgentD.tab/Data Agent.panel/Bridge.pushbutton/
        ext_root = os.path.dirname(os.path.abspath(__file__))
        bridge_dir = os.path.join(
            ext_root,
            "AgentD.tab",
            "Data Agent.panel",
            "Bridge.pushbutton",
        )

        if not os.path.isdir(bridge_dir):
            _log("Bridge.pushbutton folder not found at {} — skipping autostart".format(bridge_dir))
            return

        # Make the pushbutton folder importable
        if bridge_dir not in sys.path:
            sys.path.insert(0, bridge_dir)

        # script.py is a pushbutton entrypoint — when imported (not executed via
        # pyRevit's button click path) it should NOT auto-run start_bridge.
        # Refactor script.py so the start_bridge() function is at module level
        # and the auto-run line is guarded.
        # Easiest: import the function directly. See section 3.1 above.
        import script as _bridge_script  # imports Bridge.pushbutton/script.py

        # __revit__ is injected by pyRevit at startup time
        ok = _bridge_script.start_bridge(__revit__)  # noqa: F821 — __revit__ is global in pyRevit
        if ok:
            _log("bridge auto-started on http://127.0.0.1:8101")
        else:
            _log("bridge auto-start returned False — user can click Start Bridge manually")

    except Exception as e:
        # NEVER let this propagate — would block the whole extension from loading
        _log("autostart failed (extension still loaded, click Start Bridge manually): {}".format(e))
        _log(traceback.format_exc())


# Wrap the top level too, belt-and-braces
try:
    _autostart_bridge()
except Exception:
    pass
```

### 4.1 Naming collision warning

`Bridge.pushbutton/script.py` will be imported as the module name `script`. If pyRevit or any other extension is also importing `script` from a different location, there could be a conflict. If you see strange behavior, rename to something less generic and update the `import` line:

```python
# In Bridge.pushbutton/, rename script.py -> bridge_main.py
# Then in startup.py:
import bridge_main as _bridge_script
```

pyRevit pushbutton entries by convention use `script.py`, so keep that name unless there's a conflict.

## 5. Handling the `if __name__ == "__main__"` Pattern Inside a Pushbutton

pyRevit doesn't actually run pushbutton scripts via `if __name__ == "__main__"` — it executes the file directly. So a naked top-level `start_bridge(__revit__)` will run BOTH when the button is clicked AND when `startup.py` imports the module. Two solutions:

**Option A (simplest): rely on idempotency.** If `start_bridge()` is properly idempotent (section 3.2), the second call is a no-op and you can keep the naked top-level call. This is what I'd recommend.

**Option B (explicit guard):** Use a pyRevit-specific check. pyRevit injects `__file__` and `__forceddebugmode__` etc., but the cleanest discriminator is to gate on whether you're being imported vs run:

```python
# Bridge.pushbutton/script.py (bottom of file)

def _is_pushbutton_click():
    # When pyRevit executes a pushbutton, the module's __name__ is "__main__".
    # When imported from startup.py, __name__ is "script".
    return __name__ == "__main__"

if _is_pushbutton_click():
    start_bridge(__revit__)
```

Option A is less code; Option B is more explicit. Either works.

## 6. Testing

After applying the changes, here's the verification ladder:

### 6.1 Cold-start test (the main goal)

1. Close Revit completely.
2. Open Revit. Wait for it to finish loading (no models open yet).
3. Open pyRevit's output panel (the console where `print()` goes). You should see:
   ```
   [AgentD startup] bridge auto-started on http://127.0.0.1:8101
   ```
4. From PowerShell:
   ```powershell
   Get-NetTCPConnection -State Listen -LocalPort 8101 -ErrorAction SilentlyContinue
   ```
   You should see a row with `LocalPort: 8101`. No need to even open a Revit document.
5. Click Agent A's **Start Server** button (or whatever launches its chat). Type *"audit the door schedule for fire rating"*. It should reach the bridge without the *"couldn't reach Data Agent"* error.

### 6.2 Manual restart still works

1. With the bridge already running (from auto-start), click the **Start Bridge** ribbon button.
2. Expected: pyRevit output prints something like *"Bridge already running"* (or no error). Port 8101 still has exactly ONE listener.
3. Expected: AgentD extension still loaded; Check/Fill/Start buttons all still present.

### 6.3 Failure-mode test (critical — proves the safety net)

This proves a bad startup.py won't brick the extension.

1. Temporarily break `startup.py` — e.g. add `raise RuntimeError("test")` at the top.
2. Restart Revit.
3. Expected: pyRevit output shows the test exception traceback prefixed `[AgentD startup]`. AgentD extension is **still loaded**. The Check/Fill/Start buttons are still in the ribbon. The Start Bridge button is also there.
4. Click Start Bridge manually — bridge starts as if nothing happened.
5. Revert the temporary `raise` and restart Revit; auto-start should resume working.

If step 3 shows the AgentD ribbon DISAPPEARED instead of the buttons being present, your top-level `try/except` in `startup.py` isn't wrapping correctly — fix that before shipping.

### 6.4 Port-already-taken test

1. From PowerShell, before opening Revit, run a fake listener on 8101:
   ```powershell
   $listener = [System.Net.HttpListener]::new()
   $listener.Prefixes.Add("http://127.0.0.1:8101/")
   $listener.Start()
   ```
2. Open Revit.
3. Expected: `[AgentD startup] bridge auto-start returned False` in pyRevit output. AgentD ribbon still fully loaded. No crash.
4. Stop the PowerShell listener (`$listener.Stop()`), restart Revit — auto-start succeeds normally.

## 7. What NOT To Do

- Don't put `startup.py` inside `AgentD.tab/` or any subfolder. It must be at the extension root.
- Don't let any exception propagate from `startup.py`. Wrap everything.
- Don't touch `ActiveUIDocument` at startup — there is no active document yet.
- Don't `time.sleep()` or block in `startup.py`. Revit's startup is single-threaded; you'll delay the whole UI.
- Don't double-bind port 8101. Make `start_bridge()` idempotent.
- Don't change the port. Agent A's `external_agents.json` is pinned to 8101.
- Don't try to import anything from Agent A's extension. Agent D must remain runnable standalone (the existing 3 buttons must keep working even if Agent A isn't installed).

## 8. Quick Reference

| Thing | Value |
|---|---|
| File to create | `AgentD.extension/startup.py` |
| File to refactor | `AgentD.extension/AgentD.tab/Data Agent.panel/Bridge.pushbutton/script.py` |
| Required: callable | `start_bridge(uiapp) -> bool` exported from script.py |
| Required: idempotency | Second call when already listening = no-op success |
| Required: error handling | startup.py must NEVER raise, must NEVER print a traceback to a popup |
| When it runs | Once, at Revit launch, when pyRevit loads AgentD extension |
| What `__revit__` is at startup | A `UIApplication` reference (no active doc yet — that's fine) |
| Manual fallback | Existing Start Bridge ribbon button — kept, still works |
