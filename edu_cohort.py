#!/usr/bin/env python3
"""
Is the education working? — combines the R1+R2 bonus-education completers (CleverTap)
with each CSP's quality-bonus status (Snowflake via Metabase) and writes
quality_impact.json for bonus.html.

Cohort  = CSPs who completed R1 (Bonus_Seva_Quiz_Answered) OR R2 (Bonus_Seva2_Flow_Completed).
Compare = their bonus status (COMPOSITE_STATE) before vs now, vs everyone else (control).

Secrets: CLEVERTAP_ACCOUNT / CLEVERTAP_PASSCODE (eu1) + METABASE_API_KEY.
"""
import os, sys, json, urllib.request
from datetime import datetime, timezone, timedelta
import fetch_ct_data as ct          # reuses export_event(), cspid_of(), EXCLUDE_CSP

IST = timezone(timedelta(hours=5, minutes=30))
R1_EVENT = "Bonus_Seva_Quiz_Answered"      # completed round 1 (answered the quiz)
R2_EVENT = "Bonus_Seva2_Flow_Completed"    # completed round 2 (finished the story)
FROM = 20260716000000                       # education started 16 Jul
PRE_DATE  = "2026-07-15"                     # status just before the education
POST_DATE = None                            # None -> latest available snapshot


def completers(event):
    ids = set()
    for rec in ct.export_event(event, FROM, int(datetime.now(IST).strftime("%Y%m%d%H%M%S"))):
        c = ct.cspid_of(rec)
        if c and c.lower() not in ct.EXCLUDE_CSP:
            ids.add(c.lower())
    return ids


def metabase(sql):
    req = urllib.request.Request("https://metabase.wiom.in/api/dataset",
        data=json.dumps({"database": 113, "type": "native", "native": {"query": sql}}).encode(),
        headers={"x-api-key": os.environ["METABASE_API_KEY"], "Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=200))
    if d.get("error"):
        raise SystemExit("Metabase: " + str(d["error"])[:300])
    return [c["name"] for c in d["data"]["cols"]], d["data"]["rows"]


def here(f): return os.path.join(os.path.dirname(os.path.abspath(__file__)), f)


def main():
    r1, r2 = completers(R1_EVENT), completers(R2_EVENT)
    cohort = sorted(r1 | r2)
    print(f"R1={len(r1)} R2={len(r2)} combined={len(cohort)}")
    json.dump({"generated": datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
               "r1": sorted(r1), "r2": sorted(r2), "combined": cohort},
              open(here("education_cohort.json"), "w"), ensure_ascii=False)

    if not os.environ.get("METABASE_API_KEY"):
        print("no METABASE_API_KEY — wrote education_cohort.json only (skipping quality join)")
        return

    # latest snapshot date
    _, rows = metabase("SELECT MAX(SNAPSHOT_DATE)::string FROM PROD_DB.DBT_CSP.QUALITY_DAILY_METRIC_SNAPSHOTS")
    post_date = POST_DATE or rows[0][0][:10]

    inlist = "','".join(cohort)
    sql = f"""
    WITH snap AS (
      SELECT LOWER(CSP_ID) csp_id, SNAPSHOT_DATE,
             (COMPOSITE_STATE='COMPLIANT')::int comp, COMPOSITE_STATE, M5_AVG_RATING
      FROM PROD_DB.DBT_CSP.QUALITY_DAILY_METRIC_SNAPSHOTS
      WHERE SNAPSHOT_DATE IN (DATE '{PRE_DATE}', DATE '{post_date}') AND ETL_CURRENT=true )
    SELECT CASE WHEN csp_id IN ('{inlist}') THEN 'cohort' ELSE 'control' END grp,
      SNAPSHOT_DATE::string d, COUNT(*) scored, SUM(comp) compliant,
      ROUND(100.0*SUM(comp)/COUNT(*),1) pct_compliant, ROUND(AVG(M5_AVG_RATING),2) rating
    FROM snap GROUP BY 1,2 ORDER BY 1,2
    """
    cols, rows = metabase(sql)
    ix = {c: i for i, c in enumerate(cols)}
    grid = {}
    for r in rows:
        grid[(r[ix["GRP"]], r[ix["D"]][:10])] = {"scored": r[ix["SCORED"]], "compliant": r[ix["COMPLIANT"]],
                                                  "pct": float(r[ix["PCT_COMPLIANT"]]), "rating": float(r[ix["RATING"]] or 0)}

    # per-CSP transitions for the cohort (how many newly compliant / slipped)
    tsql = f"""
    WITH pre AS (SELECT LOWER(CSP_ID) csp_id, COMPOSITE_STATE st FROM PROD_DB.DBT_CSP.QUALITY_DAILY_METRIC_SNAPSHOTS WHERE SNAPSHOT_DATE=DATE '{PRE_DATE}' AND ETL_CURRENT=true),
         post AS (SELECT LOWER(CSP_ID) csp_id, COMPOSITE_STATE st FROM PROD_DB.DBT_CSP.QUALITY_DAILY_METRIC_SNAPSHOTS WHERE SNAPSHOT_DATE=DATE '{post_date}' AND ETL_CURRENT=true)
    SELECT
      SUM(CASE WHEN pre.st<>'COMPLIANT' AND post.st='COMPLIANT' THEN 1 ELSE 0 END) newly,
      SUM(CASE WHEN pre.st='COMPLIANT'  AND post.st<>'COMPLIANT' THEN 1 ELSE 0 END) slipped,
      SUM(CASE WHEN pre.st='COMPLIANT'  AND post.st='COMPLIANT' THEN 1 ELSE 0 END) stayed,
      SUM(CASE WHEN post.st='COMPLIANT' THEN 1 ELSE 0 END) compliant_now,
      COUNT(*) matched
    FROM (SELECT column1 csp_id FROM (VALUES {','.join("('%s')"%c for c in cohort)}) ) c
    LEFT JOIN pre ON pre.csp_id=c.csp_id LEFT JOIN post ON post.csp_id=c.csp_id
    """
    tcols, trows = metabase(tsql)
    t = dict(zip([c.lower() for c in tcols], trows[0]))

    out = {
        "generated": datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
        "pre_date": PRE_DATE, "post_date": post_date,
        "cohort": {"r1": len(r1), "r2": len(r2), "combined": len(cohort)},
        "cohort_pre":  grid.get(("cohort", PRE_DATE)),  "cohort_post":  grid.get(("cohort", post_date)),
        "control_pre": grid.get(("control", PRE_DATE)), "control_post": grid.get(("control", post_date)),
        "transitions": {k: int(v or 0) for k, v in t.items()},
    }
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "quality_impact.json"), "w"),
              ensure_ascii=False, indent=2)
    print("wrote quality_impact.json:", json.dumps(out["cohort"]), "| post", post_date)


if __name__ == "__main__":
    main()
