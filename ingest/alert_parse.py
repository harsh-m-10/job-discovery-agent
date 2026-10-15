"""LAYER 1. Turn a job-alert email into postings (decided 2026-10-15, PLAN 6.7).

LinkedIn, Naukri and Indeed each mail the operator alerts listing new jobs.
apps_script/outcomes.gs forwards every mail under the Gmail label job/alerts
to alert_inbox; ingest/aggregate.py calls parse() on each. Nothing here
fetches anything: the email is the only input, and the links stay links.

Written before any real alert had arrived in the inbox, so it is deliberately
structural rather than tuned to one template:

  * A posting is identified by its link: a LinkedIn /jobs/view/<id>, an Indeed
    link carrying jk=<id>, or a Naukri /job-listings-...-<id>.
  * HTML mails: the title is the link's own text; company and location are
    the next non-boilerplate text lines before the next posting's link.
  * Plain-text mails ("View job: <url>" style): title, company and location
    are the lines just before the link.

A mail that yields nothing is recorded with jobs_found = 0 in alert_inbox, so
a template change shows up as a count, never as a silent loss.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

JOB_LINK = re.compile(
    r"(?P<li>linkedin\.com/(?:comm/)?jobs/view/(?P<li_id>\d+))"
    r"|(?P<in>indeed\.[a-z.]+/[^\s\"'<>]*?[?&]jk=(?P<in_id>[0-9a-f]{8,}))"
    r"|(?P<nk>naukri\.com/job-listings-[^\s\"'<>?]*?-(?P<nk_id>\d{6,}))",
    re.I,
)
URL = re.compile(r"https?://[^\s\"'<>]+", re.I)

# Lines that are chrome, not data. Compared lower-cased and stripped.
BOILERPLATE = re.compile(
    r"^(view job|view jobs?|apply( now)?|easy apply|promoted|new|actively (recruiting|hiring)"
    r"|be an early applicant|see all jobs|see more jobs|view all jobs|save|share|unsubscribe"
    r"|.*\bapplicants?\b.*|.*\bago\b|\d+[dhw]|just posted|today|urgently hiring"
    r"|.*job alert.*|.*jobs? (similar|matching|for you).*|.*manage (your )?alerts?.*"
    r"|.*(privacy|help center|unsubscribe|this email was).*|[\W\d_]*|(₹|rs\.?|inr).*"
    r"|(responds|typically responds).*|.*(easily apply|hiring multiple).*"
    r"|\d+(\.\d+)?\s*(★|stars?)?|(full|part)[- ]time|internship|contract|permanent"
    r"|(\d+\s*-\s*)?\d+\s*(yrs?|years?)( exp.*)?|view (details|more))$",
    re.I,
)
SPLIT = re.compile(r"\s+[·•|]\s+|\s+-\s+")


@dataclass
class AlertJob:
    site: str            # linkedin | indeed | naukri
    site_job_id: str
    title: str
    company: str
    location: str
    url: str


def site_of(match: re.Match) -> tuple[str, str]:
    if match.group("li"):
        return "linkedin", match.group("li_id")
    if match.group("in"):
        return "indeed", match.group("in_id")
    return "naukri", match.group("nk_id")


def clean_url(url: str, site: str, job_id: str) -> str:
    """Drop tracking parameters. Indeed keeps only jk, which is the posting."""
    base = url.split("#")[0].split("?")[0]
    if site == "indeed":
        return f"{base}?jk={job_id}"
    return base


def _is_noise(line: str) -> bool:
    return not line or len(line) > 160 or bool(BOILERPLATE.match(line.strip().lower()))


# --- HTML -------------------------------------------------------------------

BLOCK_TAGS = {"p", "div", "br", "tr", "td", "li", "h1", "h2", "h3", "h4", "table",
              "span", "strong", "b"}


class _Segments(HTMLParser):
    """HTML -> [("text", s) | ("link", href, s)], in document order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[tuple] = []
        self._href: str | None = None
        self._buf: list[str] = []
        self._skip = 0

    def _flush(self):
        text = " ".join("".join(self._buf).split())
        self._buf = []
        if not text:
            return
        if self._href:
            self.out.append(("link", self._href, text))
        else:
            self.out.append(("text", text))

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head", "title"):
            self._skip += 1
        if tag == "a":
            self._flush()
            self._href = dict(attrs).get("href") or ""
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "title"):
            self._skip = max(0, self._skip - 1)
        if tag == "a":
            self._flush()
            self._href = None
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if not self._skip:
            self._buf.append(data)


def _parse_html(body: str) -> list[AlertJob]:
    seg = _Segments()
    seg.feed(body)
    seg.close()
    seg._flush()
    items = seg.out

    out: dict[tuple[str, str], AlertJob] = {}
    i = 0
    while i < len(items):
        item = items[i]
        m = JOB_LINK.search(item[1]) if item[0] == "link" else None
        if not m:
            i += 1
            continue
        site, job_id = site_of(m)
        key = (site, job_id)
        title = item[2] if not _is_noise(item[2]) else ""
        url = item[1]
        lines: list[str] = []
        j = i + 1
        while j < len(items):
            nxt = items[j]
            nm = JOB_LINK.search(nxt[1]) if nxt[0] == "link" else None
            if nm and site_of(nm) != key:
                break
            text = nxt[-1]
            if nm and not title and not _is_noise(text):
                title = text
            elif not nm and not _is_noise(text):
                lines.append(text)
            j += 1
        i = j
        if key in out or not title:
            continue
        company, location = _company_location([l for l in lines if l != title])
        if company:
            out[key] = AlertJob(site, job_id, title, company, location,
                                clean_url(url, site, job_id))
    return list(out.values())


# --- plain text ---------------------------------------------------------------

def _parse_text(body: str) -> list[AlertJob]:
    lines = [l.strip() for l in body.splitlines()]
    out: dict[tuple[str, str], AlertJob] = {}
    last_link_line = -1
    for n, line in enumerate(lines):
        for u in URL.findall(line):
            m = JOB_LINK.search(u)
            if not m:
                continue
            site, job_id = site_of(m)
            text_on_line = URL.sub("", line).strip(" :-")
            before = [l for l in lines[last_link_line + 1:n] if not _is_noise(l)]
            if text_on_line and not _is_noise(text_on_line):
                before.append(text_on_line)
            last_link_line = n
            if (site, job_id) in out or not before:
                continue
            # The block is title, company, location -- the last three lines
            # before the link, after the previous posting's link.
            block = before[-3:] if len(before) >= 3 else before
            title = block[0]
            company, location = _company_location(block[1:])
            if company:
                out[(site, job_id)] = AlertJob(site, job_id, title, company, location,
                                               clean_url(u, site, job_id))
    return list(out.values())


def _company_location(lines: list[str]) -> tuple[str, str]:
    """First line is the company, unless it is 'Company · Location' or
    'Company - Location', which several templates put on one line."""
    if not lines:
        return "", ""
    first = lines[0]
    if len(lines) == 1:
        parts = SPLIT.split(first, maxsplit=1)
        if len(parts) == 2:
            return parts[0].strip(), parts[1].strip()
        return first.strip(), ""
    parts = SPLIT.split(first, maxsplit=1)
    if len(parts) == 2 and not lines[1]:
        return parts[0].strip(), parts[1].strip()
    return first.strip(), lines[1].strip()


def parse(sender: str | None, subject: str | None, body: str | None) -> list[AlertJob]:
    """-> the postings in one alert mail; [] when nothing recognisable."""
    text = body or ""
    if re.search(r"<(html|body|table|div|a)\b", text, re.I):
        return _parse_html(text)
    return _parse_text(text)
