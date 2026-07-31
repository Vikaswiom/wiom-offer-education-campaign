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

# --------------------------------------------------------------- round 2 ----
# The Home-story creative replaces the video for CSPs who never answered the
# round-1 quiz. Entirely different event names, so it cannot ride the trigger
# split used for round 1 — the round-1 loop never sees these at all.
#
# The creative has shipped in two shapes. The six-card build merged its "1–15
# window" and "the rule" cards into one, which shifted every later card_index
# down by one, so index 2 means a different screen in each. The creative marks
# that with a different `trigger` value AND stamps a stable `card_name` on every
# card event. We bucket on trigger and label from card_name, never from the
# index, so the two builds can never blend into one nonsense funnel.
R2 = "Bonus_Seva2_"
R2_EVENTS = ["Story_Viewed", "Gift_Opened", "Card_Viewed", "Quality_Toggled",
             "Chip_Tapped", "Help_Tapped", "GoTo_SevaSthiti", "Flow_Completed", "Dismissed"]

# The build actually in market. Older builds are only worth a panel if real CSPs
# ever reached them — a superseded build whose only traffic was internal test
# fires is noise, and showing it twice-over just makes the section look broken.
R2_CURRENT = "home_story_r2_5card"
R2_BUILD_LABEL = {
    "home_story_r2_5card": "Home story · 5 cards",
    "home_story_r2": "Home story · 6 cards (superseded)",
}
# Screens by their stable name. Index is deliberately absent — it moved.
CARD_NAME_LABEL = {
    "hero_gift":   "hero · the gift",
    "window_rule": "1–15 अगस्त + the rule",
    "home_chip":   "where सेवा स्थिति lives",
    "help_icon":   "the (?) button",
    "done":        "handoff",
}
# the two drilldown rows the (?) buttons sit next to, as the CSP reads them
METRIC_LABEL = {"samay_par_kaam": "समय पर काम", "grahak_ki_santushti": "ग्राहक की संतुष्टि"}

# Funnel for the shipping five-card build. Chip_Tapped is what CARRIES the CSP
# to the help_icon card and Help_Tapped is what unlocks the handoff, so those
# taps stand in for those two screens — listing the card arrivals as well would
# count the same people twice and, worse, put a step before the tap that causes it.
R2_FUNNEL_5 = [
    ("shown",       "Story shown",                   "Story_Viewed"),
    ("gift",        "Opened the gift",               "Gift_Opened"),
    ("window_rule", "1–15 अगस्त + the rule",          "Card_Viewed"),
    ("home_chip",   "Where सेवा स्थिति lives",         "Card_Viewed"),
    ("chip",        "Tapped the सेवा स्थिति chip",     "Chip_Tapped"),
    ("help",        "Tapped the (?) button",         "Help_Tapped"),
    ("completed",   "Completed the story",           "Flow_Completed"),
]
# Any other build: index-agnostic, so it stays correct whatever the cards were.
R2_FUNNEL_GENERIC = [
    ("shown",     "Story shown",                "Story_Viewed"),
    ("gift",      "Opened the gift",            "Gift_Opened"),
    ("chip",      "Tapped the सेवा स्थिति chip",  "Chip_Tapped"),
    ("help",      "Tapped the (?) button",      "Help_Tapped"),
    ("completed", "Completed the story",        "Flow_Completed"),
]


def _r2_blank():
    return {"u": {}, "n": {}, "cards": {}, "quits": {}, "exits": {},
            "metrics": {}, "first_ts": 0}


def csp_key(rec):
    """One row per CSP SHOP, not per login.

    Measured 2026-07-31 on live round-2 traffic: 312 distinct identities across
    288 distinct cspids — 24 shops carry two logins, and no identity ever spans
    two shops. Deduping on identity therefore counted those 24 shops twice, an
    ~8% overstatement at the top of the funnel. cspid is the coarser and correct
    unit for "how many CSPs saw this".

    Returns None for the excluded internal shop and for records with nothing
    usable, so both stay out of every count.
    """
    c = F.cspid_of(rec)
    if c:
        return None if c in F.EXCLUDE_CSP else "csp:" + c
    i = F.identity_of(rec)          # already returns None for the excluded shop
    return ("id:" + str(i)) if i else None


def raw_identity(rec):
    """Identity WITHOUT the internal-CSP exclusion F.identity_of applies.

    Used only to tell "excluded internal shop" apart from "profile has no
    identity at all" in the log. The internal shop's traffic is never reported
    as data — a funnel sitting at zero needs to say which of those two it is."""
    p = rec.get("profile") or {}
    return p.get("identity") or p.get("objectId") or p.get("email") or None


def _r2_add(a, name, ident, props, rec):
    """Fold one real-CSP record into the accumulator."""
    a["n"][name] = a["n"].get(name, 0) + 1
    cname = str(props.get("card_name", "") or "")
    try:
        ci = int(props.get("card_index", -1))
    except (TypeError, ValueError):
        ci = -1

    if name == "Card_Viewed":
        if ident and (cname or ci >= 0):
            a["cards"].setdefault(cname or ("index_%d" % ci), {"idx": ci, "u": set()})["u"].add(ident)
    elif name == "Dismissed":
        q = a["quits"].setdefault(cname or ("index_%d" % ci), {"idx": ci, "u": set(), "n": 0})
        q["n"] += 1
        if ident:
            q["u"].add(ident)
    elif name == "Help_Tapped":
        m = str(props.get("metric", "") or "")
        if m and ident:
            a["metrics"].setdefault(m, set()).add(ident)
    elif name == "Flow_Completed":
        x = str(props.get("exit", "") or "unknown")
        if ident:
            a["exits"].setdefault(x, set()).add(ident)
    elif name == "Story_Viewed":
        ts = ts_of(rec)
        a["first_ts"] = min(a["first_ts"], ts) if a["first_ts"] and ts else (ts or a["first_ts"])

    if ident:
        a["u"].setdefault(name, set()).add(ident)


def _r2_view(a, shape):
    """Turn one accumulator into the funnel + splits the dashboard renders."""
    def users_for(key, ev):
        if ev == "Card_Viewed":
            c = a["cards"].get(key)
            return len(c["u"]) if c else 0
        return len(a["u"].get(ev, set()))

    cards = sorted(a["cards"].items(), key=lambda kv: kv[1]["idx"])
    quits = sorted(a["quits"].items(), key=lambda kv: kv[1]["idx"])
    exits = {k: len(v) for k, v in a["exits"].items()}
    return {
        "shown": len(a["u"].get("Story_Viewed", set())),
        "funnel": [{"key": k, "label": lbl, "event": R2 + ev,
                    "users": users_for(k, ev),
                    "events": a["n"].get(ev, 0) if ev != "Card_Viewed" else None}
                   for k, lbl, ev in shape],
        "cards": [{"name": nm, "index": v["idx"],
                   "label": CARD_NAME_LABEL.get(nm, nm), "users": len(v["u"])}
                  for nm, v in cards],
        "quits": [{"name": nm, "index": v["idx"],
                   "label": CARD_NAME_LABEL.get(nm, nm), "users": len(v["u"]), "events": v["n"]}
                  for nm, v in quits],
        "exits": exits,
        "goto": {"users": len(a["u"].get("GoTo_SevaSthiti", set())), "events": a["n"].get("GoTo_SevaSthiti", 0)},
        "toggled": {"users": len(a["u"].get("Quality_Toggled", set())), "events": a["n"].get("Quality_Toggled", 0)},
        "help_metrics": [{"metric": m, "label": METRIC_LABEL.get(m, m), "users": len(s)}
                         for m, s in sorted(a["metrics"].items(), key=lambda kv: -len(kv[1]))],
        "dismissed_total": {"users": len(a["u"].get("Dismissed", set())), "events": a["n"].get("Dismissed", 0)},
    }


def fetch_round2(frm, to):
    """Bucket every Bonus_Seva2_* event by trigger, and within that by whether it
    came from a real CSP or the excluded internal test shop."""
    B = {}
    drop = {"real": 0, "test": 0, "no_identity": 0}
    sample_keys = set()

    def acc(t):
        return B.setdefault(t, {"real": _r2_blank()})

    for name in R2_EVENTS:
        ev, seen = R2 + name, 0
        for rec in F.export_event(ev, frm, to):
            seen += 1
            props = F.props_of(rec)
            pair = acc(trigger_of(props) if props.get("trigger") else "home_story_r2")
            ident = csp_key(rec)
            if ident:
                drop["real"] += 1
                _r2_add(pair["real"], name, ident, props, rec)
            else:
                any_id = raw_identity(rec)
                if any_id:
                    # a0a0b1 is the internal testing shop: counted here only so the
                    # log can distinguish "excluded on purpose" from "broken", never
                    # surfaced as data
                    drop["test"] += 1
                else:
                    drop["no_identity"] += 1
                    if len(sample_keys) < 12:
                        sample_keys |= set((rec.get("profile") or {}).keys())
        print(f"  {ev:34s} -> {seen} events")

    print(f"  attribution: {drop['real']} from real CSPs, {drop['test']} discarded as the internal testing shop, "
          f"{drop['no_identity']} with no usable identity")
    if drop["no_identity"]:
        print(f"  ::warning::{drop['no_identity']} Bonus_Seva2_* records carry no identity/objectId/email — "
              f"those can never reach a funnel. profile keys seen: {sorted(sample_keys)}")

    if not B:
        B[R2_CURRENT] = {"real": _r2_blank()}

    builds = []
    cohorts = {}   # trigger -> set of cspids that COMPLETED, for the r1-vs-r2 read
    for t in sorted(B, key=lambda k: -(B[k]["real"]["first_ts"] or 0)):
        pair = B[t]
        shape = R2_FUNNEL_5 if t == R2_CURRENT else R2_FUNNEL_GENERIC
        real = _r2_view(pair["real"], shape)
        a = pair["real"]
        cohorts[t] = set(a["u"].get("Flow_Completed", set()))
        first_ts = a["first_ts"]
        raw = sum(a["n"].values())
        known = [e for e in R2_EVENTS if a["n"].get(e)]
        b = dict(real)
        b.update({
            "key": t,
            "label": R2_BUILD_LABEL.get(t, t.replace("_", " ")),
            "live_ts": first_ts or None,
            "live_label": (datetime.datetime.strptime(str(first_ts), "%Y%m%d%H%M%S")
                           .strftime("%d %b %Y, %H:%M IST") if first_ts else None),
            "raw_events": raw,
            "events_confirmed": known,
            "events_unseen": [e for e in R2_EVENTS if e not in known],
        })
        builds.append(b)
        rf = {x["key"]: x["users"] for x in real["funnel"]}
        print(f"  {t}: shown={rf['shown']} completed={rf['completed']} into_app={real['goto']['users']}")

    keep = []
    for b in builds:
        # A superseded build is only worth a panel if real CSPs reached it. Test
        # traffic on a build nobody ships is not a reason to keep showing it.
        if b["key"] != R2_CURRENT and not b["shown"]:
            print(f"  dropping panel for {b['key']}: superseded, no real CSPs "
                  f"({b['raw_events']} events, test-only)")
            continue
        keep.append(b)
    if not keep:
        keep = [b for b in builds if b["key"] == R2_CURRENT] or builds[:1]
    return keep, cohorts


# ---------------------------------------------------------------- impact ----
# Does the education actually change behaviour? The quiz teaches that quality
# detail lives behind the (?) icon, so the test is whether the people who
# answered the quiz open that help screen more often after the campaign than
# they did before it.
#
# Same people on both sides — a within-subject comparison, so CSP mix, tenure
# and seasonality cancel out.
IMPACT_EVENT = "service_status_help_opened"
PRE_DAYS = 11                    # length of the before window, ending the day before go-live

# The windows are wildly different lengths (11 days vs hours), so raw counts are
# not comparable and per-user-per-day is the number to read. reach_pct is kept
# because it answers "how many of them ever look", but it is biased toward the
# longer window and the dashboard says so.

# ------------------------------------------------------------- triggers -----
# Both CleverTap triggers run at once with the same event names, so the creative
# stamps every event with `trigger` and the funnels are split on it. Events fired
# before that property shipped have none, and only the quality-section campaign
# was ever live then — so a missing value means quality_section, not "unknown".
LEGACY_TRIGGER = "quality_section"
TRIGGER_LABEL = {"quality_section": "Quality-of-work section", "app_launch": "App launch"}
TRIGGER_ORDER = ["quality_section", "app_launch"]


def trigger_of(props):
    t = str(props.get("trigger", "") or "").strip()
    return t if t else LEGACY_TRIGGER
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



def day_add(yyyymmdd, delta):
    d = datetime.datetime.strptime(yyyymmdd, "%Y%m%d") + datetime.timedelta(days=delta)
    return d.strftime("%Y%m%d")


def load_help_opens(frm, to):
    """Every service_status_help_opened as (identity, ts), fetched once and
    windowed in memory — two triggers x two windows would otherwise be four
    identical exports of the same busy event."""
    opens = []
    for rec in F.export_event(IMPACT_EVENT, frm, to):
        ident = F.identity_of(rec)
        # cspid too: round 1's cohorts are identity-keyed, round 2's are cspid-keyed,
        # and both read this same list.
        opens.append((ident, csp_key(rec), ts_of(rec)))
    withcsp = sum(1 for _i, c, _t in opens if c and c.startswith("csp:"))
    print(f"  {IMPACT_EVENT}: {len(opens)} records {frm}->{to} (fetched once, windowed per trigger); "
          f"{withcsp} carry a cspid")
    if opens and not withcsp:
        # The round-2 cohort is cspid-keyed. If these records only ever resolve to
        # an identity, the two keyspaces never intersect and the round-1-vs-round-2
        # panel reads a flat zero that looks like "no behaviour change".
        print("  ::warning::no service_status_help_opened record carries a cspid — the "
              "round-1-vs-round-2 comparison cannot match its cohort and will read zero")
    return opens


def impact(cohort, opens, live_ts, now_ist, key_idx=0):
    """Help-screen opens for this trigger's quiz cohort, before vs after ITS go-live.

    The before window is the PRE_DAYS days ending the day before go-live, so a
    trigger that launches later is compared against its own recent baseline
    rather than a fixed calendar range someone has to remember to update.
    """
    if not cohort or not live_ts:
        return None

    live = datetime.datetime.strptime(str(live_ts), "%Y%m%d%H%M%S").replace(tzinfo=IST)
    pre_to = day_add(str(live_ts)[:8], -1)
    pre_frm = day_add(pre_to, -(PRE_DAYS - 1))
    pre_lo, pre_hi = int(pre_frm + "000000"), int(pre_to + "235959")
    post_days = max((now_ist - live).total_seconds() / 86400, 1 / 24)   # floor at 1h, never divide by ~0

    def window(lo, hi, days, label):
        users, events = set(), 0
        for row in opens:
            ident, ts = row[key_idx], row[2]
            if lo <= ts <= hi and ident and ident in cohort:
                users.add(ident); events += 1
        per = round(events / len(cohort) / days, 3) if days else 0
        print(f"    {label:5s} {lo}->{hi}: {events} opens, {len(users)}/{len(cohort)} cohort, "
              f"{per}/user/day over {days:.2f}d")
        return {"users": len(users), "events": events, "days": round(days, 2),
                "reach_pct": round(100 * len(users) / len(cohort)), "per_user_day": per}

    pre = window(pre_lo, pre_hi, float(PRE_DAYS), "pre")
    post = window(live_ts, int(now_ist.strftime("%Y%m%d%H%M%S")), post_days, "post")
    lift = (round(100 * (post["per_user_day"] - pre["per_user_day"]) / pre["per_user_day"])
            if pre["per_user_day"] else None)

    fmt = lambda s: datetime.datetime.strptime(str(s), "%Y%m%d").strftime("%d %b")
    return {
        "event": IMPACT_EVENT, "cohort_event": "Bonus_Seva_Quiz_Answered", "cohort_users": len(cohort),
        "pre":  dict(pre,  label=f"{fmt(pre_frm)}–{fmt(pre_to)} (before)"),
        "post": dict(post, label=f"since {live.strftime('%d %b %H:%M')} (after)"),
        "lift_pct": lift,
    }


def blank():
    return {"users": {}, "counts": {}, "quiz_first": {}, "vd": {}, "un": {}, "daily": {}, "first_ts": 0}


def main():
    frm = sys.argv[1] if len(sys.argv) > 1 else START_DATE
    now_ist = datetime.datetime.now(IST)
    to = now_ist.strftime("%Y%m%d")
    print(f"Bonus Seva · CleverTap {F.REGION} · {frm} -> {to}")

    T = {}   # trigger -> per-trigger accumulators

    def acc(t):
        return T.setdefault(t, blank())

    for event, key, *_ in [(*f,) for f in FUNNEL] + [(*d,) for d in DROPOFFS]:
        seen = Counter()
        for rec in F.export_event(event, frm, to):
            ident = F.identity_of(rec)
            props = F.props_of(rec)
            t = trigger_of(props)
            a = acc(t)
            a["counts"][key] = a["counts"].get(key, 0) + 1
            seen[t] += 1
            if not ident:
                continue
            a["users"].setdefault(key, set()).add(ident)
            d = F.day_of(rec)
            if key in ("intro_viewed", "quiz_answered") and d:
                a["daily"].setdefault(d, {"intro_viewed": set(), "quiz_answered": set()})[key].add(ident)
            if key == "intro_viewed":
                ts = ts_of(rec)
                a["first_ts"] = min(a["first_ts"], ts) if a["first_ts"] and ts else (ts or a["first_ts"])
            if key == "quiz_answered" and ident not in a["quiz_first"]:
                a["quiz_first"][ident] = (str(props.get("choice", "")),
                                          str(props.get("correct", "")).lower() in ("true", "1"))
            if key == "video_dismissed":
                try:
                    a["vd"][ident] = max(a["vd"].get(ident, 0), int(float(props.get("watched_seconds", 0))))
                except (TypeError, ValueError):
                    pass
            if key == "understood":
                try:
                    a["un"][ident] = max(a["un"].get(ident, 0), int(float(props.get("watched_seconds", 0))))
                except (TypeError, ValueError):
                    pass
        split = ", ".join(f"{t}={n}" for t, n in sorted(seen.items())) or "none"
        print(f"  {event:34s} -> {sum(seen.values())} events [{split}]")

    # order known triggers first, then anything the creative starts sending later
    keys = [t for t in TRIGGER_ORDER if t in T] + [t for t in sorted(T) if t not in TRIGGER_ORDER]
    if not keys:
        keys = [LEGACY_TRIGGER]
        T[LEGACY_TRIGGER] = blank()

    live = {t: T[t]["first_ts"] for t in keys}
    for t in keys:
        print(f"  {t}: first Intro_Viewed ts = {live[t] or '—'}  (CT stamps in IST for this account)")

    earliest = min([v for v in live.values() if v] or [int(frm + "000000")])
    help_frm = day_add(str(earliest)[:8], -PRE_DAYS)
    opens = load_help_opens(help_frm, to)

    triggers = []
    for t in keys:
        a = T[t]
        u = lambda k: a["users"].get(k, set())
        c = lambda k: a["counts"].get(k, 0)
        print(f"  impact · {t}:")
        choice_counts = Counter(ch for ch, _ in a["quiz_first"].values() if ch)
        triggers.append({
            "key": t,
            "label": TRIGGER_LABEL.get(t, t.replace("_", " ").title()),
            "live_ts": a["first_ts"] or None,
            "live_label": (datetime.datetime.strptime(str(a["first_ts"]), "%Y%m%d%H%M%S")
                           .strftime("%d %b %Y, %H:%M IST") if a["first_ts"] else None),
            "funnel": [{"key": k, "label": lbl, "event": ev, "users": len(u(k)), "events": c(k)}
                       for ev, k, lbl in FUNNEL],
            "dropoffs": {
                "intro_dismissed": {"users": len(u("intro_dismissed")), "events": c("intro_dismissed")},
                "video_dismissed": {"users": len(u("video_dismissed")), "events": c("video_dismissed"),
                                    "watched": stats(a["vd"])},
            },
            "dismiss_tap": {"users": len(u("dismiss_tap")), "events": c("dismiss_tap")},
            "understood_watched": stats(a["un"]),
            "quiz": {
                "answered": len(a["quiz_first"]),
                "correct": sum(1 for _, ok in a["quiz_first"].values() if ok),
                "wrong": sum(1 for _, ok in a["quiz_first"].values() if not ok),
                "choices": [{"choice": ch, "label": CHOICE_LABEL.get(ch, ch), "users": n}
                            for ch, n in choice_counts.most_common()],
            },
            "impact": impact(u("quiz_answered"), opens, a["first_ts"], now_ist),
            "daily": [{"date": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
                       "intro_viewed": len(v["intro_viewed"]), "completed": len(v["quiz_answered"])}
                      for d, v in sorted(a["daily"].items())],
        })

    # A trigger that launches second has a "before" window that runs through the
    # first trigger's live period, so part of its baseline is people the campaign
    # had already educated — the comparison understates itself. Same for CSPs who
    # appear in both cohorts. Detect both and let the dashboard say so rather than
    # quietly reporting a clean-looking lift.
    cohorts = {t: T[t]["users"].get("quiz_answered", set()) for t in keys}
    for tr in triggers:
        im, t = tr["impact"], tr["key"]
        if not im or not live.get(t):
            continue
        pre_to = day_add(str(live[t])[:8], -1)
        lo, hi = int(day_add(pre_to, -(PRE_DAYS - 1)) + "000000"), int(pre_to + "235959")
        im["pre_overlaps"] = [TRIGGER_LABEL.get(o, o) for o in keys
                              if o != t and live.get(o) and lo <= live[o] <= hi]
        others = set().union(*[cohorts[o] for o in keys if o != t]) if len(keys) > 1 else set()
        im["cohort_shared"] = len(cohorts[t] & others)
        if im["pre_overlaps"] or im["cohort_shared"]:
            print(f"    caveat · {t}: baseline overlaps {im['pre_overlaps'] or 'nothing'}, "
                  f"{im['cohort_shared']} CSPs shared with another trigger")

    print("  --- round 2 (Home story) ---")
    r2, r2_cohorts = fetch_round2(frm, to)
    # One uniform before/after read per campaign, all on the same event and the
    # same method (PRE_DAYS before its own go-live vs since), so the three can be
    # laid side by side and actually compared.
    impacts = [{"key": t["key"], "label": t["label"], "live_label": t["live_label"],
                "cohort_label": "answered the quiz", "impact": t["impact"]}
               for t in triggers if t.get("impact")]
    for b in r2:
        coh = r2_cohorts.get(b["key"], set())
        im = impact(coh, opens, b["live_ts"], now_ist, key_idx=1)   # round 2 is cspid-keyed
        if im:
            im["cohort_event"] = "Bonus_Seva2_Flow_Completed"
            # Round 2 launched last, so its 11-day baseline runs straight through
            # the round-1 campaigns' live period — same contamination the later
            # round-1 trigger carries, and it must be said here too.
            if b["live_ts"]:
                pre_to = day_add(str(b["live_ts"])[:8], -1)
                lo = int(day_add(pre_to, -(PRE_DAYS - 1)) + "000000")
                hi = int(pre_to + "235959")
                im["pre_overlaps"] = [TRIGGER_LABEL.get(o, o) for o in keys
                                      if live.get(o) and lo <= live[o] <= hi]
            impacts.append({"key": b["key"], "label": b["label"], "live_label": b["live_label"],
                            "cohort_label": "finished the story", "impact": im})
    print("  --- before/after, one row per campaign ---")
    for x in impacts:
        i = x["impact"]
        print(f"  {x['label'][:34]:36s} {i['pre']['per_user_day']} -> {i['post']['per_user_day']} "
              f"/user/day  (lift {i['lift_pct']}%)  cohort {i['cohort_users']}")

    # headline totals: unique across triggers, so one CSP served by both is one person
    def union(k):
        s = set()
        for t in keys:
            s |= T[t]["users"].get(k, set())
        return s

    out = {
        "generated": now_ist.strftime("%Y-%m-%d %H:%M IST"),
        "region": F.REGION,
        "start_date": f"{frm[:4]}-{frm[4:6]}-{frm[6:8]}",
        "triggers": triggers,
        "round2": r2,
        "impacts": impacts,
        "totals": {"funnel": [{"key": k, "label": lbl, "event": ev,
                               "users": len(union(k)),
                               "events": sum(T[t]["counts"].get(k, 0) for t in keys)}
                              for ev, k, lbl in FUNNEL]},
    }
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bonus_data.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("wrote", path)

    p = lambda a_, b_: round(100 * a_ / b_) if b_ else 0
    for tr in triggers:
        f = {x["key"]: x["users"] for x in tr["funnel"]}
        line = (f"  {tr['key']:16s} shown={f['intro_viewed']} played={f['video_played']} "
                f"understood={f['understood']} quiz={f['quiz_answered']} "
                f"({p(f['quiz_answered'], f['intro_viewed'])}% of shown)")
        im = tr["impact"]
        if im:
            line += f" | impact {im['pre']['per_user_day']}->{im['post']['per_user_day']} (lift {im['lift_pct']}%)"
        print(line)
    for b in r2:
        rf = {x["key"]: x["users"] for x in b["funnel"]}
        print(f"  {b['key']:22s} shown={rf['shown']} gift={rf['gift']} chip={rf['chip']} "
              f"help={rf['help']} completed={rf['completed']} ({p(rf['completed'], rf['shown'])}% of shown) "
              f"| into app={b['goto']['users']} exits={b['exits']}")


if __name__ == "__main__":
    main()
