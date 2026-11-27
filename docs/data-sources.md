# Data sources, terms and robots.txt review (P3-T01)

Rule (CLAUDE.md §5): use a source's **official API** whenever one exists; never parse its HTML pages for the same data, never log in
to a site, never keep private data. "Official API" means the interface the site itself publishes for programs (documented,
versioned, rate-limited, covered by API terms). HTML scraping is brittle, is usually forbidden by a site's terms, and gets
the whole project's IP or account blocked, which would stop the ingestion that every later phase depends on.

**Requirement:** at least 1,500 cards from **at least 3 sources**. Plan: three sources that need **no credential at all**
(Hacker News, GitHub, one more of Lobsters / Stack Exchange), plus Reddit and Product Hunt as optional extras that switch on
the day credentials exist. Nothing in the project depends on getting Reddit approved.

## How verified is each row
`V` = read from the provider's own page in this review. `R` = reported by third-party sources on 2026-11-27, **a human must
re-read the provider's terms page before submission** (link in the last column). `H` = from memory of well-known public
behaviour; verify. No row below has been read end to end by a human yet.

| Source | API | Credential | Limit (what we use) | Verified | Fields kept (no usernames, no private data) |
|---|---|---|---|---|---|
| Hacker News | Firebase API `hacker-news.firebaseio.com`, Algolia search `hn.algolia.com` | none | no documented hard limit for Firebase; we use 600/min | H | title, text, url, time, score, kind (Show/Ask) |
| GitHub | REST search `api.github.com` | optional token (read-only, no scopes) | unauthenticated 60/h core and 10/min search; with a token 5,000/h and 30/min search; we use 25/min | H | name, description, topics, stars, created_at, html_url |
| Lobsters | JSON endpoints `lobste.rs/hottest.json`, `/newest.json`, `/t/<tag>.json` | none | no published limit; "casual use": we poll at most a few requests/min | R | title, url, tags, score, created_at, comment count |
| Stack Exchange | API v2.3 `api.stackexchange.com` | optional free key (stackapps.com) | 300 requests/day/IP without a key, 10,000/day with one | R | question title, body excerpt, tags, score, creation_date, link |
| Reddit | Data API `oauth.reddit.com` (OAuth app) | **app credentials, approval required** | about 100 requests/min per client id after approval; we use 60/min | R | title, selftext, url, created_utc, subreddit (no usernames) |
| Product Hunt | GraphQL API v2 `api.producthunt.com/v2/api/graphql` | **developer token** (needs a Product Hunt account) | 450 requests/15 min on non-GraphQL endpoints (V), GraphQL by complexity points (figures conflict) | V (limits) / R (terms) | name, tagline, topics, votes, url |

Provider pages to re-read: Reddit [Responsible Builder Policy / Data API terms](https://support.reddithelp.com/hc/articles/42728983564564),
Product Hunt [API rate limits](https://www.producthunt.com/v2/docs/rate_limits/headers) and
[terms of service](https://www.producthunt.com/terms-of-service), Stack Exchange [API terms](https://api.stackexchange.com/docs),
GitHub [REST rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api),
Lobsters [about page](https://lobste.rs/about), Hacker News [API](https://github.com/HackerNews/API).

## Reddit: is there an API, and why not just scrape?
Yes: the **Reddit Data API** (OAuth 2, `https://oauth.reddit.com`). What changed: since late 2025 Reddit's *Responsible Builder
Policy* is reported to require **requesting access and getting explicit approval** for new apps; rejections can come without a
reason and without an appeal, and commercial use or model-training use needs a separate agreement. So the API exists but
approval is not guaranteed and may take days. Reddit's terms also forbid rotating IPs/accounts/apps to get around limits.
Consequence for this project: Reddit is an **optional, credential-gated** source (`enabled: false` in `config/sources.yaml` until
`REDDIT_CLIENT_ID/SECRET` exist). We do not scrape reddit.com HTML or old.reddit.com, and we do not use a logged-in browser.
Procedure when the owner decides to apply: sign in as the owner at reddit.com, open `https://www.reddit.com/prefs/apps`, create
a "script" app (name, description, redirect URI `http://localhost:8080`, state the purpose: *university coursework, read-only,
no storage of usernames, about 60 requests/min*), submit the access request through Reddit's developer support form, then put
`REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` in the git-ignored `.env`. A person has to do the sign-in; the credentials must
never be typed into a tool that stores them.

## Product Hunt
Yes: **GraphQL API v2** with a free developer token. Procedure: sign in at producthunt.com, open the API dashboard
(`https://www.producthunt.com/v2/oauth/applications`), create an application, generate a **developer token**, put it in `.env` as
`PRODUCTHUNT_TOKEN`. Third-party sources say the API may not be used commercially without permission: coursework is
non-commercial, but read the terms page and record the date here. Optional, `enabled: false` until the token exists.

## Why a third keyless source (decision, see ADR-009)
Reddit approval is outside our control and Product Hunt needs an account, so the "≥ 3 sources" requirement must not hinge on
either. Lobsters (curated tech stories, tags, discussion) and Stack Exchange (questions with real pain points, tags, scores)
are both official, keyless, JSON, and good evidence for "demand/pain" and "competition". Cards from every source keep the same
schema and `source` field, so adding Reddit or Product Hunt later changes nothing downstream.

## Collection rules (apply to every scout)
- All fetches go through M5 `/fetch` (allow-listed host, SSRF guard, per-host rate limit, sanitiser). Exact host match only.
- One identifying `User-Agent` with a contact URL (set in `config/sources.yaml`). Respect `Retry-After` and 429.
- Store only the fields in the table; never usernames, emails or profile data. Keep the canonical URL and fetch date.
- No logins, no cookies, no CAPTCHA solving, no proxy rotation, no headless browsing of pages that have an API.
- Cap per run and per source (`config/budgets.yaml`); an empty or failing source must degrade cleanly (tested, P9-T07).
- Scraping HTML is **not** a fallback for a denied API. If a source is unavailable we drop it and say so in the report.

## Attribution and licences (record per source before submission)
Stack Exchange content is CC BY-SA and the API expects attribution (link back to the question, keep the author credit rule in
mind: we store no usernames, so store the question link and the licence name). Reddit and Product Hunt content is governed by
their API terms. Hacker News and GitHub data are public; we store titles/descriptions/urls only. Cards carry `source` and
`url` so every claim in a verdict links back to the original page.
