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
| Lobsters (**off**, see below) | JSON endpoints `lobste.rs/hottest.json`, `/newest.json`, `/t/<tag>.json` | none | no published limit; "casual use": we poll at most a few requests/min | R | title, url, tags, score, created_at, comment count |
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

## Terms actually read (2026-11-27) and the decision on scraping Reddit and Product Hunt
The owner asked to collect Reddit and Product Hunt with browser automation, accepting the terms risk. What the providers' own
pages say, read in a browser on 2026-11-27:

- **Reddit** (`support.reddithelp.com`, article "Responsible Builder Policy", read in full): "Approval is required: you must request
  access and get explicit approval before accessing any Reddit data through our API"; "you must not misrepresent or mask how or why
  you are accessing Reddit data"; and, under *No Unapproved Commercialization or AI Training*, "this extends to commercial and
  **non-commercial** mining, **scraping**, or using data for purposes like ads targeting or to **train machine learning or AI
  models**". Researchers must not keep copies beyond the immediate project. So scraping Reddit is prohibited outright, and so is
  training on it without approval, and this project fine-tunes a model (M10t).
- **Product Hunt** (`producthunt.com/terms-of-service`): **not read**. Every request from an automated browser, even for the terms
  page, is answered with a Cloudflare "Just a moment..." challenge (HTTP 403), and the Internet Archive has no copy. Passing a bot
  challenge is evading an access control, which we do not do, so a human must read the terms in a normal browser. The rate-limit
  page (V, above) is the only first-party Product Hunt text verified.

**Decision (owner request reviewed):** no scraping of Reddit or Product Hunt, no logins, no challenge evasion.
1. It is not needed: the 3-source minimum is met by Hacker News, GitHub and Lobsters (ADR-009).
2. It would not be allowed to feed the model: Reddit forbids AI training use even with API access, so Reddit-derived cards must
   be **excluded from every fine-tuning set** (`finetune.build_candidates` filters `source != "reddit"`), and are retrieval-only.
3. The risks are real and the benefit is zero: bans for the owner's IP/accounts, a coursework submission built on prohibited data,
   and a bot challenge that makes Product Hunt unreliable to automate anyway.
What the owner can create so both sources can still be added through their **official** interfaces (a person must sign in; paste the
values into the git-ignored `.env`, never into git):
- Product Hunt: sign in, open `https://www.producthunt.com/v2/oauth/applications`, create an application, copy the **developer
  token** -> `PRODUCTHUNT_TOKEN`. Then read the terms page in a normal browser and record the date here.
- Reddit: sign in, create a "script" app at `https://www.reddit.com/prefs/apps`, and submit the access request through Reddit's
  developer support form (purpose: university coursework, read-only, no storage of usernames, about 60 requests/min, no model
  training on Reddit data). If approved: `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`. Reddit may refuse; nothing depends on it.

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

## Personal data decision (scouts, 2026-11-27)
Scouts store **public project identifiers only**: title, text, canonical url, score/stars, topics/tags, timestamps. They never store an
author, username, submitter, owner, display name or avatar (Hacker News `by`/`author`, Lobsters `submitter_user`, GitHub `owner`,
Stack Exchange `owner`, Reddit `author`, Product Hunt makers are dropped at parse time; `RawItem.metadata` has a key whitelist and
`services/scouts/tests/component/test_scouts_security_review.py` scans every recorded fixture for such keys and names). Free text may
still mention people: it is kept short (8,000 chars), and processed rows can be deleted with `python -m scouts prune`.


## Finding from the first live run (2026-11-27): Lobsters is off
Running the scouts for real, Lobsters failed with "M5 refused the request". Its `robots.txt` says `User-agent: *  Disallow: /` and
`Content-Signal: ai-input=no, ai-train=no`; only a few named search engines are allowed. M5 checks robots.txt, so it refused, and
that is correct: we do not work around it. The source is disabled in `config/sources.yaml` and its CronJob is suspended. The three-source
minimum is met by Hacker News, GitHub and Stack Exchange (all three ran live through M5: 500 + 500 + 390 raw items, 1,387 cards).

## First snapshot and what it can answer
`data/snapshots/cards-20261127.jsonl.gz`: 1,387 cards (GitHub 500 repos, Hacker News 497 Show/Ask HN, Stack Exchange 390
programmer questions). The mix is developer-heavy, so retrieval queries about students, journaling, event management and similar
were not answerable; `eval/retrieval/queries.yaml` was adapted to what the corpus can answer. More topics need more scout topics or
sources (Product Hunt and Reddit once credentials exist), not different code.
