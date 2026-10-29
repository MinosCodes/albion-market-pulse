"""Order tracker: fees, stock and profit, outbid detection and the HTTP routes."""
from __future__ import annotations

import json

import pytest

from albion_flips.insights import AlertBook
from albion_flips.orders import OrderBook, unit_value
from albion_flips.server import Insights, attach_overlay

from test_insights import AK, IK, _login, _req, _store, market  # noqa: F401  (fixtures)

TAX, SETUP = 0.04, 0.025
CITIES = ["Martlock", "Lymhurst", "Black Market"]


def _o(**kw):
    base = {"item_id": "T4_BAG", "kind": "buy_order", "city": "Martlock", "price": 1000, "qty": 10}
    base.update(kw)
    return OrderBook.validate(base, CITIES)


def test_fees() -> None:
    assert unit_value("buy_order", 1000, TAX, SETUP) == pytest.approx(1025)
    assert unit_value("buy_instant", 1000, TAX, SETUP) == 1000
    assert unit_value("sell_order", 1000, TAX, SETUP) == pytest.approx(935)
    assert unit_value("sell_instant", 1000, TAX, SETUP) == pytest.approx(960)


def test_validation() -> None:
    assert _o()["filled"] == 0
    assert _o(kind="buy_instant")["filled"] == 10  # instant trades are complete
    for bad in ({"kind": "steal"}, {"city": "Atlantis"}, {"price": 0}, {"qty": 0}, {"filled": 11}, {"item_id": "../x"}):
        with pytest.raises(ValueError):
            _o(**bad)


def test_stock_and_profit() -> None:
    book = OrderBook()
    book.add(_o(price=1000, qty=10, filled=10))                       # 10 × 1025 = 10,250
    book.add(_o(kind="buy_instant", price=1200, qty=10))              # 10 × 1200 = 12,000 -> avg 1112.5
    book.add(_o(kind="sell_order", city="Lymhurst", price=2000, qty=5, filled=5))  # 5 × 1870 = 9,350
    quotes = [("T4_BAG", "Black Market", 1, 0, 1500), ("T4_BAG", "Martlock", 1, 1300, 1100)]
    v = book.view(quotes, TAX, SETUP)
    t = v["totals"]
    assert t["spent"] == 22250 and t["earned"] == 9350
    assert abs(t["realised"] - (9350 - 5 * 1112.5)) <= 1
    stock = v["stock"][0]
    assert stock["qty"] == 15 and stock["avg_cost"] == 1112 and stock["best_city"] == "Black Market"
    assert stock["value_now"] == 15 * 1440 and t["stock_value"] == 21600
    assert t["total"] == 9350 + 21600 - 22250
    assert t["open"] == 0  # filled orders close themselves


def test_outbid_fires_once_until_back_on_top() -> None:
    book = OrderBook()
    o = book.add(_o(price=1000))
    assert book.check([("T4_BAG", "Martlock", 1, 0, 990)]) == []
    ev = book.check([("T4_BAG", "Martlock", 1, 0, 1010)])
    assert len(ev) == 1 and ev[0]["kind"] == "outbid" and ev[0]["price"] == 1010 and ev[0]["target"] == 1000
    assert book.check([("T4_BAG", "Martlock", 1, 0, 1020)]) == []     # still outbid: no repeat
    assert book.check([("T4_BAG", "Lymhurst", 1, 0, 9999)]) == []     # other city does not matter
    book.update(o["id"], {"price": 1021})                              # relisted
    assert book.check([("T4_BAG", "Martlock", 1, 0, 1021)]) == []
    assert len(book.check([("T4_BAG", "Martlock", 1, 0, 1022)])) == 1
    view = book.view([("T4_BAG", "Martlock", 1, 0, 1022)], TAX, SETUP)
    assert view["orders"][0]["status"] == "outbid" and view["totals"]["outbid"] == 1


def test_new_price_keeps_filled_part_at_old_price() -> None:
    book = OrderBook()
    o = book.add(_o(price=2000, qty=50, filled=12))
    book.update(o["id"], {"price": 2478})
    done = [x for x in book.orders if x.get("closed")]
    assert len(done) == 1 and done[0]["qty"] == 12 and done[0]["price"] == 2000
    live = next(x for x in book.orders if x["id"] == o["id"])
    assert live["qty"] == 38 and live["filled"] == 0 and live["price"] == 2478
    assert book.view([], TAX, SETUP)["totals"]["spent"] == round(12 * 2000 * 1.025)


def test_sell_order_undercut_and_close(tmp_path) -> None:
    book = OrderBook(tmp_path / "orders.json")
    o = book.add(_o(kind="sell_order", price=2000, qty=4))
    assert book.check([("T4_BAG", "Martlock", 1, 1999, 0)])[0]["order_kind"] == "sell_order"
    book.update(o["id"], {"filled": 4})
    assert OrderBook(tmp_path / "orders.json").orders[0]["closed"]  # completely filled -> closed, saved
    with pytest.raises(ValueError):
        book.update(o["id"], {"filled": 5})
    with pytest.raises(ValueError):
        book.update("nope", {"filled": 1})


# ------------------------------------------------------------ HTTP
@pytest.fixture()
def server(market, tmp_path):  # noqa: F811
    import socket

    from albion_flips.server import Auth, make_handler
    from albion_flips.web import start_web_server

    store = _store(market)
    auth = Auth(access_key=AK, ingest_key=IK, public_read=True, allow_local_ingest=False)
    ins = Insights(market, store, AlertBook(), ["Martlock", "Lymhurst"], ["T4_BAG"], orders=OrderBook(tmp_path / "o.json"))
    hub = attach_overlay(store, None, ins)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    httpd = start_web_server(store, port=port, host="127.0.0.1", handler_cls=make_handler(auth, dict, hub, store, ins))
    yield port, ins
    httpd.shutdown()


def test_http_orders(server) -> None:
    port, ins = server
    order = {"kind": "buy_order", "item_id": "T4_BAG", "city": "Martlock", "price": 700, "qty": 20}
    assert _req(port, "POST", "/api/orders", order)[0] == 401
    st, d = _req(port, "GET", "/api/orders")
    assert st == 200 and d["can_edit"] is False and d["orders"] == []

    cookie = _login(port)
    st, d = _req(port, "POST", "/api/orders", order, cookie)
    assert st == 200
    oid = d["order"]["id"]
    # Martlock's best buy order is 800 > 700: outbid right away, reported in the alert feed
    st, d = _req(port, "GET", "/api/orders", cookie=cookie)
    assert d["orders"][0]["status"] == "outbid" and d["orders"][0]["market_now"] == 800
    assert ins.alerts.events and ins.alerts.events[0]["kind"] == "outbid"
    assert _req(port, "POST", "/api/orders/update", {"id": oid, "filled": 25}, cookie)[0] == 400
    assert _req(port, "POST", "/api/orders/update", {"id": oid, "price": 801, "filled": 5}, cookie)[1]["order"]["filled"] == 5
    st, d = _req(port, "GET", "/api/orders", cookie=cookie)
    assert d["orders"][0]["status"] == "top" and d["totals"]["spent"] == round(5 * 801 * 1.025)
    assert _req(port, "POST", "/api/orders/delete", {"id": oid}, cookie)[1]["ok"] is True
    assert json.loads(json.dumps(ins.orders.orders)) == []
