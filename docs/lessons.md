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
