"""Hourly job: download the US Horde + Blackwater auction houses, store a summary, write the report.

Environment: BLIZZARD_CLIENT_ID, BLIZZARD_CLIENT_SECRET.
Usage: python run.py            normal hourly run
       python run.py --discover list every Horde/Blackwater house with reference-item prices
"""
import gzip
import json
import os
import sys
from datetime import datetime, timezone

from wowprices.api import Api
from wowprices.collect import hour_path, ladders, parse_listings, snapshot_time, summarize, write_rows
from wowprices import report

ROOT = os.environ.get("WOWPRICES_ROOT") or os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
STATE = os.path.join(ROOT, "state")


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def load_state(key):
    p = os.path.join(STATE, f"{key}.json.gz")
    try:
        with gzip.open(p, "rt") as f:
            return json.load(f)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def save_state(key, st):
    os.makedirs(STATE, exist_ok=True)
    with gzip.open(os.path.join(STATE, f"{key}.json.gz"), "wt") as f:
        json.dump(st, f, separators=(",", ":"))


def fetch_house(api, cfg, house_id):
    ns = f"dynamic-forever-{cfg['region']}"
    base = f"/data/wow/ruleset/{cfg['ruleset']}/auctions/{house_id}"
    auctions, h1 = api.get(base, ns)
    commodities, h2 = api.get(base + "/commodities", ns)
    return parse_listings(auctions, commodities), snapshot_time(h2 if commodities else h1), (h2 or h1).get("Last-Modified")


def make_namer(api, cfg, cache):
    budget = {"left": cfg["report"]["max_names_per_run"]}

    def name_of(iid):
        k = str(iid)
        if k in cache:
            return cache[k]
        if api is None or budget["left"] <= 0:
            return f"item {iid}"
        budget["left"] -= 1
        for ns in (f"static-forever-{cfg['region']}", f"static-{cfg['region']}"):
            try:
                j, _ = api.get(f"/data/wow/item/{iid}", ns, retries=2, timeout=30)
            except Exception:
                j = None
            if j and j.get("name"):
                cache[k] = j["name"] if isinstance(j["name"], str) else j["name"].get("en_US", str(iid))
                return cache[k]
        cache[k] = f"item {iid}"
        return cache[k]
    return name_of


def discover(api, cfg):
    ns = f"dynamic-forever-{cfg['region']}"
    idx, _ = api.get(f"/data/wow/ruleset/{cfg['ruleset']}/auctions/", ns)
    houses = (idx or {}).get("auctions") or []
    refs = {int(k): v for k, v in cfg["reference_items"].items()}
    out = []
    for h in houses:
        name = h.get("name") if isinstance(h.get("name"), str) else (h.get("name") or {}).get("en_US", "")
        if "Alliance" in (name or ""):
            continue
        listings, ts, _ = fetch_house(api, cfg, h["id"])
        lad = ladders(listings)
        out.append({"id": h["id"], "name": name, "key": h.get("key"), "auctions": len(listings),
                    "snapshot_utc": ts.isoformat(),
                    "reference": {refs[i]: ({"min": round(lad[i][0][0]), "units": sum(q for _, q in lad[i])}
                                            if i in lad else None) for i in refs}})
        print(f"house {h['id']} {name}: {len(listings)} auctions")
    with open(os.path.join(ROOT, "discovery.json"), "w") as f:
        json.dump({"checked_utc": datetime.now(timezone.utc).isoformat(), "ruleset": cfg["ruleset"],
                   "houses": out}, f, indent=1)


def main(api=None):
    cfg = load_json(os.path.join(ROOT, "config.json"), None)
    holdings = load_json(os.path.join(ROOT, "holdings.json"), {})
    names = load_json(os.path.join(ROOT, "items.json"), {})
    os.makedirs(STATE, exist_ok=True)
    if api is None:
        cid, secret = os.environ.get("BLIZZARD_CLIENT_ID"), os.environ.get("BLIZZARD_CLIENT_SECRET")
        if not cid or not secret:
            print("BLIZZARD_CLIENT_ID / BLIZZARD_CLIENT_SECRET are not set yet: skipping this run. See README.")
            return
        api = Api(cid, secret, cfg["region"])

    if "--discover" in sys.argv or (not cfg.get("houses_verified") and not os.path.exists(os.path.join(ROOT, "discovery.json"))):
        discover(api, cfg)
        if "--discover" in sys.argv:
            return

    lads, newest = {}, None
    for key, house_id in cfg["houses"].items():
        listings, ts, lm = fetch_house(api, cfg, house_id)
        prev = load_state(key)
        if prev.get("last_modified") and prev.get("last_modified") == lm:
            print(f"{key}: no new snapshot since {lm}")
            lads[key] = ladders(listings)
            newest = newest or ts
            continue
        rows, st = summarize(listings, prev.get("auctions"))
        write_rows(hour_path(DATA, key, ts), rows)
        save_state(key, {"last_modified": lm, "snapshot_utc": ts.isoformat(), "auctions": st})
        lads[key] = ladders(listings)
        newest = max(newest, ts) if newest else ts
        print(f"{key} (house {house_id}): {len(listings)} auctions, {len(rows)} items at {ts.isoformat()}")

    report.build(cfg, DATA, newest, lads, holdings, make_namer(api, cfg, names), ROOT)
    with open(os.path.join(ROOT, "items.json"), "w") as f:
        json.dump(dict(sorted(names.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0)), f, indent=0)


if __name__ == "__main__":
    main()
