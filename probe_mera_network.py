#!/usr/bin/env python3
"""Read-only probe: what do CleverTap's OWN in-app events carry for one campaign?

The मेरा नेटवर्क creative live in CleverTap is the untracked build, so no
MeraNetwork_Edu_* events exist — but the SDK's own inApp_Shown / inApp_Dismissed
are there. This prints, per event name:

  * how many records mention the campaign id at all
  * the union of prop keys (so we can see what is available to split on)
  * the distinct values of every low-cardinality prop, which is where a
    समझ गया tap has to separate from a ✕ or a back-press if it separates anywhere
  * a few whole sample records, profile stripped

Nothing is written and no PII is printed — identities are reduced to a count.

Usage: python3 probe_mera_network.py [campaign_id] [from_YYYYMMDD]
"""
import json, sys, datetime
from collections import Counter, defaultdict

import fetch_ct_data as F   # creds, _req, export_event, helpers

CID = sys.argv[1] if len(sys.argv) > 1 else "1790081258"
FRM = sys.argv[2] if len(sys.argv) > 2 else "20260922"
TO = datetime.date.today().strftime("%Y%m%d")

# Capitalisation is not guaranteed across SDK versions, so try both. An unknown
# name comes back as HTTP 400 and export_event() already treats that as zero.
EVENTS = [
    "inApp_Shown", "InApp_Shown",
    "inApp_Dismissed", "InApp_Dismissed",
    "inApp_Clicked", "InApp_Clicked",
    "Notification Viewed", "Notification Clicked",
]

print(f"probe campaign {CID} · window {FRM} -> {TO} · region {F.REGION}")

for ev in EVENTS:
    try:
        total = hits = 0
        keys = Counter()
        values = defaultdict(Counter)
        users = set()
        samples = []
        for rec in F.export_event(ev, FRM, TO):
            total += 1
            props = F.props_of(rec)
            blob = json.dumps(props, ensure_ascii=False)
            if CID not in blob:
                continue
            hits += 1
            keys.update(props.keys())
            for k, v in props.items():
                values[k][str(v)[:60]] += 1
            ident = F.identity_of(rec)
            if ident:
                users.add((F.cspid_of(rec) or ident).strip().lower())
            if len(samples) < 3:
                samples.append({"ts": rec.get("ts"), "props": props})
        if not total:
            continue
        print(f"\n=== {ev}: {total} records in window, {hits} for {CID}, {len(users)} unique CSPs")
        if keys:
            print("  prop keys:", ", ".join(f"{k} x{v}" for k, v in keys.most_common(20)))
        for k, c in values.items():
            if len(c) <= 12:                      # low cardinality = a candidate to split on
                print(f"  {k}: " + ", ".join(f"{val}={n}" for val, n in c.most_common(12)))
            else:
                print(f"  {k}: {len(c)} distinct values")
        for s in samples:
            print("  sample:", json.dumps(s, ensure_ascii=False)[:500])
    except Exception as e:
        print(f"{ev}: ERROR {e}")

# ---------------------------------------------------------------------------
# Part 2 — inApp_Dismissed carries no campaign id (probe part 1 found 0 hits in
# 713 records). So: what DOES it carry, and can a dismissal be tied back to this
# campaign by identity + time instead?
# ---------------------------------------------------------------------------
print("\n\n=== inApp_Dismissed, unfiltered ===")
keys = Counter(); values = defaultdict(Counter); samples = []
dism = defaultdict(list)                      # identity -> [datetime]
for rec in F.export_event("inApp_Dismissed", FRM, TO):
    props = F.props_of(rec)
    keys.update(props.keys())
    for k, v in props.items():
        values[k][str(v)[:60]] += 1
    if len(samples) < 4:
        samples.append({"ts": rec.get("ts"), "props": props})
    ident = F.identity_of(rec)
    if ident:
        try:
            dism[(F.cspid_of(rec) or ident).strip().lower()].append(
                datetime.datetime.strptime(str(rec.get("ts")), "%Y%m%d%H%M%S"))
        except ValueError:
            pass
print("  prop keys:", ", ".join(f"{k} x{v}" for k, v in keys.most_common(20)))
for k, c in values.items():
    if len(c) <= 12:
        print(f"  {k}: " + ", ".join(f"{val}={n}" for val, n in c.most_common(12)))
    else:
        print(f"  {k}: {len(c)} distinct values")
for s in samples:
    print("  sample:", json.dumps(s, ensure_ascii=False)[:500])

print("\n=== can a dismissal be matched to the campaign by identity + time? ===")
shown = []
for rec in F.export_event("inApp_Shown", FRM, TO):
    if CID not in json.dumps(F.props_of(rec), ensure_ascii=False):
        continue
    ident = F.identity_of(rec)
    if not ident:
        continue
    try:
        shown.append(((F.cspid_of(rec) or ident).strip().lower(),
                      datetime.datetime.strptime(str(rec.get("ts")), "%Y%m%d%H%M%S")))
    except ValueError:
        pass
buckets = Counter(); matched = 0
for who, t in shown:
    deltas = [(d - t).total_seconds() for d in dism.get(who, []) if 0 <= (d - t).total_seconds() <= 900]
    if not deltas:
        buckets["no dismissal within 15 min"] += 1
        continue
    matched += 1
    s = min(deltas)
    buckets["0-2s" if s <= 2 else "3-5s" if s <= 5 else "6-10s" if s <= 10 else
            "11-30s" if s <= 30 else "31-120s" if s <= 120 else "2-15 min"] += 1
print(f"  {len(shown)} campaign impressions, {matched} with a dismissal inside 15 min")
for k, n in buckets.most_common():
    print(f"    {k}: {n}")
