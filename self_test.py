# -*- coding: utf-8 -*-
"""self_test for slow-query-pattern-ledger — R14, inherits 4-defence template v2."""
import atexit
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="\n")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import slow_query_pattern_ledger as m  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="sqpl_selftest_")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)


def wf(name, content):
    p = os.path.join(_TMP, name)
    with open(p, "w", encoding="utf-8", newline="") as f:  # Defence 3
        f.write(content)
    return p


def run_cli(argv):
    buf_out, buf_err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            m.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except Exception as e:  # noqa: BLE001
        code = 1
        buf_err.write(repr(e))
    return code, buf_out.getvalue(), buf_err.getvalue()


SQL = "SELECT * FROM users WHERE id = 42"
FP = m.fingerprint(SQL)

Q_OK = wf("q_ok.jsonl", "\n".join(
    json.dumps({"sql": "SELECT * FROM users WHERE id = %d" % i, "duration_ms": 100 + i})
    for i in range(5)) + "\n")
Q_EMPTY = wf("q_empty.jsonl", "")
Q_MALFORMED = wf("q_malformed.jsonl", '{"sql": "SELECT 1", "duration_ms": 5}\nnot json\n')
BASE_REG = wf("base_reg.json", json.dumps({FP: {"avg_ms": 50, "count": 5}}))
BASE_LOW = wf("base_low.json", json.dumps({FP: {"avg_ms": 1000, "count": 5}}))
BASE_MALFORMED = wf("base_malformed.json", "{oops")


def recs(n=5, dur=100):
    return [{"sql": "SELECT * FROM users WHERE id = %d" % i, "duration_ms": dur}
            for i in range(n)]

BASELINE = {FP: {"avg_ms": 50.0, "count": 5}}

# ---- [A] functional: (name, fn(self)) run as generated tests ----
def _a(name):
    def deco(fn):
        A_CASES.append((name, fn))
        return fn
    return deco

A_CASES = []


@_a("normalize_literals")
def _(s):
    s.assertEqual(m.fingerprint("SELECT * FROM t WHERE a = 7 AND b = 'x'"),
                  m.fingerprint("select * from t where a = ? and b = ?"))


@_a("normalize_in_list")
def _(s):
    s.assertEqual(m.fingerprint("SELECT * FROM t WHERE id IN (1, 2, 3)"),
                  m.fingerprint("SELECT * FROM t WHERE id IN (9)"))


@_a("normalize_stable")
def _(s):
    s.assertEqual(m.fingerprint(FP), FP)


@_a("normalize_whitespace")
def _(s):
    s.assertEqual(m.fingerprint("SELECT  1"), m.fingerprint("select\n 1"))


@_a("fingerprint_differs_for_diff_queries")
def _(s):
    s.assertNotEqual(m.fingerprint("SELECT * FROM a"),
                     m.fingerprint("SELECT * FROM b"))


@_a("ledger_add_ok")
def _(s):
    led = m.Ledger()
    s.assertTrue(led.add({"sql": SQL, "duration_ms": 120}))


@_a("ledger_add_invalid_duration")
def _(s):
    led = m.Ledger()
    s.assertFalse(led.add({"sql": SQL, "duration_ms": -1}))


@_a("ledger_add_string_duration")
def _(s):
    led = m.Ledger()
    s.assertFalse(led.add({"sql": SQL, "duration_ms": "120"}))


@_a("ledger_add_bool_duration")
def _(s):
    led = m.Ledger()
    s.assertFalse(led.add({"sql": SQL, "duration_ms": True}))


@_a("ledger_add_no_sql")
def _(s):
    led = m.Ledger()
    s.assertFalse(led.add({"duration_ms": 10}))


@_a("ledger_add_prefer_fingerprint")
def _(s):
    led = m.Ledger()
    led.add({"fingerprint": "fp-x", "duration_ms": 10})
    s.assertEqual(list(led.by_fp), ["fp-x"])


@_a("report_aggregates")
def _(s):
    led = m.Ledger()
    for d in (100, 200, 300):
        led.add({"sql": SQL, "duration_ms": d})
    row = led.report()[0]
    s.assertEqual(row["count"], 3)
    s.assertAlmostEqual(row["avg_ms"], 200.0)
    s.assertEqual(row["max_ms"], 300)
    s.assertEqual(row["min_ms"], 100)


@_a("report_sorted_by_total_desc")
def _(s):
    rows, _f = m.analyze_records(
        [{"sql": "SELECT 1", "duration_ms": 10}] * 2 +
        [{"sql": "SELECT 2", "duration_ms": 500}] * 2)
    s.assertEqual(rows[0]["fingerprint"], m.fingerprint("SELECT 2"))


@_a("p95_within_bounds")
def _(s):
    led = m.Ledger()
    for d in range(1, 101):
        led.add({"sql": SQL, "duration_ms": d})
    row = led.report()[0]
    s.assertTrue(90 <= row["p95_ms"] <= 100)


@_a("p50_single_value")
def _(s):
    led = m.Ledger()
    led.add({"sql": SQL, "duration_ms": 42})
    s.assertEqual(led.report()[0]["p50_ms"], 42)


@_a("regression_detected")
def _(s):
    rows, _ = None, None
    led = m.Ledger()
    for _i in range(5):
        led.add({"sql": SQL, "duration_ms": 200})
    fs = led.regressions(BASELINE)
    s.assertEqual([f["code"] for f in fs], ["REGRESSION"])


@_a("regression_needs_min_count")
def _(s):
    led = m.Ledger()
    for _i in range(2):
        led.add({"sql": SQL, "duration_ms": 200})
    s.assertEqual(led.regressions(BASELINE), [])


@_a("new_pattern_info")
def _(s):
    led = m.Ledger()
    for _i in range(5):
        led.add({"sql": "SELECT * FROM brandnew", "duration_ms": 10})
    fs = led.regressions(BASELINE)
    s.assertEqual([f["code"] for f in fs], ["NEW_PATTERN"])


@_a("no_baseline_no_findings")
def _(s):
    _rows, fs = m.analyze_records(recs())
    s.assertEqual(fs, [])


@_a("analyze_invalid_records_warn")
def _(s):
    _rows, fs = m.analyze_records(recs(3) + [{"sql": SQL, "duration_ms": None}])
    s.assertEqual([f["code"] for f in fs], ["INVALID_RECORD"])


@_a("duration_zero_is_valid")
def _(s):
    led = m.Ledger()
    s.assertTrue(led.add({"sql": SQL, "duration_ms": 0}))


@_a("baseline_nested_form_accepted")
def _(s):
    led = m.Ledger()
    for _i in range(5):
        led.add({"sql": SQL, "duration_ms": 500})
    fs = led.regressions({"fingerprints": BASELINE})
    s.assertEqual([f["code"] for f in fs], ["REGRESSION"])


@_a("baseline_bad_entry_skipped")
def _(s):
    led = m.Ledger()
    for _i in range(5):
        led.add({"sql": SQL, "duration_ms": 500})
    fs = led.regressions({FP: {"avg_ms": "high"}})
    s.assertEqual(fs, [])


@_a("factor_override")
def _(s):
    led = m.Ledger()
    for _i in range(5):
        led.add({"sql": SQL, "duration_ms": 120})
    s.assertEqual(led.regressions({FP: {"avg_ms": 100}}, factor=1.0)[0]["code"], "REGRESSION")


@_a("analyze_records_sorted_rows")
def _(s):
    rows, _fs = m.analyze_records(recs())
    s.assertEqual(rows[0]["fingerprint"], FP)


class TestA(unittest.TestCase):
    pass


for _i, (_nm, _fn) in enumerate(A_CASES):
    setattr(TestA, "test_a%02d_%s" % (_i, _nm), _fn)

# ---- [B] CLI contract ----
B_CASES = [
    ("analyze_ok_exit0", [Q_OK], 0, "", ""),
    ("analyze_malformed_line_warn", [Q_MALFORMED], 0, "MALFORMED_JSONL", ""),
    ("analyze_missing_file", ["absent.jsonl"], 2, "", "cannot read"),
    ("baseline_missing_file", [Q_OK, "--baseline", "absent_base.json"], 2, "", "cannot read"),
    ("baseline_malformed", [Q_OK, "--baseline", BASE_MALFORMED], 2, "", "not valid JSON"),
    ("baseline_regression_exit1", [Q_OK, "--baseline", BASE_REG], 1, "REGRESSION", ""),
    ("baseline_clean_exit0", [Q_OK, "--baseline", BASE_LOW], 0, "", ""),
    ("json_flag", [Q_OK, "--json"], 0, '"rows"', ""),
    ("top_limit", [Q_OK, "--top", "1"], 0, "", ""),
    ("text_output_summary", [Q_OK], 0, "summary:", ""),
    ("json_summary_key", [Q_OK, "--json"], 0, '"summary"', ""),
    ("empty_file_exit0", [Q_EMPTY], 0, "0 pattern(s)", ""),
]


def _mk_b(name, argv_extra, code, out_sub="", err_sub=""):
    def t(self):
        c, out, err = run_cli(argv_extra)
        self.assertEqual(c, code, msg="out=%r err=%r" % (out, err))
        if out_sub:
            self.assertIn(out_sub, out)
        if err_sub:
            self.assertIn(err_sub, err)
    t.__doc__ = "B: %s" % name
    return t


class TestB(unittest.TestCase):
    def test_b12_missing_positional(self):
        c, _out, _err = run_cli([])
        self.assertEqual(c, 2)

    def test_b13_help_exit0(self):
        c, out, _err = run_cli(["--help"])
        self.assertEqual(c, 0)
        self.assertIn("usage:", out)

    def test_b14_json_roundtrip(self):
        c, out, _err = run_cli([Q_OK, "--json"])
        self.assertEqual(c, 0)
        data = json.loads(out)
        self.assertEqual(data["summary"]["error"], 0)
        self.assertEqual(len(data["rows"]), 1)


for _i, (_nm, _ex, _cd, _os, _es) in enumerate(B_CASES):
    setattr(TestB, "test_b%02d_%s" % (_i, _nm), _mk_b(_nm, _ex, _cd, _os, _es))

# ---- [C] extreme/edge ----
C_CASES = [
    ("analyze_empty_records", lambda s: s.assertEqual(m.analyze_records([]), ([], []))),
    ("normalize_empty_sql", lambda s: s.assertEqual(m.fingerprint(""), "")),
    ("normalize_comment_only", lambda s: s.assertEqual(m.fingerprint("-- hi"), "")),
    ("normalize_non_str_raises", lambda s: s.assertRaises(TypeError, m.fingerprint, 42)),
    ("ledger_non_dict_raises", lambda s: s.assertRaises(TypeError, m.Ledger().add, "x")),
    ("analyze_non_list_raises", lambda s: s.assertRaises(TypeError, m.analyze_records, "x")),
    ("regressions_none_baseline", lambda s: s.assertEqual(m.Ledger().regressions(None), [])),
    ("regressions_bad_type_raises", lambda s: s.assertRaises(
        TypeError, m.Ledger().regressions, [1])),
    ("ten_thousand_records", lambda s: s.assertEqual(
        len(m.analyze_records([{"sql": "SELECT * FROM t%d" % i, "duration_ms": 1}
                               for i in range(10000)])[0]), 10000)),
    ("unicode_sql_ok", lambda s: s.assertEqual(
        m.fingerprint("SELECT '名字' FROM 用户"), m.fingerprint("select ? from 用户"))),
    ("escaped_quote_literal", lambda s: s.assertEqual(
        m.fingerprint("SELECT 'O''Brien'"), "select ?")),
    ("decimal_literal_normalized", lambda s: s.assertEqual(
        m.fingerprint("SELECT 3.14"), m.fingerprint("SELECT 2.71"))),
    ("crlf_jsonl_lines", lambda s: s.assertEqual(
        run_cli([wf("c_crlf.jsonl",
                               '{"sql": "SELECT 1", "duration_ms": 5}\r\n')])[0], 0)),
    ("blank_lines_skipped", lambda s: s.assertEqual(
        run_cli([wf("c_blank.jsonl", "\n\n\n")])[0], 0)),
    ("multiline_comment_normalized", lambda s: s.assertEqual(
        m.fingerprint("SELECT /* c1 */ 1"), m.fingerprint("SELECT 1"))),
    ("double_dash_comment_normalized", lambda s: s.assertEqual(
        m.fingerprint("SELECT 1 -- tail"), "select ?")),
    ("uppercase_IN_list", lambda s: s.assertEqual(
        m.fingerprint("SELECT * FROM t WHERE id IN (1,2)"),
        m.fingerprint("select * from t where id in (?)"))),
    ("percent_number_normalized", lambda s: s.assertEqual(
        m.fingerprint("UPDATE t SET a = 50"), "update t set a = ?")),
    ("huge_sql_completes", lambda s: s.assertIsInstance(
        m.fingerprint("SELECT " + "x" * 100000), str)),
    ("p95_n2", lambda s: s.assertEqual(
        m._pct(sorted([10, 20]), 95), 20)),
    ("avg_is_float", lambda s: s.assertIsInstance(
        m.analyze_records([{"sql": "SELECT 1", "duration_ms": 3}])[0][0]["avg_ms"], float)),
    ("cli_directory_exit2", lambda s: s.assertEqual(run_cli([_TMP])[0], 2)),
    ("cli_top_zero_empty", lambda s: s.assertEqual(
        run_cli([Q_OK, "--top", "0"])[0], 0)),
    ("invalid_records_do_not_crash_cli", lambda s: s.assertEqual(
        run_cli([wf("c_invalid.jsonl",
                               json.dumps({"duration_ms": 5}) + "\n")])[0], 0)),
]


class TestC(unittest.TestCase):
    pass


for _i, (_nm, _fn) in enumerate(C_CASES):
    setattr(TestC, "test_c%02d_%s" % (_i, _nm), _fn)


# ---- Round-2 additions (v0.1.1): extra extreme-edge coverage ----
class TestR2(unittest.TestCase):
    def test_r2_mixed_case_same_fp(self):
        self.assertEqual(m.fingerprint("SeLeCt * FrOm users"),
                         m.fingerprint("select * from users"))

    def test_r2_thousand_same_fp(self):
        led = m.Ledger()
        for _ in range(1000):
            led.add({"sql": SQL, "duration_ms": 10})
        self.assertEqual(led.report()[0]["count"], 1000)

    def test_r2_regression_at_exact_factor_boundary(self):
        led = m.Ledger()
        for _ in range(5):
            led.add({"sql": SQL, "duration_ms": 75})
        self.assertEqual(led.regressions({FP: {"avg_ms": 50}}), [])

    def test_r2_new_pattern_min_count_1(self):
        led = m.Ledger()
        led.add({"sql": "SELECT * FROM fresh", "duration_ms": 5})
        self.assertEqual([f["code"] for f in led.regressions({}, min_count=1)], ["NEW_PATTERN"])

if __name__ == "__main__":
    r = unittest.TextTestRunner(verbosity=0).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules["__main__"]))
    total, fails = r.testsRun, len(r.failures) + len(r.errors)
    print("SUMMARY: PASS %d/%d" % (total - fails, total))  # Defence 4
    sys.exit(0 if fails == 0 else 1)
