# Technical Summary: AgentD (pyRevit AI Data Agent)

## 1. System Overview
AgentD is a highly specialized pyRevit extension acting as an AI-powered BIM Data Agent for Autodesk Revit. Its primary function is to automate the auditing, sanity-checking, and intelligent population of missing parameter data within Revit models. 

**Core Capabilities:**
- **Data Auditing (`Check.pushbutton`):** Dynamically scans elements within a selected Revit Schedule, identifying missing vs. filled parameter values across instances and types. It outputs statistics and generates a local, interactive HTML dashboard using Chart.js.
- **AI Data Imputation (`Fill.pushbutton`):** Acts as an intelligent data entry assistant. It queries Anthropic's Claude API to predict missing parameter values based on the element's contextual metadata (Category, Family, and Type) and safely applies these predictions to the Revit model via a single transaction.
- **Unified Workflow (`Start.pushbutton`):** Combines the Check and Fill capabilities into a unified autonomous loop. It audits the data, auto-fills missing parameters using AI, evaluates the updated dataset using a secondary AI prompt (Data Quality Sanity Check), and generates a comprehensive interactive dashboard.

## 2. Tech Stack
- **Environment:** IronPython 2.7.11 (Embedded within pyRevit)
- **Host Application:** Autodesk Revit API (`Autodesk.Revit.DB`) via .NET CLR (`clr.AddReference('RevitAPI')`)
- **Wrapper Framework:** pyRevit (`pyrevit.script`, `pyrevit.forms`, `pyrevit.userconfig`)
- **Network / HTTP Client:** .NET Framework `System.Net.WebRequest` (Used instead of Python's `requests` due to IronPython environment constraints; strictly enforces TLS 1.2 via `ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12`)
- **AI Provider:** Anthropic API (Model: `claude-haiku-4-5-20251001`)
- **Frontend / Visualization:** Vanilla HTML/CSS/JS with Chart.js (CDN) injected into a local temp file.

## 3. Architecture & Project Structure
The project follows the strict folder structure required by pyRevit for UI generation.

```text
c:\Users\water\AppData\Roaming\pyRevit-Master\extensions\AgentD.extension\
├── .git/
├── AgentD.tab/                           # Represents the Ribbon Tab in Revit
│   ├── README.md                         # Project documentation
│   └── Data Agent.panel/                 # Represents the UI Panel inside the Ribbon Tab
│       ├── Check.pushbutton/             # Tool 1: Data Auditing & Visualization
│       │   ├── icon.png                  
│       │   ├── script.py                 # Core logic for checking data and generating HTML
│       │   └── test_dashboard.html       
│       ├── Fill.pushbutton/              # Tool 2: AI Data Auto-Population
│       │   ├── icon.png
│       │   └── script.py                 # Core logic for predicting & writing data via AI
│       └── Start.pushbutton/             # Tool 3: Unified Check + Fill + AI Insights
│           ├── icon.png
│           ├── script.py                 # Master script combining Tool 1 & 2 logic
│           └── test_dashboard.html       
```

**Entry-point files:** 
The `script.py` files within the `.pushbutton` directories are the entry points. They execute synchronously on the Revit main thread when the user clicks the corresponding ribbon button. 

## 4. Data Flow & State Management
Because this operates as a pyRevit script, state is ephemeral per execution.
1. **Trigger & Scope Definition:** Execution begins via a UI trigger. The script uses `pyrevit.forms` to prompt the user for conversational commands (e.g., "check the data"). It then surfaces a UI menu (`forms.SelectFromList`) to select a target `ViewSchedule` and the target Parameter to operate on.
2. **Data Extraction (Read):** The script executes a `FilteredElementCollector(doc, target_schedule.Id)`. It loops through the elements, attempts to retrieve the target parameter on the **Instance**, and falls back to the **Type** (`doc.GetElement(el.GetTypeId())`) if missing.
3. **Evaluation Loop:** Parameters are evaluated for emptiness (`StorageType.String` checked for empty strings; other types checked via `HasValue`). Elements are categorized into nested dictionaries: `report[Category][Family][Type] = {"missing": [], "filled": []}`.
4. **AI Processing (External Outbound):** For elements missing data, context strings are constructed and sent synchronously to the Anthropic API.
5. **Data Imputation (Write):** Predicted values are queued into an `updates_to_make` list. A Revit `Transaction` is started. The queued updates are iterated over, `param.Set(predicted_value)` is called, and the transaction is committed. If an error occurs, `t.RollBack()` is invoked.
6. **Output Generation:** The script uses `pyrevit.script.get_output().print_md()` to print real-time markdown logs. Finally, it constructs a large raw HTML string (replacing placeholders like `%MISSING_COUNT%`), saves it to `tempfile.gettempdir() + "\DataAgentDashboard.html"`, and launches the default system browser via `os.startfile()`.

## 5. APIs & Interfaces (Crucial for Integration)
Currently, AgentD is **tightly coupled to pyRevit UI forms**. To integrate AgentD into a larger Agent A architecture, the interactive UI prompts must be bypassed or abstracted into headless functions. 

### External Communication (Outbound)
The Agent connects to Anthropic via `POST https://api.anthropic.com/v1/messages`.
**Payload Example (Data Imputation):**
```json
{
  "model": "claude-haiku-4-5-20251001",
  "max_tokens": 50,
  "system": "You are an expert BIM data agent...",
  "messages": [
    {
      "role": "user",
      "content": "Predict the value for the parameter 'Fire Rating'.\nCategory: Doors\nFamily: Single-Flush\nType: 36x84"
    }
  ],
  "temperature": 0.0
}
```

### Required Refactoring for Agent A Integration (Inbound Interface Design)
To allow "Agent A" to call AgentD as a sub-routine or microservice, the monolithic `main()` functions must be refactored to accept programmatic arguments.

**Target Signature for Integration:**
```python
def execute_data_agent(doc, schedule_name_or_id, target_parameter_name, api_key):
    """
    Proposed entry point for Agent A.
    Returns JSON payload of results instead of launching an HTML dashboard.
    """
    pass
```
**Expected Output Structure for Agent A:**
Instead of raw HTML, AgentD should be refactored to return a serialized JSON summary to Agent A:
```json
{
  "status": "success",
  "target_parameter": "Fire Rating",
  "statistics": {
    "total_elements": 150,
    "initially_missing": 45,
    "ai_filled_successfully": 42,
    "errors": 3
  },
  "ai_sanity_check_insights": [
    "Task 1: Value 'TBD' found in Category Doors. Flagged for review.",
    "Task 2: Recommend running FillData on Category Windows."
  ]
}
```

## 6. Known Constraints & Dependencies
1. **Environment Variables / Configs:** Relies on an Anthropic API Key securely stored in pyRevit's local `user_config.ini` (`[DataAgent]` section). If absent, it blocks execution and requests it via a UI prompt.
2. **IronPython Limitations:** Because it runs on IronPython 2.7, modern Python 3 libraries (`requests`, `pydantic`, modern `openai`/`anthropic` SDKs) **cannot** be used. All HTTP requests must be constructed manually using `System.Net.WebRequest`.
3. **Revit Thread Context:** Reads and writes to the Revit database **must** happen on the main Revit thread. If Agent A attempts to call this asynchronously or via a background worker, it will result in an `Autodesk.Revit.Exceptions.InvalidOperationException`.
4. **Hardware / Local Files:** Relies on `tempfile.gettempdir()` to write the HTML dashboard. Relies on the user's default browser to view the UI. No external database is used; all data is extracted directly from the active Revit Document memory space (`__revit__.ActiveUIDocument.Document`).
5. **Parameter Editability:** The AI imputation will fail/skip if the target parameter is marked `IsReadOnly` or driven by a Revit formula.
