# REVIEW.md — Morning verification

Pre-shaped checklist for reviewing the overnight build. Work top to bottom.
The loop should fill in the two bracketed sections at the end before stopping.

-----

## 0. First glance (30 seconds)

- [ ] Is there a `BLOCKERS.md`? If yes, read it first — it tells you which chunk
  stopped and why. Everything below still applies to the chunks that did land.
- [ ] `git log --oneline` on `feat/auto-build` — expect one clean commit per
  completed chunk (Chunk 0 → 8), newest last.
- [ ] Working tree clean? `git status` should show nothing uncommitted.

## 1. Gate still green (2 minutes)

Re-run the full gate yourself, in the container, to confirm nothing was left red:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy --strict src
uv run pytest -q --cov=src/tattoo_feed --cov-report=term-missing --cov-fail-under=90
```

- [ ] All four exit 0. Coverage ≥ 90%.

## 2. Go live (the first real Instagram call)

This is the first time real credentials and the real API are used — the loop
never did this.

- [ ] Copy `.env.example` to `.env` and fill in your real `IG_ACCESS_TOKEN` and
  `IG_USER_ID`. (`.env` is gitignored — confirm it is **not** staged.)
- [ ] Optionally run the live integration test: `RUN_LIVE=1 uv run pytest -k live`.
- [ ] Start the server and connect it from your MCP client (e.g. Claude Desktop).
  It should boot and list every tool from PLAN.md §6.

## 3. Functional smoke test (chat with it)

- [ ] `add_artist` with a real tattoo-artist handle you follow → succeeds.
- [ ] `add_artist` with a personal (non-professional) account → fails *cleanly*
  with a readable error, not a stack trace.
- [ ] `get_feed` → returns recent posts, newest-first, no videos present.
- [ ] `next_inspiration` → returns one post you haven’t seen; calling it again
  gives a *different* post.
- [ ] `save_to_inspiration` then `list_inspiration` → the saved item is there,
  in save order, with the artist handle attached.
- [ ] `record_preference` → the assistant **proposes and asks you to confirm**
  before writing (propose-then-confirm). Then `get_preference_summary`
  returns it.

## 4. THE EYEBALL CHECK (image rendering — tests cannot verify this)

This is the one thing automated gates could not confirm. Look with your eyes:

- [ ] `next_inspiration` actually **displays the image inline** in your client.
- [ ] The image is the **right image** for that post (cross-check the permalink).
- [ ] Orientation is correct (not rotated/flipped — EXIF was stripped).
- [ ] It’s a reasonable preview size (≤ 640px long edge), not full-res or tiny.
- [ ] A carousel post shows its **first** image (not a broken/blank frame).

If any of these is off, it’s almost certainly isolated to `imaging.py` or the
Chunk 8 wiring in `server/app.py` — the rest of the system is gate-verified.

## 5. Code review pass (it’s a portfolio piece)

Skim with a reviewer’s eye — this repo may go in front of an interviewer:

- [ ] `core` has zero MCP imports; `server/app.py` has zero business logic.
- [ ] Files are small and single-purpose; names are clear.
- [ ] Docstrings + type hints throughout; errors are typed, never bare.
- [ ] README explains setup, design decisions, and limitations honestly.
- [ ] No secrets, no `.env`, no `data/` committed.

## 6. Phase-2 readiness (sanity, not a task)

- [ ] Could a GUI import `core` and call the services directly, with no MCP in
  the way? If yes, the seam held and the Inspiration/Feed/Artists tabs will
  bolt straight on.

-----

## Loop fills these in before stopping

**Chunks completed:** [ e.g. 0–8 all green / 0–6 green, 7 blocked ]

**Anything I flagged for you:** [ free text from the loop — surprises,
assumptions made, anything in BLOCKERS.md, anything worth a closer look ]