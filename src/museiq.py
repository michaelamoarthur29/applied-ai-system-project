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


def live_printer(entry: dict) -> None:
    """Print each agent step as it happens (stderr, so saved stdout transcripts stay clean)."""
    ev, t = entry["event"], entry["t"]
    say = lambda msg: print(f"  [{t:5.1f}s] {msg}", file=sys.stderr, flush=True)
    if ev == "request":
        say(f"request received: {entry['text']!r}")
    elif ev == "input_flagged":
        say(f"input guardrail flagged: {', '.join(entry['flags'])}")
    elif ev == "input_rejected":
        say(f"input guardrail rejected the request: {entry['reason']}")
    elif ev == "model_turn":
        say(f"Claude responded (turn {entry['turn'] + 1}), thinking about the next step...")
    elif ev == "reasoning":
        summary = " ".join(entry["summary"].split())
        say("Claude's plan: " + (summary[:220] + "..." if len(summary) > 220 else summary))
    elif ev == "tool_call":
        args = entry["input"]
        if entry["name"] == "search_songs":
            extra = ", spotlight only" if args.get("spotlight_only") else ""
            say(f"-> search_songs: genre={args.get('genre')}, mood={args.get('mood')}, energy={args.get('target_energy')}{extra}")
        elif entry["name"] == "get_culture_notes":
            say(f"-> get_culture_notes: {', '.join(map(str, args.get('topics', [])))}")
        elif entry["name"] == "submit_capsule":
            say(f"-> submit_capsule: '{args.get('title')}' with {len(args.get('picks', []))} songs")
        else:
            say(f"-> {entry['name']}")
    elif ev == "tool_result":
        s = entry["summary"]
        if entry["name"] == "search_songs":
            say(f"   found {len(s)} songs, top: {s[0] if s else 'none'}")
        elif entry["name"] == "get_culture_notes":
            say(f"   retrieved notes: {', '.join(s)}")
        else:
            say("   catalog options loaded")
    elif ev == "guardrail_check":
        if entry["passed"]:
            say("output guardrails: PASSED (catalog, retrieved, no repeats, spotlight share, notes, confidence)")
        else:
            say("output guardrails: FAILED, sending back to Claude: " + "; ".join(entry["violations"]))
    elif ev == "declined":
        say("request declined")
    elif ev == "fallback":
        say(f"switching to rule-based fallback: {entry['reason']}")
    elif ev == "final":
        say(f"done in {entry['latency_s']:.1f}s\n")


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
    parser.add_argument("--quiet", action="store_true", help="hide the live step-by-step view")
    args = parser.parse_args(argv)
    if not 3 <= args.k <= 8:
        parser.error("--k must be between 3 and 8")

    live = sys.stderr.isatty() and not args.quiet
    if live:
        print("MuseIQ live view (each step as it happens):", file=sys.stderr, flush=True)
    result = MuseIQ(style=args.style, k=args.k, offline=args.offline,
                    on_event=live_printer if live else None).run(args.request)
    print(render(result))
    if result.status == "capsule" and not args.no_feedback and sys.stdin.isatty():
        ask_feedback(result)
    return 1 if result.status == "input_error" else 0


if __name__ == "__main__":
    sys.exit(main())
