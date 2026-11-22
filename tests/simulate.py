"""Offline end-to-end test: simulate a week of hourly auction house snapshots.

Run:  python tests/simulate.py
It runs run.main() against a fake Blizzard API in a temporary folder and checks the report.
"""
import json
import math
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="wowsim-")
os.environ["WOWPRICES_ROOT"] = TMP
sys.path.insert(0, REPO)
_cfg = json.load(open(os.path.join(REPO, "config.json")))
_cfg["houses_verified"] = False  # so the first run also exercises discovery
json.dump(_cfg, open(os.path.join(TMP, "config.json"), "w"))
with open(os.path.join(TMP, "holdings.json"), "w") as f:
    json.dump({"cash_copper": 50000, "positions": [
        {"item_id": 15417, "name": "Devilsaur Leather", "qty": 8, "paid_each": 6500},
        {"item_id": 13465, "name": "Mountain Silversage", "qty": 20, "paid_each": 1200}]}, f)

import run  # noqa: E402  (after WOWPRICES_ROOT is set)

START = datetime(2026, 11, 15, 18, tzinfo=timezone.utc)
HOURS = 170
NAMES = {1001: "Dipper Root", 1002: "Falling Knife Ore", 15417: "Devilsaur Leather",
         13465: "Mountain Silversage", 2001: "Briny Seafood Stew", 3001: "Tigerstrike Mantle"}


def price(iid, h, house):
    if iid == 1001:  # 1g normally, dips to 70s for 5h every 30h; currently in a dip
        return 7000 if (h - 165) % 30 < 5 else 10000
    if iid == 1002:  # falls ~0.6% an hour from 2g
        return 20000 * (0.994 ** h)
    if iid == 15417:  # daily swing 56s-76s
        return 6600 + 1000 * math.sin(2 * math.pi * h / 24)
    if iid == 13465:  # slowly drifting down
        return 1250 - h * 0.8
    if iid == 2001:
        return 1509 if house == 114 else 2888
    if iid == 3001:
        return 30000
    raise KeyError(iid)


SALES = {1001: 10, 1002: 10, 15417: 30, 13465: 80, 2001: 60, 3001: 1}
ITEMS = {114: [1001, 1002, 15417, 13465, 2001, 3001], 117: [2001]}
BW_SALES = {2001: 14}


class Sim:
    def __init__(self):
        self.pools = {(house, iid): [] for house, ids in ITEMS.items() for iid in ids}
        self.next_id = 1
        self.h = 0

    def step(self, h):
        self.h = h
        for (house, iid), pool in self.pools.items():
            sales = BW_SALES.get(iid, SALES[iid]) if house == 117 else SALES[iid]
            # sales take the cheapest units first
            pool.sort(key=lambda a: a["unit"])
            left = sales
            for a in pool:
                n = min(a["qty"], left)
                a["qty"] -= n
                left -= n
                if not left:
                    break
            # expiry: 24h old auctions go SHORT, then vanish
            for a in pool:
                age = h - a["born"]
                if age >= 25:
                    a["qty"] = 0
                elif age >= 24:
                    a["tl"] = "SHORT"
            pool[:] = [a for a in pool if a["qty"] > 0]
            # new listings, slightly above the current price
            p = price(iid, h, house)
            per = max(1, round(sales * 1.15 / 2))
            for bump in (1.0, 1.03):
                pool.append({"id": self.next_id, "qty": 1 if iid == 3001 else per, "unit": round(p * bump),
                             "tl": "VERY_LONG", "born": h})
                self.next_id += 1

    def payload(self, house, commodities):
        out = []
        for (hid, iid), pool in self.pools.items():
            if hid != house or (iid == 3001) == commodities:
                continue
            for a in pool:
                if commodities:
                    out.append({"id": a["id"], "item": {"id": iid}, "quantity": a["qty"],
                                "unit_price": a["unit"], "time_left": a["tl"]})
                else:
                    out.append({"id": a["id"], "item": {"id": iid}, "quantity": 1,
                                "buyout": a["unit"], "time_left": a["tl"]})
        return {"auctions": out}


class FakeApi:
    def __init__(self, sim):
        self.sim = sim

    def get(self, path, namespace, params=None, retries=4, timeout=180):
        hdr = {"Last-Modified": format_datetime(START + timedelta(hours=self.sim.h), usegmt=True)}
        if path.endswith("/auctions/"):
            return {"auctions": [{"id": 110, "name": "Alliance Auction House"},
                                 {"id": 113, "name": "Horde Auction House"},
                                 {"id": 114, "name": "Horde Auction House"},
                                 {"id": 117, "name": "Blackwater Auction House"}]}, hdr
        if path.startswith("/data/wow/item/"):
            iid = int(path.rsplit("/", 1)[1])
            return ({"name": NAMES[iid]} if iid in NAMES else None), hdr
        parts = path.split("/")
        house = int(parts[6])
        if house not in ITEMS:
            return {"id": house}, hdr  # empty house: no auctions field
        return self.sim.payload(house, path.endswith("/commodities")), hdr


def main():
    sim, api = Sim(), None
    api = FakeApi(sim)
    for h in range(HOURS):
        sim.step(h)
        run.main(api)
    run.main(api)  # same snapshot again: should be skipped, not duplicated
    md = open(os.path.join(TMP, "report.md")).read()
    rep = json.load(open(os.path.join(TMP, "report.json")))
    disc = json.load(open(os.path.join(TMP, "discovery.json")))
    print(md)
    print("---- checks")
    buys = [b["name"] for b in rep["buy_now"]]
    checks = {
        "discovery lists Horde + Blackwater only": sorted(h["id"] for h in disc["houses"]) == [113, 114, 117],
        "Dipper Root is a buy": "Dipper Root" in buys,
        "Falling Knife is not a buy": "Falling Knife Ore" not in buys,
        "two holdings reported": len(rep["holdings"]) == 2,
        "Blackwater stew found": any(b["name"] == "Briny Seafood Stew" for b in rep["blackwater"]),
        "history hours ~170": 165 <= rep["history_hours"] <= 171,
    }
    th = json.load(open(os.path.join(TMP, "thresholds.json")))
    d = th["items"].get("1001", {})
    checks["thresholds.json has Dipper with buy limit"] = d.get("ok") and d.get("buy_max", 0) > 0 and d.get("name") == "Dipper Root"
    checks["thresholds.json has Blackwater stew"] = "2001" in th["blackwater"]
    files = sum(len(fs) for _, _, fs in os.walk(os.path.join(TMP, "data")))
    checks["one file per house-hour"] = files == HOURS * 2
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    print("data size:", sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(os.path.join(TMP, "data")) for f in fs), "bytes")
    shutil.rmtree(TMP)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
