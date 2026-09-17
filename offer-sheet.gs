/**
 * Wiom — CSP/technician education banner pages · view + tap log
 *
 * ONE script, ONE spreadsheet, ONE TAB PER CREATIVE. The two banner pages have
 * different event sets and different funnels, so interleaving them in a single
 * tab would force every reader to filter before they could count anything.
 *
 *   offer.html      page="offer"      tab "Log"        events: view, ok
 *   callnudge.html  page="callnudge"  tab "CallNudge"  events: view, call, later
 *
 *   https://vikaswiom.github.io/wiom-offer-education-campaign/offer.html?cspId=<ID>
 *   https://vikaswiom.github.io/wiom-offer-education-campaign/callnudge.html?cspId=<ID>
 *   ...&app=TECH on the technician app's banner.
 *
 * Every beacon arrives as:
 *   ?flow=OFFER&event=<...>&uid=<cspId>&app=CSP|TECH&page=<...>&oid=<open id>&t=<ms>
 *
 * THE OPEN-ID PARAMETER IS `oid`, NOT `sid`. Google's frontend reserves `sid`
 * on script.google.com and answers HTTP 400 to a session-id-shaped value before
 * doGet is ever called — no execution log, no catchable error, just a generic
 * Drive error page for the caller. Cost an afternoon; do not rename it back.
 *
 * ONE ROW PER EVENT, on purpose. Repeat exposure is the measurement, so nothing
 * is deduped except an exact beacon replay (same oid + event + t), which is a
 * network retry rather than a second view.
 *
 * ADDING A THIRD CREATIVE: add one entry to PAGES below. Nothing else changes —
 * the tab is created on the first event, and stats picks it up automatically.
 *
 * SETUP / REDEPLOY
 *   Extensions → Apps Script → select all in Code.gs → paste ALL of this → Save.
 *   First time : Deploy → New deployment → Web app → Execute as Me,
 *                Who has access ANYONE. Anything else silently drops rows.
 *   After that : Deploy → Manage deployments → ✏️ → Version: NEW VERSION.
 *                "New deployment" mints a DIFFERENT /exec URL and the pages
 *                keep writing to the old one.
 */
function doGet(e) {
 try {
  var p = (e && e.parameter) || {};

  /* The whole routing table. Event names are an allowlist per page, not a free
     text column: a typo would otherwise open a silent funnel nobody counts. */
  var PAGES = {
    offer:     { tab: 'Log',       events: { view: 1, ok: 1 } },
    callnudge: { tab: 'CallNudge', events: { view: 1, call: 1, later: 1 } }
  };
  var HEADER = ['date', 'time (IST)', 'csp_id', 'app', 'event', 'page', 'open_id', 't'];

  /* ── Self-test ────────────────────────────────────────────────────────
     ?action=ping -> what this is bound to, who it runs as, existing tabs, and
     whether it can actually WRITE. Reads can succeed while writes fail, and
     Google reports that as an unhelpful "unable to open the file" page. */
  if (String(p.action || '') === 'ping') {
    var out0 = { ok: true, pages: {} };
    var kp;
    for (kp in PAGES) if (PAGES.hasOwnProperty(kp)) out0.pages[kp] = PAGES[kp].tab;
    try {
      var s0 = SpreadsheetApp.getActiveSpreadsheet();
      out0.bound_to = s0 ? s0.getName() : null;
      out0.file_id  = s0 ? s0.getId()   : null;
      out0.tabs = [];
      var all = s0 ? s0.getSheets() : [];
      for (var z = 0; z < all.length; z++) out0.tabs.push(all[z].getName());
    } catch (e0) { out0.ok = false; out0.read_error = String(e0 && e0.message || e0); }
    try { out0.runs_as = Session.getEffectiveUser().getEmail(); } catch (e1) { out0.runs_as = 'unknown'; }
    try {
      var t0 = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('_ping')
            || SpreadsheetApp.getActiveSpreadsheet().insertSheet('_ping');
      t0.getRange(1, 1).setValue(new Date());
      out0.can_write = true;
    } catch (e2) { out0.can_write = false; out0.write_error = String(e2 && e2.message || e2); }
    return ContentService.createTextOutput(JSON.stringify(out0))
      .setMimeType(ContentService.MimeType.JSON);
  }

  /* ── Aggregate feed for a dashboard: counts only, never a csp_id ──────
     ?action=stats            -> JSON
     ?action=stats&callback=f -> JSONP (Apps Script's 302 breaks a plain
                                 cross-origin fetch, so a dashboard needs this)
     Counted per page AND per app, never merged: a combined number across two
     different creatives answers a question nobody asked. */
  if (String(p.action || '') === 'stats') {
    var ss0 = SpreadsheetApp.getActiveSpreadsheet();
    var out = { updated: new Date().toISOString(), by_page: {}, days: [] };
    var days = {};
    var mk = function () { return { rows: 0, events: {}, csps: 0, csps_by_event: {}, by_app: {}, _c: {}, _e: {} }; };
    var bump = function (b, ev, id) {
      b.rows++;
      if (!b.events[ev]) b.events[ev] = 0;
      b.events[ev]++;
      if (id && id !== 'unknown') {
        if (!b._c[id]) { b._c[id] = 1; b.csps++; }
        if (!b._e[ev]) b._e[ev] = {};
        if (!b._e[ev][id]) {
          b._e[ev][id] = 1;
          if (!b.csps_by_event[ev]) b.csps_by_event[ev] = 0;
          b.csps_by_event[ev]++;
        }
      }
    };
    var kq;
    for (kq in PAGES) if (PAGES.hasOwnProperty(kq)) {
      var shq = ss0.getSheetByName(PAGES[kq].tab);
      var bq  = mk();
      out.by_page[kq] = bq;
      if (!shq || shq.getLastRow() < 2) continue;
      var vals = shq.getRange(2, 1, shq.getLastRow() - 1, 6).getValues();
      for (var i = 0; i < vals.length; i++) {
        var d  = vals[i][0];
        var ds = (d && typeof d.getTime === 'function')
               ? Utilities.formatDate(d, 'Asia/Kolkata', 'yyyy-MM-dd') : String(d);
        var id = String(vals[i][2] || '');
        var ap = String(vals[i][3] || 'CSP');
        var ev = String(vals[i][4] || '');
        if (!ev) continue;
        bump(bq, ev, id);
        if (!bq.by_app[ap]) bq.by_app[ap] = mk();
        bump(bq.by_app[ap], ev, id);
        if (!days[ds]) days[ds] = { d: ds, pages: {} };
        if (!days[ds].pages[kq]) days[ds].pages[kq] = {};
        if (!days[ds].pages[kq][ev]) days[ds].pages[kq][ev] = 0;
        days[ds].pages[kq][ev]++;
      }
    }
    var strip = function (b) { delete b._c; delete b._e; var k;
      for (k in b.by_app) if (b.by_app.hasOwnProperty(k)) strip(b.by_app[k]); };
    var kr;
    for (kr in out.by_page) if (out.by_page.hasOwnProperty(kr)) strip(out.by_page[kr]);
    for (kr in days) if (days.hasOwnProperty(kr)) out.days.push(days[kr]);
    out.days.sort(function (x, y) { return x.d < y.d ? -1 : 1; });
    var body = JSON.stringify(out);
    if (p.callback) {
      return ContentService.createTextOutput(p.callback + '(' + body + ')')
        .setMimeType(ContentService.MimeType.JAVASCRIPT);
    }
    return ContentService.createTextOutput(body).setMimeType(ContentService.MimeType.JSON);
  }

  /* ── Write one row ───────────────────────────────────────────────── */
  var page = String(p.page || 'offer').trim().toLowerCase();
  if (!PAGES.hasOwnProperty(page)) return ContentService.createTextOutput('bad-page');

  var event = String(p.event || '').trim().toLowerCase();
  if (!PAGES[page].events.hasOwnProperty(event)) return ContentService.createTextOutput('bad-event');

  var csp = String(p.uid || p.cspId || p.csp_id || p.csp || '').trim().substring(0, 80) || 'unknown';
  var app = String(p.app || 'CSP').trim().toUpperCase().substring(0, 12);
  if (app !== 'CSP' && app !== 'TECH') app = 'CSP';
  var oid   = String(p.oid || p.sid || '').trim().substring(0, 40);
  var stamp = String(p.t   || '').trim().substring(0, 20);

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(PAGES[page].tab);
  if (!sh) {
    sh = ss.insertSheet(PAGES[page].tab);
    sh.appendRow(HEADER);
    sh.setFrozenRows(1);
    sh.getRange(1, 1, 1, HEADER.length).setFontWeight('bold');
  }

  /* Replay guard. NOT a dedup of repeat views — those are wanted. This only
     drops the exact same beacon arriving twice (a retried request), identified
     by oid + event + t being identical. Looks at the tail only. */
  if (oid && stamp) {
    var last = sh.getLastRow();
    if (last > 1) {
      var n    = Math.min(last - 1, 300);
      var from = last - n + 1;
      var tail = sh.getRange(from, 5, n, 4).getValues();   /* event, page, open_id, t */
      for (var j = tail.length - 1; j >= 0; j--) {
        if (String(tail[j][2]) === oid && String(tail[j][0]) === event && String(tail[j][3]) === stamp) {
          return ContentService.createTextOutput('ok-replay');
        }
      }
    }
  }

  var now = new Date();
  sh.appendRow([
    Utilities.formatDate(now, 'Asia/Kolkata', 'yyyy-MM-dd'),
    Utilities.formatDate(now, 'Asia/Kolkata', 'HH:mm:ss'),
    csp, app, event, page, oid, stamp
  ]);
  return ContentService.createTextOutput('ok');

 } catch (err) {
  /* Never let an exception fall through to Google's "Sorry, unable to open the
     file at present" page — that page is indistinguishable from a bad URL, a
     wrong account and a broken deployment, and it cost us a round trip. */
  return ContentService.createTextOutput('err: ' + String((err && err.message) || err));
 }
}

/**
 * RUN THIS FROM THE EDITOR to diagnose a write failure.
 *
 * Pick `testWrite` in the function dropdown at the top of the Apps Script
 * editor and press Run. Two things happen that a web-app request cannot do:
 *   1. If authorization is missing or too narrow, Google prompts for it here.
 *      A deployed web app cannot prompt — it just throws, and the caller gets
 *      an unhelpful HTML error page.
 *   2. The real exception appears in the execution log.
 * It also creates both tabs, taking insertSheet out of the request path.
 */
function testWrite() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  Logger.log('bound to : %s', ss ? ss.getName() : 'NOTHING — this script is not bound to a spreadsheet');
  try { Logger.log('runs as  : %s', Session.getEffectiveUser().getEmail()); } catch (e) { Logger.log('runs as  : unknown'); }

  var HEADER = ['date', 'time (IST)', 'csp_id', 'app', 'event', 'page', 'open_id', 't'];
  var tabs = ['Log', 'CallNudge'];
  for (var i = 0; i < tabs.length; i++) {
    var sh = ss.getSheetByName(tabs[i]);
    if (!sh) {
      sh = ss.insertSheet(tabs[i]);
      sh.appendRow(HEADER);
      sh.setFrozenRows(1);
      sh.getRange(1, 1, 1, HEADER.length).setFontWeight('bold');
      Logger.log('created tab %s', tabs[i]);
    } else {
      Logger.log('tab %s already existed', tabs[i]);
    }
  }
  Logger.log('WRITE OK — both tabs exist and are writable');
}
