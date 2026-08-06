#!/usr/bin/env python3
"""
Is the education working? — combines the R1+R2 bonus-education completers (CleverTap)
with each CSP's quality-bonus status (Snowflake via Metabase) → quality_impact.json.

Cohort  = CSPs who completed R1 (Bonus_Seva_Quiz_Answered) OR R2 (Bonus_Seva2_Flow_Completed).
Compare = their bonus status (COMPOSITE_STATE) before the education vs now, vs everyone
          else (control). CleverTap dedups on profile identity (phone); we map identity ->
          cspId via CLEVERTAP_CSP_API.PROFILE_DATA before joining to the quality snapshots.

Secrets: CLEVERTAP_ACCOUNT / CLEVERTAP_PASSCODE (eu1) + METABASE_API_KEY.
"""
import os, sys, json, urllib.request
from datetime import datetime, timezone, timedelta
import fetch_ct_data as ct          # export_event(), identity_of(), creds/plumbing

IST = timezone(timedelta(hours=5, minutes=30))
R1_EVENT = "Bonus_Seva_Quiz_Answered"      # completed round 1 (answered the quiz)
R2_EVENT = "Bonus_Seva2_Flow_Completed"    # completed round 2 (finished the story)
FROM = 20260716000000                       # education started 16 Jul
PRE_DATE = "2026-07-15"                      # status just before the education


def here(f): return os.path.join(os.path.dirname(os.path.abspath(__file__)), f)


def completer_identities(event):
    ids, csps, n, sample = set(), set(), 0, None
    now = int(datetime.now(IST).strftime("%Y%m%d%H%M%S"))
    for rec in ct.export_event(event, FROM, now):
        n += 1
        if sample is None:
            p = rec.get("profile") or {}
            sample = {"profile_keys": list(p.keys()),
                      "profileData_keys": list((p.get("profileData") or {}).keys()),
                      "identity": p.get("identity"), "objectId": p.get("objectId")}
        i = ct.identity_of(rec)                 # profile identity (phone), test shop already dropped
        c = ct.cspid_of(rec)
        if i: ids.add(str(i).strip())
        if c: csps.add(c.strip().lower())
    print(f"  {event}: {n} records, {len(ids)} identities, {len(csps)} cspids | sample={sample}")
    return ids, csps


def metabase(sql):
    req = urllib.request.Request("https://metabase.wiom.in/api/dataset",
        data=json.dumps({"database": 113, "type": "native", "native": {"query": sql}}).encode(),
        headers={"x-api-key": os.environ["METABASE_API_KEY"], "Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=240))
    if d.get("error"):
        raise SystemExit("Metabase: " + str(d["error"])[:400])
    return [c["name"] for c in d["data"]["cols"]], d["data"]["rows"]


def identities_to_csps(idents):
    if not idents:
        return []
    inlist = "','".join(x.replace("'", "") for x in idents)
    sql = f"""
    SELECT DISTINCT LOWER(CSPID) csp_id
    FROM PROD_DB.CLEVERTAP_CSP_API.PROFILE_DATA
    WHERE _FIVETRAN_DELETED=FALSE AND CSPID IS NOT NULL AND LOWER(CSPID)<>'a0a0b1'
      AND (IDENTITY IN ('{inlist}') OR PHONE IN ('{inlist}'))
    """
    _, rows = metabase(sql)
    return sorted(r[0] for r in rows if r[0])


def main():
    id_r1, cspd_r1 = completer_identities(R1_EVENT)
    id_r2, cspd_r2 = completer_identities(R2_EVENT)
    print(f"R1={len(id_r1)} ids/{len(cspd_r1)} csp  R2={len(id_r2)} ids/{len(cspd_r2)} csp")

    if not os.environ.get("METABASE_API_KEY"):
        json.dump({"generated": datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
                   "r1_identities": sorted(id_r1), "r2_identities": sorted(id_r2)},
                  open(here("education_cohort.json"), "w"), ensure_ascii=False)
        print("no METABASE_API_KEY — wrote identities only"); return

    # Prefer the cspid the export already carries (profileData.cspid); only fall
    # back to mapping identity->cspId via PROFILE_DATA for records that lacked it.
    csp_r1 = set(cspd_r1) | set(identities_to_csps(id_r1)) if not cspd_r1 else set(cspd_r1)
    csp_r2 = set(cspd_r2) | set(identities_to_csps(id_r2)) if not cspd_r2 else set(cspd_r2)
    csp_r1.discard("a0a0b1"); csp_r2.discard("a0a0b1")
    cohort = sorted(csp_r1 | csp_r2)
    print(f"mapped cspIds  R1={len(csp_r1)} R2={len(csp_r2)} combined={len(cohort)}")
    json.dump({"generated": datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
               "r1": sorted(csp_r1), "r2": sorted(csp_r2), "combined": cohort},
              open(here("education_cohort.json"), "w"), ensure_ascii=False)
    if not cohort:
        raise SystemExit("cohort empty after identity->csp mapping")

    _, rows = metabase("SELECT MAX(SNAPSHOT_DATE)::string FROM PROD_DB.DBT_CSP.QUALITY_DAILY_METRIC_SNAPSHOTS")
    post_date = rows[0][0][:10]
    inlist = "','".join(cohort)

    # cohort vs control, pre & post: compliant % and avg rating
    cols, rows = metabase(f"""
    WITH snap AS (
      SELECT LOWER(CSP_ID) csp_id, SNAPSHOT_DATE, (COMPOSITE_STATE='COMPLIANT')::int comp, M5_AVG_RATING
      FROM PROD_DB.DBT_CSP.QUALITY_DAILY_METRIC_SNAPSHOTS
      WHERE SNAPSHOT_DATE IN (DATE '{PRE_DATE}', DATE '{post_date}') AND ETL_CURRENT=true )
    SELECT CASE WHEN csp_id IN ('{inlist}') THEN 'cohort' ELSE 'control' END grp,
      SNAPSHOT_DATE::string d, COUNT(*) scored, SUM(comp) compliant,
      ROUND(100.0*SUM(comp)/COUNT(*),1) pct, ROUND(AVG(M5_AVG_RATING),2) rating
    FROM snap GROUP BY 1,2 ORDER BY 1,2 """)
    ix = {c: i for i, c in enumerate(cols)}
    grid = {(r[ix["GRP"]], r[ix["D"]][:10]): {"scored": r[ix["SCORED"]], "compliant": r[ix["COMPLIANT"]],
            "pct": float(r[ix["PCT"]]), "rating": float(r[ix["RATING"]] or 0)} for r in rows}

    # per-CSP transitions for the cohort
    tcols, trows = metabase(f"""
    WITH pre AS (SELECT LOWER(CSP_ID) csp_id, COMPOSITE_STATE st FROM PROD_DB.DBT_CSP.QUALITY_DAILY_METRIC_SNAPSHOTS WHERE SNAPSHOT_DATE=DATE '{PRE_DATE}' AND ETL_CURRENT=true),
         post AS (SELECT LOWER(CSP_ID) csp_id, COMPOSITE_STATE st FROM PROD_DB.DBT_CSP.QUALITY_DAILY_METRIC_SNAPSHOTS WHERE SNAPSHOT_DATE=DATE '{post_date}' AND ETL_CURRENT=true),
         c AS (SELECT column1 csp_id FROM (VALUES {','.join("('%s')" % x for x in cohort)}))
    SELECT
      SUM(CASE WHEN pre.st<>'COMPLIANT' AND post.st='COMPLIANT' THEN 1 ELSE 0 END) newly,
      SUM(CASE WHEN pre.st='COMPLIANT'  AND post.st<>'COMPLIANT' THEN 1 ELSE 0 END) slipped,
      SUM(CASE WHEN pre.st='COMPLIANT'  AND post.st='COMPLIANT' THEN 1 ELSE 0 END) stayed,
      SUM(CASE WHEN post.st='COMPLIANT' THEN 1 ELSE 0 END) compliant_now,
      COUNT(post.csp_id) matched_post
    FROM c LEFT JOIN pre ON pre.csp_id=c.csp_id LEFT JOIN post ON post.csp_id=c.csp_id """)
    t = {k.lower(): int(v or 0) for k, v in zip(tcols, trows[0])}

    out = {"generated": datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
           "pre_date": PRE_DATE, "post_date": post_date,
           "cohort": {"r1": len(csp_r1), "r2": len(csp_r2), "combined": len(cohort)},
           "cohort_pre": grid.get(("cohort", PRE_DATE)),   "cohort_post": grid.get(("cohort", post_date)),
           "control_pre": grid.get(("control", PRE_DATE)), "control_post": grid.get(("control", post_date)),
           "transitions": t}
    json.dump(out, open(here("quality_impact.json"), "w"), ensure_ascii=False, indent=2)
    print("wrote quality_impact.json:", json.dumps(out["cohort"]), "post", post_date)


if __name__ == "__main__":
    main()
