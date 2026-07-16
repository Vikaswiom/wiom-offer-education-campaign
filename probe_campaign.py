#!/usr/bin/env python3
"""One-off diagnostic: what does the CleverTap API know about one campaign id?

Context: the campaigns UI shows impressions for the Technician campaign, but the
events export has no inApp_Shown for it. This probes every API surface that could
carry that impression so we can see where (or whether) it is exposed:

  1. /1/targets/result.json         — campaign stats endpoint
  2. events export                  — system events (Notification Viewed/Clicked)
                                      + inApp_Shown, searching ALL props for the id
  3. inApp_Shown breakdown          — which campaign_id values actually arrive

Runs in GitHub Actions via probe-campaign.yml (needs the same two secrets).
Read-only: prints to the job log, writes nothing.

Usage: python3 probe_campaign.py [campaign_id] [from_YYYYMMDD]
"""
import json, sys, datetime
from collections import Counter

import fetch_ct_data as F   # reuse creds, _req, export_event, helpers

CID = sys.argv[1] if len(sys.argv) > 1 else "1784203536"
FRM = sys.argv[2] if len(sys.argv) > 2 else F.START_DATE
TO  = datetime.date.today().strftime("%Y%m%d")

print(f"probe campaign {CID} · window {FRM} -> {TO} · region {F.REGION}")

print("\n--- 1) campaign stats endpoint ---")
try:
    r = F._req(F.BASE + "/1/targets/result.json", method="POST", body={"id": int(CID)})
    print("/1/targets/result.json:", json.dumps(r, ensure_ascii=False)[:800])
except Exception as e:
    print("/1/targets/result.json:", e)

print("\n--- 2) event export probes ---")
for ev in ("Notification Viewed", "Notification Clicked", "inApp_Shown"):
    try:
        total = hits = 0
        prop_keys = Counter()
        samples = []
        for rec in F.export_event(ev, FRM, TO):
            total += 1
            props = F.props_of(rec)
            prop_keys.update(props.keys())
            if CID in json.dumps(props, ensure_ascii=False):
                hits += 1
                if len(samples) < 3:
                    samples.append({"props": props, "identity": F.identity_of(rec),
                                    "role_tech": F.is_tech(rec), "ts": rec.get("ts")})
        print(f"{ev}: {total} records in window, {hits} mention {CID}")
        if prop_keys:
            print("  prop keys:", ", ".join(f"{k}x{v}" for k, v in prop_keys.most_common(12)))
        for s in samples:
            print("  sample:", json.dumps(s, ensure_ascii=False)[:400])
    except Exception as e:
        print(f"{ev}: ERROR {e}")

print("\n--- 3) inApp_Shown by campaign_id ---")
try:
    c = Counter()
    tech_c = Counter()
    for rec in F.export_event("inApp_Shown", FRM, TO):
        key = str(F.props_of(rec).get("campaign_id", "")) or "(none)"
        c[key] += 1
        if F.is_tech(rec):
            tech_c[key] += 1
    print("all profiles:       ", dict(c.most_common(10)) or "no records")
    print("technician profiles:", dict(tech_c.most_common(10)) or "no records")
except Exception as e:
    print("breakdown ERROR:", e)
