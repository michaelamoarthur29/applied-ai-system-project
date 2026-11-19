"""Input and output guardrails for MuseIQ Lite.

Input:  validate_request() rejects empty / oversized input and flags likely prompt injection.
Output: check_capsule() verifies a Claude-built capsule is grounded and fair before it is shown.
Fallback ranking: fair_rerank() applies the same fairness and repetition rules to rule-based results.
"""

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Set, Tuple

from src.config import MAX_REQUEST_CHARS, SPOTLIGHT_SHARE

INJECTION_PATTERNS = [
    r"ignore (all |any |the |your )?(previous |prior |above )?(instructions|rules)",
    r"system prompt",
    r"you are now",
    r"disregard (the |your )?(rules|instructions)",
    r"developer mode",
]


class InputError(ValueError):
    """Raised when a listener request can't be processed safely."""


@dataclass
class CleanRequest:
    text: str
    flags: List[str] = field(default_factory=list)


def validate_request(raw: str) -> CleanRequest:
    """Normalize the request; raise InputError for empty or too-long input."""
    if raw is None:
        raise InputError("Request is empty. Describe what you want to listen to.")
    # drop control characters (keeps newlines/tabs), collapse whitespace
    text = "".join(ch for ch in raw if unicodedata.category(ch)[0] != "C" or ch in "\n\t")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise InputError("Request is empty. Describe what you want to listen to.")
    if len(text) > MAX_REQUEST_CHARS:
        raise InputError(
            f"Request is {len(text)} characters; the limit is {MAX_REQUEST_CHARS}. Please shorten it."
        )
    flags = []
    lowered = text.lower()
    if any(re.search(p, lowered) for p in INJECTION_PATTERNS):
        flags.append("possible_prompt_injection")
    return CleanRequest(text=text, flags=flags)


def spotlight_needed(k: int) -> int:
    return math.ceil(SPOTLIGHT_SHARE * k)


def check_capsule(
    capsule: Dict,
    songs_by_id: Dict[int, Dict],
    retrieved_song_ids: Set[int],
    retrieved_note_ids: Set[str],
    k: int,
) -> List[str]:
    """Return a list of rule violations (empty list = capsule passes)."""
    problems = []
    picks = capsule.get("picks") or []
    if not str(capsule.get("title", "")).strip():
        problems.append("Title is empty.")
    if len(picks) != k:
        problems.append(f"Capsule has {len(picks)} songs; exactly {k} are required.")

    seen_ids, seen_artists, spotlight = set(), set(), 0
    for pick in picks:
        sid = pick.get("song_id")
        song = songs_by_id.get(sid)
        if song is None:
            problems.append(f"Song id {sid} is not in the catalog. Recommend catalog songs only.")
            continue
        if sid not in retrieved_song_ids:
            problems.append(
                f"Song id {sid} ('{song['title']}') was not returned by search_songs. "
                "Only recommend songs you retrieved."
            )
        if sid in seen_ids:
            problems.append(f"Song id {sid} appears more than once.")
        seen_ids.add(sid)
        if song["artist"] in seen_artists:
            problems.append(f"Artist '{song['artist']}' appears more than once (repetition rule).")
        seen_artists.add(song["artist"])
        if song.get("spotlight"):
            spotlight += 1
        if not str(pick.get("why", "")).strip():
            problems.append(f"Song id {sid} has no explanation.")

    need = spotlight_needed(k)
    if spotlight < need:
        problems.append(
            f"Only {spotlight} spotlight song(s); at least {need} required (fairness rule). "
            "Try search_songs with spotlight_only=true."
        )

    note_ids = capsule.get("note_ids") or []
    if not note_ids:
        problems.append("No culture notes cited. Ground the story in get_culture_notes results.")
    for nid in note_ids:
        if nid not in retrieved_note_ids:
            problems.append(f"Note '{nid}' was not retrieved with get_culture_notes.")
    if not str(capsule.get("story", "")).strip():
        problems.append("Story is empty.")

    conf = capsule.get("confidence")
    if not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
        problems.append("Confidence must be a number between 0 and 1.")
    return problems


def fair_rerank(scored: Iterable[Tuple[Dict, float, str]], k: int) -> List[Tuple[Dict, float, str]]:
    """Pick the top k by score with no repeated artist, then swap in spotlight songs to meet the share."""
    ranked = list(scored)
    picks, artists = [], set()
    for item in ranked:
        if item[0]["artist"] not in artists:
            picks.append(item)
            artists.add(item[0]["artist"])
        if len(picks) == k:
            break

    need = spotlight_needed(len(picks))
    have = sum(1 for s, _, _ in picks if s.get("spotlight"))
    spares = [
        item for item in ranked
        if item[0].get("spotlight") and item not in picks and item[0]["artist"] not in artists
    ]
    while have < need and spares:
        incoming = spares.pop(0)
        if incoming[0]["artist"] in artists:
            continue
        # replace the lowest-scoring non-spotlight pick
        idx = max(i for i, (s, _, _) in enumerate(picks) if not s.get("spotlight"))
        out = picks[idx]
        artists.discard(out[0]["artist"])
        artists.add(incoming[0]["artist"])
        picks[idx] = incoming
        have += 1
    return sorted(picks, key=lambda item: item[1], reverse=True)
