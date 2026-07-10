# Intern — common commands. Run `make help` for a list.
# Requires GNU make (winget install GnuWin32.Make on Windows).

PYTHON     ?= python
TRACE_DIR  ?= data/demos/eight_Tabs_clean
MODEL      ?= tasks/form_filling/model_eight_tabs.pt
SEM_MODEL  ?= tasks/form_filling/model_eight_tabs_semantic_v2.pt
EPOCHS     ?= 80
START_TAB  ?= 0

.PHONY: help record clean-demos train train-semantic run run-semantic form test tree

help:               ## show this list
	@grep -E "^[a-zA-Z_-]+:.*?## " Makefile | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-16s %s\n", $$1, $$2}'

record:             ## launch the GUI demo recorder (out_dir defaults to data/demos/eight_Tabs)
	$(PYTHON) app/main.py

clean-demos:        ## strip dropdown-select/junk/dupes: make clean-demos SRC=<src> DST=<dst>
	$(PYTHON) scripts/clean_demos.py $(SRC) $(DST)

train:              ## train legacy action-space model (TRACE_DIR/MODEL/EPOCHS overridable)
	$(PYTHON) train.py --trace_dir $(TRACE_DIR) --save_path $(MODEL) --epochs $(EPOCHS)

train-semantic:     ## train Universal Semantic Action Space model
	$(PYTHON) train.py --trace_dir $(TRACE_DIR) --save_path $(SEM_MODEL) --epochs $(EPOCHS) --action_space semantic

run:                ## run the agent with the legacy model (needs form + Notepad open)
	$(PYTHON) run_task.py --model $(MODEL) --start_tab $(START_TAB)

run-semantic:       ## run the agent with the semantic v2 model
	$(PYTHON) run_task.py --model $(SEM_MODEL) --start_tab $(START_TAB)

form:               ## open the wx car-insurance test form
	$(PYTHON) car_insurance_entry/car_insurance_form_wx.py

test:               ## run the test suite
	$(PYTHON) -m pytest tests/ -q

tree:               ## open the treetask project-management tree in the browser
	start "" treetask/index.html
