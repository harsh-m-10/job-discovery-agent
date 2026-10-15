"""Outcome capture: classification, matching, status rule. No network, no DB.

The emails below are written in the shapes Greenhouse, Lever, Ashby and
Workday tenants actually send. The two failure modes that matter most:

  * an acknowledgement read as an interview ("if selected, we will schedule an
    interview") would fabricate progress in the funnel;
  * an old email overwriting a newer outcome would make the funnel lie in the
    other direction.

    python tests/test_outcomes.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from outcomes.classify import classify
from outcomes.match import App, match, sender_text
from outcomes.run import Event, next_status

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


# --- classification ----------------------------------------------------------

CASES = {
    "rejection": [
        ("Your application to Okta", "Thank you for your interest. After careful consideration, we "
         "have decided to move forward with other candidates whose experience more closely matches."),
        ("Update on your application", "Unfortunately, we will not be moving forward with your "
         "application at this time."),
        ("Software Engineer - Glean", "We wanted to let you know the position has been filled."),
        ("Re: SDE II", "We regret to inform you that you have not been selected for the next stage."),
        ("Application status", "Unfortunately your application was unsuccessful."),
    ],
    "acknowledgement": [
        ("Thank you for applying to Razorpay!", "We have received your application for Backend "
         "Engineer. Our team will review it and if your profile is a match, we will reach out to "
         "schedule an interview."),
        ("We received your application", "Thanks for applying to Sarvam AI."),
        ("Application submitted", "Your application for Data Engineer has been received."),
    ],
    "assessment": [
        ("Next step: HackerRank assessment", "Please complete the coding challenge within 5 days."),
        ("Take-home assignment for Platform Engineer", "Here is the take-home exercise; we will "
         "discuss it in your interview."),
    ],
    "interview_invite": [
        ("Interview invitation - Software Engineer", "We would like to invite you to an interview."),
        ("Next steps with Target", "Please book a time using this link: calendly.com/x/30min"),
        ("Re: Interview", "Unfortunately the interviewer is unavailable on Monday; could we "
         "reschedule to Wednesday?"),
    ],
    "other": [
        ("Your weekly job alerts", "12 new jobs match your search."),
        ("", ""),
    ],
}


def test_every_sample_is_classified_correctly():
    wrong = []
    for kind, samples in CASES.items():
        for subject, snippet in samples:
            got = classify("x@y.com", subject, snippet)
            if got != kind:
                wrong.append(f"{subject!r}: expected {kind}, got {got}")
    assert not wrong, "\n  " + "\n  ".join(wrong)


def test_a_conditional_promise_of_an_interview_is_not_an_invite():
    s = ("Thank you for applying. Should your experience match our needs, we will contact you to "
         "schedule an interview.")
    assert classify(None, "Application received", s) == "acknowledgement"


def test_a_reschedule_that_says_unfortunately_is_not_a_rejection():
    assert classify(None, "Interview", "Unfortunately we need to reschedule your interview.") \
        == "interview_invite"


# --- sender parsing -----------------------------------------------------------

def test_the_ats_domain_never_names_the_employer():
    assert sender_text("Okta Recruiting <no-reply@greenhouse-mail.io>") == "Okta Recruiting"
    assert "lever" not in sender_text("Talent <hire@lever.co>")


def test_workday_mail_names_the_employer_in_the_mailbox():
    assert "cisco" in sender_text("Cisco <cisco@myworkday.com>")


def test_an_employer_domain_is_used():
    assert "razorpay" in sender_text("<careers@razorpay.com>")


# --- matching ----------------------------------------------------------------

def app(i, key, title="Software Engineer", status="applied", days_ago=10):
    return App(id=i, status=status, applied_at=T0 - timedelta(days=days_ago),
               company_key=key, title=title)


def test_one_application_at_the_company_is_matched():
    apps = [app(1, "okta"), app(2, "glean")]
    m = match("Okta Recruiting <no-reply@greenhouse-mail.io>", "Your application", "x",
              T0, "rejection", apps)
    assert (m.application_id, m.state) == (1, "matched")


def test_the_title_breaks_a_tie_between_two_applications():
    apps = [app(1, "okta", "Backend Engineer, Identity Platform"),
            app(2, "okta", "Data Engineer, Analytics")]
    m = match("Okta <x@greenhouse-mail.io>", "Update: Backend Engineer, Identity Platform",
              "Unfortunately...", T0, "rejection", apps)
    assert (m.application_id, m.state) == (1, "matched")


def test_an_unbreakable_tie_goes_to_review_not_a_guess():
    apps = [app(1, "okta", "Software Engineer"), app(2, "okta", "Software Engineer II")]
    m = match("Okta <x@greenhouse-mail.io>", "Your application", "Unfortunately...",
              T0, "rejection", apps)
    assert m.state == "needs_review" and m.application_id is None
    assert m.candidates == (1, 2)


def test_an_ambiguous_acknowledgement_is_not_worth_reviewing():
    apps = [app(1, "okta"), app(2, "okta", "Software Engineer II")]
    m = match("Okta <x@greenhouse-mail.io>", "Thanks for applying", "", T0, "acknowledgement", apps)
    assert m.state == "ignored"


def test_applications_outside_the_window_are_not_candidates():
    apps = [app(1, "okta", days_ago=200)]
    m = match("Okta <x@greenhouse-mail.io>", "Unfortunately", "", T0, "rejection", apps)
    assert m.state == "unmatched"


def test_queued_and_skipped_jobs_were_never_applied_to():
    apps = [app(1, "okta", status="skipped"), app(2, "okta", status="queued")]
    m = match("Okta <x@greenhouse-mail.io>", "Unfortunately", "", T0, "rejection", apps)
    assert m.state == "unmatched"


def test_an_unknown_company_rejection_is_kept_for_review_an_ad_is_dropped():
    assert match("Acme <a@acme.com>", "Unfortunately", "", T0, "rejection", []).state == "unmatched"
    assert match("Acme <a@acme.com>", "Newsletter", "", T0, "other", []).state == "ignored"


# --- status rule ---------------------------------------------------------------

def ev(kind, hours):
    return Event(kind, T0 + timedelta(hours=hours))


def test_a_rejection_sets_rejected_and_responded():
    p = next_status("applied", T0, None, [ev("acknowledgement", 1), ev("rejection", 30)])
    assert p["status"] == "rejected"
    assert p["responded_at"] == (T0 + timedelta(hours=30)).isoformat()
    assert p["updated_at"] == (T0 + timedelta(hours=30)).isoformat(), \
        "updated_at must be the email's time, not now"


def test_the_latest_email_wins_whatever_order_they_arrive_in():
    events = [ev("interview_invite", 100), ev("rejection", 20)]   # old rejection backfilled late
    assert next_status("applied", T0, None, events)["status"] == "interviewing"


def test_an_offer_is_never_overwritten():
    assert "status" not in next_status("offer", T0, T0, [ev("rejection", 50)])


def test_a_manual_change_after_the_latest_email_is_respected():
    manual = T0 + timedelta(hours=200)
    assert "status" not in next_status("interviewing", manual, T0, [ev("rejection", 50)])


def test_clicking_applied_after_a_fast_rejection_does_not_hide_it():
    """Applied at 09:00, auto-rejected at 09:20, Applied clicked at 11:00."""
    clicked = T0 + timedelta(hours=2)
    p = next_status("applied", clicked, None, [Event("rejection", T0 + timedelta(minutes=20))])
    assert p.get("status") == "rejected"


def test_an_email_processed_after_the_last_write_still_applies():
    """The race the email-time updated_at exists for: the previous run wrote
    updated_at = the previous email's time, so a later email still wins."""
    prev = T0 + timedelta(hours=10)
    p = next_status("responded", prev, prev, [ev("assessment", 10), ev("interview_invite", 12)])
    assert p.get("status") == "interviewing"


def test_acknowledgements_alone_change_nothing():
    assert next_status("applied", T0, None, [ev("acknowledgement", 1), ev("other", 2)]) == {}


def test_processing_twice_is_a_no_op():
    events = [ev("rejection", 30)]
    first = next_status("applied", T0, None, events)
    again = next_status(first["status"], datetime.fromisoformat(first["updated_at"]),
                        datetime.fromisoformat(first["responded_at"]), events)
    assert again == {}


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  pass  {name}")
            except AssertionError as exc:
                failed += 1
                print(f"  FAIL  {name}: {exc or 'assertion failed'}")
    print(f"\n{failed} failed" if failed else "\nall tests passed")
    sys.exit(1 if failed else 0)
