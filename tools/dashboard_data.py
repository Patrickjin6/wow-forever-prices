"""Build the data files for the WoW Forever dashboard (Claude Data artifact).

Runs advise.py's merge (Blizzard history + live Auctionator scan) and writes one JSON file of
rows per dashboard dataset into an output folder:
  status.json     one row: freshness of both sources, gold
  buys.json       buy now (history rule passed, price under the buy limit)
  holdings.json   what you hold: price now vs break-even, what to do
  blackwater.json buy on Horde, sell on Blackwater
  sell_high.json  well above usual: sell if you hold
  watch.json      just above the buy limit
  daily.json      Auctionator's daily low/high for held and recommended items (last 10 days)
Prices are copper numbers, each with a *_text column ("1g 2s 3c") for display.

  python tools/dashboard_data.py --auctionator path/to/Auctionator.lua --out /tmp/dash
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import advise  # noqa: E402
import auctionator  # noqa: E402

coin = auctionator.coin


def r0(v):
    return None if v is None else int(round(v))


def priced(row, **fields):
    for k, v in fields.items():
        row[k] = r0(v)
        row[k + "_text"] = coin(v) if v is not None else "-"
    return row


def hist_cols(row, it):
    it = it or {}
    priced(row, low72=it.get("low72"), med72=it.get("med72"), high72=it.get("high72"))
    row["bounced"] = f"{it.get('recovered', 0)}/{it.get('dips', 0)}" if it.get("dips") else "-"
    row["sold_per_day"] = round(it["sold_per_day"]) if it.get("sold_per_day") else None
    return row


def build(repo, auc_path, out, max_age=2.0, now=None):
    now = now or datetime.now(timezone.utc)
    a = advise.advise(repo, auc_path, max_age, now)
    hold = advise.load_json(os.path.join(repo, "holdings.json"), {})
    src = lambda s: "your scan" if s == "live" else ("Blizzard" if s else "-")
    os.makedirs(out, exist_ok=True)

    status = [priced({
        "generated_utc": now.isoformat(timespec="minutes"),
        "blizzard_snapshot_utc": a["snapshot"].isoformat(timespec="minutes") if a["snapshot"] else None,
        "history_hours": a["history_hours"],
        "scan_utc": a["scan"].isoformat(timespec="minutes") if a["scan"] else None,
        "scan_used": bool(a["scan"] and not a["scan_note"]),
        "scan_note": a["scan_note"] or "",
        "live_items": a["live_items"],
        "buy_count": len(a["plan"]),
        "holdings_below_break_even": sum(1 for h in a["holdings"] if h["now"] and h["now"] < h["break_even"]),
        "holdings_count": len(a["holdings"]),
    }, cash=hold.get("cash_copper"), pending=hold.get("pending_copper"), listed_total=hold.get("listed_total_copper"))]

    buys = [hist_cols(priced({"item": b["name"], "item_id": int(b["id"]), "qty": b["qty"], "price_source": src(b["source"])},
                             buy_max=b["buy_max"], cheapest=b["price"], sell_at=b["sell_at"], cost=b["cost"],
                             profit=b["profit"]), b["history"]) for b in a["plan"]]

    holdings = [hist_cols(priced({"item": h["name"], "item_id": int(h["id"]), "qty": h["qty"],
                                  "status": h["status"] or "in bags", "price_source": src(h["source"]),
                                  "action": h["verdict"],
                                  "vs_break_even": (round(h["now"] / h["break_even"] - 1, 3) if h["now"] else None)},
                                 paid=h["paid"], break_even=h["break_even"], now=h["now"]), h["history"])
                for h in a["holdings"]]

    bw = [priced({"item": b["name"], "item_id": int(b["id"]), "qty": b["qty"], "price_source": src(b["source"]),
                  "bw_sold_per_day": round(b["bw_sold_per_day"])},
                 buy_max=b["buy_max"], cheapest=b["price"], sell_at=b["sell_at"], cost=b["cost"], profit=b["profit"])
          for b in a["blackwater"]]

    high = [priced({"item": h["name"], "item_id": int(h["id"]), "price_source": src(h["source"]),
                    "above_usual": round(h["price"] / h["target"] - 1, 3)}, now=h["price"], usual=h["target"])
            for h in a["high"]]

    watch = [priced({"item": w["name"], "item_id": int(w["id"]), "price_source": src(w["source"]),
                     "gap": round(w["price"] / w["buy_max"] - 1, 3)}, now=w["price"], buy_max=w["buy_max"])
             for w in a["watch"]]

    # Auctionator daily ranges for the items on the page
    daily = []
    if auc_path:
        d = auctionator.load(auc_path)
        db = d["databases"].get("PvE Horde") or next(iter(d["databases"].values()), {})
        want = {str(r["item_id"]): r["item"] for r in holdings + buys + bw}
        for iid, name in want.items():
            e = db.get(iid)
            if not e:
                continue
            h, l, av = e.get("h") or {}, e.get("l") or {}, e.get("a") or {}
            l = l if isinstance(l, dict) else {}
            for day in sorted(set(h) | set(l), key=int)[-10:]:
                lo, hi = l.get(day, h.get(day)), h.get(day, l.get(day))
                daily.append(priced({"item": name, "item_id": int(iid), "date": str(auctionator.day_date(day)),
                                     "units": av.get(day)}, low=lo, high=hi))

    pct_text = lambda v: "-" if v is None else f"{v * 100:+.0f}%"
    for r in holdings:
        r["vs_break_even_text"] = pct_text(r["vs_break_even"])
    for r in high:
        r["above_usual_text"] = pct_text(r["above_usual"])
    for r in watch:
        r["gap_text"] = pct_text(r["gap"])

    files = {"status": status, "buys": buys, "holdings": holdings, "blackwater": bw,
             "sell_high": high, "watch": watch, "daily": daily}
    meta = [
        {"label": "Made by", "value": "tools/dashboard_data.py in github.com/Patrickjin6/wow-forever-prices"},
        {"label": "Blizzard snapshot (UTC)", "value": status[0]["blizzard_snapshot_utc"] or "-"},
        {"label": "Auctionator scan (UTC)", "value": status[0]["scan_utc"] or "-"},
        {"label": "Built (UTC)", "value": status[0]["generated_utc"]},
        {"label": "dashboard_data.py", "value": open(os.path.abspath(__file__)).read(), "type": "code", "lang": "python"},
    ]
    for k, rows in files.items():
        with open(os.path.join(out, f"{k}.json"), "w") as f:
            json.dump({"rows": rows, "meta": meta}, f, separators=(",", ":"))
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.path.dirname(HERE))
    ap.add_argument("--auctionator")
    ap.add_argument("--out", default="/tmp/wow-dashboard")
    ap.add_argument("--max-age", type=float, default=2.0)
    a = ap.parse_args()
    files = build(a.repo, a.auctionator, a.out, a.max_age)
    print("wrote", a.out, {k: len(v) for k, v in files.items()})


if __name__ == "__main__":
    main()
