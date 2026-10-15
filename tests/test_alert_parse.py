"""Job-alert email parsing. Synthetic mails modelled on each site's layout.

No real alert had reached the inbox when this was written, so these pin the
structural rules (link identifies the posting; title, company, location around
it) rather than one template's markup. When a real mail parses badly, add it
here as a fixture first.

    python tests/test_alert_parse.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.alert_parse import parse

LI = "https://www.linkedin.com/comm/jobs/view/{}/?trackingId=abc%3D&refId=x"
IN = "https://in.indeed.com/rc/clk/dl?jk={}&from=ja&tk=zzz"
NK = "https://www.naukri.com/job-listings-backend-engineer-acme-bengaluru-3-to-5-years-{}?src=jobalert"


def test_linkedin_plain_text_view_job_blocks():
    body = f"""Your job alert for backend engineer in Bengaluru
30+ new jobs match your preferences.

Backend Engineer
Acme Technologies
Bengaluru, Karnataka, India
View job: {LI.format(4011111111)}

---------------------------------------------------------

Senior Software Engineer, Platform
Globex
Pune, Maharashtra, India
Be an early applicant
View job: {LI.format(4022222222)}

See all jobs: https://www.linkedin.com/comm/jobs/search?keywords=x
Unsubscribe: https://www.linkedin.com/comm/psettings/email
"""
    jobs = parse("LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", "backend engineer", body)
    assert [(j.title, j.company, j.location) for j in jobs] == [
        ("Backend Engineer", "Acme Technologies", "Bengaluru, Karnataka, India"),
        ("Senior Software Engineer, Platform", "Globex", "Pune, Maharashtra, India"),
    ], jobs
    assert jobs[0].url == "https://www.linkedin.com/comm/jobs/view/4011111111/"
    assert jobs[0].site == "linkedin" and jobs[0].site_job_id == "4011111111"


def test_linkedin_html_title_is_the_link_text_and_logo_links_are_ignored():
    body = f"""<html><body><table>
<tr><td><a href="{LI.format(4033333333)}"><img src="logo.png"></a></td>
<td><a href="{LI.format(4033333333)}">Machine Learning Engineer</a>
<p>Initech · Hyderabad, Telangana, India</p><p>Actively recruiting</p><p>3 applicants</p></td></tr>
<tr><td><a href="{LI.format(4044444444)}">Data Engineer</a><p>Umbrella</p><p>Remote</p></td></tr>
<tr><td><a href="https://www.linkedin.com/comm/jobs/search">See all jobs</a></td></tr>
</table></body></html>"""
    jobs = parse("jobalerts-noreply@linkedin.com", "x", body)
    assert [(j.title, j.company, j.location) for j in jobs] == [
        ("Machine Learning Engineer", "Initech", "Hyderabad, Telangana, India"),
        ("Data Engineer", "Umbrella", "Remote"),
    ], jobs


def test_indeed_html_company_dash_location_and_salary_skipped():
    body = f"""<html><body>
<div><a href="{IN.format('54f9aa84f34be464')}"><b>AI Integration Engineer</b></a></div>
<div>Turnipseed International - Bengaluru, Karnataka</div>
<div>₹12,00,000 - ₹18,00,000 a year</div><div>Easily apply</div>
<div><a href="{IN.format('89a9c4291829f4c4')}">Software Engineer 2</a></div>
<div>Microsoft</div><div>Hyderabad, Telangana</div><div>Just posted</div>
</body></html>"""
    jobs = parse("Indeed <alert@indeed.com>", "new jobs", body)
    assert [(j.title, j.company, j.location) for j in jobs] == [
        ("AI Integration Engineer", "Turnipseed International", "Bengaluru, Karnataka"),
        ("Software Engineer 2", "Microsoft", "Hyderabad, Telangana"),
    ], jobs
    assert jobs[0].url == "https://in.indeed.com/rc/clk/dl?jk=54f9aa84f34be464", \
        "only jk survives; the tracking token must go"


def test_naukri_html():
    body = f"""<html><body><table>
<tr><td><a href="{NK.format('151025012345')}">Backend Engineer</a></td></tr>
<tr><td>Acme Pvt Ltd</td></tr><tr><td>3-5 Yrs</td></tr><tr><td>Bengaluru</td></tr>
<tr><td><a href="{NK.format('151025012345')}">Apply</a></td></tr>
</table></body></html>"""
    jobs = parse("Naukri <jobalert@naukri.com>", "jobs for you", body)
    assert [(j.title, j.company, j.location) for j in jobs] == [
        ("Backend Engineer", "Acme Pvt Ltd", "Bengaluru")], jobs
    assert jobs[0].site == "naukri" and "?" not in jobs[0].url


def test_a_mail_with_no_job_links_yields_nothing():
    assert parse("x@linkedin.com", "Your weekly digest", "<html><a href='https://www.linkedin.com/feed'>Feed</a></html>") == []
    assert parse("x", "y", "") == []


def test_a_posting_without_a_company_is_dropped_rather_than_guessed():
    body = f"Backend Engineer\nView job: {LI.format(4055555555)}\n"
    assert parse("jobalerts-noreply@linkedin.com", "x", body) == []


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
