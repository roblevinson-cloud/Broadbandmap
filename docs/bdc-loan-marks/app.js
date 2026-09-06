const app = document.querySelector('#app');
const state = { summary: null, search: null, searchOpen: false, query: '', issuerCache: new Map(), portfolioCache: new Map() };
const fmt = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 });
const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1 });
const number = new Intl.NumberFormat('en-US');
const esc = value => String(value ?? '—').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
const mark = value => value == null ? '—' : Number(value).toFixed(1);
const tone = value => value == null ? '' : value < 80 ? 'mark-low' : value < 95 ? 'mark-mid' : value > 101 ? 'mark-high' : '';
const path = relative => new URL(relative, location.href.split('#')[0]).href;

function dueBucket(date) {
  if (!date) return 'Not disclosed';
  const years = (new Date(date) - new Date(state.summary.meta.coverageEnd)) / (365.25 * 864e5);
  return years <= 0 ? 'Past due / amended' : years <= 1 ? '≤1 year' : years <= 2 ? '1–2 years' : years <= 3 ? '2–3 years' : years <= 5 ? '3–5 years' : '>5 years';
}

function searchBox() {
  return `<div class="search-wrap"><div class="search-box"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg><input id="issuer-search" type="search" autocomplete="off" aria-label="Search issuer or borrower" placeholder="Search an issuer or borrower…" value="${esc(state.query)}"><kbd>⌘ K</kbd></div><div id="search-results"></div></div>`;
}

function rankedSearch(query) {
  const q = query.trim().toLocaleLowerCase();
  if (q.length < 2) return [];
  return state.search.filter(x => x.name.toLocaleLowerCase().includes(q)).sort((a, b) => {
    const an = a.name.toLocaleLowerCase(), bn = b.name.toLocaleLowerCase();
    return Number(bn.startsWith(q)) - Number(an.startsWith(q)) || b.holders - a.holders || b.positions - a.positions;
  }).slice(0, 12);
}

function updateResults() {
  const host = document.querySelector('#search-results');
  if (!host) return;
  const rows = rankedSearch(state.query);
  if (!state.searchOpen || state.query.trim().length < 2) { host.innerHTML = ''; return; }
  host.innerHTML = `<div class="results">${rows.length ? rows.map(x => `<button class="result" data-issuer="${x.id}" data-bucket="${x.bucket}"><strong>${esc(x.name)}</strong><small>${esc(x.industry || 'Unclassified')} · ${x.holders} holder${x.holders === 1 ? '' : 's'} · ${x.positions} observations</small></button>`).join('') : '<div class="empty">No issuer found</div>'}</div>`;
}

function bindSearch() {
  const input = document.querySelector('#issuer-search');
  if (!input) return;
  input.addEventListener('input', event => { state.query = event.target.value; state.searchOpen = true; updateResults(); });
  input.addEventListener('focus', () => { state.searchOpen = true; updateResults(); });
  input.addEventListener('keydown', event => {
    if (event.key === 'Escape') { state.searchOpen = false; updateResults(); }
    if (event.key === 'Enter') { const first = rankedSearch(state.query)[0]; if (first) location.hash = `issuer=${first.id}&bucket=${first.bucket}`; }
  });
  document.querySelector('#search-results')?.addEventListener('click', event => {
    const row = event.target.closest('[data-issuer]');
    if (row) location.hash = `issuer=${row.dataset.issuer}&bucket=${row.dataset.bucket}`;
  });
}

const kpi = (label, value, detail) => `<article class="kpi"><label>${label}</label><strong>${value}</strong><small>${detail}</small></article>`;
const panelHead = (kicker, title, action = '') => `<header class="panel-head"><span><small>${kicker}</small><h2>${title}</h2></span>${action}</header>`;

function lineChart(series, metric = 'value') {
  const rows = series.filter(x => x[metric] != null), labels = [...new Set(rows.map(x => x.quarter))].sort();
  if (!rows.length) return '<div class="empty">No comparable history available</div>';
  const values = rows.map(x => x[metric]), low = Math.min(...values) - 2, high = Math.max(...values) + 2;
  const width = 720, height = 230, pad = 28;
  const x = quarter => pad + labels.indexOf(quarter) / Math.max(1, labels.length - 1) * (width - pad * 2);
  const y = value => height - pad - (value - low) / (high - low || 1) * (height - pad * 2);
  const groups = new Map();
  rows.forEach(row => { const key = row.holder || 'Median'; if (!groups.has(key)) groups.set(key, []); groups.get(key).push(row); });
  const colors = ['#7dd3fc', '#fbbf24', '#a78bfa', '#34d399', '#fb7185', '#60a5fa', '#f472b6', '#c4b5fd'];
  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Quarterly mark history"><line class="axis" x1="${pad}" y1="${height-pad}" x2="${width-pad}" y2="${height-pad}"/>${[0,.5,1].map(t => { const yy = pad + t * (height-pad*2), v = high - t * (high-low); return `<line class="grid" x1="${pad}" y1="${yy}" x2="${width-pad}" y2="${yy}"/><text x="0" y="${yy+4}">${v.toFixed(0)}</text>`; }).join('')}${labels.map(q => `<text x="${x(q)}" y="${height-5}" text-anchor="middle">${q.replace('Q',' Q')}</text>`).join('')}${[...groups].slice(0,8).map(([name, points], i) => `<polyline fill="none" stroke="${colors[i]}" stroke-width="2" points="${points.sort((a,b)=>a.quarter.localeCompare(b.quarter)).map(r=>`${x(r.quarter)},${y(r[metric])}`).join(' ')}"><title>${esc(name)}</title></polyline>${points.map(r=>`<circle cx="${x(r.quarter)}" cy="${y(r[metric])}" r="3" fill="${colors[i]}"><title>${esc(name)} · ${r.quarter}: ${mark(r[metric])}</title></circle>`).join('')}`).join('')}</svg>`;
}

function bars(rows, valueKey = 'positions', labels = null) {
  const order = labels || ['Past due / amended','≤1 year','1–2 years','2–3 years','3–5 years','>5 years','Not disclosed'];
  const grouped = Object.fromEntries(order.map(x => [x, 0]));
  rows.forEach(row => { const bucket = row.bucket || dueBucket(row.maturity); grouped[bucket] = (grouped[bucket] || 0) + (Number(row[valueKey]) || 1); });
  const max = Math.max(...Object.values(grouped), 1);
  return `<div class="bars">${order.map(label => `<div class="bar-row"><span>${label}</span><div class="bar-track"><div class="bar-fill" style="width:${grouped[label]/max*100}%"></div></div><b>${fmt.format(grouped[label])}</b></div>`).join('')}</div>`;
}

function showError(error) {
  console.error(error);
  app.innerHTML = `<section class="error"><p class="eyebrow">Snapshot unavailable</p><h1>That view could not be opened.</h1><p>${esc(error.message || error)}</p></section>`;
}

function dashboard(tab = 'market') {
  const { meta, overview, portfolios } = state.summary, current = overview.trend.at(-1);
  app.innerHTML = `<section class="hero"><div><p class="eyebrow">Private credit valuation tape</p><h1>See the same loan through every BDC’s marks.</h1><p>Search disclosed loans, open BDC portfolios, and track fair-value dispersion by issuer, industry, and maturity.</p></div>${searchBox()}</section><section class="kpis">${kpi('Loan observations',fmt.format(meta.counts.positions),`${fmt.format(meta.counts.filings)} quarterly and annual filings`)}${kpi('Active issuers',fmt.format(state.search.length),`${portfolios.length} reporting BDC portfolios`)}${kpi('Current median mark',mark(meta.currentMedianPrice),`${current?.quarter || ''} disclosed price`)}${kpi('Comparable tranches',fmt.format(meta.crossHolderMatches),'Two or more holders; confidence scored')}</section><nav class="tabs"><button class="tab ${tab==='market'?'active':''}" data-tab="market">Market & BDCs</button><button class="tab ${tab==='dispersion'?'active':''}" data-tab="dispersion">Cross-holder dispersion</button><button class="tab ${tab==='method'?'active':''}" data-tab="method">Coverage & method</button></nav><div id="tab-content"></div>`;
  bindSearch();
  document.querySelector('.tabs').addEventListener('click', event => { const button = event.target.closest('[data-tab]'); if (button) dashboard(button.dataset.tab); });
  renderTab(tab);
}

function renderTab(tab) {
  const host = document.querySelector('#tab-content'), { overview, portfolios } = state.summary;
  if (tab === 'dispersion') {
    host.innerHTML = `<section class="panel">${panelHead('Quarter-aligned cross-holder comparison','Same instrument, different marks','<span class="chip">Confidence scored</span>')}<div class="table-scroll"><table class="data-table dispersion-table"><thead><tr><th>Issuer / instrument</th><th>Quarter</th><th class="right">Holders</th><th class="right">Low</th><th class="right">High</th><th class="right">Range</th></tr></thead><tbody>${overview.divergence.map((d,i) => { const marks = d.holders.map(h=>h.mark); return `<tr class="clickable" data-dispersion="${i}"><td class="issuer-title"><strong>${esc(d.issuer)}</strong><small>${esc(d.instrument)}</small></td><td class="mono">${d.quarter}</td><td class="right">${d.holders.length}</td><td class="right mono ${tone(Math.min(...marks))}">${mark(Math.min(...marks))}</td><td class="right mono ${tone(Math.max(...marks))}">${mark(Math.max(...marks))}</td><td class="right mono mark-mid">${d.range.toFixed(1)}</td></tr>`; }).join('')}</tbody></table></div></section>`;
    host.addEventListener('click', event => { const row = event.target.closest('[data-dispersion]'); if (row) location.hash = `dispersion=${row.dataset.dispersion}`; });
    return;
  }
  if (tab === 'method') {
    host.innerHTML = `<div class="method-grid"><article class="method"><b>Structured SEC spine</b><p>Official SEC BDC XBRL packages provide standardized holdings from August 2022 forward.</p></article><article class="method"><b>Native static snapshot</b><p>Pages hosts current portfolios plus compact issuer histories. No remote application or service account is required.</p></article><article class="method"><b>Matching guardrail</b><p>Issuer, maturity, capital structure, and spread are combined; ambiguous matches stay out of dispersion.</p></article></div><p class="notice"><b>Interpretation:</b> Price is fair value divided by disclosed principal. FV / cost is separate. Raw values remain in each registrant’s as-filed units, so compare ratios—not raw amounts—across BDCs.</p>`;
    return;
  }
  const riskRows = overview.riskPositions.map(row => ({...row, item: state.search.find(item => item.id === row.issuerId)})).filter(row => row.item).slice(0,12);
  host.innerHTML = `<div class="grid2"><section class="panel">${panelHead('Median disclosed price','Loan mark trend','<span class="chip">FV / principal</span>')}<div class="chart">${lineChart(overview.trend.map(x=>({...x,value:x.medianPrice,holder:'Median'})))}</div></section><section class="panel">${panelHead('Latest reported position count','Maturity wall')}${bars(overview.maturity)}</section></div><div class="grid2 grid-even"><section class="panel">${panelHead('Latest filing per vehicle','BDC portfolios','<span class="chip">Click to open</span>')}<div class="table-scroll"><table class="data-table"><thead><tr><th>BDC</th><th class="right">Fair value / AUM</th><th class="right">Issuers</th><th class="right">Wtd mark</th></tr></thead><tbody>${portfolios.map(p=>`<tr class="clickable" data-portfolio="${p.cik}"><td>${esc(p.holder)}</td><td class="right mono">${money.format(p.fairValue)}</td><td class="right mono">${number.format(p.issuers)}</td><td class="right mono ${tone(p.weightedPrice)}">${mark(p.weightedPrice)}</td></tr>`).join('')}</tbody></table></div></section><section class="panel">${panelHead('Latest filing per vehicle','Lowest disclosed marks','<span class="chip">Review queue</span>')}<div class="risk-list">${riskRows.map(r=>`<button class="risk" data-risk="${r.item.id}" data-bucket="${r.item.bucket}"><b class="${tone(r.price)}">${mark(r.price)}</b><span><strong>${esc(r.issuer)}</strong><small>${esc(r.holder)} · ${esc(r.instrument)}</small></span><span class="arrow">›</span></button>`).join('')}</div></section></div><p class="notice"><b>Portfolio fair value / AUM:</b> totals sum latest searchable debt positions and remain in each filer’s as-filed units. Use the linked EDGAR filing for audited totals.</p>`;
  host.addEventListener('click', event => {
    const portfolio = event.target.closest('[data-portfolio]'); if (portfolio) location.hash = `portfolio=${portfolio.dataset.portfolio}`;
    const risk = event.target.closest('[data-risk]'); if (risk) location.hash = `issuer=${risk.dataset.risk}&bucket=${risk.dataset.bucket}`;
  });
}

async function portfolio(cik) {
  const summary = state.summary.portfolios.find(x => x.cik === cik);
  if (!summary) return dashboard();
  let rows = state.portfolioCache.get(cik);
  if (!rows) { rows = await fetch(path(`data/portfolios/${cik}.json`)).then(r => { if (!r.ok) throw Error('Portfolio snapshot not found'); return r.json(); }); state.portfolioCache.set(cik, rows); }
  state.query = '';
  const markRows = [
    {bucket:'Below 80',positions:rows.filter(x=>x.price!=null&&x.price<80).length},
    {bucket:'80–90',positions:rows.filter(x=>x.price>=80&&x.price<90).length},
    {bucket:'90–100',positions:rows.filter(x=>x.price>=90&&x.price<100).length},
    {bucket:'At / above par',positions:rows.filter(x=>x.price>=100).length}
  ];
  app.innerHTML = `<button class="back" data-back>← All BDC portfolios</button><section class="detail-head"><div><p class="eyebrow">BDC portfolio · ${summary.date}</p><h1>${esc(summary.holder)}</h1><p>Latest searchable debt investments from the filer’s Schedule of Investments.</p></div>${searchBox()}</section><section class="kpis">${kpi('Fair value / AUM',money.format(summary.fairValue),'Searchable debt positions, as filed')}${kpi('Holdings',number.format(rows.length),'Individual disclosed loan positions')}${kpi('Issuers',number.format(summary.issuers),'Normalized active borrower names')}${kpi('Weighted mark',mark(summary.weightedPrice),'Fair value / disclosed principal')}</section><div class="grid2"><section class="panel">${panelHead('Latest portfolio','Maturity profile')}${bars(rows)}</section><section class="panel">${panelHead('Portfolio mix','Mark distribution')}${bars(markRows,'positions',['Below 80','80–90','90–100','At / above par'])}</section></div><section class="panel">${panelHead('Click an issuer for full history','Holdings',`<span class="chip">${number.format(rows.length)} positions</span>`)}<div class="table-scroll"><table class="data-table"><thead><tr><th>Issuer / instrument</th><th>Maturity</th><th class="right">Fair value</th><th class="right">Price</th><th class="right">FV / cost</th></tr></thead><tbody>${rows.sort((a,b)=>(b.fairValue||0)-(a.fairValue||0)).map(r=>`<tr class="clickable" data-issuer="${r.issuerId}"><td class="issuer-title"><strong>${esc(r.issuer)}</strong><small>${esc(r.instrument)}</small></td><td>${esc(r.maturity||'—')}</td><td class="right mono">${money.format(r.fairValue||0)}</td><td class="right mono ${tone(r.price)}">${mark(r.price)}</td><td class="right mono ${tone(r.fvCost)}">${mark(r.fvCost)}</td></tr>`).join('')}</tbody></table></div></section>`;
  bindSearch();
  document.querySelector('[data-back]').onclick = () => { location.hash = ''; };
  document.querySelector('tbody').addEventListener('click', event => { const row = event.target.closest('[data-issuer]'); if (row) { const item = state.search.find(x=>x.id===row.dataset.issuer); if (item) location.hash=`issuer=${item.id}&bucket=${item.bucket}`; } });
}

async function issuer(id, bucket) {
  const item = state.search.find(x => x.id === id);
  if (!item) return dashboard();
  const key = String(bucket).padStart(2, '0');
  let data = state.issuerCache.get(key);
  if (!data) { data = await fetch(path(`data/issuers/${key}.json`)).then(r => { if (!r.ok) throw Error('Issuer history not found'); return r.json(); }); state.issuerCache.set(key, data); }
  const detail = data[id]; if (!detail) throw Error('Issuer is not present in this active snapshot');
  const holdings = detail.holdings, holders = [...new Set(holdings.map(x=>x.holder))], fv = holdings.reduce((sum,x)=>sum+(x.fairValue||0),0);
  const marks = holdings.map(x=>x.price).filter(x=>x!=null).sort((a,b)=>a-b), median = marks.length ? marks[Math.floor(marks.length/2)] : null;
  state.query = item.name;
  app.innerHTML = `<button class="back" data-back>← Market overview</button><section class="detail-head"><div><p class="eyebrow">Issuer / borrower explorer</p><h1>${esc(item.name)}</h1><p>${esc(item.industry||'Industry not classified')} · last reported ${esc(item.lastReport)}</p></div>${searchBox()}</section><section class="kpis">${kpi('Current fair value',money.format(fv),'Across active holder disclosures')}${kpi('BDC holders',number.format(holders.length),holders.slice(0,3).map(esc).join(' · '))}${kpi('Current median mark',mark(median),'Fair value / disclosed principal')}${kpi('History points',number.format(detail.history.length),`${item.positions} source observations`)}</section><div class="grid2"><section class="panel">${panelHead('Quarterly holder marks','Issuer history','<span class="chip">FV / principal</span>')}<div class="chart">${lineChart(detail.history,'price')}</div></section><section class="panel">${panelHead('Current disclosed holdings','Maturity profile')}${bars(holdings)}</section></div><section class="panel">${panelHead('Latest position per active BDC','Holdings',`<span class="chip">${holdings.length} positions</span>`)}<div class="table-scroll"><table class="data-table"><thead><tr><th>BDC / instrument</th><th>Maturity</th><th class="right">Fair value</th><th class="right">Price</th><th class="right">FV / cost</th><th>Source</th></tr></thead><tbody>${holdings.sort((a,b)=>(b.fairValue||0)-(a.fairValue||0)).map(r=>`<tr><td class="issuer-title"><strong>${esc(r.holder)}</strong><small>${esc(r.instrument)}</small></td><td>${esc(r.maturity||'—')}</td><td class="right mono">${money.format(r.fairValue||0)}</td><td class="right mono ${tone(r.price)}">${mark(r.price)}</td><td class="right mono ${tone(r.fvCost)}">${mark(r.fvCost)}</td><td><a class="source" href="${esc(r.filingUrl)}" target="_blank" rel="noreferrer">EDGAR ↗</a></td></tr>`).join('')}</tbody></table></div></section>`;
  bindSearch(); document.querySelector('[data-back]').onclick = () => { state.query=''; location.hash=''; };
}

function dispersion(index) {
  const d = state.summary.overview.divergence[Number(index)]; if (!d) return dashboard('dispersion');
  const series = d.history.flatMap(h => h.holders.map(m => ({ quarter:h.quarter, holder:m.holder, value:m.mark })));
  app.innerHTML = `<button class="back" data-back>← Cross-holder dispersion</button><section class="detail-head"><div><p class="eyebrow">Cross-holder mark comparison</p><h1>${esc(d.issuer)}</h1><p>${esc(d.instrument)}${d.maturity?` · due ${esc(d.maturity)}`:''}</p></div></section><section class="kpis">${kpi('Latest range',`${d.range.toFixed(1)} pts`,'High minus low disclosed mark')}${kpi('Median mark',mark(d.median),d.quarter)}${kpi('BDC holders',number.format(d.holders.length),'Quarter-aligned comparison')}${kpi('Match confidence',`${Math.round(d.matchConfidence*100)}%`,'Issuer and tranche match')}</section><div class="grid2 grid-even"><section class="panel">${panelHead('Quarter-aligned marks','Comparison history')}<div class="chart">${lineChart(series)}</div></section><section class="panel">${panelHead('Latest reported quarter','Holder marks')}<div class="table-scroll"><table class="data-table"><thead><tr><th>BDC</th><th class="right">Mark</th><th class="right">Fair value</th><th>Source</th></tr></thead><tbody>${d.holders.map(h=>`<tr><td>${esc(h.holder)}</td><td class="right mono ${tone(h.mark)}">${mark(h.mark)}</td><td class="right mono">${money.format(h.fairValue)}</td><td><a class="source" href="${esc(h.filingUrl)}" target="_blank" rel="noreferrer">EDGAR ↗</a></td></tr>`).join('')}</tbody></table></div></section></div><p class="notice"><b>Comparison note:</b> holder marks align to the same fiscal quarter and require a high-confidence tranche match. Reporting dates, currency, revolver utilization, or amendments may still explain differences.</p>`;
  document.querySelector('[data-back]').onclick = () => { location.hash=''; dashboard('dispersion'); };
}

async function route() {
  try {
    const params = new URLSearchParams(location.hash.slice(1));
    if (params.has('portfolio')) return await portfolio(params.get('portfolio'));
    if (params.has('issuer')) return await issuer(params.get('issuer'), params.get('bucket'));
    if (params.has('dispersion')) return dispersion(params.get('dispersion'));
    state.query=''; dashboard();
  } catch (error) { showError(error); }
}

async function init() {
  try {
    [state.summary, state.search] = await Promise.all([
      fetch(path('data/summary.json')).then(r=>r.json()),
      fetch(path('data/search.json')).then(r=>r.json())
    ]);
    document.querySelector('#snapshot-date').textContent = `Snapshot through ${state.summary.meta.coverageEnd}`;
    window.addEventListener('hashchange', route);
    document.addEventListener('keydown', event => { if ((event.metaKey||event.ctrlKey) && event.key.toLowerCase()==='k') { event.preventDefault(); document.querySelector('#issuer-search')?.focus(); } });
    await route();
  } catch (error) { showError(error); }
}

init();
