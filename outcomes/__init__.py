"""Outcome capture (docs/PLAN.md Phase 4).

Turns ATS emails into application outcomes. The dashboard's /api/events route
only stores raw rows; everything that decides anything lives here, in Python,
so it can be tested and so the dashboard holds no business logic.

    classify.py  email text -> rejection | interview_invite | assessment |
                 acknowledgement | other
    match.py     email + applications -> which application, or "needs review"
    run.py       the pipeline step: process pending rows, apply status effects

Tables: reads applications, jobs, companies; writes application_events and
applications. Runs as a step of the ingest workflow, after notify.
"""
