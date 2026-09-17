#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate annotation output against the batch it answers, and compare two passes.

    annotate_validate.py --batches DIR OUT.jsonl [OUT.jsonl …]
    annotate_validate.py --batches DIR --compare A.jsonl B.jsonl

Blocking errors (exit 1): a line that is not JSON, a site missing / extra /
duplicated / out of order, a value outside the vocabulary of
ANNOTATION_PRINCIPLES.md Appendix A, constraint_verdicts keys that do not
match the batch header's reasons, a SUPPORTED / CONFLICT verdict without an
`L<n>` evidence line, contradictions between fields (escape without the
matching value_fate, is_api_use NO with an operation).

Principles 1.9 rules checked here as well: operations without a data value
(SYNC / ACQUIRE / REMOVE / OBSERVE) carry value_fate ["LOCAL_ONLY"] and no
escape; a WRAPPED site whose wrapper_ref says SET carries ["PERSISTED_LOCAL"];
an ALT site judged NO has exceeds_all_reasons null; a per-domain verdict dict
is only allowed on a DefaultsDomainIs constraint of a MIXED_DOMAINS site;
needs_context carries machine-readable `requests` (kind / symbol).

Warnings: things worth a look but not a rejection -- disagreement with the
scanner's prefills (that is the annotator's job, we only count it), an UNSURE
fate without a needs_context request, a needs_context with no UNKNOWN /
UNSURE anywhere, a needs_context whose only UNKNOWN is an EXCEPTION clause.

Output lines beginning with `#` are ignored (the batch-name comment the
prompt asks for).
"""

import argparse
import collections
import io
import json
import pathlib
import re
import sys

VOCAB = {
    "site_class": {"RRA", "ALT"},
    "is_api_use": {"YES", "NO", "UNSURE"},
    "unit_confirmed": {"YES", "NO", "UNSURE"},
    "unit_role": {"FIRST_PARTY", "THIRD_PARTY", "VENDORED_THIRD_PARTY", "FORK_OF_THIRD_PARTY", "UNSURE"},
    "operation": {"READ", "WRITE", "REMOVE", "OBSERVE", "SYNC", "WRAPPED", "ACQUIRE", "NA"},
    "value_fate": {"LOCAL_ONLY", "UI_DISPLAY", "LOGGED", "PERSISTED_LOCAL", "STORED", "RETURNED", "PASSED_OUT", "OFF_DEVICE", "DERIVED_ID", "UNSURE"},
    "escape.kind": {"RETURNED", "PASSED_OUT", "PERSISTED_LOCAL", "STORED"},
    "escape.value": {"RAW", "DERIVED"},
    "verdict": {"SUPPORTED", "CONFLICT", "UNKNOWN"},
    "alt_equivalence": {"NEAR_EQUIVALENT", "CONDITIONAL", "PARTIAL_DATUM"},
    "exceeds_all_reasons": {"YES", "NO", "UNKNOWN"},
}
REQUIRED = ["site_id", "site_class", "is_api_use", "is_api_use_reason", "unit_confirmed", "unit_role", "declaring_unit",
            "operation", "value_fate", "fate_evidence", "escape", "applicable_reason", "constraint_verdicts",
            "alt_equivalence", "exceeds_all_reasons", "needs_context", "flows", "notes"]
FLOW_KEYS = ["path_type", "sink_unit", "hops", "sink_use", "sink_value", "sink_declared", "verdict", "evidence", "stuck_at", "notes"]
PATH_TYPES = {"RETURN_VALUE", "TRIGGER", "CHANNEL"}
RE_LOC = re.compile(r".+:\d+$")
RE_EVIDENCE = re.compile(r"\bL\d+")
#: unit-level constraints may cite worksheet / batch-header fields instead of a code line (§4.5)
RE_UNIT_EVIDENCE = re.compile(r"\bL\d+|unit_kind=|unit_location=|app_facts|清单|manifest|PrivacyInfo")
NO_CODES = ("COMPILE_GUARD_EXCLUDES_IOS_RELEASE", "NAME_COLLISION", "WRAPPER_CALL", "DECLARATION", "STRING_LITERAL", "WRAPPER_TYPE_MEMBER", "OTHER")
NO_VALUE_OPS = ("SYNC", "ACQUIRE", "REMOVE", "OBSERVE")
REQUEST_KINDS = {"DEFINITION", "CALLERS", "BODY", "TYPE", "FILE"}


def read_batch(path):
    lines = io.open(path, encoding="utf-8").read().splitlines()
    header = json.loads(lines[0])
    sites = {}
    for l in lines[1:]:
        if l.strip():
            s = json.loads(l); sites[s["site_id"]] = s
    return header, sites


def read_output(path):
    rows = []; bad = []
    for n, l in enumerate(io.open(path, encoding="utf-8").read().splitlines(), 1):
        if not l.strip() or l.lstrip().startswith("#"): continue
        try:
            rows.append((n, json.loads(l)))
        except json.JSONDecodeError as e:
            bad.append((n, str(e)))
    return rows, bad


def pseudo_batch_from_worksheets(ws_dir, rows):
    """When only the worksheets are at hand (no batch pack): the sites the output names,
    with reasons rebuilt from each record's reason_constraints.  Count / order checks
    are skipped (there is no batch header to check against)."""
    ids = [r["site_id"] for _, r in rows if isinstance(r, dict) and "site_id" in r]
    want = set(ids); sites = {}; reasons = {}
    for p in sorted(pathlib.Path(ws_dir).glob("*.jsonl")):
        for l in io.open(p, encoding="utf-8"):
            s = json.loads(l)
            if s["site_id"] in want:
                sites[s["site_id"]] = s
                for cat, rr in (s.get("reason_constraints") or {}).items():
                    for code, r in rr.items():
                        reasons.setdefault(code, {"category": cat, "constraints": [{k: c.get(k) for k in ("id", "type", "predicate")} for c in r.get("constraints", [])]})
    header = {"_batch": "(worksheets)", "site_ids": [i for i in ids if i in sites], "reasons": reasons, "n_sites": len(sites)}
    return header, sites


def find_batch(batches_dir, rows):
    """The batch whose site_ids best cover the output's site_ids."""
    ids = {r["site_id"] for _, r in rows if isinstance(r, dict) and "site_id" in r}
    best = None
    for p in sorted(pathlib.Path(batches_dir).glob("*__*.jsonl")):
        h = json.loads(io.open(p, encoding="utf-8").readline())
        k = len(ids & set(h["site_ids"]))
        if k and (best is None or k > best[0]): best = (k, p)
    return best[1] if best else None


def validate(batch_path, out_path, ws_dir=None):
    rows, bad = read_output(out_path)
    if batch_path is not None:
        header, sites = read_batch(batch_path)
    else:
        header, sites = pseudo_batch_from_worksheets(ws_dir, rows)
    errors, warnings = [], []
    for n, e in bad: errors.append(f"第 {n} 行不是 JSON: {e}")
    expected = header["site_ids"]
    got = [r.get("site_id") for _, r in rows]
    dup = [k for k, v in collections.Counter(got).items() if v > 1]
    if dup: errors.append(f"重复 site_id: {dup}")
    if batch_path is not None:
        if len(got) != len(expected): errors.append(f"行数 {len(got)} ≠ n_sites {len(expected)}")
        missing = [k for k in expected if k not in got]; extra = [k for k in got if k not in sites]
        if missing: errors.append(f"缺 {len(missing)} 个: {missing[:5]}{'…' if len(missing) > 5 else ''}")
        if extra: errors.append(f"多出 {len(extra)} 个不属于本批: {extra[:5]}")
        if not missing and not extra and got != expected: warnings.append("顺序与批次头 site_ids 不一致")
    else:
        unknown = [k for k in got if k not in sites]
        warnings.append(f"无批次包：只校验字段，不校验数量/顺序；{len(unknown)} 个 site_id 在工作表里找不到（跳过）: {unknown[:5]}")

    stats = collections.Counter(); disagree = collections.Counter()
    for n, r in rows:
        sid = r.get("site_id"); s = sites.get(sid)
        if not s: continue
        tag = f"{sid} ({s['file'].split('/')[-1]}:{s['line']})"
        for k in REQUIRED:
            if k not in r: errors.append(f"{tag} 缺字段 {k}")
        unknown = set(r) - set(REQUIRED)
        if unknown: warnings.append(f"{tag} 多余字段 {sorted(unknown)}")
        if any(k not in r for k in REQUIRED): continue
        # vocabulary
        for k in ("site_class", "is_api_use", "unit_confirmed", "unit_role", "operation"):
            if r[k] not in VOCAB[k]: errors.append(f"{tag} {k}={r[k]!r} 不在词表")
        if r["site_class"] != s["site_class"]: errors.append(f"{tag} site_class 被改成 {r['site_class']}（输入 {s['site_class']}）")
        if not isinstance(r["value_fate"], list) or any(v not in VOCAB["value_fate"] for v in r["value_fate"]):
            errors.append(f"{tag} value_fate={r['value_fate']!r} 不在词表")
        esc = r["escape"]
        if esc is not None:
            if not isinstance(esc, dict) or esc.get("kind") not in VOCAB["escape.kind"] or esc.get("value") not in VOCAB["escape.value"] or not esc.get("target"):
                errors.append(f"{tag} escape={esc!r} 形状/词表错")
            elif esc["kind"] not in r["value_fate"]:
                errors.append(f"{tag} escape.kind={esc['kind']} 但 value_fate 里没有它")
            else:
                if "::" not in esc["target"]: warnings.append(f"{tag} escape.target 不是 <单元>::<类型>.<成员> 的写法: {esc['target']!r}")
                if not isinstance(esc.get("symbols"), list) or not esc.get("symbols"): warnings.append(f"{tag} escape.symbols 缺失或为空")
        # flows (§5): every escape carries at least one flow record (a searched-but-not-found one counts)
        flows = r["flows"]
        if not isinstance(flows, list):
            errors.append(f"{tag} flows 必须是列表")
        else:
            if esc is not None and r["is_api_use"] != "NO" and not flows and esc.get("kind") in ("PASSED_OUT", "RETURNED", "STORED", "PERSISTED_LOCAL"):
                warnings.append(f"{tag} 有 escape 但 flows 为空（出了单元的要追，没找到接收方也要记 NO_CONSUMER_FOUND；留在单元内的写 notes IN_UNIT）")
            if r["is_api_use"] == "NO" and flows: errors.append(f"{tag} is_api_use=NO 但 flows 非空")
            for i, fl in enumerate(flows):
                ft = f"{tag} flows[{i}]"
                if not isinstance(fl, dict): errors.append(f"{ft} 不是对象"); continue
                missing = [k for k in FLOW_KEYS if k not in fl]
                if missing: errors.append(f"{ft} 缺字段 {missing}"); continue
                if fl["path_type"] not in PATH_TYPES: errors.append(f"{ft} path_type={fl['path_type']!r}")
                if fl["verdict"] not in VOCAB["verdict"]: errors.append(f"{ft} verdict={fl['verdict']!r}")
                if not isinstance(fl["sink_use"], list) or any(v not in VOCAB["value_fate"] for v in fl["sink_use"]): errors.append(f"{ft} sink_use={fl['sink_use']!r}")
                if fl["sink_value"] not in (None, "RAW", "DERIVED"): errors.append(f"{ft} sink_value={fl['sink_value']!r}")
                if fl["sink_declared"] not in (None, True, False): errors.append(f"{ft} sink_declared 要是 true/false/null")
                hops = fl["hops"]
                if not isinstance(hops, list) or any(not isinstance(h, dict) or not h.get("unit") or not h.get("symbol") or not RE_LOC.match(str(h.get("loc", ""))) for h in hops):
                    errors.append(f"{ft} hops 每项要有 unit / symbol / loc(<文件>:<行>)")
                found = fl["sink_unit"] is not None
                if found and not hops: errors.append(f"{ft} 有 sink_unit 但没有 hops")
                if found and fl["verdict"] in ("SUPPORTED", "CONFLICT") and not (RE_EVIDENCE.search(fl["evidence"]) or RE_LOC.search(fl["evidence"].split(":")[0] + ":1")):
                    errors.append(f"{ft} verdict={fl['verdict']} 但 evidence 没有行号")
                if not found:
                    if fl["verdict"] != "UNKNOWN" or not (fl.get("stuck_at") or {}).get("why"): errors.append(f"{ft} 没找到接收方时 verdict 应为 UNKNOWN 且 stuck_at.why 写明查了什么")
                if fl["verdict"] == "UNKNOWN" and found and not (fl.get("stuck_at") or {}).get("why"): warnings.append(f"{ft} UNKNOWN 却没有 stuck_at.why")
                stats[("flow", fl["path_type"])] += 1; stats[("flow_verdict", fl["verdict"])] += 1
        nc = r["needs_context"]
        if nc is not None:
            if not isinstance(nc, dict) or not nc.get("what") or not nc.get("why"):
                errors.append(f"{tag} needs_context 要有 what 与 why")
            else:
                reqs = nc.get("requests")
                if not isinstance(reqs, list) or not reqs:
                    errors.append(f"{tag} needs_context 要有 requests 列表（§4.6）")
                else:
                    for q in reqs:
                        if not isinstance(q, dict) or q.get("kind") not in REQUEST_KINDS or not q.get("symbol"):
                            errors.append(f"{tag} needs_context.requests 项要有 kind∈{sorted(REQUEST_KINDS)} 与 symbol: {q!r}")
        # reason / verdicts
        reasons = header.get("reasons", {})
        ar = r["applicable_reason"]; cv = r["constraint_verdicts"]
        declared = [c for cat, codes in (s.get("declared_reasons") or {}).items() for c in codes] if r["site_class"] == "RRA" else []
        if r["site_class"] == "ALT":
            if ar != "NA" or cv != {}: errors.append(f"{tag} ALT 站点 applicable_reason/constraint_verdicts 必须是 NA / {{}}")
            if r["alt_equivalence"] not in VOCAB["alt_equivalence"]: errors.append(f"{tag} alt_equivalence={r['alt_equivalence']!r}")
            if r["is_api_use"] == "NO":
                if r["exceeds_all_reasons"] is not None: errors.append(f"{tag} ALT 站点判 NO 时 exceeds_all_reasons 应为 null（§4.7）")
            elif r["exceeds_all_reasons"] not in VOCAB["exceeds_all_reasons"]: errors.append(f"{tag} exceeds_all_reasons={r['exceeds_all_reasons']!r}")
        else:
            if r["alt_equivalence"] is not None or r["exceeds_all_reasons"] is not None: errors.append(f"{tag} RRA 站点的 ALT 字段应为 null")
            if r["is_api_use"] == "NO":
                if ar != "NA": errors.append(f"{tag} is_api_use=NO 时 applicable_reason 应为 NA，得到 {ar!r}")
                if cv != {}: errors.append(f"{tag} is_api_use=NO 时 constraint_verdicts 应为 {{}}")
            elif ar == "NONE":
                if declared: errors.append(f"{tag} applicable_reason=NONE 但 declared_reasons={declared}")
                if cv != {}: errors.append(f"{tag} NONE 时 constraint_verdicts 应为 {{}}")
            elif ar == "UNSURE":
                if len(declared) < 2: errors.append(f"{tag} applicable_reason=UNSURE 只在多条声明时允许（declared={declared}）")
                want = {f"{c}/{k['id']}" for c in declared for k in reasons.get(c, {}).get("constraints", [])}
                if set(cv) != want: errors.append(f"{tag} UNSURE 时 verdict 键应为 {sorted(want)}，得到 {sorted(cv)}")
            elif ar == "NA":
                errors.append(f"{tag} is_api_use={r['is_api_use']} 的 RRA 站点不能填 NA")
            else:
                if ar not in declared: errors.append(f"{tag} applicable_reason={ar} 不在 declared_reasons={declared}")
                want = {k["id"] for k in reasons.get(ar, {}).get("constraints", [])}
                if set(cv) != want: errors.append(f"{tag} verdict 键 {sorted(cv)} ≠ 约束 id {sorted(want)}")
            for k, v in cv.items():
                vals = list(v.values()) if isinstance(v, dict) else [v]
                if any(x not in VOCAB["verdict"] for x in vals): errors.append(f"{tag} verdict {k}={v!r} 不在词表")
                if isinstance(v, dict):
                    mixed_inst = s.get("instance_domains") and len({d["domain"] for d in s["instance_domains"] if d.get("domain")}) > 1
                    mixed_callers = len([d for d in ((s.get("callers") or {}).get("by_domain") or {}) if d not in ("UNKNOWN",)]) > 1
                    if not (mixed_inst or mixed_callers):
                        errors.append(f"{tag} verdict {k} 按域分开，但工作表没有 MIXED_DOMAINS（instance_domains / callers）")
                    pred = next((c.get("predicate", "") for code in ([ar] if ar not in ("UNSURE",) else declared) for c in reasons.get(code, {}).get("constraints", []) if c.get("id") == k.split("/")[-1]), "")
                    if not pred.startswith("DefaultsDomainIs"):
                        errors.append(f"{tag} verdict {k} 按域分开，但它不是 DefaultsDomainIs 类约束（{pred}）")
                if any(x in ("SUPPORTED", "CONFLICT") for x in vals) and not (RE_EVIDENCE.search(r["fate_evidence"]) or RE_UNIT_EVIDENCE.search(r["notes"])):
                    errors.append(f"{tag} verdict {k}={v} 没有 L<n> 证据行（单元级约束可引用字段）")
        # consistency
        if r["is_api_use"] == "NO":
            if not r["is_api_use_reason"].startswith(NO_CODES): errors.append(f"{tag} is_api_use=NO 的 is_api_use_reason 必须以代码开头（§4.1）: {r['is_api_use_reason'][:40]!r}")
            stats[("no_code", r["is_api_use_reason"].split(":")[0].split(" ")[0].split("（")[0])] += 1
            if r["operation"] != "NA": errors.append(f"{tag} is_api_use=NO 但 operation={r['operation']}")
            if r["value_fate"] or esc is not None: errors.append(f"{tag} is_api_use=NO 但 value_fate/escape 非空")
        else:
            if r["site_class"] == "RRA" and r["operation"] == "NA" and r["is_api_use"] == "YES": errors.append(f"{tag} is_api_use=YES 但 operation=NA")
            if r["value_fate"] and not RE_EVIDENCE.search(r["fate_evidence"]): errors.append(f"{tag} value_fate 非空但 fate_evidence 没有 L<n> 证据行")
            if "UNSURE" in r["value_fate"] and r["needs_context"] is None: warnings.append(f"{tag} value_fate 含 UNSURE 却没有 needs_context")
            if r["operation"] in NO_VALUE_OPS and (r["value_fate"] != ["LOCAL_ONLY"] or esc is not None):
                errors.append(f"{tag} {r['operation']} 没有可追的值：value_fate 应为 [LOCAL_ONLY]、escape null（§4.2）")
            wr = s.get("wrapper_ref") or []
            if r["operation"] == "WRAPPED" and wr and wr[0].get("access") == "SET" and (r["value_fate"] != ["PERSISTED_LOCAL"] or esc is not None):
                errors.append(f"{tag} 经封装 setter 的写入：value_fate 应为 [PERSISTED_LOCAL]、escape null（§4.2）")
            if r["operation"] == "WRITE" and (r["value_fate"] != ["PERSISTED_LOCAL"] or esc is not None):
                errors.append(f"{tag} WRITE 的 value_fate 应为 [PERSISTED_LOCAL]、escape null（§4.2）")
        if r["needs_context"] is not None:
            blob = json.dumps(r, ensure_ascii=False)
            if "UNKNOWN" not in blob and "UNSURE" not in blob: warnings.append(f"{tag} 有 needs_context 但没有任何 UNKNOWN/UNSURE")
            else:
                unknown_ids = [k for k, v in cv.items() if (v == "UNKNOWN" if not isinstance(v, dict) else "UNKNOWN" in v.values())]
                exc_ids = {c["id"] for code in reasons for c in reasons[code].get("constraints", []) if c.get("type") == "EXCEPTION"}
                other = json.dumps({k: v for k, v in r.items() if k not in ("constraint_verdicts", "needs_context", "notes")}, ensure_ascii=False)
                if unknown_ids and all(k.split("/")[-1] in exc_ids for k in unknown_ids) and "UNKNOWN" not in other and "UNSURE" not in other:
                    warnings.append(f"{tag} needs_context 只为 EXCEPTION 条款的 UNKNOWN 而填（§4.6 说不填）")
        # prefill disagreement (counted, not judged)
        if s.get("operation_prefill") and r["operation"] not in ("NA",) and s["operation_prefill"] not in ("NA", "WRAPPED?") and r["operation"] != s["operation_prefill"]:
            disagree["operation"] += 1
        if s.get("guard_live_on_ios") is False and r["is_api_use"] != "NO": disagree["guard_dead_but_YES"] += 1
        if s.get("guard_live_on_ios") is True and r["is_api_use"] == "NO" and "GUARD" in r["is_api_use_reason"]: disagree["guard_live_but_guard_NO"] += 1
        if r["unit_role"] != (s.get("unit_role_prefill") or "").rstrip("?"): disagree["unit_role"] += 1
        if s.get("declaring_unit_prefill") and r["declaring_unit"] != s["declaring_unit_prefill"]: disagree["declaring_unit"] += 1
        stats[("is_api_use", r["is_api_use"])] += 1
        stats[("operation", r["operation"])] += 1
        for v in r["value_fate"]: stats[("value_fate", v)] += 1
        for k, v in cv.items():
            for x in (v.values() if isinstance(v, dict) else [v]): stats[("verdict", x)] += 1
        if r["needs_context"]: stats[("needs_context", "yes")] += 1
    return header, errors, warnings, stats, disagree, rows


def compare(batch_path, a_path, b_path, ws_dir=None):
    A = {r["site_id"]: r for _, r in read_output(a_path)[0]}
    B = {r["site_id"]: r for _, r in read_output(b_path)[0]}
    if batch_path is not None:
        header, sites = read_batch(batch_path); order = header["site_ids"]
    else:
        order = list(A); header = {"site_ids": [k for k in order if k in B]}
    common = [k for k in order if k in A and k in B]
    agree = collections.Counter(); total = collections.Counter(); diffs = []
    for k in common:
        a, b = A[k], B[k]
        both_no = a.get("is_api_use") == "NO" and b.get("is_api_use") == "NO"
        for f in ("is_api_use", "operation", "applicable_reason", "alt_equivalence", "exceeds_all_reasons", "unit_role", "declaring_unit"):
            if both_no and f in ("applicable_reason",): continue      # NO sites: reason/verdict conventions differ between principle versions
            total[f] += 1; agree[f] += a.get(f) == b.get(f)
            if a.get(f) != b.get(f): diffs.append((k, f, a.get(f), b.get(f)))
        if both_no: continue
        total["value_fate"] += 1; agree["value_fate"] += set(a.get("value_fate") or []) == set(b.get("value_fate") or [])
        if set(a.get("value_fate") or []) != set(b.get("value_fate") or []): diffs.append((k, "value_fate", a.get("value_fate"), b.get("value_fate")))
        ea, eb = a.get("escape") or {}, b.get("escape") or {}
        total["escape"] += 1; agree["escape"] += (ea.get("kind"), ea.get("value")) == (eb.get("kind"), eb.get("value"))
        for cid in set(a.get("constraint_verdicts") or {}) | set(b.get("constraint_verdicts") or {}):
            total["verdicts"] += 1; va, vb = (a.get("constraint_verdicts") or {}).get(cid), (b.get("constraint_verdicts") or {}).get(cid)
            agree["verdicts"] += va == vb
            if va != vb: diffs.append((k, f"verdict {cid}", va, vb))
    print(f"两遍共有站点 {len(common)} / {len(header['site_ids'])}")
    for f in total: print(f"  {f:20s} 一致 {agree[f]}/{total[f]} = {agree[f] / total[f]:.0%}")
    for k, f, va, vb in diffs: print(f"  DIFF {k} {f}: {va!r} | {vb!r}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batches", default=None, help="批次目录（含 MANIFEST.json）")
    ap.add_argument("--worksheets", default=None, help="没有批次包时，用工作表目录做字段级校验")
    ap.add_argument("outputs", nargs="*")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"))
    ap.add_argument("--json", default=None, help="把校验结果写成 JSON")
    a = ap.parse_args(argv)
    if a.compare:
        rows, _ = read_output(a.compare[0]); bp = find_batch(a.batches, rows) if a.batches else None
        return compare(bp, a.compare[0], a.compare[1], a.worksheets)
    rc = 0; report = {}
    if not a.batches and not a.worksheets:
        ap.error("--batches 或 --worksheets 至少给一个")
    for out in a.outputs:
        rows, _ = read_output(out)
        bp = find_batch(a.batches, rows) if a.batches else None
        if a.batches and not bp:
            print(f"{out}: 找不到对应批次（site_id 一个都对不上）"); rc = 1; continue
        header, errors, warnings, stats, disagree, _ = validate(bp, out, a.worksheets)
        verdict = "通过" if not errors else "打回"
        print(f"== {out} → {bp.name if bp else '(worksheets)'}: {verdict}，{len(errors)} 错误，{len(warnings)} 警告")
        for e in errors: print(f"   ERR  {e}")
        for w in warnings: print(f"   WARN {w}")
        print("   统计:", ", ".join(f"{k[0]}={k[1]}:{v}" for k, v in sorted(stats.items())))
        if disagree: print("   与预填不一致:", dict(disagree))
        report[out] = {"batch": bp.name if bp else None, "errors": errors, "warnings": warnings, "stats": {f"{k[0]}={k[1]}": v for k, v in stats.items()}, "disagree": dict(disagree)}
        if errors: rc = 1
    if a.json: pathlib.Path(a.json).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
