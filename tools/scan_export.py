"""Save the prices from Patrick's latest Auctionator scan as scan.json, so every device sees them.

The GitHub page can read Auctionator.lua only in the browser it's dropped into. When Claude
runs a check it also writes this file, which the page uses if it's newer than the browser's own.

  python tools/scan_export.py path/to/Auctionator.lua      # writes scan.json in the repo root
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import auctionator  # noqa: E402


def export(auc_path, out_path=None):
    d = auctionator.load(auc_path)
    db = d["databases"].get("PvE Horde") or next(iter(d["databases"].values()), {})
    scan = d["last_scan"]
    scan_day = (scan - auctionator.DAY0).days if scan else 10 ** 9
    try:
        held = {str(p["item_id"]) for p in json.load(open(os.path.join(ROOT, "holdings.json"))).get("positions", [])}
    except (FileNotFoundError, json.JSONDecodeError):
        held = set()
    prices, daily = {}, {}
    for k, v in db.items():
        iid = k.split(":")[1] if k.startswith("gr:") else k.split(":")[0]
        if not iid.isdigit() or not isinstance(v.get("m"), (int, float)):
            continue
        h = v.get("h") if isinstance(v.get("h"), dict) else {}
        days = [int(x) for x in h]
        if days and max(days) >= scan_day:
            prices[iid] = min(prices.get(iid, v["m"]), v["m"])
        if iid in held and k == iid:
            l = v.get("l") if isinstance(v.get("l"), dict) else {}
            a = v.get("a") if isinstance(v.get("a"), dict) else {}
            daily[iid] = [{"day": int(x), "low": l.get(x, h.get(x)), "high": h.get(x), "units": a.get(x)}
                          for x in sorted(h, key=int)[-10:]]
    out = {"scan": scan.isoformat() if scan else None, "prices": prices, "daily": daily}
    out_path = out_path or os.path.join(ROOT, "scan.json")
    json.dump(out, open(out_path, "w"), separators=(",", ":"))
    return out


if __name__ == "__main__":
    o = export(sys.argv[1])
    print("wrote scan.json | scan", o["scan"], "|", len(o["prices"]), "prices |", len(o["daily"]), "holdings with daily history")
