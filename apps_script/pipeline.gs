/**
 * job-agent -- standby trigger, job-alert forwarding and one-click install.
 * Lives beside outcomes.gs in the same Apps Script project (decided 2026-10-15).
 *
 * Extra script properties (install() sets them from _local_config.gs):
 *   GH_DISPATCH_PAT   token that may dispatch ingest.yml (same as cron-job.org)
 *   STANDBY_MINUTES   optional, default 25: dispatch when the newest run is older
 *   STALE_MINUTES     optional, default 45: checkPipeline alerts past this
 */

var REPO = 'harsh-m-10/job-discovery-agent';
var WORKFLOW = 'ingest.yml';
// Gmail searches the nested label "job/alerts" as "job-alerts".
var ALERT_QUERY = 'label:job-alerts';
// Vercel rejects request bodies over 4.5 MB; alert mails are long HTML.
var ALERT_BATCH_BYTES = 3 * 1024 * 1024;

function gh_(path, options) {
  var cfg = props_();
  var token = cfg.props.getProperty('GH_DISPATCH_PAT') || '';
  if (!token) throw new Error('Set GH_DISPATCH_PAT in Script properties');
  options = options || {};
  options.headers = {
    Authorization: 'Bearer ' + token,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
  };
  options.muteHttpExceptions = true;
  return UrlFetchApp.fetch('https://api.github.com/repos/' + REPO + path, options);
}

/** At most one mail per key per 6 hours, from the operator's own Gmail. */
function alertOnce_(key, subject, body) {
  var p = PropertiesService.getScriptProperties();
  var last = Number(p.getProperty('LAST_ALERT_MS_' + key) || 0);
  if (Date.now() - last < 6 * 3600 * 1000) { console.log('suppressed: ' + subject); return; }
  MailApp.sendEmail(Session.getActiveUser().getEmail(), subject, body);
  p.setProperty('LAST_ALERT_MS_' + key, String(Date.now()));
}

/** Minutes since the newest automated ingest run: the dashboard first (the
 *  same check checkPipeline makes), GitHub's own run list if that fails. */
function lastRunAgeMinutes_() {
  var cfg = props_();
  try {
    var res = UrlFetchApp.fetch(cfg.url + '/api/health', {
      headers: { 'x-dashboard-secret': cfg.secret }, muteHttpExceptions: true,
    });
    if (res.getResponseCode() === 200) {
      var age = JSON.parse(res.getContentText()).last_run_age_minutes;
      if (age !== null && age !== undefined) return { age: age, via: 'dashboard' };
    }
  } catch (err) { console.log('health unreachable: ' + err); }
  var r = gh_('/actions/workflows/' + WORKFLOW + '/runs?per_page=1');
  if (r.getResponseCode() === 401) return { age: null, via: 'github', unauthorized: true };
  var runs = JSON.parse(r.getContentText()).workflow_runs || [];
  if (!runs.length) return { age: Infinity, via: 'github' };
  return { age: (Date.now() - Date.parse(runs[0].created_at)) / 60000, via: 'github' };
}

/**
 * Every 15 minutes. The STANDBY trigger: cron-job.org (:00/:15/:30/:45) and
 * the Cloudflare Worker (:07/:22/:37/:52) dispatch unconditionally; this one
 * dispatches only when the newest run is older than STANDBY_MINUTES, i.e.
 * when both of them have stopped. Runs it fires say source=apps-script.
 * A 401 from GitHub means the shared token is dead -- every external trigger
 * is failing at once -- and is mailed immediately.
 */
function standbyDispatch() {
  var cfg = props_();
  var limit = Number(cfg.props.getProperty('STANDBY_MINUTES') || 25);
  var last = lastRunAgeMinutes_();
  if (last.unauthorized) {
    alertOnce_('401', '[job agent] GitHub rejected the dispatch token (401)',
      'The token cron-job.org, the Cloudflare Worker and this standby share was ' +
      'rejected. No trigger can start the pipeline until it is replaced.\n\n' +
      'docs/RUNBOOK.md, section 6.');
    return;
  }
  if (last.age !== null && last.age <= limit) {
    console.log('standby: newest run ' + Math.round(last.age) + ' min old (' + last.via + '); nothing to do');
    return;
  }
  var res = gh_('/actions/workflows/' + WORKFLOW + '/dispatches', {
    method: 'post', contentType: 'application/json',
    payload: JSON.stringify({ ref: 'main', inputs: { source: 'apps-script' } }),
  });
  var code = res.getResponseCode();
  console.log('standby: newest run ' + last.age + ' min old -> dispatch HTTP ' + code);
  if (code === 401) {
    alertOnce_('401', '[job agent] GitHub rejected the dispatch token (401)',
      'The standby tried to start a stale pipeline and GitHub answered 401. ' +
      'Every external trigger shares this token.\n\ndocs/RUNBOOK.md, section 6.');
  } else if (code !== 204) {
    alertOnce_('dispatch', '[job agent] standby dispatch failed (' + code + ')',
      res.getContentText().slice(0, 500));
  }
}

/**
 * Every 15 minutes. Forwards LinkedIn / Naukri / Indeed job-alert mail under
 * the label job/alerts to the dashboard's /api/alerts; ingest/alert_parse.py
 * parses it on the next ingest run. Re-sends two days of mail each time --
 * message_id is unique server-side, so duplicates cost nothing.
 */
function syncAlerts() {
  var cfg = props_();
  var threads = GmailApp.search(ALERT_QUERY + ' newer_than:2d', 0, 100);
  var batch = [], size = 0, sent = 0, stored = 0;
  function flush() {
    if (!batch.length) return;
    var res = UrlFetchApp.fetch(cfg.url + '/api/alerts', {
      method: 'post', contentType: 'application/json',
      headers: { 'x-dashboard-secret': cfg.secret },
      payload: JSON.stringify({ alerts: batch }), muteHttpExceptions: true,
    });
    if (res.getResponseCode() !== 200) {
      throw new Error('POST /api/alerts -> ' + res.getResponseCode() + ': ' +
                      res.getContentText().slice(0, 300));
    }
    stored += JSON.parse(res.getContentText()).stored;
    sent += batch.length; batch = []; size = 0;
  }
  threads.forEach(function (thread) {
    thread.getMessages().forEach(function (msg) {
      var a = {
        message_id: msg.getId(), received_at: msg.getDate().toISOString(),
        sender: msg.getFrom(), subject: msg.getSubject(),
        body: (msg.getBody() || msg.getPlainBody() || '').slice(0, 400000),
      };
      var bytes = a.body.length * 2 + 1000;
      if (size + bytes > ALERT_BATCH_BYTES || batch.length >= 50) flush();
      batch.push(a); size += bytes;
    });
  });
  flush();
  if (sent) console.log('alerts: ' + sent + ' sent, ' + stored + ' new');
}

/**
 * Run ONCE from the editor (Run -> install). Copies _local_config.gs into
 * Script properties, replaces this project's triggers with the full set, and
 * runs the 30-day outcome backfill -- Gmail empties Trash after 30 days.
 */
function install() {
  var p = PropertiesService.getScriptProperties();
  if (typeof localConfig_ === 'function') {
    var c = localConfig_();
    Object.keys(c).forEach(function (k) { if (c[k]) p.setProperty(k, String(c[k])); });
    console.log('script properties set: ' + Object.keys(c).join(', '));
  }
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('standbyDispatch').timeBased().everyMinutes(15).create();
  ScriptApp.newTrigger('syncAlerts').timeBased().everyMinutes(15).create();
  ScriptApp.newTrigger('syncOutcomes').timeBased().everyMinutes(30).create();
  ScriptApp.newTrigger('checkPipeline').timeBased().everyMinutes(30).create();
  console.log('triggers: standbyDispatch 15m, syncAlerts 15m, syncOutcomes 30m, checkPipeline 30m');
  backfillOutcomes();
  standbyDispatch();
}
