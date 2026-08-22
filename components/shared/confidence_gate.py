"""
Shared "should I trust the fast answer, or escalate" decision, used by both
Scope #1 (components/agent/agent.py) and Scope #2
(components/scope2/resolver/assign.py).

Both scopes already compute a confidence signal before this point --
Scope #1's Transformer click-confidence plus two struggle-streak counters,
Scope #2's matcher score and margin from resolver/assign.py -- this module
does not touch or know about either. It only combines already-evaluated
booleans into one escalate/don't-escalate decision, replacing three
separately-written copies of the same shape of expression (two in agent.py,
one implicit in resolver/assign.py's `score >= tau and margin >= delta`)
with one shared, tested function.

See docs/superpowers/specs/2026-08-21-scope-unification-design.md, Piece 5.
"""


def should_escalate(*signals: bool) -> bool:
    """True if any signal indicates the fast/cheap answer shouldn't be
    trusted as-is. No signals given -> no reason to escalate -> False."""
    return any(signals)
