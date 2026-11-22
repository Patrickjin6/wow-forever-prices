// Run the dashboard page against the real data files with a stand-in for the dashboard runtime.
import { JSDOM } from 'jsdom';
import * as d3 from 'd3';
import fs from 'fs';

const dataDir = process.argv[2] || '/home/claude/wow-dashboard';
const html = fs.readFileSync(process.argv[3] || '/home/claude/wow-dashboard/index.html', 'utf8');
const markup = html.replace(/<script>[\s\S]*<\/script>/, '');
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];

const dom = new JSDOM(`<!doctype html><div id="dash-root">${markup}</div>`, { runScripts: 'outside-only', pretendToBeVisual: true });
const { window } = dom;
window.SVGElement.prototype.getComputedTextLength = function () { return (this.textContent || '').length * 6.5; };
Object.defineProperty(window.HTMLElement.prototype, 'clientWidth', { get() { return 720; } });

const load = (id) => { const j = JSON.parse(fs.readFileSync(`${dataDir}/${id}.json`, 'utf8')); return j.rows || j; };
const sources = {};
for (const id of ['status', 'buys', 'holdings', 'blackwater', 'sell_high', 'watch', 'daily']) sources[id] = load(id);
const calcs = {}, draws = {}, onData = [];
const errors = [];
const dash = {
  colors: ['var(--c0)', 'var(--c1)'],
  data(id) {
    if (sources[id]) return { status: 'ok', data: sources[id] };
    if (calcs[id]) return { status: 'ok', data: calcs[id].fn(...calcs[id].inputs.map((i) => sources[i])) };
    return { status: 'missing', data: [] };
  },
  calc(id, def) { calcs[id] = def; },
  onData(fn) { onData.push(fn); },
  draw(name, fn) { draws[name] = fn; },
};
window.dash = dash;
window.d3 = d3;
try { window.eval(script); } catch (e) { errors.push('script: ' + e.stack); }
for (const fn of onData) { try { fn(); } catch (e) { errors.push('onData: ' + e.stack); } }
const doc = window.document;
for (const [name, fn] of Object.entries(draws)) {
  const els = doc.querySelectorAll(`[data-draw="${name}"]`);
  if (!els.length) errors.push(`no element for drawing ${name}`);
  els.forEach((el) => {
    if (el.closest('[hidden]')) return; // hidden: the type doesn't run it
    try { (typeof fn === 'function' ? fn : fn.update)(el); } catch (e) { errors.push(`draw ${name}: ${e.stack}`); }
  });
}
doc.querySelectorAll('[data-draw]').forEach((el) => { if (!draws[el.dataset.draw]) errors.push('drawing with no code: ' + el.dataset.draw); });

// Marks: every data-field must exist in its source's rows (or calc)
doc.querySelectorAll('[data-source][data-field]').forEach((el) => {
  const ids = el.dataset.source.split(' ');
  const rows = ids.flatMap((id) => dash.data(id).data || []);
  if (rows.length && !(el.dataset.field in rows[0])) errors.push(`mark ${el.dataset.source}.${el.dataset.field} not a column`);
});
// Table headers name real columns
doc.querySelectorAll('table[data-source]').forEach((t) => {
  const rows = dash.data(t.dataset.source).data;
  t.querySelectorAll('th[data-field]').forEach((th) => {
    if (rows.length && !(th.dataset.field in rows[0])) errors.push(`table ${t.dataset.source} th ${th.dataset.field} not a column`);
  });
});

const text = (sel) => [...doc.querySelectorAll(sel)].map((e) => e.textContent.replace(/\s+/g, ' ').trim());
console.log('TAKEAWAY:', text('#takeaway')[0]);
console.log('FRESH:', text('#freshness')[0]);
console.log('GOLD:', text('.wf-card-value').join(' | '));
console.log('VISIBLE NOTES:', [...doc.querySelectorAll('.wf-note')].filter((e) => !e.hidden).map((e) => e.id));
console.log('EMPTY STATES SHOWN:', [...doc.querySelectorAll('.wf-empty')].filter((e) => !e.hidden).map((e) => e.id));
console.log('HOLD ROWS:', text('#hold-table tbody tr').join('\n           '));
console.log('BUY ROWS:', text('#buy-table tbody tr').join(' || ') || '(none)', '| TOTAL:', text('#buy-total')[0]);
console.log('BW ROWS:', text('#bw-table tbody tr').join(' || ') || '(none)');
console.log('CHART panels:', doc.querySelectorAll('#hold-chart .wf-panel').length, '| bars:', doc.querySelectorAll('#hold-chart rect').length,
  '| labels:', text('#hold-chart svg > text').join(' / '));
console.log(errors.length ? 'ERRORS:\n' + errors.join('\n') : 'NO ERRORS');
