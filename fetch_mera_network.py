#!/usr/bin/env python3
"""
fetch_mera_network.py — pull the MeraNetwork_Edu_* in-app events and write
mera_network_data.json for mera-network.html.

ONE creative, one screen, two ways out:

    MeraNetwork_Edu_Shown        the page painted and found the bridge
    MeraNetwork_Edu_Understood   tapped समझ गया      props: source=cta, seconds
    MeraNetwork_Edu_Closed       tapped the ✕        props: source=x,   seconds

The creative fires its own Shown rather than leaning on the app's inApp_Shown,
so the funnel needs no campaign-id attribution. CleverTap's own inApp_Shown for
campaign 1790081258 is pulled anyway, purely as a cross-check on reach: it is
fired by the SDK, so it catches the devices whose bridge was still injecting when
the page painted and that therefore missed the creative's own Shown. The funnel
is NOT clamped — a partner who acted without a recorded Shown is reported as an
orphan rather than hidden or folded in.

WHAT IS COUNTED
Unique CSPs, not events. Someone who saw the screen on three separate days is
one person at every step. `raw` alongside each step is the event volume, so a
wide gap between the two means the frequency cap is not doing its job.

`seconds` is wall-clock time on the screen before the tap. It is the only read
we get on whether the message was absorbed or swatted away, hence the buckets.

Secrets: CLEVERTAP_ACCOUNT / CLEVERTAP_PASSCODE (eu1). Reuses fetch_ct_data for
creds and the export plumbing.
"""
import os, json, collections
from datetime import datetime, timezone, timedelta
import fetch_ct_data as ct          # creds, export_event(), identity_of(), props_of(), day_of(), cspid_of()

IST = timezone(timedelta(hours=5, minutes=30))
START = os.environ.get("MERA_NETWORK_START", "20260922")   # campaign go-live
CREATIVE = "mera_network_moved_v1"
PREFIX = "MeraNetwork_Edu_"
CAMPAIGN = "1790081258"          # CleverTap campaign id
DISMISS_WINDOW = 900             # seconds an inApp_Dismissed may trail its impression

STEPS = [
    ("Shown",      "shown",      "Screen shown"),
    ("Understood", "understood", "Tapped समझ गया"),
    ("Closed",     "closed",     "Closed with ✕"),
]

# seconds-on-screen buckets. 0-2s is a reflex swat, 6s+ is someone who read it.
BUCKETS = [(0, 2, "0–2s"), (3, 5, "3–5s"), (6, 10, "6–10s"), (11, 30, "11–30s"), (31, 10 ** 9, "31s+")]


def here(f):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), f)


def pull(event):
    """-> {"users": set, "raw": n, "days": {YYYYMMDD: set}, "secs": [int], "freq": {user: n}}"""
    out = {"users": set(), "raw": 0, "days": collections.defaultdict(set),
           "secs": [], "freq": collections.Counter()}
    now = datetime.now(IST).strftime("%Y%m%d")
    for rec in ct.export_event(PREFIX + event, START, now):
        ident = ct.identity_of(rec)             # test shop already dropped
        if not ident:
            continue
        who = (ct.cspid_of(rec) or ident).strip().lower()
        p = ct.props_of(rec)
        out["users"].add(who)
        out["raw"] += 1
        out["freq"][who] += 1
        d = ct.day_of(rec)
        if d:
            out["days"][d].add(who)
        s = p.get("seconds")
        if s is not None:
            try:
                out["secs"].append(int(s))
            except (TypeError, ValueError):
                pass
    print(f"  {PREFIX + event:28s} {out['raw']:6d} records, {len(out['users']):5d} unique CSPs")
    return out


def _ts(rec):
    """ts is YYYYMMDDHHMMSS stamped in IST for this account — not epoch."""
    try:
        return datetime.strptime(str(rec.get("ts")), "%Y%m%d%H%M%S")
    except (TypeError, ValueError):
        return None


def system_events():
    """The SDK's own inApp_Shown / inApp_Dismissed, paired into screen sessions.

    inApp_Shown carries `campaign_id` (as "<id>_<YYYYMMDD>", hence the substring
    match). inApp_Dismissed carries NO campaign id at all — proven by probe, which
    found 0 of 713 dismissals mentioning this campaign anywhere in their props. So
    a dismissal is tied back to an impression by identity and time:

      * take the first dismissal AFTER the impression, within DISMISS_WINDOW
      * reject it if ANY other in-app was shown to that CSP in between, so a
        dismissal can never be stolen from a different campaign's screen
      * one dismissal is consumed by at most one impression

    The gap between the two is time on screen, which is the only attention signal
    available while the untracked build of the creative is live.
    """
    now = datetime.now(IST).strftime("%Y%m%d")

    shown_all = collections.defaultdict(list)      # who -> [(t, is_ours)]
    ours, raw, days = [], 0, collections.defaultdict(set)
    users, imp_freq = set(), collections.Counter()
    for rec in ct.export_event("inApp_Shown", START, now):
        ident = ct.identity_of(rec)
        t = _ts(rec)
        if not ident or t is None:
            continue
        who = (ct.cspid_of(rec) or ident).strip().lower()
        mine = CAMPAIGN in str(ct.props_of(rec).get("campaign_id", ""))
        shown_all[who].append((t, mine))
        if mine:
            ours.append((who, t))
            users.add(who)
            imp_freq[who] += 1
            raw += 1
            d = ct.day_of(rec)
            if d:
                days[d].add(who)

    dismissals = collections.defaultdict(list)     # who -> [t]
    n_dis = 0
    for rec in ct.export_event("inApp_Dismissed", START, now):
        ident = ct.identity_of(rec)
        t = _ts(rec)
        if not ident or t is None:
            continue
        dismissals[(ct.cspid_of(rec) or ident).strip().lower()].append(t)
        n_dis += 1

    for v in shown_all.values():
        v.sort()
    for v in dismissals.values():
        v.sort()

    matched, secs, closed_users = 0, [], set()
    closed_days = collections.defaultdict(set)
    used = collections.defaultdict(set)            # who -> {index of dismissal already claimed}
    for who, t in sorted(ours, key=lambda x: x[1]):
        cand = None
        for i, d in enumerate(dismissals.get(who, ())):
            if i in used[who] or d < t:
                continue
            gap = (d - t).total_seconds()
            if gap > DISMISS_WINDOW:
                break
            # another in-app opened in between → that dismissal belongs to it
            if any(t < o < d for o, _ in shown_all[who]):
                break
            cand = (i, gap, d)
            break
        if cand is None:
            continue
        used[who].add(cand[0])
        matched += 1
        secs.append(int(round(cand[1])))
        closed_users.add(who)
        closed_days[cand[2].strftime("%Y%m%d")].add(who)

    print(f"  {'inApp_Shown (' + CAMPAIGN + ')':28s} {raw:6d} records, {len(users):5d} unique CSPs")
    print(f"  {'inApp_Dismissed (all)':28s} {n_dis:6d} records, {matched:5d} matched to this campaign")
    return {"users": users, "raw": raw, "days": days, "imp_freq": imp_freq,
            "closed_users": closed_users, "closed_raw": matched, "closed_days": closed_days,
            "secs": secs, "dismissals_seen": n_dis}


def bucketise(secs):
    rows = []
    for lo, hi, label in BUCKETS:
        n = sum(1 for s in secs if lo <= s <= hi)
        rows.append({"label": label, "n": n})
    return rows


def freq_buckets(counter):
    rows = [{"label": str(i), "n": sum(1 for v in counter.values() if v == i)} for i in (1, 2, 3, 4)]
    rows.append({"label": "5+", "n": sum(1 for v in counter.values() if v >= 5)})
    return rows


def median(xs):
    if not xs:
        return None
    xs = sorted(xs)
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else round((xs[m - 1] + xs[m]) / 2, 1)


def main():
    data = {key: pull(ev) for ev, key, _ in STEPS}
    sysev = system_events()
    imp_users, imp_raw, imp_days = sysev["users"], sysev["raw"], sysev["days"]

    shown = data["shown"]["users"]
    understood = data["understood"]["users"]
    closed = data["closed"]["users"]
    acted = understood | closed

    funnel = []
    for ev, key, label in STEPS:
        b = data[key]
        funnel.append({
            "key": key, "event": PREFIX + ev, "label": label,
            "users": len(b["users"]), "raw": b["raw"],
        })

    # Every day the campaign has been live, so a gap reads as a gap.
    all_days = sorted({d for key in data for d in data[key]["days"]} | set(imp_days))
    daily = [{
        "d": d,
        "impressions": len(imp_days.get(d, ())),
        "dismissed": len(sysev["closed_days"].get(d, ())),
        "shown": len(data["shown"]["days"].get(d, ())),
        "understood": len(data["understood"]["days"].get(d, ())),
        "closed": len(data["closed"]["days"].get(d, ())),
    } for d in all_days]

    secs_all = data["understood"]["secs"] + data["closed"]["secs"]
    out = {
        "generated": datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
        "region": ct.REGION,
        "start_date": START,
        "creative": CREATIVE,
        "campaign_id": CAMPAIGN,
        "funnel": funnel,
        "totals": {
            "shown": len(shown),
            "understood": len(understood),
            "closed": len(closed),
            "acted": len(acted),
            # Shown but neither tap recorded: backgrounded the app, or the in-app
            # was dismissed by something other than the creative's own buttons.
            "no_action": len(shown - acted),
            # Acted with no Shown on record — bridge attached after paint. Never
            # folded into shown; a big number here means Shown is under-counting.
            "orphans": len(acted - shown),
            "raw_shown": data["shown"]["raw"],
            "impressions": len(imp_users),
            "raw_impressions": imp_raw,
            # System-event funnel: works today, cannot tell समझ गया from ✕.
            "dismissed": len(sysev["closed_users"]),
            "raw_dismissed": sysev["closed_raw"],
            "never_dismissed": imp_raw - sysev["closed_raw"],
            # Impressions CleverTap recorded where the creative's own Shown never
            # arrived = the bridge-not-ready blind spot, measured rather than guessed.
            "shown_gap": len(imp_users - shown),
        },
        "daily": daily,
        "seconds": {
            "understood": {"n": len(data["understood"]["secs"]),
                           "avg": round(sum(data["understood"]["secs"]) / len(data["understood"]["secs"]), 1)
                           if data["understood"]["secs"] else None,
                           "median": median(data["understood"]["secs"]),
                           "buckets": bucketise(data["understood"]["secs"])},
            "closed": {"n": len(data["closed"]["secs"]),
                       "avg": round(sum(data["closed"]["secs"]) / len(data["closed"]["secs"]), 1)
                       if data["closed"]["secs"] else None,
                       "median": median(data["closed"]["secs"]),
                       "buckets": bucketise(data["closed"]["secs"])},
            "all_median": median(secs_all),
        },
        "freq": freq_buckets(data["shown"]["freq"]),
        # Time on screen from the impression -> dismissal gap. One row per matched
        # screen session (events, not unique CSPs), which is the only attention
        # read available until the tracked creative is republished.
        "on_screen": {
            "n": len(sysev["secs"]),
            "avg": round(sum(sysev["secs"]) / len(sysev["secs"]), 1) if sysev["secs"] else None,
            "median": median(sysev["secs"]),
            "buckets": bucketise(sysev["secs"]),
            "window": DISMISS_WINDOW,
        },
        # How many times each CSP was served the in-app — from SDK impressions, so
        # it reflects the real frequency cap rather than the creative's own JS.
        "imp_freq": freq_buckets(sysev["imp_freq"]),
    }

    p = here("mera_network_data.json")
    json.dump(out, open(p, "w"), ensure_ascii=False, indent=1)
    t = out["totals"]
    print(f"\nwrote {p}")
    print(f"  impressions {t['impressions']} CSPs / {t['raw_impressions']} screens "
          f"-> dismissed {t['dismissed']} CSPs / {t['raw_dismissed']} screens "
          f"(never {t['never_dismissed']}) | median {out['on_screen']['median']}s on screen")
    print(f"  tracked events: shown {t['shown']} -> समझ गया {t['understood']} / ✕ {t['closed']} "
          f"| orphans {t['orphans']} | shown gap {t['shown_gap']}")


if __name__ == "__main__":
    main()
