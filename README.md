# Wiom campaign dashboards

CleverTap-backed tracking dashboards, served on GitHub Pages and auto-refreshed
every ~30 minutes by GitHub Actions.

| Page | Campaign | Data | Fetcher |
|---|---|---|---|
| [`dashboard.html`](https://vikaswiom.github.io/wiom-offer-education-campaign/dashboard.html) | Offer Education in-app (CSP + Technician apps) | `data.json` | `fetch_ct_data.py` |
| [`bonus.html`](https://vikaswiom.github.io/wiom-offer-education-campaign/bonus.html) | Bonus Seva video in-app (`Bonus_Seva_*` events) | `bonus_data.json` | `fetch_bonus_data.py` |

## How the refresh works

`.github/workflows/refresh-ct-data.yml` runs both fetchers on a 30-min schedule
(off-peak cron minutes `11,41` — GitHub's scheduler drops the congested
quarter-hour slots) and pushes the JSONs only when the numbers changed; Pages
redeploys on push. Manual refresh: Actions → **refresh-ct-data** → Run workflow.

Secrets (Settings → Secrets and variables → Actions): `CLEVERTAP_ACCOUNT`,
`CLEVERTAP_PASSCODE`. Region defaults to `eu1` in code. The job skips quietly
if the secrets are missing. **Never commit credentials — this repo is public.**

`probe-campaign.yml` + `probe_campaign.py` is a read-only diagnostic for any
campaign id (campaign stats endpoint, Notification Viewed/Clicked and
inApp_Shown export search, campaign_id breakdown). Run it from the Actions tab
whenever the CleverTap UI and a dashboard disagree.

## Attribution notes (why the fetchers look the way they do)

- All counts are **unique users deduped on profile identity**, from the events
  export API (`/1/events.json`) — the counts API cannot split by app.
- Only `inApp_Shown` (a custom event fired by app code) carries `campaign_id`;
  events fired by the in-app HTML itself carry no app marker, so downstream
  steps attribute by identity intersection with the shown cohort.
- The **Technician app does not fire `inApp_Shown`**. Its impressions exist
  only as CleverTap's system event `Notification Viewed` with
  `wzrk_id = "<campaignId>_<YYYYMMDD>"` — the same counter the campaigns UI
  shows as "viewed". The Technician funnel's Shown step uses that (locked
  cohort), with `role = technician` profile-property filtering as fallback.
- CleverTap returns HTTP 400 for event names it has never seen — the normal
  state before a new campaign's first fire. Fetchers count that as zero and
  the dashboards render a pending state until launch.

Campaign HTML sources and the full working context live in
`Vikaswiom/wiom-mbg-kamai-kavach` → `clevertap-campaigns/` (branch
`claude/clevrtap-campaign-clickable-88k0sl`), guarantee-campaign pages in
`Vikaswiom/wiom-csp-guarantee-campaign`, and videos in
`Vikaswiom/wiom-inapp-video` (served via jsDelivr; always upload new videos
under a new filename to dodge CDN caching).
