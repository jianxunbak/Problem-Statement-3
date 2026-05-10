# WL-Test pyRevit Extension

This pyRevit extension provides a suite of tools under the **Data Agent** panel to automate model data checking, parameter setup, data import/export, and AI-driven data auditing in Autodesk Revit.

## 1. What the code does
The extension consists of several scripts designed to work together for comprehensive model data management:
- **Setup Parameters**: Programmatically creates and binds "Uniclass" classification project parameters (Pr.Number, Pr.Description, Ss.Number, Ss.Description) to selected Revit categories using a temporary shared parameters file.
- **Create**: Automatically generates a "WL_Model Data Check" multi-category schedule in Revit, organized by Category, Family, and Type to help audit basic model data.
- **Add**: Similar to "Create", but generates a schedule that additionally includes the Uniclass classification parameters set up previously.
- **Export (AI Data Agent)**: An intelligent tool that uses the Anthropic Claude API to predict and fill in missing parameter values based on an element's Category, Family, and Type. It uses an existing schedule as its scope to efficiently audit and update the model.
- **Import Parameters**: Allows you to bulk-update Revit element parameters by importing a CSV file (must contain an 'Id' or 'Element ID' column).

## 2. How to use it
**A typical workflow:**
1. **Initialize Parameters**: Start with the **Setup Parameters** tool to inject the required Uniclass parameters into your target categories.
2. **Generate Audit Schedules**: Use the **Create** or **Add** tools to quickly build schedules that display your model's current data completeness.
3. **AI Data Auditing**: Run the **Export (AI Data Agent)** tool. 
   - You will be prompted to enter your Anthropic API Key (this is only needed once and is saved securely in your pyRevit settings).
   - When prompted for a command, type `start the data auditing`.
   - Select a schedule to define the scope of elements to audit.
   - Choose the target parameter you want the AI to predict and fill.
   - The tool will query Claude for missing values and automatically apply the predictions to the elements in your model.
4. **Bulk Importing**: If you have externally modified data in a CSV file, use the **Import Parameters** tool to select the CSV and push those values back into the Revit elements.

## 3. How to install it
Since this is a pyRevit extension, you can install it by mapping its local path in pyRevit settings:
1. Ensure you have [pyRevit](https://github.com/eirannejad/pyRevit) installed on your machine.
2. Open Revit and go to the **pyRevit** tab -> **Settings** -> **Custom Extension Directories**.
3. Add the parent folder containing the `WL-test.extension` folder (e.g., `c:\Users\water\AppData\Roaming\pyRevit-Master\extensions\`).
4. Save settings and reload pyRevit (**pyRevit** tab -> **Reload**).
5. The **Wynn-Test** tab and **Data Agent** panel should now appear in the Revit ribbon.

## 4. Any other guides to help users
- **Anthropic API Key**: The AI Data Agent requires an active Anthropic API Key to function. Ensure your account has sufficient credits or access to `claude-haiku-4-5-20251001`. The key is securely saved locally via pyRevit `user_config`.
- **Importing CSV Data**: When using the **Import Parameters** tool, make sure your CSV contains an `Id` or `Element ID` column. The tool will use this to match rows to Revit elements and update the respective columns.
- **Revit API Transactions**: All tools use Revit API transactions safely. If an error occurs during parameter creation or AI data population, changes are rolled back to prevent model corruption.
- **Button Naming**: Please note that the AI Data Agent is currently located under the **Export** pushbutton, which may have been repurposed from a previous schedule exporting tool.
