#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Freeze a ground-truth version, or refuse and say which gate failed.

    freeze_gt.py --in merged_1_11/out --batches all_batches --out gt_v1 \\
        --principles annot/ANNOTATION_PRINCIPLES.md --protocol-version 1.11 \\
        --report report --rule-fixes report/rule_fixes.json \\
        --agreement report/agreement_R1_R2_post.json \\
        [--r3 r3/out --agreement-r3 report/agreement_R2_R3.json] \\
        [--adjudications adjudications.jsonl] \\
        --materials report/annot_materials.json --tools tools \\
        --protocols annot/versions --prompts annot/prompts \\
        --candidates PRINCIPLES_1.12_CANDIDATES.md \\
        [--unit-bundle-map report/unit_bundles.json]

Two jobs.

**The gates.** Every condition in `ANNOTATION_ROUND2_PLAN.md` §7 is checked and
any failure stops the run with nothing written, because a ground truth that was
frozen with a known hole is worse than one that is late: everything downstream
cites it by version and nobody re-checks.  The script refuses; it does not warn.

**The record.** `FREEZE.json` carries a `process` section with a sha256 for
every input the pass depended on -- each protocol version and prompt, the
materials the annotator was given, every raw output file of every pass, every
report, and a snapshot of the tools -- so a reader can verify that the frozen
records are the ones the reported numbers were computed from.  A file that is
not there is recorded as MISSING by name rather than skipped: an absent line in
a provenance record reads as "not applicable", and that is the wrong reading.

`gt_confidence` marks how much support each record has, and **nothing is
adjudicated by default** (the standing decision is 宁可漏不可造):

    AGREED       a third pass covered this site and agrees on every judged field
    DISPUTED     a third pass covered it and differs -- kept, flagged, excluded
                 from the strict evaluation set by the consumer, never silently fixed
    SINGLE       no third pass covered it
    ADJUDICATED  a human ruling was supplied for it in `--adjudications`, whose
                 value overrides and whose basis is recorded on the record

The comparison fields are the ones the evaluation joins on: `is_api_use`,
`operation`, `applicable_reason`, `constraint_verdicts`, `value_fate`, `escape`.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import hashlib
import io
import json
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import agreement as AG
import annotate_validate as AV
import rule_table as RT

CONFIDENCE_FIELDS = ("is_api_use", "operation", "applicable_reason", "constraint_verdicts",
                     "value_fate", "escape")
MISSING = "MISSING"


def sha256(path) -> str:
    p = pathlib.Path(path)
    if not p.is_file():
        return MISSING
    h = hashlib.sha256()
    with io.open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha_tree(root, pattern="*"):
    root = pathlib.Path(root) if root else None
    if not root or not root.is_dir():
        return MISSING
    return {str(p.relative_to(root)): sha256(p)
            for p in sorted(root.rglob(pattern)) if p.is_file()}


def _norm(rec, field):
    v = rec.get(field)
    if field == "value_fate":
        return sorted(v or ())
    if field == "escape":
        if not isinstance(v, dict):
            return None
        return {"kind": v.get("kind"), "value": v.get("value")}
    if field == "constraint_verdicts":
        return {k: (dict(sorted(x.items())) if isinstance(x, dict) else x)
                for k, x in sorted((v or {}).items())}
    return v


def compare_fields(a, b):
    return [f for f in CONFIDENCE_FIELDS if _norm(a, f) != _norm(b, f)]


class Gate:
    def __init__(self):
        self.rows = []

    def check(self, name, ok, detail=""):
        self.rows.append({"gate": name, "pass": bool(ok), "detail": str(detail)})
        return bool(ok)

    @property
    def failed(self):
        return [r for r in self.rows if not r["pass"]]

    def to_list(self):
        return list(self.rows)


def run(a):
    gate = Gate()
    in_dir = pathlib.Path(a.in_dir)
    index, dup = AV.build_site_index(a.batches)
    gate.check("批次目录无重复 site_id", not dup, f"{len(dup)} 个重复")

    # ---- load the pass being frozen ----------------------------------------
    _, records, unit_from_file = AG.load_pass(f"GT={in_dir}")
    files = sorted(in_dir.glob("*.out.jsonl"))
    manifest = {}
    mpath = pathlib.Path(a.batches) / "MANIFEST.json"
    if mpath.is_file():
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    man_batches = manifest.get("batches") or []
    man_sites = {s for b in man_batches for s in (b.get("site_ids") or ())}
    gate.check("批次文件数与 MANIFEST 一致", not man_batches or len(man_batches) == len(list(
        pathlib.Path(a.batches).glob("*__*.jsonl"))),
        f"MANIFEST {len(man_batches)} 个，目录里 {len(list(pathlib.Path(a.batches).glob('*__*.jsonl')))} 个")
    if man_sites:
        gate.check("站点数与 MANIFEST 一致", set(records) == man_sites,
                   f"标注 {len(records)}，MANIFEST {len(man_sites)}，"
                   f"缺 {len(man_sites - set(records))}，多 {len(set(records) - man_sites)}")
    else:
        gate.check("站点数与 MANIFEST 一致", True, "MANIFEST 没有 batches 段，跳过")

    # ---- gate: the frozen records pass the validator under the final table --
    # A protocol version that is not itself a rule-table version (a future 1.12)
    # is checked against the newest table there is, and the gate says which, so the
    # number is never read as having been checked against something it was not.
    table = a.protocol_version if a.protocol_version in RT.TABLES else max(RT.TABLES)
    errors_total, err_examples = 0, []
    for p in files:
        errs = AV.validate(None, p, rule_table_version=table, site_index=index)[1]
        errors_total += len(errs)
        err_examples.extend(errs[:2])
    gate.check(f"最终 out/ 过校验器（规则表 {table}）0 错", errors_total == 0,
               f"{errors_total} 条"
               + (f"；协议 {a.protocol_version} 没有对应规则表，按 {table} 校验" if table != a.protocol_version else "")
               + (f"；例：{err_examples[:3]}" if err_examples else ""))

    # ---- gate: reports exist and say what they must -------------------------
    rule_fixes = None
    if a.rule_fixes and pathlib.Path(a.rule_fixes).is_file():
        rule_fixes = json.loads(pathlib.Path(a.rule_fixes).read_text(encoding="utf-8"))
        gate.check("rule_fixes.json 存在", True, a.rule_fixes)
        ins = (rule_fixes.get("run") or {}).get("input_sha256") or {}
        # The corrected pass is the fixer's *output*; its inputs are the pass before
        # it.  What must match is the file set, so a report from another run cannot
        # be passed off as this one's provenance.
        gate.check("rule_fixes 的输入文件集与本次一致",
                   set(ins) == {p.name for p in files} or not ins,
                   f"报告里 {len(ins)} 个文件，本次 {len(files)} 个")
    else:
        gate.check("rule_fixes.json 存在", False, str(a.rule_fixes))
    for label, path in (("agreement（R1 vs R2）", a.agreement),
                        ("agreement（R2 vs R3）", a.agreement_r3)):
        if path is None and label.endswith("R3）") and not a.r3:
            gate.check(label + " 存在", True, "未做第三遍，按 §7 第 4 条可缺")
            continue
        gate.check(label + " 存在", bool(path) and pathlib.Path(path).is_file(), str(path))
    if a.recheck and pathlib.Path(a.recheck).is_file():
        rc = json.loads(pathlib.Path(a.recheck).read_text(encoding="utf-8"))
        bad = len(rc.get("citation_problems") or [])
        gate.check("recheck 的引用不符为 0", bad == 0, f"{bad} 条")
    else:
        gate.check("recheck 报告存在", bool(a.recheck) and pathlib.Path(a.recheck or "").is_file(),
                   str(a.recheck))
    gate.check("PRINCIPLES_1.12_CANDIDATES.md 存在（可以为空）",
               bool(a.candidates) and pathlib.Path(a.candidates).is_file(), str(a.candidates))
    gate.check("原则全文存在", bool(a.principles) and pathlib.Path(a.principles).is_file(),
               str(a.principles))

    # ---- gt_confidence ------------------------------------------------------
    r3 = {}
    if a.r3:
        _, r3, _ = AG.load_pass(f"R3={a.r3}")
    adjud = collections.defaultdict(dict)
    adjud_basis = {}
    if a.adjudications:
        for line in io.open(a.adjudications, encoding="utf-8").read().splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            row = json.loads(line)
            adjud[row["site_id"]][row["field"]] = row["value"]
            adjud_basis.setdefault(row["site_id"], []).append(
                {"field": row["field"], "basis": row.get("basis", "")})
    counts = collections.Counter()
    disputes = []
    for sid, rec in records.items():
        if sid in adjud:
            for f, v in adjud[sid].items():
                if f not in CONFIDENCE_FIELDS and f not in rec:
                    raise SystemExit(f"裁决写了记录里没有的字段：{sid} {f}")
                rec[f] = v
            rec["gt_confidence"] = "ADJUDICATED"
            rec["gt_adjudication"] = adjud_basis[sid]
        elif sid in r3:
            diff = compare_fields(rec, r3[sid])
            rec["gt_confidence"] = "AGREED" if not diff else "DISPUTED"
            if diff:
                rec["gt_disputed_fields"] = diff
                disputes.append({"site_id": sid, "fields": diff})
        else:
            rec["gt_confidence"] = "SINGLE"
        rec["source_pass"] = a.source_pass
        rec["protocol_version"] = a.protocol_version
        fixes = sorted({seg.split(":")[1] for seg in str(rec.get("notes") or "").split("；")
                        if seg.startswith("RULE_FIX:") and len(seg.split(":")) > 1})
        rec["rule_fixes"] = fixes
        counts[rec["gt_confidence"]] += 1
    gate.check("每条记录都有 gt_confidence",
               all("gt_confidence" in r for r in records.values()), dict(counts))
    unknown_adjud = sorted(set(adjud) - set(records))
    gate.check("裁决只针对存在的站点", not unknown_adjud, unknown_adjud[:5])

    if gate.failed:
        return None, gate, counts, disputes

    # ---- write --------------------------------------------------------------
    out = pathlib.Path(a.out)
    if out.exists():
        raise SystemExit(f"{out} 已存在；冻结不覆盖已有版本")
    (out / "out").mkdir(parents=True)
    written = {}
    for p in files:
        rows, _ = AV.read_output(p)
        target = out / "out" / p.name
        with io.open(target, "w", encoding="utf-8") as f:
            for _, rec in rows:
                f.write(json.dumps(records[rec["site_id"]], ensure_ascii=False) + "\n")
        written[p.name] = sha256(target)
    for label, src in (("MANIFEST.json", mpath),
                       ("ANNOTATION_PRINCIPLES.md", a.principles),
                       ("PRINCIPLES_1.12_CANDIDATES.md", a.candidates)):
        if src and pathlib.Path(src).is_file():
            shutil.copy2(src, out / label)
    if a.tools and pathlib.Path(a.tools).is_dir():
        shutil.copytree(a.tools, out / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    report_out = out / "report"
    report_out.mkdir(exist_ok=True)
    for src in (a.rule_fixes, a.agreement, a.agreement_r3, a.recheck, a.validate, a.materials):
        if src and pathlib.Path(src).is_file():
            shutil.copy2(src, report_out / pathlib.Path(src).name)

    materials = {}
    if a.materials and pathlib.Path(a.materials).is_file():
        materials = json.loads(pathlib.Path(a.materials).read_text(encoding="utf-8"))
    unit_bundles = MISSING
    if a.unit_bundle_map and pathlib.Path(a.unit_bundle_map).is_file():
        unit_bundles = json.loads(pathlib.Path(a.unit_bundle_map).read_text(encoding="utf-8"))

    by_unit = collections.Counter()
    for sid in records:
        entry = index.get(sid)
        by_unit[str((entry[0].get("unit_location") if entry else None)
                    or unit_from_file.get(sid) or "(unknown)")] += 1

    freeze = {
        "version": a.version,
        "frozen_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "protocol_version": a.protocol_version,
        "source_pass": a.source_pass,
        "counts": {
            "sites": len(records),
            "files": len(files),
            "units": len(by_unit),
            "gt_confidence": dict(counts),
            "is_api_use": dict(collections.Counter(str(r.get("is_api_use")) for r in records.values())),
            "flows": sum(len(r.get("flows") or ()) for r in records.values()),
            "no_consumer_found": sum(
                1 for r in records.values() for fl in (r.get("flows") or ())
                if "NO_CONSUMER_FOUND" in str(((fl or {}).get("stuck_at") or {}).get("why", ""))),
            "verdicts": dict(collections.Counter(
                (x if isinstance(x, str) else "MIXED")
                for r in records.values()
                for v in (r.get("constraint_verdicts") or {}).values()
                for x in ([v] if isinstance(v, str) else ["MIXED"]))),
            "rule_fixed_records": sum(1 for r in records.values() if r.get("rule_fixes")),
            "sites_per_unit": dict(sorted(by_unit.items())),
        },
        "gates": gate.to_list(),
        "disputed": disputes,
        "process": {
            "protocol_versions": sha_tree(a.protocols, "*.md"),
            "prompts": sha_tree(a.prompts, "*.md"),
            "principles_frozen": sha256(a.principles),
            "materials": materials or MISSING,
            "passes": {
                "GT": {"dir": str(in_dir), "files": {p.name: sha256(p) for p in files},
                       "lines": {p.name: sum(1 for l in io.open(p, encoding="utf-8") if l.strip())
                                 for p in files}},
                "R3": ({"dir": str(a.r3),
                        "files": {p.name: sha256(p) for p in sorted(pathlib.Path(a.r3).glob("*.jsonl"))}}
                       if a.r3 else MISSING),
                "R1": ({"dir": str(a.r1),
                        "files": {p.name: sha256(p) for p in sorted(pathlib.Path(a.r1).glob("*.jsonl"))}}
                       if a.r1 else MISSING),
            },
            "progress": sha256(in_dir / "PROGRESS.jsonl"),
            "reports": {pathlib.Path(x).name: sha256(x) for x in
                        (a.rule_fixes, a.agreement, a.agreement_r3, a.recheck, a.validate)
                        if x},
            "tools": sha_tree(a.tools, "*.py"),
            "batches": {"dir": str(a.batches), "manifest_sha256": sha256(mpath)},
            "frozen_files": written,
            "unit_to_build_product": unit_bundles,
        },
    }
    (out / "FREEZE.json").write_text(json.dumps(freeze, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    return freeze, gate, counts, disputes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="in_dir", required=True, help="要冻结的那一轮（修正后的 out/）")
    ap.add_argument("--batches", required=True)
    ap.add_argument("--out", required=True, help="gt_vN 目录；已存在则拒绝")
    ap.add_argument("--version", default="gt_v1")
    ap.add_argument("--protocol-version", default="1.11")
    ap.add_argument("--source-pass", default="R2")
    ap.add_argument("--principles", default=None)
    ap.add_argument("--candidates", default=None, help="PRINCIPLES_1.12_CANDIDATES.md")
    ap.add_argument("--protocols", default=None, help="放各版原则全文的目录")
    ap.add_argument("--prompts", default=None, help="放各版提示词的目录")
    ap.add_argument("--materials", default=None, help="annot_materials.json")
    ap.add_argument("--tools", default=None, help="工具目录，快照进 gt_vN/tools")
    ap.add_argument("--report", default=None, help="报告目录（只用于默认路径推断）")
    ap.add_argument("--rule-fixes", default=None)
    ap.add_argument("--agreement", default=None)
    ap.add_argument("--agreement-r3", default=None)
    ap.add_argument("--recheck", default=None)
    ap.add_argument("--validate", default=None)
    ap.add_argument("--r1", default=None, help="第一轮 out/，只为把指纹记进过程记录")
    ap.add_argument("--r3", default=None, help="第三遍 out/；给了才算 AGREED/DISPUTED")
    ap.add_argument("--unit-bundle-map", default=None,
                    help="单元 ↔ 构建产物对应表（评估 ① 的连接点）；缺了就在记录里写 MISSING")
    ap.add_argument("--adjudications", default=None,
                    help="人工裁决 jsonl：{site_id, field, value, basis}；不给就一条不裁决")
    ap.add_argument("--json", dest="out_json", default=None)
    a = ap.parse_args(argv)
    if a.report:
        r = pathlib.Path(a.report)
        a.rule_fixes = a.rule_fixes or str(r / "rule_fixes.json")
        a.agreement = a.agreement or str(r / "agreement_R1_R2_post.json")
        a.recheck = a.recheck or str(r / "recheck_r2_all.json")
        a.validate = a.validate or str(r / "validate_r2_1_11.json")
        a.materials = a.materials or str(r / "annot_materials.json")
    freeze, gate, counts, disputes = run(a)
    for row in gate.to_list():
        print(f"  [{'OK ' if row['pass'] else 'FAIL'}] {row['gate']}"
              + (f" —— {row['detail']}" if row["detail"] else ""))
    if freeze is None:
        print(f"\n拒绝冻结：{len(gate.failed)} 个判据未过，什么都没写。")
        return 1
    print(f"\n冻结 {freeze['version']}：{freeze['counts']['sites']} 个站点，"
          f"{freeze['counts']['units']} 个单元，gt_confidence {freeze['counts']['gt_confidence']}"
          + (f"，DISPUTED {len(disputes)} 条" if disputes else ""))
    print(f"→ {a.out}/FREEZE.json")
    if a.out_json:
        pathlib.Path(a.out_json).write_text(json.dumps(freeze, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
