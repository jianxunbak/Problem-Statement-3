# -*- coding: utf-8 -*-
"""Headless Agent D operations for the HTTP bridge.

These functions are called from script.py on the Revit main thread (marshalled
via ExternalEvent). They take a Revit `doc` plus primitive arguments and return
JSON-serializable dicts. No pyrevit.forms UI prompts, no os.startfile, no HTML
output. Errors are returned as structured dicts.
"""

import json
import clr

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
