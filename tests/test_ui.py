"""Guards against dashboard mistakes that only a browser would show."""

from __future__ import annotations

from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "fxlab" / "static" / "index.html"


class _Inputs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.inputs: dict[str, dict[str, str | None]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "input":
            d = dict(attrs)
            if d.get("name"):
                self.inputs[str(d["name"])] = d


def test_default_quantity_passes_the_browsers_own_validation() -> None:
    """Regression: `min=1 step=1000` made 1,000,000 invalid, because browsers count the step
    from `min` (valid values were 1, 1001, 2001...), so the ticket could not be submitted."""
    parser = _Inputs()
    parser.feed(INDEX.read_text())
    qty = parser.inputs["quantity"]

    value = Decimal(str(qty["value"]))
    assert value > 0
    step = qty.get("step")
    if step not in (None, "any"):
        base = Decimal(str(qty.get("min") or 0))  # HTML: the step base is `min`
        assert (value - base) % Decimal(step) == 0, "default quantity violates its own step"
    if qty.get("min") is not None:
        assert value >= Decimal(str(qty["min"]))
    if qty.get("max") is not None:
        assert value <= Decimal(str(qty["max"]))


def test_quantity_accepts_any_size_the_server_allows() -> None:
    """Size limits are the server's job (it records a visible REJECTED order); the browser must
    not add a second, surprising rule."""
    parser = _Inputs()
    parser.feed(INDEX.read_text())
    assert parser.inputs["quantity"].get("step") == "any"


def test_dashboard_has_no_admin_key_field() -> None:
    assert 'id="api-key"' not in INDEX.read_text()
