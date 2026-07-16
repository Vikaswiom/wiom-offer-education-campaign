#!/usr/bin/env python3
"""
fetch_ct_data.py  —  Pull CleverTap events for the Offer Education in-app and write
data.json for dashboard.html.  Two apps, one shared creative:

    CSP App          campaigns 1784201594 + 1784207437
    Technician App   campaign  1784203536

HOW THE SPLIT WORKS (important): only `inApp_Shown` carries campaign_id. The
CSP_Offer_* events are fired by the in-app HTML itself, which is identical in both
apps, so those events carry NO app or campaign marker. We therefore attribute them
by IDENTITY: the set of profiles shown an app's campaign is intersected with the set
of profiles that fired each downstream event. That is only possible because we use
the events export (which returns profile.identity) rather than the counts API.

Consequences worth knowing:
  * A profile that fired education/quiz but has no inApp_Shown record in either app
    cannot be attributed — it is reported as `unattributed` rather than silently
    dropped into one app's numbers.
  * A profile shown BOTH apps' campaigns would count in both funnels. CSPs and
    technicians are different people, so this should be ~0; it is reported if seen.

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
START_DATE = "20260716"                      # campaign launch (YYYYMMDD); override via argv[1]

APPS = [
    {"key": "csp",  "label": "CSP App",        "color": "#D9008D",
     "campaigns": ["1784201594", "1784207437"],
     "note": "Partner/CSP app — the original two campaigns."},
    {"key": "tech", "label": "Technician App", "color": "#2563EB",
     "campaigns": ["1784203536"],
     "note": "Technician app — same creative, separate campaign."},
]

# funnel event name -> data key. Only inApp_Shown is campaign-filtered; the rest are
# attributed by identity (see module docstring).
FUNNEL = [
    ("inApp_Shown",                    "shown"),
    ("CSP_Offer_Education_OK_Clicked", "edu_ok"),
    ("CSP_Offer_Quiz_Answered",        "completed"),
]
STEP_LABELS = {
    "shown":     "Shown (in-app)",
    "edu_ok":    "Read education → tapped ठीक है",
    "completed": "Completed (education + quiz)",
}

# All three options are IDENTICAL — deliberately. The CSP reads the same sentence
# whichever one they tap, so there is nothing to compare and nothing to get wrong.
# That means `option` records only WHICH POSITION was tapped, not which wording.
OPTION_ANSWER = "आपका सही अमाउंट आपके व्योम ऐप में दिखेगा, वही फाइनल अमाउंट होगा।"
OPTION_LABEL = {"1": "1st (top)", "2": "2nd (middle)", "3": "3rd (bottom)"}

# Technician workaround: campaign 1784203536 reports no inApp_Shown, so campaign
# attribution can never populate the Technician funnel. Instead, every funnel event
# is filtered by this profile property (matched case-insensitively on key and value).
# NOTE: this funnel is NOT a locked cohort — a step can exceed "shown" if technician
# impressions are under-reported.
ROLE_PROP = "role"
TECH_ROLE_VALUE = "technician"

ALL_CAMPAIGNS = [c for a in APPS for c in a["campaigns"]]

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
    """Yield every event record {profile, ts, event_props} for the date range.

    CleverTap 400s on an event name it has never seen. That is the normal state for a
    freshly renamed event before the campaign is republished, so treat it as zero
    rather than crashing the whole refresh.
    """
    url = f"{BASE}/1/events.json?batch_size=5000"
    try:
        resp = _req(url, method="POST", body={"event_name": event_name, "from": int(frm), "to": int(to)})
    except urllib.error.HTTPError as e:
        if e.code == 400:
            print(f"  {event_name:34s} -> unknown to CleverTap yet (HTTP 400), counting as 0")
            return
        raise
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

def day_of(rec):
    d = str(rec.get("ts", ""))[:8]              # ts = YYYYMMDDHHMMSS, not epoch
    return d if len(d) == 8 else None

def campaign_of(rec):
    """Which tracked campaign this inApp_Shown belongs to, or None. Matched on
    'contains' because campaign_id can arrive bare or wrapped in a longer string."""
    v = str(props_of(rec).get("campaign_id", ""))
    for c in ALL_CAMPAIGNS:
        if c in v:
            return c
    return None

def is_tech(rec):
    """True when the record's profile carries ROLE_PROP == TECH_ROLE_VALUE.
    The events export returns custom profile fields under profile.profileData."""
    pd = (rec.get("profile") or {}).get("profileData") or {}
    for k, v in pd.items():
        if str(k).strip().lower() == ROLE_PROP:
            return str(v).strip().lower() == TECH_ROLE_VALUE
    return False

# ---- pull -------------------------------------------------------------------
def main():
    frm = sys.argv[1] if len(sys.argv) > 1 else START_DATE
    to = datetime.date.today().strftime("%Y%m%d")
    print(f"CleverTap {REGION} · {frm} -> {to}")
    for a in APPS:
        print(f"  {a['label']:16s} campaigns {' + '.join(a['campaigns'])}")

    # ---- pass 1: raw collection, no attribution yet ----
    shown_by_cmp   = {c: set() for c in ALL_CAMPAIGNS}    # campaign -> set(identity)
    shown_day      = {c: {} for c in ALL_CAMPAIGNS}       # campaign -> day -> set(identity)
    edu_idents     = set()                                # identity
    comp_records   = []                                   # (identity, day, option)

    # technician workaround: role-filtered collection, independent of campaign ids
    tech           = {"shown": set(), "edu_ok": set(), "completed": set()}
    tech_daily     = {}                                   # day -> {"shown","completed"} sets
    tech_opts      = {"1": set(), "2": set(), "3": set()}
    tech_cmp_seen  = {}                                   # raw campaign_id -> set(identity)

    for event_name, key in FUNNEL:
        n = kept = 0
        for rec in export_event(event_name, frm, to):
            n += 1
            ident = identity_of(rec)
            if not ident:
                continue
            if key == "shown":
                if is_tech(rec):
                    tech["shown"].add(ident)
                    d = day_of(rec)
                    if d:
                        tech_daily.setdefault(d, {"shown": set(), "completed": set()})["shown"].add(ident)
                    raw = str(props_of(rec).get("campaign_id", "")) or "(no campaign_id)"
                    tech_cmp_seen.setdefault(raw, set()).add(ident)
                cid = campaign_of(rec)
                if not cid:
                    continue                              # some other campaign's impression
                shown_by_cmp[cid].add(ident); kept += 1
                d = day_of(rec)
                if d:
                    shown_day[cid].setdefault(d, set()).add(ident)
            elif key == "edu_ok":
                if is_tech(rec):
                    tech["edu_ok"].add(ident)
                edu_idents.add(ident); kept += 1
            else:
                o = str(props_of(rec).get("option", ""))
                if is_tech(rec):
                    tech["completed"].add(ident)
                    if o in tech_opts:
                        tech_opts[o].add(ident)
                    d = day_of(rec)
                    if d:
                        tech_daily.setdefault(d, {"shown": set(), "completed": set()})["completed"].add(ident)
                comp_records.append((ident, day_of(rec), o))
                kept += 1
        uniq_note = {"shown": len(set().union(*shown_by_cmp.values())) if shown_by_cmp else 0,
                     "edu_ok": len(edu_idents),
                     "completed": len({i for i, _, _ in comp_records})}[key]
        print(f"  {event_name:34s} -> {n} events, {uniq_note} unique users")

    comp_idents = {i for i, _, _ in comp_records}

    # ---- pass 2: attribute downstream events to an app by identity ----
    apps_out = []
    for a in APPS:
        a_shown = set().union(*[shown_by_cmp[c] for c in a["campaigns"]]) if a["campaigns"] else set()
        a_edu   = edu_idents & a_shown
        a_comp  = comp_idents & a_shown

        a_daily = {}
        for c in a["campaigns"]:
            for d, idents in shown_day[c].items():
                a_daily.setdefault(d, {"shown": set(), "completed": set()})["shown"] |= idents
        for ident, d, _ in comp_records:
            if d and ident in a_shown:
                a_daily.setdefault(d, {"shown": set(), "completed": set()})["completed"].add(ident)

        a_opts = {"1": set(), "2": set(), "3": set()}
        for ident, _, o in comp_records:
            if ident in a_shown and o in a_opts:
                a_opts[o].add(ident)

        apps_out.append({
            "key": a["key"], "label": a["label"], "color": a["color"], "note": a["note"],
            "campaigns": a["campaigns"],
            "shown_by_campaign": [{"id": c, "users": len(shown_by_cmp[c])} for c in a["campaigns"]],
            "funnel": {"shown": len(a_shown), "edu_ok": len(a_edu), "completed": len(a_comp)},
            "options": [{"option": o, "label": OPTION_LABEL[o], "users": len(a_opts[o])} for o in ("1", "2", "3")],
            "daily": [{"date": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
                       "shown": len(a_daily[d]["shown"]),
                       "completed": len(a_daily[d]["completed"])} for d in sorted(a_daily)],
        })

    # technician workaround: overwrite the tech app's campaign-locked numbers (always
    # zero — campaign 1784203536 reports no impressions) with the role-filtered funnel
    tech_idents = tech["shown"] | tech["edu_ok"] | tech["completed"]
    seen = ", ".join(f"{cid} ({len(s)})" for cid, s in
                     sorted(tech_cmp_seen.items(), key=lambda kv: -len(kv[1]))[:5]) or "none"
    for a_out in apps_out:
        if a_out["key"] != "tech":
            continue
        a_out["attribution"] = "role"
        a_out["note"] = ("Technician app — attributed by profile property role = technician "
                         "on every event (campaign attribution unusable: campaign "
                         f"{'/'.join(a_out['campaigns'])} reports no impressions). "
                         f"Campaign ids observed on technicians' inApp_Shown: {seen}.")
        a_out["funnel"] = {k: len(tech[k]) for k in ("shown", "edu_ok", "completed")}
        a_out["options"] = [{"option": o, "label": OPTION_LABEL[o], "users": len(tech_opts[o])}
                            for o in ("1", "2", "3")]
        a_out["daily"] = [{"date": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
                           "shown": len(tech_daily[d]["shown"]),
                           "completed": len(tech_daily[d]["completed"])} for d in sorted(tech_daily)]

    # profiles that acted but belong to no funnel above (no tracked inApp_Shown, not
    # role = technician either)
    all_shown = set().union(*shown_by_cmp.values()) if shown_by_cmp else set()
    unattributed = {"edu_ok": len(edu_idents - all_shown - tech_idents),
                    "completed": len(comp_idents - all_shown - tech_idents)}
    # overlap between the two funnels' cohorts (CSP campaign-shown vs technician role)
    both_apps = len(
        (set().union(*[shown_by_cmp[c] for c in APPS[0]["campaigns"]])) & tech_idents
    ) if len(APPS) == 2 else 0

    out = {
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sample": False,
        "region": REGION,
        "start_date": f"{frm[:4]}-{frm[4:6]}-{frm[6:8]}",
        "funnel_steps": [[k, STEP_LABELS[k], ev] for ev, k in FUNNEL],
        "option_answer": OPTION_ANSWER,   # the single sentence all 3 options show
        "apps": apps_out,
        "unattributed": unattributed,
        "both_apps": both_apps,
    }
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("wrote", path)

    p = lambda x, y: round(100 * x / y) if y else 0
    for a in apps_out:
        f = a["funnel"]
        tag = " (by role)" if a.get("attribution") == "role" else ""
        print(f"  {a['label'] + tag:26s} shown={f['shown']:4d}  edu_ok={f['edu_ok']:4d} ({p(f['edu_ok'],f['shown'])}%)  "
              f"completed={f['completed']:4d} ({p(f['completed'],f['shown'])}%)")
        if a.get("attribution") == "role":
            # not a locked cohort: role-filtered steps are independent, so a later step
            # exceeding "shown" means technician impressions are under-reported, not a bug
            if f["edu_ok"] > f["shown"] or f["completed"] > f["shown"]:
                print(f"  WARNING: {a['label']}: funnel steps exceed shown — "
                      "technician inApp_Shown is under-reported")
            continue
        # A locked cohort can only shrink: nobody may appear at a later step who was not shown.
        assert f["edu_ok"] <= f["shown"], f"{a['label']}: edu_ok {f['edu_ok']} > shown {f['shown']}"
        assert f["completed"] <= f["edu_ok"] or f["completed"] <= f["shown"], \
            f"{a['label']}: completed {f['completed']} exceeds its cohort"
    print(f"  technician inApp_Shown campaign ids: {seen}")
    if unattributed["edu_ok"] or unattributed["completed"]:
        print(f"  unattributed (acted but no inApp_Shown in either app): "
              f"edu_ok={unattributed['edu_ok']}  completed={unattributed['completed']}")
    if both_apps:
        print(f"  WARNING: {both_apps} profile(s) shown BOTH apps — counted in both funnels")

if __name__ == "__main__":
    main()
