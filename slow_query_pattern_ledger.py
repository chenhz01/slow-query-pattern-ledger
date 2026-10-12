# -*- coding: utf-8 -*-
"""slow-query-pattern-ledger: query fingerprint aggregation + regression alerts.

Normalizes SQL statements into fingerprints (literals -> '?'), aggregates
frequency x duration matrices, and flags regressions against a baseline.
Pure stdlib, offline.
"""
import argparse
import json
import sys

__version__ = "0.1.1"

import re

COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
STR_LIT_RE = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")
NUM_LIT_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
IN_LIST_RE = re.compile(r"\bin\s*\(\s*\?(?:\s*,\s*\?)+\s*\)", re.I)


def normalize_sql(sql):
    """Normalize a SQL string into a fingerprint; non-str raises TypeError."""
    if not isinstance(sql, str):
        raise TypeError("sql must be str")
    s = COMMENT_RE.sub(" ", sql)
    s = STR_LIT_RE.sub("?", s)
    s = NUM_LIT_RE.sub("?", s)
    s = IN_LIST_RE.sub("in (?)", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def fingerprint(sql):
    """Alias of normalize_sql (the fingerprint IS the normalized string)."""
    return normalize_sql(sql)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _pct(sorted_vals, p):
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    idx = min(n - 1, int((n - 1) * p / 100.0 + 0.5))
    return sorted_vals[idx]


class Ledger(object):
    """Aggregates slow-query records by fingerprint."""

    def __init__(self):
        self.by_fp = {}
        self.invalid = []

    def add(self, rec):
        """Add one record {sql|fingerprint, duration_ms}; True on success."""
        if not isinstance(rec, dict):
            raise TypeError("record must be a dict")
        dur = rec.get("duration_ms")
        if not _is_num(dur) or dur < 0:
            self.invalid.append(rec)
            return False
        fp = rec.get("fingerprint")
        if not isinstance(fp, str) or not fp.strip():
            fp = normalize_sql(rec["sql"]) if isinstance(rec.get("sql"), str) else None
        if not fp:
            self.invalid.append(rec)
            return False
        self.by_fp.setdefault(fp, []).append(dur)
        return True

    def report(self):
        """Return per-pattern rows sorted by total_ms desc."""
        rows = []
        for fp, durs in self.by_fp.items():
            s = sorted(durs)
            n = len(s)
            total = sum(s)
            rows.append({"fingerprint": fp, "count": n, "total_ms": total,
                         "avg_ms": total / n, "min_ms": s[0], "max_ms": s[-1],
                         "p50_ms": _pct(s, 50), "p95_ms": _pct(s, 95)})
        rows.sort(key=lambda r: -r["total_ms"])
        return rows

    def regressions(self, baseline, factor=1.5, min_count=3):
        """Compare report() against baseline {fp: {avg_ms, count}}.

        Baseline entries may also arrive nested under key 'fingerprints'.
        Returns finding dicts (REGRESSION / NEW_PATTERN).
        """
        if baseline is None:
            return []
        if isinstance(baseline, dict) and isinstance(baseline.get("fingerprints"), dict):
            baseline = baseline["fingerprints"]
        if not isinstance(baseline, dict):
            raise TypeError("baseline must be a dict")
        out = []
        for row in self.report():
            fp = row["fingerprint"]
            base = baseline.get(fp)
            if row["count"] < min_count:
                continue
            if base is None:
                out.append({"code": "NEW_PATTERN", "severity": "INFO",
                            "fingerprint": fp,
                            "message": "new pattern with %d occurrences (avg %.6g ms)"
                                       % (row["count"], row["avg_ms"])})
                continue
            bavg = base.get("avg_ms") if isinstance(base, dict) else None
            if not _is_num(bavg):
                continue
            if row["avg_ms"] > bavg * factor:
                out.append({"code": "REGRESSION", "severity": "ERROR",
                            "fingerprint": fp,
                            "message": "avg %.6g ms exceeds baseline %.6g ms by > %.2fx (%d hits)"
                                       % (row["avg_ms"], bavg, factor, row["count"])})
        return out


def analyze_records(records, baseline=None, factor=1.5, min_count=3):
    """Analyze a list of records; return (report_rows, findings).

    Raises TypeError when records is not a list.
    """
    if not isinstance(records, list):
        raise TypeError("records must be a list")
    ledger = Ledger()
    for rec in records:
        try:
            ledger.add(rec)
        except TypeError:
            ledger.invalid.append(rec)
    findings = []
    for i, _bad in enumerate(ledger.invalid):
        findings.append({"code": "INVALID_RECORD", "severity": "WARN",
                         "message": "record #%d lacks valid sql/fingerprint and duration_ms"
                                    % i, "location": "$.records[%d]" % i})
    if baseline is not None:
        findings += ledger.regressions(baseline, factor=factor, min_count=min_count)
    return ledger.report(), findings


def summarize(findings):
    out = {"error": 0, "warn": 0, "info": 0}
    for f in findings:
        key = f["severity"].lower()
        out[key] = out.get(key, 0) + 1
    return out


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="slow-query-pattern-ledger",
        description="Aggregate slow-query logs into fingerprints and flag "
                    "regressions against a baseline.")
    p.add_argument("queries", help="JSONL file: one {sql, duration_ms} record per line")
    p.add_argument("--baseline", default=None,
                   help="JSON file: {fingerprint: {avg_ms, count}}")
    p.add_argument("--top", type=int, default=None, dest="top",
                   help="limit printed rows to the N costliest patterns")
    p.add_argument("--factor", type=float, default=1.5,
                   help="regression threshold multiplier (default 1.5)")
    p.add_argument("--min-count", type=int, default=3, dest="min_count",
                   help="minimum occurrences before regression checks (default 3)")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="emit report and findings as JSON")
    a = p.parse_args(argv)

    try:
        with open(a.queries, "r", encoding="utf-8") as f:
            raw_lines = f.read().splitlines()
    except OSError as e:
        print("error: cannot read queries: %s" % e, file=sys.stderr)
        sys.exit(2)

    records, malformed = [], []
    for n, line in enumerate(raw_lines, 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            malformed.append(n)

    baseline = None
    if a.baseline:
        try:
            with open(a.baseline, "r", encoding="utf-8") as f:
                baseline = json.load(f)
        except OSError as e:
            print("error: cannot read baseline: %s" % e, file=sys.stderr)
            sys.exit(2)
        except ValueError as e:
            print("error: baseline is not valid JSON: %s" % e, file=sys.stderr)
            sys.exit(2)

    try:
        rows, findings = analyze_records(records, baseline,
                                         factor=a.factor, min_count=a.min_count)
    except TypeError as e:
        print("error: %s" % e, file=sys.stderr)
        sys.exit(2)

    for n in malformed:
        findings.append({"code": "MALFORMED_JSONL", "severity": "WARN",
                         "message": "line %d is not valid JSON" % n,
                         "location": "line %d" % n})
    findings.sort(key=lambda f: f["code"])

    shown = rows if a.top is None else rows[:max(0, a.top)]
    s = summarize(findings)
    if a.as_json:
        print(json.dumps({"rows": shown, "findings": findings, "summary": s},
                         ensure_ascii=False, indent=2))
    else:
        for r in shown:
            print("%6d hits  avg %10.3f ms  p95 %10.3f ms  %s"
                  % (r["count"], r["avg_ms"], r["p95_ms"], r["fingerprint"]))
        for f in findings:
            print("[%s] %s: %s" % (f["severity"], f["code"], f["message"]))
        print("summary: %d pattern(s), %d error(s), %d warn(s), %d info(s)"
              % (len(rows), s["error"], s["warn"], s["info"]))
    sys.exit(1 if s["error"] else 0)


if __name__ == "__main__":
    main()
