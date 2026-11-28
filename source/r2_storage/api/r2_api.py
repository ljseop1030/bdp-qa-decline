# -*- coding: utf-8 -*-
"""R2 query API (Stack Exchange Q&A decline project).

Runs inside the course VM with the system Python 2.6 standard library only.
Impala is queried by calling `impala-shell -B` (port 21000); no client
library, no pip.  Also runs on Python 2.7 / 3.x (used for local tests).

Endpoints (spec v0 + 11/21 decisions + 11/26 Q2 serving):
  GET /api/meta
  GET /api/sites?group=A|B|C
  GET /api/metrics?metric=<m>&sites=a,b|group=A&from=YYYY-MM&to=YYYY-MM
  GET /api/trend?site=<host>&metric=<m>[&from=YYYY-MM&to=YYYY-MM]
      data = site_trend_model rows of the primary model, meta = its
      site_trend_summary row (R4 Q2 v2: v1 columns + unit, is_primary, ...)
  GET /api/group_summary?metric=<m>     (q2_group_summary, A vs B DID)
Errors: {"error": {"code", "message"}}
  400 INVALID_METRIC / INVALID_RANGE / TOO_MANY_SITES, 404 SITE_NOT_FOUND,
  503 QUERY_FAILED  (unknown path: 404 NOT_FOUND)

Usage:  python r2_api.py [--port 8100] [--impalad localhost:21000]
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from optparse import OptionParser

try:  # Python 2
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
    from SocketServer import ThreadingMixIn
    from urlparse import urlparse, parse_qs
except ImportError:  # Python 3 (local testing only)
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn
    from urllib.parse import urlparse, parse_qs

DUMP_DATE = "2024-04-02"
METRICS = ["questions_count", "new_users_count", "answered_rate", "downvote_ratio"]
COUNT_METRICS = ("questions_count", "new_users_count")
GROUPS = ("A", "B", "C")
MAX_SITES = 20
COVERAGE_LAST_MONTH = "2024-02"
METRIC_LAST_MONTH = {
    "questions_count": "2024-02",
    "new_users_count": "2024-02",
    "answered_rate": "2024-01",
    "downvote_ratio": "2024-02",
}
SITE_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]*$")
MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
MODEL_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

# Python 2.6's json writes floats with repr() (0.1 -> 0.10000000000000001).
# Its encoder is pure Python, so FLOAT_REPR can shorten that to 15 digits.
try:
    import json.encoder as _json_encoder
    _json_encoder.FLOAT_REPR = lambda f: "%.15g" % f
except Exception:
    pass

CONF = {
    "impala_shell": os.environ.get("R2_IMPALA_SHELL", "impala-shell"),
    "impalad": "localhost:21000",
    "db": "qa_decline",
    "serving_table": "site_metrics_monthly",
    "raw_table": "posts",
    "trend_model_table": "site_trend_model",
    "trend_summary_table": "site_trend_summary",
    "group_summary_table": "q2_group_summary",
    "ledger": "/data/work/r2/ledger.json",
    "query_timeout": 120,
    "cache_ttl": 60,
}


class ApiError(Exception):
    def __init__(self, status, code, message):
        Exception.__init__(self, message)
        self.status = status
        self.code = code
        self.message = message


class QueryError(Exception):
    pass


# ---------------------------------------------------------------- Impala
def run_query(sql):
    """Run one SQL statement through impala-shell; return list of row lists.
    The string NULL becomes None."""
    cmd = [CONF["impala_shell"], "-i", CONF["impalad"], "-B",
           "--output_delimiter=\t", "-q", sql]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
    except OSError:
        raise QueryError("impala-shell not found")
    timer = threading.Timer(CONF["query_timeout"], _kill, [proc])
    timer.start()
    try:
        out, err = proc.communicate()
    finally:
        timer.cancel()
    if not isinstance(out, str):  # Python 3
        out = out.decode("utf-8", "replace")
        err = err.decode("utf-8", "replace")
    if proc.returncode != 0:
        msg = err.strip().splitlines()
        raise QueryError(msg[-1] if msg else "impala-shell exit %d" % proc.returncode)
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        rows.append([None if c == "NULL" else c for c in line.split("\t")])
    return rows


def _kill(proc):
    try:
        proc.kill()
    except Exception:
        pass


def tbl(name):
    return "%s.%s" % (CONF["db"], name)


# ---------------------------------------------------------------- cache
_cache = {}
_cache_lock = threading.Lock()


def cached(key, loader):
    now = time.time()
    _cache_lock.acquire()
    try:
        hit = _cache.get(key)
        if hit and now - hit[0] < CONF["cache_ttl"]:
            return hit[1]
    finally:
        _cache_lock.release()
    value = loader()
    _cache_lock.acquire()
    try:
        _cache[key] = (time.time(), value)
    finally:
        _cache_lock.release()
    return value


def load_sites():
    sql = ("SELECT site, domain_group, "
           "MIN(CASE WHEN questions_count IS NOT NULL THEN month END), "
           "MAX(CASE WHEN questions_count IS NOT NULL THEN month END), "
           "COUNT(questions_count) "
           "FROM %s GROUP BY site, domain_group ORDER BY site" % tbl(CONF["serving_table"]))
    sites = []
    for r in run_query(sql):
        sites.append({
            "site": r[0],
            "domain_group": r[1],
            "first_month": r[2],
            "last_month": r[3],
            "n_months": int(r[4]) if r[4] is not None else 0,
        })
    return sites


def get_sites():
    return cached("sites", load_sites)


def load_n_sites_loaded():
    rows = run_query("SHOW PARTITIONS %s" % tbl(CONF["raw_table"]))
    names = {}
    for r in rows:
        if r and r[0] and r[0] != "Total":
            names[r[0]] = 1
    return len(names)


def load_tables_last_updated():
    """Latest registration time from the R2 ledger (None if no ledger)."""
    try:
        f = open(CONF["ledger"])
        try:
            ledger = json.load(f)
        finally:
            f.close()
    except (IOError, ValueError):
        return None
    times = [u.get("registered_at") for u in ledger.get("units", {}).values()
             if u.get("registered_at")]
    times.extend([v for v in ledger.get("serving", {}).values() if v])
    return max(times) if times else None


# ---------------------------------------------------------------- handlers
def parse_month(value, name):
    if value is None or value == "":
        return None
    if not MONTH_RE.match(value):
        raise ApiError(400, "INVALID_RANGE", "%s must be YYYY-MM" % name)
    return value


def api_meta(params):
    n_sites = cached("n_sites_loaded", load_n_sites_loaded)
    return {
        "dump_date": DUMP_DATE,
        "n_sites_loaded": n_sites,
        "tables_last_updated": load_tables_last_updated(),
        "metrics_available": list(METRICS),
        "coverage_last_month": COVERAGE_LAST_MONTH,
        "metric_last_month": dict(METRIC_LAST_MONTH),
        "trend_available": cached("trend_available", load_trend_available),
    }


def api_sites(params):
    group = first(params, "group")
    if group is not None and group not in GROUPS:
        raise ApiError(400, "INVALID_RANGE", "group must be one of A, B, C")
    sites = get_sites()
    if group is not None:
        sites = [s for s in sites if s["domain_group"] == group]
    return {"data": sites, "meta": {"dump_date": DUMP_DATE, "n_sites": len(sites)}}


def api_metrics(params):
    metric = first(params, "metric")
    if metric not in METRICS:
        raise ApiError(400, "INVALID_METRIC",
                       "metric must be one of " + ", ".join(METRICS))
    m_from = parse_month(first(params, "from"), "from")
    m_to = parse_month(first(params, "to"), "to")
    if m_from and m_to and m_from > m_to:
        raise ApiError(400, "INVALID_RANGE", "from must not be after to")
    sites_arg = first(params, "sites")
    group = first(params, "group")
    if sites_arg and group:
        raise ApiError(400, "INVALID_RANGE", "use either sites or group, not both")
    known = get_sites()
    known_names = dict((s["site"], s["domain_group"]) for s in known)
    if sites_arg:
        wanted = []
        for s in sites_arg.split(","):
            s = s.strip().lower()
            if s and s not in wanted:
                wanted.append(s)
        if len(wanted) > MAX_SITES:
            raise ApiError(400, "TOO_MANY_SITES",
                           "at most %d sites per request" % MAX_SITES)
        if not wanted:
            raise ApiError(400, "INVALID_RANGE", "sites is empty")
        for s in wanted:
            if not SITE_RE.match(s) or s not in known_names:
                raise ApiError(404, "SITE_NOT_FOUND", "unknown site: %s" % s)
    elif group:
        if group not in GROUPS:
            raise ApiError(400, "INVALID_RANGE", "group must be one of A, B, C")
        wanted = [s["site"] for s in known if s["domain_group"] == group]
    else:
        wanted = [s["site"] for s in known]

    data = []
    if wanted:
        # values are validated against SITE_RE and the site list above
        in_list = ", ".join(["'%s'" % s for s in wanted])
        sql = ("SELECT site, month, %s FROM %s WHERE site IN (%s) "
               "ORDER BY site, month" % (metric, tbl(CONF["serving_table"]), in_list))
        by_site = {}
        order = []
        for r in run_query(sql):
            if r[0] not in by_site:
                by_site[r[0]] = []
                order.append(r[0])
            by_site[r[0]].append((r[1], to_number(r[2], metric)))
        for site in order:
            rows = by_site[site]
            base = baseline_2019(rows)
            for month, value in rows:
                if m_from and month < m_from:
                    continue
                if m_to and month > m_to:
                    continue
                if base is None or value is None:
                    idx = None
                else:
                    idx = round(value / base * 100.0, 4)
                data.append({"site": site, "month": month, "value": value,
                             "index_2019": idx})
    return {"data": data,
            "meta": {"metric": metric, "n_rows": len(data), "dump_date": DUMP_DATE,
                     "from": m_from, "to": m_to, "n_sites": len(wanted),
                     "last_valid_month": METRIC_LAST_MONTH[metric]}}


def baseline_2019(rows):
    vals = [v for (m, v) in rows if m.startswith("2019-")]
    if len(vals) != 12 or None in vals:
        return None
    mean = sum(vals) / 12.0
    if mean <= 0:
        return None
    return mean


def to_number(text, metric):
    if text is None:
        return None
    if metric in COUNT_METRICS:
        return int(float(text))
    return float(text)


def first(params, key):
    v = params.get(key)
    if not v:
        return None
    return v[0]


# ---------------------------------------------------------------- Q2 (R4) tables
# site_trend_summary columns, in R4 q2_trend_model_v2.py order (v1, then v2).
SUMMARY_V1 = ["site", "metric", "domain_group", "model_id", "fit_start", "fit_end",
              "post_start", "post_end", "pre_trend_pct_per_year", "post_gap_pct",
              "placebo_min", "placebo_max", "beyond_placebo", "break_month",
              "break_strength"]
SUMMARY_V2 = ["unit", "post_gap_12m", "placebo_values", "n_fit_months",
              "n_zero_months_excluded", "n_out_of_range", "smearing_factor",
              "is_primary", "note"]
SUMMARY_COLS = SUMMARY_V1 + SUMMARY_V2
SUMMARY_TYPES = {
    "pre_trend_pct_per_year": "float", "post_gap_pct": "float",
    "placebo_min": "float", "placebo_max": "float", "break_strength": "float",
    "post_gap_12m": "float", "smearing_factor": "float",
    "n_fit_months": "int", "n_zero_months_excluded": "int", "n_out_of_range": "int",
    "beyond_placebo": "bool", "is_primary": "bool",
}
TREND_COLS = ["month", "segment", "actual", "counterfactual", "band_low",
              "band_high", "gap"]

# q2_group_summary.csv columns (R4), loaded as a text table. Hyphens in the
# placebo column names become underscores in the table and in the response.
GROUP_COLS = ["metric", "unit", "n_sites_A", "n_sites_B", "did_12m", "did_12m_perm_p",
              "did_12m_cluster_se_logpt", "did_full", "did_full_perm_p", "perm_type",
              "n_perm", "did_placebo_2020_07", "did_placebo_2021_01",
              "did_placebo_2021_07", "did_placebo_2021_11", "did_placebo_min",
              "did_placebo_max", "did_beyond_placebo", "trend_gap12_median_A",
              "trend_gap12_median_B", "trend_gap12_A_minus_B",
              "share_sites_beyond_placebo_A", "share_sites_beyond_placebo_B",
              "primary_model", "same_sign_trend_vs_did", "break_signal",
              "break_signal_strict", "A_minus_B_break_month",
              "A_minus_B_break_strength"]
GROUP_TYPES = {"n_sites_A": "int", "n_sites_B": "int", "n_perm": "int",
               "did_beyond_placebo": "bool", "same_sign_trend_vs_did": "bool",
               "break_signal": "bool", "break_signal_strict": "bool",
               "metric": "str", "unit": "str", "perm_type": "str",
               "primary_model": "str", "A_minus_B_break_month": "str"}


def convert(text, kind):
    """impala-shell -B text -> JSON value. Empty strings from text tables are null."""
    if text is None or text == "":
        return None
    if kind == "float":
        try:
            v = float(text)
        except ValueError:
            return None
        if v != v:  # NaN
            return None
        return v
    if kind == "int":
        return int(float(text))
    if kind == "bool":
        return text.lower() in ("true", "1")
    return text


def parse_placebo(text):
    """'2020-07:6.431;2021-01:NA' -> {'2020-07': 6.431, '2021-01': None}"""
    out = {}
    if not text:
        return out
    for part in text.split(";"):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        try:
            out[k] = float(v)
        except ValueError:
            out[k] = None
    return out


def load_trend_available():
    try:
        rows = run_query("SELECT COUNT(*) FROM %s WHERE is_primary = true"
                         % tbl(CONF["trend_summary_table"]))
    except QueryError:
        return False
    return bool(rows) and rows[0][0] is not None and int(rows[0][0]) > 0


def load_trend_sites():
    try:
        rows = run_query("SELECT DISTINCT site FROM %s" % tbl(CONF["trend_summary_table"]))
    except QueryError:
        return {}
    return dict((r[0], 1) for r in rows if r and r[0])


def api_trend(params):
    metric = first(params, "metric")
    if metric not in METRICS:
        raise ApiError(400, "INVALID_METRIC",
                       "metric must be one of " + ", ".join(METRICS))
    site = (first(params, "site") or "").strip().lower()
    if not site:
        raise ApiError(400, "INVALID_RANGE", "site is required")
    if not SITE_RE.match(site):
        raise ApiError(404, "SITE_NOT_FOUND", "unknown site: %s" % site)
    m_from = parse_month(first(params, "from"), "from")
    m_to = parse_month(first(params, "to"), "to")
    if m_from and m_to and m_from > m_to:
        raise ApiError(400, "INVALID_RANGE", "from must not be after to")
    if not cached("trend_available", load_trend_available):
        raise ApiError(404, "NOT_FOUND", "Q2 trend tables are not loaded yet")
    if site not in cached("trend_sites", load_trend_sites):
        raise ApiError(404, "SITE_NOT_FOUND", "no trend model for site: %s" % site)

    # values are validated against SITE_RE / METRICS above
    sql = ("SELECT %s FROM %s WHERE site = '%s' AND metric = '%s' AND is_primary = true"
           % (", ".join(SUMMARY_COLS), tbl(CONF["trend_summary_table"]), site, metric))
    rows = run_query(sql)
    if not rows:
        raise ApiError(404, "SITE_NOT_FOUND",
                       "no trend model for %s / %s" % (site, metric))
    srow = rows[0]
    meta = {"site": site, "metric": metric, "dump_date": DUMP_DATE}
    for i, col in enumerate(SUMMARY_COLS):
        if col in ("site", "metric"):
            continue
        value = convert(srow[i], SUMMARY_TYPES.get(col, "str"))
        if col == "model_id":
            meta["model"] = value
        elif col == "placebo_values":
            meta[col] = parse_placebo(value)
        else:
            meta[col] = value

    model_id = meta["model"]
    if model_id is None or not MODEL_RE.match(model_id):
        raise QueryError("unexpected model_id in %s" % CONF["trend_summary_table"])
    sql = ("SELECT %s FROM %s WHERE site = '%s' AND metric = '%s' AND model_id = '%s' "
           "ORDER BY month" % (", ".join(TREND_COLS), tbl(CONF["trend_model_table"]),
                               site, metric, model_id))
    data = []
    for r in run_query(sql):
        month = r[0]
        if m_from and month < m_from:
            continue
        if m_to and month > m_to:
            continue
        row = {"site": site, "month": month, "segment": r[1]}
        for i, col in enumerate(TREND_COLS[2:]):
            row[col] = convert(r[i + 2], "float")
        data.append(row)
    meta["n_rows"] = len(data)
    meta["from"] = m_from
    meta["to"] = m_to
    return {"data": data, "meta": meta}


def api_group_summary(params):
    metric = first(params, "metric")
    if metric not in METRICS:
        raise ApiError(400, "INVALID_METRIC",
                       "metric must be one of " + ", ".join(METRICS))
    sql = ("SELECT %s FROM %s WHERE metric = '%s'"
           % (", ".join(GROUP_COLS), tbl(CONF["group_summary_table"]), metric))
    try:
        rows = run_query(sql)
    except QueryError:
        if not table_exists(CONF["group_summary_table"]):
            raise ApiError(404, "NOT_FOUND", "q2_group_summary is not loaded yet")
        raise
    if not rows:
        raise ApiError(404, "SITE_NOT_FOUND", "no group summary for %s" % metric)
    row = {}
    for i, col in enumerate(GROUP_COLS):
        if col == "metric":
            continue
        row[col] = convert(rows[0][i], GROUP_TYPES.get(col, "float"))
    return {"data": row, "meta": {"metric": metric, "dump_date": DUMP_DATE}}


def table_exists(name):
    try:
        rows = run_query("SHOW TABLES IN %s LIKE '%s'" % (CONF["db"], name))
    except QueryError:
        return False
    return any(r and r[0] == name for r in rows)


ROUTES = {
    "/api/meta": api_meta,
    "/api/sites": api_sites,
    "/api/metrics": api_metrics,
    "/api/trend": api_trend,
    "/api/group_summary": api_group_summary,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "R2API/1.0"

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        started = time.time()
        url = urlparse(self.path)
        path = url.path.rstrip("/") or "/"
        params = parse_qs(url.query)
        handler = ROUTES.get(path)
        try:
            if handler is None:
                raise ApiError(404, "NOT_FOUND", "unknown endpoint: %s" % path)
            status, body = 200, handler(params)
        except ApiError:
            e = sys.exc_info()[1]
            status, body = e.status, {"error": {"code": e.code, "message": e.message}}
        except QueryError:
            e = sys.exc_info()[1]
            status, body = 503, {"error": {"code": "QUERY_FAILED", "message": str(e)}}
        except Exception:
            e = sys.exc_info()[1]
            status, body = 503, {"error": {"code": "QUERY_FAILED",
                                           "message": "internal error: %s" % e}}
        payload = json.dumps(body)
        if not isinstance(payload, bytes):
            payload = payload.encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        sys.stderr.write("%s %s %d %.2fs\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"),
                                              self.path, status, time.time() - started))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, fmt, *args):
        pass  # one line per request is written in do_GET


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    p = OptionParser()
    p.add_option("--host", default="0.0.0.0")
    p.add_option("--port", type="int", default=8100)
    p.add_option("--impalad", default=CONF["impalad"])
    p.add_option("--db", default=CONF["db"])
    p.add_option("--ledger", default=CONF["ledger"])
    opts, _ = p.parse_args()
    CONF["impalad"] = opts.impalad
    CONF["db"] = opts.db
    CONF["ledger"] = opts.ledger
    srv = Server((opts.host, opts.port), Handler)
    sys.stderr.write("R2 API listening on %s:%d (impalad %s, db %s)\n"
                     % (opts.host, opts.port, opts.impalad, opts.db))
    srv.serve_forever()


if __name__ == "__main__":
    main()
