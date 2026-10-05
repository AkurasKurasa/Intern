"""
components/inbox_router/automate_checks.py
==========================================
Walks the boss' "Check ..." tasks on the real Inbox Dispatch page: opens
each check, reads the answer the page shows, goes back. Used as the last
phase of run_boss_task_list.py (after Cold Email and the inbox, so a reply
check sees the mailbox as the run left it).

Read-only in every mode -- a check never drafts, sends or schedules -- so
there is no --commit distinction here: a dry run and a real run read the
same answers.

The answers come from checker.py through the page; this script decides
nothing itself, it only reads what is on screen, the same way the other
two phases read their rows off the DOM.
"""
from __future__ import annotations

from pointer import Pointer

# What a check's answer is shown as, so the summary can count them.
POSITIVE = {"yes", "free"}


def open_checks_view(page):
    """Switch the page to its Checks list and wait for the real answers."""
    with page.expect_response(lambda r: "/checks/api/list" in r.url and r.request.method == "GET",
                              timeout=60_000):
        page.evaluate("setView('checks')")
    page.wait_for_timeout(200)


def process_one(page, index: int, pointer=None):
    """Open check `index`, read its answer, go back. None when there are no
    more checks. Never changes the list, so the caller just advances."""
    pointer = pointer or Pointer(page, enabled=False)
    row = page.locator("#checksRowList .row-item").nth(index)
    if row.count() == 0:
        return None

    pointer.click(row)
    page.wait_for_selector("#checksDetailView:not([hidden])")

    label = page.locator("#checkLabel").inner_text()
    answer = page.locator("#checkAnswer").inner_text().strip().lower()
    detail = page.locator("#checkDetail").inner_text()
    evidence = page.locator("#checkEvidence .row-item").count()

    print(f"\n  {label}")
    print(f"    -> {answer.upper()}: {detail}")

    pointer.click("#checksBackBtn")
    page.wait_for_selector("#checksListView:not([hidden])")
    return {"label": label, "answer": answer, "detail": detail, "evidence_count": evidence}
