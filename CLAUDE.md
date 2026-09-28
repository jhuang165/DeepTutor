# CLAUDE.md

This is the canonical agent guide for Jeffrey's fork of DeepTutor
(`origin` = `jhuang165/DeepTutor`, `upstream` = `HKUDS/DeepTutor`). The upstream
architecture overview lives in [AGENTS.md](AGENTS.md) and is imported below so it keeps
tracking upstream without being copied here. Anything in this file that is fork-specific
takes precedence.

@AGENTS.md

## Shared memory bridge

This repository uses the Mac-local shared memory bridge (project id `deeptutor`); Claude
native auto-memory and Codex native memories are disabled. At every session start, run the
exact `memoryctl load` command emitted by the SessionStart hook with its current writer and
session ID, read the returned `MEMORY.md` index in full, then read only relevant bodies with
`memoryctl read --repo "$PWD" --topic TOPIC-SLUG`. Treat that revision-bound load as required
context; reload before continuing if a hook reports newer memory.

After every meaningful completed unit and before stopping or compacting, start one focused
lowercase kebab-case topic with `memoryctl begin --repo "$PWD" --topic TOPIC-SLUG --operation
upsert --writer WRITER --session SESSION-ID`. Edit only the staged `topic.md` and `pointer.txt`,
never the canonical memory directory. Record the change and reason, absolute date, state,
branch, key files or functions, verification, pending work, and non-obvious decisions; keep the
pointer to exactly one Markdown link and exclude secrets and transient narration. Commit with
`memoryctl commit TRANSACTION-ID`. On a stale revision, reload, reread, and reconcile through a
new transaction, never force-copy staged files. Use `memoryctl no-op` only for a clean session
with a truthful reason. The bridge has no report policy for this project.

The operator guide is `~/claude-tools/docs/shared-memory-bridge.md`.

## Running the app locally

- The CLI is installed in the project venv, not on `PATH`: use `.venv/bin/deeptutor`
  (Python 3.13).
- `.venv/bin/deeptutor start --detach` launches backend (`http://127.0.0.1:8001`) and
  frontend (`http://localhost:3782`) in the background; the log is
  `data/user/runtime/launcher.log`. Stop with
  `.venv/bin/deeptutor stop --home "$PWD"`. Add `--dev` for the Next.js dev server when
  working on `web/`.
- Runtime settings (LLM/embedding providers, tools) live in `data/user/settings/*.json`;
  project-root `.env` files are ignored. Never print or commit keys from those files.
- `data/` is user state (knowledge bases, books, sessions). Do not commit it or delete it
  to "reset" something without asking.

## Verification

Run the checks that cover what you touched before claiming done, mirroring
`.github/workflows/tests.yml`:

- Python: `.venv/bin/pytest -q tests deeptutor/learning/tests` (or the relevant subset).
  CI also runs `ruff check .` and `ruff format --check .`; ruff is not installed in the
  venv, so use `uvx ruff` or install the `.[dev]` extra.
- Web (from `web/`): `npm run check:fast` for contracts, architecture, typecheck, node and
  vitest unit tests, lint, and i18n; `npm run check` adds the production build and perf
  budget. Playwright suites (`npm run audit`, `npm run test:e2e:critical`) need a running
  frontend.
- If backend WebSocket or API payloads change, regenerate and check the web contracts
  (`npm run contracts:generate` / `contracts:check`).
- For UI or turn-flow changes, also start the app and exercise the change in the browser.

Begin bug fixes with a failing regression test when practical.

## Git workflow

- Work on a topic branch off `main` (`fix/...`, `feat/...`, `docs/...`) and merge back into
  `main` on `origin`. Commit or push only when asked.
- Keep diffs to upstream-owned files small and targeted so `upstream/main` merges stay
  cheap; put fork-only guidance here rather than in `AGENTS.md`, `README.md`, or
  `.gitignore`. Local-only ignores go in `.git/info/exclude`.
- Do not open pull requests against `HKUDS/DeepTutor` unless asked.
