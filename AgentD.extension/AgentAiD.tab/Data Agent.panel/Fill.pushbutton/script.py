# -*- coding: utf-8 -*-
"""AI Data Agent: Audits and fills missing parameter values using Anthropic Claude."""

import os
import json
import clr

clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ViewSchedule,
    Transaction,
    StorageType
)

from pyrevit import script, forms
from pyrevit.userconfig import user_config

import System
clr.AddReference('System')
from System.Net import WebRequest, ServicePointManager, SecurityProtocolType
from System.IO import StreamReader

output = script.get_output()

def get_api_key():
    try:
        config = user_config.get_section("DataAgent")
    except Exception:
        config = user_config.add_section("DataAgent")
        
    api_key = getattr(config, "anthropic_api_key", None)
    if not api_key:
        api_key = forms.ask_for_string(
            prompt="Enter your Anthropic (Claude) API Key:\n(It will be saved securely in your pyRevit settings)",
            title="API Key Required"
        )
        if api_key:
            setattr(config, "anthropic_api_key", api_key)
            user_config.save_changes()
    return api_key

def call_claude(api_key, target_param_name, category, family, type_name):
    # Ensure TLS 1.2 is enabled for modern API requests in IronPython
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
        "model": "claude-haiku-4-5-20251001", # Fast and efficient for this task
        "max_tokens": 50,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.0 # Keep it deterministic
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

def main():
    doc = __revit__.ActiveUIDocument.Document
    
    # 1. Ask the user for their command
    user_command = forms.ask_for_string(
        default="start the data auditing",
        prompt="Enter Agent Command:",
        title="Data Agent Chat"
    )
    
    if not user_command:
        script.exit()
        
    if "audit" not in user_command.lower():
        forms.alert("I am currently configured to handle data audits. Please type 'start the data auditing' to begin.", title="Agent Response")
        script.exit()

    # 2. Ensure we have the API key
    api_key = get_api_key()
    if not api_key:
        forms.alert("API Key is required to run the agent. You can run the tool again to enter it.", title="Missing Configuration")
        script.exit()

    # 3. Select Schedule for scope
    all_schedules = [s for s in FilteredElementCollector(doc).OfClass(ViewSchedule).ToElements() if not s.IsTitleblockRevisionSchedule]
    all_schedules.sort(key=lambda s: s.Name)
    
    target_schedule = forms.SelectFromList.show(
        all_schedules,
        name_attr='Name',
        title="Select Schedule to act as the audit scope",
        button_name="Select"
    )
    
    if not target_schedule:
        script.exit()
        
    output.print_md("## 🤖 Data Agent Audit")
    output.print_md("**Scope:** Schedule `{}`".format(target_schedule.Name))

    # 4. Get fields from the schedule to choose which parameter to audit
    definition = target_schedule.Definition
    field_dict = {}
    for i in range(definition.GetFieldCount()):
        field = definition.GetField(i)
        heading = field.ColumnHeading
        if heading not in field_dict:
            field_dict[heading] = field.ParameterId
            
    target_param_name = forms.SelectFromList.show(
        sorted(field_dict.keys()),
        title="Which parameter should the Agent predict and fill?",
        button_name="Audit this Parameter"
    )
    
    if not target_param_name:
        script.exit()
        
    target_param_id = field_dict[target_param_name]
    output.print_md("**Target Parameter:** `{}`".format(target_param_name))
    output.print_md("---")

    # 5. Gather elements from the schedule
    elements = FilteredElementCollector(doc, target_schedule.Id).ToElements()
    
    # Phase 1: Collect predictions (No transaction open yet!)
    updates_to_make = []
    errors = 0
    skipped = 0

    with forms.ProgressBar(title="Agent is predicting missing data...", step=len(elements)) as pb:
        for idx, el in enumerate(elements):
            param = None
            for p in el.Parameters:
                if p.Id.Equals(target_param_id):
                    param = p
                    break
            
            # If not found on instance, check if it's a Type parameter
            if not param:
                try:
                    el_type = doc.GetElement(el.GetTypeId())
                    if el_type:
                        for p in el_type.Parameters:
                            if p.Id.Equals(target_param_id):
                                param = p
                                break
                except:
                    pass
            
            # Check if param exists, is writable, and is a string type
            if param:
                if not param.IsReadOnly:
                    if param.StorageType == StorageType.String:
                        current_val = param.AsString()
                        
                        if not current_val or current_val.strip() == "":
                            # Gather context for the LLM
                            category_name = el.Category.Name if el.Category else "Unknown Category"
                            family_name = "Unknown Family"
                            type_name = "Unknown Type"
                            
                            try:
                                el_type = doc.GetElement(el.GetTypeId())
                                if el_type:
                                    type_name = getattr(el_type, "Name", "Unknown Type")
                                    family_name = getattr(el_type, "FamilyName", "Unknown Family")
                            except:
                                pass
                                
                            # Query Claude
                            predicted_value = call_claude(api_key, target_param_name, category_name, family_name, type_name)
                            
                            if predicted_value and not predicted_value.startswith("ERROR") and predicted_value != "UNKNOWN":
                                updates_to_make.append((el.Id, predicted_value, category_name, family_name, type_name))
                            else:
                                output.print_md("⚠️ **Skipped Element `{}`** - Agent uncertain (Returned: `{}`)".format(el.Id, predicted_value))
                                errors += 1
                        else:
                            output.print_md("🔍 **Skipped Element `{}`**: Already has value `{}`".format(el.Id, current_val))
                            skipped += 1
                    else:
                        output.print_md("ℹ️ **Skipped Element `{}`**: Parameter is not text type (It is `{}`).".format(el.Id, param.StorageType))
                        skipped += 1
                else:
                    output.print_md("🔒 **Skipped Element `{}`**: Parameter is Read-Only.".format(el.Id))
                    skipped += 1
            else:
                output.print_md("❌ **Skipped Element `{}`**: Parameter not found on element! (Searched for ID: {})".format(el.Id, target_param_id.ToString()))
                skipped += 1
                
            pb.update_progress(idx + 1, len(elements))
            
    # Phase 2: Apply all updates in a single, extremely fast Transaction
    if updates_to_make:
        t = Transaction(doc, "Agent Data Audit: " + target_param_name)
        t.Start()
        try:
            for el_id, predicted_value, cat, fam, typ in updates_to_make:
                el = doc.GetElement(el_id)
                param = None
                for p in el.Parameters:
                    if p.Id.Equals(target_param_id):
                        param = p
                        break
                        
                if not param:
                    try:
                        el_type = doc.GetElement(el.GetTypeId())
                        if el_type:
                            for p in el_type.Parameters:
                                if p.Id.Equals(target_param_id):
                                    param = p
                                    break
                    except:
                        pass
                        
                if param:
                    param.Set(predicted_value)
                output.print_md("✅ **Updated Element `{}`** | Context: `{} | {} | {}` ➔ Predicted: **{}**".format(
                    el.Id, cat, fam, typ, predicted_value
                ))
            t.Commit()
        except Exception as e:
            t.RollBack()
            forms.alert("Error applying values to the model:\n\n{}".format(str(e)))
            script.exit()

    output.print_md("---")
    output.print_md("🎉 **Audit Complete!**")
    output.print_md("- ✏️ **Successfully filled:** {} elements".format(len(updates_to_make)))
    output.print_md("- ⏭️ **Skipped (already filled or read-only):** {}".format(skipped))
    if errors > 0:
        output.print_md("- ❌ **Agent uncertain/errors:** {}".format(errors))

if __name__ == '__main__':
    main()