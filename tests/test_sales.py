"""Sales counting: cancels and relists must not count as sales. Run: python tests/test_sales.py"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wowprices.collect import summarize  # noqa: E402
from wowprices.report import relist_share, speed_label  # noqa: E402


def snap(*auctions):
    """auctions: (aid, iid, qty, unit, time_left, pooled)"""
    return list(auctions)


def run():
    # previous hour: material 1 at 10c x5 (a), 12c x5 (b), 20c x5 (c); gear 2 at 500c (d), 900c (e)
    prev = snap((1, 1, 5, 10, "LONG", True), (2, 1, 5, 12, "LONG", True), (3, 1, 5, 20, "LONG", True),
                (4, 2, 1, 500, "LONG", False), (5, 2, 1, 900, "LONG", False))
    _, state = summarize(prev, None)
    # now: a bought out, b partly bought (5 -> 2), c gone while b is still up (cancel),
    # d gone (sold), e gone and back at 800 with the same quantity (relist)
    cur = snap((2, 1, 2, 12, "LONG", True), (6, 2, 1, 800, "VERY_LONG", False))
    rows, _ = summarize(cur, state)
    checks = {
        "material: bought-out and partial buys count, pricier cancel doesn't": rows[1]["sold_est"] == 5 + 3,
        "gear: sold one counts, relisted one doesn't": rows[2]["sold_est"] == 1 and rows[2]["relisted"] == 1,
        "old state without prices still works": summarize(cur, {"1": [1, 5, "LONG"]})[0][1]["sold_est"] == 5,
    }
    now = datetime(2026, 11, 24, tzinfo=timezone.utc)
    pts = [(now - timedelta(hours=h), {"sold_est": 3, "relisted": 1}) for h in range(10)]
    checks["relist share"] = abs(relist_share(pts, now) - 0.25) < 1e-9
    checks["speed labels"] = [speed_label(x) for x in (0.2, 1, 3, None)] == ["fast", "normal", "slow", None]
    bad = [k for k, ok in checks.items() if not ok]
    for k, ok in checks.items():
        print("PASS" if ok else "FAIL", k)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    run()
