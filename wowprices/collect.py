"""Turn raw auction house snapshots into hourly per-item summaries.

Each hourly file (data/<house>/YYYY/MM/DD/HH.csv.gz) has one row per item:
  item_id, min   cheapest buyout per unit (copper)
  p15            quantity-weighted average of the cheapest 15% of units (copper)
  units          units listed with a buyout
  listings       number of auctions with a buyout
  sold_est       units that disappeared since the previous snapshot without
                 expiring (a rough sales estimate: cancels also count)
  new_units      units in auctions that were not there last snapshot
"""
import csv
import gzip
import io
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

# How much of an auction that disappears counts as "sold", by its last time_left.
# SHORT auctions mostly expire; MEDIUM (30 min - 2 h) is a coin flip.
SOLD_WEIGHT = {"SHORT": 0.0, "MEDIUM": 0.5, "LONG": 1.0, "VERY_LONG": 1.0}

FIELDS = ["item_id", "min", "p15", "units", "listings", "sold_est", "new_units"]


def parse_listings(auctions_json, commodities_json):
    """Return [(auction_id, item_id, quantity, unit_price_or_None, time_left)]."""
    out = []
    for src in (auctions_json, commodities_json):
        for a in (src or {}).get("auctions") or []:
            item = a.get("item") or {}
            iid = item.get("id")
            if iid is None or "id" not in a:
                continue
            qty = int(a.get("quantity") or 1)
            if a.get("unit_price"):
                unit = float(a["unit_price"])
            elif a.get("buyout"):
                unit = float(a["buyout"]) / qty
            else:
                unit = None  # bid-only
            out.append((a["id"], int(iid), qty, unit, a.get("time_left")))
    return out


def ladders(listings):
    """item_id -> [(unit_price, qty)] sorted cheapest first (buyout listings only)."""
    lad = {}
    for _, iid, qty, unit, _ in listings:
        if unit is None:
            continue
        lad.setdefault(iid, []).append((unit, qty))
    for v in lad.values():
        v.sort()
    return lad


def summarize(listings, prev_state):
    """Return (rows, new_state). prev_state: {auction_id(str): [item_id, qty, time_left]}."""
    rows = {}
    lad = ladders(listings)
    for iid, L in lad.items():
        units = sum(q for _, q in L)
        take = max(1, round(units * 0.15))
        got = cost = 0
        for u, q in L:
            n = min(q, take - got)
            got += n
            cost += n * u
            if got >= take:
                break
        rows[iid] = {"item_id": iid, "min": round(L[0][0]), "p15": round(cost / got),
                     "units": units, "listings": len(L), "sold_est": None, "new_units": None}

    cur = {str(aid): (iid, qty, tl) for aid, iid, qty, _, tl in listings}
    new_state = {aid: [iid, qty, tl] for aid, (iid, qty, tl) in cur.items()}
    if prev_state:
        sold, new = {}, {}
        for aid, (iid, pq, ptl) in prev_state.items():
            c = cur.get(aid)
            if c:
                if pq > c[1]:
                    sold[iid] = sold.get(iid, 0) + (pq - c[1])
            else:
                sold[iid] = sold.get(iid, 0) + pq * SOLD_WEIGHT.get(ptl, 1.0)
        for aid, (iid, qty, _) in cur.items():
            if aid not in prev_state:
                new[iid] = new.get(iid, 0) + qty
        for iid, r in rows.items():
            r["sold_est"] = round(sold.get(iid, 0), 1)
            r["new_units"] = new.get(iid, 0)
        # items that sold out completely still need a row so their sales count
        for iid, s in sold.items():
            if iid not in rows and s > 0:
                rows[iid] = {"item_id": iid, "min": "", "p15": "", "units": 0, "listings": 0,
                             "sold_est": round(s, 1), "new_units": 0}
    return rows, new_state


def snapshot_time(headers):
    lm = headers.get("Last-Modified") or headers.get("last-modified")
    if lm:
        try:
            return parsedate_to_datetime(lm).astimezone(timezone.utc)
        except (TypeError, ValueError):
            pass
    return datetime.now(timezone.utc)


def hour_path(data_dir, house_key, ts):
    return os.path.join(data_dir, house_key, ts.strftime("%Y"), ts.strftime("%m"),
                        ts.strftime("%d"), ts.strftime("%H") + ".csv.gz")


def write_rows(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS)
    w.writeheader()
    for iid in sorted(rows):
        r = dict(rows[iid])
        for k in ("sold_est", "new_units"):
            if r[k] is None:
                r[k] = ""
        w.writerow(r)
    # mtime=0 keeps the gzip bytes identical for identical content
    with open(path, "wb") as f:
        f.write(gzip.compress(buf.getvalue().encode(), mtime=0))


def read_rows(path):
    with gzip.open(path, "rt", newline="") as f:
        out = {}
        for r in csv.DictReader(f):
            def num(k, cast=float):
                v = r.get(k, "")
                return cast(v) if v not in ("", None) else None
            out[int(r["item_id"])] = {
                "min": num("min"), "p15": num("p15"), "units": num("units", int) or 0,
                "listings": num("listings", int) or 0, "sold_est": num("sold_est"),
                "new_units": num("new_units"),
            }
        return out
