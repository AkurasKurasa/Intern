# Task Specification — form_filling
**Goal:** Fill the car insurance form using data from the open text file  
**Last updated:** 2026-05-22T16:03:27.550681  
**Sessions processed:** session_20260521_211247

---

## Inferred Goal
Complete a car insurance form by filling out each field with data from an open text file.

## Navigation Rules
- Start at the main application window.
- Navigate to the "Car Insurance" tab or section using keyboard navigation (e.g., Tab, Shift+Tab).
- Use logical navigation based on the form structure and field names. If additional sections are present (e.g., emergency contact information), navigate through them similarly.

## Field Rules
- For each field on the car insurance form:
  - Read corresponding data from the open text file in Notepad.
  - Fields are named according to standard car insurance forms (e.g., "Make", "Model", "Year", etc.).
  - If a required field is missing, skip it and move to the next one.
  - If a field is not present on the form, ignore that entry from the text file.

## Data Rules
- The text file contains tab-separated values (TSV) with headers matching form fields.
- Open Notepad if the text file is not already open before proceeding.
- Ensure that the file is in TSV format for accurate field mapping.
- If a required field is missing from the text file, skip it and move to the next one.

## Edge Cases & Corrections
- If the text file contains data for fields that do not exist on the form, ignore those entries and continue with the next valid entry.
- Handle cases where the form has additional sections (e.g., emergency contact information) by navigating through them using similar field rules.
- If the agent encounters a field that requires a selection from a dropdown menu, select the appropriate option based on the data in the text file.
- If the text file contains multiple entries and the form needs to be filled out for each entry, repeat the process for each new set of fields.
- Handle conditional fields by checking the data in the text file before filling out those fields.

## Confidence
I am confident that this specification covers the necessary actions based on the given traces and new edge cases. Further testing with more complex scenarios would increase confidence.

---

### Additional Notes
- Ensure that the text file is properly formatted to avoid any parsing errors.
- If the form has conditional fields (e.g., fields that only appear if certain criteria are met), handle these conditions by checking the data in the text file before filling out those fields.
