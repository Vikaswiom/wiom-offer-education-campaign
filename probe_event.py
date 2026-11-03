#!/usr/bin/env python3
"""Read-only dump of every occurrence of one CleverTap event.

Prints one JSON line per event (event, cspid, identity, ts, props) between
EVENT_ROWS_BEGIN / EVENT_ROWS_END so per-CSP lists can be built from the job log.

Usage: python3 probe_event.py <event_name> [from_YYYYMMDD]
"""
import json, sys, datetime

import fetch_ct_data as F

EV  = sys.argv[1]
FRM = sys.argv[2] if len(sys.argv) > 2 else (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y%m%d")
TO  = datetime.date.today().strftime("%Y%m%d")

print(f"event dump · {EV} · {FRM} -> {TO}")
print("EVENT_ROWS_BEGIN")
n = 0
for rec in F.export_event(EV, FRM, TO):
    n += 1
    p = rec.get("profile") or {}
    print(json.dumps({"e": EV, "csp": F.cspid_of(rec), "id": p.get("identity") or p.get("objectId"),
                      "ts": rec.get("ts"), "p": F.props_of(rec)}, ensure_ascii=False))
print("EVENT_ROWS_END")
print(f"# {EV}: {n}", file=sys.stderr)
