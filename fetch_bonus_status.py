#!/usr/bin/env python3
"""
fetch_bonus_status.py — pull the Bonus_Status_* in-app events and write
bonus_status_data.json for bonus_status.html.

Two creatives, one per segment, both live from 12 Aug 2026:

    S3_char_me_se_3   111 CSPs   one check short of qualifying
    S4_char_me_se_2   152 CSPs   two checks short

Every event the creative fires carries `segment`, so the two funnels separate
cleanly with no campaign-id attribution and nothing to intersect — unlike the
offer-education funnels in fetch_ct_data.py.

WHAT IS COUNTED
Unique CSPs, not events. A CSP who reopened the in-app three times is one person
at every step. `raw` alongside each step is the event volume, so a wide gap
between the two means the frequency cap is not doing its job.

The funnel is NOT clamped. A step can exceed the one above it: `Comm_Viewed` is
fired by the creative's own JS, so a device whose bridge was still injecting when
the page painted can miss it and still record the tap. Any such surplus is
reported as `orphans` rather than hidden.

Secrets: CLEVERTAP_ACCOUNT / CLEVERTAP_PASSCODE (eu1). Reuses fetch_ct_data for
creds and the export plumbing.
"""
import os, sys, json, collections
from datetime import datetime, timezone, timedelta
import fetch_ct_data as ct          # creds, export_event(), identity_of(), cspid_of()

IST = timezone(timedelta(hours=5, minutes=30))
START = "20260812"                  # both campaigns went live 12 Aug 2026
PREFIX = "Bonus_Status_"

SEGMENTS = [
    {"key": "S3_char_me_se_3", "label": "4 में से 3", "sub": "One check short", "size": 111},
    {"key": "S4_char_me_se_2", "label": "4 में से 2", "sub": "Two checks short", "size": 152},
]

# The funnel, in the order a CSP walks it. (event, key, label)
FUNNEL = [
    ("Comm_Viewed",   "viewed",   "Screen shown"),
    ("Next_Tapped",   "next",     "Tapped आगे"),
    ("Help_Viewed",   "help",     "Reached the help question"),
    ("Finale_Viewed", "finale",   "Reached the ending"),
    ("GoTo_SevaSthiti", "goto",   "Tapped through to सेवा स्थिति"),
]

# Page 2 — the comprehension readout. One event per option, so no prop parsing.
HELP = [
    ("Help_Understood", "understood",     "नहीं, सब समझ गया"),
    ("Help_Questions",  "some_questions", "हाँ, कुछ सवाल हैं"),
    ("Help_NeedCall",   "need_help",      "हाँ, बात करनी है"),
    ("Help_Skipped",    "skipped",        "छोड़ें"),
]

# Every distinct tap, for the click table.
CLICKS = [
    ("Next_Tapped",                      "आगे (page 1)"),
    ("Ring_Tapped",                      "The pulsing check"),
    ("Help_Understood",                  "समझ गया"),
    ("Help_Questions",                   "कुछ सवाल हैं"),
    ("Help_NeedCall",                    "बात करनी है"),
    ("Help_Skipped",                     "छोड़ें"),
    ("SevaSthiti_Primary_Tapped",        "सेवा स्थिति — primary button"),
    ("SevaSthiti_Link_Tapped",           "सेवा स्थिति — text link"),
    ("SevaSthiti_After_Callback_Tapped", "सेवा स्थिति — after callback"),
    ("Callback_Primary_Tapped",          "कॉल बैक — primary button"),
    ("Callback_Link_Tapped",             "कॉल बैक — text link"),
    ("OK_Tapped",                        "ठीक है"),
    ("Close_Tapped",                     "✕ closed"),
]

EXTRA = ["Callback_Requested", "DeepLink_Attempt", "Dismissed", "Self_Report"]


def here(f):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), f)


def pull(event):
    """-> {segment: {"users": set(csp_or_identity), "raw": n, "props": [ ... ]}}"""
    out = collections.defaultdict(lambda: {"users": set(), "raw": 0, "props": []})
    now = datetime.now(IST).strftime("%Y%m%d")
    n = 0
    for rec in ct.export_event(PREFIX + event, START, now):
        ident = ct.identity_of(rec)             # test shop already dropped
        if not ident:
            continue
        p = ct.props_of(rec)
        seg = str(p.get("segment") or "unknown")
        who = (ct.cspid_of(rec) or ident).strip().lower()
        b = out[seg]
        b["users"].add(who)
        b["raw"] += 1
        b["props"].append(p)
        n += 1
    print(f"  {PREFIX + event:38s} {n:6d} records, {len(out)} segment(s)")
    return out


def main():
    data = {ev: pull(ev) for ev in
            sorted({e for e, _, _ in FUNNEL} | {e for e, _, _ in HELP} |
                   {e for e, _ in CLICKS} | set(EXTRA))}

    cohorts = {}
    p = here("bonus_status_cohorts.json")
    if os.path.exists(p):
        cohorts = json.load(open(p))

    out_segments = []
    for seg in SEGMENTS:
        k = seg["key"]
        g = lambda ev: data.get(ev, {}).get(k, {"users": set(), "raw": 0, "props": []})

        viewed = g("Comm_Viewed")["users"]
        funnel = []
        for ev, key, label in FUNNEL:
            b = g(ev)
            funnel.append({"key": key, "label": label, "event": PREFIX + ev,
                           "users": len(b["users"]), "raw": b["raw"]})

        help_mix = [{"key": key, "label": label, "event": PREFIX + ev,
                     "users": len(g(ev)["users"])} for ev, key, label in HELP]
        answered = sum(h["users"] for h in help_mix if h["key"] != "skipped")

        clicks = [{"event": PREFIX + ev, "label": label,
                   "users": len(g(ev)["users"]), "raw": g(ev)["raw"]}
                  for ev, label in CLICKS]

        # the callback queue — csp_id only, never a phone number
        cb = g("Callback_Requested")
        callback_ids = sorted(x for x in cb["users"] if x.startswith("a0"))

        # which hand-off route actually ran, straight from production
        mech = collections.Counter(str(x.get("mechanism", "?"))
                                   for x in g("DeepLink_Attempt")["props"])
        # where patience ran out
        pages = collections.Counter(str(x.get("page", "?"))
                                    for x in g("Dismissed")["props"])

        reached = sorted(viewed)
        roster = set(cohorts.get(k, []))
        out_segments.append({
            "key": k, "label": seg["label"], "sub": seg["sub"],
            "size": seg["size"],
            "reached": len(viewed),
            "not_reached": max(0, seg["size"] - len(viewed)),
            "orphans": sorted(set(reached) - roster)[:20] if roster else [],
            "funnel": funnel,
            "help": help_mix,
            "help_answered": answered,
            "clicks": clicks,
            "callbacks": len(callback_ids),
            "callback_ids": callback_ids,
            "deeplink_mechanism": dict(mech),
            "dismiss_by_page": dict(pages),
        })

    totals = {
        "reached": sum(s["reached"] for s in out_segments),
        "audience": sum(s["size"] for s in out_segments),
        "callbacks": sum(s["callbacks"] for s in out_segments),
        "goto": sum(next(f["users"] for f in s["funnel"] if f["key"] == "goto")
                    for s in out_segments),
        "need_help": sum(next(h["users"] for h in s["help"] if h["key"] == "need_help")
                         for s in out_segments),
    }
    all_cb = sorted({i for s in out_segments for i in s["callback_ids"]})

    out = {
        "generated": datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
        "start_date": START,
        "cycle_end": "2026-08-31",
        "credit_on": "2026-09-01",
        "segments": out_segments,
        "totals": totals,
        "all_callback_ids": all_cb,
    }
    json.dump(out, open(here("bonus_status_data.json"), "w"),
              ensure_ascii=False, indent=1)
    print(f"\nwrote bonus_status_data.json — reached {totals['reached']}/{totals['audience']}, "
          f"{totals['callbacks']} callback requests, {totals['goto']} tapped through")


if __name__ == "__main__":
    main()
