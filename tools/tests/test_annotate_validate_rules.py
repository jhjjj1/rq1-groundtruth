#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The two rule-table checks added after the R1/R2 audit, and the site-index mode
that lets a pass cut into different batches be re-validated at all.

Every fixture here is a reduction of a real shape from the corpus: wikipedia-ios'
`UserDefaults.standard` under a 1C8F.1-only declaration (337 pairs wrote UNKNOWN
where §4.5 says SUPPORTED), wBlock's one-shot random suite (the row 1.11
rewrites), wBlock's `?? .standard` fallback (where the prefill is wrong and the
annotation is right), and the ALT sites the first pass marked YES against §4.7.
"""
import io
import json
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import annotate_validate as V  # noqa: E402

UD_LINE = "    UserDefaults.standard.set(true, forKey: \"seen\")"
SUITE_LINE = "    let d = UserDefaults(suiteName: \"test.demo.popup.abc\")"
SIG = "func markSeen() {"


def _ctx(line12):
    src = {11: SIG, 12: line12, 13: "}"}
    return [f"{n:5d}{'>>' if n == 12 else '  '} {src.get(n, f'// {n}')}" for n in range(1, 21)]


CTX = _ctx(UD_LINE)
CTX_SUITE = _ctx(SUITE_LINE)

C_1C8F = [
    {"id": "R1C8F_C1", "type": "REQUIRED", "predicate": "DefaultsDomainIs(APP_GROUP)"},
    {"id": "R1C8F_C2", "type": "REQUIRED", "predicate": "AllIntendedParticipantsAreMembersOfSameAppGroup()"},
    {"id": "R1C8F_C3", "type": "FORBIDDEN", "predicate": "NoReadFrom(OUTSIDE_APP_GROUP | SYSTEM)"},
    {"id": "R1C8F_C4", "type": "FORBIDDEN", "predicate": "NoWriteAccessibleBy(OUTSIDE_APP_GROUP)"},
    {"id": "R1C8F_C5", "type": "EXCEPTION", "predicate": "SystemGlobalFallbackIsExemptWhenRequestedGroupKeyMissing()"},
]
C_CA92 = [
    {"id": "RCA92_C1", "type": "REQUIRED", "predicate": "DefaultsDomainIs(APP_PRIVATE)"},
    {"id": "RCA92_C2", "type": "FORBIDDEN", "predicate": "NoReadFrom(OTHER_APP | SYSTEM)"},
    {"id": "RCA92_C3", "type": "FORBIDDEN", "predicate": "NoWriteAccessibleBy(OTHER_APP)"},
]
GROUPS = ["group.org.demo.app$(SIGNING_DISAMBIGUATOR)"]


def batch(tmp, name, sites, code="1C8F.1", constraints=C_1C8F, unit_location="repos/demo-app"):
    header = {"_batch": name, "unit_location": unit_location, "source_dir": f"src/{unit_location}",
              "n_sites": len(sites), "site_ids": [s["site_id"] for s in sites],
              "declares": {"UserDefaults": [code]},
              "hosts": [{"repo": "demo/app", "declares": {"UserDefaults": [code]},
                         "app_facts": {"app_group_ids": GROUPS}}],
              "build_facts": {"projects": [{"path": "Demo.xcodeproj", "targets": [
                  {"name": "Demo", "kind": "APP", "app_groups": GROUPS}]}]},
              "reasons": {code: {"category": "UserDefaults", "constraints": constraints}}}
    p = tmp / f"{name}.jsonl"
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(header, ensure_ascii=False) + "\n")
        for s in sites:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    return p


def site(sid, code="1C8F.1", **kw):
    s = {"site_id": sid, "file": "App/Seen.swift", "line": 12, "col": 5,
         "api": "user_defaults_family", "category": "UserDefaults", "site_class": "RRA",
         "declared_reasons": {"UserDefaults": [code]} if code else {},
         "operation_prefill": "WRITE", "domain_hint": "APP_PRIVATE", "key": None,
         "wrapper_ref": None, "callers": None, "guard_live_on_ios": True,
         "instance_domains": None, "unit_role_prefill": "FIRST_PARTY",
         "declaring_unit_prefill": "APP", "enclosing_function": SIG, "compile_guard": [],
         "target": {"name": "Demo", "kind": "APP", "app_groups": GROUPS},
         "context": {"start_line": 1, "site_line": 12, "function_lines": [11, 13],
                     "truncated": False, "lines": CTX}}
    s.update(kw)
    return s


def rec(sid, **kw):
    r = {"site_id": sid, "site_class": "RRA", "is_api_use": "YES",
         "is_api_use_reason": f"L12: {UD_LINE.strip()}", "unit_confirmed": "YES",
         "unit_role": "FIRST_PARTY", "declaring_unit": "APP", "operation": "WRITE",
         "value_fate": ["PERSISTED_LOCAL"], "fate_evidence": f"L12: {UD_LINE.strip()}",
         "escape": None, "applicable_reason": "1C8F.1",
         "constraint_verdicts": {"R1C8F_C1": "CONFLICT", "R1C8F_C2": "SUPPORTED",
                                 "R1C8F_C3": "SUPPORTED", "R1C8F_C4": "SUPPORTED",
                                 "R1C8F_C5": "UNKNOWN"},
         "alt_equivalence": None, "exceeds_all_reasons": None, "needs_context": None,
         "flows": [], "notes": f"TERMINAL: L12: {UD_LINE.strip()}；域是 UserDefaults.standard"}
    r.update(kw)
    return r


def write(tmp, name, recs):
    p = tmp / name
    with io.open(p, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def run(bp, op, **kw):
    return V.validate(bp, op, **kw)


def rule_errs(errors, marker):
    return [e for e in errors if marker in e]


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="avr_"))
    try:
        # ---------------------------------------------------------------- R-UD-DOMAIN
        # wikipedia-ios' shape: `.standard` under a 1C8F.1-only declaration.  C1 conflicts
        # (the declared reason wants an App Group), C2 does not (the only participant is
        # this app).  The second pass wrote UNKNOWN on C2 for 337 pairs.
        bp = batch(tmp, "b0001__repos__demo-app", [site("s1")])
        bad = write(tmp, "bad.out.jsonl", [rec("s1", constraint_verdicts={
            "R1C8F_C1": "CONFLICT", "R1C8F_C2": "UNKNOWN", "R1C8F_C3": "SUPPORTED",
            "R1C8F_C4": "SUPPORTED", "R1C8F_C5": "UNKNOWN"})])
        errors = run(bp, bad)[1]
        hits = rule_errs(errors, "§4.5 规则表")
        assert len(hits) == 1 and "R1C8F_C2" in hits[0] and "应为 SUPPORTED" in hits[0], errors
        assert errors == hits, errors          # nothing else fires on this record

        good = write(tmp, "good.out.jsonl", [rec("s1")])
        assert run(bp, good)[1] == [], run(bp, good)[1]
        stats = run(bp, good)[3]
        assert stats[("rule_check", "R-UD-DOMAIN:checked")] == 2, dict(stats)   # C1 and C2

        # the same record passes once the departure is declared, and is counted
        ov = write(tmp, "ov.out.jsonl", [rec("s1", constraint_verdicts={
            "R1C8F_C1": "CONFLICT", "R1C8F_C2": "UNKNOWN", "R1C8F_C3": "SUPPORTED",
            "R1C8F_C4": "SUPPORTED", "R1C8F_C5": "UNKNOWN"},
            notes=f"TERMINAL: L12: {UD_LINE.strip()}；DOMAIN_OVERRIDE: 这个键由键盘扩展也写")])
        _, errors, _, stats, disagree, _ = run(bp, ov)
        assert rule_errs(errors, "§4.5 规则表") == [], errors
        assert stats[("rule_check", "R-UD-DOMAIN:overridden")] == 1, dict(stats)
        assert disagree["domain_rule_override"] == 1, dict(disagree)

        # ------------------------------------------------- the row 1.11 rewrites
        # wBlock's one-shot suite: 1.10 calls a non-group suite a CONFLICT for
        # `DefaultsDomainIs(APP_PRIVATE)`, 1.11 calls it SUPPORTED.
        s2 = site("s2", code="CA92.1", domain_hint="SUITE:test.demo.popup.abc",
                  context={"start_line": 1, "site_line": 12, "function_lines": [11, 13],
                           "truncated": False, "lines": CTX_SUITE})
        bp2 = batch(tmp, "b0002__repos__demo-app", [s2], code="CA92.1", constraints=C_CA92)
        suite_rec = dict(applicable_reason="CA92.1", operation="ACQUIRE",
                         value_fate=["LOCAL_ONLY"],
                         is_api_use_reason=f"L12: {SUITE_LINE.strip()}",
                         fate_evidence=f"L12: {SUITE_LINE.strip()}",
                         notes=f"NO_VALUE：ACQUIRE 没有可追的值。L12: {SUITE_LINE.strip()}")
        conflicting = write(tmp, "ca92.out.jsonl", [rec(
            "s2", constraint_verdicts={"RCA92_C1": "CONFLICT", "RCA92_C2": "SUPPORTED",
                                       "RCA92_C3": "SUPPORTED"}, **suite_rec)])
        assert rule_errs(run(bp2, conflicting, rule_table_version="1.10")[1], "§4.5 规则表") == []
        hits = rule_errs(run(bp2, conflicting, rule_table_version="1.11")[1], "§4.5 规则表")
        assert len(hits) == 1 and "RCA92_C1" in hits[0] and "应为 SUPPORTED" in hits[0], hits

        # ------------------------------------ prefill wrong, annotation right
        # `UserDefaults(suiteName: GroupIdentifier.shared.value) ?? .standard`: the hint
        # says APP_PRIVATE, the notes resolve a suite.  Contradictory evidence yields no
        # expectation, so the (correct) CONFLICT is not reported as an error.
        fallback = write(tmp, "fallback.out.jsonl", [rec(
            "s2", constraint_verdicts={"RCA92_C1": "CONFLICT", "RCA92_C2": "SUPPORTED",
                                       "RCA92_C3": "SUPPORTED"},
            **{**suite_rec,
               "notes": ("NO_VALUE：C1：域是 L12: let d = "
                         "UserDefaults(suiteName: GroupIdentifier.shared.value) ?? .standard"),
               "is_api_use_reason": "L12: let d = UserDefaults(suiteName: GroupIdentifier.shared.value) ?? .standard",
               "fate_evidence": "L12: let d = UserDefaults(suiteName: GroupIdentifier.shared.value) ?? .standard"})])
        s2b = site("s2", code="CA92.1", domain_hint="APP_PRIVATE",
                   context={"start_line": 1, "site_line": 12, "function_lines": [11, 13],
                            "truncated": False,
                            "lines": _ctx("    let d = UserDefaults(suiteName: GroupIdentifier.shared.value) ?? .standard")})
        bp2b = batch(tmp, "b0003__repos__demo-app", [s2b], code="CA92.1", constraints=C_CA92)
        _, errors, _, stats, _, _ = run(bp2b, fallback, rule_table_version="1.11")
        assert rule_errs(errors, "§4.5 规则表") == [], errors
        assert stats[("rule_check", "R-UD-DOMAIN:UNKNOWN:no_expectation")] == 1, dict(stats)

        # ---------------------------------------------- per-domain verdict dicts
        s3 = site("s3", domain_hint="MIXED_DOMAINS:APP_GROUP|APP_PRIVATE",
                  instance_domains=[{"domain": "APP_GROUP"}, {"domain": "APP_PRIVATE"}])
        bp3 = batch(tmp, "b0004__repos__demo-app", [s3])
        mixed_ok = write(tmp, "mixed.out.jsonl", [rec("s3", constraint_verdicts={
            "R1C8F_C1": {"APP_GROUP": "SUPPORTED", "APP_PRIVATE": "CONFLICT"},
            "R1C8F_C2": "SUPPORTED", "R1C8F_C3": "SUPPORTED", "R1C8F_C4": "SUPPORTED",
            "R1C8F_C5": "UNKNOWN"})])
        assert rule_errs(run(bp3, mixed_ok)[1], "§4.5 规则表") == [], run(bp3, mixed_ok)[1]
        mixed_bad = write(tmp, "mixedbad.out.jsonl", [rec("s3", constraint_verdicts={
            "R1C8F_C1": {"APP_GROUP": "CONFLICT", "APP_PRIVATE": "CONFLICT"},
            "R1C8F_C2": "SUPPORTED", "R1C8F_C3": "SUPPORTED", "R1C8F_C4": "SUPPORTED",
            "R1C8F_C5": "UNKNOWN"})])
        hits = rule_errs(run(bp3, mixed_bad)[1], "§4.5 规则表")
        assert len(hits) == 1 and "R1C8F_C1[APP_GROUP]" in hits[0], hits

        # ----------------------------------------------------------- R-ALT-EXCEEDS
        alt_site = site("a1", code=None, site_class="ALT", declared_reasons={},
                        domain_hint=None, operation_prefill="READ", alt_tier="NEAR")
        bpa = batch(tmp, "b0005__deps__demo-lib", [alt_site], unit_location="deps/demo-lib@1")
        alt_common = dict(site_class="ALT", applicable_reason="NA", constraint_verdicts={},
                          alt_equivalence="NEAR_EQUIVALENT", operation="READ",
                          value_fate=["LOCAL_ONLY"], unit_role="THIRD_PARTY",
                          declaring_unit="demo-lib",
                          flows=[{"path_type": "TRIGGER", "sink_unit": None, "hops": [],
                                  "sink_use": [], "sink_value": None, "sink_declared": None,
                                  "verdict": "UNKNOWN", "evidence": "",
                                  "stuck_at": {"hop": 0, "why": "NO_CONSUMER_FOUND: 语料里没有宿主调用"},
                                  "notes": ""}])
        # the first pass's shape: local-only, no escape, yet 越界=YES
        bad_alt = write(tmp, "alt_bad.out.jsonl",
                        [rec("a1", exceeds_all_reasons="YES", **alt_common)])
        hits = rule_errs(run(bpa, bad_alt)[1], "§4.7")
        assert len(hits) == 1 and "应为 NO" in hits[0], run(bpa, bad_alt)[1]
        ok_alt = write(tmp, "alt_ok.out.jsonl",
                       [rec("a1", exceeds_all_reasons="NO", **alt_common)])
        assert run(bpa, ok_alt)[1] == [], run(bpa, ok_alt)[1]
        ov_alt = write(tmp, "alt_ov.out.jsonl", [rec(
            "a1", exceeds_all_reasons="YES",
            **{**alt_common, "notes": f"TERMINAL: L12: {UD_LINE.strip()}；ALT_OVERRIDE: 原值在别处被读回"})])
        _, errors, _, stats, _, _ = run(bpa, ov_alt)
        assert rule_errs(errors, "§4.7") == [], errors
        assert stats[("rule_check", "R-ALT-EXCEEDS:overridden")] == 1, dict(stats)
        # a raw escape that has not been traced: §4.7 defers, so no expectation
        defer = write(tmp, "alt_defer.out.jsonl", [rec("a1", exceeds_all_reasons="UNKNOWN", **{
            **alt_common, "value_fate": ["RETURNED"],
            "escape": {"kind": "RETURNED", "value": "RAW", "target": "demo-lib::Clock.now",
                       "symbols": ["now"]},
            "flows": alt_common["flows"] + [{
                "path_type": "RETURN_VALUE", "sink_unit": None, "hops": [], "sink_use": [],
                "sink_value": None, "sink_declared": None, "verdict": "UNKNOWN", "evidence": "",
                "stuck_at": {"hop": 0, "why": "NO_CONSUMER_FOUND: 宿主没有调用"}, "notes": ""}]})])
        _, errors, _, stats, _, _ = run(bpa, defer)
        assert rule_errs(errors, "§4.7") == [], errors
        assert stats[("rule_check", "R-ALT-EXCEEDS:no_expectation")] == 1, dict(stats)

        # ------------------------------------------------------ --rule-checks off
        assert rule_errs(run(bp, bad, rule_checks=False)[1], "§4.5 规则表") == []
        assert rule_errs(run(bpa, bad_alt, rule_checks=False)[1], "§4.7") == []

        # --------------------------------------------------------- --site-index
        # One output file holding records from two different batches -- the shape the
        # first pass's 188 files have relative to the second pass's 183.
        idx_dir = tmp / "batches"
        idx_dir.mkdir()
        batch(idx_dir, "b0001__repos__demo-app", [site("s1")])
        batch(idx_dir, "b0002__repos__demo-app", [site("s9", domain_hint="APP_PRIVATE")],
              unit_location="repos/demo-app")
        index, dup = V.build_site_index(idx_dir)
        assert set(index) == {"s1", "s9"} and dup == [], (sorted(index), dup)
        mixed_file = write(tmp, "crossbatch.out.jsonl", [rec("s1"), rec("s9")])
        _, errors, warnings, stats, _, _ = run(None, mixed_file, site_index=index)
        assert errors == [], errors
        assert stats[("rule_check", "R-UD-DOMAIN:checked")] == 4, dict(stats)
        # a site the batches do not know is skipped with a warning, not an error
        stray = write(tmp, "stray.out.jsonl", [rec("s1"), rec("zz")])
        _, errors, warnings, stats, _, _ = run(None, stray, site_index=index)
        assert errors == [] and any("不在任何批次" in w for w in warnings), (errors, warnings)
        assert stats[("site_index", "not_in_any_batch")] == 1, dict(stats)
        # and the per-batch count/order checks do not fire in this mode
        one_only = write(tmp, "one.out.jsonl", [rec("s9")])
        assert run(None, one_only, site_index=index)[1] == []

        # ----------------------------------------------- CLI plumbing end to end
        out_json = tmp / "report.json"
        rc = V.main(["--batches", str(idx_dir), "--site-index", "--rule-table", "1.11",
                     "--json", str(out_json), str(mixed_file)])
        assert rc == 0, rc
        payload = json.loads(out_json.read_text(encoding="utf-8"))
        assert payload["run"]["rule_table"] == "1.11" and payload["run"]["site_index"] is True
        assert payload["totals"]["errors"] == 0
        assert payload["totals"]["stats"]["rule_check=R-UD-DOMAIN:checked"] == 4, payload["totals"]

        print("PASS  §4.5 规则表（1.10/1.11 两版、MIXED 逐域、矛盾证据不判）+ §4.7 ALT 越界 + "
              "DOMAIN_OVERRIDE/ALT_OVERRIDE 出口 + --rule-checks off 复现旧口径 + "
              "--site-index 跨批次校验（不认的站点只警告）+ --json 带运行口径与合计")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
