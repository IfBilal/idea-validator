# ingestion (M6i): raw scout items to cards in the Knowledge Base

Batch job (docs/harness/10 section 5). Reads the scouts' raw items, turns them into `IdeaCard`s, removes
duplicates, and posts them to M6 `POST /ingest`. Also writes and restores card snapshots.

## What it does

1. **Read** raw items: `RawItem {source, url, title, text, created_at, score|stars, platform, metadata}`
   from a JSONL file (`.jsonl` or `.jsonl.gz`) or, later, the scouts' `raw_pages` table (`PostgresRawPages`
   behind the `RawSource` protocol; not exercised against a real database yet, `row_to_item` is unit tested).
   A malformed row is counted and skipped, never fatal.
2. **Map** per source to a card (`mappers.py`): canonical URL (`urls.py`: tracking params, fragment, host case,
   known mirrors such as `old.reddit.com`), `kind` by deterministic rules (repo / product / request / complaint /
   discussion), text cleaned, e-mail addresses and `u/handle` mentions masked, author fields never read.
3. **Dedupe** (`dedupe.py`): same canonical URL, same content hash, then MinHash near-duplicates (Jaccard of
   name+summary 2-word shingles `>= INGESTION_MINHASH_THRESHOLD`, default 0.9, fixed seed, banded LSH candidates
   confirmed by the exact Jaccard). Every platform that mentioned a page is kept: the representative card is posted
   once, then once more per extra platform with identical content, so M6 records it in `card_sources`
   (`n_platforms` for the evidence gate). The result does not depend on input order.
4. **Post** in batches (at most 50, M6's cap) with the internal token, timeouts, at most 2 retries on 429/5xx.
   A resume cursor (`<state dir>/cursor.json`, tied to a digest of the planned cards) lets a run that failed part-way
   continue; a changed input starts over. A batch M6 refuses with 422 is retried card by card and the bad card is
   dropped and counted. Every run appends a row to `<state dir>/ingestion_runs.jsonl`.
5. **Snapshot / restore** (`snapshot.py`): see below.

M6 mints card ids (`card_<8 hex of sha1(canonical url)>`, longer on collision), chunks the text (every chunk keeps
its `card_id`) and indexes it, so the job sends cards, not chunks.

## Commands

```bash
export KB_URL=http://localhost:8000 INTERNAL_TOKEN=...          # M6 address and token (never in git)
uv run --package idea-validator-ingestion python -m ingestion run --input raw.jsonl     # ingest
uv run --package idea-validator-ingestion python -m ingestion run                       # from raw_pages (DATABASE_URL)
uv run --package idea-validator-ingestion python -m ingestion snapshot                  # data/snapshots/cards-YYYYMMDD.jsonl.gz + manifest
uv run --package idea-validator-ingestion python -m ingestion restore                   # newest snapshot in data/snapshots
uv run --package idea-validator-ingestion python -m ingestion restore data/snapshots/cards-20260301.jsonl.gz
make seed                                                       # kind cluster: build image, run the seed Job
```

Exit codes: 0 done, 1 the run failed part-way (run again to resume), 2 bad usage or configuration, 3 snapshot
missing, empty or corrupt. The last stdout line is the JSON report (`added`, `updated`, `unchanged`, `dropped`,
`duplicates`, ...).

## Configuration (environment)

`KB_URL`, `INTERNAL_TOKEN` (Secret), `DATABASE_URL` (Secret, only for `raw_pages`), `INGESTION_BATCH_SIZE` (1-50),
`INGESTION_MAX_RETRIES` (0-2), `INGESTION_TIMEOUT_S`, `INGESTION_MINHASH_THRESHOLD|NUM_PERM|BANDS|SEED`,
`INGESTION_STATE_DIR` (default `/tmp/ingestion`), `LOG_LEVEL`.

## Snapshots

`cards-YYYYMMDD.jsonl.gz` (one card per line, sorted by id, gzip with a zero timestamp so identical corpora give
identical bytes) and `cards-YYYYMMDD.manifest.json` (total, counts per source / platform / kind, sha256). Only
public card fields are written. An empty knowledge base is refused, so a failed seed can never overwrite a good
snapshot. `restore` checks the manifest checksum, skips malformed rows and rows whose text no longer matches its
hash, and is idempotent. Large snapshots go to a release asset (see `data/snapshots/README.md`).

## Tests

`uv run pytest jobs/ingestion`. `tests/synthetic.py` generates **synthetic** raw items (reserved `.invalid` hosts,
the `synthetic` topic) for tests only; it is never an input to a snapshot and is not in the image.

## Image

`docker build -f jobs/ingestion/Dockerfile -t ingestion:$(git rev-parse --short=7 HEAD) .` (multi-stage, UID 10001,
pinned base). It carries `data/snapshots`, and its default command restores the newest one.
