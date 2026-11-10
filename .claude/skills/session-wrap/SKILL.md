---
name: session-wrap
description: Wrap up a build session. Runs lint and tests, updates docs/BUILD_PLAN.md, commits, and reports what is next.
disable-model-invocation: true
---
# Session wrap

1. Run `uv run ruff check .` and `uv run pytest`. Fix failures, or report why one cannot be fixed.
2. Tick the finished session in docs/BUILD_PLAN.md and add a one-line note (what works, what was measured).
3. Run `git status`. Confirm that .env, data/ and media files are not staged.
4. `git add -A` then commit with the message "Session N: <what was built>". Do not push.
5. Report in at most 6 lines: built, how to run it, test result, measurements, blockers,
   and what I must prepare for the next session.
