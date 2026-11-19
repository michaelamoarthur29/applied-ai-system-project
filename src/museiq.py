"""MuseIQ Lite command-line app.

    python -m src.museiq "studying late tonight, something calm"
    python -m src.museiq "afro vibes for a cookout" --k 4 --style baseline
    python -m src.museiq "rainy day jazz" --offline          # no API key needed
"""

import argparse
import json
import sys
import textwrap
from datetime import datetime, timezone

from src.agent import CapsuleResult, MuseIQ
from src.config import DEFAULT_K, LOG_DIR

try:  # optional: load ANTHROPIC_API_KEY from a local .env file
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def render(result: CapsuleResult) -> str:
    wrap = lambda s, indent="   ": textwrap.fill(s, width=88, initial_indent=indent, subsequent_indent=indent)
    lines = []
    if result.status == "input_error":
        return f"[input error] {result.message}"
    if result.status == "declined":
        return f"[declined] {result.message}"
    source = "Claude agent" if result.source == "claude" else f"rule-based fallback ({result.message})"
    lines.append(f"=== {result.title} ===")
    lines.append(f"source: {source} | style: {result.style} | revisions: {result.revisions} | "
                 f"tool calls: {result.tool_calls} | {result.latency_s:.1f}s")
    if result.flags:
        lines.append(f"input flags: {', '.join(result.flags)}")
    lines.append("")
    for i, pick in enumerate(result.picks, 1):
        s = pick["song"]
        star = "  *spotlight" if s["spotlight"] else ""
        lines.append(f"{i}. {s['title']} - {s['artist']}  [{s['genre']} / {s['mood']}, energy {s['energy']:.2f}]{star}")
        lines.append(wrap(pick["why"]))
    lines.append("")
    lines.append("Culture story:")
    lines.append(wrap(result.story))
    lines.append(f"   sources: {', '.join(result.note_ids)}")
    lines.append("")
    conf = f"{result.confidence:.2f}" if result.confidence is not None else "n/a"
    lines.append(f"Confidence: {conf} - {result.confidence_reason}")
    if result.trace_path:
        lines.append(f"Trace: {result.trace_path}")
    return "\n".join(lines)


def ask_feedback(result: CapsuleResult) -> None:
    """Human-in-the-loop: record a thumbs up/down for the capsule in logs/feedback.jsonl."""
    try:
        answer = input("\nWas this capsule useful? [y/n, Enter to skip] ").strip().lower()
    except EOFError:
        return
    if answer not in ("y", "n"):
        return
    note = input("Optional comment: ").strip()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_DIR / "feedback.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "request": result.request, "source": result.source, "style": result.style,
            "picks": [p["song"]["id"] for p in result.picks], "confidence": result.confidence,
            "useful": answer == "y", "comment": note,
        }) + "\n")
    print("Thanks, feedback saved to logs/feedback.jsonl")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="MuseIQ Lite: culture-aware music recommendations")
    parser.add_argument("request", help="what you want to listen to, in your own words")
    parser.add_argument("--k", type=int, default=DEFAULT_K, help="number of songs (3-8, default 5)")
    parser.add_argument("--style", choices=["museiq", "baseline"], default="museiq")
    parser.add_argument("--offline", action="store_true", help="skip Claude and use the rule-based recommender")
    parser.add_argument("--no-feedback", action="store_true", help="don't ask for a thumbs up/down")
    args = parser.parse_args(argv)
    if not 3 <= args.k <= 8:
        parser.error("--k must be between 3 and 8")

    result = MuseIQ(style=args.style, k=args.k, offline=args.offline).run(args.request)
    print(render(result))
    if result.status == "capsule" and not args.no_feedback and sys.stdin.isatty():
        ask_feedback(result)
    return 1 if result.status == "input_error" else 0


if __name__ == "__main__":
    sys.exit(main())
