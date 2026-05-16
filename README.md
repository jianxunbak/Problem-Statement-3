# AgentD pyRevit Extension

This pyRevit extension provides tools under the **Data Agent** panel to automate model data auditing, visualization, and AI-driven data population in Autodesk Revit.

## 1. What the code does
The extension currently consists of two powerful tools designed for model data management:

- **CheckData**: A comprehensive data auditing tool that evaluates the completeness of your model's parameters. It allows you to select a schedule, target a specific parameter, and filter by Category and Family. Once checked, it generates a beautiful, interactive HTML dashboard visualizing missing vs. filled data (using Chart.js) and outputs a detailed list of clickable Element IDs in pyRevit to help you quickly locate missing data in your model.
- **FillData (AI Data Agent)**: An intelligent tool that uses the Anthropic Claude API to predict and fill in missing parameter values. Based on an element's Category, Family, and Type context, the AI will make an educated prediction for the missing parameter and automatically apply it back to the Revit elements in a single, fast transaction.

## 2. How to use it
**A typical workflow:**
1. **Data Auditing (`CheckData`)**: 
   - Click the **CheckData** button.
   - When prompted for a command, type `check the data`.
   - Select a Revit Schedule to define the scope of elements.
   - Choose the target parameter you want to audit.
   - Select the Categories and Families you want to analyze from the multi-select menu.
   - The tool will run and automatically open a graphic HTML dashboard in your web browser, while also providing clickable Element IDs in the pyRevit output window.
   
2. **AI Data Population (`FillData`)**: 
   - Once you know what data is missing, click the **FillData** button.
   - When prompted for a command, type `start the data auditing`.
   - Select the schedule and target parameter.
   - The tool will securely prompt you for your Anthropic API Key (only needed once, saved in pyRevit settings).
   - Claude will evaluate elements missing data and intelligently fill the parameter based on Revit category/family context.

## 3. How to install it
Since this is a pyRevit extension, you can install it by mapping its local path in pyRevit settings:
1. Ensure you have [pyRevit](https://github.com/eirannejad/pyRevit) installed on your machine.
2. Open Revit and go to the **pyRevit** tab -> **Settings** -> **Custom Extension Directories**.
3. Add the parent folder containing the `AgentD.extension` folder.
4. Save settings and reload pyRevit (**pyRevit** tab -> **Reload**).
5. The **AgentD** tab and **Data Agent** panel should now appear in the Revit ribbon.

## 4. Any other guides to help users
- **Anthropic API Key**: The `FillData` AI Data Agent requires an active Anthropic API Key to function. Ensure your account has sufficient credits or access to `claude-haiku-4-5-20251001`. The key is securely saved locally via pyRevit `user_config`.
- **Revit API Transactions**: The `FillData` tool uses Revit API transactions safely. If an error occurs during AI data population, changes are rolled back to prevent model corruption.
- **Dashboard Output**: The `CheckData` tool generates its dashboard as a temporary HTML file and opens it in your default web browser. For security and performance, this is generated entirely locally and no model data is sent to external servers during the check phase.
