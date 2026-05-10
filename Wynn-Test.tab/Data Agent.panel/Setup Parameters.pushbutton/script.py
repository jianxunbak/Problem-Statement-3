# -*- coding: utf-8 -*-
"""Creates Uniclass classification project parameters for all categories."""

import os
import tempfile
import clr
clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import (
    Transaction, 
    CategorySet, 
    InstanceBinding, 
    GroupTypeId, 
    ExternalDefinitionCreationOptions,
    SpecTypeId
)
from pyrevit import revit, forms

doc = revit.doc
app = revit.doc.Application

# 1. Configuration
PARAM_NAMES = [
    "Classification.Uniclass.Pr.Number",
    "Classification.Uniclass.Pr.Description",
    "Classification.Uniclass.Ss.Number",
    "Classification.Uniclass.Ss.Description"
]
PARAM_GROUP = GroupTypeId.Data  # Modern "Data" group ID

def get_all_valid_categories():
    """Returns a sorted list of all categories that allow bound parameters."""
    categories = []
    for category in doc.Settings.Categories:
        if category.AllowsBoundParameters:
            categories.append(category)
    return sorted(categories, key=lambda x: x.Name)

def create_project_parameters(selected_categories):
    # Store original shared parameter file path to restore it later
    original_shared_file = app.SharedParametersFilename
    
    # Create a temporary shared parameter file
    temp_path = os.path.join(tempfile.gettempdir(), "pyrevit_temp_params.txt")
    if not os.path.exists(temp_path):
        with open(temp_path, "w") as f:
            pass
            
    app.SharedParametersFilename = temp_path
    
    try:
        shared_file = app.OpenSharedParameterFile()
        # Create or get the group in the shared file
        group = shared_file.Groups.get_Item("Classification") or shared_file.Groups.Create("Classification")
        
        cat_set = CategorySet()
        for c in selected_categories:
            cat_set.Insert(c)
        binding = app.Create.NewInstanceBinding(cat_set)
        
        with Transaction(doc, "Add Uniclass Parameters") as t:
            t.Start()
            
            added_count = 0
            for name in PARAM_NAMES:
                # 1. Create the definition in the temp shared file
                definition = group.Definitions.get_Item(name)
                if not definition:
                    # SpecTypeId.String.Text is the modern way to define Text parameter type
                    opt = ExternalDefinitionCreationOptions(name, SpecTypeId.String.Text)
                    definition = group.Definitions.Create(opt)
                
                # 2. Bind the definition to the project
                # We use Insert to add new ones. It won't overwrite existing ones.
                if doc.ParameterBindings.Insert(definition, binding, PARAM_GROUP):
                    added_count += 1
                else:
                    # If Insert fails, it might already exist. We could try ReInsert if needed.
                    doc.ParameterBindings.ReInsert(definition, binding, PARAM_GROUP)
                    added_count += 1
                    
            t.Commit()
            return added_count
            
    finally:
        # Restore original shared parameter file path
        app.SharedParametersFilename = original_shared_file
        # Clean up temp file
        try:
            os.remove(temp_path)
        except:
            pass

# Execute
if __name__ == "__main__":
    valid_categories = get_all_valid_categories()
    
    # Prompt the user to select categories
    selected_categories = forms.SelectFromList.show(
        valid_categories,
        name_attr='Name',
        title='Select Categories for Uniclass Parameters',
        button_name='Select Categories',
        multiselect=True
    )
    
    if selected_categories:
        try:
            count = create_project_parameters(selected_categories)
            forms.alert(
                "Successfully set up {} Uniclass parameters across {} categories.".format(count, len(selected_categories)),
                title="Success",
                warn_icon=False
            )
        except Exception as e:
            forms.alert("An error occurred: {}".format(str(e)), title="Error")
