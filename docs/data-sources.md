# Data sources, terms and robots.txt review (fill in Phase 3, task P3-T01)
| Source | API / URL | Terms read (link, date) | robots.txt checked | Rate limit used | Fields kept | Key? |
|---|---|---|---|---|---|---|
| Hacker News | Firebase API + Algolia HN Search | | | | name/title, text, url, time | no |
| GitHub | REST search API | | | 25/min | name, description, topics, stars, created_at, html_url | token |
| Reddit | OAuth API | | | 60/min | title, selftext, url, created_utc (no usernames) | app creds |
| Product Hunt | GraphQL v2 | | | | name, tagline, topics, votes, url | token |
