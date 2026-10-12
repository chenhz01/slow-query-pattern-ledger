# Changelog

## v0.1.1 (2026-10-13)

Round-2 revision after full self-review of the v0.1.0 draft:

- Added 4 extreme-edge test cases (mixed-case fingerprint equality, 1000
  records sharing one fingerprint, regression exactly at the 1.5x boundary,
  NEW_PATTERN with min_count=1); suite grew 64 -> 68 cases, all PASS.
- Fixed the 10,000-record fixture to use distinct table names so it really
  produces 10,000 patterns instead of one.
- Measured and documented Performance numbers ([Measured] tags, repro
  commands).
- Added ALIGNMENT.md (rules 85-100 audit, 16 items with evidence).

## v0.1.0 (2026-10-13)

Initial draft:

- SQL fingerprinting: comment stripping, string/numeric literal replacement,
  IN-list collapsing, whitespace/case normalization; dialect-agnostic.
- Ledger aggregation: count / total / avg / min / max / p50 / p95 per
  pattern, sorted by total cost; invalid records counted as WARN findings.
- Regression diffing: baseline {fingerprint: {avg_ms, count}} with factor
  and min_count thresholds; REGRESSION (error) and NEW_PATTERN (info).
- CLI (`analyze`, `--baseline`, `--top`, `--factor`, `--min-count`,
  `--json`) with exit-code contract 0/1/2; malformed JSONL lines warn.
- Self-test suite: 64 cases across functional / exception / extreme-edge
  classes, `SUMMARY: PASS n/n` reporting, four-defence template.
- README, MIT LICENSE.
