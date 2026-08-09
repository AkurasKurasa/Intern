"""
Regression test for agent.py's OPT2 reclick guard -- once the transformer's
own pointer drifts back onto the SAME already-handled field several
consecutive times, redirect to a known-good target instead of another blind
Tab that just gambles on OS focus-traversal order.

Found 2026-08-08, live, direct user report ("still not finding the right
view"): the pointer aimed at the exact same already-filled field's screen
position on 3 consecutive steps in a row before finally moving on -- each
one caught safely by the reclick guard (no wasted click, no data
corruption), but the guard's only response was a blind Tab every time, with
no tracking of how many times this had just happened and no attempt to
steer toward a field that's actually still empty. This repeated dozens of
times across one run, which is what the "not sweeping cleanly, feels
scattered" complaint was actually about once the earlier scroll/reveal fix
was confirmed working.

REWRITTEN 2026-08-08, SAME NIGHT, after a second live regression: the
original redirect just clicked the known target and assumed it worked,
`continue`-ing straight back to the loop top. Direct user report ("It's
not working... it's using Tab to fucking navigate"): a run got stuck
oscillating between this redirect and a plain Tab for 25+ consecutive
steps, zero new fields filled, because the redirect click wasn't actually
moving OS focus to the target -- the exact "assume it worked instead of
verifying" mistake this project has hit and fixed multiple times already
(verify-at-fill, the scroll branch). Fixed by re-observing after the
redirect click and checking whether focus genuinely landed on the target;
repeated failures to land now escalate to advancing the tab instead of
retrying the identical failed maneuver forever.

REWRITTEN AGAIN 2026-08-08, SAME NIGHT, on direct repeated user instruction
("Do not use Tab to fucking navigate"): _RECLICK_REDIRECT_LIMIT dropped
from 2 to 1 -- the very FIRST drift onto an already-handled field now
redirects straight to a known target via click, no blind Tab tolerated
first. The same limit change was also applied to the sibling
"combobox already attempted (known blank)" guard, which previously had NO
streak tracking at all and just Tabbed every single time forever -- live
evidence: 'Suffix' hit that exact untracked path repeatedly across one run.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.navigation_protocol import find_visible_empty_target, find_scroll_target_element

VIEWPORT_BOTTOM = 1000.0


def _field(label, value="", bbox=(100, 100, 300, 130), ftype="editcontrol"):
    return {"element_id": label, "type": ftype, "label": label, "value": value,
            "bbox": list(bbox), "window_role": "active"}


def _run_reclick_guard(state, reclick_streak, redirect_limit, executor):
    """Mirrors the pre-verify (2026-08-07) reclick-guard shape -- kept for
    the "first drift, plain Tab" and "nothing visible to redirect to" cases,
    which the verify addition below doesn't change.

    Target search switched to find_scroll_target_element 2026-08-09, live,
    direct user report ("it's still revealing them one at a time"): the
    redirect's actual action is UIA SetFocus, not a click -- and this wx
    form auto-scrolls whatever gets focused into view as its own native
    behavior. Searching only the CURRENT viewport for a redirect target
    meant every redirect's SetFocus revealed exactly ONE field via that
    side effect, before Navigation Protocol's own explicit "scroll to the
    densest cluster" logic ever got a turn. Aiming the redirect at the
    deepest field in the next dense CLUSTER instead makes that same auto-
    scroll side effect reveal the whole cluster at once. Uses the REAL
    viewport height (VIEWPORT_BOTTOM), not unbounded -- the width
    parameter IS the density calculation; an effectively-infinite width
    would degenerate it into "just the single deepest field on the whole
    tab," losing the "fits in one screen" concept entirely (caught while
    writing this very test, before it ever reached a live run).

    CORRECTED 2026-08-09, SAME NIGHT, live, direct user report ("it skipped
    a lot of Policyholder tabs"): always reaching for the deep-cluster
    target overshot when a real, already-visible, zero-scroll-needed
    target existed nearby. Log + source cross-check: 'Suffix' (known
    blank) sat right next to 'Marital Status'/'Occupation'/etc -- already
    on screen -- but the redirect reached past all twelve of them for 'DL
    Number', and the resulting auto-scroll pushed them off the TOP of the
    viewport (element count dropped 162->155). Since this module only ever
    scrolls DOWN, they were gone for the rest of the run. Now tries the
    nearest already-visible target FIRST (zero overshoot risk by
    construction); only falls back to the deep-cluster strategy when
    nothing is visible in the current viewport at all -- preserving the
    original fix's actual intent (don't stop at the FIRST reveal when nothing
    closer exists) without letting it discard perfectly good, already-visible
    fields to get there."""
    reclick_streak += 1
    if reclick_streak >= redirect_limit:
        target = find_visible_empty_target(state, VIEWPORT_BOTTOM)
        if target is None:
            target = find_scroll_target_element(state, VIEWPORT_BOTTOM)
        if target and target.get("bbox"):
            b = target["bbox"]
            executor.execute({"action_type": "click",
                               "click_position": [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]})
            return 0   # streak resets
    executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
    return reclick_streak


def _run_reclick_guard_verified(state, post_redirect_focused_id, reclick_streak,
                                 stall_count, redirect_limit, stall_limit,
                                 executor, advance_tab_fn, attempted_keys=None):
    """Mirrors the CURRENT (2026-08-08) reclick-guard block in agent.py's
    run(): redirect to a known target, then VERIFY (via the post-click
    observation's focused_element_id) that focus actually landed there
    before trusting it. On repeated stalls, marks ONLY the specific
    unreachable target attempted (so it stops being re-offered) and looks
    for a DIFFERENT target ANYWHERE on the tab -- on- or off-screen,
    viewport-UNBOUNDED (1e9) -- before concluding the tab itself is
    exhausted. Was viewport-bounded at first; found live, same night, that
    a viewport-limited search still wrongly advanced the tab when a
    different target existed just one scroll away (the search only asked
    "is anything else visible RIGHT NOW", not "does anything else exist
    anywhere on this tab"), compounding into the reported jump to Vehicle."""
    attempted_keys = attempted_keys if attempted_keys is not None else set()
    _key_fn = lambda e, els: e.get("element_id")
    reclick_streak += 1
    if reclick_streak < redirect_limit:
        executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
        return reclick_streak, stall_count
    # Real viewport height, same reasoning as _run_reclick_guard above --
    # aim at the densest cluster's deepest field, not just the nearest
    # visible one, and not an unbounded "just the last field on the tab."
    target = find_scroll_target_element(state, VIEWPORT_BOTTOM,
                                         attempted_keys=attempted_keys, attempt_key_fn=_key_fn)
    if not (target and target.get("bbox")):
        executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
        return reclick_streak, stall_count
    b = target["bbox"]
    executor.execute({"action_type": "click",
                       "click_position": [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]})
    reclick_streak = 0
    landed = post_redirect_focused_id == target["element_id"]
    if landed:
        return reclick_streak, 0
    stall_count += 1
    if stall_count >= stall_limit:
        stall_count = 0
        attempted_keys.add(target["element_id"])
        alt = find_visible_empty_target(state, 1e9,   # unbounded -- on- or off-screen
                                         attempted_keys=attempted_keys, attempt_key_fn=_key_fn)
        if alt is None:
            advance_tab_fn()
    return reclick_streak, stall_count


class TestRedirectPrefersAnAlreadyVisibleTargetOverADistantCluster:
    """CORRECTED 2026-08-09, SAME NIGHT as the original cluster-aim fix,
    live, direct user report ("it skipped a lot of Policyholder tabs").
    The original fix (below, `TestFallsBackToTheDensestClusterOnlyWhenNothingIsVisible`)
    was right that a redirect must not settle for revealing just one field
    when scrolling further would reveal a whole cluster -- but ALWAYS
    reaching for the deep-cluster target, even when a real, already-
    visible, zero-scroll-needed target existed, went too far the other
    direction. Log + source cross-check: 'Suffix' (known blank) sat right
    next to 'Marital Status'/'Occupation'/'Education Level'/'Credit Score'
    -- already on screen -- but the redirect reached past ALL of them
    (plus eight more fields further down) for 'DL Number'. The resulting
    auto-scroll pushed the skipped fields off the TOP of the viewport
    (element count dropped 162->155 in the live log) -- and since this
    module only ever scrolls DOWN, they were gone for the rest of the run.
    A field sitting right there, already visible, needing zero scroll,
    must never be skipped in favor of a farther cluster."""

    def test_a_nearby_already_visible_field_is_preferred_over_a_farther_cluster(self):
        """The exact live regression, reproduced: a lone empty field is
        already visible nearby; a denser 3-field cluster sits further
        below. The redirect must take the nearby field -- it needs no
        scroll at all, so there is zero risk of overshooting past it."""
        executor = MagicMock()
        state = {"elements": [
            _field("Years Continuously Insured", value="9", bbox=(100, 100, 300, 130)),
            _field("Lonely Nearby Field", value="", bbox=(100, 200, 300, 230)),
            _field("Street Address 1", value="", bbox=(100, 900, 300, 930)),
            _field("Street Address 2", value="", bbox=(100, 940, 300, 970)),
            _field("City", value="", bbox=(100, 980, 300, 1010)),
        ]}
        streak = _run_reclick_guard(state, reclick_streak=0, redirect_limit=1, executor=executor)
        assert streak == 0
        calls = [c.args[0] for c in executor.execute.call_args_list]
        assert calls == [{"action_type": "click", "click_position": [200.0, 215.0]}]


class TestFallsBackToTheDensestClusterOnlyWhenNothingIsVisible:
    """Added 2026-08-09, live, direct user report ("it's still revealing
    them one at a time"): the redirect's real action is UIA SetFocus, and
    this wx form auto-scrolls whatever gets focused into view as its own
    native behavior -- previously undiagnosed. Searching only the current
    viewport for a redirect target meant every redirect's SetFocus
    revealed exactly ONE field via that side effect, so Navigation
    Protocol's own explicit "scroll to the densest cluster" logic never
    got a turn -- decide() never saw cur==0 because a redirect target was
    always found nearby first. Still valid for the genuine "nothing
    visible right now at all" case -- the fix above only narrows WHEN
    this strategy applies, it doesn't remove it."""

    def test_falls_back_to_the_deepest_field_of_the_densest_cluster_when_nothing_is_visible(self):
        """Nothing is currently visible in the viewport at all -- only
        then does the redirect reach for the deep-cluster strategy,
        aiming at the deepest member of the densest reachable cluster
        below the fold."""
        executor = MagicMock()
        state = {"elements": [
            _field("Years Continuously Insured", value="9", bbox=(100, 100, 300, 130)),
            _field("Street Address 1", value="", bbox=(100, 1100, 300, 1130)),
            _field("Street Address 2", value="", bbox=(100, 1140, 300, 1170)),
            _field("City", value="", bbox=(100, 1180, 300, 1210)),
        ]}
        streak = _run_reclick_guard(state, reclick_streak=0, redirect_limit=1, executor=executor)
        assert streak == 0
        calls = [c.args[0] for c in executor.execute.call_args_list]
        # City's bbox center -- the deepest member of the 3-field cluster,
        # none of which fit within VIEWPORT_BOTTOM=1000 (all off-screen).
        assert calls == [{"action_type": "click", "click_position": [200.0, 1195.0]}]


class TestReclickStreakRedirectsInsteadOfBlindTabbing:
    def test_first_drift_redirects_immediately_no_tab_tolerated(self):
        """redirect_limit=1: the very first drift onto an already-handled
        field goes straight to a known target via click -- no blind Tab
        first. Matches the real _RECLICK_REDIRECT_LIMIT=1 in agent.py."""
        executor = MagicMock()
        state = {"elements": [
            _field("Years Continuously Insured", value="9", bbox=(100, 100, 300, 130)),
            _field("Cell Phone", value="", bbox=(100, 200, 300, 230)),
        ]}
        streak = _run_reclick_guard(state, reclick_streak=0, redirect_limit=1, executor=executor)
        assert streak == 0
        calls = [c.args[0] for c in executor.execute.call_args_list]
        assert calls == [{"action_type": "click", "click_position": [200.0, 215.0]}]

    def test_redirect_falls_back_to_tab_when_nothing_else_is_visible(self):
        """No genuinely empty target exists yet (e.g. mid-transition) --
        still safe to fall back to a plain Tab rather than clicking nothing.
        This is the one remaining place Tab can still appear from this
        guard -- there is genuinely nothing else to click."""
        executor = MagicMock()
        state = {"elements": [
            _field("Years Continuously Insured", value="9", bbox=(100, 100, 300, 130)),
        ]}
        streak = _run_reclick_guard(state, reclick_streak=0, redirect_limit=1, executor=executor)
        # Streak stays incremented (not reset) since no target was found to
        # redirect to -- matches agent.py: the counter only resets on an
        # actual successful redirect, so the very next step retries
        # immediately instead of waiting through another full Tab-and-hope
        # cycle before trying again.
        assert streak == 1
        calls = [c.args[0] for c in executor.execute.call_args_list]
        assert calls == [{"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}]


class TestRedirectIsVerifiedNotAssumed:
    """The second-round fix: a redirect click that doesn't actually move
    focus must not be silently trusted. Live evidence: 13 consecutive
    redirect-click/plain-Tab cycles onto the exact same target, zero real
    progress -- the click plainly wasn't landing, but the old code had no
    way to notice and kept repeating it."""

    def test_redirect_that_lands_resets_both_counters(self):
        """focused_element_id matches the target after re-observing --
        genuine success, both the reclick streak and stall count reset."""
        executor = MagicMock()
        advance_tab_fn = MagicMock()
        state = {"elements": [
            _field("Years at Address", value="6", bbox=(100, 100, 300, 130)),
            _field("Prior Insurer", value="", bbox=(100, 200, 300, 230)),
        ]}
        streak, stall = _run_reclick_guard_verified(
            state, post_redirect_focused_id="Prior Insurer",
            reclick_streak=0, stall_count=0, redirect_limit=1, stall_limit=2,
            executor=executor, advance_tab_fn=advance_tab_fn)
        assert (streak, stall) == (0, 0)
        advance_tab_fn.assert_not_called()

    def test_redirect_that_does_not_land_increments_stall_without_advancing_yet(self):
        """The click was issued, but the post-click observation shows focus
        never actually moved to the target -- record the failure, but don't
        give up on the tab after just one miss."""
        executor = MagicMock()
        advance_tab_fn = MagicMock()
        state = {"elements": [
            _field("Years at Address", value="6", bbox=(100, 100, 300, 130)),
            _field("Prior Insurer", value="", bbox=(100, 200, 300, 230)),
        ]}
        streak, stall = _run_reclick_guard_verified(
            state, post_redirect_focused_id="Years at Address",   # still stuck there
            reclick_streak=0, stall_count=0, redirect_limit=1, stall_limit=2,
            executor=executor, advance_tab_fn=advance_tab_fn)
        assert (streak, stall) == (0, 1)
        advance_tab_fn.assert_not_called()

    def test_repeated_failed_redirects_advance_the_tab_instead_of_looping_forever(self):
        """The actual live regression, reproduced: the redirect keeps
        failing to stick -- once that's happened stall_limit times in a
        row, stop repeating it and move on to the next tab instead of an
        infinite click/Tab oscillation with zero fields filled."""
        executor = MagicMock()
        advance_tab_fn = MagicMock()
        state = {"elements": [
            _field("Years at Address", value="6", bbox=(100, 100, 300, 130)),
            _field("Prior Insurer", value="", bbox=(100, 200, 300, 230)),
        ]}
        streak, stall = _run_reclick_guard_verified(
            state, post_redirect_focused_id="Years at Address",   # still stuck there, AGAIN
            reclick_streak=0, stall_count=1, redirect_limit=1, stall_limit=2,
            executor=executor, advance_tab_fn=advance_tab_fn)
        assert (streak, stall) == (0, 0)   # stall resets after escalating
        advance_tab_fn.assert_called_once()


class TestStalledRedirectTriesADifferentTargetBeforeAbandoningTheTab:
    """Found 2026-08-08, live, direct user report ("What the fuck was that
    why did it go to Vehicle?"): 'Street Address 1' failed to receive focus
    twice in a row (its own click coordinates specifically), which the OLD
    escalation treated as proof the whole tab was exhausted -- advancing
    past a dozen other genuinely fillable Policyholder fields (City, State,
    ZIP, County, DL info, ...) that were never even tried, and the SAME
    thing happened again one tab later, compounding into a 2-tab jump
    straight to Vehicle. One unreachable field is not evidence the tab
    itself has nothing left."""

    def test_a_different_reachable_target_is_tried_before_advancing(self):
        """The actual live regression, shape preserved: a stalled target
        must not be retried, and there must be a different one to try
        instead of advancing. With the target search now aiming at the
        deepest field in the cluster (find_scroll_target_element,
        2026-08-09), 'City' (the deeper of the two empty fields) is what
        actually gets picked and stalls here -- 'Street Address 1' is the
        remaining alternative found afterward, the reverse of the original
        scenario's field names but the identical mechanism."""
        executor = MagicMock()
        advance_tab_fn = MagicMock()
        state = {"elements": [
            _field("Years Continuously Insured", value="9", bbox=(100, 100, 300, 130)),
            _field("Street Address 1", value="", bbox=(100, 200, 300, 230)),
            _field("City", value="", bbox=(100, 300, 300, 330)),
        ]}
        attempted = set()
        streak, stall = _run_reclick_guard_verified(
            state, post_redirect_focused_id="Years Continuously Insured",  # City never actually focused
            reclick_streak=0, stall_count=1, redirect_limit=1, stall_limit=2,
            executor=executor, advance_tab_fn=advance_tab_fn, attempted_keys=attempted)
        assert (streak, stall) == (0, 0)
        advance_tab_fn.assert_not_called()
        assert "City" in attempted, "the unreachable field must be excluded from future offers"

    def test_an_off_screen_alternate_target_also_counts_as_reachable(self):
        """The SECOND round of this same live bug: the alternate-target
        search was viewport-bounded, so it only asked 'is anything else
        visible RIGHT NOW' -- 'Street Address 2' existed but was just below
        the fold, not yet scrolled to, so the search wrongly concluded
        nothing else remained and advanced anyway. Fixed: search
        unbounded (on- or off-screen). This test's 'City' sits at y=5000,
        far outside any normal viewport -- must still be found."""
        executor = MagicMock()
        advance_tab_fn = MagicMock()
        state = {"elements": [
            _field("Years Continuously Insured", value="9", bbox=(100, 100, 300, 130)),
            _field("Street Address 1", value="", bbox=(100, 200, 300, 230)),
            _field("City", value="", bbox=(100, 5000, 300, 5030)),   # far below any real viewport
        ]}
        attempted = set()
        streak, stall = _run_reclick_guard_verified(
            state, post_redirect_focused_id="Years Continuously Insured",
            reclick_streak=0, stall_count=1, redirect_limit=1, stall_limit=2,
            executor=executor, advance_tab_fn=advance_tab_fn, attempted_keys=attempted)
        assert (streak, stall) == (0, 0)
        advance_tab_fn.assert_not_called()

    def test_advances_the_tab_only_when_truly_nothing_else_is_reachable(self):
        """No alternative exists once the stalled target is excluded --
        THIS is genuine tab exhaustion, so advancing is still correct."""
        executor = MagicMock()
        advance_tab_fn = MagicMock()
        state = {"elements": [
            _field("Years at Address", value="6", bbox=(100, 100, 300, 130)),
            _field("Prior Insurer", value="", bbox=(100, 200, 300, 230)),
        ]}
        attempted = set()
        streak, stall = _run_reclick_guard_verified(
            state, post_redirect_focused_id="Years at Address",
            reclick_streak=0, stall_count=1, redirect_limit=1, stall_limit=2,
            executor=executor, advance_tab_fn=advance_tab_fn, attempted_keys=attempted)
        assert (streak, stall) == (0, 0)
        advance_tab_fn.assert_called_once()


class TestComboboxAlreadyAttemptedBlankAlsoEscalates:
    """Found 2026-08-08, live, direct user report ("It's using Tab to
    fucking navigate. Its not finding the actual optimal view."): the
    'Suffix' combobox (legitimately blank -- no value in the record) hit a
    COMPLETELY SEPARATE guard from the one above ("combobox %r already
    attempted (known blank) -- Tab, no re-click") that had NO streak
    tracking and NO escalation at all -- it Tabbed every single time it
    recurred, forever. Live evidence: 'Suffix' hit this exact path 3 times
    in a row (steps 11-13 of one run), then twice more later in the SAME
    run, at moderate transformer confidence (0.72+) that never tripped the
    separate low-confidence gate. Fixed by reusing the SAME _reclick_streak
    counter and verified-redirect mechanism as the sibling "already-filled"
    guard, rather than maintaining two inconsistent copies -- this class
    proves the shared mechanism applies here too, using the same mirror
    (the real fix literally shares the counters and redirect/verify code)."""

    def test_first_occurrence_redirects_immediately_no_tab_tolerated(self):
        """Was: unconditional Tab, no escalation whatsoever. Now: the very
        first time a known-blank combobox (like 'Suffix') recurs, redirect
        to a real target via click, matching the _RECLICK_REDIRECT_LIMIT=1
        sibling guard. 'Suffix' itself isn't in this state's elements --
        the real code already excludes it via attempted_keys before this
        guard ever fires; this test only checks WHERE the redirect lands."""
        executor = MagicMock()
        state = {"elements": [
            _field("City", value="", bbox=(100, 200, 300, 230)),
        ]}
        streak = _run_reclick_guard(state, reclick_streak=0, redirect_limit=1, executor=executor)
        assert streak == 0
        calls = [c.args[0] for c in executor.execute.call_args_list]
        assert calls == [{"action_type": "click", "click_position": [200.0, 215.0]}]


def _focus_target_after_landing(state, landed_key, attempted_keys=None):
    """Mirrors the CURRENT (2026-08-09) post-landed refocus step in
    agent.py's run(): after a redirect's SetFocus lands on the DEEPEST
    field of a cluster (used to trigger the cluster's auto-scroll reveal),
    immediately look for the SHALLOWEST now-visible unattempted field and
    refocus onto THAT one instead, so the field that ends up actually
    filled next is the shallowest one, not the deep one merely used to
    trigger the reveal. Returns the key of whichever field ends up
    focused for filling."""
    attempted_keys = attempted_keys if attempted_keys is not None else set()
    _key_fn = lambda e, els: e.get("element_id")
    shallow = find_visible_empty_target(state, VIEWPORT_BOTTOM,
                                         attempted_keys=attempted_keys, attempt_key_fn=_key_fn)
    if shallow and shallow["element_id"] != landed_key:
        return shallow["element_id"]   # refocused
    return landed_key   # nothing shallower found -- stay on the landed target


class TestRefocusesShallowestFieldAfterRevealingACluster:
    """Found 2026-08-09, live, direct user report ("Marital Status was not
    even filled up until Cell Phone but we navigated away" / "It navigated
    away after filling in SSN"): landing SetFocus on the DEEPEST field of a
    cluster (to reveal the whole cluster via its auto-scroll side effect)
    also made THAT deepest field the next thing that got filled -- the
    auto-fill fast path always acts on whatever is currently focused. Live
    evidence: redirecting to 'Homeowner' (the deepest field, chosen to
    reveal a cluster including Marital Status, Occupation, Education Level,
    and Credit Score) immediately filled Homeowner itself the very next
    step -- none of the shallower fields in that same newly-revealed
    cluster ever got touched, because none of THEM ever became focused."""

    def test_refocuses_the_shallowest_visible_unattempted_field(self):
        """The actual live regression: landed on 'Homeowner' (deep), but
        'Marital Status' (shallower, same cluster) is now visible too --
        must refocus to Marital Status so it gets filled first."""
        state = {"elements": [
            _field("Marital Status", value="", bbox=(100, 100, 300, 130), ftype="comboboxcontrol"),
            _field("Occupation", value="", bbox=(100, 140, 300, 170), ftype="comboboxcontrol"),
            _field("Homeowner", value="", bbox=(100, 500, 300, 530), ftype="checkboxcontrol"),
        ]}
        focused = _focus_target_after_landing(state, landed_key="Homeowner")
        assert focused == "Marital Status"

    def test_stays_on_the_landed_target_when_it_is_already_the_shallowest(self):
        """No refocus needed/possible if the landed field is itself the
        only (or shallowest) one visible -- don't redundantly refocus
        something that's already correct."""
        state = {"elements": [
            _field("Homeowner", value="", bbox=(100, 500, 300, 530), ftype="checkboxcontrol"),
        ]}
        focused = _focus_target_after_landing(state, landed_key="Homeowner")
        assert focused == "Homeowner"
