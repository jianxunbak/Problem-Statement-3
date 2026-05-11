# -*- coding: utf-8 -*-
"""Data Check Agent: Checks if parameter values are filled or missing."""

import os
import clr
import json
import tempfile

clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ViewSchedule,
    StorageType,
    Transaction
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
            prompt="Enter your Anthropic (Claude) API Key for AI Insights:\n(Optional: Cancel to run without AI)",
            title="API Key Setup"
        )
        if api_key:
            setattr(config, "anthropic_api_key", api_key)
            user_config.save_changes()
    return api_key

def call_claude_insights(api_key, system_prompt, user_prompt):
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
    except System.Net.WebException as e:
        err_text = ""
        if e.Response:
            try:
                reader = StreamReader(e.Response.GetResponseStream())
                err_text = reader.ReadToEnd()
            except: pass
        return "<div class='ai-card'><h3>⚠️ AI Error</h3><p>.NET Web Error: " + str(e) + "</p><p>Details: " + err_text + "</p></div>"
    except BaseException as e:
        return "<div class='ai-card'><h3>⚠️ AI Error</h3><p>Python Error: " + str(e) + "</p></div>"
    except System.Exception as e:
        return "<div class='ai-card'><h3>⚠️ AI Error</h3><p>.NET Error: " + str(e) + "</p></div>"
    except:
        import traceback
        return "<div class='ai-card'><h3>⚠️ AI Error</h3><pre>" + traceback.format_exc() + "</pre></div>"

def predict_missing_value(api_key, target_param_name, category, family, type_name):
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

def main():
    doc = __revit__.ActiveUIDocument.Document
    
    api_key = get_api_key()
    
    # 1. Ask the user for their command
    user_command = forms.ask_for_string(
        default="check the data",
        prompt="Enter Agent Command:",
        title="Data Agent Chat"
    )
    
    if not user_command:
        script.exit()
        
    if "check" not in user_command.lower():
        forms.alert("I am currently configured to handle data checking. Please type 'check the data' to begin.", title="Agent Response")
        script.exit()

    # 2. Select Schedule for scope
    all_schedules = [s for s in FilteredElementCollector(doc).OfClass(ViewSchedule).ToElements() if not s.IsTitleblockRevisionSchedule]
    all_schedules.sort(key=lambda s: s.Name)
    
    target_schedule = forms.SelectFromList.show(
        all_schedules,
        name_attr='Name',
        title="Select Schedule to act as the check scope",
        button_name="Select"
    )
    
    if not target_schedule:
        script.exit()
        
    output.print_md("## 🤖 Data Agent Check")
    output.print_md("**Scope:** Schedule `{}`".format(target_schedule.Name))

    # 3. Get fields from the schedule to choose which parameter to check
    definition = target_schedule.Definition
    field_dict = {}
    for i in range(definition.GetFieldCount()):
        field = definition.GetField(i)
        heading = field.ColumnHeading
        if heading not in field_dict:
            field_dict[heading] = field.ParameterId
            
    target_param_name = forms.SelectFromList.show(
        sorted(field_dict.keys()),
        title="Which parameter should the Agent check?",
        button_name="Check this Parameter"
    )
    
    if not target_param_name:
        script.exit()
        
    target_param_id = field_dict[target_param_name]
    output.print_md("**Target Parameter:** `{}`".format(target_param_name))
    output.print_md("---")

    # 4. Gather elements from the schedule
    all_elements = FilteredElementCollector(doc, target_schedule.Id).ToElements()
    
    # 5. Extract unique Category and Family combinations
    cat_fam_options = set()
    for el in all_elements:
        category_name = el.Category.Name if getattr(el, "Category", None) else "Unknown Category"
        family_name = "Unknown Family"
        try:
            el_type = doc.GetElement(el.GetTypeId())
            if el_type:
                family_name = getattr(el_type, "FamilyName", "Unknown Family")
        except:
            pass
        cat_fam_options.add("{} - {}".format(category_name, family_name))
        
    if not cat_fam_options:
        forms.alert("No elements found in the selected schedule.", title="Empty Schedule")
        script.exit()
        
    selected_cat_fams = forms.SelectFromList.show(
        sorted(list(cat_fam_options)),
        title="Select Categories & Families to check",
        multiselect=True,
        button_name="Start Checking"
    )
    
    if not selected_cat_fams:
        script.exit()
        
    selected_set = set(selected_cat_fams)
    
    missing_count = 0
    filled_count = 0
    skipped = 0
    
    report = {}
    cat_unique_filled = {}

    with forms.ProgressBar(title="Agent is checking data...", step=len(all_elements)) as pb:
        for idx, el in enumerate(all_elements):
            if idx % 20 == 0 or idx == len(all_elements) - 1:
                pb.update_progress(idx + 1, len(all_elements))
                
            # Gather context
            category_name = el.Category.Name if getattr(el, "Category", None) else "Unknown Category"
            family_name = "Unknown Family"
            type_name = "Unknown Type"
            
            try:
                el_type = doc.GetElement(el.GetTypeId())
                if el_type:
                    type_name = getattr(el_type, "Name", "Unknown Type")
                    family_name = getattr(el_type, "FamilyName", "Unknown Family")
            except:
                pass
                
            # Filter by user selection
            cat_fam_key = "{} - {}".format(category_name, family_name)
            if cat_fam_key not in selected_set:
                continue

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
            
            # Check if param exists
            if param:
                # Determine if parameter is "empty"
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
                
                if category_name not in report: report[category_name] = {}
                if family_name not in report[category_name]: report[category_name][family_name] = {}
                if type_name not in report[category_name][family_name]: 
                    report[category_name][family_name][type_name] = {"missing": [], "filled": []}

                if is_empty:
                    report[category_name][family_name][type_name]["missing"].append(el.Id)
                    missing_count += 1
                else:
                    report[category_name][family_name][type_name]["filled"].append((el.Id, current_val))
                    filled_count += 1
                    
                    if category_name not in cat_unique_filled:
                        cat_unique_filled[category_name] = set()
                    if len(cat_unique_filled[category_name]) < 20:
                        cat_unique_filled[category_name].add(current_val)
            else:
                skipped += 1
            
    # Print report
    for cat in sorted(report.keys()):
        output.print_md("### 📂 Category: {}".format(cat))
        for fam in sorted(report[cat].keys()):
            output.print_md("#### 🏷️ Family: {}".format(fam))
            for typ in sorted(report[cat][fam].keys()):
                missing_list = report[cat][fam][typ]["missing"]
                filled_list = report[cat][fam][typ]["filled"]
                
                output.print_md("- **Type:** `{}`".format(typ))
                if missing_list:
                    max_ids_to_show = 20
                    display_ids = missing_list[:max_ids_to_show]
                    ids_str = ", ".join([output.linkify(eid) for eid in display_ids])
                    if len(missing_list) > max_ids_to_show:
                        ids_str += ", ... (+ {} more)".format(len(missing_list) - max_ids_to_show)
                        
                    output.print_md("  - ❌ **Missing:** {} elements (IDs: {})".format(len(missing_list), ids_str))
                if filled_list:
                    output.print_md("  - ✅ **Filled:** {} elements".format(len(filled_list)))

    output.print_md("---")

    if missing_count > 0:
        output.print_md("⚠️ **Missing data detected! Proceeding to FillData automatically...**")
        
        updates_to_make = []
        errors = 0
        
        missing_elements = []
        for cat in report:
            for fam in report[cat]:
                for typ in report[cat][fam]:
                    for el_id in report[cat][fam][typ]["missing"]:
                        missing_elements.append((el_id, cat, fam, typ))
                        
        with forms.ProgressBar(title="Agent is predicting missing data...", step=len(missing_elements)) as pb:
            for idx, (el_id, cat, fam, typ) in enumerate(missing_elements):
                predicted_value = predict_missing_value(api_key, target_param_name, cat, fam, typ)
                if predicted_value and not predicted_value.startswith("ERROR") and predicted_value != "UNKNOWN":
                    updates_to_make.append((el_id, predicted_value, cat, fam, typ))
                else:
                    output.print_md("⚠️ **Skipped Element `{}`** - Agent uncertain (Returned: `{}`)".format(el_id, predicted_value))
                    errors += 1
                pb.update_progress(idx + 1, len(missing_elements))
                
        if updates_to_make:
            t = Transaction(doc, "Agent Data Auto-Fill: " + target_param_name)
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
                    if param and not param.IsReadOnly:
                        param.Set(predicted_value)
                        
                        report[cat][fam][typ]["missing"].remove(el_id)
                        report[cat][fam][typ]["filled"].append((el_id, predicted_value))
                        missing_count -= 1
                        filled_count += 1
                        
                        if cat not in cat_unique_filled:
                            cat_unique_filled[cat] = set()
                        if len(cat_unique_filled[cat]) < 20:
                            cat_unique_filled[cat].add(predicted_value)
                            
                    output.print_md("✅ **Auto-Filled Element `{}`** | Context: `{} | {} | {}` ➔ Predicted: **{}**".format(
                        el.Id, cat, fam, typ, predicted_value
                    ))
                t.Commit()
            except Exception as e:
                t.RollBack()
                output.print_md("❌ **Error during auto-fill transaction:** " + str(e))
                
        output.print_md("---")
    output.print_md("🎉 **Check Complete! Generating Graphic Dashboard...**")
    output.print_md("- ❌ **Total Missing data:** {} elements".format(missing_count))
    output.print_md("- ✅ **Total Filled data:** {} elements".format(filled_count))
    if skipped > 0:
        output.print_md("- ⏭️ **Skipped (Parameter not found):** {}".format(skipped))

    # Generate HTML Dashboard
    cat_data = {}
    fam_data = {}
    
    for cat, fams in report.items():
        if cat not in cat_data:
            cat_data[cat] = {"missing": 0, "filled": 0}
        for fam, types in fams.items():
            if fam not in fam_data:
                fam_data[fam] = {"missing": 0, "filled": 0}
            for typ, data in types.items():
                m_count = len(data["missing"])
                f_count = len(data["filled"])
                cat_data[cat]["missing"] += m_count
                cat_data[cat]["filled"] += f_count
                fam_data[fam]["missing"] += m_count
                fam_data[fam]["filled"] += f_count

    cat_labels = list(cat_data.keys())
    cat_missing_arr = [cat_data[k]["missing"] for k in cat_labels]
    cat_filled_arr = [cat_data[k]["filled"] for k in cat_labels]
    
    sorted_fams = sorted(fam_data.items(), key=lambda x: x[1]["missing"], reverse=True)[:10]
    fam_labels = [x[0] for x in sorted_fams]
    fam_missing_arr = [x[1]["missing"] for x in sorted_fams]
    fam_filled_arr = [x[1]["filled"] for x in sorted_fams]

    if api_key:
        output.print_md("---")
        output.print_md("🤖 **AI is analyzing the results...**")
        
        try:
            system_prompt = (
                u"You are an expert BIM Manager AI. Analyze the Revit data audit results and provide "
                u"actionable insights. Output purely in HTML. Do not use markdown code blocks. "
                u"Do NOT output a full HTML document (no <!DOCTYPE html>, no <html>, <head>, or <body> tags). "
                u"Only output the inner HTML elements. "
                u"Use <div class='ai-card'> for sections, <h3> for titles, and <ul>/<li> for lists. "
                u"Style the HTML to be clean and readable without any surrounding markdown."
            )
            
            user_prompt = u"Audit Results for Parameter: '{}'\n\n".format(target_param_name)
            user_prompt += u"MISSING DATA COUNTS:\n"
            for i in range(len(cat_labels)):
                user_prompt += u"- {}: {} missing, {} filled\n".format(cat_labels[i], cat_missing_arr[i], cat_filled_arr[i])
                
            user_prompt += u"\nUNIQUE FILLED VALUES SAMPLED:\n"
            for cat, vals in cat_unique_filled.items():
                if vals:
                    # Convert vals to a list of unicode strings safely
                    safe_vals = [unicode(v) for v in vals]
                    user_prompt += u"- {}: {}\n".format(cat, safe_vals)
                    
            user_prompt += (
                u"\nTask 1 (Data Quality Sanity Check): Identify any filled values that look like errors, typos, or generic placeholders (e.g., 'TBD', 'N/A'). "
                u"List them and explain why they are problematic. If none, briefly state data quality looks standard.\n"
                u"Task 2 (Next Steps): Provide 3 actionable next steps. Step 1 MUST be a recommendation to run the 'FillData' tool "
                u"on the category missing the most data."
            )
            
            ai_insights_html = call_claude_insights(api_key, system_prompt, user_prompt)
            ai_insights_html = ai_insights_html.replace("```html", "").replace("```", "").strip()
        except Exception as e:
            import traceback
            ai_insights_html = u"<div class='ai-card'><h3>⚠️ Prompt Generation Error</h3><pre>" + traceback.format_exc() + u"</pre></div>"
    else:
        ai_insights_html = u"<div class='ai-card'><h3>🤖 AI Insights Disabled</h3><p>Provide an Anthropic API Key to enable Sanity Checks and Action Generation.</p></div>"

    html_content = """
    <!DOCTYPE html>
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
                margin: 0;
                padding: 2rem;
                font-family: 'Inter', sans-serif;
                background-color: var(--bg-color);
                color: var(--text-main);
                background-image: radial-gradient(circle at top right, #1e1b4b, #0f172a);
                min-height: 100vh;
            }
            .header {
                text-align: center;
                margin-bottom: 2rem;
            }
            .header h1 {
                font-weight: 800;
                font-size: 2.5rem;
                margin: 0;
                background: linear-gradient(to right, var(--accent), #818cf8);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }
            .stats-container {
                display: flex;
                gap: 1.5rem;
                justify-content: center;
                margin-bottom: 2rem;
                flex-wrap: wrap;
            }
            .stat-card {
                background: var(--card-bg);
                backdrop-filter: blur(10px);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 1rem;
                padding: 1.5rem 2.5rem;
                text-align: center;
                min-width: 150px;
                box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.5);
                transition: transform 0.2s;
            }
            .stat-card:hover {
                transform: translateY(-5px);
            }
            .stat-card h3 {
                margin: 0 0 0.5rem 0;
                color: var(--text-muted);
                font-weight: 600;
                font-size: 1rem;
            }
            .stat-card .value {
                font-size: 2.5rem;
                font-weight: 800;
                margin: 0;
            }
            .stat-card.missing .value { color: var(--danger); }
            .stat-card.filled .value { color: var(--success); }
            
            .ai-card {
                background: rgba(56, 189, 248, 0.1);
                border: 1px solid rgba(56, 189, 248, 0.3);
                border-radius: 1rem;
                padding: 1.5rem 2.5rem;
                margin-bottom: 2rem;
                box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.5);
                max-width: 1200px;
                margin-left: auto;
                margin-right: auto;
            }
            .ai-card h3 {
                color: var(--accent);
                margin-top: 0;
            }
            .ai-card ul {
                margin: 0 0 1rem 0;
                padding-left: 1.5rem;
            }
            .ai-card li {
                margin-bottom: 0.5rem;
            }
            
            .charts-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
                gap: 2rem;
                max-width: 1200px;
                margin: 0 auto;
            }
            .chart-wrapper {
                background: var(--card-bg);
                backdrop-filter: blur(10px);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 1rem;
                padding: 1.5rem;
                box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.5);
            }
            canvas {
                width: 100% !important;
                height: 300px !important;
            }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Data Audit Dashboard</h1>
            <p style="color: var(--text-muted)">Analysis of Target Parameter: <strong>%TARGET_PARAM%</strong></p>
        </div>
        
        <div class="stats-container">
            <div class="stat-card missing">
                <h3>Missing Data</h3>
                <p class="value">%MISSING_COUNT%</p>
            </div>
            <div class="stat-card filled">
                <h3>Filled Data</h3>
                <p class="value">%FILLED_COUNT%</p>
            </div>
        </div>

        %AI_INSIGHTS%

        <div class="charts-grid">
            <div class="chart-wrapper">
                <canvas id="overviewChart"></canvas>
            </div>
            <div class="chart-wrapper">
                <canvas id="categoryChart"></canvas>
            </div>
            <div class="chart-wrapper" style="grid-column: 1 / -1;">
                <canvas id="familyChart"></canvas>
            </div>
        </div>

        <script>
            Chart.defaults.color = '#94a3b8';
            Chart.defaults.font.family = 'Inter';
            
            new Chart(document.getElementById('overviewChart'), {
                type: 'doughnut',
                data: {
                    labels: ['Missing', 'Filled'],
                    datasets: [{
                        data: [%MISSING_COUNT%, %FILLED_COUNT%],
                        backgroundColor: ['#f43f5e', '#10b981'],
                        borderWidth: 0,
                        hoverOffset: 10
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { position: 'bottom' },
                        title: { display: true, text: 'Overall Completion', color: '#f8fafc', font: {size: 16} }
                    },
                    cutout: '70%'
                }
            });

            new Chart(document.getElementById('categoryChart'), {
                type: 'bar',
                data: {
                    labels: %CAT_LABELS%,
                    datasets: [
                        { label: 'Missing', data: %CAT_MISSING%, backgroundColor: '#f43f5e', borderRadius: 4 },
                        { label: 'Filled', data: %CAT_FILLED%, backgroundColor: '#10b981', borderRadius: 4 }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        title: { display: true, text: 'Data by Category', color: '#f8fafc', font: {size: 16} }
                    },
                    scales: {
                        x: { stacked: true, grid: {color: 'rgba(255,255,255,0.05)'} },
                        y: { stacked: true, grid: {color: 'rgba(255,255,255,0.05)'} }
                    }
                }
            });

            new Chart(document.getElementById('familyChart'), {
                type: 'bar',
                data: {
                    labels: %FAM_LABELS%,
                    datasets: [
                        { label: 'Missing', data: %FAM_MISSING%, backgroundColor: '#f43f5e', borderRadius: 4 },
                        { label: 'Filled', data: %FAM_FILLED%, backgroundColor: '#10b981', borderRadius: 4 }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        title: { display: true, text: 'Top Families (Most Missing Data)', color: '#f8fafc', font: {size: 16} }
                    },
                    scales: {
                        x: { stacked: true, grid: {color: 'rgba(255,255,255,0.05)'} },
                        y: { stacked: true, grid: {color: 'rgba(255,255,255,0.05)'} }
                    }
                }
            });
        </script>
    </body>
    </html>
    """

    html_content = html_content.replace("%TARGET_PARAM%", target_param_name)
    html_content = html_content.replace("%MISSING_COUNT%", str(missing_count))
    html_content = html_content.replace("%AI_INSIGHTS%", ai_insights_html)
    html_content = html_content.replace("%FILLED_COUNT%", str(filled_count))
    html_content = html_content.replace("%CAT_LABELS%", json.dumps(cat_labels))
    html_content = html_content.replace("%CAT_MISSING%", json.dumps(cat_missing_arr))
    html_content = html_content.replace("%CAT_FILLED%", json.dumps(cat_filled_arr))
    html_content = html_content.replace("%FAM_LABELS%", json.dumps(fam_labels))
    html_content = html_content.replace("%FAM_MISSING%", json.dumps(fam_missing_arr))
    html_content = html_content.replace("%FAM_FILLED%", json.dumps(fam_filled_arr))

    import io
    temp_path = os.path.join(tempfile.gettempdir(), "DataAgentDashboard.html")
    with io.open(temp_path, "w", encoding="utf-8") as f:
        if type(html_content) is not type(u""):
            f.write(html_content.decode("utf-8", "ignore"))
        else:
            f.write(html_content)
        
    os.startfile(temp_path)

if __name__ == '__main__':
    main()