# Dashboard

The "WoW Forever flips" dashboard (Claude Data artifact `eb1d1467-ead0-4bdc-be81-3fa8af7d317a`,
https://claude.ai/artifact/W2uSHXduxewWeTC1TjejZj) shows what to buy and sell now, your holdings
against break-even, Blackwater gaps, and daily price ranges from your scans.

- `index.html` is a copy of the page stored in the dashboard (`files/index.html`). The dashboard's
  copy is the live one: if it was edited there, read it back before changing this copy.
- Its data is seven JSON files built by `tools/dashboard_data.py` (status, buys, holdings,
  blackwater, sell_high, watch, daily), each attached to the dataset of the same id.
- `harness.mjs` runs the page against a folder of those files in a headless DOM:
  `npm install jsdom@24 d3@7 && node harness.mjs <data folder> index.html`

To refresh it: build the files (`python tools/dashboard_data.py --auctionator <Auctionator.lua> --out <folder>`),
upload them to the dashboard, and point each dataset's `source.url` at its new file with a new `updated` time.
