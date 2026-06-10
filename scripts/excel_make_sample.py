"""
Open Excel with a small sample roster (for the swap-proof smoke). Leaves Excel
visible and running; exits without quitting it.
"""
import win32com.client as win32

xl = win32.Dispatch("Excel.Application")
xl.Visible = True
wb = xl.Workbooks.Add()
ws = xl.ActiveSheet
ws.Name = "Employee Roster"

rows = [
    ["First Name", "Last Name", "Department", "Employee ID"],
    ["Alex",   "Rivera",   "Engineering", "EMP-1001"],
    ["Jordan", "Chen",     "Marketing",   "EMP-1002"],
    ["Sam",    "Whitfield","Finance",     "EMP-1003"],
]
for r, row in enumerate(rows, start=1):
    for c, val in enumerate(row, start=1):
        ws.Cells(r, c).Value = val

ws.Range("B2").Select()   # active cell on a data cell
print("Excel opened with sample roster. Leave it open, run excel_swap_smoke.py.")
