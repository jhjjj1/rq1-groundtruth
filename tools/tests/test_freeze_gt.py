#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The freeze either happens completely or not at all, and what it writes says
where every byte came from.  Also: a disagreement is recorded, never resolved,
unless a human ruling was supplied."""
import io
import json
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import freeze_gt as FZ  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_annotate_validate_rules import batch, rec, site, write  # noqa: E402


class Args:
    def __init__(self, **kw):
        d = dict(in_dir=None, batches=None, out=None, version="gt_v1", protocol_version="1.11",
                 source_pass="R2", principles=None, candidates=None, protocols=None, prompts=None,
                 materials=None, tools=None, report=None, rule_fixes=None, agreement=None,
                 agreement_r3=None, recheck=None, validate=None, r1=None, r3=None,
                 adjudications=None, unit_bundle_map=None, out_json=None)
        d.update(kw)
        self.__dict__.update(d)


def good_record(sid="std"):
    return rec(sid, constraint_verdicts={
        "R1C8F_C1": "CONFLICT", "R1C8F_C2": "SUPPORTED", "R1C8F_C3": "SUPPORTED",
        "R1C8F_C4": "SUPPORTED", "R1C8F_C5": "UNKNOWN"})


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="fz_"))
    try:
        bdir = tmp / "batches"
        bdir.mkdir()
        bp = batch(bdir, "b0001__repos__demo-app", [site("s1"), site("s2")])
        (bdir / "MANIFEST.json").write_text(json.dumps({
            "batch_size": 40,
            "batches": [{"file": bp.name, "unit_location": "repos/demo-app", "n": 2,
                         "site_ids": ["s1", "s2"]}],
            "counts": {}}), encoding="utf-8")
        in_dir = tmp / "in"
        in_dir.mkdir()
        write(in_dir, "b0001__repos__demo-app.out.jsonl", [good_record("s1"), good_record("s2")])
        (in_dir / "PROGRESS.jsonl").write_text('{"batch":"b0001","n":2}\n', encoding="utf-8")

        rep = tmp / "report"
        rep.mkdir()
        (rep / "rule_fixes.json").write_text(json.dumps({"run": {
            "input_sha256": {"b0001__repos__demo-app.out.jsonl": "x" * 64}},
            "fixes": {}}), encoding="utf-8")
        (rep / "agreement_R1_R2_post.json").write_text("{}", encoding="utf-8")
        (rep / "recheck_r2_all.json").write_text(json.dumps({"citation_problems": []}),
                                                 encoding="utf-8")
        (rep / "validate_r2_1_11.json").write_text("{}", encoding="utf-8")
        (rep / "annot_materials.json").write_text(json.dumps({"all_batches.zip": "abc"}),
                                                  encoding="utf-8")
        principles = tmp / "ANNOTATION_PRINCIPLES.md"
        principles.write_text("版本 1.11\n", encoding="utf-8")
        cands = tmp / "PRINCIPLES_1.12_CANDIDATES.md"
        cands.write_text("", encoding="utf-8")
        protos = tmp / "protocols"
        protos.mkdir()
        (protos / "v1.9.md").write_text("九\n", encoding="utf-8")
        (protos / "v1.11.md").write_text("十一\n", encoding="utf-8")

        base = dict(in_dir=str(in_dir), batches=str(bdir), report=str(rep),
                    rule_fixes=str(rep / "rule_fixes.json"),
                    agreement=str(rep / "agreement_R1_R2_post.json"),
                    recheck=str(rep / "recheck_r2_all.json"),
                    validate=str(rep / "validate_r2_1_11.json"),
                    materials=str(rep / "annot_materials.json"),
                    principles=str(principles), candidates=str(cands),
                    protocols=str(protos), tools=str(pathlib.Path(__file__).resolve().parents[1]))

        # -------------------------------------------------- a gate fails: nothing written
        a = Args(out=str(tmp / "gt_fail"), **{**base, "candidates": str(tmp / "nope.md")})
        freeze, gate, _, _ = FZ.run(a)
        assert freeze is None and not (tmp / "gt_fail").exists()
        assert any("1.12" in r["gate"] for r in gate.failed), gate.failed
        # a record that breaks the rule table also stops the freeze
        bad_dir = tmp / "bad"
        bad_dir.mkdir()
        write(bad_dir, "b0001__repos__demo-app.out.jsonl", [
            good_record("s1"),
            rec("s2", constraint_verdicts={"R1C8F_C1": "CONFLICT", "R1C8F_C2": "UNKNOWN",
                                           "R1C8F_C3": "SUPPORTED", "R1C8F_C4": "SUPPORTED",
                                           "R1C8F_C5": "UNKNOWN"})])
        a = Args(out=str(tmp / "gt_bad"), **{**base, "in_dir": str(bad_dir)})
        freeze, gate, _, _ = FZ.run(a)
        assert freeze is None and not (tmp / "gt_bad").exists()
        assert any("校验器" in r["gate"] for r in gate.failed), gate.failed

        # ------------------------------------------------------------ SINGLE only
        a = Args(out=str(tmp / "gt1"), **base)
        freeze, gate, counts, disputes = FZ.run(a)
        assert freeze is not None, [r for r in gate.failed]
        assert counts == {"SINGLE": 2}, counts
        got = [json.loads(l) for l in
               (tmp / "gt1" / "out" / "b0001__repos__demo-app.out.jsonl").read_text(
                   encoding="utf-8").splitlines()]
        assert all(g["gt_confidence"] == "SINGLE" for g in got)
        assert all(g["protocol_version"] == "1.11" and g["source_pass"] == "R2" for g in got)
        assert all(g["rule_fixes"] == [] for g in got)
        pr = freeze["process"]
        assert set(pr["protocol_versions"]) == {"v1.9.md", "v1.11.md"}
        assert len(pr["principles_frozen"]) == 64
        assert pr["passes"]["R3"] == FZ.MISSING and pr["passes"]["R1"] == FZ.MISSING
        assert pr["unit_to_build_product"] == FZ.MISSING     # named, not omitted
        assert len(pr["progress"]) == 64
        assert pr["frozen_files"]["b0001__repos__demo-app.out.jsonl"] == \
            FZ.sha256(tmp / "gt1" / "out" / "b0001__repos__demo-app.out.jsonl")
        assert (tmp / "gt1" / "tools" / "freeze_gt.py").is_file()
        assert (tmp / "gt1" / "ANNOTATION_PRINCIPLES.md").is_file()
        assert (tmp / "gt1" / "report" / "rule_fixes.json").is_file()
        assert freeze["counts"]["verdicts"]["CONFLICT"] == 2
        # a frozen version is not overwritten
        try:
            FZ.run(Args(out=str(tmp / "gt1"), **base))
            raise AssertionError("已存在的版本不该被覆盖")
        except SystemExit:
            pass

        # ------------------------------------------------------ AGREED / DISPUTED
        r3 = tmp / "r3"
        r3.mkdir()
        write(r3, "b0001__repos__demo-app.out.jsonl", [
            good_record("s1"),
            rec("s2", operation="READ", value_fate=["LOCAL_ONLY"],
                constraint_verdicts={"R1C8F_C1": "CONFLICT", "R1C8F_C2": "SUPPORTED",
                                     "R1C8F_C3": "SUPPORTED", "R1C8F_C4": "SUPPORTED",
                                     "R1C8F_C5": "UNKNOWN"})])
        (rep / "agreement_R2_R3.json").write_text("{}", encoding="utf-8")
        a = Args(out=str(tmp / "gt2"), r3=str(r3),
                 agreement_r3=str(rep / "agreement_R2_R3.json"), **base)
        freeze, gate, counts, disputes = FZ.run(a)
        assert freeze is not None, gate.failed
        assert counts == {"AGREED": 1, "DISPUTED": 1}, counts
        assert disputes == [{"site_id": "s2", "fields": ["operation", "value_fate"]}], disputes
        got = {json.loads(l)["site_id"]: json.loads(l) for l in
               (tmp / "gt2" / "out" / "b0001__repos__demo-app.out.jsonl").read_text(
                   encoding="utf-8").splitlines()}
        # the disagreement is recorded on the record and the value is NOT changed
        assert got["s2"]["gt_disputed_fields"] == ["operation", "value_fate"]
        assert got["s2"]["operation"] == "WRITE", "分歧不裁决：本轮的值原样保留"

        # --------------------------------------------------------- ADJUDICATED
        adj = tmp / "adj.jsonl"
        adj.write_text(json.dumps({"site_id": "s2", "field": "operation", "value": "READ",
                                   "basis": "L12 是读"}, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        a = Args(out=str(tmp / "gt3"), r3=str(r3), adjudications=str(adj),
                 agreement_r3=str(rep / "agreement_R2_R3.json"), **base)
        freeze, gate, counts, _ = FZ.run(a)
        assert freeze is not None, gate.failed
        assert counts == {"AGREED": 1, "ADJUDICATED": 1}, counts
        got = {json.loads(l)["site_id"]: json.loads(l) for l in
               (tmp / "gt3" / "out" / "b0001__repos__demo-app.out.jsonl").read_text(
                   encoding="utf-8").splitlines()}
        assert got["s2"]["operation"] == "READ"
        assert got["s2"]["gt_adjudication"] == [{"field": "operation", "basis": "L12 是读"}]
        # a ruling for a site that is not there is a mistake, not a no-op
        adj.write_text(json.dumps({"site_id": "zz", "field": "operation", "value": "READ"}) + "\n",
                       encoding="utf-8")
        freeze, gate, _, _ = FZ.run(Args(out=str(tmp / "gt4"), adjudications=str(adj), **base))
        assert freeze is None and any("裁决" in r["gate"] for r in gate.failed)

        # ------------------------------------------------------------ RULE_FIX 溯源
        fixed = tmp / "fixed"
        fixed.mkdir()
        r = good_record("s1")
        r["notes"] += "；RULE_FIX:FIX_C2_STANDARD: R1C8F_C2 UNKNOWN→SUPPORTED, domain=STANDARD/domain_hint"
        write(fixed, "b0001__repos__demo-app.out.jsonl", [r, good_record("s2")])
        freeze, gate, _, _ = FZ.run(Args(out=str(tmp / "gt5"), **{**base, "in_dir": str(fixed)}))
        assert freeze is not None, gate.failed
        assert freeze["counts"]["rule_fixed_records"] == 1
        got = [json.loads(l) for l in (tmp / "gt5" / "out" / "b0001__repos__demo-app.out.jsonl")
               .read_text(encoding="utf-8").splitlines()]
        assert got[0]["rule_fixes"] == ["FIX_C2_STANDARD"], got[0]["rule_fixes"]

        print("PASS  判据不过就什么都不写（缺 1.12 候选 / 规则表不符各一例）+ SINGLE/AGREED/DISPUTED/"
              "ADJUDICATED 四档（分歧只记不改）+ 裁决到不存在的站点要报错 + 不覆盖已有版本 + "
              "FREEZE.json 的过程记录（各版原则、提示词、材料、各轮每文件 sha、工具快照、"
              "缺的东西写 MISSING）+ RULE_FIX 溯源")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
