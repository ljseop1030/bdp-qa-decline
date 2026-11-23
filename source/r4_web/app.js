/*
 * Q&A community decline tracker: one-screen dashboard (Phase 1 mock).
 * ES5 only (Firefox 31): no fetch, no template literals, no let/const, no arrow functions.
 *
 * Data source
 *   default        -> MockAPI (mock_api.js)
 *   ?api=<base>    -> real API, e.g. ?api=http://localhost:5000
 *   ?fail=1        -> mock returns QUERY_FAILED (to check the error view)
 */
(function () {
  'use strict';

  var MAX_SITES = 20;
  var MIN_GROUP_SITES = 3;  /* fewer sites than this in a month: no median drawn */
  var LLM_MONTH = '2022-12';  /* ChatGPT released 2022-11-30; the line sits on the first full month after it (= Q2 baseline month) */

  var METRIC_INFO = {
    questions_count: { label: 'Questions per month', rate: false },
    new_users_count: { label: 'New users per month', rate: false },
    answered_rate: { label: 'Share of questions answered', rate: true },
    downvote_ratio: { label: 'Share of votes that are downvotes', rate: true }
  };

  var GROUP_INFO = {
    A: { name: 'A: LLM-strong domains', colors: ['#24539C', '#3F78CF', '#6E9BE0', '#173A70', '#5468B5'] },
    B: { name: 'B: experience domains', colors: ['#A3561A', '#D2832A', '#E2A949', '#7A4110', '#BF6E2C'] },
    C: { name: 'C: undecided', colors: ['#5E636B', '#8E949C', '#B1B6BD'] }
  };

  var ERROR_HELP = {
    INVALID_METRIC: 'That metric is not available. Pick one from the list.',
    INVALID_RANGE: 'Check the period. Use YYYY-MM, and From must not be after To.',
    TOO_MANY_SITES: 'Select ' + MAX_SITES + ' sites or fewer.',
    SITE_NOT_FOUND: 'A selected site is not loaded yet. Remove it or wait for the next load.',
    QUERY_FAILED: 'The query failed on the server. Try again in a moment.',
    NETWORK: 'The API did not respond. Check that the API server is running.',
    NOT_FOUND: 'This view is not served by the API yet.'
  };

  var config = readConfig();
  var state = {
    sites: [],
    selected: {},
    group: 'all',
    metric: 'questions_count',
    scale: 'index',
    from: '2019-01',
    to: '',
    excludedTail: 0,
    coverageLast: '',   /* last fully observed month from /api/meta; used when "To" is empty */
    metricLast: {},     /* per-metric last valid month from /api/meta (R3 rule, not decided by the UI) */
    answerWindow: 0,
    view: 'sites',      /* 'sites', 'group' (A vs B) or 'trend' (Q2) */
    trendSite: '',
    trendUnit: 'index'  /* 'index' or 'rate', from /api/trend meta.unit (Q2 v2) */
  };
  var chart = null;
  var requestSeq = 0;

  /* ---------- helpers ---------- */

  function $(id) { return document.getElementById(id); }

  function readConfig() {
    var m = /(^|[?&])api=([^&]+)/.exec(window.location.search);
    return { useMock: !m, apiBase: m ? decodeURIComponent(m[2]).replace(/\/$/, '') : '' };
  }

  function queryString(params) {
    var parts = [];
    for (var k in params) {
      if (params.hasOwnProperty(k) && params[k] !== '' && params[k] !== null && params[k] !== undefined) {
        parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(params[k]));
      }
    }
    return parts.length ? '?' + parts.join('&') : '';
  }

  function api(path, params, done) {
    if (config.useMock) {
      setTimeout(function () {
        var res = window.MockAPI.request(path, params);
        if (res.body && res.body.error) { done(res.body.error); } else { done(null, res.body); }
      }, 120);
      return;
    }
    var xhr = new XMLHttpRequest();
    xhr.open('GET', config.apiBase + path + queryString(params), true);
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) { return; }
      if (xhr.status === 0) { done({ code: 'NETWORK', message: 'No response from ' + config.apiBase }); return; }
      var body = null;
      try { body = JSON.parse(xhr.responseText); } catch (e) { body = null; }
      if (body && body.error) { done(body.error); return; }
      if (xhr.status >= 400 || !body) {
        done({ code: 'QUERY_FAILED', message: 'HTTP ' + xhr.status });
        return;
      }
      done(null, body);
    };
    xhr.send(null);
  }

  function monthOk(s) { return /^\d{4}-(0[1-9]|1[0-2])$/.test(s); }

  function colorFor(site) {
    var group = site.domain_group;
    var same = state.sites.filter(function (s) { return s.domain_group === group; });
    var palette = (GROUP_INFO[group] || GROUP_INFO.C).colors;
    return palette[same.indexOf(site) % palette.length];
  }

  function selectedNames() {
    return state.sites
      .filter(function (s) { return state.selected[s.site] && visible(s); })
      .map(function (s) { return s.site; });
  }

  function visible(site) { return state.group === 'all' || site.domain_group === state.group; }

  function shortName(name) { return name.replace('.stackexchange.com', ''); }

  function onIndexScale() {
    if (state.view === 'trend') { return state.trendUnit !== 'rate'; }
    return state.scale === 'index';
  }

  function formatValue(v, rate) {
    if (v === null || v === undefined) { return 'no data'; }
    if (state.view === 'trend' && state.trendUnit === 'rate') { return v.toFixed(1) + '%'; }
    if (onIndexScale()) { return v.toFixed(1); }
    return rate ? (v * 100).toFixed(1) + '%' : String(v);
  }

  function effectiveTo() { return state.to || state.coverageLast; }

  function shiftMonth(m, k) {
    var i = parseInt(m.substr(0, 4), 10) * 12 + parseInt(m.substr(5, 2), 10) - 1 + k;
    var mm = (i % 12) + 1;
    return Math.floor(i / 12) + '-' + (mm < 10 ? '0' : '') + mm;
  }

  /* Last valid month of a metric. Taken from /api/meta; the fallback only covers an API without the field. */
  function metricLastMonth(metric) {
    if (state.metricLast[metric]) { return state.metricLast[metric]; }
    if (!state.coverageLast) { return ''; }
    return metric === 'answered_rate' && state.excludedTail ? shiftMonth(state.coverageLast, -state.excludedTail) : state.coverageLast;
  }

  /* One reference month for the whole summary table: the earliest of "To", the metric's last valid month and the data end. */
  function referenceMonth(rows) {
    var dataEnd = '';
    rows.forEach(function (r) { if (r.month > dataEnd) { dataEnd = r.month; } });
    var ref = dataEnd;
    [effectiveTo(), metricLastMonth(state.metric)].forEach(function (m) {
      if (m && (!ref || m < ref)) { ref = m; }
    });
    return ref;
  }

  function inPeriod(month) {
    var to = effectiveTo();
    return (!state.from || month >= state.from) && (!to || month <= to);
  }

  function show(id, on) {
    var el = $(id);
    el.className = on ? el.className.replace(/\s*is-hidden/g, '') : (el.className.indexOf('is-hidden') < 0 ? el.className + ' is-hidden' : el.className);
  }

  /* ---------- messages ---------- */

  function showError(err) {
    var box = $('error');
    var help = ERROR_HELP[err.code] || 'Something went wrong.';
    box.innerHTML = '';
    var strong = document.createElement('strong');
    strong.appendChild(document.createTextNode(help));
    box.appendChild(strong);
    var detail = document.createElement('span');
    detail.className = 'error-detail';
    detail.appendChild(document.createTextNode(' ' + err.code + (err.message ? ': ' + err.message : '')));
    box.appendChild(detail);
    box.className = 'error is-visible';
  }

  function clearError() { $('error').className = 'error'; }

  function setNotes(lines) {
    var ul = $('notes');
    ul.innerHTML = '';
    lines.forEach(function (text) {
      var li = document.createElement('li');
      li.appendChild(document.createTextNode(text));
      ul.appendChild(li);
    });
  }

  function setLoading(on) { $('chart-wrap').className = on ? 'chart-wrap is-loading' : 'chart-wrap'; }

  /* ---------- controls ---------- */

  function renderSiteList() {
    var list = $('site-list');
    list.innerHTML = '';
    state.sites.forEach(function (s) {
      if (!visible(s)) { return; }
      var label = document.createElement('label');
      label.className = 'site';
      var box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = !!state.selected[s.site];
      box.addEventListener('change', function () {
        state.selected[s.site] = box.checked;
        load();
      });
      var swatch = document.createElement('span');
      swatch.className = 'swatch';
      swatch.style.backgroundColor = colorFor(s);
      var name = document.createElement('span');
      name.className = 'site-name';
      name.appendChild(document.createTextNode(shortName(s.site)));
      var tag = document.createElement('span');
      tag.className = 'tag tag-' + s.domain_group;
      tag.appendChild(document.createTextNode(s.domain_group));
      label.appendChild(box);
      label.appendChild(swatch);
      label.appendChild(name);
      label.appendChild(tag);
      list.appendChild(label);
    });
    $('site-count').innerHTML = selectedNames().length + ' of ' + MAX_SITES + ' max';
  }

  function bindControls() {
    var groups = document.getElementsByName('group');
    for (var i = 0; i < groups.length; i++) {
      groups[i].addEventListener('change', function (e) {
        state.group = e.target.value;
        renderSiteList();
        load();
      });
    }
    $('metric').addEventListener('change', function (e) {
      state.metric = e.target.value;
      load();
    });
    var views = document.getElementsByName('view');
    for (var v = 0; v < views.length; v++) {
      views[v].addEventListener('change', function (e) {
        state.view = e.target.value;
        applyView();
        load();
      });
    }
    $('trend-site').addEventListener('change', function (e) {
      state.trendSite = e.target.value;
      load();
    });
    var scales = document.getElementsByName('scale');
    for (var j = 0; j < scales.length; j++) {
      scales[j].addEventListener('change', function (e) {
        state.scale = e.target.value;
        load();
      });
    }
    $('period-form').addEventListener('submit', function (e) {
      e.preventDefault();
      applyPeriod();
    });
  }

  function applyPeriod() {
    var from = $('from').value.replace(/\s/g, '');
    var to = $('to').value.replace(/\s/g, '');
    if ((from && !monthOk(from)) || (to && !monthOk(to))) {
      showError({ code: 'INVALID_RANGE', message: 'got "' + from + '" and "' + to + '"' });
      return;
    }
    state.from = from;
    state.to = to;
    load();
  }

  /* ---------- chart ---------- */

  var markerPlugin = {
    afterDraw: function (c) {
      var xScale = c.scales['x-axis-0'];
      var yScale = c.scales['y-axis-0'];
      if (!xScale || !yScale) { return; }
      var ctx = c.chart.ctx;
      ctx.save();

      if (onIndexScale() && yScale.min <= 100 && yScale.max >= 100) {
        var y = yScale.getPixelForValue(100);
        ctx.strokeStyle = '#9AA1AA';
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 3]);
        ctx.beginPath();
        ctx.moveTo(xScale.left, y);
        ctx.lineTo(xScale.right, y);
        ctx.stroke();
      }

      var idx = c.data.labels.indexOf(LLM_MONTH);
      if (idx >= 0) {
        var x = xScale.getPixelForValue(LLM_MONTH, idx);
        ctx.strokeStyle = '#1F2328';
        ctx.lineWidth = 1;
        ctx.setLineDash([5, 4]);
        ctx.beginPath();
        ctx.moveTo(x, yScale.top);
        ctx.lineTo(x, yScale.bottom);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = '#1F2328';
        ctx.font = '12px "DejaVu Sans", "Helvetica Neue", Arial, sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText('ChatGPT released 30 Nov 2022; line = Dec 2022 (analysis start)', x - 6, yScale.top + 14);
      }
      ctx.restore();
    }
  };

  function ensureChart() {
    if (chart) { return chart; }
    chart = new Chart($('chart').getContext('2d'), {
      type: 'line',
      data: { labels: [], datasets: [] },
      plugins: [markerPlugin],
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 250 },
        spanGaps: false,
        legend: { display: false, labels: { boxWidth: 14, filter: function (item) { return item.text !== ''; } } },
        elements: { point: { radius: 0, hitRadius: 6, hoverRadius: 3 }, line: { tension: 0, borderWidth: 1.75 } },
        tooltips: {
          mode: 'index',
          intersect: false,
          callbacks: {
            label: function (item, data) {
              var ds = data.datasets[item.datasetIndex];
              var raw = ds.data[item.index];
              var name = ds.tooltipLabel || shortName(ds.label);
              return ' ' + name + ': ' + formatValue(raw, METRIC_INFO[state.metric].rate);
            }
          }
        },
        scales: {
          xAxes: [{ gridLines: { display: false }, ticks: { autoSkip: true, maxTicksLimit: 12, maxRotation: 0 } }],
          yAxes: [{
            gridLines: { color: '#E6E9ED', zeroLineColor: '#C9CED4' },
            ticks: {
              callback: function (v) {
                if (state.view === 'trend' && state.trendUnit === 'rate') { return v + '%'; }
                if (onIndexScale()) { return v; }
                return METRIC_INFO[state.metric].rate ? Math.round(v * 100) + '%' : v;
              }
            }
          }]
        }
      }
    });
    return chart;
  }

  function draw(rows) {
    var names = selectedNames();
    var monthSet = {};
    var bySite = {};
    rows.forEach(function (r) {
      monthSet[r.month] = true;
      if (!bySite[r.site]) { bySite[r.site] = {}; }
      bySite[r.site][r.month] = state.scale === 'index' ? r.index_2019 : r.value;
    });
    var labels = Object.keys(monthSet).sort();

    var c = ensureChart();
    c.options.legend.display = false;
    c.data.labels = labels;
    c.data.datasets = names.map(function (name) {
      var site = state.sites.filter(function (s) { return s.site === name; })[0];
      var color = colorFor(site);
      var values = bySite[name] || {};
      return {
        label: name,
        data: labels.map(function (m) { return values.hasOwnProperty(m) ? values[m] : null; }),
        borderColor: color,
        backgroundColor: color,
        fill: false
      };
    });
    c.update();

    $('y-label').innerHTML = state.scale === 'index'
      ? METRIC_INFO[state.metric].label + ', 2019 average = 100'
      : METRIC_INFO[state.metric].label;
  }

  function summarize(rows) {
    var ref = referenceMonth(rows);
    var at = {};
    var lastValid = {};
    rows.forEach(function (r) {
      if (r.month === ref) { at[r.site] = r; }
      if (r.value !== null && (!lastValid[r.site] || r.month > lastValid[r.site])) { lastValid[r.site] = r.month; }
    });
    $('summary-caption').innerHTML = ref
      ? 'Index in ' + ref + ' for every site (2019 average = 100). ' + ref + ' is the last valid month of this metric in the period.'
      : 'No months in the selected period.';

    function indexOf(name) {
      var r = at[name];
      return (r && r.value !== null && r.index_2019 !== null) ? r.index_2019 : null;
    }
    function noteOf(name) {
      var r = at[name];
      if (!r || r.value === null) {
        return 'No data in ' + ref + (lastValid[name] ? '; last valid ' + lastValid[name] : '');
      }
      if (r.index_2019 === null) { return 'No 2019 baseline'; }
      return '';
    }

    var tbody = $('summary-body');
    tbody.innerHTML = '';
    var names = selectedNames().slice().sort(function (a, b) {
      var ia = indexOf(a);
      var ib = indexOf(b);
      if (ia === null && ib === null) { return 0; }
      if (ia === null) { return 1; }
      if (ib === null) { return -1; }
      return ia - ib;
    });
    names.forEach(function (name) {
      var site = state.sites.filter(function (s) { return s.site === name; })[0];
      var idx = indexOf(name);
      var tr = document.createElement('tr');
      var cells = [shortName(name), site.domain_group, idx === null ? 'N/A' : idx.toFixed(1), noteOf(name)];
      cells.forEach(function (text, i) {
        var td = document.createElement('td');
        if (i === 1) { td.className = 'tag-cell'; }
        if (i === 2) { td.className = 'num'; }
        if (i === 3) { td.className = 'note-cell'; }
        td.appendChild(document.createTextNode(text));
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }

  function buildNotes(rows) {
    var notes = [];
    var noBase = {};
    var hasGap = {};
    var last = metricLastMonth(state.metric);
    var hasTail = false;
    rows.forEach(function (r) {
      var inTail = last && r.month > last;
      if (inTail) { hasTail = true; }
      if (r.index_2019 === null && r.value !== null) { noBase[r.site] = true; }
      if (r.value === null && !inTail) { hasGap[r.site] = true; }
    });
    var nb = Object.keys(noBase).map(shortName);
    if (nb.length && state.scale === 'index') {
      notes.push('No 2019 baseline, so no index line for: ' + nb.join(', ') + '. Switch to raw values to see them.');
    }
    var gaps = Object.keys(hasGap).map(shortName);
    if (gaps.length) {
      notes.push('Breaks in a line are months with no data, not zero: ' + gaps.join(', ') + '.');
    }
    if (state.metric === 'answered_rate' && hasTail) {
      notes.push('Answered rate stops at ' + last + ': later questions have not had the full ' +
        (state.answerWindow ? state.answerWindow + '-day ' : '') + 'answer window. Cutoff from the API (R3 rule), not set by this page.');
    }
    setNotes(notes);
  }

  /* ---------- Q2 trend view ---------- */

  function drawTrend(rows) {
    var site = state.sites.filter(function (s) { return s.site === state.trendSite; })[0];
    var color = site ? colorFor(site) : '#24539C';
    var labels = rows.map(function (r) { return r.month; });
    var rate = state.trendUnit === 'rate';
    var pick = function (key) {
      return rows.map(function (r) {
        var v = r[key];
        return (rate && v !== null && v !== undefined) ? Math.round(v * 10000) / 100 : v;  /* ratio -> % */
      });
    };

    var c = ensureChart();
    c.options.legend.display = true;
    c.data.labels = labels;
    c.data.datasets = [
      { label: 'Placebo range', tooltipLabel: 'Range high', data: pick('band_high'), borderColor: 'rgba(0,0,0,0)',
        backgroundColor: 'rgba(120,128,138,0.18)', pointRadius: 0, fill: '+1' },
      { label: '', tooltipLabel: 'Range low', data: pick('band_low'), borderColor: 'rgba(0,0,0,0)',
        backgroundColor: 'rgba(120,128,138,0.18)', pointRadius: 0, fill: false },
      { label: 'Pre-LLM trend (fit to Nov 2022, then extended)', tooltipLabel: 'Trend', data: pick('counterfactual'),
        borderColor: '#5E636B', backgroundColor: '#5E636B', borderDash: [6, 4], fill: false },
      { label: 'Actual', tooltipLabel: 'Actual', data: pick('actual'),
        borderColor: color, backgroundColor: color, fill: false }
    ];
    c.update();
    $('y-label').innerHTML = METRIC_INFO[state.metric].label + (rate ? ', % of questions' : ', 2019 average = 100');
  }

  function pct(v, pp) {
    if (v === null || v === undefined) { return 'n/a'; }
    return (v > 0 ? '+' : '') + v.toFixed(1) + (pp ? ' pp' : '%');
  }

  function summarizeTrend(meta) {
    var pp = meta.unit === 'rate';  /* rate metrics: gaps are percentage points */
    var gapWord = pp ? 'pp' : '%';
    var lines = [
      ['Site', shortName(meta.site) + ' (group ' + meta.domain_group + ')'],
      ['Model', meta.model + ', fit ' + meta.fit_start + ' to ' + meta.fit_end],
      ['Pre-LLM trend', pct(meta.pre_trend_pct_per_year, pp) + ' per year'],
      ['Gap, first 12 months (' + meta.post_start + ' to ' + shiftMonth(meta.post_start, 11) + ')', pct(meta.post_gap_12m, pp)],
      ['Gap, full period (' + meta.post_start + ' to ' + meta.post_end + ')', pct(meta.post_gap_pct, pp)],
      ['Placebo gaps (fake breaks 2020 to 2021)', pct(meta.placebo_min, pp) + ' to ' + pct(meta.placebo_max, pp)],
      ['Gap outside placebo range', meta.beyond_placebo ? 'yes' : 'no'],
      ['Detected change point', meta.break_month + ' (fit improves ' + Math.round(meta.break_strength * 100) + '%)'],
      ['Fit months', meta.n_fit_months + (meta.n_zero_months_excluded ? ' (' + meta.n_zero_months_excluded + ' zero months left out)' : '')]
    ];
    if (meta.note) { lines.push(['Note', meta.note]); }
    var tbody = $('trend-body');
    tbody.innerHTML = '';
    lines.forEach(function (l, i) {
      var tr = document.createElement('tr');
      var th = document.createElement('td');
      th.className = 'summary-trend-label';
      th.appendChild(document.createTextNode(l[0]));
      var td = document.createElement('td');
      if (i === 6) { td.className = meta.beyond_placebo ? 'flag-yes' : 'flag-no'; }
      td.appendChild(document.createTextNode(l[1]));
      tr.appendChild(th);
      tr.appendChild(td);
      tbody.appendChild(tr);
    });
  }

  function trendNotes(meta) {
    var notes = [
      'Dashed line: trend fitted on ' + meta.fit_start + ' to ' + meta.fit_end + ', then extended. Grey band: where the actual line would sit if the method erred as it did at the fake break dates.',
      'A gap only counts as a break if it falls outside the placebo range.',
      'Pilot of 10 sites, exploratory. Not evidence of an LLM effect.'
    ];
    if (meta.beyond_placebo && meta.post_gap_pct > meta.placebo_max) {
      notes.push('Here the gap is above the placebo range: after Nov 2022 this metric fell less (or rose more) than the pre-LLM trend implied.');
    } else if (meta.beyond_placebo) {
      notes.push('Here the gap is below the placebo range: after Nov 2022 this metric fell more than the pre-LLM trend implied.');
    }
    setNotes(notes);
  }

  function loadTrend() {
    if (!state.trendSite) { setNotes(['No site with a trend model.']); return; }
    var seq = ++requestSeq;
    setLoading(true);
    api('/api/trend', { site: state.trendSite, metric: state.metric }, function (err, body) {
      if (seq !== requestSeq) { return; }
      setLoading(false);
      if (err && err.code === 'NOT_FOUND') {
        /* R2 has not added /api/trend yet (planned): say so instead of a generic failure */
        clearError();
        drawTrend([]);
        setNotes(['Q2 trend results are not served by this API yet (/api/trend). Use the mock (no ?api=) to preview this view.']);
        return;
      }
      if (err) { showError(err); return; }
      clearError();
      state.trendUnit = body.meta.unit === 'rate' ? 'rate' : 'index';
      drawTrend(body.data.filter(function (r) { return inPeriod(r.month); }));
      summarizeTrend(body.meta);
      trendNotes(body.meta);
    });
  }

  /* ---------- Domain group view: A/B median + interquartile band ---------- */

  function quantile(sorted, q) {
    var pos = (sorted.length - 1) * q;
    var lo = Math.floor(pos);
    var hi = Math.ceil(pos);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
  }

  function round2(v) { return Math.round(v * 100) / 100; }

  /* rows of one group -> { month: { n, q1, med, q3 } }. Missing values (null) are skipped, not counted as 0. */
  function groupStats(rows) {
    var byMonth = {};
    var sitesSeen = {};
    rows.forEach(function (r) {
      sitesSeen[r.site] = true;
      var v = state.scale === 'index' ? r.index_2019 : r.value;
      if (v === null || v === undefined) { return; }
      if (!byMonth[r.month]) { byMonth[r.month] = []; }
      byMonth[r.month].push(v);
    });
    var out = {};
    for (var m in byMonth) {
      if (byMonth.hasOwnProperty(m) && byMonth[m].length >= MIN_GROUP_SITES) {
        var vals = byMonth[m].slice().sort(function (a, b) { return a - b; });
        out[m] = { n: vals.length, q1: quantile(vals, 0.25), med: quantile(vals, 0.5), q3: quantile(vals, 0.75) };
      }
    }
    return { months: out, nSites: Object.keys(sitesSeen).length };
  }

  function drawGroups(a, b) {
    var monthSet = {};
    [a, b].forEach(function (g) { for (var m in g.months) { if (g.months.hasOwnProperty(m) && inPeriod(m)) { monthSet[m] = true; } } });
    var labels = Object.keys(monthSet).sort();
    var rate = METRIC_INFO[state.metric].rate && state.scale !== 'index';
    function series(g, key) {
      return labels.map(function (m) {
        var s = g.months[m];
        return s ? round2(s[key]) : null;
      });
    }
    function sets(g, name, color, band) {
      return [
        { label: name + ': middle 50% of sites', tooltipLabel: name + ' Q3', data: series(g, 'q3'), borderColor: 'rgba(0,0,0,0)',
          backgroundColor: band, pointRadius: 0, fill: '+1' },
        { label: '', tooltipLabel: name + ' Q1', data: series(g, 'q1'), borderColor: 'rgba(0,0,0,0)',
          backgroundColor: band, pointRadius: 0, fill: false },
        { label: name + ' median (' + g.nSites + ' sites)', tooltipLabel: name + ' median', data: series(g, 'med'),
          borderColor: color, backgroundColor: color, borderWidth: 2.5, fill: false }
      ];
    }
    var c = ensureChart();
    c.options.legend.display = true;
    c.data.labels = labels;
    c.data.datasets = sets(a, 'A', '#24539C', 'rgba(63,120,207,0.18)').concat(sets(b, 'B', '#A3561A', 'rgba(210,131,42,0.20)'));
    c.update();
    $('y-label').innerHTML = METRIC_INFO[state.metric].label + (state.scale === 'index' ? ', 2019 average = 100' : (rate ? ', %' : ''));
  }

  function summarizeGroups(a, b) {
    var ref = '';
    [a, b].forEach(function (g) { for (var m in g.months) { if (g.months.hasOwnProperty(m) && inPeriod(m) && m > ref) { ref = m; } } });
    var last = metricLastMonth(state.metric);
    if (last && ref > last) { ref = last; }
    $('group-caption').innerHTML = ref ? 'Across-site spread in ' + ref + ' (the last valid month in the period)' : 'No months in the selected period.';
    var tbody = $('group-body');
    tbody.innerHTML = '';
    [['A', a], ['B', b]].forEach(function (pair) {
      var s = pair[1].months[ref];
      var tr = document.createElement('tr');
      var cells = [pair[0], s ? String(s.n) + ' of ' + pair[1].nSites : '0 of ' + pair[1].nSites,
        s ? formatValue(s.med, METRIC_INFO[state.metric].rate) : 'N/A',
        s ? formatValue(s.q1, METRIC_INFO[state.metric].rate) + ' to ' + formatValue(s.q3, METRIC_INFO[state.metric].rate) : 'N/A'];
      cells.forEach(function (text, i) {
        var td = document.createElement('td');
        if (i === 0) { td.className = 'tag-cell'; }
        if (i > 0) { td.className = 'num'; }
        td.appendChild(document.createTextNode(text));
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }

  function groupNotes(a, b, gs) {
    var notes = [
      'Line = median across the sites of a group; band = Q1 to Q3 of those sites. A month needs at least ' + MIN_GROUP_SITES + ' sites with data, otherwise the line breaks.',
      'Sites with no data in a month are left out of that month, not counted as zero.'
    ];
    if (gs) {
      var u = gs.unit === 'pct' ? '%' : ' pp';
      notes.push('Q2 A vs B difference-in-differences (first 12 months after Nov 2022): ' + (gs.did_12m > 0 ? '+' : '') + gs.did_12m.toFixed(1) + u +
        ', permutation p = ' + (gs.did_12m_perm_p === null ? 'n/a' : gs.did_12m_perm_p.toFixed(2)) +
        '; placebo range ' + gs.did_placebo_min.toFixed(1) + ' to ' + gs.did_placebo_max.toFixed(1) + u +
        '; ' + (gs.did_beyond_placebo ? 'outside' : 'inside') + ' the placebo range.');
    }
    setNotes(notes);
  }

  function loadGroups() {
    var seq = ++requestSeq;
    setLoading(true);
    var params = { metric: state.metric, from: state.from, to: effectiveTo() };
    var res = {};
    var pending = 3;
    function finish() {
      if (--pending > 0) { return; }
      if (seq !== requestSeq) { return; }
      setLoading(false);
      if (res.err) { showError(res.err); return; }
      clearError();
      var a = groupStats(res.A);
      var b = groupStats(res.B);
      drawGroups(a, b);
      summarizeGroups(a, b);
      groupNotes(a, b, res.gs);
    }
    ['A', 'B'].forEach(function (g) {
      api('/api/metrics', { group: g, metric: state.metric, from: params.from, to: params.to }, function (err, body) {
        if (err) { res.err = err; } else { res[g] = body.data; }
        finish();
      });
    });
    /* A vs B DID summary: optional, the view works without it */
    api('/api/group_summary', { metric: state.metric }, function (err, body) {
      res.gs = err ? null : body.data;
      finish();
    });
  }

  function applyView() {
    var trend = state.view === 'trend';
    var grp = state.view === 'group';
    show('trend-site-box', trend);
    show('summary-group', grp);
    show('summary-trend', trend);
    show('pilot-flag', trend);
    show('group-box', state.view === 'sites');
    show('sites-box', state.view === 'sites');
    show('scale-box', !trend);
    show('summary-sites', state.view === 'sites');
  }

  /* ---------- load ---------- */

  function load() {
    if (state.view === 'trend') { loadTrend(); return; }
    if (state.view === 'group') { loadGroups(); return; }
    renderSiteList();
    var names = selectedNames();
    if (names.length === 0) {
      clearError();
      draw([]);
      summarize([]);
      setNotes(['Select at least one site to draw a line.']);
      return;
    }
    if (names.length > MAX_SITES) {
      showError({ code: 'TOO_MANY_SITES', message: names.length + ' selected' });
      return;
    }
    var seq = ++requestSeq;
    setLoading(true);
    api('/api/metrics', { sites: names.join(','), metric: state.metric, from: state.from, to: effectiveTo() }, function (err, body) {
      if (seq !== requestSeq) { return; }
      setLoading(false);
      if (err) { showError(err); return; }
      clearError();
      draw(body.data);
      summarize(body.data);
      buildNotes(body.data);
    });
  }

  function fillTrendSites() {
    var sel = $('trend-site');
    sel.innerHTML = '';
    state.sites.forEach(function (s) {
      var o = document.createElement('option');
      o.value = s.site;
      o.appendChild(document.createTextNode(shortName(s.site) + ' (' + s.domain_group + ')'));
      sel.appendChild(o);
    });
    state.trendSite = state.sites.length ? state.sites[0].site : '';
  }

  function start() {
    $('source').innerHTML = config.useMock ? 'Mock data' : 'Live API';
    $('source').className = config.useMock ? 'source is-mock' : 'source';
    $('from').value = state.from;
    bindControls();
    applyView();

    api('/api/meta', {}, function (err, meta) {
      if (err) { $('dump-date').innerHTML = 'unavailable'; showError(err); return; }
      $('dump-date').innerHTML = meta.dump_date;
      state.excludedTail = meta.excluded_months_tail || 0;
      state.coverageLast = meta.coverage_last_month || '';
      state.metricLast = meta.metric_last_month || {};
      state.answerWindow = meta.answer_window_days || 0;
      if (config.useMock && meta.data_source && meta.data_source !== 'synthetic') {
        $('source').innerHTML = 'Pilot data (mock API)';
      }
      if (meta.trend_available === false) { show('view-trend-opt', false); }
      if (state.coverageLast) { $('to').placeholder = state.coverageLast; }

      api('/api/sites', {}, function (err2, body) {
        if (err2) { showError(err2); return; }
        state.sites = body.data;
        fillTrendSites();
        var picked = { A: 0, B: 0 };
        state.sites.forEach(function (s) {
          if (picked[s.domain_group] !== undefined && picked[s.domain_group] < 2) {
            state.selected[s.site] = true;
            picked[s.domain_group]++;
          }
        });
        load();
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
