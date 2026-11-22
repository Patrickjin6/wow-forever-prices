// Load index.html in a headless DOM, serve the GitHub data from a local folder, optionally feed an Auctionator.lua.
import { JSDOM } from 'jsdom';
import fs from 'fs';

const [page, dataDir, aucPath] = process.argv.slice(2);
const html = fs.readFileSync(page, 'utf8');
const errors = [];
const dom = new JSDOM(html, {
  runScripts: 'dangerously', pretendToBeVisual: true, url: 'https://patrickjin6.github.io/wow-forever-prices/',
  beforeParse(window) {
    window.fetch = async (url) => {
      const name = String(url).split('/').pop().split('?')[0];
      const p = `${dataDir}/${name}`;
      if (!fs.existsSync(p)) return { ok: false, status: 404, json: async () => { throw new Error('404'); } };
      const body = fs.readFileSync(p, 'utf8');
      return { ok: true, status: 200, json: async () => JSON.parse(body) };
    };
    window.TextDecoder = TextDecoder;
    window.addEventListener('error', (e) => errors.push('window error: ' + e.message));
  },
});
const { window } = dom;
Object.defineProperty(window.HTMLElement.prototype, 'clientWidth', { get() { return 460; } });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
await sleep(300);
const doc = window.document;
const text = (sel) => [...doc.querySelectorAll(sel)].map((e) => e.textContent.replace(/\s+/g, ' ').trim());
const report = (label) => {
  console.log(`--- ${label}`);
  console.log('LEDE:', text('#lede')[0]);
  console.log('FRESH:', text('#fresh')[0]);
  console.log('SCANLINE:', text('#scanline')[0]);
  console.log('GOLD:', text('.card .value').join(' | '), '| thin note shown:', !doc.getElementById('thin').hidden, '| load error shown:', !doc.getElementById('loaderr').hidden);
  console.log('BUY:', text('#buyrows tr').join(' || '), '|', text('#buytotal')[0]);
  console.log('HOLD:', text('#holdrows tr').join('\n      '));
  console.log('CHARTS:', doc.querySelectorAll('#charts .chart').length, 'paths:', doc.querySelectorAll('#charts path').length, '|', text('#charts .sub').join(' / '));
  console.log('BW:', text('#bwrows tr').join(' || '));
  console.log('HIGH:', text('#highrows tr').join(' || '), '| WATCH:', text('#watchrows tr').join(' || '));
};
report('after load');
if (aucPath) {
  const buf = fs.readFileSync(aucPath);
  const t0 = Date.now();
  const parsed = window.wfTest.parseAuctionator(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));
  const ids = new Set(['15417', '13465', '13467', '4480', '274971', '13108', '2771']);
  const live = window.wfTest.scanPrices(parsed, ids, new Set(['15417', '13465']));
  console.log(`--- Auctionator parse: ${Object.keys(parsed.db).length} entries in ${Date.now() - t0} ms, scan ${parsed.scan && parsed.scan.toISOString()}`);
  console.log('PRICES:', JSON.stringify(live.prices));
  console.log('DAILY 15417:', JSON.stringify(live.daily['15417']));
  if (process.env.FAKE_NOW_SCAN) {
    // pretend the scan is fresh so the page uses it
    live.scan = new Date().toISOString();
  }
  window.wfTest.state.live = live;
  window.wfTest.render();
  report('with the scan applied');
}
console.log(errors.length ? 'ERRORS:\n' + errors.join('\n') : 'NO ERRORS');
