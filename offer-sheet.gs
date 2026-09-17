/**
 * Wiom — Offer Education banner page · view + tap log
 *
 * Paired with offer.html in this repo:
 *   https://vikaswiom.github.io/wiom-offer-education-campaign/offer.html?cspId=<ID>
 *
 * Every beacon arrives as:
 *   ?flow=OFFER&event=view|ok&uid=<cspId>&app=CSP|TECH&page=offer&sid=<open id>&t=<ms>
 *
 * ONE ROW PER EVENT, on purpose. The ₹750 script dedups per CSP per day because
 * it answers "who asked for a callback". This one answers "how many times has
 * this CSP been taught this", so repeat views are the data, not noise. The only
 * thing suppressed is an exact replay of the same beacon (same sid + event + t),
 * which is a network retry rather than a second view.
 *
 * SETUP
 *   1. Open the tracking Google Sheet → Extensions → Apps Script.
 *   2. Select everything in Code.gs and paste ALL of this over it. Partial
 *      pastes leave helpers undefined — that is what cost several rounds on the
 *      ₹750 script, hence the single self-contained doGet.
 *   3. Save → Deploy → New deployment → type "Web app".
 *        Execute as: Me.      Who has access: ANYONE.
 *      Anything other than "Anyone" redirects to accounts.google.com and the
 *      rows are dropped silently.
 *   4. Copy the /exec URL into `var LOG = '...'` in offer.html.
 *
 * CHANGING THIS LATER: Save does NOT update the live URL. Deploy → Manage
 * deployments → ✏️ → Version: New version → Deploy. Picking "New deployment"
 * instead mints a DIFFERENT /exec URL and the page keeps writing to the old one.
 */
function doGet(e) {
  var p = (e && e.parameter) || {};

  /* ── Aggregate feed for a dashboard: counts only, never a csp_id ──────
     ?action=stats            -> JSON
     ?action=stats&callback=f -> JSONP (Apps Script's 302 breaks a plain
                                 cross-origin fetch, so a dashboard needs this) */
  if (String(p.action || '') === 'stats') {
    var ss0 = SpreadsheetApp.getActiveSpreadsheet();
    var sh0 = ss0.getSheetByName('Log');
    var out = {
      updated: new Date().toISOString(),
      totals: { views: 0, oks: 0, csps_viewed: 0, csps_tapped: 0 },
      by_app: {},
      days: []
    };
    if (sh0 && sh0.getLastRow() > 1) {
      var vals = sh0.getRange(2, 1, sh0.getLastRow() - 1, 5).getValues();
      var uv = {}, ut = {}, days = {}, apps = {};
      for (var i = 0; i < vals.length; i++) {
        var d  = vals[i][0];
        var ds = (d && typeof d.getTime === 'function')
               ? Utilities.formatDate(d, 'Asia/Kolkata', 'yyyy-MM-dd') : String(d);
        var id = String(vals[i][2] || '');
        var ap = String(vals[i][3] || 'CSP');
        var ev = String(vals[i][4] || '');
        if (!days[ds]) days[ds] = { d: ds, views: 0, oks: 0 };
        if (!apps[ap]) apps[ap] = { views: 0, oks: 0, csps: {} };
        if (ev === 'view') { out.totals.views++; days[ds].views++; apps[ap].views++; if (id) uv[id] = 1; }
        if (ev === 'ok')   { out.totals.oks++;   days[ds].oks++;   apps[ap].oks++;   if (id) ut[id] = 1; }
        if (id) apps[ap].csps[id] = 1;
      }
      var count = function (o) { var c = 0, k; for (k in o) if (o.hasOwnProperty(k)) c++; return c; };
      out.totals.csps_viewed = count(uv);
      out.totals.csps_tapped = count(ut);
      var k;
      for (k in apps) if (apps.hasOwnProperty(k)) {
        out.by_app[k] = { views: apps[k].views, oks: apps[k].oks, csps: count(apps[k].csps) };
      }
      for (k in days) if (days.hasOwnProperty(k)) out.days.push(days[k]);
      out.days.sort(function (x, y) { return x.d < y.d ? -1 : 1; });
    }
    var body = JSON.stringify(out);
    if (p.callback) {
      return ContentService.createTextOutput(p.callback + '(' + body + ')')
        .setMimeType(ContentService.MimeType.JAVASCRIPT);
    }
    return ContentService.createTextOutput(body).setMimeType(ContentService.MimeType.JSON);
  }

  /* ── Write one row ───────────────────────────────────────────────── */
  var event = String(p.event || '').trim().toLowerCase();
  if (event !== 'view' && event !== 'ok') {
    return ContentService.createTextOutput('bad-event');
  }

  var csp = String(p.uid || p.cspId || p.csp_id || p.csp || '').trim().substring(0, 80) || 'unknown';
  var app = String(p.app || 'CSP').trim().toUpperCase().substring(0, 12);
  if (app !== 'CSP' && app !== 'TECH') app = 'CSP';
  var page  = String(p.page  || 'offer').trim().substring(0, 40);
  var sid   = String(p.sid   || '').trim().substring(0, 40);
  var stamp = String(p.t     || '').trim().substring(0, 20);

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName('Log');
  if (!sh) {
    sh = ss.insertSheet('Log');
    sh.appendRow(['date', 'time (IST)', 'csp_id', 'app', 'event', 'page', 'sid', 't']);
    sh.setFrozenRows(1);
    sh.getRange(1, 1, 1, 8).setFontWeight('bold');
  }

  /* Replay guard. Not a dedup of repeat views — those are wanted. This only
     drops the exact same beacon arriving twice (a retried request), which is
     identified by sid + event + t being identical. Looks at the tail only. */
  if (sid && stamp) {
    var last = sh.getLastRow();
    if (last > 1) {
      var n    = Math.min(last - 1, 300);
      var from = last - n + 1;
      var tail = sh.getRange(from, 5, n, 4).getValues();   /* event, page, sid, t */
      for (var j = tail.length - 1; j >= 0; j--) {
        if (String(tail[j][2]) === sid && String(tail[j][0]) === event && String(tail[j][3]) === stamp) {
          return ContentService.createTextOutput('ok-replay');
        }
      }
    }
  }

  var now = new Date();
  sh.appendRow([
    Utilities.formatDate(now, 'Asia/Kolkata', 'yyyy-MM-dd'),
    Utilities.formatDate(now, 'Asia/Kolkata', 'HH:mm:ss'),
    csp, app, event, page, sid, stamp
  ]);
  return ContentService.createTextOutput('ok');
}
