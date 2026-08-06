# CLAUDE.md

Guidance for Claude Code when working in this repository.

See [DEVELOPERS.md](DEVELOPERS.md) for the project overview, architecture, and task list.

## Workflow

- Every session, commit and push all work done: `git add`, `git commit`, `git push` — do this for every change, not just at the end.
- Do not add Claude as co-author on commits pushed to GitHub.
- Whenever we encounter a problem or diverge from the plan, consistently update the Task Tree branches (`treetask/index.html`) to reflect it — keep it in sync with DEVELOPERS.md's Task List, not as an afterthought.
- Always create unit tests, integration tests, and E2E tests after every problem we encounter and solve.

## Architecture

- Do not hard code for tasks — let the Transformer and Agent work throughout.
