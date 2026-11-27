# ADR-009: a third keyless data source; Reddit and Product Hunt become optional

- **Title:** A third keyless official data source; Reddit and Product Hunt optional
- **Status:** Accepted (2026-11-27), owner Dev A
- **Context:** The requirement is at least 1,500 cards from at least 3 sources. The backlog (P3-T05) assumed Reddit and/or
  Product Hunt. Reddit's Data API is reported to require approval for new apps (to be confirmed on Reddit's own policy page),
  which can be refused without a reason; Product Hunt needs a person to sign in and create a token. The team cannot make either
  happen on a schedule, and CLAUDE.md §5 forbids the workaround (scraping HTML, logging in).
- **Options:** (1) wait for Reddit approval and a Product Hunt token. (2) scrape reddit.com / producthunt.com HTML or use a
  logged-in headless browser. (3) take a third source that is official and needs no credential (Lobsters JSON, Stack Exchange
  API) and keep Reddit and Product Hunt as optional, credential-gated scouts.
- **Decision:** Option 3. Required sources: Hacker News, GitHub, Lobsters (Stack Exchange as the next one). The Reddit and
  Product Hunt scouts are built against their official APIs and stay `enabled: false` until credentials exist; adding them later
  changes no schema, card or downstream code.
- **Why:** No human action blocks Phase 3. Option 1 puts the schedule in a third party's hands. Option 2 breaks the sites'
  terms, risks an IP/account ban that would stop ingestion, is brittle, and violates a project hard rule.
- **Cost:** Allow-list gains `lobste.rs` and `api.stackexchange.com` (exact hosts, https) in the scouts PR, with SSRF tests.
  Stack Exchange needs attribution (CC BY-SA); Lobsters needs polite polling. "Complaint"-type cards may be thinner than with
  Reddit; the retrieval evaluation and labelled set will show whether that matters.
- **AI use:** Drafted by AI; source terms were looked up by web search on 2026-11-27 and are marked V/R/H in
  `docs/data-sources.md`. A human must re-read the providers' own terms before submission.
