/**
 * job-agent — Gmail outcome capture and an independent pipeline watchdog.
 *
 * Runs in the operator's own Google account (docs/PLAN.md 4.2 and 9.2), so it
 * needs no OAuth app, no stored Google token, and nothing that expires weekly.
 * Setup is in docs/RUNBOOK.md, "Outcome capture".
 *
 * Script properties (Project Settings -> Script properties):
 *   DASHBOARD_URL     https://<your-vercel-app>.vercel.app   (no trailing path)
 *   DASHBOARD_SECRET  the dashboard secret (same as DASHBOARD_SECRET on Vercel)
 *   STALE_MINUTES     optional; watchdog threshold, default 360. Set it to 45
 *                     once the 15-minute dispatch trigger is live.
 *
 * Triggers (Triggers -> Add trigger, time-driven):
 *   syncOutcomes      every 30 minutes
 *   checkPipeline     every 30 minutes
 * Run once by hand:
 *   backfillOutcomes  sends the last 30 days, including Trash
 */

// A nested Gmail label "job/outcome" is searched as "job-outcome".
var LABEL_QUERY = 'label:job-outcome';
var BATCH = 100;

// Senders and subjects that identify ATS mail, for the one-off backfill over
// mail that was never labelled (and over Trash, where rejections went to die).
var ATS_QUERY =
  '(from:(greenhouse-mail.io OR greenhouse.io OR lever.co OR hire.lever.co OR ashbyhq.com ' +
  'OR myworkday.com OR smartrecruiters.com OR successfactors.com OR workablemail.com ' +
  'OR keka.com OR darwinbox.in OR icims.com) ' +
  'OR subject:(application OR candidacy OR interview OR assessment))';

function props_() {
  var p = PropertiesService.getScriptProperties();
  var url = (p.getProperty('DASHBOARD_URL') || '').replace(/\/+$/, '');
  var secret = p.getProperty('DASHBOARD_SECRET') || '';
  if (!url || !secret) throw new Error('Set DASHBOARD_URL and DASHBOARD_SECRET in Script properties');
  return { url: url, secret: secret, props: p };
}

/** Every message in the matching threads, as the shape /api/events expects. */
function collect_(query, max) {
  var events = [];
  var threads = GmailApp.search(query, 0, max || 200);
  threads.forEach(function (thread) {
    thread.getMessages().forEach(function (msg) {
      var body = (msg.getPlainBody() || '').replace(/\s+/g, ' ').trim();
      events.push({
        message_id: msg.getId(),
        received_at: msg.getDate().toISOString(),
        sender: msg.getFrom(),
        subject: msg.getSubject(),
        snippet: body.slice(0, 800),
      });
    });
  });
  return events;
}

function post_(events) {
  var cfg = props_();
  var stored = 0, dupes = 0;
  for (var i = 0; i < events.length; i += BATCH) {
    var res = UrlFetchApp.fetch(cfg.url + '/api/events', {
      method: 'post',
      contentType: 'application/json',
      headers: { 'x-dashboard-secret': cfg.secret },
      payload: JSON.stringify({ events: events.slice(i, i + BATCH) }),
      muteHttpExceptions: true,
    });
    if (res.getResponseCode() !== 200) {
      throw new Error('POST /api/events -> ' + res.getResponseCode() + ': ' +
                      res.getContentText().slice(0, 300));
    }
    var out = JSON.parse(res.getContentText());
    stored += out.stored; dupes += out.duplicates;
  }
  return { stored: stored, duplicates: dupes };
}

/**
 * Every 30 minutes. Re-sends the last two days of labelled mail rather than
 * marking threads as synced: Gmail labels are per thread, so a rejection that
 * arrives in a thread already marked would be skipped forever. The server
 * ignores messages it has seen (message_id is unique), so re-sending is free.
 */
function syncOutcomes() {
  var events = collect_(LABEL_QUERY + ' newer_than:2d', 200);
  if (events.length === 0) return;
  var r = post_(events);
  console.log('outcomes: ' + events.length + ' sent, ' + r.stored + ' new, ' + r.duplicates + ' already known');
}

/**
 * Run once by hand. The last 30 days of labelled mail, plus ATS-looking mail
 * from the inbox, archive and Trash -- Gmail empties Trash after 30 days, so
 * this is the only chance to recover the rejections that were deleted.
 * Unrelated mail it picks up is classified "other" and ignored server-side.
 */
function backfillOutcomes() {
  var queries = [
    LABEL_QUERY + ' newer_than:30d',
    ATS_QUERY + ' newer_than:30d',
    'in:trash ' + ATS_QUERY + ' newer_than:30d',
  ];
  var seen = {}, events = [];
  queries.forEach(function (q) {
    collect_(q, 500).forEach(function (e) {
      if (!seen[e.message_id]) { seen[e.message_id] = true; events.push(e); }
    });
  });
  var r = post_(events);
  console.log('backfill: ' + events.length + ' sent, ' + r.stored + ' new, ' + r.duplicates + ' already known');
}

/**
 * Every 30 minutes (docs/PLAN.md 9.2). A watchdog that shares nothing with the
 * pipeline it watches: not GitHub, not the workflow's email transport. It asks
 * the dashboard how old the newest ingest run is and mails the operator from
 * their own Gmail if it is stale or unreachable. At most one mail per 6 hours.
 */
function checkPipeline() {
  var cfg = props_();
  var stale = Number(cfg.props.getProperty('STALE_MINUTES') || 360);
  var problem = null, age = null;
  try {
    var res = UrlFetchApp.fetch(cfg.url + '/api/health', {
      headers: { 'x-dashboard-secret': cfg.secret }, muteHttpExceptions: true,
    });
    if (res.getResponseCode() !== 200) {
      problem = 'dashboard /api/health answered ' + res.getResponseCode();
    } else {
      age = JSON.parse(res.getContentText()).last_run_age_minutes;
      if (age === null) problem = 'no ingest run has ever been recorded';
      else if (age > stale) problem = 'newest ingest run is ' + Math.round(age) + ' min old (threshold ' + stale + ')';
    }
  } catch (err) {
    problem = 'could not reach the dashboard: ' + err;
  }
  if (!problem) return;

  var last = Number(cfg.props.getProperty('LAST_ALERT_MS') || 0);
  if (Date.now() - last < 6 * 3600 * 1000) { console.log('suppressed: ' + problem); return; }
  MailApp.sendEmail(Session.getActiveUser().getEmail(),
    '[job agent] pipeline looks dead',
    problem + '\n\nThis check runs in Google Apps Script, independent of GitHub, ' +
    'so it still fires when the workflow never starts.\n\n' +
    'Diagnose: docs/RUNBOOK.md, section 5 (trigger report) and the Actions tab.');
  cfg.props.setProperty('LAST_ALERT_MS', String(Date.now()));
}
