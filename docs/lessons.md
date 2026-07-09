# lessons.md — corrections and patterns

One entry per correction or debugging session. Format: date, what went wrong, the rule that prevents it.

## 2026-07-09 — seed-era lessons (from the overhaul audits)

- A documented caveat is not a fix. The old CLAUDE.md forbade summing BTC-quoted costs as USD,
  but `cost_basis.py` did exactly that; nothing enforced the doc. Rule: every documented
  invariant gets a test that fails when violated.
- Suffix-matching pair codes breaks on real assets (`XTZUSD` -> `XT/USD`). Rule: symbol
  parsing must be tested against the full live pair list (`AssetPairs`), not hand-picked cases.
- Batch calls need per-item degradation: one bad pair blanked the whole Risk panel while the
  Positions panel had already solved the same failure. Rule: when one call site handles a
  failure mode, grep for siblings that need the same handling.
- Never put private planning docs in a public repo; it happened in the very first commit.
  Rule: security-scan tree AND history before any public push.
- A test can encode a bug: test_buy_then_partial_sell asserted the wrong net-cash
  average (0.00075) and guarded the defect. Rule: when a finding contradicts a test,
  re-derive the expected value from first principles before trusting either.
- PowerShell 5.1 mangles double quotes inside `git commit -m` here-strings. Rule: write
  commit messages to a temp file and use `git commit -F <file>`.

## 2026-07-09 — Phase 2 debugging

- Silent app death with no traceback: modal dialogs exec'd BEFORE
  loop.run_forever() stepped an ensure_future task outside a running qasync
  loop, and PySide6 treats exceptions reaching a Qt event handler as fatal.
  Rules: (1) never exec() a dialog that schedules async work before the loop
  runs - defer with QTimer.singleShot(0, ...); (2) a GUI app gets file-based
  crash logging (excepthook + loop exception handler) from day one, because
  pythonw has no stderr.
- One state file, many writers: each panel's client got its own NonceCounter
  over the same file, risking duplicate nonces under concurrency. Rule: state
  with a uniqueness invariant gets exactly one process-wide owner.

## 2026-07-09 — Phase 3 live-fire gate

- The audit log caught a bug nobody reported: the owner's paper dry-run was
  silently rejected (unseeded overlay) and only the JSONL trail showed it.
  Rule: after any end-to-end test, read the audit/app logs even when the user
  says it worked - the failures they route around are still failures.
- A widget can pass every test and still be unusable: the cancel button was
  clipped illegible by default padding inside a table row. Rule: components
  embedded in nonstandard containers (table cells, headers) need their own
  compact style variant, checked visually at real row heights.
