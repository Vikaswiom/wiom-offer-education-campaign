#!/usr/bin/env python3
"""fetch_bonus_data.py — Pull CleverTap events for the Bonus Seva video in-app and
write bonus_data.json for bonus.html.

The campaign is a single flow (education popup -> portrait video with a 45s
'समझ गया' gate -> one quiz -> done), instrumented with the Bonus_Seva_* events:

    Bonus_Seva_Intro_Viewed          in-app rendered (funnel base)
    Bonus_Seva_LearnMore_Clicked     'और जानें' tapped
    Bonus_Seva_Video_Played          video actually started
    Bonus_Seva_Understood_Clicked    'समझ गया' tapped        {watched_seconds}
    Bonus_Seva_Quiz_Answered         quiz option tapped      {choice, correct}
    Bonus_Seva_Flow_Completed        final 'ठीक है'
    Bonus_Seva_Intro_Dismissed       x on the popup
    Bonus_Seva_Video_Dismissed       x on the player         {watched_seconds}

All counts are unique users (deduped on profile identity). CleverTap 400s on an
event name it has never seen — normal before the campaign's first real fire —
and export_event() already treats that as zero.

Reuses creds + API plumbing from fetch_ct_data.py. Runs in GitHub Actions via
refresh-ct-data.yml alongside the education dashboard's refresh.

Usage: python3 fetch_bonus_data.py [from_YYYYMMDD]
"""
import os, sys, json, datetime
from collections import Counter

import fetch_ct_data as F   # creds, _req, export_event, identity_of, props_of, day_of

START_DATE = "20260727"     # bonus seva campaign launch

# The runner's clock is UTC, but every reader of this dashboard is in India.
# Stamping and windowing in IST also stops the export from missing the first
# 5h30m of an Indian day, when the UTC date is still yesterday.
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

FUNNEL = [
    ("Bonus_Seva_Intro_Viewed",       "intro_viewed",  "Popup shown"),
    ("Bonus_Seva_LearnMore_Clicked",  "learn_more",    "Tapped और जानें"),
    ("Bonus_Seva_Video_Played",       "video_played",  "Video started"),
    ("Bonus_Seva_Understood_Clicked", "understood",    "Tapped समझ गया"),
    ("Bonus_Seva_Quiz_Answered",      "quiz_answered", "Answered the quiz"),
    ("Bonus_Seva_Flow_Completed",     "completed",     "Completed the flow"),
]
DROPOFFS = [
    ("Bonus_Seva_Intro_Dismissed", "intro_dismissed"),
    ("Bonus_Seva_Video_Dismissed", "video_dismissed"),
]
CHOICE_LABEL = {"help_icon": "(?) आइकन (सही)", "luck": "किस्मत", "rotate_phone": "फोन घुमाना"}
BUCKETS = [(0, 5, "0–5s"), (6, 15, "6–15s"), (16, 30, "16–30s"), (31, 44, "31–44s"), (45, 10**9, "45s+")]


def bucketize(seconds_by_ident):
    out = []
    for lo, hi, label in BUCKETS:
        n = sum(1 for s in seconds_by_ident.values() if lo <= s <= hi)
        out.append({"label": label, "users": n})
    return out


def stats(seconds_by_ident):
    vals = sorted(seconds_by_ident.values())
    if not vals:
        return {"users": 0, "avg": 0, "median": 0, "buckets": bucketize({})}
    return {"users": len(vals),
            "avg": round(sum(vals) / len(vals), 1),
            "median": vals[len(vals) // 2],
            "buckets": bucketize(seconds_by_ident)}


def main():
    frm = sys.argv[1] if len(sys.argv) > 1 else START_DATE
    to = datetime.datetime.now(IST).strftime("%Y%m%d")
    print(f"Bonus Seva · CleverTap {F.REGION} · {frm} -> {to}")

    users = {}          # key -> set(identity)
    counts = {}         # key -> raw event count
    daily = {}          # day -> {"intro_viewed","completed"} sets
    quiz_first = {}     # identity -> (choice, correct) first answer only
    vd_seconds = {}     # identity -> max watched_seconds on Video_Dismissed
    un_seconds = {}     # identity -> max watched_seconds on Understood_Clicked

    for event, key, *_ in [(*f,) for f in FUNNEL] + [(*d,) for d in DROPOFFS]:
        u, n = set(), 0
        for rec in F.export_event(event, frm, to):
            n += 1
            ident = F.identity_of(rec)
            if not ident:
                continue
            u.add(ident)
            props = F.props_of(rec)
            d = F.day_of(rec)
            if key in ("intro_viewed", "completed") and d:
                daily.setdefault(d, {"intro_viewed": set(), "completed": set()})[key].add(ident)
            if key == "quiz_answered" and ident not in quiz_first:
                quiz_first[ident] = (str(props.get("choice", "")),
                                     str(props.get("correct", "")).lower() in ("true", "1"))
            if key == "video_dismissed":
                try:
                    vd_seconds[ident] = max(vd_seconds.get(ident, 0), int(float(props.get("watched_seconds", 0))))
                except (TypeError, ValueError):
                    pass
            if key == "understood":
                try:
                    un_seconds[ident] = max(un_seconds.get(ident, 0), int(float(props.get("watched_seconds", 0))))
                except (TypeError, ValueError):
                    pass
        users[key], counts[key] = u, n
        print(f"  {event:34s} -> {n} events, {len(u)} unique users")

    choice_counts = Counter(c for c, _ in quiz_first.values() if c)
    out = {
        "generated": datetime.datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
        "region": F.REGION,
        "start_date": f"{frm[:4]}-{frm[4:6]}-{frm[6:8]}",
        "funnel": [{"key": k, "label": lbl, "event": ev, "users": len(users[k]), "events": counts[k]}
                   for ev, k, lbl in FUNNEL],
        "dropoffs": {
            "intro_dismissed": {"users": len(users["intro_dismissed"]), "events": counts["intro_dismissed"]},
            "video_dismissed": {"users": len(users["video_dismissed"]), "events": counts["video_dismissed"],
                                "watched": stats(vd_seconds)},
        },
        "understood_watched": stats(un_seconds),
        "quiz": {
            "answered": len(quiz_first),
            "correct": sum(1 for _, ok in quiz_first.values() if ok),
            "wrong": sum(1 for _, ok in quiz_first.values() if not ok),
            "choices": [{"choice": c, "label": CHOICE_LABEL.get(c, c), "users": n}
                        for c, n in choice_counts.most_common()],
        },
        "daily": [{"date": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
                   "intro_viewed": len(v["intro_viewed"]), "completed": len(v["completed"])}
                  for d, v in sorted(daily.items())],
    }
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bonus_data.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("wrote", path)

    f = {x["key"]: x["users"] for x in out["funnel"]}
    p = lambda a, b: round(100 * a / b) if b else 0
    print(f"  funnel: shown={f['intro_viewed']} -> learn_more={f['learn_more']} ({p(f['learn_more'],f['intro_viewed'])}%) "
          f"-> played={f['video_played']} -> understood={f['understood']} -> quiz={f['quiz_answered']} "
          f"-> completed={f['completed']} ({p(f['completed'],f['intro_viewed'])}%)")


if __name__ == "__main__":
    main()
