#!/usr/bin/env python3
"""Read-only diagnostic: the campaigns UI says N CSPs saw the round-2 in-app, the
dashboard funnel says zero. Those two numbers come from different places, and
this prints all of them side by side so the gap can be attributed rather than
guessed at.

  1. Bonus_Seva2_* raw records   — how many exist at all, per day, and whose
                                   they are (identity present? which cspid?)
  2. inApp_Shown by campaign_id  — the app's own impression event
  3. Notification Viewed         — CleverTap's system impression counter

The three explanations this separates:
  * impressions but NO custom events at all  -> the creative renders but its JS
    never runs ("Include JavaScript" off, or a different creative is live)
  * custom events exist but no identity      -> they can never reach a funnel
  * custom events exist with identities      -> the fetcher window is wrong

Usage: python3 probe_round2.py [from_YYYYMMDD]
Read-only: prints to the job log, writes nothing.
"""
import sys, datetime
from collections import Counter, defaultdict

import fetch_ct_data as F

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
FRM = sys.argv[1] if len(sys.argv) > 1 else "20260730"
TO = datetime.datetime.now(IST).strftime("%Y%m%d")

R2 = "Bonus_Seva2_"
EVENTS = ["Story_Viewed", "Gift_Opened", "Card_Viewed", "Quality_Toggled",
          "Chip_Tapped", "Help_Tapped", "GoTo_SevaSthiti", "Flow_Completed", "Dismissed"]

print(f"probe round 2 · window {FRM} -> {TO} · region {F.REGION}")

print("\n--- 1) Bonus_Seva2_* raw records ---")
grand = 0
for name in EVENTS:
    per_day = Counter()
    cspids = Counter()
    triggers = Counter()
    with_id = no_id = 0
    sample = None
    for rec in F.export_event(R2 + name, FRM, TO):
        grand += 1
        per_day[str(rec.get("ts", ""))[:8]] += 1
        c = F.cspid_of(rec)
        cspids[c or "(no cspid)"] += 1
        p = rec.get("profile") or {}
        if p.get("identity") or p.get("objectId") or p.get("email"):
            with_id += 1
        else:
            no_id += 1
        triggers[str(F.props_of(rec).get("trigger", "") or "(none)")] += 1
        if sample is None:
            sample = {k: v for k, v in p.items() if k != "profileData"}
    total = sum(per_day.values())
    if not total:
        print(f"  {R2+name:34s} 0")
        continue
    print(f"  {R2+name:34s} {total:5d}  identity:{with_id} no-identity:{no_id}")
    print(f"      by day     {dict(sorted(per_day.items()))}")
    print(f"      by trigger {dict(triggers)}")
    print(f"      by cspid   {dict(cspids.most_common(6))}")
    if sample:
        print(f"      sample profile keys {sample}")
print(f"  TOTAL Bonus_Seva2_* records in window: {grand}")

print("\n--- 2) inApp_Shown by campaign_id (the app's own impression event) ---")
camp = Counter()
shown_ids = set()
n = 0
for rec in F.export_event("inApp_Shown", FRM, TO):
    n += 1
    camp[str(F.props_of(rec).get("campaign_id", "") or "(none)")] += 1
    i = F.identity_of(rec)
    if i:
        shown_ids.add(i)
print(f"  {n} inApp_Shown records, {len(shown_ids)} distinct real identities")
for cid, c in camp.most_common(12):
    print(f"    campaign_id {cid:20s} {c}")

print("\n--- 3) Notification Viewed (CleverTap's own impression counter) ---")
wz = Counter()
n = 0
for rec in F.export_event("Notification Viewed", FRM, TO):
    props = F.props_of(rec)
    if str(props.get("Campaign type", "")).lower() not in ("inapp", "in-app", ""):
        continue
    n += 1
    wz[str(props.get("wzrk_id", "") or "(none)")] += 1
print(f"  {n} in-app Notification Viewed records")
for k, c in wz.most_common(12):
    print(f"    wzrk_id {k:28s} {c}")

print("\n--- read this as ---")
print("  impressions high + Bonus_Seva2_* zero  -> creative renders, its JS never runs")
print("                                            (Include JavaScript off, or a different creative is live)")
print("  Bonus_Seva2_* present, no-identity     -> events can never reach a funnel")
print("  Bonus_Seva2_* present, identity, but   -> the dashboard window/filter is wrong")
print("     dashboard still zero")

print("\n--- 4) identity vs cspid: is one CSP one row, or several? ---")
from collections import defaultdict
ids_per_csp = defaultdict(set)
csp_per_id = defaultdict(set)
no_csp = 0
tot = 0
for rec in F.export_event(R2 + "Story_Viewed", FRM, TO):
    tot += 1
    p = rec.get("profile") or {}
    ident = p.get("identity") or p.get("objectId") or p.get("email")
    c = F.cspid_of(rec)
    if not c:
        no_csp += 1
        continue
    if ident:
        ids_per_csp[c].add(ident)
        csp_per_id[ident].add(c)
print(f"  Story_Viewed records: {tot}  (no cspid on {no_csp})")
print(f"  distinct identities : {len(csp_per_id)}")
print(f"  distinct cspids     : {len(ids_per_csp)}")
multi = {c: sorted(v) for c, v in ids_per_csp.items() if len(v) > 1}
print(f"  cspids with >1 identity: {len(multi)}")
for c, v in list(multi.items())[:8]:
    print(f"    {c} -> {v}")
shared = {i: sorted(v) for i, v in csp_per_id.items() if len(v) > 1}
print(f"  identities seen under >1 cspid: {len(shared)}")
for i, v in list(shared.items())[:5]:
    print(f"    identity {i} -> {v}")
print("  => if distinct cspids < distinct identities, the funnel currently")
print("     over-counts: one shop appearing as several users.")
