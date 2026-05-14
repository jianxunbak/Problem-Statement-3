# AgentD pyRevit Extension

This pyRevit extension provides tools under the **Data Agent** panel to automate model data auditing, visualization, and AI-driven data population in Autodesk Revit.

## 1. What the code does
The extension consists of three powerful tools designed for intelligent model data management:

- **Start (Autonomous Agent)**: The flagship tool that combines auditing and population. It first identifies missing parameter data within a selected scope and then automatically leverages the Anthropic Claude API to predict and fill those values in a single transaction. It concludes by generating an interactive HTML dashboard with AI-driven quality sanity checks.
- **Check**: A dedicated auditing tool. It evaluates parameter completeness and generates a beautiful, interactive HTML dashboard visualizing missing vs. filled data. It also provides clickable Element IDs in the pyRevit output for quick manual navigation.
- **Fill (AI Data Agent)**: A focused data population tool. It uses the Anthropic Claude API to intelligently predict missing parameter values based on Category, Family, and Type context, automatically applying them to the model.

## 2. How to use it
**Workflows:**

### Option A: Fully Autonomous (`Start`)
1. Click the **Start** button.
2. Type `check the data` when prompted.
3. Select a Revit Schedule to define the element scope.
4. Choose the target parameter to audit and fill.
5. Select specific Categories and Families to process.
6. The agent will check the data, predict missing values via AI, update the model, and open a comprehensive HTML dashboard with BIM Manager insights.

### Option B: Manual Audit & Review (`Check`)
1. Click the **Check** button.
2. Type `check the data` and follow the selection prompts.
3. Use the generated HTML dashboard and pyRevit Element ID links to review model completeness without making changes.

### Option C: AI Data Population (`Fill`)
1. Click the **Fill** button.
2. Type `start the data auditing` (this triggers the filling logic).
3. Select the scope and parameter.
4. The AI will securely use your API key to fill missing values and report its actions in the output window.

## 3. How to install it
Since this is a pyRevit extension, you can install it by mapping its local path in pyRevit settings:
1. Ensure you have [pyRevit](https://github.com/eirannejad/pyRevit) installed on your machine.
2. Open Revit and go to the **pyRevit** tab -> **Settings** -> **Custom Extension Directories**.
3. Add the parent folder containing the `AgentD.extension` folder.
4. Save settings and reload pyRevit (**pyRevit** tab -> **Reload**).
5. The **AgentD** tab and **Data Agent** panel should now appear in the Revit ribbon.

## 4. Configuration & Security
- **Anthropic API Key**: The `Start` and `Fill` tools require an active Anthropic API Key. You will be prompted to enter it once; it is then securely saved locally via pyRevit `user_config`.
- **AI Model**: Currently optimized for `claude-haiku-4-5-20251001` for a balance of speed and intelligence.
- **Transactions**: All model updates are handled within Revit API transactions. If an error occurs, changes are rolled back to maintain model integrity.
- **Privacy**: No model geometry or sensitive data is sent to external servers. Only the Category, Family, and Type names of elements missing data are sent to the Anthropic API for value prediction.
