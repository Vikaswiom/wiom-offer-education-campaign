#!/usr/bin/env python3
"""Does an inApp_Dismissed record carry the campaign ANYWHERE, not just in props?

The first probe only searched event_props. This one searches the whole record —
every field the export returns, profile included — for the campaign id, and prints
a couple of complete records (identity blanked) so the shape is on the record.

Usage: python3 probe_dismiss_raw.py [campaign_id] [from_YYYYMMDD]
"""
import json, sys, datetime
from collections import Counter
import fetch_ct_data as F

CID = sys.argv[1] if len(sys.argv) > 1 else "1790081258"
FRM = sys.argv[2] if len(sys.argv) > 2 else "20260922"
TO = datetime.date.today().strftime("%Y%m%d")

def blank(rec):
    r = json.loads(json.dumps(rec))
    p = r.get("profile") or {}
    for k in ("identity", "objectId", "email", "phone", "name"):
        if k in p:
            p[k] = "<blanked>"
    pd = p.get("profileData") or {}
    for k in list(pd):
        if k.lower() not in ("cspid", "role"):
            pd[k] = "<blanked>"
    return r

for ev in ("inApp_Dismissed", "inApp_Shown"):
    total = anywhere = in_props = 0
    top_keys = Counter()
    samples = []
    for rec in F.export_event(ev, FRM, TO):
        total += 1
        top_keys.update(rec.keys())
        whole = json.dumps(rec, ensure_ascii=False)
        if CID in whole:
            anywhere += 1
            if CID in json.dumps(F.props_of(rec), ensure_ascii=False):
                in_props += 1
        if len(samples) < 2:
            samples.append(blank(rec))
    print(f"\n=== {ev}: {total} records · {anywhere} contain {CID} anywhere · {in_props} in event_props")
    print("  top-level keys:", ", ".join(f"{k} x{v}" for k, v in top_keys.most_common()))
    for s in samples:
        print("  full record:", json.dumps(s, ensure_ascii=False)[:900])
