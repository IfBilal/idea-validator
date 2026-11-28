# Card snapshots

`cards-YYYYMMDD.jsonl.gz` is the restorable card corpus (`python -m ingestion snapshot` / `restore`, `make seed`), with a
manifest holding counts per source and the sha256. Only public card fields are stored (no author names, e-mails masked).

Licences and terms, per source (see `docs/data-sources.md`):
- Hacker News: public Algolia/Firebase API; titles and text of public posts, kept with their source URL.
- GitHub: public repository metadata through the official REST API (name, description, topics, stars).
- Stack Exchange: content is CC BY-SA 4.0. Every card keeps its question URL, which is the attribution; the snapshot is
  distributed under the same licence for those rows.
- Lobsters is not in the corpus: its robots.txt disallows crawlers (`User-agent: *  Disallow: /`) and says
  `ai-train=no`, so M5 refuses it and the connector is off.
- Nothing from Reddit is stored; Reddit-derived data must never be used for training anyway.
