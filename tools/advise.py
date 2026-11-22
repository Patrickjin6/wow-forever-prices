"""Combine the hourly Blizzard history (thresholds.json) with a live Auctionator scan.

Blizzard history decides the limits (buy max, sell price, does it bounce back, sales per day).
Your Auctionator scan says what the cheapest price is right now. Where the scan is fresh it
wins; otherwise the Blizzard snapshot price is used.

  python tools/advise.py --auctionator path/to/Auctionator.lua      # markdown to stdout
  python tools/advise.py --auctionator ... --json advice.json      # also write JSON
  python tools/advise.py                                            # Blizzard prices only
Run from the repo root (or pass --repo). No dependencies.
"""
import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import auctionator  # noqa: E402

try:
    from zoneinfo import ZoneInfo
    UK = ZoneInfo("Europe/London")
except Exception:  # pragma: no cover
    UK = timezone.utc

coin = auctionator.coin


def uk(ts):
    return ts.astimezone(UK).strftime("%a %d %b %H:%M")


def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def live_prices(path, max_age_h, now):
    """item_id(str) -> live cheapest price from an Auctionator scan, if the scan is fresh."""
    if not path:
        return {}, None, "no Auctionator file given"
    d = auctionator.load(path)
    scan = d["last_scan"]
    db = d["databases"].get("PvE Horde") or next(iter(d["databases"].values()), {})
    if not scan:
        return {}, None, "no full scan recorded in the file"
    age = (now - scan).total_seconds() / 3600
    if age > max_age_h:
        return {}, scan, f"scan is {age:.1f}h old (limit {max_age_h}h): do a full scan and /reload"
    scan_day = (scan - auctionator.DAY0).days
    out = {}
    for k, v in db.items():
        iid = k.split(":")[1] if k.startswith("gr:") else k.split(":")[0]
        if not iid.isdigit() or not isinstance(v.get("m"), (int, float)):
            continue
        days = [int(x) for x in (v.get("h") or {})] if isinstance(v.get("h"), dict) else []
        if days and max(days) >= scan_day:  # seen in the latest scan
            out[iid] = min(out.get(iid, math.inf), v["m"])
    return out, scan, None


def advise(repo, auc_path=None, max_age_h=2.0, now=None):
    now = now or datetime.now(timezone.utc)
    th = load_json(os.path.join(repo, "thresholds.json"), {})
    hold = load_json(os.path.join(repo, "holdings.json"), {})
    names = load_json(os.path.join(repo, "items.json"), {})
    cfg = load_json(os.path.join(repo, "config.json"), {})
    rc = cfg.get("report", {})
    cut, bcut = th.get("cut", 0.05), th.get("blackwater_cut", 0.15)
    items, bwl = th.get("items", {}), th.get("blackwater", {})
    live, scan, why = live_prices(auc_path, max_age_h, now)
    snap = datetime.fromisoformat(th["snapshot_utc"]) if th.get("snapshot_utc") else None

    def name(iid):
        return (items.get(iid) or {}).get("name") or (bwl.get(iid) or {}).get("name") or names.get(iid) or f"item {iid}"

    def price(iid):
        if iid in live:
            return live[iid], "live"
        c = (items.get(iid) or {}).get("cheapest")
        return c, "blizzard" if c is not None else None

    budget = int(hold.get("cash_copper") or 50000)
    cap = budget * rc.get("max_share", 0.4)
    day_share = rc.get("day_share", 0.25)

    buys = []
    for iid, it in items.items():
        p, src = price(iid)
        if not it.get("ok") or p is None or not it.get("buy_max") or p > it["buy_max"]:
            continue
        qty = int(min(max(1, it["sold_per_day"] * day_share), cap // p))
        profit = qty * (it["sell_at"] * (1 - cut) - p)
        if qty and profit > 0:
            buys.append({"id": iid, "name": name(iid), "qty": qty, "price": p, "source": src,
                         "buy_max": it["buy_max"], "sell_at": it["sell_at"], "cost": qty * p, "profit": profit,
                         "history": it})
    buys.sort(key=lambda b: -b["profit"])
    plan, left = [], budget
    for b in buys:
        if b["cost"] <= left:
            plan.append(b)
            left -= b["cost"]

    bw = []
    for iid, b in bwl.items():
        p, src = price(iid)
        if p is None or p > b["buy_max"]:
            continue
        qty = int(min(max(1, b["bw_sold_per_day"] * 0.10), cap // p))
        profit = qty * (b["sell_at"] * (1 - bcut) - p)
        if qty and profit > 0:
            bw.append({"id": iid, "name": name(iid), "qty": qty, "price": p, "source": src, "cost": qty * p,
                       "profit": profit, **b})
    bw.sort(key=lambda b: -b["profit"])

    held = []
    for pos in hold.get("positions", []):
        iid = str(pos["item_id"])
        it = items.get(iid, {})
        p, src = price(iid)
        be = pos["paid_each"] / (1 - cut)
        patient = it.get("p75_72")
        if p and p - 1 >= be:
            verdict = f"sell now: undercut at {coin(p - 1)} for ~{coin(pos['qty'] * ((p - 1) * (1 - cut) - pos['paid_each']))} profit"
        elif patient and patient >= be:
            verdict = (f"below break-even now; list at {coin(patient)} (the price it reached about a quarter of the last 72h) "
                       f"for ~{coin(pos['qty'] * (patient * (1 - cut) - pos['paid_each']))} profit")
        elif it.get("high72"):
            verdict = f"hold: its 72h high ({coin(it['high72'])}) is under your break-even"
        else:
            verdict = "no history yet"
        held.append({"id": iid, "name": pos.get("name") or name(iid), "qty": pos["qty"], "status": pos.get("status"),
                     "paid": pos["paid_each"], "break_even": be, "now": p, "source": src, "verdict": verdict, "history": it})

    high = []
    for iid, it in items.items():
        p, src = price(iid)
        if p and it.get("target") and it["sold_per_day"] >= rc.get("min_sold_per_day", 10) and (it.get("points") or 0) >= 24 \
                and p >= it["target"] * 1.15:
            high.append({"id": iid, "name": name(iid), "price": p, "target": it["target"], "source": src})
    high.sort(key=lambda h: -(h["price"] / h["target"]))

    watch = []
    for iid, it in items.items():
        p, src = price(iid)
        if it.get("ok") and p and it.get("buy_max") and it["buy_max"] < p <= it["buy_max"] * 1.15:
            watch.append({"id": iid, "name": name(iid), "price": p, "buy_max": it["buy_max"], "source": src})
    watch.sort(key=lambda w: w["price"] / w["buy_max"])

    return {"now": now, "snapshot": snap, "history_hours": th.get("history_hours", 0), "scan": scan, "scan_note": why,
            "live_items": len(live), "budget": budget, "plan": plan, "left": left, "blackwater": bw[:5],
            "holdings": held, "high": high[:8], "watch": watch[:8]}


def hist(it):
    if not it or it.get("low72") is None:
        return "no history yet"
    s = f"72h low {coin(it['low72'])} / median {coin(it['med72'])} / high {coin(it['high72'])}"
    if it.get("dips"):
        s += f" · bounced back {it['recovered']}/{it['dips']} dips"
    if it.get("sold_per_day"):
        s += f" · ~{round(it['sold_per_day'])} sold/day"
    return s


def render(a):
    tag = lambda src: " (live)" if src == "live" else " (Blizzard, ~1h old)"
    o = ["# What to do now", ""]
    o.append(f"- Blizzard history: snapshot {uk(a['snapshot']) if a['snapshot'] else '-'} UK, {a['history_hours']} hourly snapshots")
    if a["scan"] and not a["scan_note"]:
        o.append(f"- Your Auctionator scan: {uk(a['scan'])} UK, {a['live_items']} items priced live")
    else:
        o.append(f"- Your Auctionator scan: not used ({a['scan_note']})")
    if a["history_hours"] < 48:
        o.append("- History is still building: buys need two past dips that recovered, so expect few or none yet.")
    o += ["", "## Buy now", ""]
    for n, b in enumerate(a["plan"], 1):
        o += [f"{n}. {b['name']}",
              f"   1. Buy: up to {b['qty']} at {coin(b['buy_max'])} or less (cheapest {coin(b['price'])}{tag(b['source'])})",
              f"   2. Sell at: {coin(b['sell_at'])}",
              f"   3. Profit: ~{coin(b['profit'])}",
              f"   4. History: {hist(b['history'])}"]
    if a["plan"]:
        o += ["", f"Total: {coin(a['budget'] - a['left'])} spent, ~{coin(sum(b['profit'] for b in a['plan']))} profit "
                  f"(budget {coin(a['budget'])}). Check the total the game quotes before you confirm."]
    else:
        o.append("Nothing passes the history rule at today's prices.")
    o += ["", "## Your holdings", ""]
    for h in a["holdings"]:
        o += [f"- **{h['name']}** x{h['qty']}{' (listed)' if h['status'] == 'listed' else ''}: paid {coin(h['paid'])}, "
              f"break-even {coin(h['break_even'])}, now {coin(h['now'])}{tag(h['source']) if h['now'] else ''}",
              f"  - {h['verdict']}", f"  - {hist(h['history'])}"]
    o += ["", "## Blackwater (buy on Horde, sell at Booty Bay / Gadgetzan / Everlook, 15% cut)", ""]
    o += [f"- {b['name']}: buy {b['qty']} at {coin(b['buy_max'])} or less (cheapest {coin(b['price'])}{tag(b['source'])}), "
          f"sell at {coin(b['sell_at'])}, ~{coin(b['profit'])} profit · Blackwater sells ~{round(b['bw_sold_per_day'])}/day"
          for b in a["blackwater"]] or ["(none)"]
    o += ["", "## Sell if you hold (well above usual)", ""]
    o += [f"- {h['name']}: {coin(h['price'])}{tag(h['source'])} vs usual ~{coin(h['target'])}" for h in a["high"]] or ["(none)"]
    o += ["", "## Watch (just above the buy limit)", ""]
    o += [f"- {w['name']}: {coin(w['price'])}{tag(w['source'])}, buy at {coin(w['buy_max'])} or less" for w in a["watch"]] or ["(none)"]
    return "\n".join(o) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.path.dirname(HERE))
    ap.add_argument("--auctionator")
    ap.add_argument("--max-age", type=float, default=2.0, help="hours before an Auctionator scan counts as stale")
    ap.add_argument("--json")
    a = ap.parse_args()
    res = advise(a.repo, a.auctionator, a.max_age)
    print(render(res))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f, indent=1, default=str)


if __name__ == "__main__":
    main()
