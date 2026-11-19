"""Rule-based fallback: keyword parsing + the original Module 3 scorer + fairness rerank.

Used when there is no API key (--offline), when the API fails, or when Claude's capsule
keeps failing the guardrails. It never calls an LLM, so it is fast and deterministic.
"""

import re
from typing import Dict, List

from src.culture import retrieve_notes
from src.guardrails import fair_rerank
from src.recommender import recommend_songs

GENRE_SYNONYMS = {
    "ghana": "highlife", "ghanaian": "highlife", "highlife": "highlife",
    "nigeria": "afrobeats", "nigerian": "afrobeats", "afrobeats": "afrobeats", "afro": "afrobeats",
    "afro-fusion": "afro-fusion", "diaspora": "afro-fusion",
    "amapiano": "amapiano", "south africa": "amapiano",
    "bossa": "bossa nova", "brazil": "bossa nova",
    "lofi": "lofi", "lo-fi": "lofi",
    "synthwave": "synthwave", "80s": "synthwave",
    "hip-hop": "hip-hop", "hip hop": "hip-hop", "rap": "hip-hop",
    "jazz": "jazz", "ambient": "ambient", "classical": "classical", "piano": "classical",
    "country": "country", "folk": "folk", "edm": "edm", "electronic": "edm",
    "metal": "metal", "rock": "rock", "reggae": "reggae", "r&b": "r&b", "rnb": "r&b",
    "indie": "indie pop", "bedroom pop": "bedroom pop", "pop": "pop",
}
MOOD_WORDS = {
    "calm": "chill", "chill": "chill", "relax": "relaxed", "relaxing": "relaxed", "coffee": "relaxed",
    "study": "focused", "studying": "focused", "focus": "focused", "work": "focused",
    "happy": "happy", "sunny": "happy", "joy": "joyful", "celebrate": "joyful", "party": "euphoric",
    "sad": "sad", "heartbreak": "sad", "cry": "sad",
    "overthinking": "melancholic", "melancholic": "melancholic", "lonely": "melancholic",
    "gym": "intense", "workout": "intense", "intense": "intense", "angry": "angry",
    "romantic": "romantic", "date": "romantic", "nostalgic": "nostalgic", "uplifting": "uplifting",
    "confident": "confident", "moody": "moody", "night drive": "moody",
}
LOW_ENERGY = ["calm", "sleep", "study", "studying", "relax", "chill", "quiet", "late", "soft", "focus"]
HIGH_ENERGY = ["gym", "workout", "party", "run", "running", "hype", "high energy", "dance", "leg day"]


def _has(text: str, word: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", text) is not None


def _first_match(text: str, table: Dict[str, str]) -> str:
    # longest keywords first so "afro-fusion" beats "afro" and "bedroom pop" beats "pop"
    for word in sorted(table, key=len, reverse=True):
        if _has(text, word):
            return table[word]
    return "any"


def parse_request_offline(text: str) -> Dict:
    """Turn free text into a taste profile with simple keyword rules."""
    t = text.lower()
    genre = _first_match(t, GENRE_SYNONYMS)
    mood = _first_match(t, MOOD_WORDS)
    energy = 0.5
    if any(_has(t, w) for w in HIGH_ENERGY):
        energy = 0.9
    elif any(_has(t, w) for w in LOW_ENERGY):
        energy = 0.3
    acoustic = any(_has(t, w) for w in ["acoustic", "unplugged", "soft", "calm", "study", "studying"])
    return {"favorite_genre": genre, "favorite_mood": mood, "target_energy": energy, "likes_acoustic": acoustic}


def has_music_signal(profile: Dict) -> bool:
    """False when the keyword parser found nothing music-related (genre, mood or energy cue)."""
    return profile["favorite_genre"] != "any" or profile["favorite_mood"] != "any" or profile["target_energy"] != 0.5


def fallback_capsule(text: str, songs: List[Dict], notes: List[Dict], k: int, profile: Dict = None) -> Dict:
    """Build a capsule without an LLM. The story quotes retrieved notes verbatim (no generation)."""
    profile = profile or parse_request_offline(text)
    picks = fair_rerank(recommend_songs(profile, songs, k=len(songs)), k)
    top_genres = [s["genre"] for s, _, _ in picks[:2]]
    found = retrieve_notes(top_genres, notes, k=2)
    story = " ".join(n["text"] for n in found) or "No cultural notes matched these picks."
    confidence = round(sum(score for _, score, _ in picks) / (5.0 * len(picks)), 2) if picks else 0.0
    return {
        "title": "Quick Picks (rule-based)",
        "profile": profile,
        "picks": [{"song_id": s["id"], "why": why} for s, _, why in picks],
        "story": story,
        "note_ids": [n["id"] for n in found],
        "confidence": confidence,
        "confidence_reason": "Average match score of the picks (rule-based, not self-rated).",
    }
