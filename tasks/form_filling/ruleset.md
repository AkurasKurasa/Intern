# Task Specification — form_filling
**Goal:** Fill the car insurance form using data from the open text file  
**Last updated:** 2026-08-18T18:55:36.136335  
**Sessions processed:** session_20260402_180511

---

## Inferred Goal
Precisely fill out a car insurance form using data from an open text file.

## Navigation Rules
- Start at the main application window.
- Minimize any non-essential windows or overlays by clicking on "Minimize" in the top-left corner of the screen if necessary.
- Directly focus on the "Car Insurance — Data Entry Form" section without unnecessary clicks.
- Maximize the car insurance form if it is not already maximized.

## Field Rules
- Navigate to specific fields for data entry, such as "Driver Name", "Vehicle Make", etc., by clicking directly on their labels or using tab navigation.
- For each field, read the corresponding value from the open text file and enter it into the respective form field.
- Use the "Next" button (if present) to move to the next section of the form after entering data for a particular section.

## Data Rules
- Open the text file containing the car insurance form data before starting the task.
- Read each line or value from the text file in the order they appear, matching them to the corresponding fields on the form.
- Ensure that special characters and formatting in the text file are correctly interpreted when entering data into the form.

## Edge Cases & Corrections
- If a field is not found on the form but exists in the text file, skip it or log an error.
- If the text file contains more values than required fields on the form, ignore extra values.
- Handle cases where the text file may be missing or contain incorrect data by logging an error and stopping the task.

## Confidence
I am confident that this spec is complete and correct based on the provided traces. However, further testing with varied edge cases should be conducted to ensure robustness.
