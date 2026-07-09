"""Order ticket panel: build an order, validate inline, confirm, submit.

The panel is a thin shell: `build_order_request` (pure, tested) turns raw
field text into an OrderRequest or a list of input problems; OrderService owns
validation, confirmation, routing, and audit. Submission is a two-step flow -
Review opens the confirmation dialog, and only its Confirm button mints the
actual send.
"""

from __future__ import annotations

import asyncio

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
)

from cqd.data.errors import KrakenError
from cqd.data.normalize import translate_asset
from cqd.trading.limits import ORDER_TYPES, PRICE2_TYPES, PRICED_TYPES, PairSpec
from cqd.trading.orders import OrderRequest
from cqd.ui import services
from cqd.ui.dialogs.order_confirm import OrderConfirmDialog
from cqd.ui.panels.base import Panel
from cqd.ui.widgets import Badge, PanelHeader

_ORDER_TYPE_CHOICES = [
    "market",
    "limit",
    "stop-loss",
    "take-profit",
    "stop-loss-limit",
    "take-profit-limit",
    "trailing-stop",
]
_CLOSE_CHOICES = ["none", "stop-loss", "take-profit"]


def _parse_float(text: str, label: str, problems: list[str]) -> float | None:
    text = text.strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        problems.append(f"{label} is not a number: '{text}'")
        return None


def build_order_request(
    *,
    spec: PairSpec,
    side: str,
    ordertype: str,
    volume_text: str,
    price_text: str = "",
    price2_text: str = "",
    close_type: str = "none",
    close_price_text: str = "",
    mark: float | None = None,
    source: str = "ui",
) -> tuple[OrderRequest | None, list[str]]:
    """Raw ticket inputs -> OrderRequest, or input problems (pure, testable)."""
    problems: list[str] = []
    if ordertype not in ORDER_TYPES:
        problems.append(f"Unsupported order type '{ordertype}'.")
        return None, problems

    volume = _parse_float(volume_text, "Volume", problems)
    if volume is None and not problems:
        problems.append("Volume is required.")
    price = _parse_float(price_text, "Price", problems)
    price2 = _parse_float(price2_text, "Limit-after-trigger price", problems)
    close_price = _parse_float(close_price_text, "Close price", problems)

    needs_price = ordertype in PRICED_TYPES or ordertype.startswith("trailing-stop")
    if needs_price and price is None:
        label = "Offset" if ordertype.startswith("trailing-stop") else "Price"
        problems.append(f"{label} is required for '{ordertype}'.")
    if ordertype in PRICE2_TYPES and price2 is None:
        problems.append(f"'{ordertype}' needs the limit-after-trigger price.")
    if close_type != "none" and close_price is None:
        problems.append("Conditional close needs a price.")
    if problems:
        return None, problems

    pair = f"{translate_asset(spec.base)}/{translate_asset(spec.quote)}"
    request = OrderRequest(
        pair=pair,
        kraken_pair=spec.pair,
        side=side,
        ordertype=ordertype,
        volume=float(volume),
        price=price,
        price2=price2,
        close_ordertype=None if close_type == "none" else close_type,
        close_price=close_price,
        mark=mark,
        source=source,
    )
    return request, []


class TicketPanel(Panel):
    title = "Order ticket"

    #: Emitted after any successful submit so the orders panel can refresh.
    order_submitted = Signal()
    #: Emitted on pair change so the stream subscribes the slash symbol.
    pair_selected = Signal(str)  # "BTC/USD"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._specs: dict[str, PairSpec] = {}
        self._mark: float | None = None
        self._slash: str | None = None

        header = PanelHeader("Order ticket")
        self.mode_badge = Badge("PAPER")
        header.add_left(self.mode_badge)
        self._layout.addWidget(header)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.pair_combo = QComboBox()
        self.pair_combo.setEditable(True)
        self.pair_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.pair_combo.currentTextChanged.connect(self._on_pair_changed)
        form.addRow("Pair", self.pair_combo)

        self.mark_label = QLabel("-")
        self.mark_label.setProperty("role", "subtitle")
        form.addRow("Mark", self.mark_label)

        self.side_combo = QComboBox()
        self.side_combo.addItems(["buy", "sell"])
        form.addRow("Side", self.side_combo)

        self.type_combo = QComboBox()
        self.type_combo.addItems(_ORDER_TYPE_CHOICES)
        self.type_combo.currentTextChanged.connect(self._on_type_changed)
        form.addRow("Type", self.type_combo)

        self.volume_edit = QLineEdit()
        self.volume_edit.setPlaceholderText("base quantity, e.g. 0.001")
        form.addRow("Volume", self.volume_edit)

        self.price_edit = QLineEdit()
        self.price_label = QLabel("Price")
        form.addRow(self.price_label, self.price_edit)

        self.price2_edit = QLineEdit()
        self.price2_label = QLabel("Limit after trigger")
        form.addRow(self.price2_label, self.price2_edit)

        self.close_combo = QComboBox()
        self.close_combo.addItems(_CLOSE_CHOICES)
        form.addRow("Cond. close", self.close_combo)
        self.close_price_edit = QLineEdit()
        self.close_price_edit.setPlaceholderText("close trigger price")
        form.addRow("Close price", self.close_price_edit)

        self._layout.addLayout(form)

        self.problems_label = QLabel("")
        self.problems_label.setWordWrap(True)
        self.problems_label.setProperty("role", "footnote")
        self._layout.addWidget(self.problems_label)

        self.review_btn = QPushButton("Review order...")
        self.review_btn.clicked.connect(self._on_review)
        self._layout.addWidget(self.review_btn)

        self.status = QLabel("Loading pairs...")
        self.status.setProperty("role", "subtitle")
        self._layout.addWidget(self.status)
        self._layout.addStretch(1)

        self._on_type_changed(self.type_combo.currentText())
        self.refresh_mode_badge()
        asyncio.ensure_future(self._load_pairs())

    # ---------- data ----------

    async def _load_pairs(self) -> None:
        try:
            self._specs = await services.pair_specs()
        except KrakenError as e:
            self.status.setText(f"Could not load pairs: {e}")
            return
        names = sorted(self._specs)
        self.pair_combo.blockSignals(True)
        self.pair_combo.addItems(names)
        completer = QCompleter(names, self.pair_combo)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.pair_combo.setCompleter(completer)
        default = "XBTUSD" if "XBTUSD" in self._specs else names[0]
        self.pair_combo.setCurrentText(default)
        self.pair_combo.blockSignals(False)
        self.status.setText(f"{len(names)} pairs loaded")
        self._on_pair_changed(default)

    def _on_pair_changed(self, name: str) -> None:
        spec = self._specs.get(name)
        if spec is None:
            return
        self._slash = f"{translate_asset(spec.base)}/{translate_asset(spec.quote)}"
        self.pair_selected.emit(self._slash)
        asyncio.ensure_future(self._load_mark(name))

    def on_tick(self, symbol: str, price: float) -> None:
        """Live mark for the selected pair, streamed."""
        if symbol == self._slash:
            self._mark = price
            self.mark_label.setText(f"{price:,.8g}")

    async def _load_mark(self, name: str) -> None:
        gen = self._begin_load()
        try:
            from cqd.data.rest import KrakenRESTClient

            async with KrakenRESTClient(api_key="", api_secret="") as client:
                marks = await client.get_marks([name])
        except KrakenError:
            if self._is_current(gen):
                self.mark_label.setText("-")
                self._mark = None
            return
        if not self._is_current(gen):
            return
        self._mark = next(iter(marks.values()), None)
        self.mark_label.setText(f"{self._mark:,.8g}" if self._mark else "-")

    # ---------- field visibility ----------

    def _on_type_changed(self, ordertype: str) -> None:
        needs_price = ordertype in PRICED_TYPES or ordertype.startswith("trailing-stop")
        needs_price2 = ordertype in PRICE2_TYPES
        self.price_edit.setVisible(needs_price)
        self.price_label.setVisible(needs_price)
        self.price_label.setText(
            "Trailing offset" if ordertype.startswith("trailing-stop") else "Price"
        )
        self.price2_edit.setVisible(needs_price2)
        self.price2_label.setVisible(needs_price2)

    # ---------- prefill (Positions "Close") ----------

    def prefill_close(self, asset: str, qty: float) -> None:
        """Pre-fill a full-position market sell; NEVER auto-submits."""
        name = f"{asset}USD"
        candidates = [n for n in self._specs if n.upper() == name.upper()]
        if not candidates and asset == "BTC":
            candidates = [n for n in self._specs if n.upper() == "XBTUSD"]
        if candidates:
            self.pair_combo.setCurrentText(candidates[0])
        self.side_combo.setCurrentText("sell")
        self.type_combo.setCurrentText("market")
        self.volume_edit.setText(f"{qty:.10f}".rstrip("0").rstrip("."))
        self.problems_label.setText("Review the pre-filled close order, then submit.")

    # ---------- submit flow ----------

    def refresh_mode_badge(self) -> None:
        self.mode_badge.setText("PAPER" if services.trading_mode() == "paper" else "LIVE")

    def _on_review(self) -> None:
        self.problems_label.setText("")
        name = self.pair_combo.currentText().strip()
        spec = self._specs.get(name)
        if spec is None:
            self.problems_label.setText(f"Unknown pair '{name}'.")
            return
        request, problems = build_order_request(
            spec=spec,
            side=self.side_combo.currentText(),
            ordertype=self.type_combo.currentText(),
            volume_text=self.volume_edit.text(),
            price_text=self.price_edit.text() if self.price_edit.isVisible() else "",
            price2_text=self.price2_edit.text() if self.price2_edit.isVisible() else "",
            close_type=self.close_combo.currentText(),
            close_price_text=self.close_price_edit.text(),
            mark=self._mark,
        )
        if request is None:
            self.problems_label.setText("\n".join(problems))
            return
        prepared, violations = services.order_service().prepare(request, spec)
        if prepared is None:
            self.problems_label.setText("\n".join(violations))
            return
        dialog = OrderConfirmDialog(prepared, self)
        if dialog.exec() != OrderConfirmDialog.DialogCode.Accepted:
            self.status.setText("Order not sent.")
            return
        self.review_btn.setEnabled(False)
        self.status.setText("Submitting...")
        asyncio.ensure_future(self._submit(prepared.token))

    async def _submit(self, token: str) -> None:
        try:
            if services.trading_mode() == "paper":
                try:
                    await services.ensure_paper_seeded()
                except KrakenError:
                    pass  # unseeded broker rejects cleanly; no crash
            result = await services.order_service().submit(token)
        except Exception as e:  # noqa: BLE001 - surface, never crash the app
            self.status.setText(f"Submit failed: {e}")
            return
        finally:
            self.review_btn.setEnabled(True)
        if result.status == "rejected":
            self.status.setText(f"Rejected: {result.detail}")
        elif result.status == "unknown":
            self.status.setText("Status unknown - check Open Orders (reconciling).")
        else:
            self.status.setText(f"{result.status.upper()} {result.txid or ''} ({result.mode})")
            self.price_edit.clear()
            self.price2_edit.clear()
            self.close_price_edit.clear()
        self.order_submitted.emit()

    def refresh(self) -> None:
        self.refresh_mode_badge()
        name = self.pair_combo.currentText().strip()
        if name in self._specs:
            asyncio.ensure_future(self._load_mark(name))
