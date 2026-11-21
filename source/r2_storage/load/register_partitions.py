# -*- coding: utf-8 -*-
"""R2 status-based partition registration (replaces MSCK REPAIR TABLE).

The external tables qa_decline.{posts,users,votes} point at the same HDFS
path R1's parser writes to, so "loading" = validate + register partitions.
A site/table is registered only when its R1 status JSON exists AND:
  * dump_date == 2024-04-02, parser_version == v0.3
  * status.years == the year=YYYY directories (with files) actually in HDFS
  * after registration, Impala COUNT(*) for the site == status.rows
Decisions per unit (site, table), using the R2 ledger:
  NEW      not in ledger                      -> register
  SKIP     same finished_at/rows/years        -> nothing (no append, no dup)
  REPLACE  finished_at (or rows/years) changed -> drop stale years, add, REFRESH
  FAIL     validation failed                  -> not registered (rolled back)
Sites without a status file (e.g. the 10 sites loaded in Phase 1) are never
touched.  Hive does the ALTER TABLE, then Impala REFRESH (decision 11/21).

Python 2.6 standard library only.
  python register_partitions.py [--site beer.stackexchange.com] [--dry-run]
"""
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import shutil
import time
from optparse import OptionParser

TABLES = ("posts", "users", "votes")
try:
    INT_TYPES = (int, long)
except NameError:  # Python 3
    INT_TYPES = (int,)
SITE_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]*$")
STATUS_RE = re.compile(r"^(?P<site>.+)\.(?P<table>posts|users|votes)\.json$")
YEAR_FILE_RE = re.compile(
    r"/(?P<table>posts|users|votes)/site=(?P<site>[^/]+)/year=(?P<year>\d{4})/[^/]+\.parquet$")


def log(msg):
    sys.stdout.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stdout.flush()


def run(cmd, check=True):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate()
    if check and proc.returncode != 0:
        raise RuntimeError("command failed (%d): %s\n%s" % (
            proc.returncode, " ".join(cmd), err.strip()))
    return proc.returncode, out, err


def impala(opts, sql):
    _, out, _ = run(["impala-shell", "-i", opts.impalad, "-B",
                     "--output_delimiter=\t", "-q", sql])
    return [line.split("\t") for line in out.splitlines() if line.strip()]


def hive_file(opts, statements):
    fd, path = tempfile.mkstemp(suffix=".hql", prefix="r2_register_")
    f = os.fdopen(fd, "w")
    try:
        f.write("USE %s;\n" % opts.db)
        for s in statements:
            f.write(s + ";\n")
    finally:
        f.close()
    try:
        log("hive -f (%d statements)" % len(statements))
        for s in statements:
            log("  " + s)
        run(["hive", "-S", "-f", path])
    finally:
        os.remove(path)


def hive_partitions(opts, table):
    """{site: set(years)} currently registered in the metastore."""
    _, out, _ = run(["hive", "-S", "-e", "SHOW PARTITIONS %s.%s" % (opts.db, table)])
    result = {}
    for line in out.splitlines():
        m = re.match(r"^site=([^/]+)/year=(\d+)$", line.strip())
        if m:
            result.setdefault(m.group(1), set()).add(int(m.group(2)))
    return result


# ------------------------------------------------------------------ inputs
def read_statuses(opts):
    tmp = tempfile.mkdtemp(prefix="r2_status_")
    try:
        dest = os.path.join(tmp, "s")
        code, _, err = run(["hdfs", "dfs", "-get", opts.status_dir, dest], check=False)
        if code != 0:
            raise RuntimeError("cannot read %s: %s" % (opts.status_dir, err.strip()))
        statuses = []
        names = os.path.isdir(dest) and os.listdir(dest) or []
        for name in sorted(names):
            m = STATUS_RE.match(name)
            if not m:  # skips .tmp.<pid> and anything else
                continue
            if opts.sites and m.group("site") not in opts.sites:
                continue
            f = open(os.path.join(dest, name))
            try:
                try:
                    st = json.load(f)
                except ValueError:
                    st = {"_invalid_json": True}
            finally:
                f.close()
            st["_file"] = name
            st["_site"] = m.group("site")
            st["_table"] = m.group("table")
            statuses.append(st)
        return statuses
    finally:
        shutil.rmtree(tmp)


def hdfs_years(opts, sites):
    """{(site, table): set(years with >=1 parquet file)} in one HDFS call."""
    result = {}
    if not sites:
        return result
    globs = ["%s/%s/site=%s/year=*/*.parquet" % (opts.root, t, s)
             for s in sites for t in TABLES]
    _, out, _ = run(["hdfs", "dfs", "-ls"] + globs, check=False)
    for line in out.splitlines():
        m = YEAR_FILE_RE.search(line)
        if m:
            key = (m.group("site"), m.group("table"))
            result.setdefault(key, set()).add(int(m.group("year")))
    return result


def load_ledger(path):
    if not os.path.exists(path):
        return {"units": {}, "serving": {}}
    f = open(path)
    try:
        return json.load(f)
    finally:
        f.close()


def save_ledger(path, ledger):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    tmp = path + ".tmp"
    f = open(tmp, "w")
    try:
        json.dump(ledger, f, indent=1, sort_keys=True)
    finally:
        f.close()
    os.rename(tmp, path)


# ------------------------------------------------------------------ main
def validate(opts, st, years_found):
    problems = []
    if st.get("_invalid_json"):
        return ["status is not valid JSON"]
    if st.get("site") != st["_site"] or st.get("table") != st["_table"]:
        problems.append("site/table in file != file name")
    if not SITE_RE.match(st["_site"]):
        problems.append("illegal site name")
    if st.get("dump_date") != opts.dump_date:
        problems.append("dump_date %r != %s" % (st.get("dump_date"), opts.dump_date))
    if st.get("parser_version") != opts.parser_version:
        problems.append("parser_version %r != %s" % (st.get("parser_version"),
                                                     opts.parser_version))
    rows = st.get("rows")
    if isinstance(rows, bool) or not isinstance(rows, INT_TYPES) or rows < 0:
        problems.append("rows missing/invalid")
    years = st.get("years")
    if not isinstance(years, list) or not years:
        problems.append("years missing")
    else:
        if set(years) != years_found:
            problems.append("status years %s != HDFS years %s" % (
                sorted(set(years)), sorted(years_found)))
    if not st.get("finished_at"):
        problems.append("finished_at missing")
    return problems


def main():
    p = OptionParser()
    p.add_option("--root", default="/project/parquet")
    p.add_option("--status-dir", default="/project/parquet/_status")
    p.add_option("--db", default="qa_decline")
    p.add_option("--impalad", default="localhost:21000")
    p.add_option("--ledger", default="/data/work/r2/ledger.json")
    p.add_option("--lock", default="/data/work/r2/register.lock")
    p.add_option("--dump-date", default="2024-04-02")
    p.add_option("--parser-version", default="v0.3")
    p.add_option("--site", action="append", dest="sites",
                 help="limit to this site (repeatable)")
    p.add_option("--dry-run", action="store_true", default=False)
    opts, _ = p.parse_args()

    lockdir = os.path.dirname(opts.lock)
    if lockdir and not os.path.isdir(lockdir):
        os.makedirs(lockdir)
    lockf = open(opts.lock, "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        log("another register run is active; exiting")
        return 2

    t0 = time.time()
    ledger = load_ledger(opts.ledger)
    statuses = read_statuses(opts)
    log("status files: %d" % len(statuses))
    sites = sorted(set([s["_site"] for s in statuses if SITE_RE.match(s["_site"])]))
    found = hdfs_years(opts, sites)

    plan = []  # (unit dict, action)
    results = []
    for st in statuses:
        key = "%s|%s" % (st["_site"], st["_table"])
        yrs = found.get((st["_site"], st["_table"]), set())
        problems = validate(opts, st, yrs)
        if problems:
            results.append((key, "FAIL", "; ".join(problems)))
            continue
        prev = ledger["units"].get(key)
        sig = (st["finished_at"], st["rows"], sorted(st["years"]))
        if prev and prev.get("state") == "REGISTERED" and \
                (prev.get("finished_at"), prev.get("rows"), prev.get("years")) == sig:
            results.append((key, "SKIP", "same finished_at %s" % st["finished_at"]))
            continue
        action = "REPLACE" if prev and prev.get("state") == "REGISTERED" else "NEW"
        plan.append((st, action))

    if plan:
        touched = sorted(set([st["_table"] for st, _ in plan]))
        current = dict((t, hive_partitions(opts, t)) for t in touched)
        stmts = []
        for st, action in plan:
            t, s = st["_table"], st["_site"]
            have = current[t].get(s, set())
            want = set(st["years"])
            for y in sorted(have - want):
                stmts.append("ALTER TABLE %s DROP IF EXISTS PARTITION (site='%s', year=%d)"
                             % (t, s, y))
            for y in sorted(want):
                stmts.append("ALTER TABLE %s ADD IF NOT EXISTS PARTITION (site='%s', year=%d)"
                             % (t, s, y))
        if opts.dry_run:
            for st, action in plan:
                results.append(("%s|%s" % (st["_site"], st["_table"]), action + "(dry-run)", ""))
            for s in stmts:
                log("DRY " + s)
        else:
            if stmts:
                hive_file(opts, stmts)
            for t in touched:
                log("impala: REFRESH %s.%s" % (opts.db, t))
                impala(opts, "REFRESH %s.%s" % (opts.db, t))
            # verify row counts through Impala
            rollback = []
            for t in touched:
                units = [st for st, _ in plan if st["_table"] == t]
                in_list = ", ".join(["'%s'" % st["_site"] for st in units])
                counts = dict((r[0], int(r[1])) for r in impala(
                    opts, "SELECT site, COUNT(*) FROM %s.%s WHERE site IN (%s) GROUP BY site"
                    % (opts.db, t, in_list)))
                for st in units:
                    key = "%s|%s" % (st["_site"], t)
                    action = [a for u, a in plan if u is st][0]
                    got = counts.get(st["_site"], 0)
                    if got == st["rows"]:
                        ledger["units"][key] = {
                            "state": "REGISTERED", "finished_at": st["finished_at"],
                            "rows": st["rows"], "years": sorted(st["years"]),
                            "registered_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                            "action": action}
                        results.append((key, action, "rows %d = status" % got))
                    else:
                        rollback.append((t, st))
                        ledger["units"][key] = {"state": "FAILED", "finished_at": st["finished_at"],
                                                "rows": st["rows"], "years": sorted(st["years"]),
                                                "impala_rows": got}
                        results.append((key, "FAIL", "impala rows %d != status %d (rolled back)"
                                        % (got, st["rows"])))
            if rollback:
                hive_file(opts, ["ALTER TABLE %s DROP IF EXISTS PARTITION (site='%s')"
                                 % (t, st["_site"]) for t, st in rollback])
                for t in sorted(set([t for t, _ in rollback])):
                    impala(opts, "REFRESH %s.%s" % (opts.db, t))
            save_ledger(opts.ledger, ledger)

    log("---- summary (%.1fs) ----" % (time.time() - t0))
    for key, action, note in sorted(results):
        log("%-45s %-8s %s" % (key, action, note))
    counts = {}
    for _, a, _ in results:
        counts[a] = counts.get(a, 0) + 1
    log("totals: " + ", ".join(["%s=%d" % kv for kv in sorted(counts.items())]))
    return 1 if counts.get("FAIL") else 0


if __name__ == "__main__":
    sys.exit(main())
