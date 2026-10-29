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
  const MINE = ['overview', 'profile', 'history', 'orders', 'island', 'map', 'wiki', 'alerts'];
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
    if (tab === 'island') loadIsland();
    if (tab === 'profile') loadProfile();
    if (tab === 'overview') loadOverview();
    if (tab === 'map') loadWorld();
    if (tab === 'wiki' && !wx.id) setTimeout(() => $('wx-search').focus(), 0);
    if (tab === 'history' && !hx.item) setTimeout(() => $('hx-search').focus(), 0);
    store.set('lastTab', tab);
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

  // ------------------------------------------------------------ island and gathering
  const IX_DEFAULTS = { home: 'Thetford', mode: 'instant', seeds: 18, yield: 9, water: '0', animals: 9, afocus: '0',
    rates: [600, 450, 300, 180, 100, 60, 40], fish_rate: 90 };
  // own settings only after the user changed something here; until then the server fills in the profile
  const ownIsland = store.get('island', null);
  const ix = { own: !!ownIsland, s: Object.assign({}, IX_DEFAULTS, { rates: IX_DEFAULTS.rates.slice() }, ownIsland || {}), part: store.get('islandPart', 'crops'), data: null, seq: 0, onlyMine: store.get('islandOnlyMine', false) };
  const TYPE_NAME = { WOOD: 'Wood', ROCK: 'Stone', ORE: 'Ore', FIBER: 'Fiber', HIDE: 'Hide', FISH: 'Fish' };
  const pct = (x) => x == null ? '–' : Math.round(x * 100) + ' %';
  const histLink = (id, label) => `<a href="#" data-hist="${esc(id)}" data-q="1" style="color:var(--text);text-decoration:none;">${esc(label)}</a>`;

  function islandQuery() {
    const s = ix.s;
    if (!ix.own) return '/api/island';
    const p = new URLSearchParams({ home: s.home, mode: s.mode, seeds: s.seeds, yield: s.yield, water: s.water,
      animals: s.animals, afocus: s.afocus, rates: s.rates.join(','), fish_rate: s.fish_rate });
    return '/api/island?' + p.toString();
  }

  function settingsFromServer(st) {
    // the server's answer becomes the form's values while the user has no own settings
    Object.assign(ix.s, { home: st.home, mode: st.sell_mode, seeds: st.seeds, yield: st.crops_per_seed,
      water: st.water ? '1' : '0', animals: st.animals, afocus: st.animal_focus ? '1' : '0',
      rates: [2, 3, 4, 5, 6, 7, 8].map((t) => st.rates[t]), fish_rate: st.fish_rate });
  }

  async function loadIsland() {
    const my = ++ix.seq;
    try {
      const r = await fetch(islandQuery(), { credentials: 'same-origin' });
      if (!r.ok || my !== ix.seq) return;
      ix.data = await r.json();
      if (!$('ix-home').options.length) {
        $('ix-home').innerHTML = ix.data.settings.markets.map((c) => `<option>${esc(c)}</option>`).join('');
      }
      if (!ix.own) { settingsFromServer(ix.data.settings); fillIslandForm(); }
      $('ix-home').value = ix.data.settings.home;
      $('ix-source').innerHTML = ix.own
        ? 'Your own settings for this tab. <a href="#" id="ix-reset" style="color:var(--blue);">Use the profile values</a>'
        : (ix.data.profile_saved ? 'Values from your <a href="#" data-goto="profile" style="color:var(--blue);">profile</a>. Change them here to try other numbers.'
          : 'Default values. Fill in your <a href="#" data-goto="profile" style="color:var(--blue);">profile</a> to use your island and tiers.');
      renderIsland();
    } catch (e) { $('ix-main').innerHTML = '<div class="sx-empty">Could not load the island data.</div>'; }
  }

  function fillIslandForm() {
    const s = ix.s;
    $('ix-mode').value = s.mode; $('ix-seeds').value = s.seeds; $('ix-yield').value = s.yield;
    $('ix-water').value = s.water; $('ix-animals').value = s.animals; $('ix-afocus').value = s.afocus;
    $('ix-rates').innerHTML = s.rates.map((v, i) => `<label class="sx-muted" style="font-size:0.8rem;">T${i + 2} <input class="sx-num-in" data-rate="${i}" type="number" min="0" step="10" value="${v}"></label>`).join('')
      + `<label class="sx-muted" style="font-size:0.8rem;">Fish <input class="sx-num-in" id="ix-fish" type="number" min="0" step="5" value="${s.fish_rate}"></label>`;
  }

  function readIslandForm() {
    const n = (id, d) => { const v = parseFloat($(id).value); return isFinite(v) && v >= 0 ? v : d; };
    const s = ix.s;
    s.home = $('ix-home').value || s.home; s.mode = $('ix-mode').value;
    s.seeds = n('ix-seeds', IX_DEFAULTS.seeds); s.yield = n('ix-yield', IX_DEFAULTS.yield);
    s.water = $('ix-water').value; s.animals = n('ix-animals', IX_DEFAULTS.animals); s.afocus = $('ix-afocus').value;
    document.querySelectorAll('#ix-rates [data-rate]').forEach((el) => { const v = parseFloat(el.value); if (isFinite(v) && v >= 0) s.rates[+el.dataset.rate] = v; });
    s.fish_rate = n('ix-fish', IX_DEFAULTS.fish_rate);
    ix.own = true;
    store.set('island', s);
  }

  function warnPills(r) {
    let h = '';
    if (r.locked) h += ` <span class="sx-pill outbid" title="Your profile has not unlocked tier ${r.locked} for this yet">needs T${r.locked}</span>`;
    if (r.no_history) h += ' <span class="sx-pill nodata" title="No trades in the last 7 days: the price may not hold">no history</span>';
    if (r.market_small) h += ' <span class="sx-pill warn" title="You would make more than a quarter of what this market trades per day">small market</span>';
    if (r.feed_unknown) h += ' <span class="sx-pill nodata" title="Eats meat: food cost not included">meat food</span>';
    return h;
  }

  function cityNote(r, home) {
    return r.sell_city === home ? '' : ` <span class="sx-muted">${esc(r.sell_city)}</span>`;
  }

  function renderIsland() {
    const d = ix.data; if (!d) return;
    const s = d.settings, home = s.home;
    ['crops', 'animals', 'gathering'].forEach((p) => {
      const b = document.querySelector(`#ix-part [data-p="${p}"]`); if (b) b.className = p === ix.part ? 'on' : '';
    });
    const mine = (r) => !ix.onlyMine || !r.locked;
    const crops = d.crops.filter((r) => !r.missing && mine(r)).sort((a, b) => b.profit_day - a.profit_day);
    const animals = d.animals.filter((r) => !r.missing && mine(r)).sort((a, b) => b.profit_day_per_spot - a.profit_day_per_spot);
    const gath = d.gathering.filter(mine).sort((a, b) => (b.per_hour_home || 0) - (a.per_hour_home || 0) || b.per_hour_best - a.per_hour_best);
    // the cards show what you can do now; locked rows only when nothing else is there
    const pick = (rows, ok) => rows.find((r) => !r.locked && ok(r)) || rows.find(ok);
    const bestCrop = pick(crops, () => true), bestAnimal = pick(animals, (a) => !a.feed_unknown) || animals[0];
    const bestGather = pick(gath, (g) => g.per_hour_home) || gath[0];
    $('ix-only').checked = ix.onlyMine;
    const locked = d.crops.concat(d.animals, d.gathering).filter((r) => r.locked && !r.missing).length;
    $('ix-only-wrap').title = locked + ' rows need a tier you have not unlocked';
    $('ix-kpis').innerHTML = [
      ['Best crop', bestCrop ? silver(bestCrop.profit_day) : '–', bestCrop ? `${bestCrop.name}, ${s.seeds} seeds a day` : 'no prices yet', bestCrop ? tone(bestCrop.profit_day) : ''],
      ['Best animal', bestAnimal ? silver(bestAnimal.profit_day) : '–', bestAnimal ? `${bestAnimal.grown_name}, ${s.animals} spots, per day` : 'no prices yet', bestAnimal ? tone(bestAnimal.profit_day) : ''],
      ['Best gathering', bestGather ? silver(bestGather.per_hour_home || bestGather.per_hour_best) : '–', bestGather ? `${bestGather.name} per hour` : 'no prices yet', ''],
      ['Cheapest food', d.food[0] ? silver(d.food[0].price) : '–', d.food[0] ? `${d.food[0].name}, ${d.food[0].per_point} silver per nutrition` : 'no prices yet', ''],
      ['Market tax', Math.round(d.tax_rate * 1000) / 10 + ' %', s.mode === 'order' ? `plus ${Math.round(d.setup_fee_rate * 1000) / 10} % setup fee` : 'instant sale, no setup fee', ''],
      ['Home biome', (d.biome || []).map((t) => TYPE_NAME[t] || t).join(', ') || '–', `resources around ${home}`, ''],
    ].map(([k, v, sub, cls]) => `<div class="sx-kpi"><div class="k">${k}</div><div class="v ${cls}">${v}</div><div class="s">${esc(sub)}</div></div>`).join('');

    let h = '', note = '', sub = '';
    if (ix.part === 'crops') {
      sub = `${s.seeds} seeds a day, ${s.yield} crops each, ${s.water === '1' ? 'watered' : 'not watered'}`;
      h = `<table class="sx-table"><thead><tr><th>Crop</th><th class="num">Seed</th><th class="num">Seed back</th><th class="num">Crop after tax</th><th class="num">Profit / seed</th><th class="num">Profit / day</th><th class="num" title="Extra silver per 10,000 focus when you water">Watering / 10k focus</th><th class="num">Traded / day</th></tr></thead><tbody>`
        + (crops.map((r) => `<tr><td>${histLink(r.crop, r.name)} <span class="sx-muted">T${r.tier} ${r.kind === 'herb' ? 'herb' : ''}</span>${warnPills(r)}</td>`
          + `<td class="num">${histLink(r.seed, silver(r.seed_price))}${r.seed_city !== home ? ` <span class="sx-muted">${esc(r.seed_city)}</span>` : ''}</td>`
          + `<td class="num" title="Unwatered ${pct(r.seed_back_dry)}, watered ${pct(r.seed_back_wet)}">${pct(r.seed_back)}</td>`
          + `<td class="num">${silver(r.crop_net)}${cityNote(r, home)}</td>`
          + `<td class="num ${tone(r.profit_per_seed)}">${signed(r.profit_per_seed)}</td>`
          + `<td class="num ${tone(r.profit_day)}"><b>${signed(r.profit_day)}</b></td>`
          + `<td class="num ${tone(r.water_per_10k_focus)}">${r.water_per_10k_focus == null ? '–' : signed(r.water_per_10k_focus)}</td>`
          + `<td class="num">${r.volume ? silver(r.volume) : '–'}</td></tr>`).join('')
          || '<tr><td colspan="8" class="sx-empty">No crop prices yet. They arrive with the next market sweeps.</td></tr>')
        + '</tbody></table>';
      const missing = d.crops.filter((r) => r.missing).map((r) => r.name);
      note = 'A harvest takes 22 hours, so a spot gives one harvest a day. Profit = crops × price after tax − seed price × (1 − seed back). '
        + 'Watering adds the bonus from the game data to the seed chance, e.g. wheat 60 % → 140 %. Your own crops count at market price, because you could sell them.'
        + (missing.length ? `<br>No price yet: ${esc(missing.join(', '))}.` : '');
    } else if (ix.part === 'animals') {
      sub = `${s.animals} animals, ${s.afocus === '1' ? 'fed with focus' : 'fed without focus'}`;
      h = `<table class="sx-table"><thead><tr><th>Animal</th><th class="num">Baby</th><th class="num">Food</th><th class="num">Grows</th><th class="num">Grown after tax</th><th class="num">New baby</th><th class="num">Profit / animal</th><th class="num">Per spot / day</th><th class="num">All spots / day</th></tr></thead><tbody>`
        + (animals.map((r) => `<tr><td>${histLink(r.grown, r.grown_name)} <span class="sx-muted">T${r.tier}</span>${warnPills(r)}</td>`
          + `<td class="num">${histLink(r.baby, silver(r.baby_price))}${r.baby_city !== home ? ` <span class="sx-muted">${esc(r.baby_city)}</span>` : ''}</td>`
          + `<td class="num">${r.food ? `${silver(r.food.cost)} <span class="sx-muted" title="${esc(r.food.units + ' × ' + r.food.name)}">${Math.ceil(r.food.units)}× ${esc(r.food.name)}${r.food.favorite ? ' ★' : ''}</span>` : '–'}</td>`
          + `<td class="num">${r.grow_h >= 48 ? Math.round(r.grow_h / 24 * 10) / 10 + ' d' : r.grow_h + ' h'}</td>`
          + `<td class="num">${silver(r.grown_net)}${cityNote(r, home)}</td>`
          + `<td class="num" title="Chance for a new baby: ${pct(r.offspring)}">${silver(r.offspring_value)} <span class="sx-muted">${pct(r.offspring)}</span></td>`
          + `<td class="num ${tone(r.profit)}">${signed(r.profit)}</td>`
          + `<td class="num ${tone(r.profit_day_per_spot)}">${signed(r.profit_day_per_spot)}</td>`
          + `<td class="num ${tone(r.profit_day)}"><b>${signed(r.profit_day)}</b></td></tr>`).join('')
          || '<tr><td colspan="9" class="sx-empty">No animal prices yet. They arrive with the next market sweeps.</td></tr>')
        + '</tbody></table>';
      note = 'Profit = grown animal after tax + value of a new baby − baby price − food. A new baby is worth its purchase price, because you do not have to buy the next one. '
        + 'Food is the cheapest crop or herb per nutrition point; ★ marks the animal\'s favourite food, which counts double. Mounts you could build from grown animals are not included yet.';
      const noBaby = d.animals.filter((r) => r.missing === 'no baby price').length;
      const noGrown = d.animals.filter((r) => r.missing && r.missing !== 'no baby price').length;
      if (noBaby || noGrown) note += `<br>Not shown, because the market data has no price: ${noBaby} babies, ${noGrown} grown animals. Most grown animals are rarely sold, they are turned into mounts or meat.`;
    } else {
      sub = `units per hour from your settings, sold in ${home} or the best city`;
      h = `<table class="sx-table"><thead><tr><th>Resource</th><th class="num">Units / h</th><th class="num">After tax in ${esc(home)}</th><th class="num">Best city</th><th class="num">Silver / h here</th><th class="num">Silver / h best</th><th class="num">Traded / day</th><th>Where near ${esc(home)}</th></tr></thead><tbody>`
        + (gath.map((r) => `<tr><td>${histLink(r.id, r.name)} <span class="sx-muted">T${r.tier} ${TYPE_NAME[r.type] || ''}</span>${warnPills(r)}</td>`
          + `<td class="num">${silver(r.rate)}</td><td class="num">${silver(r.home_net)}</td>`
          + `<td class="num">${silver(r.best_net)} <span class="sx-muted">${esc(r.best_city)}</span></td>`
          + `<td class="num"><b>${silver(r.per_hour_home)}</b></td><td class="num">${silver(r.per_hour_best)}</td>`
          + `<td class="num">${r.volume ? silver(r.volume) : '–'}</td>`
          + `<td style="font-size:0.8rem;">${r.type === 'FISH' ? '<span class="sx-muted">any water of this tier</span>'
            : r.zones.length ? r.zones.slice(0, 3).map((z) => `${esc(z.name)} <span class="sx-muted">T${z.tier} ${z.kind}${z.nodes ? ', ' + z.nodes + ' nodes' : ''}</span>`).join('<br>') + (r.zones.length > 3 ? ` <span class="sx-muted">+${r.zones.length - 3}</span>` : '')
            : `<span class="sx-muted">${r.in_biome ? 'no zone data' : 'not in this biome'}</span>`}</td></tr>`).join('')
          || '<tr><td colspan="8" class="sx-empty">No resource prices yet.</td></tr>')
        + '</tbody></table>';
      note = 'Gathering speed is your own estimate: set it under "Gathering speed". Zones are the blue and yellow zones of your home region with nodes of that tier, from the game\'s map data. Enchanted resources are not listed.';
    }
    $('ix-main').innerHTML = h;
    $('ix-note').innerHTML = note;
    $('ix-sub').textContent = sub;
  }

  // ------------------------------------------------------------ profile
  const PX_GROUPS = [
    ['gather', 'Gathering', [['ORE', 'Ore'], ['FIBER', 'Fiber'], ['WOOD', 'Wood'], ['ROCK', 'Stone'], ['HIDE', 'Hide'], ['FISH', 'Fishing']]],
    ['refine', 'Refining', [['METALBAR', 'Metal bars'], ['CLOTH', 'Cloth'], ['PLANKS', 'Planks'], ['STONEBLOCK', 'Stone blocks'], ['LEATHER', 'Leather']]],
    ['craft', 'Crafting', [['gear', 'Weapons and armor'], ['saddler', 'Mounts (saddler)'], ['cook', 'Food (cook)'], ['alchemist', 'Potions (alchemist)']]],
    ['farming', 'Island', [['crops', 'Crops (farmer)'], ['herbs', 'Herbs (herbalist)'], ['animals', 'Animals (breeder)']]],
  ];
  const px = { data: null, dirty: false };

  async function loadProfile() {
    try {
      const r = await fetch('/api/profile', { credentials: 'same-origin' });
      if (!r.ok) return;
      px.data = await r.json();
      if (!px.dirty) fillProfileForm();
      renderSuggestions();
    } catch (e) { $('px-sugg').innerHTML = '<div class="sx-empty">Could not load the profile.</div>'; }
  }

  function fillProfileForm() {
    const d = px.data, p = d.profile;
    $('px-home').innerHTML = d.cities.map((c) => `<option>${esc(c)}</option>`).join('');
    $('px-name').value = p.name; $('px-home').value = p.home;
    $('px-premium').value = p.premium ? '1' : '0'; $('px-safe').value = p.safe_only ? '1' : '0';
    $('px-focus').value = p.focus_per_day; $('px-hours').value = p.hours_per_day; $('px-capital').value = p.capital;
    $('px-farms').value = p.island.farms; $('px-herbs').value = p.island.herb_gardens; $('px-pastures').value = p.island.pastures;
    $('px-tiers').innerHTML = PX_GROUPS.map(([g, title, keys]) => `<fieldset><legend>${title}</legend>`
      + keys.map(([k, label]) => `<label>${label} <span>T <input class="sx-num-in" type="number" min="0" max="8" data-g="${g}" data-k="${k}" value="${p[g][k]}"></span></label>`).join('')
      + '</fieldset>').join('');
    const edit = d.can_edit;
    document.querySelectorAll('#view-profile input, #view-profile select').forEach((el) => { el.disabled = !edit; });
    $('px-save').disabled = !edit;
    $('px-hint').textContent = !edit ? 'Read-only view: open your login link to change the profile.'
      : d.saved ? 'Saved ' + ago(d.updated) + '.' : 'Not saved yet: these are default values.';
  }

  function readProfileForm() {
    const n = (id) => +$(id).value;
    const body = { name: $('px-name').value.trim(), home: $('px-home').value, premium: $('px-premium').value === '1',
      safe_only: $('px-safe').value === '1', focus_per_day: n('px-focus'), hours_per_day: n('px-hours'), capital: n('px-capital'),
      island: { farms: n('px-farms'), herb_gardens: n('px-herbs'), pastures: n('px-pastures') } };
    document.querySelectorAll('#px-tiers [data-g]').forEach((el) => { (body[el.dataset.g] = body[el.dataset.g] || {})[el.dataset.k] = +el.value; });
    return body;
  }

  async function saveProfile() {
    try {
      await api('/api/profile', readProfileForm());
      px.dirty = false;
      ix.data = null; // the island tab follows the new profile
      await loadProfile();
      $('px-hint').textContent = 'Saved.';
    } catch (e) { $('px-hint').textContent = e.message; }
  }

  function renderSuggestions() {
    const d = px.data; if (!d) return;
    const s = d.suggestions, p = d.profile;
    $('px-sub').textContent = `Fits your tiers${p.safe_only ? ', blue and yellow zones only' : ''}, from the current market data.`;
    const where = (a, b) => a && b && a !== b ? `${esc(a)} → ${esc(b)}` : esc(a || b || '');
    const tierTxt = (t, e) => t ? ` <span class="sx-muted">T${t}${e ? '.' + e : ''}</span>` : '';
    const box = (title, rows, line, empty) => `<div class="sx-box"><h3>${title}</h3>${rows.length
      ? '<ol>' + rows.map((r) => '<li>' + line(r) + '</li>').join('') + '</ol>'
      : `<div class="sx-muted" style="font-size:0.84rem;">${empty}</div>`}</div>`;
    const made = (r) => `${histLink(r.item_id, r.name)}${tierTxt(r.tier, r.enchant)}${r.focus ? ' <span class="sx-pill buy">focus</span>' : ''}: <b class="sx-good">+${silver(r.profit)}</b> each <span class="sx-muted">${where(r.city, r.sell_city)}</span>`;
    $('px-sugg').innerHTML = [
      box('Refining', s.refine, made, 'Nothing profitable within your refining tiers right now.'),
      box('Crafting', s.craft, made, 'Nothing profitable within your crafting tiers in the current list.'),
      box('Transport', s.transport, (r) => `${histLink(r.item_id, r.name)}${tierTxt(r.tier, r.enchant)}: <b class="sx-good">+${silver(r.per_day)}</b>/day <span class="sx-muted">${where(r.buy_city, r.sell_city)}, +${silver(r.profit)} each</span>`, 'No checked flip on safe routes right now.'),
      box('Crops and herbs', s.crops, (r) => `${histLink(r.crop, r.name)}${tierTxt(r.tier)}: <b class="sx-good">+${silver(r.profit_day)}</b>/day <span class="sx-muted">${r.made_day} pieces, sell in ${esc(r.sell_city)}</span>`, 'Nothing profitable you can plant yet.'),
      box('Animals', s.animals, (r) => `${histLink(r.grown, r.grown_name)}${tierTxt(r.tier)}: <b class="sx-good">+${silver(r.profit_day)}</b>/day <span class="sx-muted">baby in ${esc(r.baby_city)}</span>`, 'No priced animal you can raise yet.'),
      box('Gathering', s.gathering, (r) => `${histLink(r.id, r.name)}${tierTxt(r.tier)}: <b>${silver(r.per_hour_home)}</b>/h <span class="sx-muted">${r.type === 'FISH' ? 'fishing' : esc((r.zones[0] || {}).name || '')}</span>`, 'Nothing in your home region within your gathering tiers.'),
    ].join('');
  }

  // ------------------------------------------------------------ map and routes
  const KIND_COLOR = { blue: '#60a5fa', yellow: '#facc15', red: '#f87171', black: '#334155', city: '#c084fc' };
  const KIND_NAME = { blue: 'blue zone', yellow: 'yellow zone', red: 'red zone', black: 'black zone (Outlands)', city: 'city' };
  const BIOME_NAME = { FR: 'Forest', ST: 'Steppe', HL: 'Highland', MN: 'Mountain', SW: 'Swamp' };
  const FACTION_NAME = { KPR: 'Keepers', HER: 'Heretics', MOR: 'Morgana', UND: 'Undead' };
  const SVGNS = 'http://www.w3.org/2000/svg';
  const mx = { w: null, vb: null, sel: null, route: null, home: 'Thetford', byName: {}, r: 10, drag: null };
  const P = (z) => [z.pos[0] * 10, -z.pos[1] * 10];

  async function loadWorld() {
    if (mx.w) { applyView(); return; }
    try {
      const r = await fetch('/api/world', { credentials: 'same-origin' });
      if (!r.ok) return;
      mx.w = await r.json();
      mx.home = mx.w.home;
      $('mx-red').checked = !mx.w.safe_only;
      const zs = Object.entries(mx.w.zones);
      zs.forEach(([id, z]) => { mx.byName[z.name.toLowerCase()] = id; });
      $('mx-zones').innerHTML = zs.map(([, z]) => z.name).sort().map((n) => `<option value="${esc(n)}">`).join('');
      if (!$('mx-from').value) $('mx-from').value = mx.home;
      $('mx-stops').innerHTML = Object.keys(mx.w.cities).sort((a, b) => (a === mx.home ? -1 : b === mx.home ? 1 : a.localeCompare(b)))
        .map((c) => `<label><input type="checkbox" data-stop="${esc(c)}"${c === mx.home ? ' checked' : ''}> ${esc(c)}</label>`).join('');
      // hexagon size from the typical distance between neighbouring zones
      const d = [];
      zs.forEach(([id, z]) => z.exits.forEach((e) => { const o = mx.w.zones[e]; if (o) { const a = P(z), b = P(o); d.push(Math.hypot(a[0] - b[0], a[1] - b[1])); } }));
      d.sort((a, b) => a - b);
      mx.r = (d[Math.floor(d.length / 2)] || 200) * 0.36;
      drawMap();
      fitMap(false);
    } catch (e) { $('mx-zone').innerHTML = '<div class="sx-muted">Could not load the map.</div>'; }
  }

  function hexPoints(x, y, r) {
    const pts = [];
    for (let i = 0; i < 6; i++) { const a = Math.PI / 6 + i * Math.PI / 3; pts.push((x + r * Math.cos(a)).toFixed(0) + ',' + (y + r * Math.sin(a)).toFixed(0)); }
    return pts.join(' ');
  }

  function drawMap() {
    const w = mx.w, svg = $('mx-svg'), showBlack = $('mx-black').checked;
    const visible = (z) => showBlack || z.kind !== 'black';
    const routeSet = new Set(mx.route ? mx.route.path : []);
    let lines = '', hexes = '', labels = '';
    const seen = new Set();
    for (const [id, z] of Object.entries(w.zones)) {
      if (!visible(z)) continue;
      const [x, y] = P(z);
      for (const e of z.exits) {
        const o = w.zones[e]; if (!o || !visible(o)) continue;
        const key = id < e ? id + '|' + e : e + '|' + id; if (seen.has(key)) continue; seen.add(key);
        const [x2, y2] = P(o);
        lines += `<line x1="${x.toFixed(0)}" y1="${y.toFixed(0)}" x2="${x2.toFixed(0)}" y2="${y2.toFixed(0)}"/>`;
      }
      const cls = (routeSet.has(id) ? ' on-route' : '') + (mx.sel === id ? ' sel' : '');
      hexes += `<polygon data-zone="${id}" class="zk-${z.kind}${cls}" points="${hexPoints(x, y, z.kind === 'city' ? mx.r * 1.15 : mx.r)}"><title>${esc(z.name)} · T${z.tier} ${KIND_NAME[z.kind] || ''}</title></polygon>`;
      labels += `<text x="${x.toFixed(0)}" y="${(y + mx.r * 0.15).toFixed(0)}" class="${z.kind === 'city' ? 'city' : 'zn'}">${esc(z.kind === 'city' ? z.name : z.name)}</text>`;
    }
    let route = '';
    if (mx.route) {
      const pts = mx.route.path.map((id) => P(w.zones[id]).map((v) => v.toFixed(0)).join(',')).join(' ');
      route = `<polyline class="route" points="${pts}"/>`;
    }
    const sw = (mx.r * 0.12).toFixed(1);
    svg.innerHTML = `<style>
      #mx-svg line { stroke: #475569; stroke-width: ${sw}; opacity: .55; }
      #mx-svg polygon { stroke: #0a0e17; stroke-width: ${sw}; cursor: pointer; }
      #mx-svg .zk-blue { fill: #1e3a5f; } #mx-svg .zk-yellow { fill: #5c4a12; } #mx-svg .zk-red { fill: #6b1d1d; }
      #mx-svg .zk-black { fill: #1a202c; } #mx-svg .zk-city { fill: #6b21a8; }
      #mx-svg polygon:hover { stroke: #e2e8f0; }
      #mx-svg .on-route { stroke: #f59e0b; stroke-width: ${(mx.r * 0.2).toFixed(1)}; }
      #mx-svg .sel { stroke: #ffffff; stroke-width: ${(mx.r * 0.25).toFixed(1)}; }
      #mx-svg .route { fill: none; stroke: #f59e0b; stroke-width: ${(mx.r * 0.22).toFixed(1)}; stroke-linejoin: round; stroke-linecap: round; opacity: .9; pointer-events: none; }
      #mx-svg text { text-anchor: middle; fill: #e2e8f0; pointer-events: none; font-family: inherit; }
      #mx-svg text.city { font-size: ${(mx.r * 0.8).toFixed(0)}px; font-weight: 700; paint-order: stroke; stroke: #0a0e17; stroke-width: ${(mx.r * 0.18).toFixed(1)}; }
      #mx-svg text.zn { font-size: ${(mx.r * 0.32).toFixed(0)}px; opacity: .85; display: none; }
      #mx-svg.near text.zn { display: block; }
    </style><g>${lines}</g><g>${hexes}</g>${route}<g>${labels}</g>`;
    // the edge colour shows the zone type, the fill stays dark so the route stands out
    svg.querySelectorAll('polygon').forEach((p) => {
      const z = w.zones[p.dataset.zone];
      if (!p.classList.contains('on-route') && !p.classList.contains('sel')) p.style.stroke = KIND_COLOR[z.kind];
    });
    applyView();
  }

  function applyView() {
    if (!mx.vb) return;
    const svg = $('mx-svg');
    svg.setAttribute('viewBox', `${mx.vb.x} ${mx.vb.y} ${mx.vb.w} ${mx.vb.h}`);
    svg.classList.toggle('near', mx.vb.w < mx.r * 28);
  }

  function fitMap(all) {
    const w = mx.w; if (!w) return;
    const zs = Object.values(w.zones).filter((z) => all || z.kind !== 'black');
    const xs = zs.map((z) => P(z)[0]), ys = zs.map((z) => P(z)[1]);
    const pad = mx.r * 2;
    let x0 = Math.min(...xs) - pad, x1 = Math.max(...xs) + pad, y0 = Math.min(...ys) - pad, y1 = Math.max(...ys) + pad;
    const box = $('mx-map'), ar = box.clientWidth / Math.max(1, box.clientHeight);
    let wdt = x1 - x0, hgt = y1 - y0;
    if (wdt / hgt < ar) { const nw = hgt * ar; x0 -= (nw - wdt) / 2; wdt = nw; } else { const nh = wdt / ar; y0 -= (nh - hgt) / 2; hgt = nh; }
    mx.vb = { x: x0, y: y0, w: wdt, h: hgt };
    applyView();
  }

  function zoomMap(f, cx, cy) {
    const vb = mx.vb; if (!vb) return;
    const nw = Math.max(mx.r * 8, Math.min(vb.w * f, mx.r * 2000)), k = nw / vb.w;
    cx = cx == null ? vb.x + vb.w / 2 : cx; cy = cy == null ? vb.y + vb.h / 2 : cy;
    mx.vb = { x: cx - (cx - vb.x) * k, y: cy - (cy - vb.y) * k, w: nw, h: vb.h * k };
    applyView();
  }

  function svgPoint(evt) {
    const r = $('mx-svg').getBoundingClientRect(), vb = mx.vb;
    return [vb.x + (evt.clientX - r.left) / r.width * vb.w, vb.y + (evt.clientY - r.top) / r.height * vb.h];
  }

  function centerOn(id) {
    const z = mx.w.zones[id]; if (!z || !mx.vb) return;
    const [x, y] = P(z);
    const w = Math.min(mx.vb.w, mx.r * 40), h = w * mx.vb.h / mx.vb.w;
    mx.vb = { x: x - w / 2, y: y - h / 2, w, h };
    applyView();
  }

  async function showZone(id) {
    const w = mx.w, z = w.zones[id]; if (!z) return;
    mx.sel = id; drawMap();
    const res = Object.entries(z.resources || {}).map(([k, v]) => `${TYPE_NAME[k] || k} ${v.map(([t, n]) => 'T' + t + ' ×' + n).join(', ')}`).join('<br>');
    const exits = z.exits.filter((e) => w.zones[e]).map((e) => `<a href="#" data-zone-link="${e}">${esc(w.zones[e].name)}</a>`).join('');
    let way = '';
    const homeId = w.cities[mx.home];
    if (homeId && homeId !== id) {
      const r = await fetch(`/api/route?from=${encodeURIComponent(homeId)}&to=${encodeURIComponent(id)}&red=${$('mx-red').checked ? 1 : 0}&min=${+$('mx-min').value || 2.5}`, { credentials: 'same-origin' });
      const d = r.ok ? await r.json() : null;
      way = d && d.route ? `From ${esc(mx.home)}: <b>${d.route.minutes} min</b>, ${d.route.zones} zones${kindsText(d.route.kinds)}`
        : `<span class="sx-bad">Not reachable from ${esc(mx.home)}${$('mx-red').checked ? '' : ' without red zones'}.</span>`;
    }
    $('mx-zone').innerHTML = `<div class="sx-title" style="margin-bottom:6px;"><h2 style="font-size:1.05rem;">${esc(z.name)}</h2><span class="sx-pill" style="background:${KIND_COLOR[z.kind]};color:#0b0f1a;">T${z.tier} ${KIND_NAME[z.kind]}</span></div>`
      + `<div class="sx-facts">${z.region ? `<span>Region <b>${esc(z.region)}</b></span>` : ''}${z.biome ? `<span>Biome <b>${BIOME_NAME[z.biome] || z.biome}</b></span>` : ''}${z.faction ? `<span>Faction <b>${FACTION_NAME[z.faction] || z.faction}</b></span>` : ''}</div>`
      + `<div style="margin-top:8px;font-size:0.86rem;">${res ? '<b>Resource nodes</b><br>' + res : '<span class="sx-muted">No node counts for this zone.</span>'}</div>`
      + (way ? `<div style="margin-top:8px;font-size:0.86rem;">${way}</div>` : '')
      + `<div class="sx-row" style="margin-top:10px;"><button class="sx-btn-sm" data-route-to="${id}">Route here</button><button class="sx-btn-sm" data-route-from="${id}">Start here</button></div>`
      + `<div style="margin-top:10px;font-size:0.8rem;" class="sx-muted">Exits</div><div class="sx-chips">${exits}</div>`;
  }

  function kindsText(k) {
    const parts = Object.entries(k || {}).filter(([kind]) => kind !== 'city').map(([kind, n]) => `${n} ${kind}`);
    return parts.length ? ` <span class="sx-muted">(${parts.join(', ')})</span>` : '';
  }

  function zoneRef(text) {
    const t = (text || '').trim(); if (!t) return '';
    return mx.byName[t.toLowerCase()] || t;
  }

  async function findRoute() {
    const from = zoneRef($('mx-from').value), to = zoneRef($('mx-to').value);
    if (!from || !to) { $('mx-result').textContent = 'Pick a start and a destination.'; return; }
    const r = await fetch(`/api/route?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}&red=${$('mx-red').checked ? 1 : 0}&min=${+$('mx-min').value || 2.5}`, { credentials: 'same-origin' });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { $('mx-result').textContent = d.error === 'unknown zone' ? 'Unknown city or zone name.' : (d.error || 'Error'); return; }
    mx.route = d.route;
    if (!d.route) {
      $('mx-result').innerHTML = `<span class="sx-bad">No way ${d.allow_red ? '' : 'without red zones '}between these two.</span>${d.allow_red ? '' : ' Tick "Allow red zones" to see the risky way.'}`;
      drawMap(); return;
    }
    const names = d.route.path.map((id) => mx.w.zones[id].name);
    $('mx-result').innerHTML = `<b>${d.route.minutes} min</b>, ${d.route.zones} zones${kindsText(d.route.kinds)}: ${names.map(esc).join(' → ')}`;
    drawMap();
  }

  async function planTour() {
    const stops = [...document.querySelectorAll('#mx-stops [data-stop]')].filter((c) => c.checked).map((c) => c.dataset.stop);
    if (stops.length < 2) { $('mx-tour').textContent = 'Tick at least two cities.'; return; }
    const q = `stops=${encodeURIComponent(stops.join(','))}&red=${$('mx-red').checked ? 1 : 0}&round=${$('mx-round').checked ? 1 : 0}&min=${+$('mx-min').value || 2.5}`;
    const r = await fetch('/api/tour?' + q, { credentials: 'same-origin' });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || !d.tour) { $('mx-tour').innerHTML = `<span class="sx-bad">${esc(d.error || 'No way through all of them' + (d.allow_red ? '' : ' without red zones'))}.</span>`; return; }
    const order = d.tour.order.map((id) => mx.w.zones[id].name);
    $('mx-tour').innerHTML = `<b>${d.tour.minutes} min</b>: ${order.map(esc).join(' → ')}<br>` + d.tour.legs.map((l, i) => `${esc(order[i])} → ${esc(order[i + 1])}: ${l.minutes} min`).join('<br>');
    mx.route = { path: d.tour.legs.flatMap((l, i) => (i ? l.path.slice(1) : l.path)) };
    drawMap();
  }

  // ------------------------------------------------------------ wiki
  const wx = { id: null, q: 1, data: null };
  const CAT_NAME = { gear: 'Equipment', farm: 'Farming', raw: 'Raw resource', mount: 'Mount', refined: 'Refined resource', food: 'Food', fish: 'Fish', ingredient: 'Ingredient', potion: 'Potion', crop: 'Crop', herb: 'Herb', product: 'Animal product' };

  window.openWiki = function (itemId, quality) {
    wx.id = itemId; wx.q = quality || 1; $('wx-quality').value = String(wx.q);
    $('wx-search').value = itemId;
    window.switchTab('wiki');
    loadWiki();
  };

  async function loadWiki() {
    if (!wx.id) return;
    try {
      const r = await fetch(`/api/wiki?id=${encodeURIComponent(wx.id)}&q=${wx.q}`, { credentials: 'same-origin' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      wx.data = await r.json();
      renderWiki();
    } catch (e) { $('wx-main').innerHTML = `<div class="sx-card"><div class="sx-empty">Could not load the item (${esc(e.message)}).</div></div>`; }
  }

  function renderWiki() {
    const d = wx.data; if (!d) return;
    const link = (id, label) => `<a href="#" data-wiki="${esc(id)}" style="color:var(--text);">${esc(label || id)}</a>`;
    const facts = [
      d.tier ? `Tier <b>${d.tier}${d.enchant ? '.' + d.enchant : ''}</b>` : '',
      d.category ? `<b>${CAT_NAME[d.category] || d.category}</b>${d.slot ? ' · ' + esc(d.slot) : ''}` : '',
      d.weight != null ? `Weight <b>${d.weight} kg</b>` : '',
      d.power ? `Item power <b>${d.power}</b>` : '',
      d.nutrition ? `Nutrition <b>${d.nutrition}</b>` : '',
      d.name_de ? `German <b>${esc(d.name_de)}</b>` : '',
    ].filter(Boolean).map((f) => `<span>${f}</span>`).join('');
    let h = `<div class="sx-card"><div class="sx-title"><h2>${esc(d.name)}</h2><code>${esc(d.item_id)}</code>${d.quality > 1 ? `<span class="sx-muted">${QUALITY[d.quality]}</span>` : ''}</div>`
      + `<div class="sx-facts">${facts || '<span>No game data for this item, only prices.</span>'}</div>`
      + `<div class="sx-row" style="margin-top:12px;"><button class="sx-btn-sm" data-hist="${esc(d.item_id)}">📈 Price history</button><button class="sx-btn-sm" data-alert="${esc(d.item_id)}">🔔 Price alert</button></div></div>`;

    const best = d.best_sale;
    h += `<div class="sx-card"><div class="sx-title"><h2>Prices now</h2><span class="sx-muted" style="font-size:0.84rem;">${best ? `Best instant sale: <b style="color:var(--text);">${silver(best.net_instant)}</b> after tax in ${esc(best.city)}` : 'No buy order at the moment.'}</span></div>`
      + (d.prices.length ? `<div style="overflow-x:auto;"><table class="sx-table"><thead><tr><th>City</th><th class="num">Cheapest sell order</th><th class="num">Best buy order</th><th class="num">7-day average</th><th class="num">Sold / day</th><th>Updated</th></tr></thead><tbody>`
        + d.prices.map((p) => `<tr><td>${esc(p.city)}</td><td class="num">${silver(p.sell_min)}</td><td class="num">${silver(p.buy_max)}</td><td class="num">${silver(p.avg7)}</td><td class="num">${p.per_day ? silver(p.per_day) : '–'}</td><td class="sx-muted">${ago(p.updated)}</td></tr>`).join('')
        + '</tbody></table></div>' : '<div class="sx-empty" style="padding:20px;">No prices for this item and quality yet.</div>') + '</div>';

    if (d.recipe) {
      const rc = d.recipe;
      h += `<div class="sx-card"><div class="sx-title"><h2>Recipe</h2><span class="sx-muted" style="font-size:0.84rem;">${rc.amount > 1 ? `makes ${rc.amount} · ` : ''}${rc.focus ? `focus cost ${silver(rc.focus)} when you craft with focus` : ''}</span></div>`
        + `<table class="sx-table"><thead><tr><th>Material</th><th class="num">Count</th><th class="num">Cheapest</th><th>Where</th></tr></thead><tbody>`
        + rc.materials.map((m) => `<tr><td>${link(m.id, m.name)}</td><td class="num">${m.count}</td><td class="num">${silver(m.price)}</td><td class="sx-muted">${esc(m.city || '–')}</td></tr>`).join('')
        + `</tbody></table><div class="sx-facts" style="margin-top:10px;"><span>Material cost per item <b>${silver(rc.cost)}</b></span>${rc.profit_instant != null ? `<span>Instant sale minus materials <b class="${tone(rc.profit_instant)}">${signed(rc.profit_instant)}</b></span>` : ''}</div>`
        + `<div class="sx-note">${esc(rc.note)}</div></div>`;
    }
    const f = d.found || {};
    let found = '';
    if (f.zones) found += f.zones.length ? `<div style="font-size:0.86rem;margin-bottom:6px;">Blue and yellow zones with T${d.tier} nodes:</div><div class="sx-chips">${f.zones.map((z) => `<a href="#" data-map-zone="${z.id}">${esc(z.name)} <span class="sx-muted">${z.region || ''} · ${z.kind} · ${z.nodes} nodes</span></a>`).join('')}</div>`
      : `<div class="sx-muted" style="font-size:0.86rem;">No blue or yellow zone with node counts for this tier.</div>`;
    if (f.refine_into) found += `<div class="sx-note">Refines into ${link(f.refine_into, f.refine_into_name || f.refine_into)}${f.refine_city ? `, with the city bonus in <b>${esc(f.refine_city)}</b>` : ''}.</div>`;
    else if (f.refine_city) found += `<div class="sx-note">Refining bonus city: <b>${esc(f.refine_city)}</b>.</div>`;
    if (f.farm) found += `<div class="sx-note">Grows in ${f.farm.grow_h} h. ${link(f.farm.seed, 'Seed')} comes back ${Math.round(f.farm.seed_chance * 100)} % of the time, ${Math.round(f.farm.seed_chance_watered * 100)} % when watered. Crop: ${link(f.farm.crop, f.farm.crop_name || f.farm.crop)}${f.farm.nutrition ? `, nutrition ${f.farm.nutrition} for animals` : ''}.</div>`;
    if (f.animal) found += `<div class="sx-note">${link(f.animal.baby, 'Baby')} grows into ${link(f.animal.grown, 'the grown animal')} in ${f.animal.grow_h >= 48 ? Math.round(f.animal.grow_h / 24 * 10) / 10 + ' days' : f.animal.grow_h + ' h'}. Offspring chance ${Math.round(f.animal.offspring * 100)} %, ${Math.round(f.animal.offspring_focus * 100)} % with focus. Eats ${f.animal.food === 'plants' ? 'crops and herbs' : esc(f.animal.food)}${f.animal.favorite ? `, favourite ${link(f.animal.favorite, f.animal.favorite_name || f.animal.favorite)}` : ''}.</div>`;
    if (f.fish) found += `<div class="sx-note">Caught in fishing spots of tier ${f.fish.tier} zones.</div>`;
    if (found) h += `<div class="sx-card"><div class="sx-title"><h2>Where it comes from</h2></div>${found}</div>`;
    if (d.used_in.length) h += `<div class="sx-card"><div class="sx-title"><h2>Used in</h2><span class="sx-muted" style="font-size:0.84rem;">${d.used_in_count} recipe${d.used_in_count === 1 ? '' : 's'}${d.used_in_count > d.used_in.length ? ', first ' + d.used_in.length + ' shown' : ''}</span></div><div class="sx-chips">${d.used_in.map((u) => `<a href="#" data-wiki="${esc(u.id)}">${esc(u.name)}${u.tier ? ` <span class="sx-muted">T${u.tier}</span>` : ''}</a>`).join('')}</div></div>`;
    $('wx-main').innerHTML = h;
    location.hash = 'wiki=' + encodeURIComponent(d.item_id);
  }

  // ------------------------------------------------------------ overview
  const vx = { data: null, all: store.get('overviewAll', false), open: null, kind: null };
  const change = (n) => n == null ? '<span class="sx-muted" title="Not watched for an hour yet">new</span>'
    : n > 0 ? `<span class="sx-good">▲ ${silver(n)}</span>` : n < 0 ? `<span class="sx-bad">▼ ${silver(-n)}</span>` : '<span class="sx-muted">→ 0</span>';

  async function loadOverview() {
    try {
      const r = await fetch('/api/overview?all=' + (vx.all ? 1 : 0), { credentials: 'same-origin' });
      if (!r.ok) return;
      vx.data = await r.json();
      renderOverview();
    } catch (e) { $('vx-rows').innerHTML = '<tr><td colspan="7" class="sx-empty">Could not load the overview.</td></tr>'; }
  }

  function renderOverview() {
    const d = vx.data; if (!d) return;
    const best = d.best;
    $('vx-kpis').innerHTML = [
      ['Best right now', best ? silver(best.per_day) : '–', best ? `${best.kind}: ${best.what}` : 'no data yet', best ? 'sx-good' : ''],
      ['Per active hour', best ? silver(best.per_active_h) : '–', best ? `about ${best.active_h} h of your time` : '', ''],
      ['Open orders', String(d.orders.open), d.orders.outbid ? `${d.orders.outbid} outbid or undercut` : 'all on top', d.orders.outbid ? 'sx-bad' : ''],
      ['Alerts, 24 h', String(d.alerts_24h), 'price alerts and outbid warnings', ''],
      ['Market data', d.last_refresh ? d.last_refresh.slice(11, 16) + ' UTC' : '–', 'last full sweep', ''],
      ['Your profile', d.profile_saved ? esc(d.home) : 'not set', d.profile_saved ? `${d.safe_only ? 'blue and yellow only' : 'red zones allowed'}, ${d.hours_per_day} h a day` : 'fill it in for your tiers', d.profile_saved ? '' : 'sx-bad'],
    ].map(([k, v, s, cls]) => `<div class="sx-kpi" ${k === 'Open orders' ? 'data-goto="orders"' : k === 'Your profile' ? 'data-goto="profile"' : k === 'Alerts, 24 h' ? 'data-goto="alerts"' : ''} style="${['Open orders', 'Your profile', 'Alerts, 24 h'].includes(k) ? 'cursor:pointer;' : ''}"><div class="k">${k}</div><div class="v ${cls}">${v}</div><div class="s">${esc(s)}</div></div>`).join('');

    let top = '';
    if (best) {
      top += `<div class="sx-card sx-best"><div class="sx-muted" style="font-size:0.78rem;text-transform:uppercase;letter-spacing:.04em;font-weight:600;">Best trade right now · ${esc(best.kind)}</div>`
        + `<div class="sx-row" style="align-items:baseline;margin-top:4px;"><span class="big sx-good">${silver(best.per_day)}</span><span class="sx-muted">silver a day</span><span>${change(best.change_1h)}</span></div>`
        + `<div style="font-size:1rem;margin-top:4px;">${histLink(best.item_id, best.what)}</div><ol>${best.steps.map((s) => `<li>${esc(s)}</li>`).join('')}</ol></div>`;
      const next = d.rows.slice(1, 4);
      const cards = next.map((e, i) => `<div class="sx-kpi"><div class="k">#${i + 2} · ${esc(e.kind)}</div><div class="v">${silver(e.per_day)}</div><div class="s">${esc(e.what)}</div><div class="s">${change(e.change_1h)} in 1 h</div></div>`);
      if (d.up) cards.push(`<div class="sx-kpi"><div class="k">Biggest riser, 1 h</div><div class="v sx-good">▲ ${silver(d.up.change_1h)}</div><div class="s">${esc(d.up.what)}</div></div>`);
      if (d.down) cards.push(`<div class="sx-kpi"><div class="k">Biggest faller, 1 h</div><div class="v sx-bad">▼ ${silver(-d.down.change_1h)}</div><div class="s">${esc(d.down.what)}</div></div>`);
      top += `<div class="sx-cards3">${cards.join('')}</div>`;
    }
    $('vx-best').innerHTML = top;

    $('vx-all').checked = vx.all;
    $('vx-all-label').textContent = vx.all ? 'Also what I cannot do yet' : `Also what I cannot do yet${d.hidden ? ' (' + d.hidden + ')' : ''}`;
    $('vx-sub').textContent = vx.all ? 'everything' : `fits your tiers${d.safe_only ? ', blue and yellow zones only' : ''}`;
    const kinds = Object.entries(d.kinds).sort((a, b) => b[1] - a[1]);
    $('vx-kinds').innerHTML = `<a href="#" data-kind="" style="${!vx.kind ? 'border-color:var(--blue);' : ''}">All</a>`
      + kinds.map(([k, n]) => `<a href="#" data-kind="${esc(k)}" style="${vx.kind === k ? 'border-color:var(--blue);' : ''}">${esc(k)} <span class="sx-muted">${n}</span></a>`).join('');
    const rows = d.rows.filter((e) => !vx.kind || e.kind === vx.kind);
    $('vx-rows').innerHTML = rows.map((e, i) => {
      const main = `<tr class="row" data-row="${esc(e.id)}"><td class="num sx-muted">${i + 1}</td><td><span class="sx-kind">${esc(e.kind)}</span></td>`
        + `<td>${esc(e.what)}${e.why_not ? ` <span class="sx-pill outbid">${esc(e.why_not)}</span>` : ''}</td>`
        + `<td class="num">${silver(e.unit)}</td><td class="num">${silver(e.per_active_h)}</td><td class="num"><b class="sx-good">${silver(e.per_day)}</b></td><td class="num">${change(e.change_1h)}</td></tr>`;
      const steps = vx.open === e.id ? `<tr class="steps"><td></td><td colspan="6"><div class="sx-steps"><ol>${e.steps.map((s) => `<li>${esc(s)}</li>`).join('')}</ol>`
        + `<div class="sx-row" style="margin-top:6px;"><button class="sx-btn-sm" data-wiki-open="${esc(e.item_id)}">📖 Wiki</button><button class="sx-btn-sm" data-hist="${esc(e.item_id)}">📈 Price history</button></div></div></td></tr>` : '';
      return main + steps;
    }).join('') || `<tr><td colspan="7" class="sx-empty">${d.hidden ? 'Nothing fits your profile right now. Tick "Also what I cannot do yet" to see the rest.' : 'No data yet: the first market sweep is still running.'}</td></tr>`;
  }

  // ------------------------------------------------------------ wiring
  function init() {
    $('vx-all').addEventListener('change', (e) => { vx.all = e.target.checked; store.set('overviewAll', vx.all); loadOverview(); });
    $('view-overview').addEventListener('click', (e) => {
      const g = e.target.closest('[data-goto]'); if (g) { window.switchTab(g.dataset.goto); return; }
      const k = e.target.closest('[data-kind]'); if (k) { e.preventDefault(); vx.kind = k.dataset.kind || null; renderOverview(); return; }
      const h = e.target.closest('[data-hist]'); if (h) { e.preventDefault(); window.openPriceHistory(h.dataset.hist, 1); return; }
      const w = e.target.closest('[data-wiki-open]'); if (w) { window.openWiki(w.dataset.wikiOpen, 1); return; }
      const r = e.target.closest('tr.row'); if (r) { vx.open = vx.open === r.dataset.row ? null : r.dataset.row; renderOverview(); }
    });
    setInterval(() => { if (!document.hidden && $('view-overview').style.display === 'block') loadOverview(); }, 60000);
    // map
    $('mx-go').addEventListener('click', findRoute);
    ['mx-from', 'mx-to'].forEach((id) => $(id).addEventListener('keydown', (e) => { if (e.key === 'Enter') findRoute(); }));
    $('mx-black').addEventListener('change', () => { drawMap(); });
    $('mx-red').addEventListener('change', () => { if (mx.route) findRoute(); });
    $('mx-stops').addEventListener('change', planTour);
    $('mx-round').addEventListener('change', planTour);
    document.querySelector('.sx-mapbtns').addEventListener('click', (e) => {
      const b = e.target.closest('[data-z]'); if (!b) return;
      if (b.dataset.z === 'in') zoomMap(0.7); else if (b.dataset.z === 'out') zoomMap(1.4); else fitMap(false);
    });
    const svg = $('mx-svg');
    svg.addEventListener('wheel', (e) => { e.preventDefault(); const [x, y] = svgPoint(e); zoomMap(e.deltaY > 0 ? 1.18 : 0.85, x, y); }, { passive: false });
    svg.addEventListener('pointerdown', (e) => { mx.drag = { x: e.clientX, y: e.clientY, vb: Object.assign({}, mx.vb), moved: false }; svg.setPointerCapture(e.pointerId); });
    svg.addEventListener('pointermove', (e) => {
      const d = mx.drag; if (!d || !mx.vb) return;
      const r = svg.getBoundingClientRect(), dx = (e.clientX - d.x) / r.width * d.vb.w, dy = (e.clientY - d.y) / r.height * d.vb.h;
      if (Math.abs(e.clientX - d.x) + Math.abs(e.clientY - d.y) > 4) { d.moved = true; svg.classList.add('drag'); }
      if (d.moved) { mx.vb = Object.assign({}, d.vb, { x: d.vb.x - dx, y: d.vb.y - dy }); applyView(); }
    });
    svg.addEventListener('pointerup', (e) => {
      const d = mx.drag; mx.drag = null; svg.classList.remove('drag');
      if (d && !d.moved) {
        const el = document.elementFromPoint(e.clientX, e.clientY);
        const p = el && el.closest && el.closest('[data-zone]');
        if (p) showZone(p.dataset.zone);
      }
    });
    $('view-map').addEventListener('click', (e) => {
      const a = e.target.closest('[data-zone-link], [data-route-to], [data-route-from]'); if (!a) return;
      e.preventDefault();
      if (a.dataset.zoneLink) { showZone(a.dataset.zoneLink); centerOn(a.dataset.zoneLink); return; }
      const name = mx.w.zones[a.dataset.routeTo || a.dataset.routeFrom].name;
      if (a.dataset.routeTo) $('mx-to').value = name; else $('mx-from').value = name;
      if ($('mx-from').value && $('mx-to').value) findRoute();
    });
    window.addEventListener('resize', () => { if (mx.vb && $('view-map').style.display === 'block') applyView(); });
    // links like /#wiki=T4_PLANKS also work while the page is already open
    window.addEventListener('hashchange', () => {
      const h = decodeURIComponent(location.hash || '');
      const w = /^#wiki=([A-Z0-9_@]+)$/.exec(h), hi = /^#history=([A-Z0-9_@]+?)(?:@q([1-5]))?$/.exec(h);
      if (w && w[1] !== wx.id) window.openWiki(w[1], 1);
      else if (hi && (!hx.item || hi[1] !== hx.item.id)) window.openPriceHistory(hi[1], hi[2] ? +hi[2] : 1);
    });

    // wiki
    searchBox('wx-search', 'wx-results', (it) => { wx.id = it.id; loadWiki(); });
    $('wx-quality').addEventListener('change', (e) => { wx.q = +e.target.value; loadWiki(); });
    $('wx-main').addEventListener('click', (e) => {
      const a = e.target.closest('[data-wiki], [data-hist], [data-alert], [data-map-zone]'); if (!a) return;
      e.preventDefault();
      if (a.dataset.wiki) { window.openWiki(a.dataset.wiki, 1); window.scrollTo(0, 0); }
      else if (a.dataset.hist) window.openPriceHistory(a.dataset.hist, wx.q);
      else if (a.dataset.alert) { window.switchTab('alerts'); prefillAlert({ id: a.dataset.alert, name: wx.data.name }, wx.q, '', wx.data.best_sale ? wx.data.best_sale.net_instant : null); }
      else if (a.dataset.mapZone) { window.switchTab('map'); loadWorld().then(() => { showZone(a.dataset.mapZone); centerOn(a.dataset.mapZone); }); }
    });

    $('px-save').addEventListener('click', saveProfile);
    $('view-profile').addEventListener('input', (e) => { if (e.target.closest('.sx-card') && e.target.id !== 'px-save') px.dirty = true; });
    $('view-profile').addEventListener('click', (e) => {
      const h = e.target.closest('a[data-hist]');
      if (h) { e.preventDefault(); window.openPriceHistory(h.dataset.hist, 1); }
    });
    fillIslandForm();
    segment('ix-part', 'p', (v) => { ix.part = v; store.set('islandPart', v); renderIsland(); });
    let it = null;
    $('view-island').addEventListener('change', (e) => {
      if (e.target.id === 'ix-only') { ix.onlyMine = e.target.checked; store.set('islandOnlyMine', ix.onlyMine); renderIsland(); return; }
      if (!e.target.closest('.sx-card')) return;
      readIslandForm(); clearTimeout(it); it = setTimeout(loadIsland, 150);
    });
    $('view-island').addEventListener('click', (e) => {
      const h = e.target.closest('a[data-hist]');
      if (h) { e.preventDefault(); window.openPriceHistory(h.dataset.hist, 1); return; }
      const g = e.target.closest('a[data-goto]');
      if (g) { e.preventDefault(); window.switchTab(g.dataset.goto); return; }
      if (e.target.id === 'ix-reset') {
        e.preventDefault(); ix.own = false; store.set('island', null); loadIsland();
      }
    });
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
    const mw = /^#wiki=([A-Z0-9_@]+)$/.exec(decodeURIComponent(location.hash || ''));
    if (m) window.openPriceHistory(m[1], m[2] ? +m[2] : 1);
    else if (mw) window.openWiki(mw[1], 1);
    else {
      // the last tab you had open; the overview on the very first visit
      const last = store.get('lastTab', null);
      if (last) window.switchTab(last);
      else window.switchTab('overview');
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
