# cloud_surreal Master Implementation Task Board

**Status:** Active execution board
**Source architecture:** [`00-index.md`](./00-index.md)
**Rule:** This board lists open work only. Completed work is recorded as evidence
in [`02-feature-parity.md`](./02-feature-parity.md), the slice READMEs, and git
history. A task is complete only when its code, migration, contract test, and
documentation are updated together.

## Milestone 0 — Baseline (read-only)

| ID | Task | Depends on | Status |
| --- | --- | --- | --- |
| M0-2 | Capture tRPC wire fixtures from the running TS server | - | Pending |

## Milestone 2 — Feature parity (Phase 2)

| ID | Task | Depends on | Status |
| --- | --- | --- | --- |
| S2 | Notes core (+ tags/comments/attachments) | - | Partial per the slice notes below; the regenerated route matrix reports 34/34 for the symbols the current `src/src` tree calls — `app/domain/notes.py`, `app/domain/collections.py`, `app/domain/tagging.py`, `app/routers/{note,collection}.py`, `app/services.py`; isolation tests in `tests/test_notes_router.py`. The `tags.*` surface is now complete (list + 7 mutations) on top of the S2t derivation service |
| S3 | Planning family | S2 | Partial (27/31) — `app/domain/{store,planning}.py`, `app/routers/planning.py`. The four remaining `task.*` symbols are **generator mutations** (`async function*` yielding progress: `importFromPlanInc` runs `DBJob.RestoreDB`, `importFromMemos` reads a Memos SQLite DB, `importFromMarkdown` walks `.md`/`.zip`) plus `exportMarkdown`. They need streaming support in `transport/trpc.py` *and* job runners, so they are a design item rather than a port |
| S5 | Analytics/config/branding/font/notification | S2 | Partial (19/22) — `app/domain/{preferences,public}.py`, `app/routers/{preferences,public}.py`; all `public.*` reads plus `fonts.getFontData` landed. Deferred: `branding.searchImages`/`generateLogo` (image provider) and `public.musicMetadata` (audio tags + Spotify) |
| S7 | Integrations | S2 | Pending |
| S8 | AI routers | Phase 4 | Pending |
| S9 | Automation (`docker`, `workflow`) | S2 | Pending (0/5) — not in the original parity plan; added with the frontend's workflow canvas and Docker panel |
| G | Route matrix: zero unowned rows | all | Partial — `cloud_surreal/route-matrix.json` + `docs/plans/cloud_surreal/route-matrix.md` (192 frontend symbols, 0 unmapped, 135 implemented: S1 15/15, S2 34/34, S3 27/31, S4 6/6, S5 19/22, S6 34/34, S7 0/21, S8 0/24, S9 0/5); gate in `tests/test_route_matrix.py`. The extractor now scans the app's real `src/src` tree, so symbol counts moved from the earlier 185-symbol `frontend/src` baseline |

## Milestone 3 — Desktop sync (Phase 3)

| ID | Task | Depends on | Status |
| --- | --- | --- | --- |
| Y1 | Outbox + idempotency store | S2 | Pending |
| Y2 | `/api/sync` WebSocket | Y1 | Pending |
| Y3 | Broadcaster | Y2 | Pending |
| Y4 | Client replay + backoff | Y1 | Pending |
| Y5 | Isolation + replay tests | Y4 | Pending |

## Milestone 4 — AI (Phase 4)

| ID | Task | Depends on | Status |
| --- | --- | --- | --- |
| A1 | AI tables + migrations | - | Pending |
| A2 | Provider ports + secret encryption | A1 | Pending |
| A3 | Conversation/run + `ai` router | A2 | Pending |
| A4 | Embeddings + retrieval | A3 | Pending |
| A5 | Scheduled tasks + MCP | A3 | Pending |
| A6 | OpenAI-compatible route | A2 | Pending |
| A7a | `/plan` router + `/plan/health` | A3 | Pending |
| A7b | `/plan/chat` + `/plan/runs` + `/plan/conversations` | A7a | Pending |
| A7c | `/plan/search` (permission-filtered) | A4 | Pending |
| A7d | `/plan/providers` admin (secrets never returned) | A2 | Pending |
| A7e | `/plan/stream` WebSocket | A7b, Y2 | Pending |

## Milestone 5 — Cutover

| ID | Task | Depends on | Status |
| --- | --- | --- | --- |
| C1 | Traefik switch to `cloud_surreal` | G, Y5 | Pending |
| C2 | Rollback rehearsal and timing | C1 | Pending |
| C3 | Legacy `server/`/`django_server/` retention decision recorded | C1 | Pending |

## Milestone 6 — Cutover, parity, and sync (Phase 6)

| ID | Task | Depends on | Status |
| --- | --- | --- | --- |
| X3 | Bidirectional sync: JSONL import (TS→Py) + outbox ingest (Py→TS), LWW + tombstones | S2 | Pending |
| X4 | Capture live TS-server tRPC fixtures via `planincEndpoint` (pins Phase 1 Task 6) | - | Pending |
| X5 | Replace/archive decision executed (Traefik cutover, `server/` + `django_server/` archived) | X3, G, C2 | Pending |

**Django is superseded:** no new `django_server/` features; it is an archive
candidate once the Robyn cutover passes. See
[`06-cutover-parity-and-sync.md`](./06-cutover-parity-and-sync.md).

## Definition of Done

Code, migration, contract test, and documentation change together. Cross-tenant authorization, replay-safe side effects, and client contract coverage are required. A slice is not complete because its modules import successfully.
