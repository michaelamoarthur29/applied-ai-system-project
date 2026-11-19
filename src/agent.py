"""MuseIQ agent: Claude plans, retrieves with tools, writes a Culture Capsule, and fixes guardrail violations.

Flow for one request:
  validate input -> Claude loop [list_catalog_options -> search_songs -> get_culture_notes -> submit_capsule]
  -> guardrails check every submission -> violations go back to Claude (max MAX_REVISIONS rewrites)
  -> on API errors, refusals, missing key or repeated violations: rule-based fallback.
Every step is written to logs/museiq.log and a per-run trace in logs/traces/.
"""

import json
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import anthropic

from src.config import DEFAULT_K, EFFORT, MAX_REVISIONS, MAX_TURNS, MODEL, NOTES_PATH, SONGS_PATH
from src.culture import load_notes, retrieve_notes
from src.fallback import fallback_capsule, has_music_signal, parse_request_offline
from src.guardrails import InputError, check_capsule, validate_request
from src.prompts import TOOLS, system_prompt
from src.recommender import load_songs, recommend_songs
from src.tracing import Trace, get_logger

log = get_logger()


@dataclass
class CapsuleResult:
    status: str                      # "capsule" | "declined" | "input_error"
    source: str                      # "claude" | "fallback" | "none"
    request: str
    style: str = "museiq"
    title: str = ""
    picks: List[Dict] = field(default_factory=list)   # [{"song": {...}, "why": "..."}]
    story: str = ""
    note_ids: List[str] = field(default_factory=list)
    confidence: Optional[float] = None
    confidence_reason: str = ""
    message: str = ""                # decline reason, input error, or fallback reason
    flags: List[str] = field(default_factory=list)
    revisions: int = 0
    tool_calls: int = 0
    violations: List[List[str]] = field(default_factory=list)
    latency_s: float = 0.0
    trace_path: str = ""


class _UseFallback(Exception):
    """Internal signal: abandon the Claude path and use the rule-based capsule."""


def has_credentials() -> bool:
    return bool(
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        or (Path.home() / ".config" / "anthropic").exists()
    )


class MuseIQ:
    def __init__(self, client=None, style: str = "museiq", k: int = DEFAULT_K, offline: bool = False,
                 songs: List[Dict] = None, notes: List[Dict] = None, trace: bool = True,
                 model: str = MODEL, effort: str = EFFORT):
        self.songs = songs if songs is not None else load_songs(str(SONGS_PATH), verbose=False)
        self.notes = notes if notes is not None else load_notes(NOTES_PATH)
        self.songs_by_id = {s["id"]: s for s in self.songs}
        self.notes_by_id = {n["id"]: n for n in self.notes}
        self.style, self.k, self.model, self.effort = style, k, model, effort
        self.trace_enabled = trace
        self.offline = offline
        self.client = client
        if self.client is None and not offline and has_credentials():
            self.client = anthropic.Anthropic()

    # ------------------------------------------------------------------ public
    def run(self, raw_request: str) -> CapsuleResult:
        shown = (raw_request or "").strip()[:80]
        trace = Trace(shown or "empty", enabled=self.trace_enabled)
        result = CapsuleResult(status="capsule", source="claude", request=raw_request or "", style=self.style)
        try:
            clean = validate_request(raw_request)
        except InputError as e:
            log.warning("input rejected: %s", e)
            trace.log("input_rejected", reason=str(e))
            result.status, result.source, result.message = "input_error", "none", str(e)
            return self._finish(result, trace)

        result.request, result.flags = clean.text, clean.flags
        if clean.flags:
            log.warning("request flagged %s: %r", clean.flags, clean.text)
            trace.log("input_flagged", flags=clean.flags)

        if self.offline or self.client is None:
            reason = "offline mode" if self.offline else "no Anthropic API key found"
            return self._fallback(result, trace, reason)

        try:
            self._agent_loop(clean.text, result, trace)
        except _UseFallback as e:
            return self._fallback(result, trace, str(e))
        except anthropic.AuthenticationError:
            return self._fallback(result, trace, "API key rejected (authentication error)")
        except anthropic.RateLimitError:
            return self._fallback(result, trace, "rate limited by the API")
        except anthropic.APIStatusError as e:
            return self._fallback(result, trace, f"API error {e.status_code}")
        except anthropic.APIConnectionError:
            return self._fallback(result, trace, "could not reach the API (network)")
        except Exception as e:  # last-resort safety net: never crash the listener's session
            log.exception("unexpected agent error")
            return self._fallback(result, trace, f"unexpected error: {type(e).__name__}")
        return self._finish(result, trace)

    # ------------------------------------------------------------- agent loop
    def _agent_loop(self, text: str, result: CapsuleResult, trace: Trace) -> None:
        retrieved_songs, retrieved_notes = set(), set()
        messages = [{
            "role": "user",
            "content": f"<listener_request>\n{text}\n</listener_request>\n\nBuild a Culture Capsule of exactly {self.k} songs.",
        }]
        nudged = False
        for turn in range(MAX_TURNS):
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system_prompt(self.style, self.k),
                tools=TOOLS,
                messages=messages,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            usage = getattr(resp, "usage", None)
            trace.log("model_turn", turn=turn, stop_reason=resp.stop_reason, model=getattr(resp, "model", None),
                      input_tokens=getattr(usage, "input_tokens", None), output_tokens=getattr(usage, "output_tokens", None))
            for block in resp.content:
                if block.type == "thinking" and getattr(block, "thinking", ""):
                    trace.log("reasoning", summary=block.thinking)
                elif block.type == "text" and block.text.strip():
                    trace.log("assistant_text", text=block.text)

            if resp.stop_reason == "refusal":
                raise _UseFallback("model declined to answer (refusal)")
            if resp.stop_reason == "max_tokens":
                raise _UseFallback("model response was cut off (max_tokens)")

            messages.append({"role": "assistant", "content": resp.content})
            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if not tool_uses:
                if nudged:
                    raise _UseFallback("model stopped without submitting a capsule")
                nudged = True
                messages.append({"role": "user", "content": "Please finish by calling submit_capsule or decline_request."})
                continue

            tool_results = []
            for tu in tool_uses:
                result.tool_calls += 1
                args = tu.input if isinstance(tu.input, dict) else {}
                trace.log("tool_call", name=tu.name, input=args)

                if tu.name == "decline_request":
                    result.status, result.message = "declined", str(args.get("reason", "Request declined."))
                    log.info("declined: %s", result.message)
                    trace.log("declined", reason=result.message)
                    return

                if tu.name == "submit_capsule":
                    problems = check_capsule(args, self.songs_by_id, retrieved_songs, retrieved_notes, self.k)
                    trace.log("guardrail_check", passed=not problems, violations=problems)
                    if not problems:
                        self._accept(args, result)
                        log.info("capsule accepted after %d revision(s)", result.revisions)
                        return
                    result.violations.append(problems)
                    if result.revisions >= MAX_REVISIONS:
                        raise _UseFallback(f"capsule still broke the rules after {MAX_REVISIONS} revisions")
                    result.revisions += 1
                    log.info("guardrail violations (revision %d): %s", result.revisions, problems)
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tu.id, "is_error": True,
                        "content": "Rule violations, fix and resubmit:\n- " + "\n- ".join(problems),
                    })
                    continue

                try:
                    output = self._run_tool(tu.name, args, retrieved_songs, retrieved_notes)
                    tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": json.dumps(output)})
                    trace.log("tool_result", name=tu.name, summary=self._summarize(tu.name, output))
                except ValueError as e:
                    tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "is_error": True, "content": str(e)})
                    trace.log("tool_error", name=tu.name, error=str(e))
            messages.append({"role": "user", "content": tool_results})
        raise _UseFallback(f"agent did not finish within {MAX_TURNS} turns")

    # ------------------------------------------------------------------ tools
    def _run_tool(self, name: str, args: Dict, retrieved_songs: set, retrieved_notes: set):
        if name == "list_catalog_options":
            return {
                "genres": dict(Counter(s["genre"] for s in self.songs)),
                "moods": dict(Counter(s["mood"] for s in self.songs)),
                "spotlight_songs": sum(1 for s in self.songs if s["spotlight"]),
                "total_songs": len(self.songs),
            }
        if name == "search_songs":
            try:
                energy = min(max(float(args["target_energy"]), 0.0), 1.0)
                k = min(max(int(args["k"]), 1), 12)
                profile = {
                    "favorite_genre": str(args["genre"]).lower().strip(),
                    "favorite_mood": str(args["mood"]).lower().strip(),
                    "target_energy": energy,
                    "likes_acoustic": bool(args["likes_acoustic"]),
                }
            except (KeyError, TypeError, ValueError) as e:
                raise ValueError(f"Invalid search_songs input: {e}")
            pool = [s for s in self.songs if s["spotlight"]] if args.get("spotlight_only") else self.songs
            hits = recommend_songs(profile, pool, k=k)
            retrieved_songs.update(s["id"] for s, _, _ in hits)
            return [{
                "id": s["id"], "title": s["title"], "artist": s["artist"], "genre": s["genre"], "mood": s["mood"],
                "energy": s["energy"], "acousticness": s["acousticness"], "spotlight": s["spotlight"],
                "match_score": round(score, 2),
            } for s, score, _ in hits]
        if name == "get_culture_notes":
            topics = args.get("topics")
            if not isinstance(topics, list) or not topics:
                raise ValueError("get_culture_notes needs a non-empty list of topics.")
            found = retrieve_notes([str(t) for t in topics[:8]], self.notes, k=5)
            retrieved_notes.update(n["id"] for n in found)
            return [{"id": n["id"], "kind": n["kind"], "about": n["about"], "text": n["text"]} for n in found]
        raise ValueError(f"Unknown tool: {name}")

    @staticmethod
    def _summarize(name: str, output) -> object:
        if name == "search_songs":
            return [f"{o['id']}: {o['title']} ({o['genre']}/{o['mood']}, score {o['match_score']}"
                    f"{', spotlight' if o['spotlight'] else ''})" for o in output]
        if name == "get_culture_notes":
            return [o["id"] for o in output]
        return output

    # ---------------------------------------------------------------- results
    def _accept(self, capsule: Dict, result: CapsuleResult) -> None:
        result.title = capsule["title"].strip()
        result.picks = [{"song": self.songs_by_id[p["song_id"]], "why": p["why"].strip()} for p in capsule["picks"]]
        result.story = capsule["story"].strip()
        result.note_ids = list(capsule["note_ids"])
        result.confidence = float(capsule["confidence"])
        result.confidence_reason = capsule.get("confidence_reason", "").strip()

    def _fallback(self, result: CapsuleResult, trace: Trace, reason: str) -> CapsuleResult:
        log.warning("using rule-based fallback: %s", reason)
        trace.log("fallback", reason=reason)
        profile = parse_request_offline(result.request)
        trace.log("fallback_profile", profile=profile)
        if not has_music_signal(profile):
            result.status, result.source = "declined", "fallback"
            result.message = ("I couldn't find any music preferences in that request (rule-based mode). "
                              "Try naming a mood, activity or genre.")
            trace.log("declined", reason=result.message)
            return self._finish(result, trace)
        cap = fallback_capsule(result.request, self.songs, self.notes, self.k, profile=profile)
        result.status, result.source, result.message = "capsule", "fallback", reason
        result.title = cap["title"]
        result.picks = [{"song": self.songs_by_id[p["song_id"]], "why": p["why"]} for p in cap["picks"]]
        result.story, result.note_ids = cap["story"], cap["note_ids"]
        result.confidence, result.confidence_reason = cap["confidence"], cap["confidence_reason"]
        return self._finish(result, trace)

    def _finish(self, result: CapsuleResult, trace: Trace) -> CapsuleResult:
        result.latency_s = round(trace.elapsed(), 2)
        result.trace_path = str(trace.path) if trace.path else ""
        trace.log("final", status=result.status, source=result.source, title=result.title,
                  picks=[p["song"]["id"] for p in result.picks], confidence=result.confidence,
                  revisions=result.revisions, message=result.message, latency_s=result.latency_s)
        log.info("run done status=%s source=%s picks=%s confidence=%s revisions=%d latency=%.2fs request=%r",
                 result.status, result.source, [p["song"]["id"] for p in result.picks], result.confidence,
                 result.revisions, result.latency_s, result.request[:80])
        return result
