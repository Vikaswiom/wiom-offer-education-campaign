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

# Completion is ANSWERING THE QUIZ, not the ठीक है tap that follows it. The tap
# only dismisses the in-app; the education has already landed by the time the
# quiz is answered, and anyone who answers and then backgrounds the app would
# otherwise be counted as a drop-out. Bonus_Seva_Flow_Completed is still
# fetched, reported separately as the dismiss tap, and no longer gates the funnel.
FUNNEL = [
    ("Bonus_Seva_Intro_Viewed",       "intro_viewed",  "Popup shown"),
    ("Bonus_Seva_LearnMore_Clicked",  "learn_more",    "Tapped और जानें"),
    ("Bonus_Seva_Video_Played",       "video_played",  "Video started"),
    ("Bonus_Seva_Understood_Clicked", "understood",    "Tapped समझ गया"),
    ("Bonus_Seva_Quiz_Answered",      "quiz_answered", "Completed — answered the quiz"),
]
DROPOFFS = [
    ("Bonus_Seva_Intro_Dismissed", "intro_dismissed"),
    ("Bonus_Seva_Video_Dismissed", "video_dismissed"),
    ("Bonus_Seva_Flow_Completed",  "dismiss_tap"),
]

# ---------------------------------------------------------------- impact ----
# Does the education actually change behaviour? The quiz teaches that quality
# detail lives behind the (?) icon, so the test is whether the people who
# answered the quiz open that help screen more often after the campaign than
# they did before it.
#
# Same people on both sides — a within-subject comparison, so CSP mix, tenure
# and seasonality cancel out.
IMPACT_EVENT = "service_status_help_opened"
PRE_FROM, PRE_TO = "20260716", "20260726"        # 11 full days before the campaign
CAMPAIGN_LIVE_TS = 20260727233000                # 27 Jul 23:30, ts is YYYYMMDDHHMMSS

# The windows are wildly different lengths (11 days vs hours), so raw counts are
# not comparable and per-user-per-day is the number to read. reach_pct is kept
# because it answers "how many of them ever look", but it is biased toward the
# longer window and the dashboard says so.
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


def ts_of(rec):
    try:
        return int(str(rec.get("ts", "0"))[:14])
    except (TypeError, ValueError):
        return 0


def impact(cohort, now_ist):
    """service_status_help_opened for the quiz cohort, before vs after the campaign."""
    if not cohort:
        return None

    def window(frm, to, days, since_ts=None, label=""):
        users, events = set(), 0
        for rec in F.export_event(IMPACT_EVENT, frm, to):
            if since_ts and ts_of(rec) < since_ts:
                continue                       # export is day-granular; trim to the go-live minute
            ident = F.identity_of(rec)
            if ident in cohort:                # cohort members only
                users.add(ident); events += 1
        per = round(events / len(cohort) / days, 3) if days else 0
        print(f"  {IMPACT_EVENT} {label:5s} {frm}->{to}: {events} events, {len(users)} of "
              f"{len(cohort)} cohort users, {per}/user/day over {days:.2f}d")
        return {"users": len(users), "events": events, "days": round(days, 2),
                "reach_pct": round(100 * len(users) / len(cohort)),
                "per_user_day": per}

    live = datetime.datetime.strptime(str(CAMPAIGN_LIVE_TS), "%Y%m%d%H%M%S").replace(tzinfo=IST)
    post_days = max((now_ist - live).total_seconds() / 86400, 1 / 24)   # floor at 1h, never divide by ~0

    pre = window(PRE_FROM, PRE_TO, 11.0, label="pre")
    post = window(str(CAMPAIGN_LIVE_TS)[:8], now_ist.strftime("%Y%m%d"), post_days,
                  since_ts=CAMPAIGN_LIVE_TS, label="post")
    lift = (round(100 * (post["per_user_day"] - pre["per_user_day"]) / pre["per_user_day"])
            if pre["per_user_day"] else None)
    return {
        "event": IMPACT_EVENT, "cohort_event": "Bonus_Seva_Quiz_Answered", "cohort_users": len(cohort),
        "pre":  dict(pre,  frm="2026-07-16", to="2026-07-26", label="16–26 Jul (before)"),
        "post": dict(post, frm="2026-07-27 23:30", to=now_ist.strftime("%d %b %H:%M IST"),
                     label="since 27 Jul 23:30 (after)"),
        "lift_pct": lift,
    }


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
    first_ts = 0        # earliest Intro_Viewed ts — confirms which clock CT stamps in

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
            if key in ("intro_viewed", "quiz_answered") and d:
                daily.setdefault(d, {"intro_viewed": set(), "quiz_answered": set()})[key].add(ident)
            if key == "intro_viewed":
                first_ts = min(first_ts, ts_of(rec)) if first_ts else ts_of(rec)
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

    print(f"  earliest Bonus_Seva_Intro_Viewed ts = {first_ts} "
          f"(campaign went live 27 Jul 23:30 IST — if this reads ~20260727 18xx, CT stamps in UTC)")

    now_ist = datetime.datetime.now(IST)
    imp = impact(users["quiz_answered"], now_ist)

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
        "dismiss_tap": {"users": len(users["dismiss_tap"]), "events": counts["dismiss_tap"]},
        "impact": imp,
        "understood_watched": stats(un_seconds),
        "quiz": {
            "answered": len(quiz_first),
            "correct": sum(1 for _, ok in quiz_first.values() if ok),
            "wrong": sum(1 for _, ok in quiz_first.values() if not ok),
            "choices": [{"choice": c, "label": CHOICE_LABEL.get(c, c), "users": n}
                        for c, n in choice_counts.most_common()],
        },
        "daily": [{"date": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
                   "intro_viewed": len(v["intro_viewed"]), "completed": len(v["quiz_answered"])}
                  for d, v in sorted(daily.items())],
    }
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bonus_data.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("wrote", path)

    f = {x["key"]: x["users"] for x in out["funnel"]}
    p = lambda a, b: round(100 * a / b) if b else 0
    print(f"  funnel: shown={f['intro_viewed']} -> learn_more={f['learn_more']} ({p(f['learn_more'],f['intro_viewed'])}%) "
          f"-> played={f['video_played']} -> understood={f['understood']} "
          f"-> completed/quiz={f['quiz_answered']} ({p(f['quiz_answered'],f['intro_viewed'])}%)")
    if imp:
        print(f"  impact: {imp['pre']['per_user_day']} -> {imp['post']['per_user_day']} opens/user/day "
              f"(lift {imp['lift_pct']}%) for {imp['cohort_users']} quiz answerers")


if __name__ == "__main__":
    main()
