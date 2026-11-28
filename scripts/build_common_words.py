#!/usr/bin/env python3
"""Build services/critic/src/svc_critic/data/common_words.txt from the card snapshot (M12 entity check).

M12 must flag a product name that the cited evidence does not contain, but a capitalised word at the start
of a sentence ("Effort fits the budget.") is just a sentence. A word counts as ordinary English when,
in the corpus, it appears in lower case at least MIN_LOWER times, is never capitalised in the middle of
a sentence (which is how names behave: "alternative to Notion") and is capitalised at most a third as
often as it is lower case. Run after a new snapshot:
    uv run python scripts/build_common_words.py [snapshot.jsonl.gz]
"""

import gzip
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "services/critic/src/svc_critic/data/common_words.txt"
MIN_LOWER = 5
COMMON_ENOUGH = (
    25  # a word in a card name is a product word unless it is very common in running text
)
# (a gerund such as "detecting" is a verb even when a card name contains it; brands ending in -ing
# such as booking are in data/brand_words.txt)
BRANDS = (
    ROOT / "services/critic/src/svc_critic/data/brand_words.txt"
)  # products that are also words
_WORD = re.compile(r"[A-Za-z][a-z]{2,}")  # 3+ letters, no inner capitals (not CamelCase)


def build(texts: list[str], name_words: frozenset[str] = frozenset()) -> list[str]:
    brands = {w for w in BRANDS.read_text().split() if not w.startswith("#")}
    lower: Counter[str] = Counter()
    cap_mid: Counter[str] = Counter()
    cap_all: Counter[str] = Counter()
    for text in texts:
        for match in _WORD.finditer(text):
            word = match.group(0)
            start = match.start()
            if word[0].islower():
                lower[word] += 1
                continue
            cap_all[word.lower()] += 1
            before = text[:start].rstrip()
            sentence_start = (
                not before or before[-1] in ".!?:\n#*-" or text[max(0, start - 1)] == "\n"
            )
            if not sentence_start:
                cap_mid[word.lower()] += 1
    return sorted(
        w
        for w, n in lower.items()
        if n >= MIN_LOWER
        and cap_mid[w] == 0
        and cap_all[w] * 3 <= n
        and w not in brands
        and not (w in name_words and n < COMMON_ENOUGH and not w.endswith("ing"))
    )


def main() -> None:
    files = sorted((ROOT / "data/snapshots").glob("cards-*.jsonl.gz"))
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else files[-1]
    texts: list[str] = []
    names: set[str] = set()
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            texts.append(f"{row['name']}. {row['text']}")
            names.update(w.lower() for w in re.findall(r"[A-Za-z]{3,}", row["name"]))
    words = build(
        texts, frozenset(names)
    )  # a word in any card name is a product word, not ordinary
    OUT.write_text("\n".join(words) + "\n")
    print(f"{len(words)} words from {path.name} -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
