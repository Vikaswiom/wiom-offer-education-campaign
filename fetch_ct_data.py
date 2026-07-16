#!/usr/bin/env python3
"""
fetch_ct_data.py  —  Pull CleverTap events for the Offer Education in-app
(campaign 1784201594) and write data.json for dashboard.html.

WHY events export (not counts API): /1/events.json returns raw events with
profile.identity, so we can dedupe to UNIQUE users per event and split by the
`option` prop. The counts API returns taps (~2.4x users) and can't split cleanly.
(See the CleverTap poller-campaign reference.)

CREDS (never commit these): set env vars, or put them in C:\\credentials\\.env
  CLEVERTAP_ACCOUNT=...        # X-CleverTap-Account-Id
  CLEVERTAP_PASSCODE=...       # X-CleverTap-Passcode
  CLEVERTAP_REGION=eu1         # Wiom = eu1

USAGE:
  python fetch_ct_data.py                 # from START_DATE (below) to today
  python fetch_ct_data.py 20260716        # override the start date (YYYYMMDD)

Then:  git commit -am "refresh dashboard" && git push   (GitHub Pages serves data.json)
"""
import os, sys, json, time, datetime, urllib.request, urllib.error

# ---- config -----------------------------------------------------------------
CAMPAIGN_ID = "1784201594"                   # offer-education in-app campaign
START_DATE  = "20260716"                     # campaign launch (YYYYMMDD); override via argv[1]
ACCENT      = "#D9008D"

# funnel event name -> data key.  inApp_Shown is filtered to CAMPAIGN_ID; the
# CSP_Offer_* events are unique to this in-app so they need no filter.
FUNNEL = [
    ("inApp_Shown",                    "shown"),
    ("CSP_Offer_Education_OK_Clicked", "edu_ok"),
    ("CSP_Offer_Quiz_Answered",        "quiz_answered"),
    ("CSP_Offer_Quiz_Closed",          "quiz_closed"),
]
STEP_LABELS = {
    "shown":         "Shown (in-app)",
    "edu_ok":        "Tapped ठीक है",
    "quiz_answered": "Answered quiz",
    "quiz_closed":   "Tapped ठीक है, समझ गया",
}
# All three options carry the correct message — this is a preference split, not a score.
OPTION_TEXT = {
    "1": "आपका सही अमाउंट आपके व्योम ऐप में दिखेगा, वही फाइनल अमाउंट होगा।",
    "2": "जो अमाउंट ऐप में दिख रहा है, बस वही देना होगा।",
    "3": "अमाउंट मैं नहीं तय करता — आपका ऐप दिखाता है।",
}

# ---- creds ------------------------------------------------------------------
def load_creds():
    acc = os.environ.get("CLEVERTAP_ACCOUNT"); pas = os.environ.get("CLEVERTAP_PASSCODE")
    reg = os.environ.get("CLEVERTAP_REGION", "eu1")
    if not (acc and pas):
        envf = r"C:\credentials\.env"
        if os.path.exists(envf):
            for line in open(envf, encoding="utf-8"):
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1); k = k.strip(); v = v.strip().strip('"').strip("'")
                    if k == "CLEVERTAP_ACCOUNT" and not acc: acc = v
                    if k == "CLEVERTAP_PASSCODE" and not pas: pas = v
                    if k == "CLEVERTAP_REGION": reg = v or reg
    if not (acc and pas):
        sys.exit("ERROR: set CLEVERTAP_ACCOUNT and CLEVERTAP_PASSCODE (env or C:\\credentials\\.env)")
    return acc, pas, reg

ACCOUNT, PASSCODE, REGION = load_creds()
BASE = f"https://{REGION}.api.clevertap.com"

REQ_TIMEOUT = 35      # per-request; CloudFront 504s at ~30s, so don't wait longer
MAX_ATTEMPTS = 3      # fail fast: a 504 outage shouldn't burn minutes per call

def _req(url, method="GET", body=None):
    headers = {"X-CleverTap-Account-Id": ACCOUNT, "X-CleverTap-Passcode": PASSCODE}
    data = None
    if body is not None:
        data = json.dumps(body).encode(); headers["Content-Type"] = "application/json"  # POST only
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    last = "unknown error"
    for attempt in range(MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=REQ_TIMEOUT) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code == 429 or e.code >= 500:      # transient (incl. CleverTap 504) -> retry
                time.sleep(1.5 * (attempt + 1)); continue
            raise                                    # 4xx (bad creds/params) -> fail immediately
        except Exception as e:
            last = f"{type(e).__name__}: {e}"; time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"CleverTap API unreachable after {MAX_ATTEMPTS} attempts ({last}): {url}")

def export_event(event_name, frm, to):
    """Yield every event record {profile, ts, event_props} for the date range."""
    url = f"{BASE}/1/events.json?batch_size=5000"
    resp = _req(url, method="POST", body={"event_name": event_name, "from": int(frm), "to": int(to)})
    cursor = resp.get("cursor")
    seen_any = False
    while cursor:
        page = _req(f"{BASE}/1/events.json?cursor={cursor}", method="GET")  # GET: no Content-Type
        recs = page.get("records") or []
        for rec in recs:
            seen_any = True; yield rec
        cursor = page.get("cursor")
        if not recs:
            break
    if not seen_any:
        for rec in (resp.get("records") or []):
            yield rec

def identity_of(rec):
    p = rec.get("profile") or {}
    return p.get("identity") or p.get("objectId") or p.get("email") or None

def props_of(rec):
    return rec.get("event_props") or {}

def in_campaign(rec):
    """inApp_Shown carries campaign_id in event_props; match on 'contains'."""
    return CAMPAIGN_ID in str(props_of(rec).get("campaign_id", ""))

# ---- pull -------------------------------------------------------------------
def main():
    frm = sys.argv[1] if len(sys.argv) > 1 else START_DATE
    to = datetime.date.today().strftime("%Y%m%d")
    print(f"CleverTap {REGION} · campaign {CAMPAIGN_ID} · {frm} -> {to}")

    uniq  = {k: set() for _, k in FUNNEL}          # key -> set(identity)
    daily = {}                                     # day -> {shown:set, quiz_answered:set}
    opts  = {"1": set(), "2": set(), "3": set()}   # option -> set(identity)

    for event_name, key in FUNNEL:
        n = kept = 0
        for rec in export_event(event_name, frm, to):
            n += 1
            if key == "shown" and not in_campaign(rec):
                continue                            # another campaign's impression
            ident = identity_of(rec)
            if not ident:
                continue
            uniq[key].add(ident); kept += 1
            if key in ("shown", "quiz_answered"):
                day = str(rec.get("ts", ""))[:8]    # ts = YYYYMMDDHHMMSS, not epoch
                if len(day) == 8:
                    daily.setdefault(day, {"shown": set(), "quiz_answered": set()})[key].add(ident)
            if key == "quiz_answered":
                o = str(props_of(rec).get("option", ""))
                if o in opts:
                    opts[o].add(ident)
        note = f" ({kept} in campaign {CAMPAIGN_ID})" if key == "shown" else ""
        print(f"  {event_name:32s} -> {n} events{note}, {len(uniq[key])} unique users")

    funnel = {k: len(uniq[k]) for _, k in FUNNEL}
    days = sorted(daily.keys())
    daily_list = [{"date": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
                   "shown": len(daily[d]["shown"]),
                   "answered": len(daily[d]["quiz_answered"])} for d in days]

    option_list = [{"option": o, "text": OPTION_TEXT[o], "users": len(opts[o])} for o in ("1", "2", "3")]

    out = {
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sample": False,
        "region": REGION,
        "campaign_id": CAMPAIGN_ID,
        "accent": ACCENT,
        "start_date": f"{frm[:4]}-{frm[4:6]}-{frm[6:8]}",
        "funnel_steps": [[k, STEP_LABELS[k], ev] for ev, k in FUNNEL],
        "funnel": funnel,
        "options": option_list,
        "daily": daily_list,
    }
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("wrote", path)

    s, e = funnel["shown"], funnel["edu_ok"]
    a, c = funnel["quiz_answered"], funnel["quiz_closed"]
    p = lambda x, y: round(100 * x / y) if y else 0
    print(f"  shown={s}  edu_ok={e} ({p(e,s)}%)  answered={a} ({p(a,e)}% of edu_ok)  closed={c} ({p(c,a)}% of answered)")
    if a and sum(len(opts[o]) for o in opts) == 0:
        print("  WARNING: quiz answers found but no `option` prop matched — check the prop name/values.")

if __name__ == "__main__":
    main()
