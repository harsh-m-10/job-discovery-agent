"""Telegram channel and instant-ping routing. No network — everything stubbed.

The property that matters most: no job is ever dropped. A job the instant
channel does not take — because it failed, or because max_pings_per_day is
spent — must land in the email digest in the same run.

    python tests/test_telegram.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SUPABASE_URL", "https://example.invalid")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")

import notify.run as nr
from notify import telegram as tg


def job(i: int, score: float = 8.0) -> dict:
    return {"job_id": i, "title": f"Engineer {i}", "company": "Acme",
            "location": "Bengaluru", "absolute_url": f"https://x.invalid/{i}",
            "fit_score": score, "age_label": "2h", "min_years": 2, "max_years": 4}


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {"ok": True}
        self.text = str(self._body)

    def json(self):
        return self._body


def with_env(env: dict, fn):
    saved = {k: os.environ.get(k) for k in env}
    try:
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return fn()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


CONFIGURED = {"TELEGRAM_BOT_TOKEN": "123:secret", "TELEGRAM_CHAT_ID": "42"}
UNCONFIGURED = {"TELEGRAM_BOT_TOKEN": None, "TELEGRAM_CHAT_ID": None,
                "CALLMEBOT_PHONE": None, "CALLMEBOT_APIKEY": None}


# --- the channel ---------------------------------------------------------

def test_send_posts_plain_text_without_previews():
    seen = {}

    def post(url, json=None, timeout=None):
        seen.update(url=url, json=json)
        return Resp()

    real = tg.requests.post
    tg.requests.post = post
    try:
        with_env(CONFIGURED, lambda: tg.send("hello"))
    finally:
        tg.requests.post = real
    assert seen["url"].endswith("/sendMessage")
    assert seen["json"] == {"chat_id": "42", "text": "hello",
                            "disable_web_page_preview": True}


def test_an_api_refusal_raises_and_never_leaks_the_token():
    real = tg.requests.post
    tg.requests.post = lambda *a, **k: Resp(400, {"ok": False, "description": "chat not found"})
    try:
        with_env(CONFIGURED, lambda: tg.send("x"))
    except RuntimeError as exc:
        assert "chat not found" in str(exc)
        assert "secret" not in str(exc), "the bot token leaked into the error"
    else:
        raise AssertionError("a refused send did not raise")
    finally:
        tg.requests.post = real


def test_a_200_with_ok_false_still_raises():
    real = tg.requests.post
    tg.requests.post = lambda *a, **k: Resp(200, {"ok": False, "description": "blocked"})
    try:
        with_env(CONFIGURED, lambda: tg.send("x"))
    except RuntimeError:
        return
    finally:
        tg.requests.post = real
    raise AssertionError("ok:false was treated as delivered")


def test_unconfigured_raises_not_configured():
    try:
        with_env(UNCONFIGURED, lambda: tg.send("x"))
    except tg.NotConfigured:
        return
    raise AssertionError("sent without credentials")


def test_the_message_carries_what_you_need_to_act_from_the_phone():
    j = {**job(1), "role_family": "backend", "resume_variant": "https://r.invalid/be"}
    text = tg.format_message(j, "https://dash.invalid/d/s")
    assert text.startswith("8.0 | Engineer 1"), "score must lead"
    assert "Resume: backend -> https://r.invalid/be" in text
    assert "Apply: https://x.invalid/1" in text
    assert "https://dash.invalid/d/s/j/1" in text
    assert "provisional" not in text


def test_a_provisional_score_says_so():
    assert "provisional" in tg.format_message({**job(1), "provisional": True})


def test_an_oversized_message_is_truncated_under_the_api_limit():
    long_job = {**job(1), "matched_skills": ["x" * 3000], "gap_skills": ["y" * 3000]}
    assert len(tg.format_message(long_job)) <= tg.MAX_CHARS


# --- referrals in the ping (Phase 5.2) -----------------------------------

def contact(i, company, title=None, batchmate=False, days_ago=100):
    from datetime import date, timedelta
    from score.referral import Contact
    return Contact(id=i, full_name=f"Person {i}", company_raw=company,
                   company_norm=nr_key(company), title=title,
                   connected_on=date(2026, 10, 1) - timedelta(days=days_ago),
                   is_batchmate=batchmate, profile_url=f"https://example.invalid/in/p{i}")


def nr_key(name):
    from common.company_key import company_key
    return company_key(name)


def test_referrals_are_ranked_batchmate_then_role_then_recency():
    people = [contact(1, "Target", "Recruiter", days_ago=10),
              contact(2, "Target Corporation India", "Senior Software Engineer", days_ago=400),
              contact(3, "Target", "Product Designer", batchmate=True, days_ago=900),
              contact(4, "Okta", "Software Engineer")]
    got = nr.referrals_for("Target", "Backend Engineer", people)
    assert [c["id"] for c in got] == [3, 2, 1], got
    assert all(c["profile_url"] for c in got)


def test_no_contact_at_the_company_is_an_empty_list():
    assert nr.referrals_for("Glean", "Engineer", [contact(1, "Okta")]) == []


def test_the_ping_names_who_to_ask_with_a_link():
    j = {**job(1), "referrals": [{"full_name": "Asha Rao", "title": "Staff Engineer",
                                  "is_batchmate": True, "profile_url": "https://example.invalid/in/asha"}]}
    text = tg.format_message(j)
    assert "Ask: Asha Rao (Staff Engineer) · batchmate https://example.invalid/in/asha" in text


# --- routing in notify.run.main -----------------------------------------

def run_main(jobs, *, sent_today=0, cap=40, telegram_fails=False, configured=True):
    """Drive notify.run.main end to end with every side effect captured."""
    records, emails, pings = [], [], []
    patches = {
        "pending_jobs": lambda *a, **k: list(jobs),
        "record": lambda job_id, channel, ok, err: records.append((job_id, channel, ok)),
        "prune_failures": lambda days: 0,
        "pings_sent_today": lambda channels: sent_today,
    }
    saved = {k: getattr(nr, k) for k in patches}
    real_send, real_email = tg.send, nr.email_channel.send
    real_yaml = nr.yaml.safe_load
    real_argv = sys.argv

    def fake_send(text):
        if telegram_fails:
            raise RuntimeError("telegram 502: bad gateway")
        pings.append(text)

    try:
        for k, v in patches.items():
            setattr(nr, k, v)
        tg.send = fake_send
        nr.email_channel.send = lambda subject, body: emails.append(body) or "test"
        nr.yaml.safe_load = lambda _: {"ping_threshold": 6.5, "max_pings_per_day": cap}
        sys.argv = ["notify.run"]
        env = CONFIGURED if configured else UNCONFIGURED
        with_env(env, nr.main)
    finally:
        for k, v in saved.items():
            setattr(nr, k, v)
        tg.send, nr.email_channel.send = real_send, real_email
        nr.yaml.safe_load = real_yaml
        sys.argv = real_argv
    return records, emails, pings


def test_every_job_goes_to_telegram_when_there_is_budget():
    records, emails, pings = run_main([job(1), job(2), job(3)])
    assert len(pings) == 3 and not emails
    assert {(j, c, ok) for j, c, ok in records} == {(1, "telegram", True),
                                                   (2, "telegram", True),
                                                   (3, "telegram", True)}


def test_jobs_over_the_daily_cap_are_emailed_not_dropped():
    records, emails, pings = run_main([job(1, 9.5), job(2, 8.0), job(3, 7.0)],
                                      sent_today=39, cap=40)
    assert len(pings) == 1 and pings[0].startswith("9.5"), "best job must take the last slot"
    assert len(emails) == 1, "overflow must go out as one digest"
    assert "daily ping cap (40) reached" in emails[0]
    emailed = {j for j, c, ok in records if c == "email" and ok}
    assert emailed == {2, 3}


def test_a_failed_ping_falls_back_to_email_in_the_same_run():
    records, emails, pings = run_main([job(1)], telegram_fails=True)
    assert not pings and len(emails) == 1
    assert (1, "telegram", False) in records and (1, "email", True) in records
    assert "telegram: telegram 502" in emails[0]


def test_with_no_instant_channel_everything_is_one_digest():
    records, emails, pings = run_main([job(1), job(2)], configured=False)
    assert not pings and len(emails) == 1
    assert "no instant channel configured" in emails[0]


def test_a_zero_cap_means_unlimited():
    _, emails, pings = run_main([job(i) for i in range(5)], sent_today=500, cap=0)
    assert len(pings) == 5 and not emails


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
