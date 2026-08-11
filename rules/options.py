"""Outcome to live option (3.8 step 4).

The rule's outcome is whatever the demonstrator picked - "Passed". The live form
may spell it differently: "PASSED", "P", "Passed *". So the outcome is resolved
against the option list actually on the page, using the same embedding and
string similarity the Feature Extractor uses, and the select is driven by the
resolved option rather than by typed text.

If nothing clears the threshold, this returns None so the caller escalates.
3.10 is explicit: never pick the nearest option when unsure.
"""

import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from features import encoders  # noqa: E402
from features.extractor import levenshtein_ratio  # noqa: E402

# An exact or case-insensitive hit is certain; anything else has to clear this
# and beat the runner-up, or the answer is "ask".
SIMILARITY_THRESHOLD = 0.75
MARGIN_THRESHOLD = 0.05


@dataclass
class Resolution:
    option: str = None
    score: float = 0.0
    margin: float = 0.0
    method: str = "none"
    reason: str = ""

    @property
    def resolved(self):
        return self.option is not None


def similarity(intended, option):
    """String similarity, lifted by semantic similarity when available.

    The two are combined by taking the better of them: "P" for "PASSED" is a
    lexical match that embeddings dislike, while "Academic Standing" for
    "Status" is the reverse.
    """
    lexical = levenshtein_ratio(intended, option)
    if not encoders.available():
        return lexical
    return max(lexical, encoders.similarity(intended, option))


def resolve_option(intended, options):
    """Map an intended outcome onto one of the options actually offered."""
    if not options:
        return Resolution(reason="the field offers no options")
    if intended is None or not str(intended).strip():
        return Resolution(reason="no outcome to resolve")

    intended = str(intended).strip()

    if intended in options:
        return Resolution(intended, 1.0, 1.0, "exact")

    folded = {str(o).casefold(): o for o in options}
    if intended.casefold() in folded:
        return Resolution(folded[intended.casefold()], 1.0, 1.0, "case-insensitive")

    scored = sorted(
        ((similarity(intended, o), o) for o in options), reverse=True
    )
    best_score, best_option = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    margin = best_score - runner_up

    if best_score < SIMILARITY_THRESHOLD:
        return Resolution(
            None, best_score, margin, "none",
            reason=(f"closest option {best_option!r} scores {best_score:.2f}, "
                    f"below the {SIMILARITY_THRESHOLD} threshold"),
        )
    if margin < MARGIN_THRESHOLD:
        return Resolution(
            None, best_score, margin, "none",
            reason=(f"{best_option!r} and {scored[1][1]!r} are too close "
                    f"({margin:.3f}) to choose between"),
        )
    return Resolution(best_option, best_score, margin, "similarity")


def resolve_rule_options(rule, options):
    """Both outcomes of a rule, against one live option list.

    Returned as a dict so the executor can look up either branch, and as None
    when a branch cannot be resolved so it escalates rather than guessing.
    """
    return {
        rule.if_true: resolve_option(rule.if_true, options),
        rule.if_false: resolve_option(rule.if_false, options),
    }
