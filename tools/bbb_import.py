"""Turn a BootyBayBroker scan export into bbb.json (same shape as thresholds.json items).

BootyBayBroker has about a week of history and its own sales/day, so its history-checked
limits fill in while the Blizzard history in this repo is still short. Only Claude's
browser session can reach BootyBayBroker, so this file is refreshed when Claude runs its scan.

Input (JSON): {"scan_utc": "...", "items": [...], "blackwater": [...]} where items come from
`window.__flipExport` in tools/bbb_scanner.js:
  {id, name, threshold, resale, target, soldPerDay, cheapest, availUnder, low72, med72, high72, p75_72,
   dips, recovered, flags, buckets, ok}
and blackwater entries are {id, name, buy_max, sell_at, bw_sold_per_day, bw_listed, max_qty?}.

  python tools/bbb_import.py                        # newest ~/Downloads/bbb_export*.json -> bbb.json
  python tools/bbb_import.py export.json            # a specific file
"""
import glob
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def r(v):
    return None if v is None else int(round(v))


def convert(src):
    items = {}
    for it in src.get("items", []):
        items[str(it["id"])] = {
            "name": it.get("name"), "target": r(it.get("target")), "buy_max": r(it.get("threshold")),
            "sell_at": r(it.get("resale")), "sold_per_day": round(it.get("soldPerDay") or 0, 1),
            "cheapest": r(it.get("cheapest")), "avail_under": it.get("availUnder"),
            "dips": it.get("dips"), "recovered": it.get("recovered"),
            "flags": it.get("flags", []), "ok": bool(it.get("ok")),
            "low72": r(it.get("low72")), "med72": r(it.get("med72")), "high72": r(it.get("high72")),
            "p75_72": r(it.get("p75_72")), "points": it.get("buckets"), "history": "BootyBayBroker",
        }
    bw = {str(b["id"]): {k: b[k] for k in ("name", "buy_max", "sell_at", "bw_sold_per_day", "bw_listed", "max_qty") if k in b}
          for b in src.get("blackwater", [])}
    return {"source": "BootyBayBroker (realm 438096, Horde)", "scan_utc": src.get("scan_utc"),
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "items": items, "blackwater": bw}


def main():
    if len(sys.argv) > 1:
        src_path = sys.argv[1]
    else:  # the scanner downloads bbb_export.json; Chrome names repeats bbb_export (1).json etc.
        found = glob.glob(os.path.join(os.path.expanduser("~"), "Downloads", "bbb_export*.json"))
        if not found:
            sys.exit("no bbb_export*.json in Downloads; pass a path")
        src_path = max(found, key=os.path.getmtime)
        print("reading", src_path)
    src = json.load(open(src_path))
    out = convert(src)
    path = os.path.join(ROOT, "bbb.json")
    json.dump(out, open(path, "w"), separators=(",", ":"))
    print("wrote", path, "|", len(out["items"]), "items,", sum(1 for v in out["items"].values() if v["ok"]), "pass the history rule,",
          len(out["blackwater"]), "blackwater")


if __name__ == "__main__":
    main()
