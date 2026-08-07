# CLAUDE.md

Guidance for Claude Code when working in this repository.

See [DEVELOPERS.md](DEVELOPERS.md) for the project overview, architecture, and task list.

## Workflow

- Every session, commit and push all work done: `git add`, `git commit`, `git push` — do this for every change, not just at the end.
- Do not add Claude as co-author on commits pushed to GitHub.
- Whenever we encounter a problem or diverge from the plan, consistently update the Task Tree branches (`treetask/index.html`) to reflect it — keep it in sync with DEVELOPERS.md's Task List, not as an afterthought. DEVELOPERS.md's Task List is restructured to match the Task Tree's branches (Scope #1/#2/#3, Perception, Representation, Learning, Adaptability, Scalability, Execution and Integration, Evaluation, UI/UX), not the other way around.
- Every choice we make on how to solve a problem gets recorded in the Task Tree — not just that a problem happened, but the decision made about how to solve it.
- Every node in the Task Tree (hub and task) has a description, not just a label.
- Task Tree nodes can declare prerequisites; a node that depends on an unfinished prerequisite is shown locked/blocked, and unlocks once the prerequisite is done.
- Always create unit tests, integration tests, and E2E tests after every problem we encounter and solve.
- `Thesis.docx` (repo root, gitignored — kept local, not pushed to GitHub) is the actual thesis paper. Read and understand it, and consistently update it (if necessary) whenever we solve a major problem — meaning a node in the Task Tree, not every minor fix.
- Only the user runs live tasks (`run_task.py` / any real GUI-automation run that clicks and types on the actual screen) from the terminal. Do not launch these live runs — that's the user's call to make and execute, every time.

## Architecture

- Do not hard code for tasks — let the Transformer and Agent work throughout.
- Prioritize Speed and Quality above almost everything else in finishing this project. A slow agent is worthless; a low-quality agent is worse than worthless. Neither is optional — don't trade one for the other.
- Make sure fixes actually generalize: a fix should be applicable to other tasks, not specific to the one we're currently working on.

## Communication

- Don't be a yes-man or a sycophant.
- Explain what you're doing in simple terms as you're doing it, as if you're actually teaching me.
