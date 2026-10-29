/* Server-only dashboard tabs: price history and price alerts (injected by albion_flips.server). */
(function () {
  'use strict';
  const CITY_COLORS = {
    'Thetford': '#a855f7', 'Lymhurst': '#22c55e', 'Bridgewatch': '#f97316', 'Martlock': '#3b82f6',
    'Fort Sterling': '#cbd5e1', 'Caerleon': '#ef4444', 'Black Market': '#eab308', 'Brecilien': '#14b8a6',
  };
  const QUALITY = ['', 'Normal', 'Good', 'Outstanding', 'Excellent', 'Masterpiece'];
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const silver = (n) => (n == null || n === 0) ? '–' : Math.round(n).toLocaleString('en-US');
  const short = (n) => n >= 1e6 ? (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + 'm' : n >= 1e4 ? Math.round(n / 1e3) + 'k' : n >= 1e3 ? (n / 1e3).toFixed(1) + 'k' : String(Math.round(n));
  const ago = (ts) => {
    if (!ts) return '–';
    const s = Math.max(0, Date.now() / 1000 - ts);
    return s < 90 ? Math.round(s) + ' s ago' : s < 5400 ? Math.round(s / 60) + ' min ago' : s < 129600 ? Math.round(s / 3600) + ' h ago' : Math.round(s / 86400) + ' d ago';
  };
  const store = {
    get(k, d) { try { const v = localStorage.getItem('amp.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('amp.' + k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
  };

  // ------------------------------------------------------------ tabs
  const MINE = ['history', 'orders', 'alerts'];
  const original = window.switchTab;
  window.switchTab = function (tab) {
    const mine = MINE.includes(tab);
    original(mine ? 'prices' : tab);
    MINE.forEach((t) => {
      $('view-' + t).style.display = (t === tab) ? 'block' : 'none';
      $('tab-' + t).className = 'tab-btn' + (t === tab ? ' active' : '');
    });
    const hide = (sel) => document.querySelectorAll(sel).forEach((el) => { el.style.display = mine ? 'none' : ''; });
    hide('.filter-panel');
    hide('.pagination-bar');
    if (mine) {
      $('view-prices').style.display = 'none';
      $('tab-prices').className = 'tab-btn';
      const mb = $('mount-toolbar'); if (mb) mb.style.display = 'none';
    }
    if (tab === 'alerts') { loadAlerts(); markSeen(); }
    if (tab === 'orders') loadOrders();
    if (tab === 'history' && !hx.item) setTimeout(() => $('hx-search').focus(), 0);
    store.set('tab', mine ? tab : null);
  };

  // ------------------------------------------------------------ item search box
  function searchBox(inputId, resultsId, onPick) {
    const input = $(inputId), box = $(resultsId);
    let timer = null, items = [], sel = -1, seq = 0;
    const render = () => {
      box.innerHTML = items.map((it, i) => `<div data-i="${i}" class="${i === sel ? 'sel' : ''}"><span>${esc(it.name)}${it.enchant ? ' <span class="sx-muted">.' + it.enchant + '</span>' : ''}</span><small>${esc(it.id)}</small></div>`).join('')
        || '<div class="sx-muted" style="cursor:default;">No item found</div>';
      box.style.display = 'block';
    };
    const pick = (i) => {
      const it = items[i]; if (!it) return;
      box.style.display = 'none'; items = []; sel = -1; seq++;
      input.value = it.name + (it.enchant ? ' .' + it.enchant : '');
      input.blur();
      onPick(it);
    };
    input.addEventListener('input', () => {
      clearTimeout(timer);
      const q = input.value.trim();
      if (q.length < 2) { box.style.display = 'none'; return; }
      timer = setTimeout(async () => {
        const my = ++seq;
        try {
          const r = await fetch('/api/items?q=' + encodeURIComponent(q), { credentials: 'same-origin' });
          const d = await r.json();
          if (my !== seq) return;
          items = d.items || []; sel = items.length ? 0 : -1; render();
        } catch (e) { /* offline */ }
      }, 160);
    });
    input.addEventListener('keydown', (e) => {
      if (box.style.display !== 'block') return;
      if (e.key === 'ArrowDown') { sel = Math.min(items.length - 1, sel + 1); render(); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { sel = Math.max(0, sel - 1); render(); e.preventDefault(); }
      else if (e.key === 'Enter') { pick(sel); e.preventDefault(); }
      else if (e.key === 'Escape') { box.style.display = 'none'; }
    });
    box.addEventListener('mousedown', (e) => { const d = e.target.closest('[data-i]'); if (d) { pick(+d.dataset.i); e.preventDefault(); } });
    input.addEventListener('blur', () => setTimeout(() => { box.style.display = 'none'; }, 120));
    input.addEventListener('focus', () => { if (items.length && input.value.trim().length >= 2) render(); });
  }

  // ------------------------------------------------------------ price history
  const hx = { item: null, quality: 1, days: 30, mode: 'daily', data: null, hidden: new Set(store.get('hiddenCities', [])) };

  function segment(id, attr, onChange) {
    $(id).addEventListener('click', (e) => {
      const b = e.target.closest('button'); if (!b) return;
      $(id).querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
      onChange(b.dataset[attr]);
    });
  }

  async function loadHistory() {
    if (!hx.item) return;
    const main = $('hx-main');
    if (!hx.data) main.innerHTML = '<div class="sx-empty">Loading...</div>';
    try {
      const r = await fetch(`/api/history?id=${encodeURIComponent(hx.item.id)}&q=${hx.quality}&days=${hx.days}`, { credentials: 'same-origin' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      hx.data = await r.json();
      renderHistory();
      location.hash = 'history=' + encodeURIComponent(hx.item.id) + (hx.quality > 1 ? '@q' + hx.quality : '');
    } catch (e) {
      main.innerHTML = '<div class="sx-empty">Could not load the history (' + esc(e.message) + ').</div>';
    }
  }

  function series() {
    const d = hx.data, out = [];
    for (const [city, c] of Object.entries(d.cities)) {
      if (hx.mode === 'daily') {
        const pts = c.daily.map((p) => ({ t: p[0] + 43200, v: p[1], n: p[2] }));
        if (pts.length) out.push({ city, pts });
      } else {
        const sell = c.orders.filter((p) => p[1] > 0).map((p) => ({ t: p[0], v: p[1] }));
        const buy = c.orders.filter((p) => p[2] > 0).map((p) => ({ t: p[0], v: p[2] }));
        if (sell.length) out.push({ city, pts: sell, kind: 'sell' });
        if (buy.length) out.push({ city, pts: buy, kind: 'buy' });
      }
    }
    return out;
  }

  function renderHistory() {
    const d = hx.data, main = $('hx-main');
    const cities = Object.keys(d.cities).sort((a, b) => (d.cities[b].per_day || 0) - (d.cities[a].per_day || 0));
    const all = series();
    const legend = [...new Set(all.map((s) => s.city))].map((c) =>
      `<span class="sx-chip ${hx.hidden.has(c) ? 'off' : ''}" data-city="${esc(c)}"><i style="background:${CITY_COLORS[c] || '#94a3b8'}"></i>${esc(c)}</span>`).join('');
    const bestSell = cities.filter((c) => d.cities[c].buy_max).sort((a, b) => d.cities[b].net_instant - d.cities[a].net_instant)[0];
    const rows = cities.map((c) => {
      const x = d.cities[c];
      const vs = x.vs_avg_pct;
      const vsTxt = vs == null ? '<span class="sx-muted">–</span>' : `<span class="${vs <= -15 ? 'sx-good' : vs >= 15 ? 'sx-bad' : ''}">${vs > 0 ? '+' : ''}${vs} %</span>`;
      return `<tr>
        <td><i style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${CITY_COLORS[c] || '#94a3b8'};margin-right:7px;"></i>${esc(c)}${c === bestSell ? ' <span class="sx-pill armed" title="Most silver after tax when selling instantly">best instant sell</span>' : ''}</td>
        <td class="num">${silver(x.sell_min)}</td><td class="num">${silver(x.buy_max)}</td>
        <td class="num">${silver(x.avg7)}</td><td class="num">${vsTxt}</td>
        <td class="num">${x.per_day ? x.per_day.toLocaleString('en-US') : '–'}</td>
        <td class="sx-muted">${ago(x.updated)}</td>
        <td style="text-align:right;"><button class="sx-btn-sm" data-alert="${esc(c)}">🔔 Alert</button></td></tr>`;
    }).join('');
    const hasData = all.length > 0;
    main.innerHTML = `
      <div class="sx-title"><h2>${esc(d.item_name)}${d.quality > 1 ? ' · ' + QUALITY[d.quality] : ''}</h2><code>${esc(d.item_id)}</code>
        <span class="sx-muted" style="font-size:0.84rem;margin-left:auto;">${hx.mode === 'daily' ? 'Average price of real trades per day' : 'Cheapest sell order (solid) and best buy order (dashed)'}</span></div>
      ${hasData ? `<div class="sx-chart" id="hx-chart"></div><div class="sx-legend" id="hx-legend">${legend}</div>`
        : `<div class="sx-empty">${hx.mode === 'daily' ? 'No trades recorded for this item in the selected period yet.' : 'No order book changes recorded in the last 7 days.'}<br><span style="font-size:0.84rem;">The server stores new data with every sweep, so this fills up over time.</span></div>`}
      <div style="overflow-x:auto;margin-top:14px;"><table class="sx-table">
        <thead><tr><th>City</th><th class="num">Sell now</th><th class="num">Buy now</th><th class="num">Avg 7 days</th><th class="num">Sell vs avg</th><th class="num">Sold / day</th><th>Updated</th><th></th></tr></thead>
        <tbody>${rows || '<tr><td colspan="8" class="sx-empty" style="padding:24px;">No prices for this item yet.</td></tr>'}</tbody></table></div>
      <div class="sx-note">"Sell now" and "Buy now" only show prices younger than 6 hours. Sold per day counts days with trades.</div>`;
    if (hasData) drawChart(all.filter((s) => !hx.hidden.has(s.city)));
    const lg = $('hx-legend');
    if (lg) lg.addEventListener('click', (e) => {
      const chip = e.target.closest('[data-city]'); if (!chip) return;
      const c = chip.dataset.city;
      hx.hidden.has(c) ? hx.hidden.delete(c) : hx.hidden.add(c);
      store.set('hiddenCities', [...hx.hidden]);
      renderHistory();
    });
    main.querySelectorAll('[data-alert]').forEach((b) => b.addEventListener('click', () => {
      const c = b.dataset.alert, x = d.cities[c];
      prefillAlert({ id: d.item_id, name: d.item_name }, d.quality, c, x.sell_min ? Math.round(x.sell_min * 0.9) : (x.avg7 ? Math.round(x.avg7 * 0.85) : ''));
      window.switchTab('alerts');
    }));
  }

  function drawChart(list) {
    const el = $('hx-chart'); if (!el) return;
    const W = Math.max(320, el.clientWidth), H = el.clientHeight || 340;
    const P = { l: 64, r: 16, t: 12, b: hx.mode === 'daily' ? 70 : 28 };
    const pts = list.flatMap((s) => s.pts);
    if (!pts.length) { el.innerHTML = '<div class="sx-empty">All cities are hidden. Click a city below to show it.</div>'; return; }
    const now = Date.now() / 1000;
    const t1 = now, t0 = now - (hx.mode === 'daily' ? hx.days : Math.min(hx.days, 7)) * 86400;
    const vals = pts.map((p) => p.v).sort((a, b) => a - b);
    // ignore the most extreme 2 % so one absurd listing does not flatten everything else
    const lo = vals[Math.floor(vals.length * 0.02)], hi = vals[Math.min(vals.length - 1, Math.ceil(vals.length * 0.98) - 1)];
    const pad = (hi - lo) * 0.08 || hi * 0.1 || 1;
    const v0 = Math.max(0, lo - pad), v1 = hi + pad;
    const X = (t) => P.l + (t - t0) / (t1 - t0) * (W - P.l - P.r);
    const plotB = H - P.b;
    const Y = (v) => P.t + (1 - (Math.min(v1, Math.max(v0, v)) - v0) / (v1 - v0)) * (plotB - P.t);
    let svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Price history chart">`;
    // grid + y labels
    for (let i = 0; i <= 4; i++) {
      const v = v0 + (v1 - v0) * i / 4, y = Y(v);
      svg += `<line x1="${P.l}" x2="${W - P.r}" y1="${y}" y2="${y}" stroke="#1f2638" stroke-width="1"/>`;
      svg += `<text x="${P.l - 8}" y="${y + 4}" text-anchor="end" fill="#64748b" font-size="11">${short(v)}</text>`;
    }
    // x labels
    const span = t1 - t0, step = span > 60 * 86400 ? 14 * 86400 : span > 20 * 86400 ? 5 * 86400 : span > 3 * 86400 ? 86400 : 6 * 3600;
    for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) {
      const dt = new Date(t * 1000);
      const lab = step < 86400 ? dt.toLocaleString('en-GB', { weekday: 'short', hour: '2-digit' }) : dt.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
      svg += `<text x="${X(t)}" y="${H - P.b + 16}" text-anchor="middle" fill="#64748b" font-size="11">${lab}</text>`;
    }
    // volume bars (daily mode): total traded per day across the shown cities
    if (hx.mode === 'daily') {
      const vol = {};
      list.forEach((s) => s.pts.forEach((p) => { vol[p.t] = (vol[p.t] || 0) + (p.n || 0); }));
      const vmax = Math.max(1, ...Object.values(vol));
      const bw = Math.max(2, (W - P.l - P.r) / hx.days * 0.7);
      const vb = H - 6, vh = 34;
      for (const [t, n] of Object.entries(vol)) {
        const h = Math.max(1, n / vmax * vh);
        svg += `<rect x="${X(+t) - bw / 2}" y="${vb - h}" width="${bw}" height="${h}" fill="#334155" rx="1"/>`;
      }
      svg += `<text x="${P.l - 8}" y="${vb - vh + 8}" text-anchor="end" fill="#475569" font-size="10">sold</text>`;
    }
    // lines
    for (const s of list) {
      const c = CITY_COLORS[s.city] || '#94a3b8';
      const ps = s.pts.filter((p) => p.t >= t0 - 86400);
      if (!ps.length) continue;
      let d = '';
      if (hx.mode === 'orders') { // step line: a price holds until it changes
        ps.forEach((p, i) => { d += (i ? `H${X(p.t).toFixed(1)}V${Y(p.v).toFixed(1)}` : `M${X(p.t).toFixed(1)},${Y(p.v).toFixed(1)}`); });
        d += `H${X(t1).toFixed(1)}`;
      } else {
        ps.forEach((p, i) => { d += (i ? 'L' : 'M') + X(p.t).toFixed(1) + ',' + Y(p.v).toFixed(1); });
      }
      svg += `<path d="${d}" fill="none" stroke="${c}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" ${s.kind === 'buy' ? 'stroke-dasharray="5 4" opacity=".85"' : ''}/>`;
      if (hx.mode === 'daily' && ps.length < 40) ps.forEach((p) => { svg += `<circle cx="${X(p.t)}" cy="${Y(p.v)}" r="2.6" fill="${c}"/>`; });
    }
    svg += `<line id="hx-cursor" x1="0" x2="0" y1="${P.t}" y2="${plotB}" stroke="#475569" stroke-width="1" style="display:none"/>`;
    svg += '</svg><div class="sx-tip" id="hx-tip"></div>';
    el.innerHTML = svg;

    const tip = $('hx-tip'), cursor = $('hx-cursor'), svgEl = el.querySelector('svg');
    svgEl.addEventListener('mousemove', (e) => {
      const rect = svgEl.getBoundingClientRect();
      const x = (e.clientX - rect.left) / rect.width * W;
      if (x < P.l || x > W - P.r) { tip.style.display = 'none'; cursor.style.display = 'none'; return; }
      const t = t0 + (x - P.l) / (W - P.l - P.r) * (t1 - t0);
      const lines = [];
      for (const s of list) {
        let best = null;
        if (hx.mode === 'orders') { for (const p of s.pts) { if (p.t <= t) best = p; else break; } }
        else { let dmin = 86400 * 0.75; for (const p of s.pts) { const dd = Math.abs(p.t - t); if (dd < dmin) { dmin = dd; best = p; } } }
        if (best) lines.push({ s, p: best });
      }
      if (!lines.length) { tip.style.display = 'none'; cursor.style.display = 'none'; return; }
      lines.sort((a, b) => b.p.v - a.p.v);
      const dt = new Date((hx.mode === 'daily' ? Math.floor(t / 86400) * 86400 : t) * 1000);
      const head = hx.mode === 'daily' ? dt.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' }) : dt.toLocaleString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
      tip.innerHTML = `<b>${head}</b>` + lines.map(({ s, p }) =>
        `<div><span><i style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${CITY_COLORS[s.city] || '#94a3b8'};margin-right:6px;"></i>${esc(s.city)}${s.kind === 'buy' ? ' buy' : s.kind === 'sell' ? ' sell' : ''}</span><span>${silver(p.v)}${p.n ? ' <span class="sx-muted">×' + p.n + '</span>' : ''}</span></div>`).join('');
      tip.style.display = 'block';
      const px = (e.clientX - rect.left);
      tip.style.left = Math.min(rect.width - tip.offsetWidth - 4, px + 14) + 'px';
      tip.style.top = '8px';
      cursor.setAttribute('x1', x); cursor.setAttribute('x2', x); cursor.style.display = '';
    });
    svgEl.addEventListener('mouseleave', () => { tip.style.display = 'none'; cursor.style.display = 'none'; });
  }

  window.openPriceHistory = function (itemId, quality) {
    hx.item = { id: itemId, name: itemId };
    hx.quality = quality || 1;
    $('hx-quality').value = String(hx.quality);
    $('hx-search').value = itemId;
    hx.data = null;
    window.switchTab('history');
    loadHistory();
  };

  // ------------------------------------------------------------ price alerts
  const ax = { item: null, data: null, seen: store.get('alertSeen', 0) };

  function prefillAlert(item, quality, city, price) {
    ax.item = item;
    $('ax-search').value = item.name || item.id;
    $('ax-quality').value = String(quality || 1);
    // the Black Market only buys, so an alert there watches its buy orders
    $('ax-kind').value = city === 'Black Market' ? 'buy_above' : 'sell_below';
    $('ax-city').value = city || '';
    $('ax-price').value = price || '';
    $('ax-hint').textContent = '';
    setTimeout(() => $('ax-price').focus(), 0);
  }

  function condText(r) {
    return (r.kind === 'sell_below' ? 'Sell order ≤ ' : 'Buy order ≥ ') + silver(r.price);
  }

  // one line for any event in the alert feed (price alert or outbid order)
  function eventText(e) {
    const name = e.item_name + (e.quality > 1 ? ' (' + QUALITY[e.quality] + ')' : '');
    if (e.kind === 'outbid') {
      return e.order_kind === 'buy_order'
        ? `${name}: your buy order in ${e.city} (${silver(e.target)}) was outbid at ${silver(e.price)}`
        : `${name}: your sell order in ${e.city} (${silver(e.target)}) was undercut at ${silver(e.price)}`;
    }
    return `${name}: ${silver(e.price)} in ${e.city} (target ${e.kind === 'sell_below' ? '≤' : '≥'} ${silver(e.target)})`;
  }

  async function loadAlerts() {
    try {
      const r = await fetch('/api/alerts', { credentials: 'same-origin' });
      if (!r.ok) return;
      const d = await r.json();
      const first = ax.data == null;
      ax.data = d;
      if (first) {
        const sel = $('ax-city');
        sel.innerHTML = '<option value="">Any city</option>' + d.cities.map((c) => `<option>${esc(c)}</option>`).join('');
        if (!ax.seen && d.events.length) { ax.seen = d.seq; store.set('alertSeen', ax.seen); } // first visit: no flood of old alerts
      }
      renderAlerts();
      notifyNew();
    } catch (e) { /* offline */ }
  }

  function renderAlerts() {
    const d = ax.data; if (!d) return;
    $('ax-add').disabled = !d.can_edit;
    if (!d.can_edit) $('ax-hint').textContent = 'Read-only view: open your login link to add or remove alerts.';
    $('ax-rules').innerHTML = d.rules.length ? d.rules.map((r) => `<tr>
        <td><a href="#" data-hist="${esc(r.item_id)}" data-q="${r.quality}" style="color:var(--text);text-decoration:none;">${esc(r.item_name || r.item_id)}</a>${r.quality > 1 ? ' <span class="sx-muted">· ' + QUALITY[r.quality] + '</span>' : ''}<br><code class="sx-muted" style="font-size:0.74rem;">${esc(r.item_id)}</code></td>
        <td>${condText(r)}</td><td>${esc(r.city || 'Any city')}</td>
        <td>${r.armed ? '<span class="sx-pill armed">watching</span>' : '<span class="sx-pill fired" title="Fires again once the price has moved back">triggered</span>'}</td>
        <td class="num">${r.fired_count || 0}</td><td class="sx-muted">${ago(r.last_fired)}</td>
        <td style="text-align:right;">${d.can_edit ? `<button class="sx-btn-sm danger" data-del="${esc(r.id)}">Remove</button>` : ''}</td></tr>`).join('')
      : '<tr><td colspan="7" class="sx-empty" style="padding:28px;">No alerts yet. Add one above, or use the 🔔 button in the price history.</td></tr>';
    $('ax-events').innerHTML = d.events.length ? d.events.map((e) => `<tr>
        <td class="sx-muted">${new Date(e.time * 1000).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}</td>
        <td>${e.kind === 'outbid' ? '<span class="sx-pill outbid">' + (e.order_kind === 'buy_order' ? 'outbid' : 'undercut') + '</span> ' : ''}${esc(e.item_name)}${e.quality > 1 ? ' <span class="sx-muted">· ' + QUALITY[e.quality] + '</span>' : ''}</td><td>${esc(e.city)}</td>
        <td class="num ${e.kind === 'outbid' ? 'sx-bad' : 'sx-good'}">${silver(e.price)}</td><td class="num sx-muted">${e.kind === 'outbid' ? 'yours ' : e.kind === 'sell_below' ? '≤ ' : '≥ '}${silver(e.target)}</td></tr>`).join('')
      : '<tr><td colspan="5" class="sx-empty" style="padding:28px;">No alert has fired yet.</td></tr>';
    const unseen = d.events.filter((e) => e.seq > ax.seen).length;
    const badge = $('alerts-badge');
    badge.textContent = unseen; badge.style.display = unseen ? 'inline-block' : 'none';
    $('ax-notify').textContent = !('Notification' in window) ? 'Desktop notifications not supported'
      : Notification.permission === 'granted' ? 'Desktop notifications on' : Notification.permission === 'denied' ? 'Notifications blocked in browser' : 'Enable desktop notifications';
  }

  function markSeen() {
    if (!ax.data) return;
    ax.seen = ax.data.seq; store.set('alertSeen', ax.seen); renderAlerts();
  }

  const notified = new Set();
  function notifyNew() {
    const d = ax.data; if (!d) return;
    const fresh = d.events.filter((e) => e.seq > ax.seen && !notified.has(e.seq) && Date.now() / 1000 - e.time < 3600);
    for (const e of fresh.reverse()) {
      notified.add(e.seq);
      const text = eventText(e);
      const head = e.kind === 'outbid' ? (e.order_kind === 'buy_order' ? 'Outbid' : 'Undercut') : 'Price alert';
      const div = document.createElement('div');
      div.innerHTML = `<b>${e.kind === 'outbid' ? '⚠️' : '🔔'} ${head}</b><br>${esc(text)}`;
      $('sx-toast').appendChild(div);
      setTimeout(() => div.remove(), 12000);
      if ('Notification' in window && Notification.permission === 'granted') {
        try { new Notification(head + ': ' + e.item_name, { body: text, icon: '/favicon.ico', tag: 'amp-alert-' + e.seq }); } catch (err) { /* ignore */ }
      }
    }
    if (fresh.some((e) => e.kind === 'outbid')) loadOrders();
    if (document.getElementById('view-alerts').style.display === 'block') markSeen();
  }

  async function addAlert() {
    const hint = $('ax-hint');
    if (!ax.item) { hint.textContent = 'Pick an item from the search list first.'; return; }
    const price = parseInt($('ax-price').value, 10);
    if (!(price > 0)) { hint.textContent = 'Enter a price in silver.'; return; }
    const body = { item_id: ax.item.id, quality: +$('ax-quality').value, kind: $('ax-kind').value, price, city: $('ax-city').value };
    $('ax-add').disabled = true;
    try {
      const r = await fetch('/api/alerts', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const d = await r.json();
      if (!r.ok) { hint.textContent = d.error || ('Error ' + r.status); return; }
      hint.textContent = 'Alert added: ' + (d.rule.item_name || d.rule.item_id) + ', ' + condText(d.rule) + '.';
      $('ax-price').value = '';
      await loadAlerts();
    } catch (e) { hint.textContent = 'Could not save the alert.'; }
    finally { $('ax-add').disabled = !(ax.data && ax.data.can_edit); }
  }

  async function removeAlert(id) {
    try {
      await fetch('/api/alerts/delete', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id }) });
      await loadAlerts();
    } catch (e) { /* ignore */ }
  }

  // ------------------------------------------------------------ order tracker
  const ox = { item: null, data: null };
  const KIND = {
    buy_order: ['buy', 'Buy order'], sell_order: ['sell', 'Sell order'],
    buy_instant: ['buy', 'Bought'], sell_instant: ['sell', 'Sold'],
  };
  const signed = (n) => (n > 0 ? '+' : n < 0 ? '−' : '') + silver(Math.abs(n) || 0);
  const tone = (n) => n > 0 ? 'sx-good' : n < 0 ? 'sx-bad' : '';

  async function api(path, body) {
    const r = await fetch(path, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || ('Error ' + r.status));
    return d;
  }

  async function loadOrders() {
    try {
      const r = await fetch('/api/orders', { credentials: 'same-origin' });
      if (!r.ok) return;
      const d = await r.json();
      if (!ox.data) {
        $('ox-city').innerHTML = d.cities.map((c) => `<option>${esc(c)}</option>`).join('');
        const home = store.get('orderCity', null);
        if (home && d.cities.includes(home)) $('ox-city').value = home;
      }
      ox.data = d;
      const typing = document.activeElement && document.activeElement.closest && document.activeElement.closest('#ox-open');
      if (!typing) renderOrders(); // never replace a number the user is typing
    } catch (e) { /* offline */ }
  }

  function itemCell(o) {
    return `<a href="#" data-hist="${esc(o.item_id)}" data-q="${o.quality}" style="color:var(--text);text-decoration:none;">${esc(o.item_name || o.item_id)}</a>${o.quality > 1 ? ' <span class="sx-muted">· ' + QUALITY[o.quality] + '</span>' : ''}${o.note ? '<br><span class="sx-muted" style="font-size:0.76rem;">' + esc(o.note) + '</span>' : ''}`;
  }

  function renderOrders() {
    const d = ox.data; if (!d) return;
    const t = d.totals, edit = d.can_edit;
    $('ox-add').disabled = !edit;
    if (!edit) $('ox-hint').textContent = 'Read-only view: open your login link to add or change orders.';
    $('ox-kpis').innerHTML = [
      ['Spent', silver(t.spent), 'filled buys incl. fees', ''],
      ['Earned', silver(t.earned), 'filled sales after tax and fees', ''],
      ['Stock worth now', silver(t.stock_value), 'at the best instant sale', ''],
      ['Realised profit', signed(t.realised), 'sales minus their average cost', tone(t.realised)],
      ['Overall', signed(t.total), 'earned + stock − spent', tone(t.total)],
      ['Open orders', String(t.open), t.outbid ? `<span class="sx-bad">${t.outbid} outbid or undercut</span>` : (t.tied_up ? silver(t.tied_up) + ' tied up in buy orders' : 'all on top'), ''],
    ].map(([k, v, s, c]) => `<div class="sx-kpi"><div class="k">${k}</div><div class="v ${c}">${v}</div><div class="s">${s}</div></div>`).join('');

    const open = d.orders.filter((o) => !o.closed), closed = d.orders.filter((o) => o.closed);
    $('ox-open-note').textContent = open.length ? 'Market prices are checked after every sweep and every upload from your PC.' : '';
    $('ox-open').innerHTML = open.length ? open.map((o) => {
      const [cls, label] = KIND[o.kind];
      const st = o.status === 'top' ? '<span class="sx-pill top">on top</span>'
        : o.status === 'outbid' ? `<span class="sx-pill outbid" title="Highest buy order is ${silver(o.market_now)}">outbid</span>`
        : o.status === 'undercut' ? `<span class="sx-pill outbid" title="Cheapest sell order is ${silver(o.market_now)}">undercut</span>`
        : '<span class="sx-pill nodata" title="No fresh price for this item in this city">no data</span>';
      const fix = (o.status === 'outbid' || o.status === 'undercut') && edit
        ? ` <button class="sx-btn-sm" data-relist="${esc(o.id)}" data-price="${o.status === 'outbid' ? o.market_now + 1 : Math.max(1, o.market_now - 1)}" title="Set your price to beat the market by 1 silver">→ ${silver(o.status === 'outbid' ? o.market_now + 1 : o.market_now - 1)}</button>` : '';
      return `<tr>
        <td><span class="sx-pill ${cls}">${label}</span></td><td>${itemCell(o)}</td><td>${esc(o.city)}</td>
        <td class="num">${edit ? `<input class="sx-num-in" type="number" min="1" value="${o.price}" data-price-of="${esc(o.id)}">` : silver(o.price)}</td>
        <td class="num">${silver(o.market_now)}</td><td>${st}${fix}</td>
        <td class="num">${edit ? `<input class="sx-num-in" type="number" min="0" max="${o.qty}" value="${o.filled}" data-fill-of="${esc(o.id)}" style="width:70px;">` : o.filled} <span class="sx-muted">/ ${o.qty.toLocaleString('en-US')}</span></td>
        <td class="num" title="Silver after fees for the filled part / still open">${silver(o.value)}<br><span class="sx-muted" style="font-size:0.76rem;">${o.open_value ? silver(o.open_value) + ' open' : ''}</span></td>
        <td class="sx-muted">${ago(o.created)}</td>
        <td style="text-align:right;white-space:nowrap;">${edit ? `<button class="sx-btn-sm" data-done="${esc(o.id)}" title="Order finished or cancelled: keep what was filled">Done</button> <button class="sx-btn-sm danger" data-odel="${esc(o.id)}" title="Delete completely">✕</button>` : ''}</td></tr>`;
    }).join('') : '<tr><td colspan="10" class="sx-empty" style="padding:28px;">No open orders. Add the orders you placed in game above.</td></tr>';

    $('ox-stock').innerHTML = d.stock.length ? d.stock.map((s) => `<tr>
        <td>${itemCell(s)}</td><td class="num">${s.qty.toLocaleString('en-US')}</td><td class="num">${silver(s.avg_cost)}</td>
        <td class="num">${silver(s.unit_now)}</td><td>${esc(s.best_city || '–')}</td><td class="num">${silver(s.value_now)}</td>
        <td class="num ${tone(s.profit_now || 0)}">${s.profit_now == null ? '–' : signed(s.profit_now)}</td></tr>`).join('')
      : '<tr><td colspan="7" class="sx-empty" style="padding:24px;">Nothing in stock. Filled buys appear here until you sell them.</td></tr>';

    $('ox-closed-count').textContent = closed.length ? `(${closed.length})` : '(none yet)';
    $('ox-closed').innerHTML = closed.map((o) => {
      const [cls, label] = KIND[o.kind];
      return `<tr><td><span class="sx-pill ${cls}">${label}</span></td><td>${itemCell(o)}</td><td>${esc(o.city)}</td>
        <td class="num">${silver(o.price)}</td><td class="num">${o.filled.toLocaleString('en-US')} <span class="sx-muted">/ ${o.qty.toLocaleString('en-US')}</span></td>
        <td class="num">${silver(o.value)}</td><td class="sx-muted">${ago(o.closed)}</td>
        <td style="text-align:right;white-space:nowrap;">${edit ? `${o.kind.endsWith('_order') ? `<button class="sx-btn-sm" data-reopen="${esc(o.id)}">Reopen</button> ` : ''}<button class="sx-btn-sm danger" data-odel="${esc(o.id)}">✕</button>` : ''}</td></tr>`;
    }).join('');

    const badge = $('orders-badge');
    badge.textContent = t.outbid; badge.style.display = t.outbid ? 'inline-block' : 'none';
  }

  async function addOrder() {
    const hint = $('ox-hint');
    if (!ox.item) { hint.textContent = 'Pick an item from the search list first.'; return; }
    const kind = $('ox-kind').value, qty = parseInt($('ox-qty').value, 10), price = parseInt($('ox-price').value, 10);
    if (!(price > 0) || !(qty > 0)) { hint.textContent = 'Enter price and quantity.'; return; }
    const body = { kind, item_id: ox.item.id, quality: +$('ox-quality').value, city: $('ox-city').value, price, qty,
      filled: kind.endsWith('_order') ? (parseInt($('ox-filled').value, 10) || 0) : qty, note: $('ox-note').value };
    $('ox-add').disabled = true;
    try {
      const d = await api('/api/orders', body);
      hint.textContent = `Added: ${KIND[kind][1]} ${d.order.item_name}, ${qty.toLocaleString('en-US')} × ${silver(price)} in ${body.city}.`;
      store.set('orderCity', body.city);
      $('ox-price').value = ''; $('ox-filled').value = '0'; $('ox-note').value = '';
      await loadOrders();
    } catch (e) { hint.textContent = e.message; }
    finally { $('ox-add').disabled = !(ox.data && ox.data.can_edit); }
  }

  async function changeOrder(id, body) {
    try { await api('/api/orders/update', Object.assign({ id }, body)); }
    catch (e) { $('ox-hint').textContent = e.message; }
    await loadOrders();
  }

  function syncKind() {
    const order = $('ox-kind').value.endsWith('_order');
    $('ox-filled').disabled = !order;
    $('ox-filled').title = order ? 'How much of the order is already filled' : 'Instant trades are filled completely';
  }

  // ------------------------------------------------------------ wiring
  function init() {
    searchBox('hx-search', 'hx-results', (it) => { hx.item = it; hx.data = null; loadHistory(); });
    searchBox('ax-search', 'ax-results', (it) => { ax.item = it; $('ax-hint').textContent = ''; $('ax-price').focus(); });
    $('hx-quality').addEventListener('change', (e) => { hx.quality = +e.target.value; loadHistory(); });
    segment('hx-days', 'd', (v) => { hx.days = +v; loadHistory(); });
    segment('hx-mode', 'm', (v) => { hx.mode = v; if (hx.data) renderHistory(); });
    $('ax-add').addEventListener('click', addAlert);
    $('ax-price').addEventListener('keydown', (e) => { if (e.key === 'Enter') addAlert(); });
    $('ax-rules').addEventListener('click', (e) => {
      const del = e.target.closest('[data-del]');
      if (del) { removeAlert(del.dataset.del); return; }
      const h = e.target.closest('[data-hist]');
      if (h) { e.preventDefault(); window.openPriceHistory(h.dataset.hist, +h.dataset.q); }
    });
    $('ax-notify').addEventListener('click', () => {
      if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission().then(renderAlerts);
    });
    searchBox('ox-search', 'ox-results', (it) => { ox.item = it; $('ox-hint').textContent = ''; $('ox-price').focus(); });
    $('ox-kind').addEventListener('change', syncKind); syncKind();
    $('ox-add').addEventListener('click', addOrder);
    ['ox-price', 'ox-qty', 'ox-filled', 'ox-note'].forEach((id) => $(id).addEventListener('keydown', (e) => { if (e.key === 'Enter') addOrder(); }));
    const ov = $('view-orders');
    ov.addEventListener('click', (e) => {
      const b = e.target.closest('button, a[data-hist]'); if (!b) return;
      if (b.dataset.hist) { e.preventDefault(); window.openPriceHistory(b.dataset.hist, +b.dataset.q); }
      else if (b.dataset.done) changeOrder(b.dataset.done, { closed: true });
      else if (b.dataset.reopen) changeOrder(b.dataset.reopen, { closed: false });
      else if (b.dataset.relist) changeOrder(b.dataset.relist, { price: +b.dataset.price });
      else if (b.dataset.odel && confirm('Delete this entry completely? Use "Done" to keep it in your history.')) {
        api('/api/orders/delete', { id: b.dataset.odel }).then(loadOrders, (err) => { $('ox-hint').textContent = err.message; });
      }
    });
    ov.addEventListener('change', (e) => {
      const i = e.target;
      if (i.dataset.fillOf) changeOrder(i.dataset.fillOf, { filled: +i.value });
      else if (i.dataset.priceOf) changeOrder(i.dataset.priceOf, { price: +i.value });
    });
    loadOrders();
    setInterval(() => { if (!document.hidden) loadOrders(); }, 60000);

    let rt = null;
    window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => { if (hx.data && $('view-history').style.display === 'block') renderHistory(); }, 150); });

    loadAlerts();
    setInterval(loadAlerts, 20000);

    const m = /^#history=([A-Z0-9_@]+?)(?:@q([1-5]))?$/.exec(decodeURIComponent(location.hash || ''));
    if (m) window.openPriceHistory(m[1], m[2] ? +m[2] : 1);
    else if (store.get('tab', null)) window.switchTab(store.get('tab', null));
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
