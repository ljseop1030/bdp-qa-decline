/*
 * Mock API for the R2 query API spec (Phase 2).
 * Same paths, parameters, response shapes and error codes as the real API,
 * so app.js can switch to the real server without changes.
 * ES5 only: the course VM runs Firefox 31.
 *
 * Data:
 *   default            -> window.PILOT_DATA (pilot_data.js): R3 local pilot, 10 real sites,
 *                         plus the Q2 trial tables (site_trend_model / site_trend_summary)
 *   ?data=synthetic    -> the old synthetic series (no /api/trend)
 */
(function (global) {
  'use strict';

  var PILOT = (global.PILOT_DATA && !/(^|[?&])data=synthetic(&|$)/.test(global.location ? global.location.search : ''))
    ? global.PILOT_DATA : null;

  var DUMP_DATE = '2024-04-02';
  var LAST_MONTH = '2024-03';
  var TAIL_EXCLUDED = PILOT ? PILOT.excluded_months_tail : 2; /* answered_rate: last months have no full observation window */
  var MAX_SITES = 20;
  var METRICS = ['questions_count', 'new_users_count', 'answered_rate', 'downvote_ratio'];

  /* Example sites only. R3 decides the real 10-site list. */
  var SITES = [
    { site: 'datascience.stackexchange.com', group: 'A', first: '2014-05', base: 420, pre: -0.03, post: -0.032 },
    { site: 'codereview.stackexchange.com', group: 'A', first: '2012-01', base: 900, pre: -0.05, post: -0.030 },
    { site: 'unix.stackexchange.com', group: 'A', first: '2012-01', base: 1500, pre: -0.04, post: -0.028 },
    { site: 'tex.stackexchange.com', group: 'A', first: '2012-01', base: 1300, pre: -0.05, post: -0.022 },
    { site: 'ell.stackexchange.com', group: 'A', first: '2013-01', base: 1100, pre: -0.02, post: -0.035 },
    { site: 'cooking.stackexchange.com', group: 'B', first: '2012-01', base: 280, pre: -0.05, post: -0.006 },
    { site: 'outdoors.stackexchange.com', group: 'B', first: '2012-01', base: 120, pre: -0.04, post: -0.004 },
    { site: 'travel.stackexchange.com', group: 'B', first: '2012-01', base: 650, pre: -0.03, post: -0.008 },
    { site: 'gardening.stackexchange.com', group: 'B', first: '2012-01', base: 210, pre: -0.03, post: -0.005, gap: ['2016-07', '2016-09'] },
    { site: 'diy.stackexchange.com', group: 'B', first: '2012-01', base: 520, pre: -0.02, post: -0.007 },
    { site: 'genai.stackexchange.com', group: 'C', first: '2023-01', base: 60, pre: 0, post: -0.01 }
  ];

  function pad2(n) { return (n < 10 ? '0' : '') + n; }

  function monthIndex(s) {
    if (typeof s !== 'string' || !/^\d{4}-(0[1-9]|1[0-2])$/.test(s)) { return null; }
    return parseInt(s.substr(0, 4), 10) * 12 + (parseInt(s.substr(5, 2), 10) - 1);
  }

  function indexToMonth(i) { return Math.floor(i / 12) + '-' + pad2((i % 12) + 1); }

  /*
   * Last valid month per metric (R3 rule): counts and votes run to coverage_last_month,
   * answered_rate stops excluded_months_tail earlier (7-day answer window not complete).
   * R2 agreed (10/3) to put per-metric valid ranges in /api/meta; field name to confirm with R2.
   */
  function metricLastMonths() {
    var last = PILOT ? PILOT.coverage_last_month : LAST_MONTH;
    var out = {};
    METRICS.forEach(function (m) {
      out[m] = m === 'answered_rate' ? indexToMonth(monthIndex(last) - TAIL_EXCLUDED) : last;
    });
    return out;
  }

  function hash(str) {
    var h = 7;
    for (var i = 0; i < str.length; i++) { h = (h * 31 + str.charCodeAt(i)) % 2147483647; }
    return h;
  }

  function rng(seed) {
    var s = seed % 2147483647;
    if (s <= 0) { s += 2147483646; }
    return function () {
      s = (s * 16807) % 2147483647;
      return (s - 1) / 2147483646;
    };
  }

  function round(v, digits) {
    var f = Math.pow(10, digits);
    return Math.round(v * f) / f;
  }

  var START_2012 = monthIndex('2012-01');
  var COVID_FROM = monthIndex('2020-04');
  var COVID_TO = monthIndex('2021-06');
  var LLM_FROM = monthIndex('2022-12');
  var END_IDX = monthIndex(LAST_MONTH);

  function generate(s, metric) {
    var r = rng(hash(s.site + '|' + metric));
    var out = [];
    for (var i = monthIndex(s.first); i <= END_IDX; i++) {
      var years = (i - START_2012) / 12;
      var trend = Math.exp(s.pre * years);
      var covid = (i >= COVID_FROM && i <= COVID_TO) ? 1 + 0.08 * Math.sin(Math.PI * (i - COVID_FROM + 1) / (COVID_TO - COVID_FROM + 2)) : 1;
      var sinceLlm = i >= LLM_FROM ? i - LLM_FROM : 0;
      var post = Math.exp(s.post * sinceLlm);
      var noise = 1 + (r() - 0.5) * 0.12;
      var v;

      if (metric === 'questions_count') {
        v = Math.round(s.base * trend * covid * post * noise);
      } else if (metric === 'new_users_count') {
        v = Math.round(s.base * 1.8 * trend * covid * Math.pow(post, 0.8) * noise);
      } else if (metric === 'answered_rate') {
        v = 0.9 - 0.012 * years - sinceLlm * (s.group === 'A' ? 0.004 : 0.001) + (r() - 0.5) * 0.04;
        v = round(Math.max(0, Math.min(1, v)), 3);
        if (i > END_IDX - TAIL_EXCLUDED) { v = null; }
      } else {
        v = round(Math.max(0, 0.07 + 0.004 * years + (r() - 0.5) * 0.02), 3);
      }

      if (s.gap && i >= monthIndex(s.gap[0]) && i <= monthIndex(s.gap[1])) { v = null; }
      out.push({ month: indexToMonth(i), value: v });
    }

    var sum = 0;
    var n = 0;
    out.forEach(function (p) {
      if (p.month.substr(0, 4) === '2019' && p.value !== null) { sum += p.value; n++; }
    });
    var base2019 = n > 0 ? sum / n : null;
    out.forEach(function (p) {
      p.index_2019 = (base2019 && p.value !== null) ? round(p.value / base2019 * 100, 1) : null;
    });
    return out;
  }

  /* Pilot series. index_2019 follows R3's rule: 12 valid 2019 months and a positive mean, else null. */
  function pilotSeries(s, metric) {
    var col = PILOT.row_columns.indexOf(metric);
    var out = s.rows.map(function (r) { return { month: r[0], value: r[col] }; });
    var sum = 0;
    var n = 0;
    out.forEach(function (p) {
      if (p.month.substr(0, 4) === '2019' && p.value !== null) { sum += p.value; n++; }
    });
    var base2019 = (n === 12 && sum > 0) ? sum / n : null;
    out.forEach(function (p) {
      p.index_2019 = (base2019 && p.value !== null) ? round(p.value / base2019 * 100, 1) : null;
    });
    return out;
  }

  if (PILOT) {
    SITES = PILOT.sites.map(function (s) { return { site: s.site, group: s.domain_group, rows: s.rows }; });
  }

  var cache = {};
  function series(s, metric) {
    var key = s.site + '|' + metric;
    if (!cache[key]) { cache[key] = PILOT ? pilotSeries(s, metric) : generate(s, metric); }
    return cache[key];
  }

  function findSite(name) {
    for (var i = 0; i < SITES.length; i++) {
      if (SITES[i].site === name) { return SITES[i]; }
    }
    return null;
  }

  function fail(status, code, message) {
    return { status: status, body: { error: { code: code, message: message } } };
  }

  function ok(body) { return { status: 200, body: body }; }

  function shouldFail() {
    return global.location && /(^|[?&])fail=1(&|$)/.test(global.location.search);
  }

  function handleSites(params) {
    var list = SITES.filter(function (s) { return !params.group || s.group === params.group; });
    return ok({
      data: list.map(function (s) {
        var rows = series(s, 'questions_count');
        return {
          site: s.site,
          domain_group: s.group,
          first_month: rows[0].month,
          last_month: rows[rows.length - 1].month,
          n_months: rows.length
        };
      }),
      meta: { dump_date: DUMP_DATE, n_sites: list.length }
    });
  }

  function handleMetrics(params) {
    if (METRICS.indexOf(params.metric) < 0) {
      return fail(400, 'INVALID_METRIC', 'Unknown metric: ' + params.metric);
    }
    if (params.sites && params.group) {
      return fail(400, 'INVALID_RANGE', 'Use either sites or group, not both.');
    }

    var targets;
    if (params.group) {
      targets = SITES.filter(function (s) { return s.group === params.group; });
    } else {
      var names = (params.sites || '').split(',').filter(function (x) { return x !== ''; });
      if (names.length === 0) { return fail(400, 'INVALID_RANGE', 'No sites given.'); }
      if (names.length > MAX_SITES) {
        return fail(400, 'TOO_MANY_SITES', 'At most ' + MAX_SITES + ' sites per request.');
      }
      targets = [];
      for (var i = 0; i < names.length; i++) {
        var s = findSite(names[i]);
        if (!s) { return fail(404, 'SITE_NOT_FOUND', 'Site not loaded: ' + names[i]); }
        targets.push(s);
      }
    }

    var from = params.from ? monthIndex(params.from) : -Infinity;
    var to = params.to ? monthIndex(params.to) : Infinity;
    if (from === null || to === null) {
      return fail(400, 'INVALID_RANGE', 'Months must look like YYYY-MM.');
    }
    if (from > to) { return fail(400, 'INVALID_RANGE', 'from is after to.'); }

    var data = [];
    targets.forEach(function (s) {
      series(s, params.metric).forEach(function (p) {
        var idx = monthIndex(p.month);
        if (idx >= from && idx <= to) {
          data.push({ site: s.site, month: p.month, value: p.value, index_2019: p.index_2019 });
        }
      });
    });

    return ok({ data: data, meta: { metric: params.metric, n_rows: data.length, dump_date: DUMP_DATE } });
  }

  function handleMeta() {
    var meta = {
      dump_date: DUMP_DATE,
      n_sites_loaded: SITES.length,
      tables_last_updated: PILOT ? '2026-10-03T00:00:00Z' : '2026-10-01T00:00:00Z',
      metrics_available: METRICS.slice(),
      excluded_months_tail: TAIL_EXCLUDED,
      data_source: PILOT ? PILOT.source : 'synthetic',
      trend_available: !!PILOT,
      metric_last_month: metricLastMonths()
    };
    if (PILOT) {
      /* fields proposed by R3 for the API meta block */
      meta.coverage_end_exclusive = PILOT.coverage_end_exclusive;
      meta.coverage_last_month = PILOT.coverage_last_month;
      meta.coverage_status = PILOT.coverage_status;
      meta.answer_window_days = PILOT.answer_window_days;
    }
    return ok(meta);
  }

  /* /api/trend?site=..&metric=..  ->  rows of site_trend_model + one row of site_trend_summary as meta */
  function handleTrend(params) {
    if (!PILOT) { return fail(404, 'SITE_NOT_FOUND', '/api/trend needs pilot_data.js'); }
    if (METRICS.indexOf(params.metric) < 0) {
      return fail(400, 'INVALID_METRIC', 'Unknown metric: ' + params.metric);
    }
    var bySite = PILOT.trend[params.site];
    if (!bySite) { return fail(404, 'SITE_NOT_FOUND', 'No trend model for: ' + params.site); }
    var rows = bySite[params.metric];
    var summary = PILOT.trend_summary[params.site][params.metric];
    if (!rows || !summary) { return fail(404, 'SITE_NOT_FOUND', 'No trend model for this metric.'); }

    var c = PILOT.trend_columns;
    var data = rows.map(function (r) {
      var o = {};
      for (var i = 0; i < c.length; i++) { o[c[i]] = r[i]; }
      return o;
    });
    var meta = { site: params.site, metric: params.metric, model: summary.model_id };
    for (var k in summary) {
      if (summary.hasOwnProperty(k) && k !== 'model_id') { meta[k] = summary[k]; }
    }
    return ok({ data: data, meta: meta });
  }

  /* /api/group_summary?metric=..  ->  one row of q2_group_summary (A vs B DID). Proposed to R2, optional for the page. */
  function handleGroupSummary(params) {
    if (!PILOT || !PILOT.group_summary) { return fail(404, 'SITE_NOT_FOUND', 'No group summary.'); }
    if (METRICS.indexOf(params.metric) < 0) { return fail(400, 'INVALID_METRIC', 'Unknown metric: ' + params.metric); }
    var row = PILOT.group_summary[params.metric];
    return row ? ok({ data: row, meta: { metric: params.metric } }) : fail(404, 'SITE_NOT_FOUND', 'No group summary for this metric.');
  }

  function request(path, params) {
    params = params || {};
    if (shouldFail()) { return fail(503, 'QUERY_FAILED', 'Simulated Impala failure (?fail=1).'); }
    if (path === '/api/sites') { return handleSites(params); }
    if (path === '/api/metrics') { return handleMetrics(params); }
    if (path === '/api/meta') { return handleMeta(); }
    if (path === '/api/trend') { return handleTrend(params); }
    if (path === '/api/group_summary') { return handleGroupSummary(params); }
    return fail(404, 'SITE_NOT_FOUND', 'No mock for ' + path);
  }

  global.MockAPI = { request: request };
})(this);
