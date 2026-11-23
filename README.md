# WoW Forever prices (US Horde)

An hourly GitHub Actions job that downloads the US WoW: Forever Horde and Blackwater
auction houses from Blizzard's API, keeps a compact price history, and writes a flip
report: what to buy, what to sell, and how your holdings are doing.

**Open the dashboard: https://patrickjin6.github.io/wow-forever-prices/** (updates itself from the hourly data; drop your Auctionator.lua on it for live prices).

The latest text report is [report.md](report.md) (updated every hour at about :17 UTC).

## One-time setup (about 5 minutes)

1. **Make a Blizzard API client.** Sign in at <https://develop.battle.net/access/clients>,
   click **Create Client**, give it any name (for example `wow-forever-prices`), and if it
   asks for a redirect URL, enter `https://localhost`. Copy the **Client ID** and
   **Client Secret**. Keep the secret private.
2. **Add them to this repo.** Go to **Settings → Secrets and variables → Actions →
   New repository secret** and add two secrets:
   - `BLIZZARD_CLIENT_ID`
   - `BLIZZARD_CLIENT_SECRET`
3. **Start it.** Open the **Actions** tab (enable workflows if GitHub asks), pick
   **collect**, and click **Run workflow**. After that it runs on its own every hour.

The first run also writes `discovery.json`, which lists every Horde and Blackwater house
with prices for a few reference items. Blizzard names every house just "Horde Auction
House", so the US PvE house in `config.json` (114) is a best guess until it's checked
against BootyBayBroker. Once it's confirmed, `houses_verified` is set to `true`.

## What's in the repo

| Path | What it is |
| --- | --- |
| `report.md` / `report.json` | The latest flip report |
| `holdings.json` | What you hold, what you paid, gold to spend, and a log of trades |
| `data/<house>/YYYY/MM/DD/HH.csv.gz` | One summary per hour: cheapest price, units listed, estimated sales |
| `items.json` | Item names (looked up from Blizzard as needed) |
| `config.json` | Region, house ids and report settings |
| `run.py`, `wowprices/` | The collector and report code (Python, no dependencies) |
| `tests/simulate.py` | Offline test with a simulated week of data |

## How the report decides

- **Sell price** = the lower of the last 24h and 72h median of the cheapest listing,
  capped at 1c under whatever would still be listed ahead of you after you buy.
- **Buy price** = sell price × 0.95 (AH cut) ÷ 1.10, so every buy clears 10% profit.
- **History rule:** an item only counts as a buy if, in the last 7 days, it dipped to the
  buy price at least twice and bounced back to the sell price within 24 hours at least
  2 out of 3 times, its 7-day trend isn't down more than 10%, and the current dip isn't
  already longer than its slowest past recovery.
- **Blackwater:** buy on Horde, sell at a neutral AH (Booty Bay, Gadgetzan, Everlook) with
  its 15% cut, only where Blackwater's price held steady for 72h and it sells many more a
  day than you'd bring.

Sales per day are estimated from listings that disappear between snapshots. Listings that come back cheaper (relists) and pricier materials that vanish while a cheaper one is still up (cancels) are left out, and each item gets a speed label (fast, normal, slow) and a relist share. Cancels of the cheapest listing still
count as sales. Treat it as a rough guide.

## Live prices from your own Auctionator scans

`tools/auctionator.py` reads Auctionator's saved data
(`World of Warcraft\_forever_\WTF\Account\<account>\SavedVariables\Auctionator.lua`).
Do a full scan in game, type `/reload` so the game writes the file, then:

```
python tools/auctionator.py Auctionator.lua 15417 13465      # price now + daily history
python tools/auctionator.py Auctionator.lua --posts          # what you posted recently
```

It gives the cheapest price for every item at the moment you scanned, plus Auctionator's
daily low/high/units for the last 21 days. It has no dependencies.

## Notes

- This repo is public, so anyone can see the prices and `holdings.json`.
- History builds from the first run. Expect few or no buys for the first few days.
- Data grows by roughly 100 MB a month. If the repo gets large, old hourly files can be
  rolled into daily summaries.
- Blizzard hasn't documented the WoW: Forever API, so endpoints may change. If a run
  fails, the Actions tab shows the error.
