# -*- coding: utf-8 -*-
"""Imports parameter values from a CSV file back into Revit elements.
The CSV file must contain a column named 'Id' or 'Element ID'."""

import csv
import io
from pyrevit import revit, forms
from Autodesk.Revit.DB import Transaction, ElementId, StorageType

doc = revit.doc

def import_parameters():
    # Prompt the user to select the CSV file
    csv_file = forms.pick_file(file_ext='csv', title='Select CSV file with Parameter Data')
    if not csv_file:
        return

    try:
        # Using io.open with utf-8-sig to handle Excel exports properly
        with io.open(csv_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames
            
            if not headers:
                forms.alert("CSV file is empty or invalid.", exitscript=True)
            
            # Find the Element ID column (ignoring case and spaces)
            id_col = next((h for h in headers if h and h.lower().replace(" ", "") in ['id', 'elementid']), None)
            
            if not id_col:
                forms.alert("Could not find an 'Id' or 'Element ID' column in the CSV.\nMake sure your exported file includes element IDs.", title="Error", exitscript=True)
                
            # Treat all other columns as parameter names
            param_cols = [h for h in headers if h != id_col and h]
            
            updated_elements = 0
            
            # Start transaction to modify Revit document
            with Transaction(doc, "Import Parameter Values from CSV") as t:
                t.Start()
                
                for row in reader:
                    id_val = row.get(id_col)
                    if not id_val or not id_val.strip():
                        continue
                        
                    # Try to get the element from the ID
                    try:
                        elem_id = ElementId(int(float(id_val.strip())))
                        elem = doc.GetElement(elem_id)
                    except:
                        continue # Skip invalid IDs or elements that no longer exist
                        
                    if not elem:
                        continue
                        
                    element_updated = False
                    
                    # Update each parameter for the element
                    for param_name in param_cols:
                        val = row.get(param_name)
                        if val is None:
                            continue
                            
                        # Try to find the parameter on the element
                        param = elem.LookupParameter(param_name)
                        if param and not param.IsReadOnly:
                            try:
                                st = param.StorageType
                                # Handle different data types
                                if st == StorageType.String:
                                    param.Set(val)
                                    element_updated = True
                                elif st == StorageType.Integer:
                                    if val.strip():
                                        param.Set(int(float(val)))
                                        element_updated = True
                                elif st == StorageType.Double:
                                    if val.strip():
                                        param.Set(float(val))
                                        element_updated = True
                            except Exception as e:
                                print("Failed to set {} on {}: {}".format(param_name, id_val, e))
                                
                    if element_updated:
                        updated_elements += 1
                        
                t.Commit()
                
            forms.alert("Successfully updated parameters for {} elements.".format(updated_elements), title="Success")
            
    except Exception as e:
        forms.alert("Error reading CSV file: {}".format(str(e)), title="Error")

# Execute
if __name__ == '__main__':
    import_parameters()
