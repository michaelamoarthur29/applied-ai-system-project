# Model Card: MuseIQ Lite

## 1. Model Details

- **Name:** MuseIQ Lite (version 2; version 1 was the Module 3 project "VibeMatch 1.0")
- **What it is:** an agentic retrieval-augmented recommender. Claude (`claude-opus-5-5`, effort `medium`) plans, retrieves songs and cultural notes through tools, and writes a "Culture Capsule". Code-level guardrails check every capsule, and a rule-based fallback takes over when anything fails.
- **Built by:** me, working with Claude Code as an AI pair programmer (see section 10).
- **Code:** [src/agent.py](src/agent.py) (agent loop), [src/guardrails.py](src/guardrails.py), [src/culture.py](src/culture.py), [src/fallback.py](src/fallback.py), [src/evaluate.py](src/evaluate.py)

## 2. Intended Use

- **For:** a classroom and portfolio demonstration of a culture-aware discovery feature, the core idea of my MuseIQ product concept. It answers plain-language listening requests ("studying late, something calm") with 5 songs, a reason for each, and the cultural story behind them.
- **Not for:** real listeners or a real music service. The catalog is 24 made-up songs. It must not be used to decide which real artists get promoted or paid, and its genre notes are short summaries, not authoritative music history.

## 3. How It Works

1. Input guardrails reject empty or over-long requests and flag likely prompt injection.
2. Claude calls `list_catalog_options` (plan), `search_songs` (the Module 3 scorer as a retriever) and `get_culture_notes` (keyword search over 32 notes), then `submit_capsule`.
3. `check_capsule` verifies that every song is in the catalog and was retrieved, no artist repeats, at least 2 of 5 songs are spotlight songs, cited notes were retrieved, and confidence is between 0 and 1. Violations go back to Claude (max 2 revisions).
4. Any failure (no key, API error, refusal, repeated violations) switches to the rule-based fallback.
5. Every step is logged, and each run gets a JSONL trace.

## 4. Data

- **Songs:** [data/songs.csv](data/songs.csv), 24 fictional songs (18 from Module 3 plus 6 I added: highlife, Afrobeats, amapiano, bossa nova, Afro-fusion, bedroom pop). 9 are flagged `spotlight`, meaning underrepresented or independent creators.
- **Culture notes:** [data/culture_notes.json](data/culture_notes.json), 32 notes. 21 summarize real genre history, and 11 are fictional artist bios that say "Fictional artist (simulated catalog)".
- **Who decided the labels:** I assigned the spotlight flags and wrote the notes with AI assistance, checking them against widely documented music history. They are not sourced citations.

## 5. Evaluation Results

From [reports/eval_report.md](reports/eval_report.md) and [evidence/](evidence/):

- **Automated tests:** 19/19 pass ([evidence/pytest_output.txt](evidence/pytest_output.txt)).
- **Evaluation suite with Claude:** 10/10 cases pass (5 personas, 4 guardrail cases, 1 consistency check). Self-rated confidence averaged 0.69, from 0.30 for the missing K-pop genre to 0.85 for study music. There were 0 fallbacks and 0 guardrail revisions. Median latency was 19.5 s per capsule.
- **Rule-based fallback alone:** 9/10. It fails the Cultural Explorer request.
- **Style specialization:** the MuseIQ voice (few-shot) scored 1.00 on the style rubric vs 0.65 for the baseline prompt, and its explanations averaged 14.8 words vs 21.9.

---

## 6. Limitations and Biases

- **Tiny, fictional catalog.** Most genres have one song, so "explore highlife" really means "the one highlife song". The system can describe a tradition but can't really explore it.
- **The spotlight label is my judgment.** Deciding who counts as "underrepresented" is a value call, and I made it per artist with no data behind it. Labeling artists by identity or scene can also be reductive. The 30% rule is a floor, so it can push a less relevant song into a capsule just to meet the quota.
- **Whose history?** The genre notes are 2-4 sentences each, written in English from a mostly Western, chart-oriented view of music history. Short summaries flatten complicated histories, and anything not in the notes (most of the world's music) simply doesn't exist for this system.
- **Inherited scoring bias.** `search_songs` is the Module 3 scorer, so it still uses exact-match genres ("indie pop" isn't "pop") and weights genre heavily.
- **Grounding isn't truth.** The guardrails verify *which* notes Claude used, not whether every sentence is supported. In one run Claude described Paper Lanterns' music as "made by students", a plausible but unsupported reading of the note.
- **Identity-based recommendations.** For "I'm Ghanaian-American", the system leaned on heritage genres. That's what this listener asked for, but the same pattern applied without being asked would be stereotyping.
- **Speed and cost.** A capsule takes 15-24 s and several API calls, far from MuseIQ's +300 ms performance target.
- **Confidence is self-reported.** It tracked catalog coverage in testing, but it's the model's own estimate, and it varied between runs of the same request (0.65 to 0.75).

## 7. Misuse and Prevention

| Possible misuse | What I built to prevent it | Remaining gap |
|---|---|---|
| **Prompt injection**: making the agent ignore its rules or promote an outside song | Input flagging, a system rule that the request is data and not instructions, the request wrapped in `<listener_request>` tags, and output checks that reject any song not retrieved from the catalog. Tested: the Rick Astley injection was flagged and declined. | A cleverer injection could still steer which catalog songs are chosen or what the story emphasizes. |
| **Using it as a free general chatbot** (cost abuse) | Off-topic requests are declined (tested), and requests are capped at 500 characters. | No rate limiting or authentication. A real deployment would need both. |
| **Pay-for-play or data poisoning**: editing the catalog, spotlight flags or notes to promote certain artists | The data is in version control, so every change is visible in git history, and the scorer is transparent. | The guardrails trust the data. Data changes need human review. |
| **Profiling listeners** from what they reveal (heritage, mood, mental state) | Nothing is sent anywhere except the Claude API. Logs and feedback stay local and are excluded from git. | `logs/feedback.jsonl` stores raw request text. A real product would need consent and data-retention rules. |
| **Passing off fiction as fact** | Fictional artist notes say they're fictional, and the system prompt tells Claude not to add facts beyond the notes. | Readers may still take a confident cultural story as authoritative. |

## 8. What Surprised Me While Testing

- **The most important bug was found by reading a trace, not by a test.** Every test passed and the Explorer capsule looked good. Only the trace showed that Claude had asked for the highlife note and never received it: the retriever read only 8 of 12 topics and kept the top 5, so tag-heavy artist notes crowded out the genre note. Claude handled it well. It said "there's no note on highlife history" and lowered its confidence instead of making it up. After the fix the story used the real highlife history. (Before/after in the [README](README.md#retrieval-improvement-found-by-testing-beforeafter).)
- **The guardrail revision loop never triggered live.** Across all 29 live runs, Claude met every rule on the first submission, so the revise-and-resubmit path is proven only by the fake-client tests. I didn't expect the prompt alone to be that reliable.
- **Confidence was meaningful but not stable.** It dropped to 0.30 for K-pop, exactly when it should. But the same Explorer request scored 0.65, 0.70 and 0.75 on different runs.
- **The few-shot voice guide changed output more than I expected.** Without it, stories ran 160-196 words. With it, every capsule met all five style checks.
- **My biggest problem wasn't the AI.** A nearly full disk made iCloud evict project files mid-run, so Python took minutes to start and git failed to read a file. Reliability includes the environment the code runs in.

## 9. Future Work

- Embedding-based note retrieval, plus a check that each sentence in the story can be traced to a note.
- Partial genre credit (pop and indie pop, rock and metal) in the scorer.
- Use listener feedback (`logs/feedback.jsonl`) in the evaluation report and in ranking.
- Rate limiting and a cheaper, faster model or effort setting for a latency budget closer to MuseIQ's target.

## 10. AI Collaboration

I built this project with **Claude Code** as a pair programmer. It set up the repository, proposed the architecture, wrote most of the code and tests, ran the evaluations, and drafted documentation from the real outputs. My role was product direction, deciding what to build and in what order, and reviewing results.

- **A suggestion I accepted:** the "MuseIQ Lite" architecture and diagram. Claude proposed the pipeline (input guardrails, Claude plans, songs and culture notes retrieved, capsule generated, output guardrails, fallback, logging, tests and human review). I previewed it in Mermaid Live and committed it unchanged, because it covered every requirement and mapped my MuseIQ ideas (Culture Capsules, personas, fairness and repetition guardrails) onto a buildable system. The final build later refined it (tool calls, up to 2 revisions), and the diagram was updated to match.
- **A suggestion I didn't accept as written:** when I pasted the first set of project requirements, Claude immediately started building a generic retrieval-augmented recommender and began installing packages. I stopped it. I wanted to collect all the requirements first and shape the product around my own MuseIQ case study instead of a generic design. The suggestion wasn't wrong, but it was premature and a poor fit for what I wanted the project to say about me. Waiting meant the personas, Culture Capsules, the spotlight fairness rule and the repetition guardrail all came from my product design.

## 11. Version History

- **v1 (Module 3): VibeMatch 1.0.** A rule-based recommender over 18 songs, with weighted genre, mood, energy and acoustic scoring. Its full model card, including the profile experiments and weight-shift analysis, is preserved in git history: [model_card.md at commit 178eec3](https://github.com/michaelamoarthur29/applied-ai-system-project/blob/178eec3/model_card.md).
- **v2 (this project): MuseIQ Lite.** Agentic RAG, guardrails, fallback, evaluation and specialization.
