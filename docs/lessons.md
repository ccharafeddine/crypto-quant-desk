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

## 2026-07-09 — Expansion E1 (QtAds docking)

- Shiboken deleted the CDockManager out from under live Python objects
  ("Internal C++ object already deleted") because the wrapper held the manager
  but not its parent QMainWindow; the host was GC'd, taking the C++ child with
  it. Rule: a Python object that owns a Qt child must keep a strong reference to
  the parent that owns it in C++.
- Verify a third-party Qt binding's API by introspection before coding to it,
  not from C++ docs: the bound `CDockWidget(title, parent)` ctor is deprecated
  (use `CDockWidget(manager, title)`), and `moveDockWidget` does not exist -
  rearranging is removeDockWidget + re-add (widget contents survive). Rule:
  `dir()`/smoke-test the actual installed bindings first.
- New third-party dep on a bleeding-edge interpreter: confirm real
  installability, not just PyPI presence. PySide6-QtAds ships abi3 wheels
  (cp312-abi3 installs on 3.14) but hard-pins PySide6-Essentials to one 6.x, so
  PySide6 and QtAds must move in lockstep. Rule: actually install + import a new
  dep in the target venv before committing it to TECH_STACK.
- A green sub-run can hide a full-suite segfault: test_workspace.py passed
  alone (17/17) but the FULL suite exited 139 (access violation) in pytest-qt's
  _process_events. Cause: QtAds FocusHighlighting installs an app-global
  CDockFocusController event filter per CDockManager; the tests build many
  managers, and stale filters over torn-down ones crash qApp.processEvents()
  once enough accumulate. Fix: leave FocusHighlighting off (style the active
  card via QtAds' own active-tab CSS in E2). Rule: for a native-GUI change,
  the gate is the FULL suite exit code, not a passing subset - segfaults are
  state/count dependent and only show at scale.
- The same suite passed under offscreen but the pre-commit hook (which runs
  pytest with no QT_QPA_PLATFORM) segfaulted on the native Windows platform,
  blocking the commit while CI (offscreen) was green. The hook and CI must run
  Qt tests the same way. Fix: tests/conftest.py does
  os.environ.setdefault("QT_QPA_PLATFORM", "offscreen") so every entry point
  (hook, CI, ad hoc) is headless-deterministic. Rule: pin the Qt platform for
  tests in conftest, don't rely on each caller's environment.

## 2026-07-09 — Expansion E3.1 (QtAds manager accumulation hang)

- Real root cause of the full-suite hang: pytest-qt calls app.processEvents()
  after every test, and it HANGS once several live CDockManagers have piled up
  (qtbot only schedules deferred deleteLater, so managers from prior tests stay
  alive). Importing pyqtgraph into the process (via the chart tests) lowers the
  threshold - it hung on the 4th manager with pyqtgraph present, vs all 18
  passing without it. Pinned it with `-v -o faulthandler_timeout`: the exact
  test where processEvents stalled, after N managers. Fix: a `make_workspace`
  fixture that shiboken6.delete()s each manager+window on teardown, so at most
  one CDockManager is alive when processEvents runs. 270 passed, 3x stable.
  Rule: GUI objects that a native toolkit keeps globally (QtAds managers) must
  be deleted synchronously per test, not left to qtbot's deferred cleanup.
- A parallel red herring: repeated `timeout`-killed Qt hangs left half-dead
  processes that made even previously-green tests flaky for a while; it cleared
  on its own (no reboot needed - uptime confirmed none happened). Rules: (1)
  don't kill Qt tests with `timeout` (SIGTERM) in a loop on Windows - each
  half-dead GUI process degrades the session; use one run with
  faulthandler_timeout to capture the stack, then stop; (2) transient flakiness
  can masquerade as environmental - fix the deterministic root cause (here,
  accumulation) rather than trusting "a reboot fixes it".
- Kraken "EAPI:Invalid nonce" under the expanded UI: a monotonic nonce counter
  is necessary but NOT sufficient. Kraken rejects any nonce <= the highest it has
  already RECEIVED, and the expansion made many panels (Positions, Risk,
  Performance, Analytics, Analyst) each build their own client and fire private
  calls concurrently at startup. The sends weren't serialized, so requests
  reached Kraken out of nonce order and the losers came back "Invalid nonce".
  Fix: a process-wide asyncio lock in rest._private held across nonce generation
  AND the POST, so Kraken sees strictly increasing nonces. The lock is keyed to
  the running loop (recreated when the loop changes) so each test's asyncio.run()
  gets a fresh one while the single qasync app loop keeps it shared. Rule: any
  API that requires an increasing nonce needs the generate-and-send serialized,
  not just the generator; monotonic generation alone loses to out-of-order
  delivery. Red herring first suspected: two app instances - but `python -m cqd`
  spawns a tiny bootstrap parent + the real GUI child (two python.exe, ONE
  instance), so a 2-process listing is normal, not a duplicate.
- Codebase convention (re-learned): panels are tested via extracted PURE logic
  (format_cost_basis, build_risk_view, chart's nearest_candle/format_readout),
  never by constructing the widget under pytest-qt. Constructing pyqtgraph/QtAds
  panels in tests is what invites the processEvents hang. Rule: put chart/panel
  logic in module-level pure functions and test those; verify the rendered
  widget visually (headless QWidget.grab screenshot), not with a qtbot smoke.
