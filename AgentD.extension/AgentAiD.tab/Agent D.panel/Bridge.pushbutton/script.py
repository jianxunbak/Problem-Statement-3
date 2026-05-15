# -*- coding: utf-8 -*-
"""Agent D HTTP Bridge.

Starts a System.Net.HttpListener (preferred port 8101, falls back through 8110)
that accepts JSON POSTs from Agent A and dispatches them to headless Agent D
operations. All Revit Document work is marshalled to the main thread via
ExternalEvent.

The chosen port is written to %TEMP%/agentd_bridge.port so Agent A can discover
it without hard-coding the value.

See integration_AgentD_bridge_spec.md for the wire protocol.
"""

import os
import sys
import json
import tempfile
import threading
import traceback

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('System')

from Autodesk.Revit.UI import IExternalEventHandler, ExternalEvent

import System
from System.Net import HttpListener
from System.Net.Sockets import TcpListener
from System.Net import IPAddress
from System.Text import Encoding
from System.Threading import Thread, ThreadStart, ManualResetEvent

from pyrevit import script, forms

# Make our own folder importable so we can load agentd_headless
_THIS_DIR = os.path.dirname(__file__)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import agentd_headless

output = script.get_output()

PREFERRED_PORT = 8101
PORT_FALLBACK_MAX = 8110  # inclusive — try 8101, 8102, ..., 8110
PORT_FILE = os.path.join(tempfile.gettempdir(), "agentd_bridge.port")

# Actual port the listener is bound to. Set by start_bridge() once it succeeds.
BRIDGE_PORT = None


# ---------------------------------------------------------------------------
# Main-thread marshalling: queue + ExternalEvent handler
# ---------------------------------------------------------------------------

class _MainThreadQueue(object):
    def __init__(self):
        self._lock = threading.Lock()
        self._items = []

    def put(self, item):
        with self._lock:
            self._items.append(item)

    def drain(self):
        with self._lock:
            items = self._items
            self._items = []
        return items


class BridgeEventHandler(IExternalEventHandler):
    """Drains the queue on the Revit main thread."""

    def __init__(self, queue):
        self._queue = queue

    def Execute(self, uiapp):
        try:
            doc = uiapp.ActiveUIDocument.Document if uiapp.ActiveUIDocument else None
        except Exception:
            doc = None

        for item in self._queue.drain():
            fn, args, result_holder, done_event = item
            try:
                if doc is None:
                    result_holder["data"] = {
                        "status": "error",
                        "reason": "no_active_document",
                        "message": "No active Revit document."
                    }
                else:
                    result_holder["data"] = fn(doc, *args)
            except Exception as e:
                result_holder["data"] = {
                    "status": "error",
                    "reason": "internal_error",
                    "message": str(e),
                    "trace": traceback.format_exc(),
                }
            try:
                done_event.Set()
            except Exception:
                pass

    def GetName(self):
        return "AgentD Bridge Handler"


_QUEUE = _MainThreadQueue()
_HANDLER = BridgeEventHandler(_QUEUE)
_EXTERNAL_EVENT = ExternalEvent.Create(_HANDLER)


def run_on_main_thread(fn, args, timeout_ms=600000):
    """Push a job onto the queue, Raise() the event, block on completion."""
    done = ManualResetEvent(False)
    result_holder = {"data": None}
    _QUEUE.put((fn, args, result_holder, done))
    _EXTERNAL_EVENT.Raise()
    signalled = done.WaitOne(timeout_ms)
    if not signalled:
        return {"status": "error", "reason": "internal_error",
                "message": "Main-thread call timed out after {} ms".format(timeout_ms)}
    return result_holder["data"]


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

_ACTIONS = {
    "audit_data": ("audit", ["schedule_name", "parameter_name"]),
    "fill_data": ("fill", ["schedule_name", "parameter_name", "api_key"]),
    "start_pipeline": ("start", ["schedule_name", "parameter_name", "api_key"]),
}


def _write_json(ctx, payload, status_code=200):
    resp = ctx.Response
    try:
        body = json.dumps(payload)
    except Exception as e:
        body = json.dumps({"status": "error", "reason": "internal_error",
                           "message": "JSON serialization failed: " + str(e)})
    data = Encoding.UTF8.GetBytes(body)
    resp.StatusCode = status_code
    resp.ContentType = "application/json"
    resp.ContentLength64 = data.Length
    try:
        resp.OutputStream.Write(data, 0, data.Length)
    finally:
        try:
            resp.OutputStream.Close()
        except Exception:
            pass
        try:
            resp.Close()
        except Exception:
            pass


class _SSEResponder(object):
    """Streams Server-Sent Events back to Agent A while a long fill/pipeline
    job runs.

    Agent A's external_agents._consume_stream looks for `data: <json>` lines
    where the json has either {"type":"status","text":...} (progress nudges)
    or {"type":"result","payload":...} (final response). We write the SSE
    headers eagerly so the client knows to switch to streaming mode, then
    flush after every event so Agent A's spinner updates per row instead of
    going dark for minutes.
    """

    def __init__(self, ctx):
        self._ctx = ctx
        resp = ctx.Response
        resp.StatusCode = 200
        resp.ContentType = "text/event-stream"
        # No ContentLength64 — chunked stream of unknown length.
        try:
            resp.SendChunked = True
        except Exception:
            pass
        try:
            resp.Headers.Add("Cache-Control", "no-cache")
        except Exception:
            pass
        self._stream = resp.OutputStream
        self._closed = False

    def status(self, text):
        self._write_event({"type": "status", "text": text})

    def result(self, payload):
        self._write_event({"type": "result", "payload": payload})

    def _write_event(self, obj):
        if self._closed:
            return
        try:
            line = "data: " + json.dumps(obj) + "\n\n"
            data = Encoding.UTF8.GetBytes(line)
            self._stream.Write(data, 0, data.Length)
            try:
                self._stream.Flush()
            except Exception:
                pass
        except Exception:
            # Client disconnected mid-stream — stop writing.
            self._closed = True

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._stream.Close()
        except Exception:
            pass
        try:
            self._ctx.Response.Close()
        except Exception:
            pass


def _read_request_body(ctx):
    req = ctx.Request
    if not req.HasEntityBody:
        return ""
    encoding = req.ContentEncoding if req.ContentEncoding else Encoding.UTF8
    from System.IO import StreamReader
    reader = StreamReader(req.InputStream, encoding)
    try:
        return reader.ReadToEnd()
    finally:
        try:
            reader.Close()
        except Exception:
            pass


def _handle_request(ctx):
    req = ctx.Request

    # Only /run POST is meaningful; everything else gets a friendly JSON.
    path = req.Url.AbsolutePath
    method = req.HttpMethod.upper() if req.HttpMethod else ""

    if path != "/run" or method != "POST":
        _write_json(ctx, {
            "status": "error",
            "reason": "unknown_action",
            "message": "POST JSON to /run. Got {} {}.".format(method, path),
        })
        return

    body = _read_request_body(ctx)
    try:
        payload = json.loads(body) if body else {}
    except Exception as e:
        _write_json(ctx, {"status": "error", "reason": "invalid_json",
                          "message": "Could not parse request body as JSON: " + str(e)})
        return

    action = payload.get("action")
    if action not in _ACTIONS:
        _write_json(ctx, {"status": "error", "reason": "unknown_action",
                          "message": "Unknown action: " + str(action)})
        return

    schedule_name = payload.get("schedule_name")
    parameter_name = payload.get("parameter_name")
    api_key = payload.get("api_key") or ""

    if not schedule_name or not parameter_name:
        _write_json(ctx, {"status": "error", "reason": "invalid_json",
                          "message": "Missing required field: schedule_name and parameter_name are required."})
        return

    if action == "audit_data":
        # audit_data is fast and doesn't call Claude — single main-thread hop is fine.
        result = run_on_main_thread(agentd_headless.audit_data,
                                    (schedule_name, parameter_name))
        _write_json(ctx, result)
        return

    # fill_data / start_pipeline: stream SSE so the main thread is free during
    # the slow per-row Claude calls. Without this, Agent A's chat window and
    # Revit's UI both freeze for the full duration of the run.
    resolved_key = agentd_headless._resolve_api_key(api_key)
    if not resolved_key:
        _write_json(ctx, {"status": "error", "reason": "api_key_missing",
                          "message": "No Anthropic API key supplied via request or user_config.ini"})
        return

    sse = _SSEResponder(ctx)
    try:
        result = _run_fill_streaming(sse, schedule_name, parameter_name,
                                     resolved_key, with_insights=(action == "start_pipeline"))
        sse.result(result)
    finally:
        sse.close()


def _run_fill_streaming(sse, schedule_name, parameter_name, api_key, with_insights):
    """Orchestrate the three-phase fill on the bridge's worker thread.

    Only fill_snapshot / fill_commit / pipeline_post_audit hop to the main
    thread (each runs in milliseconds for normal schedules). The per-row
    Claude calls run right here on the worker thread, so Revit's UI stays
    responsive throughout.
    """
    sse.status("Reading schedule…")
    snap = run_on_main_thread(agentd_headless.fill_snapshot,
                              (schedule_name, parameter_name))
    if not snap or snap.get("status") != "ready":
        return snap or {"status": "error", "reason": "internal_error",
                        "message": "fill_snapshot returned no result."}

    rows = snap["rows"]
    target_param_id = snap["target_param_id"]
    total_elements = snap["total_elements"]
    initially_missing = len(rows)

    if not rows:
        # Nothing to fill — emit a success result so Agent A doesn't show "failed".
        return {
            "status": "success",
            "target_parameter": parameter_name,
            "statistics": {
                "total_elements": total_elements,
                "initially_missing": 0,
                "ai_filled_successfully": 0,
                "errors": 0,
            }
        }

    sse.status("Predicting {} missing value{}…".format(
        initially_missing, "" if initially_missing == 1 else "s"))

    updates = []
    errors = 0
    for idx, row in enumerate(rows, start=1):
        sse.status("Predicting row {}/{} ({})…".format(idx, initially_missing, row["category"]))
        out = agentd_headless.fill_predict_one(api_key, parameter_name, row)
        if "value" in out:
            updates.append(out)
        else:
            errors += 1

    sse.status("Writing {} value{} to Revit…".format(
        len(updates), "" if len(updates) == 1 else "s"))
    commit = run_on_main_thread(agentd_headless.fill_commit,
                                (parameter_name, target_param_id, updates))
    if commit.get("status") == "error":
        return commit
    filled_ok = commit.get("filled_ok", 0)

    statistics = {
        "total_elements": total_elements,
        "initially_missing": initially_missing,
        "ai_filled_successfully": filled_ok,
        "errors": errors,
    }

    if not with_insights:
        return {
            "status": "success",
            "target_parameter": parameter_name,
            "statistics": statistics,
        }

    sse.status("Running AI sanity check…")
    post = run_on_main_thread(agentd_headless.pipeline_post_audit,
                              (schedule_name, parameter_name))
    if not post or post.get("status") != "ready":
        # Surface a soft-fail: the fill succeeded, just no insights.
        return {
            "status": "success",
            "target_parameter": parameter_name,
            "statistics": statistics,
            "ai_sanity_check_insights": ["AI sanity check unavailable: post-fill audit failed."],
        }
    system_prompt, user_prompt = agentd_headless.pipeline_build_insights_prompt(
        parameter_name, post["audit"])
    insights = agentd_headless.pipeline_call_insights(api_key, system_prompt, user_prompt)

    return {
        "status": "success",
        "target_parameter": parameter_name,
        "statistics": statistics,
        "ai_sanity_check_insights": insights,
    }


def _accept_loop(listener):
    while True:
        try:
            ctx = listener.GetContext()
        except Exception:
            # Listener was stopped or disposed.
            return
        try:
            _handle_request(ctx)
        except Exception as e:
            try:
                _write_json(ctx, {"status": "error", "reason": "internal_error",
                                  "message": str(e), "trace": traceback.format_exc()})
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Port-in-use detection & singleton guard
# ---------------------------------------------------------------------------

# Module-level handle so a second click in the same Revit session is a no-op.
_BRIDGE_LISTENER = None


def _port_in_use(port):
    try:
        probe = TcpListener(IPAddress.Loopback, port)
        probe.Start()
        probe.Stop()
        return False
    except Exception:
        return True


def _is_listening():
    if _BRIDGE_LISTENER is None:
        return False
    try:
        return bool(_BRIDGE_LISTENER.IsListening)
    except Exception:
        return False


def _try_bind(port):
    """Attempt to start an HttpListener on `port`. Return the listener on
    success, or None if the port is taken / bind fails."""
    listener = HttpListener()
    listener.Prefixes.Add("http://127.0.0.1:{}/".format(port))
    try:
        listener.Start()
        return listener
    except Exception:
        try:
            listener.Close()
        except Exception:
            pass
        return None


def _write_port_file(port):
    """Advertise the chosen port to Agent A via %TEMP%/agentd_bridge.port."""
    try:
        f = open(PORT_FILE, "w")
        try:
            f.write(str(port))
        finally:
            f.close()
    except Exception as e:
        try:
            print("Warning: could not write port file {}: {}".format(PORT_FILE, e))
        except Exception:
            pass


def _clear_port_file():
    try:
        if os.path.exists(PORT_FILE):
            os.remove(PORT_FILE)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Boot entry — callable from ribbon click OR startup.py
# ---------------------------------------------------------------------------

def start_bridge(uiapp=None):
    """Idempotent bridge starter with port fallback.

    Tries ports PREFERRED_PORT..PORT_FALLBACK_MAX (8101..8110) until one binds,
    writes the chosen port to %TEMP%/agentd_bridge.port for Agent A to discover.
    Returns True on success (already running counts as success), False if every
    candidate port is taken. Never raises.
    """
    global _BRIDGE_LISTENER, BRIDGE_PORT

    if _is_listening():
        try:
            print("Bridge already running on http://127.0.0.1:{}".format(BRIDGE_PORT))
        except Exception:
            pass
        return True

    listener = None
    chosen_port = None
    for port in range(PREFERRED_PORT, PORT_FALLBACK_MAX + 1):
        if _port_in_use(port):
            continue
        listener = _try_bind(port)
        if listener is not None:
            chosen_port = port
            break

    if listener is None:
        try:
            print("Bridge failed to start: no free port in range {}-{}".format(
                PREFERRED_PORT, PORT_FALLBACK_MAX))
        except Exception:
            pass
        return False

    _BRIDGE_LISTENER = listener
    BRIDGE_PORT = chosen_port
    _write_port_file(chosen_port)

    def _runner():
        _accept_loop(listener)

    thread = Thread(ThreadStart(_runner))
    thread.IsBackground = True
    thread.Start()

    try:
        if chosen_port == PREFERRED_PORT:
            print("Bridge listening on http://127.0.0.1:{}".format(chosen_port))
        else:
            print("Bridge listening on http://127.0.0.1:{} (preferred port {} was busy)".format(
                chosen_port, PREFERRED_PORT))
        print("Port written to {}".format(PORT_FILE))
    except Exception:
        pass
    return True


def main():
    """Ribbon button entry point."""
    if _is_listening():
        forms.alert("Bridge already running on port {}".format(BRIDGE_PORT),
                    title="Agent D Bridge")
        return

    uiapp = globals().get("__revit__")  # injected by pyRevit at runtime
    ok = start_bridge(uiapp)
    if ok:
        return

    forms.alert(
        "Failed to start bridge: ports {}-{} are all in use.\n\n"
        "Check the pyRevit output panel for details.".format(
            PREFERRED_PORT, PORT_FALLBACK_MAX),
        title="Agent D Bridge")


# pyRevit executes pushbutton scripts directly (not via if __name__ == "__main__"),
# so guard on __name__ to distinguish a click (__main__) from a startup.py import.
if __name__ == "__main__":
    main()
