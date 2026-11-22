"""Test tools/advise.py with a synthetic thresholds.json and a fake Auctionator.lua.

Needs cbor2 only to build the fake file:  pip install cbor2 ; python tests/test_advise.py
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import cbor2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
import advise  # noqa: E402
import auctionator  # noqa: E402

NOW = datetime(2026, 11, 22, 19, 30, tzinfo=timezone.utc)
SCAN = NOW - timedelta(minutes=40)
DAY = (SCAN - auctionator.DAY0).days


def lua_quote(b):
    out = bytearray(b'"')
    for c in b:
        if c == 34:
            out += b'\\"'
        elif c == 92:
            out += b'\\\\'
        elif c == 10:
            out += b'\\\r\n'      # how WoW writes an escaped newline on Windows
        elif c == 13:
            out += b'\\r'
        elif c == 0:
            out += b'\\0'
        else:
            out.append(c)
    return bytes(out + b'"')


def fake_auctionator(path, prices, scan=SCAN, day=DAY):
    db = {str(k).encode(): {b"m": v, b"h": {str(day).encode(): v}, b"l": [], b"a": {str(day).encode(): 10}}
          for k, v in prices.items()}
    db[b"999"] = {b"m": 1, b"h": {str(day - 3).encode(): 1}, b"l": [], b"a": {}}  # not seen in latest scan
    blob = cbor2.dumps(db)
    body = (b'\r\nAUCTIONATOR_SAVEDVARS = {\r\n["TimeOfLastBrowseScan"] = ' + str(int(scan.timestamp())).encode()
            + b',\r\n}\r\nAUCTIONATOR_PRICE_DATABASE = {\r\n["__dbversion"] = 8,\r\n["PvE Horde"] = '
            + lua_quote(blob) + b',\r\n}\r\nAUCTIONATOR_POSTING_HISTORY = {\r\n}\r\n')
    open(path, "wb").write(body)


def main():
    tmp = tempfile.mkdtemp()
    it = lambda **kw: {"target": 10000, "buy_max": 8636, "sell_at": 10000, "sold_per_day": 200, "listed": 300,
                       "cheapest": 9800, "dips": 4, "recovered": 4, "recovery_hours": 6, "flags": [], "ok": True,
                       "low72": 7000, "med72": 10000, "high72": 10500, "p75_72": 10200, "points": 72, **kw}
    json.dump({"snapshot_utc": (NOW - timedelta(hours=1)).isoformat(), "history_hours": 170, "cut": 0.05,
               "blackwater_cut": 0.15, "min_margin": 0.1,
               "items": {"1001": it(name="Dipper Root"),                       # live 7000 -> buy
                         "1002": it(name="Knife", ok=False, flags=["no-recovery"]),  # never a buy
                         "1003": it(name="Pricey", cheapest=12000),            # live 12000 -> sell-if-hold
                         "15417": it(name="Devilsaur Leather", target=7000, buy_max=6000, sell_at=7000,
                                     low72=6000, med72=6600, high72=7600, p75_72=7100)},
               "blackwater": {"2001": {"buy_max": 2231, "sell_at": 2887, "bw_sold_per_day": 330, "bw_listed": 120,
                                       "name": "Briny Seafood Stew"}}},
              open(os.path.join(tmp, "thresholds.json"), "w"))
    json.dump({"cash_copper": 50000, "positions": [{"item_id": 15417, "name": "Devilsaur Leather", "qty": 8,
                                                     "paid_each": 6500, "status": "listed"}]},
              open(os.path.join(tmp, "holdings.json"), "w"))
    json.dump({"report": {"max_share": 0.4, "day_share": 0.25, "min_sold_per_day": 10}}, open(os.path.join(tmp, "config.json"), "w"))
    auc = os.path.join(tmp, "Auctionator.lua")
    fake_auctionator(auc, {1001: 7000, 1002: 3000, 1003: 12000, 15417: 6019, 2001: 1508, 10: 10})

    # the fake file must round-trip through the real reader (incl. a value of 10 = newline byte)
    d = auctionator.load(auc)
    assert d["databases"]["PvE Horde"]["10"]["m"] == 10, "escaped newline decoded wrong"

    a = advise.advise(tmp, auc, 2.0, NOW)
    md = advise.render(a)
    print(md)
    stale = advise.advise(tmp, auc, 2.0, NOW + timedelta(hours=5))
    checks = {
        "live scan used": a["live_items"] >= 5 and not a["scan_note"],
        "Dipper is a buy at the live price": any(b["id"] == "1001" and b["price"] == 7000 and b["source"] == "live" for b in a["plan"]),
        "Knife is not a buy": all(b["id"] != "1002" for b in a["plan"]),
        "Blackwater stew found": any(b["id"] == "2001" for b in a["blackwater"]),
        "Pricey flagged as sell-if-hold": any(h["id"] == "1003" for h in a["high"]),
        "holding uses live price": a["holdings"][0]["now"] == 6019 and a["holdings"][0]["source"] == "live",
        "item missing from latest scan ignored": "999" not in advise.live_prices(auc, 2, NOW)[0],
        "stale scan falls back to Blizzard": stale["live_items"] == 0 and "old" in (stale["scan_note"] or ""),
    }
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    shutil.rmtree(tmp)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
