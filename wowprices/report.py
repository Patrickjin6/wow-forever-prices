"""Build the flip report (report.md and report.json) from hourly history + live ladders.

Rules match the BootyBayBroker flip-scan skill:
  target    = lower of the 24h and 72h median of the cheapest listing
  resale    = lower of target and 1c under the "wall" left after your purchase
              (cheapest price where remaining units exceed half a day of sales)
  threshold = resale x (1 - cut) / (1 + min_margin)
  A buy needs proof the price comes back: at least 2 dips to the buy price in the
  window, at least 2 of 3 recovering to the sell price within 24h, a 7-day trend
  not down more than 10%, and a current dip no longer than the slowest recovery.
"""
import json
import math
import os
import statistics
from datetime import timedelta

from .collect import hour_path, read_rows

try:
    from zoneinfo import ZoneInfo
    UK = ZoneInfo("Europe/London")
except Exception:  # pragma: no cover
    UK = None

RISK_FLAGS = ("falling-24h", "thin-history", "glut", "no-recovery")


def coin(v):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "-"
    neg = v < 0
    v = abs(int(round(v)))
    g, s, c = v // 10000, v % 10000 // 100, v % 100
    parts = []
    if g:
        parts.append(f"{g}g")
    if g or s:
        parts.append(f"{s}s")
    parts.append(f"{c}c")
    return ("-" if neg else "") + " ".join(parts)


def uk(ts):
    t = ts.astimezone(UK) if UK else ts
    return t.strftime("%a %d %b %H:%M")


def med(a):
    a = [x for x in a if x is not None]
    return statistics.median(a) if a else None


def pct(a, p):
    a = sorted(x for x in a if x is not None)
    if not a:
        return None
    return a[min(len(a) - 1, round(p * (len(a) - 1)))]


# ---------------------------------------------------------------- history

def load_history(data_dir, house_key, now, days):
    """Return (series, files): series[item_id] = [(ts, row)], files = [(ts, had_sales_data)]."""
    series, files = {}, []
    start = now - timedelta(days=days)
    t = start.replace(minute=0, second=0, microsecond=0)
    while t <= now:
        p = hour_path(data_dir, house_key, t)
        if os.path.exists(p):
            rows = read_rows(p)
            had = any(r["sold_est"] is not None for r in rows.values())
            files.append((t, had))
            for iid, r in rows.items():
                series.setdefault(iid, []).append((t, r))
        t += timedelta(hours=1)
    return series, files


def covered_hours(files, now, hours=24):
    """Hours of sales data in the last `hours` (each file covers the gap since the previous one)."""
    cut = now - timedelta(hours=hours)
    total, prev = 0.0, None
    for ts, had in files:
        if ts > cut and had:
            gap = (ts - prev).total_seconds() / 3600 if prev else 1.0
            total += min(gap, 3.0)
        prev = ts
    return total


def sold_per_day(points, now, cov):
    cut = now - timedelta(hours=24)
    s = [r["sold_est"] for ts, r in points if ts > cut and r["sold_est"] is not None]
    if not s or cov < 3:
        return None
    return sum(s) * 24 / cov


# ---------------------------------------------------------------- per item

def plan_buy(ladder, thr, max_units, cap):
    qty = cost = avail = 0
    rem = [[u, q] for u, q in ladder]
    for x in rem:
        if x[0] > thr:
            break
        avail += x[1]
        n = max(0, min(x[1], max_units - qty, int((cap - cost) // x[0]) if x[0] > 0 else 0))
        qty += n
        cost += n * x[0]
        x[1] -= n
    return qty, cost, avail, [x for x in rem if x[1] > 0]


def wall(rem, need):
    cum = 0
    for u, q in rem:
        cum += q
        if cum >= need:
            return u
    return math.inf


def history_line(pts, now):
    """72h low / median / high plus the last 24h every 3h."""
    l72 = [m for ts, m in pts if ts > now - timedelta(hours=72)]
    l24 = [(ts, m) for ts, m in pts if ts > now - timedelta(hours=24)]
    every3 = [coin(m) for i, (ts, m) in enumerate(reversed(l24)) if i % 3 == 0][::-1]
    if not l72:
        return "no history yet"
    return (f"72h low {coin(min(l72))} | median {coin(med(l72))} | high {coin(max(l72))}"
            f" | last 24h (every 3h): {', '.join(every3) if every3 else '-'}")


def analyse(iid, points, ladder, now, cov, cfg, budget):
    rc = cfg["report"]
    cut, margin = rc["cut"], rc["min_margin"]
    pts = [(ts, r["min"]) for ts, r in points if r["min"] is not None]
    mins = [m for _, m in pts]
    last24 = [m for ts, m in pts if ts > now - timedelta(hours=24)]
    prev24 = [m for ts, m in pts if now - timedelta(hours=48) < ts <= now - timedelta(hours=24)]
    last72 = [m for ts, m in pts if ts > now - timedelta(hours=72)]
    it = {"id": iid, "points": len(pts), "sold_per_day": sold_per_day(points, now, cov)}
    it["med24"], it["med72"] = med(last24), med(last72)
    it["target"] = min(x for x in (it["med24"], it["med72"]) if x is not None) if (it["med24"] or it["med72"]) else None
    it["trend24"] = (it["med24"] / med(prev24) - 1) if prev24 and it["med24"] else None
    span_h = (pts[-1][0] - pts[0][0]).total_seconds() / 3600 if len(pts) > 1 else 0
    first24 = [m for ts, m in pts if ts < pts[0][0] + timedelta(hours=24)] if pts else []
    it["trend7"] = (it["med24"] / med(first24) - 1) if span_h >= 120 and it["med24"] else None
    L = ladder or []
    it["cheapest"] = L[0][0] if L else None
    it["listed"] = sum(q for _, q in L)
    it["history"] = history_line(pts, now)
    sold = it["sold_per_day"] or 0
    it["supply_days"] = it["listed"] / sold if sold else None
    if it["target"] is None or not L:
        it.update(threshold=None, resale=None, buy_qty=0, buy_cost=0, profit=0, flags=["thin-history"])
        return it

    cap = budget * rc["max_share"]
    max_units = max(1, int(sold * rc["day_share"])) if sold else 20
    need = max(1, sold * 0.5)
    thr = math.floor(it["target"] * (1 - cut) / (1 + margin))
    q, c, a, rem = plan_buy(L, thr, max_units, cap)
    resale = min(it["target"], wall(rem, need) - 1)
    thr = math.floor(resale * (1 - cut) / (1 + margin))
    q, c, a, rem = plan_buy(L, thr, max_units, cap)
    resale = min(it["target"], wall(rem, need) - 1)
    it.update(threshold=thr, resale=resale, buy_qty=q, buy_cost=c, avail_under=a,
              profit=q * resale * (1 - cut) - c)
    ahead = sum(qq for u, qq in rem if u < resale)
    it["hours_to_sell"] = (ahead + q) / sold * 24 if sold else None

    # Patrick's rule: only a buy if the price has bounced back from similar dips.
    cur_start = None
    for ts, m in reversed(pts):
        if m <= thr * 1.05:
            cur_start = ts
        else:
            break
    cur_dip_h = (now - cur_start).total_seconds() / 3600 + 1 if cur_start else 0
    body = [(ts, m) for ts, m in pts if cur_start is None or ts < cur_start]
    dips = recovered = 0
    longest = 0.0
    for j, (ts, m) in enumerate(body):
        if m <= thr and (j == 0 or body[j - 1][1] > thr):
            dips += 1
            for ts2, m2 in body[j + 1:]:
                h = (ts2 - ts).total_seconds() / 3600
                if h > 24:
                    break
                if m2 >= resale:
                    recovered += 1
                    longest = max(longest, h)
                    break
    it.update(dips=dips, recovered=recovered, recovery_hours=longest, current_dip_hours=cur_dip_h)
    it["will_recover"] = (dips >= 2 and recovered / dips >= 0.67
                          and not (it["trend7"] is not None and it["trend7"] < -0.10)
                          and cur_dip_h <= max(longest, 3))
    it["flags"] = [f for f, on in (
        ("falling-24h", it["trend24"] is not None and it["trend24"] < -0.15),
        ("thin-history", len(pts) < 48),
        ("glut", it["supply_days"] is not None and it["supply_days"] > 2),
        ("no-recovery", not it["will_recover"]),
        ("slow", sold < rc["min_sold_per_day"]),
    ) if on]
    return it


# ---------------------------------------------------------------- report

def build(cfg, data_dir, now, ladders_by_house, holdings, name_of, out_dir="."):
    rc = cfg["report"]
    days = rc["history_days"]
    budget = int(holdings.get("cash_copper") or 50000)
    hist = {}
    for key in ladders_by_house:
        series, files = load_history(data_dir, key, now, days)
        hist[key] = (series, files, covered_hours(files, now))
    hseries, hfiles, hcov = hist["horde"]
    hlad = ladders_by_house["horde"]
    history_hours = len(hfiles)

    items = {}
    for iid in set(hlad) | set(hseries):
        items[iid] = analyse(iid, hseries.get(iid, []), hlad.get(iid), now, hcov, cfg, budget)

    ok = lambda it: (it.get("resale") is not None and (it["sold_per_day"] or 0) >= rc["min_sold_per_day"]
                     and not any(f in RISK_FLAGS for f in it["flags"]))
    buys = sorted((it for it in items.values() if ok(it) and it["buy_qty"] > 0 and it["profit"] > 0),
                  key=lambda it: -it["profit"])
    plan, left = [], budget
    for it in buys:
        if it["buy_cost"] <= left:
            plan.append(it)
            left -= it["buy_cost"]
    risky = sorted((it for it in items.values() if not ok(it) and it.get("buy_qty", 0) > 0 and it["profit"] > 0
                    and (it["sold_per_day"] or 0) >= rc["min_sold_per_day"]), key=lambda it: -it["profit"])[:6]
    watch = sorted((it for it in items.values() if ok(it) and it["buy_qty"] == 0 and it["cheapest"]
                    and it["threshold"] and it["cheapest"] <= it["threshold"] * 1.15),
                   key=lambda it: it["cheapest"] / it["threshold"])[:8]
    high = sorted((it for it in items.values() if it.get("target") and it.get("cheapest")
                   and it["cheapest"] >= it["target"] * 1.15 and (it["sold_per_day"] or 0) >= rc["min_sold_per_day"]
                   and it["points"] >= 24),
                  key=lambda it: -(it["cheapest"] / it["target"]))[:10]

    # Holdings
    cut = rc["cut"]
    held = []
    for pos in holdings.get("positions", []):
        iid = int(pos["item_id"])
        it = items.get(iid) or analyse(iid, hseries.get(iid, []), hlad.get(iid), now, hcov, cfg, budget)
        be = pos["paid_each"] / (1 - cut)
        pts72 = [r["min"] for ts, r in hseries.get(iid, []) if r["min"] is not None and ts > now - timedelta(hours=72)]
        patient = pct(pts72, 0.75)
        now_p = it.get("cheapest")
        undercut = now_p - 1 if now_p else None
        hit = (sum(1 for m in pts72 if patient and m >= patient) / len(pts72)) if pts72 and patient else None
        if undercut and undercut >= be:
            verdict = f"Above break-even now: undercut at {coin(undercut)} for ~{coin(pos['qty'] * (undercut * (1 - cut) - pos['paid_each']))} profit"
        elif patient and patient >= be:
            verdict = f"Below break-even now; list at {coin(patient)} (reached about {round((hit or 0) * 100)}% of the last 72h) for ~{coin(pos['qty'] * (patient * (1 - cut) - pos['paid_each']))} profit"
        elif pts72:
            verdict = f"Hold: under break-even for most of the last 72h (high {coin(max(pts72))})"
        else:
            verdict = "No history yet"
        held.append({"item_id": iid, "name": pos.get("name") or name_of(iid), "qty": pos["qty"],
                     "paid_each": pos["paid_each"], "break_even": be, "now": now_p,
                     "sold_per_day": it.get("sold_per_day"), "patient": patient,
                     "history": it.get("history"), "verdict": verdict})

    # Blackwater (neutral AH, 15% cut): buy on Horde, sell there.
    bw = []
    if rc.get("blackwater", True) and "blackwater" in ladders_by_house:
        bseries, bfiles, bcov = hist["blackwater"]
        blad = ladders_by_house["blackwater"]
        bcut = rc["blackwater_cut"]
        for iid, L in blad.items():
            pts = [r["min"] for ts, r in bseries.get(iid, []) if r["min"] is not None]
            if len(pts) < 24 or iid not in hlad:
                continue
            bsold = sold_per_day(bseries.get(iid, []), now, bcov) or 0
            if bsold < rc["min_sold_per_day"]:
                continue
            l24 = [r["min"] for ts, r in bseries[iid] if r["min"] is not None and ts > now - timedelta(hours=24)]
            l72 = [r["min"] for ts, r in bseries[iid] if r["min"] is not None and ts > now - timedelta(hours=72)]
            target = min(med(l24) or math.inf, med(l72) or math.inf)
            resale = min(target, wall(L, max(1, bsold * 0.5)) - 1)
            if not math.isfinite(resale) or min(l72) < resale * 0.85:
                continue  # Blackwater price not stable enough over 72h
            thr = math.floor(resale * (1 - bcut) / (1 + rc["min_margin"]))
            max_units = max(1, int(bsold * 0.10))  # bring at most a tenth of a day's Blackwater sales
            q, c, a, _ = plan_buy(hlad[iid], thr, max_units, budget * rc["max_share"])
            profit = q * resale * (1 - bcut) - c
            if q and profit > 0:
                bw.append({"id": iid, "buy_qty": q, "buy_cost": c, "threshold": thr, "horde_cheapest": hlad[iid][0][0],
                           "resale": resale, "profit": profit, "bw_sold_per_day": bsold,
                           "bw_listed": sum(x[1] for x in L),
                           "history": history_line([(ts, r["min"]) for ts, r in bseries[iid] if r["min"] is not None], now)})
        bw.sort(key=lambda x: -x["profit"])
    bw_all = list(bw)
    bw = bw[:5]

    # Names for everything shown
    for it in plan + risky + watch + high + bw:
        it["name"] = name_of(it["id"])

    write_thresholds(out_dir, cfg, now, history_hours, items, hseries, holdings, bw_all, name_of)

    md = render(cfg, now, history_hours, budget, plan, left, risky, watch, high, held, bw)
    with open(os.path.join(out_dir, "report.md"), "w") as f:
        f.write(md)
    slim = lambda xs: [{k: v for k, v in x.items() if not isinstance(v, float) or math.isfinite(v)} for x in xs]
    with open(os.path.join(out_dir, "report.json"), "w") as f:
        json.dump({"snapshot_utc": now.isoformat(), "history_hours": history_hours, "budget": budget,
                   "houses": cfg["houses"], "houses_verified": cfg.get("houses_verified"),
                   "buy_now": slim(plan), "risky": slim(risky), "watch": slim(watch),
                   "high_now": slim(high), "holdings": slim(held), "blackwater": slim(bw)},
                  f, indent=1, default=str)
    return md


def write_thresholds(out_dir, cfg, now, history_hours, items, hseries, holdings, bw_all, name_of):
    """Per-item limits so a live scan (Auctionator) can be checked against history without re-running."""
    rc = cfg["report"]
    held = {int(p["item_id"]) for p in holdings.get("positions", [])}
    out = {}
    for iid, it in items.items():
        sold = it.get("sold_per_day") or 0
        if iid not in held and (sold < rc["min_sold_per_day"] or it.get("resale") is None):
            continue
        l72 = [r["min"] for ts, r in hseries.get(iid, []) if r["min"] is not None and ts > now - timedelta(hours=72)]
        rnd = lambda v: None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(v)
        out[str(iid)] = {
            "target": rnd(it.get("target")), "buy_max": rnd(it.get("threshold")), "sell_at": rnd(it.get("resale")),
            "sold_per_day": round(sold, 1), "listed": it.get("listed"), "cheapest": rnd(it.get("cheapest")),
            "avail_under": it.get("avail_under"),
            "dips": it.get("dips"), "recovered": it.get("recovered"),
            "recovery_hours": rnd(it.get("recovery_hours")), "flags": it.get("flags", []),
            "ok": not any(f in RISK_FLAGS for f in it.get("flags", [])) and sold >= rc["min_sold_per_day"],
            "low72": rnd(min(l72)) if l72 else None, "med72": rnd(med(l72)) if l72 else None,
            "high72": rnd(max(l72)) if l72 else None, "p75_72": rnd(pct(l72, 0.75)) if l72 else None,
            "points": it.get("points"),
        }
    bw = {str(b["id"]): {"buy_max": round(b["threshold"]), "sell_at": round(b["resale"]),
                         "bw_sold_per_day": round(b["bw_sold_per_day"], 1), "bw_listed": b["bw_listed"]}
          for b in bw_all}
    # names for anything that could be recommended (cached names are free; new lookups are capped per run)
    for k, v in sorted(out.items(), key=lambda kv: -(kv[1]["sold_per_day"] or 0)):
        if v["ok"] or int(k) in held:
            v["name"] = name_of(int(k))
    for k in bw:
        bw[k]["name"] = name_of(int(k))
    with open(os.path.join(out_dir, "thresholds.json"), "w") as f:
        json.dump({"snapshot_utc": now.isoformat(), "history_hours": history_hours, "cut": rc["cut"],
                   "blackwater_cut": rc["blackwater_cut"], "min_margin": rc["min_margin"],
                   "items": out, "blackwater": bw}, f, separators=(",", ":"))

    # history.json: last 72 hourly cheapest prices for the items a page might chart
    keep = {str(i) for i in held} | set(bw)
    keep |= {k for k, v in sorted(out.items(), key=lambda kv: -(kv[1]["sold_per_day"] or 0)) if v["ok"]}
    hours = [(now - timedelta(hours=h)).replace(minute=0, second=0, microsecond=0) for h in range(71, -1, -1)]
    series = {}
    for k in keep:
        by_hour = {ts.replace(minute=0, second=0, microsecond=0): r["min"] for ts, r in hseries.get(int(k), [])}
        vals = [None if by_hour.get(h) is None else round(by_hour[h]) for h in hours]
        if any(v is not None for v in vals):
            series[k] = vals
    with open(os.path.join(out_dir, "history.json"), "w") as f:
        json.dump({"start_utc": hours[0].isoformat(), "step_hours": 1, "items": series}, f, separators=(",", ":"))


def render(cfg, now, history_hours, budget, plan, left, risky, watch, high, held, bw):
    o = [f"# WoW Forever flip report (US Horde)",
         "",
         f"Snapshot: **{uk(now)} UK** · history: {history_hours} hourly snapshots · budget {coin(budget)}",
         f"Houses: Horde {cfg['houses'].get('horde')}, Blackwater {cfg['houses'].get('blackwater')}"
         + ("" if cfg.get("houses_verified") else " (**not yet verified**)"),
         ""]
    if history_hours < 48:
        o += ["> History is still building. Buys need at least two past dips that recovered,",
              "> so expect few or no buys until there are a few days of data.", ""]
    o += ["## Buy now", ""]
    if plan:
        for n, it in enumerate(plan, 1):
            o += [f"{n}. {it['name']}",
                  f"   1. Buy: up to {it['buy_qty']} at {coin(it['threshold'])} or less (cheapest now {coin(it['cheapest'])})",
                  f"   2. Sell at: {coin(it['resale'])}",
                  f"   3. Profit: ~{coin(it['profit'])}",
                  f"   - History: {it['history']} · bounced back {it['recovered']}/{it['dips']} dips · ~{round(it['sold_per_day'] or 0)} sold/day"]
        o += ["", f"Total: {coin(budget - left)} spent, ~{coin(sum(i['profit'] for i in plan))} profit.", ""]
    else:
        o += ["Nothing passes the history rule right now.", ""]
    o += ["## Your holdings", ""]
    for h in held:
        o += [f"- **{h['name']}** x{h['qty']} · paid {coin(h['paid_each'])} · break-even {coin(h['break_even'])} · now {coin(h['now'])}",
              f"  - {h['verdict']}",
              f"  - History: {h['history']}"]
    if not held:
        o += ["(none recorded in holdings.json)"]
    o += ["", "## Watch (close to buy price)", ""]
    o += [f"- {it['name']}: now {coin(it['cheapest'])}, buy at {coin(it['threshold'])} or less · {it['history']}" for it in watch] or ["(none)"]
    o += ["", "## High now (sell if you hold)", ""]
    o += [f"- {it['name']}: {coin(it['cheapest'])} vs usual ~{coin(it['target'])}" for it in high] or ["(none)"]
    o += ["", "## Blackwater (buy on Horde, sell at a neutral AH, 15% cut)", ""]
    o += [f"- {b['name']}: buy {b['buy_qty']} at {coin(b['threshold'])} or less on Horde (now {coin(b['horde_cheapest'])}), "
          f"sell at {coin(b['resale'])} on Blackwater, ~{coin(b['profit'])} profit · Blackwater sells ~{round(b['bw_sold_per_day'])}/day, "
          f"{b['bw_listed']} listed · {b['history']}" for b in bw] or ["(none)"]
    o += ["", "## Risky (profitable on paper, don't buy)", ""]
    o += [f"- {it['name']}: now {coin(it['cheapest'])} · flags {', '.join(it['flags'])}" for it in risky] or ["(none)"]
    o += ["", "_Sales per day are estimated from listings that disappear between hourly snapshots, so cancels count as sales. Confirm prices in game._", ""]
    return "\n".join(o)
