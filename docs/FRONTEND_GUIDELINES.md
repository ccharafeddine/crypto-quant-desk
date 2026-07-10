# FRONTEND_GUIDELINES.md — Crypto Quant Desk v2

Qt desktop app. "Frontend" = PySide6 widgets styled by a token-driven QSS template (`src/cqd/ui/theme/`). All styling flows through theme tokens; **no hardcoded colors in widget code**, no per-widget `setStyleSheet` except property-based selectors already in the template.

## Theme system

- Registry of named `Theme` objects, one shared QSS template, live switching via View > Theme, persisted in QSettings. (Already built; keep.)
- **Themes: `Slate` (default), `Amber` (the former "$DOG" orange palette, renamed), `Teal`.** No $DOG naming anywhere.
- Every color below is a token name in `Theme`; hex values listed are the **Slate** set (current `colors.py` values). Amber overrides accent hues with `#F7931A` family; Teal with its own accent family. PnL green/red are shared across themes.

## Color tokens (Slate defaults)

These are the actual fields on the `Theme` dataclass (`ui/theme/__init__.py`) — the
E2 reconciliation replaced the earlier aspirational `bg_primary`-style names with
what the code ships. Only `accent` varies across themes (Slate/Amber/Teal); every
other value is shared via `_BASE`. Hex shown is the Slate set.

| Token | Hex | Use |
|---|---|---|
| `bg` | `#0B0D10` | window / workspace canvas |
| `surface` | `#14171C` | panel/card background, active dock tab |
| `surface_raised` | `#1B1F27` | headers, tab bars, table header, hover |
| `elevated` | `#22262F` | dialogs, floating docks, button hover |
| `border` | `#232830` | default 1px borders, separators |
| `border_strong` | `#333B47` | active/focused edges, floating frame |
| `text` | `#E6E9EF` | primary text |
| `text_muted` | `#8A93A2` | labels, subtitles, footnotes, muted tabs |
| `accent` | `#5B8CFF` | focus, selection, active-tab edge, links (per-theme) |
| `positive` | `#2FBF71` | positive PnL, buy/long, success |
| `negative` | `#E5484D` | negative PnL, sell/short, errors, LIVE/offline |

Rule: buy/long is always `positive`, sell/short is always `negative`, never
theme-accent. The elevation ramp `bg < surface < surface_raised < elevated`
conveys depth without drop shadows. A dedicated `warning` token (PAPER/DELAYED
amber) is not yet a separate field — those states currently reuse `accent`; add a
token here and to `Theme` if/when they need to diverge from the accent.

## Typography

Two font roles, resolved per-platform via `QFontDatabase` fallback chains:

- **Prose/UI:** `Segoe UI` (Windows) → `Helvetica Neue` (macOS) → sans-serif
- **Numeric/data (tables, prices, metrics):** `Cascadia Mono` → `Consolas` (Windows) → `Menlo`, `SF Mono` (macOS) → monospace. All numeric columns use tabular figures via the mono font.

Size scale (pt): app title 14 semibold · panel title 11 semibold · table header 9 medium uppercase +0.5px letter-spacing · body 10 regular · numeric cell 10 regular mono · badge 8 semibold uppercase · footnote 8.5 regular `text_muted`. Line height: Qt default (1.2–1.3); never set explicit pixel line heights in QSS.

## Spacing

4px base scale: **4, 8, 12, 16, 24, 32**. Panel content margins 12px; dialog margins 16px; control gaps within a form row 8px; between form rows 12px; section gaps 16px; table cell padding 4px vertical / 8px horizontal. Status bar height 24px.

## Shape and depth

- Border radius: inputs/buttons **4px**, panels/cards **6px**, badges **9px** (pill), dialogs **8px**.
- Borders 1px `border`; focused input 1px `border_strong` + accent underline.
- No drop shadows inside panels (Qt dock aesthetics); dialogs use Qt's native window shadow. Elevation is conveyed by `bg_*` steps, not shadows.

## Layout and responsiveness

Desktop-only; "responsive" = dock behavior, not breakpoints. The workspace host is the **Qt Advanced Docking System** (`PySide6-QtAds`), not raw QDockWidgets.
- Every panel is a `CDockWidget`: drag to split, tab-stack, float as a top-level window, resize freely (width and height), hide, and reopen. This is the F10 adjustable workspace.
- Default arrangement ≈ Watchlist (left) · Chart above Order book (center) · Ticket above Depth (right) · Holdings + Analytics (bottom, tabbed). Ships as the "Trading" perspective.
- **Perspectives**: named layouts saved to QSettings — presets "Trading", "Analysis", "Monitor" plus user-saved ones. **Reset layout** restores the default. Layout persists on exit / restores on launch; a layout that fails to deserialize falls back to the default (never blocks startup). Minimum window 1200×700.
- Tables get horizontal scrollbars rather than truncating numeric columns; last column stretches.

## Card chrome & workspace styling (QtAds)

Panels read as premium cards, not flat regions:
- **Elevation** is conveyed by `bg_*`/border steps (no drop shadows inside the workspace): window `bg` < panel `surface` < raised header/controls. Card radius 6px, 1px `border`; the active/focused card gets a 1px `accent` top edge or `border_strong` outline.
- **Per-panel header** (`PanelHeader`): title (11pt semibold) on the left; panel-specific controls on the right — symbol selector, timeframe segmented control, and a settings gear where relevant. Controls are ghost-styled, 24px tall, and never shift the header height.
- **QtAds surfaces are themed through tokens** (no hardcoded hex): dock tab bar matches the existing `QTabBar` tokens (selected tab = `bg` + 2px `accent` edge); title bars use `surface`; splitter handles are 4px, `border`, hover `accent`; the floating-widget frame uses `surface` + `border`. Auto-hide/pin tabs, where used, follow the same tab tokens.
- **Token reconciliation (done in E2):** the `Theme` dataclass now carries the full elevation ramp (`bg`, `surface`, `surface_raised`, `elevated`, `border`, `border_strong`, `text`, `text_muted`, `accent`, `positive`, `negative`) and the color-token table below matches it exactly. The QtAds chrome is themed from these same tokens via `build_qtads_qss`, layered over QtAds' shipped default (which keeps its button icons); the active dock tab carries the `accent` edge and recolors live on theme switch.

## Component patterns

- **Panel:** `PanelHeader` (title, optional Badge, refresh button) + body. Every panel implements the same three states: loading (skeleton/`Loading…` subtitle), error (message + Retry button, panel stays mounted), empty (one-line hint + the action that fixes it).
- **Badges:** pill, 8pt uppercase — `DEMO` (accent), `PAPER` (warning), `LIVE` (red), `DELAYED` (warning outline).
- **Buttons:** primary (accent bg, `#FFFFFF` text), destructive (red bg) reserved for order-cancel/disconnect, ghost (transparent, border) everything else. Buy button green bg, Sell button red bg, both full-width in the ticket.
- **Inputs:** `bg_tertiary` bg, 1px border, 4px radius, 8px horizontal padding; validation errors show inline 8.5pt `red` text under the field, never a popup.
- **Confirmation dialogs:** summary as a two-column key/value grid (mono values), mode badge top-right, confirm button colored by action (buy green / sell red / destructive red), cancel is ghost and default-focused.
- **Toasts (in-app):** bottom-right of the window, `bg_elevated`, 4s auto-dismiss, max 3 stacked. OS-level notifications only from the alert engine.

## Motion

Minimal and instant-feeling: state color changes 120ms ease; PnL cell flash on tick (green/red at 25% alpha fading over 400ms); no layout animations; no animated charts beyond pyqtgraph defaults.

## Charts (pyqtgraph)

Shared: background `surface`; axis/text `text_secondary`; grid `border` at 30% alpha; crosshair with a mono tooltip; no animation beyond pyqtgraph defaults.

- **Line/equity:** price line `accent`; equity curve `accent` with `green_dim`/`red_dim` area fill by sign of cumulative PnL; drawdown area `red`/`negative` 25% alpha; cost-basis overlay dashed `warning`.
- **Candlestick (E3):** up candles `green`/`positive`, down candles `red`/`negative`; bodies filled, wicks 1px same color; a volume subplot beneath (shared x-axis) with bars tinted by candle direction at ~50% alpha. Cost-basis and break-even are dashed horizontal lines (`warning`); recent fills are small triangle markers (buy `green` ▲ / sell `red` ▼) at their price/time.
- **Depth ladder (E3):** two stacked half-tables (asks above, bids below) with per-row cumulative-depth bars drawn as a background fill — asks `red`/`negative`, bids `green`/`positive`, each at ~18% alpha scaled to cumulative size; price mono, size mono right-aligned; spread readout centered between the halves.
- **Heatmaps (E4):** correlation and returns heatmaps use a diverging scale anchored at 0 — `negative` → neutral `surface` → `positive`; cell text is mono and only shown when the cell is large enough; a compact legend states the scale. Follow the `dataviz` skill for palette/contrast when building these.

## Icons

Qt-native standard icons plus inline Unicode glyphs (●, ▲, ▼) for status/PnL direction. No icon-font or SVG icon dependency in v1; if one becomes necessary, propose it in TECH_STACK.md first (Lucide's static SVG set is the pre-approved candidate).

## Writing style in UI

Sentence case everywhere ("Cancel all orders", not "Cancel All Orders"). Numbers: thousands separators; prices to the pair's tick precision; percentages 1 decimal; USD 2 decimals. Every derived metric carries its footnote (365-day annualization, EWMA λ=0.94, simple returns; quote-currency labeling for non-USD cost bases, e.g. "cost basis (BTC)"). Errors state what happened + the next action ("Kraken rejected the key pair. Check permissions and retry."), never raw tracebacks.
