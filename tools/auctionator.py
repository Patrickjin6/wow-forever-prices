"""Read Auctionator's saved scan data (WTF/Account/<account>/SavedVariables/Auctionator.lua).

Auctionator (price database version 8) stores each realm/faction as a CBOR blob inside a
Lua string. For every item it keeps:
  m  latest cheapest price seen (copper)
  l  {day: lowest price that day}     h  {day: highest price that day}
  a  {day: units available}           day = days since 2020-01-01

The game rewrites the file on /reload or logout, so scan, type /reload, then run:
  python tools/auctionator.py path/to/Auctionator.lua 15417 13465 ...   # items
  python tools/auctionator.py path/to/Auctionator.lua --posts           # your recent posts
  python tools/auctionator.py path/to/Auctionator.lua --json out.json   # whole database
No dependencies.
"""
import json
import re
import struct
import sys
from datetime import datetime, timedelta, timezone

DAY0 = datetime(2020, 1, 1, tzinfo=timezone.utc)


# ------------------------------------------------------------------ Lua + CBOR

def lua_string(data, i):
    """Decode the Lua "..." literal starting at data[i]. Returns (bytes, index after it)."""
    assert data[i] == 34
    out, i = bytearray(), i + 1
    simple = {110: 10, 114: 13, 116: 9, 97: 7, 98: 8, 102: 12, 118: 11, 92: 92, 34: 34, 39: 39, 10: 10, 13: 13}
    while True:
        c = data[i]
        if c == 34:
            return bytes(out), i + 1
        if c == 92:
            n = data[i + 1]
            if 48 <= n <= 57:
                j = i + 1
                while j < len(data) and j < i + 4 and 48 <= data[j] <= 57:
                    j += 1
                out.append(int(data[i + 1:j]))
                i = j
                continue
            if n in (10, 13):  # backslash + line break (LF or Windows CRLF) = newline byte
                out.append(10)
                i += 2
                if n == 13 and data[i:i + 1] == b"\n":
                    i += 1
                continue
            out.append(simple[n])
            i += 2
            continue
        out.append(c)
        i += 1


def cbor(buf, i=0):
    """Minimal CBOR decoder. Returns (value, next index)."""
    ib = buf[i]
    mt, ai = ib >> 5, ib & 31
    i += 1
    if ai < 24:
        n = ai
    elif ai == 24:
        n, i = buf[i], i + 1
    elif ai == 25:
        n, i = struct.unpack(">H", buf[i:i + 2])[0], i + 2
    elif ai == 26:
        n, i = struct.unpack(">I", buf[i:i + 4])[0], i + 4
    elif ai == 27:
        n, i = struct.unpack(">Q", buf[i:i + 8])[0], i + 8
    else:
        n = None  # indefinite length
    if mt == 0:
        return n, i
    if mt == 1:
        return -1 - n, i
    if mt in (2, 3):
        if n is None:
            parts = []
            while buf[i] != 0xFF:
                v, i = cbor(buf, i)
                parts.append(v if isinstance(v, bytes) else v.encode())
            return b"".join(parts), i + 1
        raw = bytes(buf[i:i + n])
        return (raw if mt == 2 else raw.decode("utf-8", "replace")), i + n
    if mt == 4:
        out = []
        while (n is None and buf[i] != 0xFF) or (n is not None and len(out) < n):
            v, i = cbor(buf, i)
            out.append(v)
        return out, (i + 1 if n is None else i)
    if mt == 5:
        out, k = {}, 0
        while (n is None and buf[i] != 0xFF) or (n is not None and k < n):
            key, i = cbor(buf, i)
            val, i = cbor(buf, i)
            out[norm(key)] = val
            k += 1
        return out, (i + 1 if n is None else i)
    if mt == 6:
        return cbor(buf, i)  # ignore tags
    if mt == 7:
        if ai == 20:
            return False, i
        if ai == 21:
            return True, i
        if ai in (22, 23):
            return None, i
        if ai == 25:
            h = struct.unpack(">H", buf[i - 2:i])[0]
            return float(struct.unpack(">e", struct.pack(">H", h))[0]), i
        if ai == 26:
            return struct.unpack(">f", buf[i - 4:i])[0], i
        if ai == 27:
            return struct.unpack(">d", buf[i - 8:i])[0], i
    raise ValueError(f"unsupported CBOR byte {ib:#x}")


def norm(v):
    if isinstance(v, bytes):
        s = v.decode("utf-8", "replace")
        return int(s) if re.fullmatch(r"-?\d+", s) else s
    if isinstance(v, dict):
        return {norm(k): norm(x) for k, x in v.items()}
    if isinstance(v, list):
        return [norm(x) for x in v]
    return v


# ------------------------------------------------------------------ reading

def load(path):
    """Return {"databases": {name: {item_key: {...}}}, "last_scan": datetime, "posts": [...]}"""
    data = open(path, "rb").read()
    start = data.index(b"AUCTIONATOR_PRICE_DATABASE = {")
    end = re.compile(rb"\r?\n\}\r?\n").search(data, start).start()
    seg = data[start:end]
    dbs = {}
    for m in re.finditer(rb'\["([^"]+)"\] = "', seg):
        raw, _ = lua_string(seg, m.end() - 1)
        val, _ = cbor(raw)
        dbs[m.group(1).decode()] = {str(k): v for k, v in norm(val).items() if isinstance(v, dict)}
    ts = re.search(rb'TimeOfLastBrowseScan"\] = (\d+)', data)
    text = data.decode("utf-8", "replace").replace("\r\n", "\n")
    posts = []
    if "AUCTIONATOR_POSTING_HISTORY = {" in text:
        a = text.index("AUCTIONATOR_POSTING_HISTORY = {")
        b = text.find("\n}\n", a)
        for im in re.finditer(r'\["([^"]+)"\] = \{(.*?)\n\},', text[a:b + 3], re.S):
            for p in re.finditer(r'\["price"\] = (\d+),\s*\["quantity"\] = (\d+),\s*\["time"\] = (\d+)', im.group(2)):
                posts.append({"item": im.group(1), "price": int(p.group(1)), "qty": int(p.group(2)),
                              "time": datetime.fromtimestamp(int(p.group(3)), timezone.utc)})
    return {"databases": dbs, "posts": sorted(posts, key=lambda p: p["time"]),
            "last_scan": datetime.fromtimestamp(int(ts.group(1)), timezone.utc) if ts else None}


def day_date(day):
    return (DAY0 + timedelta(days=int(day))).date()


def coin(v):
    if v is None:
        return "-"
    v = int(round(v))
    g, s, c = v // 10000, v % 10000 // 100, v % 100
    return " ".join(x for x in (f"{g}g" if g else "", f"{s}s" if g or s else "", f"{c}c") if x)


def describe(entry, days=5):
    h, l, a = entry.get("h") or {}, entry.get("l") or {}, entry.get("a") or {}
    if isinstance(l, list):
        l = {}
    ds = sorted(set(h) | set(l), key=int)[-days:]
    parts = []
    for d in ds:
        lo, hi = l.get(d, h.get(d)), h.get(d)
        rng = coin(hi) if lo in (None, hi) else f"{coin(lo)}-{coin(hi)}"
        parts.append(f"{day_date(d):%d %b} {rng} ({a.get(d, '?')} listed)")
    return f"now {coin(entry.get('m'))} | " + " | ".join(parts)


def main(argv):
    path, args = argv[1], argv[2:]
    d = load(path)
    print("last full scan:", d["last_scan"], "| databases:", {k: len(v) for k, v in d["databases"].items()})
    if "--json" in args:
        out = args[args.index("--json") + 1]
        json.dump(d["databases"], open(out, "w"))
        print("wrote", out)
    if "--posts" in args:
        for p in d["posts"][-30:]:
            print(f"{p['time']:%d %b %H:%M} item {p['item']} x{p['qty']} @ {coin(p['price'])}")
    ids = [a for a in args if re.fullmatch(r"\d+", a) and (not args.count("--json") or a != args[args.index('--json') + 1])]
    for name, db in d["databases"].items():
        for iid in ids:
            keys = [k for k in db if k == iid or k.startswith(iid + ":") or k.startswith("gr:" + iid + ":")]
            for k in keys[:3]:
                print(f"[{name}] {k}: {describe(db[k])}")
            if not keys:
                print(f"[{name}] {iid}: not in scan data")


if __name__ == "__main__":
    main(sys.argv)
