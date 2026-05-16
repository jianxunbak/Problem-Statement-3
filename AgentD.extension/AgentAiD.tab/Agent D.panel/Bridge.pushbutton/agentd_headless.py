# -*- coding: utf-8 -*-
"""Headless Agent D operations for the HTTP bridge.

These functions are called from script.py on the Revit main thread (marshalled
via ExternalEvent). They take a Revit `doc` plus primitive arguments and return
JSON-serializable dicts. No pyrevit.forms UI prompts, no os.startfile, no HTML
output. Errors are returned as structured dicts.
"""

import json
import clr

# IronPython 2.7 has `unicode` built-in. This shim keeps IDE static analyzers
# (which assume Python 3) quiet without changing runtime behavior.
try:
    unicode  # type: ignore[name-defined]
except NameError:  # pragma: no cover — only fires under a Py3 linter
    unicode = str  # type: ignore[assignment,misc]

clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ViewSchedule,
    Transaction,
    StorageType
)

import System
clr.AddReference('System')
from System.Net import WebRequest, ServicePointManager, SecurityProtocolType
from System.IO import StreamReader


# ---------------------------------------------------------------------------
# Anthropic helpers (copied from Fill/Start pushbuttons so we don't depend on
# their script.py files at runtime). Same WebRequest + TLS 1.2 pattern.
# ---------------------------------------------------------------------------

def _call_claude_predict(api_key, target_param_name, category, family, type_name):
    ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12

    url = "https://api.anthropic.com/v1/messages"
    request = WebRequest.Create(url)
    request.Method = "POST"
    request.ContentType = "application/json"
    request.Headers.Add("x-api-key", api_key)
    request.Headers.Add("anthropic-version", "2023-06-01")

    system_prompt = (
        "You are an expert BIM data agent. Your task is to predict the most likely "
        "value for a missing Revit parameter based on the element's Category, Family, and Type. "
        "Respond ONLY with the predicted value. Do not add any conversational text, formatting, or punctuation. "
        "Always make your best professional guess for a concise value (e.g., a descriptive name or standard code). "
        "NEVER respond with 'UNKNOWN' or say you cannot do it."
    )

    user_prompt = (
        "Predict the value for the parameter '{}'.\n"
        "Category: {}\n"
        "Family: {}\n"
        "Type: {}".format(target_param_name, category, family, type_name)
    )

    data = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 50,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        "temperature": 0.0
    }

    json_data = json.dumps(data)
    bytes_data = System.Text.Encoding.UTF8.GetBytes(json_data)
    request.ContentLength = bytes_data.Length

    try:
        stream = request.GetRequestStream()
        stream.Write(bytes_data, 0, bytes_data.Length)
        stream.Close()

        response = request.GetResponse()
        reader = StreamReader(response.GetResponseStream())
        response_text = reader.ReadToEnd()
        reader.Close()
        response.Close()

        result = json.loads(response_text)
        return result['content'][0]['text'].strip()
    except Exception as e:
        return "ERROR: " + str(e)


def _call_claude_insights(api_key, system_prompt, user_prompt):
    ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12
    url = "https://api.anthropic.com/v1/messages"
    request = WebRequest.Create(url)
    request.Method = "POST"
    request.ContentType = "application/json"
    request.Headers.Add("x-api-key", api_key)
    request.Headers.Add("anthropic-version", "2023-06-01")

    data = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 500,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        "temperature": 0.2
    }

    json_data = json.dumps(data)
    bytes_data = System.Text.Encoding.UTF8.GetBytes(json_data)
    request.ContentLength = bytes_data.Length

    try:
        stream = request.GetRequestStream()
        stream.Write(bytes_data, 0, bytes_data.Length)
        stream.Close()
        response = request.GetResponse()
        reader = StreamReader(response.GetResponseStream())
        response_text = reader.ReadToEnd()
        reader.Close()
        response.Close()
        result = json.loads(response_text)
        return result['content'][0]['text'].strip()
    except Exception as e:
        return "ERROR: " + str(e)


def _resolve_api_key(api_key):
    """Resolve the API key per spec: request value, else user_config.ini, else None."""
    if api_key:
        return api_key
    try:
        from pyrevit.userconfig import user_config
        try:
            config = user_config.get_section("DataAgent")
        except Exception:
            return None
        return getattr(config, "anthropic_api_key", None)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Schedule / parameter resolution
# ---------------------------------------------------------------------------

def _find_schedule(doc, schedule_name):
    schedules = FilteredElementCollector(doc).OfClass(ViewSchedule).ToElements()
    for s in schedules:
        try:
            if s.IsTitleblockRevisionSchedule:
                continue
        except Exception:
            pass
        if s.Name == schedule_name:
            return s
    return None


def _find_param_id_on_schedule(target_schedule, parameter_name):
    definition = target_schedule.Definition
    for i in range(definition.GetFieldCount()):
        field = definition.GetField(i)
        if field.ColumnHeading == parameter_name:
            return field.ParameterId
    return None


def _get_param(doc, el, target_param_id):
    """Return the Parameter on instance or type, or None."""
    for p in el.Parameters:
        if p.Id.Equals(target_param_id):
            return p
    try:
        el_type = doc.GetElement(el.GetTypeId())
        if el_type:
            for p in el_type.Parameters:
                if p.Id.Equals(target_param_id):
                    return p
    except Exception:
        pass
    return None


def _gather_context(doc, el):
    category_name = el.Category.Name if getattr(el, "Category", None) else "Unknown Category"
    family_name = "Unknown Family"
    type_name = "Unknown Type"
    try:
        el_type = doc.GetElement(el.GetTypeId())
        if el_type:
            type_name = getattr(el_type, "Name", "Unknown Type")
            family_name = getattr(el_type, "FamilyName", "Unknown Family")
    except Exception:
        pass
    return category_name, family_name, type_name


def _build_audit_report(doc, target_schedule, target_param_id):
    """Scan a schedule and produce the nested report + counts. Headless."""
    elements = FilteredElementCollector(doc, target_schedule.Id).ToElements()

    report = {}
    cat_unique_filled = {}
    missing_count = 0
    filled_count = 0
    skipped = 0
    param_found_on_any = False

    for el in elements:
        category_name, family_name, type_name = _gather_context(doc, el)

        param = _get_param(doc, el, target_param_id)
        if not param:
            skipped += 1
            continue

        param_found_on_any = True

        is_empty = False
        current_val = ""
        if param.StorageType == StorageType.String:
            current_val = param.AsString()
            if not current_val or current_val.strip() == "":
                is_empty = True
        else:
            if not param.HasValue:
                is_empty = True
            else:
                current_val = param.AsValueString()

        if category_name not in report:
            report[category_name] = {}
        if family_name not in report[category_name]:
            report[category_name][family_name] = {}
        if type_name not in report[category_name][family_name]:
            report[category_name][family_name][type_name] = {"missing": 0, "filled": 0, "missing_ids": []}

        bucket = report[category_name][family_name][type_name]
        if is_empty:
            bucket["missing"] += 1
            bucket["missing_ids"].append(el.Id)
            missing_count += 1
        else:
            bucket["filled"] += 1
            filled_count += 1
            if category_name not in cat_unique_filled:
                cat_unique_filled[category_name] = set()
            if len(cat_unique_filled[category_name]) < 20 and current_val:
                cat_unique_filled[category_name].add(current_val)

    return {
        "report": report,
        "cat_unique_filled": cat_unique_filled,
        "missing_count": missing_count,
        "filled_count": filled_count,
        "skipped": skipped,
        "total_elements": len(elements),
        "param_found_on_any": param_found_on_any,
    }


def _serialize_by_category(report):
    """Strip internal-only fields (ElementId objects) before JSON serialization."""
    out = {}
    for cat, fams in report.items():
        out[cat] = {}
        for fam, types in fams.items():
            out[cat][fam] = {}
            for typ, bucket in types.items():
                out[cat][fam][typ] = {
                    "missing": bucket["missing"],
                    "filled": bucket["filled"],
                }
    return out


# ---------------------------------------------------------------------------
# Public headless API
# ---------------------------------------------------------------------------

def audit_data(doc, schedule_name, parameter_name):
    if doc is None:
        return {"status": "error", "reason": "no_active_document",
                "message": "No active Revit document."}

    target_schedule = _find_schedule(doc, schedule_name)
    if target_schedule is None:
        return {"status": "error", "reason": "schedule_not_found",
                "schedule": schedule_name,
                "message": "ViewSchedule '{}' not found in active document.".format(schedule_name)}

    target_param_id = _find_param_id_on_schedule(target_schedule, parameter_name)
    if target_param_id is None:
        return {"status": "error", "reason": "parameter_not_found",
                "parameter": parameter_name,
                "message": "Parameter '{}' is not a field on schedule '{}'.".format(parameter_name, schedule_name)}

    audit = _build_audit_report(doc, target_schedule, target_param_id)

    if not audit["param_found_on_any"]:
        return {"status": "error", "reason": "parameter_not_found",
                "parameter": parameter_name,
                "message": "Parameter '{}' is not present on any element in schedule '{}'.".format(parameter_name, schedule_name)}

    return {
        "status": "success",
        "schedule": schedule_name,
        "parameter": parameter_name,
        "statistics": {
            "total_elements": audit["total_elements"],
            "missing": audit["missing_count"],
            "filled": audit["filled_count"],
            "by_category": _serialize_by_category(audit["report"]),
        }
    }


# ---------------------------------------------------------------------------
# Split-phase fill API
#
# The monolithic fill_data() below blocks Revit's main thread for the full
# duration of the run because each per-row Claude call (~1-2s) happens inside
# the main-thread loop. For schedules with dozens of missing rows this freezes
# both Revit and Agent A's chat window.
#
# These three helpers split the work so only the fast Revit-API parts run on
# the main thread; the slow Claude HTTP calls run on the bridge's worker
# thread, where they don't block the UI:
#
#   fill_snapshot(doc, ...)        — main thread, fast: collect rows to fill
#   fill_predict_one(api_key, row) — worker thread, slow: one Claude call
#   fill_commit(doc, updates, ...) — main thread, fast: one Transaction
#
# The bridge in script.py orchestrates them; agentd_headless.fill_data is kept
# below for any direct caller that still wants the blocking single-shot API.
# ---------------------------------------------------------------------------


def fill_snapshot(doc, schedule_name, parameter_name):
    """Main-thread phase 1: resolve schedule/param and list rows needing fill.

    Returns one of:
        {"status": "error", ...}
        {"status": "ready", "target_param_id": <ElementId>,
         "rows": [{"el_id": <ElementId>, "category": str, "family": str,
                   "type": str}, ...],
         "total_elements": int, "read_only_seen": bool}

    `target_param_id` is opaque to the worker thread — it's threaded back into
    fill_commit() unchanged.
    """
    if doc is None:
        return {"status": "error", "reason": "no_active_document",
                "message": "No active Revit document."}

    target_schedule = _find_schedule(doc, schedule_name)
    if target_schedule is None:
        return {"status": "error", "reason": "schedule_not_found",
                "schedule": schedule_name,
                "message": "ViewSchedule '{}' not found.".format(schedule_name)}

    target_param_id = _find_param_id_on_schedule(target_schedule, parameter_name)
    if target_param_id is None:
        return {"status": "error", "reason": "parameter_not_found",
                "parameter": parameter_name,
                "message": "Parameter '{}' is not a field on schedule '{}'.".format(parameter_name, schedule_name)}

    elements = FilteredElementCollector(doc, target_schedule.Id).ToElements()
    total_elements = len(elements)

    rows = []
    param_found_on_any = False
    read_only_seen = False

    for el in elements:
        param = _get_param(doc, el, target_param_id)
        if not param:
            continue
        param_found_on_any = True

        if param.IsReadOnly:
            read_only_seen = True
            continue

        if param.StorageType != StorageType.String:
            continue

        current_val = param.AsString()
        if current_val and current_val.strip() != "":
            continue

        category_name, family_name, type_name = _gather_context(doc, el)
        rows.append({
            "el_id": el.Id,
            "category": category_name,
            "family": family_name,
            "type": type_name,
        })

    if not param_found_on_any:
        return {"status": "error", "reason": "parameter_not_found",
                "parameter": parameter_name,
                "message": "Parameter '{}' is not present on any element in schedule '{}'.".format(parameter_name, schedule_name)}

    if not rows and read_only_seen and total_elements > 0:
        return {"status": "error", "reason": "parameter_read_only",
                "parameter": parameter_name,
                "message": "Parameter '{}' is read-only on all matching elements.".format(parameter_name)}

    return {
        "status": "ready",
        "target_param_id": target_param_id,
        "rows": rows,
        "total_elements": total_elements,
        "read_only_seen": read_only_seen,
    }


def fill_predict_one(api_key, parameter_name, row):
    """Worker-thread phase 2: one Claude call. NO Revit API access here.

    `row` is a dict from fill_snapshot()'s rows list. Returns either:
        {"el_id": ..., "value": "<predicted>"} on success, or
        {"el_id": ..., "error": "<reason>"} on failure / UNKNOWN.
    """
    predicted = _call_claude_predict(api_key, parameter_name,
                                     row["category"], row["family"], row["type"])
    if predicted and not predicted.startswith("ERROR") and predicted != "UNKNOWN":
        return {"el_id": row["el_id"], "value": predicted}
    return {"el_id": row["el_id"], "error": predicted or "empty_response"}


def fill_commit(doc, parameter_name, target_param_id, updates):
    """Main-thread phase 3: write all predicted values in one Transaction.

    `updates` is a list of {"el_id", "value"} dicts (errors filtered out by
    the caller). Returns {"filled_ok": int} on success or {"status": "error", ...}.
    """
    if doc is None:
        return {"status": "error", "reason": "no_active_document",
                "message": "No active Revit document."}

    if not updates:
        return {"filled_ok": 0}

    t = Transaction(doc, "Agent D - Fill: " + parameter_name)
    t.Start()
    filled_ok = 0
    try:
        for upd in updates:
            el = doc.GetElement(upd["el_id"])
            if el is None:
                continue
            param = _get_param(doc, el, target_param_id)
            if param and not param.IsReadOnly:
                param.Set(upd["value"])
                filled_ok += 1
        t.Commit()
    except Exception as e:
        try:
            t.RollBack()
        except Exception:
            pass
        return {"status": "error", "reason": "transaction_failed",
                "message": "Transaction failed: " + str(e)}

    return {"filled_ok": filled_ok}


def fill_data(doc, schedule_name, parameter_name, api_key):
    if doc is None:
        return {"status": "error", "reason": "no_active_document",
                "message": "No active Revit document."}

    resolved_key = _resolve_api_key(api_key)
    if not resolved_key:
        return {"status": "error", "reason": "api_key_missing",
                "message": "No Anthropic API key supplied via request or user_config.ini"}

    target_schedule = _find_schedule(doc, schedule_name)
    if target_schedule is None:
        return {"status": "error", "reason": "schedule_not_found",
                "schedule": schedule_name,
                "message": "ViewSchedule '{}' not found.".format(schedule_name)}

    target_param_id = _find_param_id_on_schedule(target_schedule, parameter_name)
    if target_param_id is None:
        return {"status": "error", "reason": "parameter_not_found",
                "parameter": parameter_name,
                "message": "Parameter '{}' is not a field on schedule '{}'.".format(parameter_name, schedule_name)}

    elements = FilteredElementCollector(doc, target_schedule.Id).ToElements()
    total_elements = len(elements)

    # Phase 1: predict (no transaction yet)
    updates_to_make = []
    errors = 0
    initially_missing = 0
    read_only_seen = False
    param_found_on_any = False

    for el in elements:
        param = _get_param(doc, el, target_param_id)
        if not param:
            continue
        param_found_on_any = True

        if param.IsReadOnly:
            read_only_seen = True
            continue

        if param.StorageType != StorageType.String:
            continue

        current_val = param.AsString()
        if current_val and current_val.strip() != "":
            continue

        initially_missing += 1
        category_name, family_name, type_name = _gather_context(doc, el)
        predicted = _call_claude_predict(resolved_key, parameter_name,
                                         category_name, family_name, type_name)
        if predicted and not predicted.startswith("ERROR") and predicted != "UNKNOWN":
            updates_to_make.append((el.Id, predicted))
        else:
            errors += 1

    if not param_found_on_any:
        return {"status": "error", "reason": "parameter_not_found",
                "parameter": parameter_name,
                "message": "Parameter '{}' is not present on any element in schedule '{}'.".format(parameter_name, schedule_name)}

    if not updates_to_make and initially_missing == 0 and read_only_seen and total_elements > 0:
        return {"status": "error", "reason": "parameter_read_only",
                "parameter": parameter_name,
                "message": "Parameter '{}' is read-only on all matching elements.".format(parameter_name)}

    filled_ok = 0
    if updates_to_make:
        t = Transaction(doc, "Agent D - Fill: " + parameter_name)
        t.Start()
        try:
            for el_id, predicted_value in updates_to_make:
                el = doc.GetElement(el_id)
                param = _get_param(doc, el, target_param_id)
                if param and not param.IsReadOnly:
                    param.Set(predicted_value)
                    filled_ok += 1
            t.Commit()
        except Exception as e:
            try:
                t.RollBack()
            except Exception:
                pass
            return {"status": "error", "reason": "transaction_failed",
                    "message": "Transaction failed: " + str(e)}

    return {
        "status": "success",
        "target_parameter": parameter_name,
        "statistics": {
            "total_elements": total_elements,
            "initially_missing": initially_missing,
            "ai_filled_successfully": filled_ok,
            "errors": errors,
        }
    }


def pipeline_post_audit(doc, schedule_name, parameter_name):
    """Main-thread helper for the split-phase start_pipeline.

    After fill_commit runs, the bridge needs the post-fill audit (so the AI
    sanity-check prompt sees the current state). Returns the audit dict on
    success, or {"status": "error", ...}. The sanity-check Claude call itself
    runs on the worker thread — see pipeline_build_insights_prompt below.
    """
    if doc is None:
        return {"status": "error", "reason": "no_active_document",
                "message": "No active Revit document."}
    target_schedule = _find_schedule(doc, schedule_name)
    if target_schedule is None:
        return {"status": "error", "reason": "schedule_not_found",
                "schedule": schedule_name,
                "message": "ViewSchedule '{}' not found.".format(schedule_name)}
    target_param_id = _find_param_id_on_schedule(target_schedule, parameter_name)
    if target_param_id is None:
        return {"status": "error", "reason": "parameter_not_found",
                "parameter": parameter_name,
                "message": "Parameter '{}' is not a field on schedule '{}'."
                           .format(parameter_name, schedule_name)}
    audit = _build_audit_report(doc, target_schedule, target_param_id)
    return {"status": "ready", "audit": audit}


def pipeline_build_insights_prompt(parameter_name, audit):
    """Pure function (no Revit, no doc) — produces the (system, user) prompts
    consumed by _call_claude_insights. Lives here so the bridge stays a thin
    orchestrator."""
    system_prompt = (
        "You are an expert BIM Manager AI. Analyze Revit data audit results and "
        "produce a SHORT plain-text list of insights. Output ONLY a JSON array of "
        "strings, e.g. [\"Task 1: ...\", \"Task 2: ...\"]. No prose outside the array."
    )

    cat_lines = []
    for cat, fams in audit["report"].items():
        m = 0
        f = 0
        for fam, types in fams.items():
            for typ, bucket in types.items():
                m += bucket["missing"]
                f += bucket["filled"]
        cat_lines.append("- {}: {} missing, {} filled".format(cat, m, f))

    unique_lines = []
    for cat, vals in audit["cat_unique_filled"].items():
        if vals:
            safe_vals = [v for v in vals if v]
            unique_lines.append("- {}: {}".format(cat, list(safe_vals)))

    user_prompt = (
        "Audit Results for Parameter: '{}'\n\n"
        "MISSING DATA COUNTS:\n{}\n\n"
        "UNIQUE FILLED VALUES SAMPLED:\n{}\n\n"
        "Task 1 (Data Quality Sanity Check): Identify any filled values that look like "
        "errors, typos, or generic placeholders (e.g. 'TBD', 'N/A'). If none, say data "
        "quality looks standard.\n"
        "Task 2 (Next Steps): Recommend the next 1-2 actions, starting with the category "
        "with the most missing data.\n"
        "Respond as a JSON array of strings only."
    ).format(parameter_name, "\n".join(cat_lines), "\n".join(unique_lines))

    return system_prompt, user_prompt


def pipeline_call_insights(api_key, system_prompt, user_prompt):
    """Worker-thread wrapper around _call_claude_insights. Returns a parsed
    list[str] of insights — never raises."""
    insights = []
    try:
        raw = _call_claude_insights(api_key, system_prompt, user_prompt)
        if raw and not raw.startswith("ERROR"):
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            try:
                parsed = json.loads(cleaned)
                if isinstance(parsed, list):
                    insights = [str(x) for x in parsed]
                else:
                    insights = [cleaned]
            except Exception:
                insights = [line.strip() for line in cleaned.split("\n") if line.strip()]
    except Exception as e:
        insights = ["AI sanity check unavailable: " + str(e)]
    return insights


def start_pipeline(doc, schedule_name, parameter_name, api_key):
    if doc is None:
        return {"status": "error", "reason": "no_active_document",
                "message": "No active Revit document."}

    resolved_key = _resolve_api_key(api_key)
    if not resolved_key:
        return {"status": "error", "reason": "api_key_missing",
                "message": "No Anthropic API key supplied via request or user_config.ini"}

    fill_result = fill_data(doc, schedule_name, parameter_name, resolved_key)
    if fill_result.get("status") != "success":
        return fill_result

    target_schedule = _find_schedule(doc, schedule_name)
    target_param_id = _find_param_id_on_schedule(target_schedule, parameter_name)
    audit = _build_audit_report(doc, target_schedule, target_param_id)

    insights = []
    try:
        system_prompt = (
            "You are an expert BIM Manager AI. Analyze Revit data audit results and "
            "produce a SHORT plain-text list of insights. Output ONLY a JSON array of "
            "strings, e.g. [\"Task 1: ...\", \"Task 2: ...\"]. No prose outside the array."
        )

        cat_lines = []
        for cat, fams in audit["report"].items():
            m = 0
            f = 0
            for fam, types in fams.items():
                for typ, bucket in types.items():
                    m += bucket["missing"]
                    f += bucket["filled"]
            cat_lines.append("- {}: {} missing, {} filled".format(cat, m, f))

        unique_lines = []
        for cat, vals in audit["cat_unique_filled"].items():
            if vals:
                safe_vals = [v for v in vals if v]
                unique_lines.append("- {}: {}".format(cat, list(safe_vals)))

        user_prompt = (
            "Audit Results for Parameter: '{}'\n\n"
            "MISSING DATA COUNTS:\n{}\n\n"
            "UNIQUE FILLED VALUES SAMPLED:\n{}\n\n"
            "Task 1 (Data Quality Sanity Check): Identify any filled values that look like "
            "errors, typos, or generic placeholders (e.g. 'TBD', 'N/A'). If none, say data "
            "quality looks standard.\n"
            "Task 2 (Next Steps): Recommend the next 1-2 actions, starting with the category "
            "with the most missing data.\n"
            "Respond as a JSON array of strings only."
        ).format(parameter_name, "\n".join(cat_lines), "\n".join(unique_lines))

        raw = _call_claude_insights(resolved_key, system_prompt, user_prompt)
        if raw and not raw.startswith("ERROR"):
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            try:
                parsed = json.loads(cleaned)
                if isinstance(parsed, list):
                    insights = [str(x) for x in parsed]
                else:
                    insights = [cleaned]
            except Exception:
                insights = [line.strip() for line in cleaned.split("\n") if line.strip()]
    except Exception as e:
        insights = ["AI sanity check unavailable: " + str(e)]

    return {
        "status": "success",
        "target_parameter": parameter_name,
        "statistics": fill_result["statistics"],
        "ai_sanity_check_insights": insights,
    }


# ---------------------------------------------------------------------------
# HTML dashboard renderer (Agent A "fill description" flow opens this at end)
#
# Mirrors the look of the standalone Start.pushbutton dashboard but builds it
# from the audit dict the bridge already has on hand, so we don't depend on
# the original pushbutton script (which we promised not to edit).
# ---------------------------------------------------------------------------

_DASHBOARD_TEMPLATE = u"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Data Audit Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0f172a;
            --card-bg: rgba(30, 41, 59, 0.7);
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent: #38bdf8;
            --danger: #f43f5e;
            --success: #10b981;
        }
        body {
            margin: 0; padding: 2rem;
            font-family: 'Inter', sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            background-image: radial-gradient(circle at top right, #1e1b4b, #0f172a);
            min-height: 100vh;
        }
        .header { text-align: center; margin-bottom: 2rem; }
        .header h1 {
            font-weight: 800; font-size: 2.5rem; margin: 0;
            background: linear-gradient(to right, var(--accent), #818cf8);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .stats-container {
            display: flex; gap: 1.5rem; justify-content: center;
            margin-bottom: 2rem; flex-wrap: wrap;
        }
        .stat-card {
            background: var(--card-bg); backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.1); border-radius: 1rem;
            padding: 1.5rem 2.5rem; text-align: center; min-width: 150px;
            box-shadow: 0 10px 15px -3px rgba(0,0,0,0.5);
        }
        .stat-card h3 { margin: 0 0 0.5rem 0; color: var(--text-muted); font-weight: 600; font-size: 1rem; }
        .stat-card .value { font-size: 2.5rem; font-weight: 800; margin: 0; }
        .stat-card.missing .value { color: var(--danger); }
        .stat-card.filled  .value { color: var(--success); }
        .stat-card.filled-now .value { color: var(--accent); }
        .ai-card {
            background: rgba(56,189,248,0.1);
            border: 1px solid rgba(56,189,248,0.3);
            border-radius: 1rem; padding: 1.5rem 2.5rem;
            margin: 0 auto 2rem auto; max-width: 1200px;
            box-shadow: 0 10px 15px -3px rgba(0,0,0,0.5);
        }
        .ai-card h3 { color: var(--accent); margin-top: 0; }
        .ai-card ul { margin: 0 0 1rem 0; padding-left: 1.5rem; }
        .ai-card li { margin-bottom: 0.5rem; }
        .charts-grid {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
            gap: 2rem; max-width: 1200px; margin: 0 auto;
        }
        .chart-wrapper {
            background: var(--card-bg); backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.1); border-radius: 1rem;
            padding: 1.5rem; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.5);
        }
        canvas { width: 100% !important; height: 300px !important; }
        .footer { text-align: center; color: var(--text-muted); margin-top: 2rem; font-size: 0.85rem; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Data Audit Dashboard</h1>
        <p style="color: var(--text-muted)">Analysis of Target Parameter: <strong>__TARGET_PARAM__</strong></p>
    </div>

    <div class="stats-container">
        <div class="stat-card missing"><h3>Missing Data</h3><p class="value">__MISSING_COUNT__</p></div>
        <div class="stat-card filled"><h3>Filled Data</h3><p class="value">__FILLED_COUNT__</p></div>
        <div class="stat-card filled-now"><h3>Filled This Run</h3><p class="value">__FILLED_NOW__</p></div>
    </div>

    __AI_INSIGHTS__

    <div class="charts-grid">
        <div class="chart-wrapper"><canvas id="overviewChart"></canvas></div>
        <div class="chart-wrapper"><canvas id="categoryChart"></canvas></div>
        <div class="chart-wrapper" style="grid-column: 1 / -1;"><canvas id="familyChart"></canvas></div>
    </div>

    <div class="footer">Generated by Agent D Bridge — __TIMESTAMP__</div>

    <script>
        Chart.defaults.color = '#94a3b8';
        Chart.defaults.font.family = 'Inter';

        new Chart(document.getElementById('overviewChart'), {
            type: 'doughnut',
            data: { labels: ['Missing','Filled'],
                    datasets: [{ data: [__MISSING_COUNT__, __FILLED_COUNT__],
                                 backgroundColor: ['#f43f5e','#10b981'],
                                 borderWidth: 0, hoverOffset: 10 }] },
            options: { responsive: true, maintainAspectRatio: false,
                       plugins: { legend: { position: 'bottom' },
                                  title: { display: true, text: 'Overall Completion',
                                           color: '#f8fafc', font: {size: 16} } },
                       cutout: '70%' }
        });

        new Chart(document.getElementById('categoryChart'), {
            type: 'bar',
            data: { labels: __CAT_LABELS__,
                    datasets: [{ label: 'Missing', data: __CAT_MISSING__, backgroundColor: '#f43f5e', borderRadius: 4 },
                               { label: 'Filled',  data: __CAT_FILLED__,  backgroundColor: '#10b981', borderRadius: 4 }] },
            options: { responsive: true, maintainAspectRatio: false,
                       plugins: { title: { display: true, text: 'Data by Category',
                                           color: '#f8fafc', font: {size: 16} } },
                       scales: { x: { stacked: true, grid: { color: 'rgba(255,255,255,0.05)' } },
                                 y: { stacked: true, grid: { color: 'rgba(255,255,255,0.05)' } } } }
        });

        new Chart(document.getElementById('familyChart'), {
            type: 'bar',
            data: { labels: __FAM_LABELS__,
                    datasets: [{ label: 'Missing', data: __FAM_MISSING__, backgroundColor: '#f43f5e', borderRadius: 4 },
                               { label: 'Filled',  data: __FAM_FILLED__,  backgroundColor: '#10b981', borderRadius: 4 }] },
            options: { responsive: true, maintainAspectRatio: false,
                       plugins: { title: { display: true, text: 'Top Families (Most Missing Data)',
                                           color: '#f8fafc', font: {size: 16} } },
                       scales: { x: { stacked: true, grid: { color: 'rgba(255,255,255,0.05)' } },
                                 y: { stacked: true, grid: { color: 'rgba(255,255,255,0.05)' } } } }
        });
    </script>
</body>
</html>
"""


def _aggregate_chart_data(audit_report):
    """Return (cat_labels, cat_missing, cat_filled, fam_labels, fam_missing, fam_filled)."""
    cat_data = {}
    fam_data = {}
    for cat, fams in audit_report.items():
        if cat not in cat_data:
            cat_data[cat] = {"missing": 0, "filled": 0}
        for fam, types in fams.items():
            if fam not in fam_data:
                fam_data[fam] = {"missing": 0, "filled": 0}
            for typ, bucket in types.items():
                m = bucket["missing"]
                f = bucket["filled"]
                cat_data[cat]["missing"] += m
                cat_data[cat]["filled"] += f
                fam_data[fam]["missing"] += m
                fam_data[fam]["filled"] += f

    cat_labels = list(cat_data.keys())
    cat_missing = [cat_data[k]["missing"] for k in cat_labels]
    cat_filled = [cat_data[k]["filled"] for k in cat_labels]

    sorted_fams = sorted(fam_data.items(), key=lambda x: x[1]["missing"], reverse=True)[:10]
    fam_labels = [x[0] for x in sorted_fams]
    fam_missing = [x[1]["missing"] for x in sorted_fams]
    fam_filled = [x[1]["filled"] for x in sorted_fams]

    return cat_labels, cat_missing, cat_filled, fam_labels, fam_missing, fam_filled


def _insights_to_html(insights):
    if not insights:
        return u""
    items = u"".join(u"<li>{}</li>".format(_html_escape(s)) for s in insights)
    return (
        u"<div class='ai-card'>"
        u"<h3>\U0001f916 AI Sanity Check &amp; Next Steps</h3>"
        u"<ul>{}</ul>"
        u"</div>"
    ).format(items)


def _html_escape(s):
    if s is None:
        return u""
    if not isinstance(s, unicode):  # noqa: F821 (IronPython 2.7)
        try:
            s = unicode(s)  # noqa: F821
        except Exception:
            s = str(s).decode("utf-8", "ignore")
    return (s.replace(u"&", u"&amp;")
             .replace(u"<", u"&lt;")
             .replace(u">", u"&gt;"))


def render_and_open_dashboard(audit, parameter_name, insights, filled_now):
    """Write the dashboard HTML to %TEMP% and open it in the default browser.

    Returns the path on success, or None on failure. Never raises — the
    response to Agent A must not be broken if the browser open fails.

    `audit` is the dict produced by _build_audit_report (post-fill audit, so
    counts reflect the freshly-written values).
    """
    try:
        import os
        import io
        import tempfile
        import datetime

        report = audit.get("report", {})
        missing_count = audit.get("missing_count", 0)
        filled_count = audit.get("filled_count", 0)

        cat_labels, cat_missing, cat_filled, fam_labels, fam_missing, fam_filled = \
            _aggregate_chart_data(report)

        html = _DASHBOARD_TEMPLATE
        html = html.replace(u"__TARGET_PARAM__", _html_escape(parameter_name))
        html = html.replace(u"__MISSING_COUNT__", unicode(missing_count))  # noqa: F821
        html = html.replace(u"__FILLED_COUNT__", unicode(filled_count))  # noqa: F821
        html = html.replace(u"__FILLED_NOW__", unicode(filled_now))  # noqa: F821
        html = html.replace(u"__CAT_LABELS__", _safe_json_dumps(cat_labels))
        html = html.replace(u"__CAT_MISSING__", _safe_json_dumps(cat_missing))
        html = html.replace(u"__CAT_FILLED__", _safe_json_dumps(cat_filled))
        html = html.replace(u"__FAM_LABELS__", _safe_json_dumps(fam_labels))
        html = html.replace(u"__FAM_MISSING__", _safe_json_dumps(fam_missing))
        html = html.replace(u"__FAM_FILLED__", _safe_json_dumps(fam_filled))
        html = html.replace(u"__AI_INSIGHTS__", _insights_to_html(insights))
        html = html.replace(u"__TIMESTAMP__", _html_escape(
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

        temp_path = os.path.join(tempfile.gettempdir(), "DataAgentDashboard.html")
        with io.open(temp_path, "w", encoding="utf-8") as f:
            if isinstance(html, bytes):
                html = html.decode("utf-8", "ignore")
            f.write(html)

        try:
            os.startfile(temp_path)
        except Exception:
            # File written but couldn't launch — caller can still find it on disk.
            pass

        return temp_path
    except Exception:
        return None


def _safe_json_dumps(value):
    try:
        return unicode(json.dumps(value, ensure_ascii=False))  # noqa: F821
    except Exception:
        try:
            return unicode(json.dumps(value))  # noqa: F821
        except Exception:
            return u"[]"
