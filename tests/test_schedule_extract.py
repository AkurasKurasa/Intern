"""Unit tests for components/inbox_router/schedule_extract.py.

The rule under test: a time is returned only when the email states BOTH a
calendar date and a clock time next to each other. Everything else returns
None and stays pending for a person, because Intern must never invent a
time.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "components", "inbox_router"))

from schedule_extract import extract_event_time  # noqa: E402

RECEIVED = "2026-08-28T09:30:00-07:00"


def _start(subject, body, received=RECEIVED):
    e = extract_event_time(subject, body, received)
    return None if e is None else e.start


# -- the emails a demo actually contains --------------------------------------

@pytest.mark.parametrize("body, expected", [
    ("Can we set up a call for September 3rd at 2pm?", datetime(2026, 9, 3, 14, 0)),
    ("Would Thursday, September 4th at 10am work?", datetime(2026, 9, 4, 10, 0)),
    ("We're scheduling a review for September 8th at 11am.", datetime(2026, 9, 8, 11, 0)),
    ("Proposing Monday, September 1st at 3pm.", datetime(2026, 9, 1, 15, 0)),
    ("Putting the retro on for Friday, September 6th at 2:30pm.", datetime(2026, 9, 6, 14, 30)),
    ("Timesheets are due by September 2nd, 5pm.", datetime(2026, 9, 2, 17, 0)),
    ("Unavailable for maintenance on Aug 20, 11pm-1am PT.", datetime(2026, 8, 20, 23, 0)),
])
def test_date_and_time_together_are_read(body, expected):
    assert _start("subject", body) == expected


@pytest.mark.parametrize("body", [
    "The signed contract is due back to us by September 5th.",
    "Could we talk pricing on September 11th, any time in the morning?",
    "Can we do a call on September 4th? Any time that afternoon works.",
    "Open enrollment begins Monday.",
    "The appeal window opens September 2nd and closes September 12th.",
    "Thanks for the update, talk soon.",
])
def test_no_time_is_invented_when_the_email_gives_none(body):
    assert _start("no time here", body) is None


# -- choosing the right date ---------------------------------------------------

def test_a_follow_up_uses_the_new_time_not_the_old_date():
    body = "Could we move our call to September 9th at 1pm instead of the 3rd?"
    assert _start("push the Sept 3rd call", body) == datetime(2026, 9, 9, 13, 0)


def test_body_is_preferred_over_subject():
    subject = "Call - Sept 3rd, 2pm"
    body = "Change of plan: September 9th at 4pm works better."
    assert _start(subject, body) == datetime(2026, 9, 9, 16, 0)


def test_subject_is_used_when_the_body_states_no_time():
    assert _start("Procurement review - Sept 8, 11am", "Please confirm.") == datetime(2026, 9, 8, 11, 0)


def test_a_time_far_from_any_date_is_not_attached_to_it():
    body = "On September 3rd we will discuss the budget and then, later on, at 2pm the team meets."
    assert _start("s", body) is None


# -- clock edge cases ------------------------------------------------------------

@pytest.mark.parametrize("phrase, hour", [
    ("September 3rd at 12pm", 12),
    ("September 3rd at 12am", 0),
    ("September 3rd at 1am", 1),
    ("September 3rd at 11 PM", 23),
])
def test_noon_midnight_and_case(phrase, hour):
    assert _start("s", phrase).hour == hour


def test_month_abbreviations_are_accepted():
    for word in ["Sep", "Sept", "Sept.", "September"]:
        assert _start("s", f"{word} 3, 2pm") == datetime(2026, 9, 3, 14, 0), word


def test_an_impossible_date_is_skipped_not_crashed_on():
    assert _start("s", "February 30th at 2pm") is None


def test_a_december_email_about_january_rolls_into_next_year():
    assert _start("s", "Kickoff is January 5th at 9am", "2026-12-20T10:00:00") == datetime(2027, 1, 5, 9, 0)


def test_input_value_and_quote_are_what_the_page_needs():
    e = extract_event_time("s", "Call on September 3rd at 2pm please", RECEIVED)
    assert e.as_input_value() == "2026-09-03T14:00"
    assert e.quote == "September 3rd at 2pm"
