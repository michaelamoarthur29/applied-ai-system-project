"""Culture retriever: keyword search over data/culture_notes.json.

Each note is a short document about a genre (real music history) or an artist
(fictional bios for the simulated catalog). Retrieval is deliberately simple and
deterministic so results are explainable and testable:

    score = 3 if the topic names the note's subject exactly
          + 1 for each topic word that appears in the note's tags
"""

import json
import re
from typing import Dict, List

from src.config import NOTES_PATH


def load_notes(path=NOTES_PATH) -> List[Dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _words(text: str) -> set:
    return set(re.findall(r"[a-z0-9&'-]+", text.lower()))


MAX_TOPICS = 12
MAX_NOTES = 12


def retrieve_notes(topics: List[str], notes: List[Dict], k: int = 4) -> List[Dict]:
    """Return notes relevant to the given topics (genres, artists, themes).

    Every topic that exactly names a note's subject is guaranteed its note. Remaining slots
    (up to k) are filled by tag-overlap score. Fix from testing: a plain top-k by score let
    tag-heavy artist notes push out the genre note the agent asked for by name.
    """
    seen, clean = set(), []
    for t in topics:
        t = (t or "").strip().lower()
        if t and t not in seen:
            seen.add(t)
            clean.append(t)
    topics = clean[:MAX_TOPICS]
    if not topics:
        return []

    by_about = {n["about"].lower(): n for n in notes}
    exact = [by_about[t] for t in topics if t in by_about]

    topic_words = set().union(*(_words(t) for t in topics))
    scored = []
    for note in notes:
        if note in exact:
            continue
        tags = {t.lower() for t in note["tags"]}
        tag_words = set().union(*(_words(t) for t in tags)) if tags else set()
        score = sum(1 for t in topics if t in tags) + len(topic_words & tag_words) * 0.5
        if score > 0:
            scored.append((score, note))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    limit = min(max(k, len(exact)), MAX_NOTES)
    return (exact + [note for _, note in scored])[:limit]
