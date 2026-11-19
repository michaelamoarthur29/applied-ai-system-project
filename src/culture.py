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


def retrieve_notes(topics: List[str], notes: List[Dict], k: int = 4) -> List[Dict]:
    """Return up to k notes ranked by relevance to the given topics (genres, artists, themes)."""
    topics = [t.strip().lower() for t in topics if t and t.strip()]
    if not topics:
        return []
    topic_words = set().union(*(_words(t) for t in topics))
    scored = []
    for note in notes:
        about = note["about"].lower()
        tags = {t.lower() for t in note["tags"]}
        tag_words = set().union(*(_words(t) for t in tags)) if tags else set()
        score = 0
        if about in topics:
            score += 3
        score += sum(1 for t in topics if t in tags)
        score += len(topic_words & tag_words) * 0.5
        if score > 0:
            scored.append((score, note))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    return [note for _, note in scored[:k]]
