# Requirements and implementation plan

## Source and exact scope

The official assignment in `data/assignment.md` lists exactly 100 apps in 10 categories, with a website or documentation **hint**, not verified research. For every app it requests a one-line description, authentication, credential access gates, API breadth and MCP, an evidence-backed buildability verdict, and sources. The actual submission is one skimmable HTML page with patterns, a table, the agent workflow, proof of execution, and an honest sample audit. The expanded build request adds a focused chat product, a persistent database, caching, and dedicated analysis pages.

## Ambiguities and unavailable inputs

- No Tavily or Gemini API key, PostgreSQL URL, deployment account, or GitHub URL was supplied. The pipeline is runnable once these are configured. The case study must not claim that unexecuted research or human checks happened.
- A few hints are broad, potentially ambiguous, or not documentation URLs (for example Paygent Connect, YouTube Transcript, NotebookLM). They are seeds for discovery, never evidence by themselves.
- “Accuracy improvement” requires a recorded initial answer and a manual ground-truth audit. The interface reports it only from actual review records.
- The official task prioritizes the HTML deliverable and trustworthy findings. The app uses the same stored results; it does not fill blank cells with model guesses.

## Architecture

`assignment.md → apps.json → resolver → Tavily Search → Tavily Extract → four LangGraph research nodes → evidence quote validation → LLM verification → PostgreSQL → API → Next.js + case-study export`.

FastAPI owns all keys. The web client receives only application metadata and saved research. A recent completed run is reused until refresh. Errors remain visible as errors and do not become research findings.

## Database schema

- `applications`: official ID, name, category, hint.
- `research_runs`: app, started/completed times, state, error, model.
- `sources`: run, URL, title, extracted content, retrieval time.
- `claims`: run, dimension, claim, exact evidence quote, source, verifier status and reason, final disposition.
- `reviews`: claim, human verdict, note, reviewed time. These are kept separate from automatic verification.
- `conversations`: session ID, active app, update time.

This records raw evidence, initial claims, verification outcomes, and human corrections without conflating them.

## LangGraph workflow

`discover` resolves source URLs and extracts content. Four independent nodes (`auth`, `credentials`, `api`, `mcp`) analyze the same bounded evidence in parallel. A fan-in `verify` node checks exact quotes and uses Gemini to judge entailment. `finalize` derives buildability from verified claims and leaves unknowns explicit. Each node returns a small state update; the claim list has a reducer.

## UI pages

- `/`: context-locked research chat, active app, progress, source-backed structured result.
- `/apps`: the exact 100-row dataset, filters, and saved research status.
- `/insights`: counts calculated only from completed, verified records and an explicit denominator.
- `/verification`: automatic verifier outcomes, human review counts, and example corrections.
- `/methodology`: pipeline, role boundaries, and limitations.
- `case-study/index.html`: standalone reviewer-facing summary generated from an export.

## Build order

1. Preserve and parse the official dataset; test resolver and context switching.
2. Implement Tavily Search and Extract and Gemini structured claims.
3. Orchestrate parallel branches and evidence verification with LangGraph.
4. Persist runs in PostgreSQL and expose APIs and batch CLI.
5. Build the chat and analysis pages.
6. Run research and a human audit when keys and services exist; export the case study from recorded data.
