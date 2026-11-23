// WoW Forever AH flip scanner — runs inside a bootybaybroker.com tab (same-origin API calls).
// Market: US PvE (realmId 438096), Horde auction house (auctionHouseId 6).
// Fallback for the GitHub/Auctionator path: BootyBayBroker already has ~7 days of history and
// sales-per-day estimates, so use it for buy decisions until the Blizzard history is a few days old.
// Usage: open https://bootybaybroker.com/forever/deals?region=us, set budget below (copper), run this
// with javascript_tool (returns 'started'), then poll:  window.__flipOut || window.__flipErr || 'running'
// Full results stay in window.__flip. At most one full scan per hour (429s after ~3/hour).
window.__flipOut = null; window.__flipErr = null;
window.__flipRun = (async () => {
const CFG = {
  realmId: 438096,      // US PvE connected realm on BootyBayBroker
  ah: 6,                // 6 = Horde, 2 = Alliance, 7 = Blackwater (neutral)
  budget: 50000,        // copper you can spend (50000 = 5g)
  maxShare: 0.4,        // max share of budget in one item
  cut: 0.05,            // AH cut on sale
  minMargin: 0.10,      // profit after cut, relative to buy price, needed to call it a buy
  minSoldPerDay: 10,    // skip items that sell less than this per day
  maxDealCandidates: 120,
  dealMinSold: 1,       // deals API filter: 1 | 10 | 100
  dealPages: 10,        // pages of 100 deals to read
  dayShare: 0.25,       // never buy more than this share of one day's sales
  staples: [2589,2592,4306,4338,14047,2770,2771,2772,3858,10620,12359,12363,2318,2319,4234,4304,8170,
            2447,765,2450,785,3356,3818,3821,3358,8838,13463,13465,8846,7068,7078,16204,10940,2452],
};

async function flipScan(cfg) {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const getJSON = async (url) => {
    for (let t = 0; t < 4; t++) {
      try { const r = await fetch(url); if (r.ok) return await r.json(); } catch (e) {}
      await sleep(700 * (t + 1));
    }
    return null;
  };
  const med = a => { a = a.filter(Number.isFinite).sort((x, y) => x - y); return a.length ? a[Math.floor(a.length / 2)] : NaN; };
  const pct = (a, p) => { a = a.filter(Number.isFinite).sort((x, y) => x - y); return a.length ? a[Math.min(a.length - 1, Math.round(p * (a.length - 1)))] : NaN; };
  const coin = v => { if (!Number.isFinite(v)) return '-'; v = Math.round(v); const g = Math.floor(v / 10000), s = Math.floor(v % 10000 / 100), c = v % 100;
    return (g ? g + 'g ' : '') + (g || s ? s + 's ' : '') + c + 'c'; };
  const base = `region=us&realmId=${cfg.realmId}&auctionHouseId=${cfg.ah}`;

  // 1. Deals universe (items listed >=15% under their 7-day reference, selling 10+/day)
  const deals = []; let vendorFlips = [];
  for (let page = 1; page <= cfg.dealPages; page++) {
    const j = await getJSON(`/api/forever/deals?${base}&under=15&minSold=${cfg.dealMinSold}&sold=1&limit=100&page=${page}`);
    if (!j) break;
    deals.push(...j.rows);
    if (page === 1) vendorFlips = j.vendorFlips || [];
    if (!j.pagination || !j.pagination.hasMore) break;
    await sleep(300);
  }
  const sold = r => r.sold && r.sold.soldPerDay ? r.sold.soldPerDay.value : NaN;
  const cands = new Map();
  deals.filter(r => !r.supplySurge && sold(r) >= cfg.minSoldPerDay && r.minBuyout <= cfg.budget * cfg.maxShare)
    .map(r => ({ r, score: (r.reference * (1 - cfg.cut) - r.minBuyout) * Math.min(sold(r) * cfg.dayShare, Math.floor(cfg.budget * cfg.maxShare / r.minBuyout)) }))
    .sort((a, b) => b.score - a.score).slice(0, cfg.maxDealCandidates)
    .forEach(({ r }) => cands.set(r.itemId, { id: r.itemId, name: r.name, soldPerDay: sold(r), soldLow: r.sold.soldPerDay.low,
      speed: r.sold.speed && r.sold.speed.value, relist: r.sold.relistShare && r.sold.relistShare.value, ref7d: r.reference, from: 'deals' }));

  // 2. Staples (always checked), sales/day from the market endpoint
  for (const id of cfg.staples) {
    if (cands.has(id)) continue;
    cands.set(id, { id, from: 'staple' });
  }

  // 3. Per-item: history, current listings, (staples) sales estimate
  const items = [...cands.values()];
  for (let i = 0; i < items.length; i += 3) {
    await Promise.all(items.slice(i, i + 3).map(async it => {
      const [hist, cur, mkt] = await Promise.all([
        getJSON(`/api/prices/history?itemId=${it.id}&realmId=${cfg.realmId}&gameVersion=forever&days=7&source=all&region=us&auctionHouseId=${cfg.ah}`),
        getJSON(`/api/prices/current?itemId=${it.id}&realmId=${cfg.realmId}&gameVersion=forever&region=us&limit=200&auctionHouseId=${cfg.ah}`),
        it.from === 'staple' ? getJSON(`/api/items/${it.id}/market?gameVersion=forever&region=us&realm=${cfg.realmId}`) : null,
      ]);
      if (mkt) {
        const h = (mkt.houses || []).find(x => x.auctionHouseId === cfg.ah) || {};
        const e = h.estimated || {};
        it.soldPerDay = e.soldPerDay && e.soldPerDay.value; it.soldLow = e.soldPerDay && e.soldPerDay.low;
        it.speed = e.speed && e.speed.value; it.relist = e.relistShare && e.relistShare.value;
      }
      const h = ((hist && hist.data) || []).filter(x => x.min_buyout).sort((a, b) => a.captured_at < b.captured_at ? -1 : 1);
      it.buckets = h.length;
      const mb = h.map(x => x.min_buyout);
      const last72 = mb.slice(-24), last24 = mb.slice(-8), prev24 = mb.slice(-16, -8);
      it.med72 = med(last72); it.med24 = med(last24); it.p20_72 = pct(last72, 0.2);
      it.low72 = last72.length ? Math.min(...last72) : NaN; it.high72 = last72.length ? Math.max(...last72) : NaN; it.p75_72 = pct(last72, 0.75);
      it.trend24 = med(last24) / med(prev24) - 1;
      it.trend7 = mb.length >= 16 ? med(last24) / med(mb.slice(0, 8)) - 1 : NaN;
      it.target = Math.min(it.med72, it.med24);                        // conservative sell price
      it.threshold = Math.floor(it.target * (1 - cfg.cut) / (1 + cfg.minMargin)); // max buy for minMargin
      const L = ((cur && cur.data) || []).filter(x => x.buyout && x.quantity)
        .map(x => ({ u: x.buyout / x.quantity, q: x.quantity, b: x.buyout })).sort((a, b) => a.u - b.u);
      it.cheapest = L.length ? L[0].u : NaN; it.listedUnits = L.reduce((s, x) => s + x.q, 0);
      it.scanAt = cur && cur.data && cur.data[0] ? cur.data.map(x => x.last_seen_at).sort().at(-1) : null;
      // buy plan: walk the ladder up to threshold, within per-item cap and a share of daily sales.
      // Materials are pooled commodities in Forever (buy any quantity, cheapest first).
      const cap = cfg.budget * cfg.maxShare;
      const maxUnits = Number.isFinite(it.soldPerDay) ? Math.max(1, Math.floor(it.soldPerDay * cfg.dayShare)) : 20;
      const planBuy = thr => {
        let qty = 0, cost = 0, avail = 0; const rem = L.map(x => ({ u: x.u, q: x.q }));
        for (const x of rem) {
          if (x.u > thr) break;
          avail += x.q;
          const n = Math.max(0, Math.min(x.q, maxUnits - qty, Math.floor((cap - cost) / x.u)));
          qty += n; cost += n * x.u; x.q -= n;
        }
        return { qty, cost, avail, rem: rem.filter(x => x.q > 0) };
      };
      // Realistic resale: you must undercut whatever is still listed after your purchase.
      // "Wall" = cheapest price at which the units still listed below it exceed half a day of sales.
      const need = Math.max(1, (it.soldPerDay || 0) * 0.5);
      const wall = rem => { let cum = 0; for (const x of rem) { cum += x.q; if (cum >= need) return x.u; } return Infinity; };
      let p = planBuy(it.threshold);
      it.resale = Math.min(it.target, wall(p.rem) - 1);
      it.threshold = Math.floor(it.resale * (1 - cfg.cut) / (1 + cfg.minMargin));
      p = planBuy(it.threshold);
      it.resale = Math.min(it.target, wall(p.rem) - 1);
      it.availUnder = p.avail; it.buyQty = p.qty; it.buyCost = p.cost;
      it.expProfit = p.qty * it.resale * (1 - cfg.cut) - p.cost;
      const ahead = p.rem.filter(x => x.u < it.resale).reduce((s, x) => s + x.q, 0);
      it.hoursToSell = it.soldPerDay ? (ahead + p.qty) / it.soldPerDay * 24 : NaN;
      it.supplyDays = it.soldPerDay ? it.listedUnits / it.soldPerDay : NaN;
      // Recovery check (Patrick's rule): only a buy if this item has bounced back from similar dips.
      // A dip = the cheapest listing at or under the buy price; recovered = back at or above the sell price within 24h (8 buckets).
      let curDip = 0; for (let j = mb.length - 1; j >= 0 && mb[j] <= it.threshold * 1.05; j--) curDip++;
      let dips = 0, recovered = 0, longest = 0;
      for (let j = 0; j < mb.length - curDip; j++) {
        if (mb[j] <= it.threshold && (j === 0 || mb[j - 1] > it.threshold)) {
          dips++;
          for (let t = j + 1; t < Math.min(mb.length, j + 9); t++) { if (mb[t] >= it.resale) { recovered++; longest = Math.max(longest, t - j); break; } }
        }
      }
      it.dips = dips; it.recovered = recovered; it.recoveryHours = longest * 3; it.currentDipHours = curDip * 3;
      it.willRecover = dips >= 2 && recovered / dips >= 0.67 && !(it.trend7 < -0.10) && curDip <= Math.max(longest, 2);
      it.flags = [
        it.trend24 < -0.15 ? 'falling-24h' : '',
        it.relist > 0.5 ? 'sticky' : '',
        it.speed === 'slow' ? 'slow' : '',
        it.buckets < 16 ? 'thin-history' : '',
        it.supplyDays > 2 ? 'glut' : '',
        !it.willRecover ? 'no-recovery' : '',
      ].filter(Boolean);
    }));
    await sleep(350);
  }
  // names for staples
  for (const it of items.filter(x => !x.name)) {
    const s = await getJSON(`/api/prices/summary?itemId=${it.id}&realmId=${cfg.realmId}&gameVersion=forever&source=all&region=us&auctionHouseId=${cfg.ah}`);
    it.name = (s && s.data && s.data.item_name) || String(it.id);
  }

  // 4. Decide
  const RISK = ['falling-24h', 'thin-history', 'glut', 'no-recovery'];
  const ok = it => Number.isFinite(it.resale) && (it.soldPerDay || 0) >= cfg.minSoldPerDay && !it.flags.some(f => RISK.includes(f));
  const buys = items.filter(it => ok(it) && it.buyQty > 0 && it.expProfit > 0).sort((a, b) => b.expProfit - a.expProfit);
  const risky = items.filter(it => !ok(it) && it.buyQty > 0 && it.expProfit > 0).sort((a, b) => b.expProfit - a.expProfit);
  let left = cfg.budget; const plan = [];
  for (const it of buys) { if (it.buyCost <= left) { plan.push(it); left -= it.buyCost; } }
  const watch = items.filter(it => ok(it) && it.buyQty === 0 && it.cheapest <= it.threshold * 1.15)
    .sort((a, b) => a.cheapest / a.threshold - b.cheapest / b.threshold);
  const sellNow = items.filter(it => Number.isFinite(it.target) && it.cheapest >= it.target * 1.15 && (it.soldPerDay || 0) >= cfg.minSoldPerDay).sort((a, b) => b.cheapest / b.target - a.cheapest / a.target);
  // Vendor prices for new Forever items (ids >= 200000) were wrong in testing, so only classic ids, and only if worth the trip.
  const vf = vendorFlips.filter(v => v.itemId < 200000 && v.vendorSellPrice - v.minBuyout >= 50);

  window.__flip = { cfg, items, plan, risky, watch, sellNow, vf, deals, at: new Date().toISOString() };
  // Compact export for tools/bbb_import.py -> bbb.json (also kept in window.__flipExport)
  const rnd = v => Number.isFinite(v) ? Math.round(v) : null;
  window.__flipExport = JSON.stringify({ scan_utc: items.map(x => x.scanAt).filter(Boolean).sort().at(-1) || null,
    items: items.filter(it => ok(it) || it.buyQty > 0).map(it => ({ id: it.id, name: it.name, threshold: rnd(it.threshold),
      resale: rnd(it.resale), target: rnd(it.target), soldPerDay: it.soldPerDay || 0, cheapest: rnd(it.cheapest), availUnder: it.availUnder,
      low72: rnd(it.low72), med72: rnd(it.med72), high72: rnd(it.high72), p75_72: rnd(it.p75_72), dips: it.dips,
      recovered: it.recovered, flags: it.flags, buckets: it.buckets, ok: ok(it) })) });
  // Save the export to Downloads as bbb_export.json; `python tools/bbb_import.py` picks up the newest one.
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([window.__flipExport], { type: 'application/json' }));
  a.download = 'bbb_export.json'; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  const line = it => `${it.name} | buy up to ${coin(it.threshold)} (now ${coin(it.cheapest)}) | sell at ${coin(it.resale)} | ${Math.round(it.soldPerDay || 0)}/day, ~${Math.round(it.hoursToSell)}h to sell, bounced back ${it.recovered}/${it.dips} dips (slowest ${it.recoveryHours}h, current dip ${it.currentDipHours}h)${it.flags.length ? ' [' + it.flags.join(',') + ']' : ''}`;
  const scan = items.map(x => x.scanAt).filter(Boolean).sort().at(-1);
  return [
    `SCAN ${scan} | checked ${items.length} items (${deals.length} deals rows) | budget ${coin(cfg.budget)}`,
    `BUY NOW (${plan.length}):`,
    ...plan.map(it => `  ${line(it)} | buy ${it.buyQty} for ${coin(it.buyCost)} -> profit ~${coin(it.expProfit)} (${Math.round(it.expProfit / it.buyCost * 100)}%)`),
    `  unspent ${coin(left)}`,
    `RISKY (profitable on paper, flagged): ${risky.slice(0, 5).map(it => line(it) + ` | ${it.buyQty} for ${coin(it.buyCost)} -> ~${coin(it.expProfit)}`).join(' || ') || 'none'}`,
    `WATCH (within 15% of buy price): ${watch.slice(0, 8).map(it => `${it.name} ${coin(it.cheapest)}/${coin(it.threshold)}`).join(' | ')}`,
    `HIGH NOW (sell if you hold): ${sellNow.slice(0, 8).map(it => `${it.name} ${coin(it.cheapest)} vs ~${coin(it.target)}`).join(' | ')}`,
    `VENDOR FLIPS (verify tooltip): ${vf.slice(0, 6).map(v => `${v.name} ${coin(v.minBuyout)} -> ${coin(v.vendorSellPrice)} x${v.unitsAtMinimum}`).join(' | ') || 'none'}`,
  ].join('\n');
}
return flipScan(CFG);
})().then(r => { window.__flipOut = r; }, e => { window.__flipErr = String((e && e.stack) || e); });
'started';
