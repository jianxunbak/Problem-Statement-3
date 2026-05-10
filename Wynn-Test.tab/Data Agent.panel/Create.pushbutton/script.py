# -*- coding: utf-8 -*-
"""Creates a multi-category schedule for model data checking."""

import clr
clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import (
    Transaction,
    ViewSchedule,
    ElementId,
    ScheduleFieldType,
    BuiltInParameter,
    ScheduleSortGroupField
)

# pyRevit context
from pyrevit import script, forms

doc = __revit__.ActiveUIDocument.Document

def create_multi_category_schedule(doc, name):
    # 1. Create the Multi-Category Schedule
    # ElementId.InvalidElementId denotes a Multi-Category schedule
    new_schedule = ViewSchedule.CreateSchedule(doc, ElementId.InvalidElementId)
    new_schedule.Name = name
    
    definition = new_schedule.Definition
    
    # 2. Define the Fields to add
    # Field names to search for: Category, Family, Type, Count, Description
    target_fields = ["Category", "Family", "Type", "Count", "Description"]
    schedulable_fields = definition.GetSchedulableFields()
    
    fields_added = []
    
    # We'll use a helper to find field by name
    def find_field_in_schedulables(schedulable_fields, name):
        for sf in schedulable_fields:
            if sf.GetName(doc) == name:
                return sf
        return None

    # Adding fields in order
    for f_name in target_fields:
        sf = find_field_in_schedulables(schedulable_fields, f_name)
        if sf:
            definition.AddField(sf)
            fields_added.append(f_name)

    # 3. Sorting / Grouping
    # Sort order: Category -> Family -> Type
    sort_names = ["Category", "Family", "Type"]
    
    # Get the fields from the definition (these are the columns in the schedule)
    all_fields = definition.GetFieldOrder()
    
    for sort_n in sort_names:
        for field_id in all_fields:
            field = definition.GetField(field_id)
            if field.GetName() == sort_n:
                sort_field = ScheduleSortGroupField(field.FieldId)
                definition.AddSortGroupField(sort_field)
                break

    # Uncheck "Itemize every instance"
    definition.IsItemized = False
    
    return new_schedule, fields_added

# Execute in Transaction
t = Transaction(doc, "Create WL_Model Data Check Schedule")
t.Start()

try:
    schedule_name = "WL_Model Data Check"
    
    schedule, added = create_multi_category_schedule(doc, schedule_name)
    
    t.Commit()
    forms.alert("Schedule '{}' created and sorted successfully.".format(schedule_name), 
                title="Success", 
                warn_icon=False)
except Exception as e:
    t.RollBack()
    forms.alert("Failed to create schedule.\n\nError: {}".format(str(e)), 
                title="Error", 
                warn_icon=True)