#!/usr/bin/env python3
"""Read-only dump of the Net-Box Return in-app (campaign 1793098955) funnel events.

Prints one JSON line per event (event, cspid, identity, ts, props) between
NETBOX_ROWS_BEGIN / NETBOX_ROWS_END so the funnel can be built from the job log.

Usage: python3 probe_netbox.py [from_YYYYMMDD]
"""
import json, sys, datetime

import fetch_ct_data as F

CID = "1793098955"
FRM = sys.argv[1] if len(sys.argv) > 1 else "20261027"
TO  = datetime.date.today().strftime("%Y%m%d")
EVENTS = ["CSP_NetBoxReturn_Shown", "CSP_NetBoxReturn_Call_Tapped", "CSP_NetBoxReturn_Cross_Clicked",
          "CSP_NetBoxReturn_Call_Fallback_Shown", "CSP_NetBoxReturn_Lang_HI", "CSP_NetBoxReturn_Lang_EN",
          "Notification Viewed", "Notification Clicked"]

print(f"netbox funnel dump · {FRM} -> {TO}")
print("NETBOX_ROWS_BEGIN")
for ev in EVENTS:
    n = 0
    for rec in F.export_event(ev, FRM, TO):
        props = F.props_of(rec)
        if ev.startswith("Notification") and CID not in json.dumps(props):
            continue
        n += 1
        p = rec.get("profile") or {}
        print(json.dumps({"e": ev, "csp": F.cspid_of(rec), "id": p.get("identity") or p.get("objectId"),
                          "ts": rec.get("ts"), "p": props}, ensure_ascii=False))
    print(f"# {ev}: {n}", file=sys.stderr)
print("NETBOX_ROWS_END")
