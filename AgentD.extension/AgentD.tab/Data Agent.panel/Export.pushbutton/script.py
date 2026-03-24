# -*- coding: utf-8 -*-
"""Export a selected Revit schedule to CSV.
Prompts the user to select a schedule from the current document
and exports it to a fixed folder as: {rvt_filename}_{schedule_name}.csv
"""

import os
import clr

clr.AddReference('RevitAPI')
clr.AddReference('System.Windows.Forms')

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ViewSchedule,
    ViewScheduleExportOptions
)
from System.Windows.Forms import FolderBrowserDialog, DialogResult

# ── pyRevit context ──────────────────────────────────────────────────────────
from pyrevit import script, forms

doc    = __revit__.ActiveUIDocument.Document
output = script.get_output()

# ── Step 1: Prompt user to select a schedule ─────────────────────────────────
all_schedules = (
    FilteredElementCollector(doc)
    .OfClass(ViewSchedule)
    .ToElements()
)

# Filter out revision schedules (they can't be exported independently)
exportable = [s for s in all_schedules if not s.IsTitleblockRevisionSchedule]

# Sort the schedules alphabetically by name to make them easier to find
exportable = sorted(exportable, key=lambda s: s.Name)

target_schedule = forms.SelectFromList.show(
    exportable,
    name_attr='Name',
    title="Select a Schedule to Export",
    button_name="Select"
)

if target_schedule is None:
    forms.alert("Export cancelled. No schedule was selected.", title="Cancelled")
    script.exit()

output.print_md("✅ Selected schedule: **{}**".format(target_schedule.Name))

# ── Step 2: Get the Revit document filename (without extension) ──────────────
doc_path     = doc.PathName                          # Full path to .rvt file
doc_basename = os.path.basename(doc_path)            # e.g. "2619_ProjectFile.rvt"
doc_filename = os.path.splitext(doc_basename)[0]     # e.g. "2619_ProjectFile"

output.print_md("📄 Document: **{}**".format(doc_filename))

# ── Step 3: Ask user to select an export folder ──────────────────────────────
dialog = FolderBrowserDialog()
dialog.Description    = "Select the folder to export the schedule CSV to"
dialog.SelectedPath   = r"Z:\2619_ChangiT5\Drawings\BIM\BIM Exchanges\BIM_to_Excel\Model Data Check"
dialog.ShowNewFolderButton = True

result = dialog.ShowDialog()

if result != DialogResult.OK:
    forms.alert("Export cancelled — no folder selected.", title="Cancelled")
    script.exit()

export_folder = dialog.SelectedPath
output.print_md("📁 Export folder: **{}**".format(export_folder))

# ── Step 4: Build the output filename ────────────────────────────────────────
# Format: {rvt_filename}_{schedule_name}.csv
csv_filename  = "{0}_{1}.csv".format(doc_filename, target_schedule.Name)
full_csv_path = os.path.join(export_folder, csv_filename)

# Remove existing file if present
if os.path.exists(full_csv_path):
    try:
        os.remove(full_csv_path)
        output.print_md("🗑️ Removed existing file: **{}**".format(csv_filename))
    except OSError as e:
        forms.alert(
            "Could not remove existing file:\n{}\n\nError: {}".format(full_csv_path, str(e)),
            title="File Error",
            warn_icon=True
        )
        script.exit()

# ── Step 5: Export the schedule ───────────────────────────────────────────────
try:
    exp_opt = ViewScheduleExportOptions()
    target_schedule.Export(export_folder, csv_filename, exp_opt)
    output.print_md("---")
    output.print_md("✅ **Export successful!**")
    output.print_md("📄 File saved to: `{}`".format(full_csv_path))
except Exception as e:
    forms.alert(
        "Export failed.\n\nError: {}".format(str(e)),
        title="Export Error",
        warn_icon=True
    )