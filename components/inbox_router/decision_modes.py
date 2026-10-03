"""
components/inbox_router/decision_modes.py
=========================================
How Scope #3 reaches each decision. Chosen at Play, the same way Scope #1
asks how to fill its form.

  habits     -- only what was learned from you: the trained model, then the
                sender's history. If neither is confident the email is left
                UNDECIDED for a person; nothing is guessed.
  hybrid     -- habits first, and the LLM reasons about whatever they cannot
                settle. This was the only behaviour before modes existed.
  reasoning  -- the LLM reads and decides every email, with no sender
                history and no trained model.

Kept in its own dependency-free module so local_server.py can validate a
mode without importing inbox_agent, which pulls in torch and would slow the
server's startup (tests/test_local_server.py guards this).
"""

DECISION_MODES = ("habits", "hybrid", "reasoning")
DEFAULT_DECISION_MODE = "hybrid"
UNDECIDED = "undecided"
