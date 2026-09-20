#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The five outcomes of a mechanical correction, on the two fixes registered today.

The point of the test is that only one of the five writes anything: a fix that
cannot tell must leave the annotation alone and say so by name, because the
corrected pass is the ground truth and an unexplained edit in it is worse than
an uncorrected row.
"""
import io
import json
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import annotate_validate as AV  # noqa: E402
import apply_rule_fixes as F  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_annotate_validate_rules import (  # noqa: E402
    C_1C8F, C_CA92, CTX, CTX_SUITE, SUITE_LINE, UD_LINE, batch, rec, site, write,
)


def counts(payload, fix_id):
    return payload["fixes"][fix_id]["counts"]


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="arf_"))
    try:
        bdir = tmp / "batches"
        bdir.mkdir()
        suite_ctx = {"start_line": 1, "site_line": 12, "function_lines": [11, 13],
                     "truncated": False, "lines": CTX_SUITE}
        # one batch per shape, so a failure names the shape
        batch(bdir, "b0001__repos__demo-app", [site("std")])                      # .standard, 1C8F.1
        batch(bdir, "b0002__repos__demo-app",
              [site("suite", code="CA92.1", domain_hint="SUITE:test.demo.popup.abc",
                    context=suite_ctx)], code="CA92.1", constraints=C_CA92)
        batch(bdir, "b0003__repos__demo-app",
              [site("unres", code="CA92.1", domain_hint="SUITE_CONST:suite unresolved",
                    context=suite_ctx)], code="CA92.1", constraints=C_CA92)
        batch(bdir, "b0004__repos__demo-app",
              [site("mixed", domain_hint="MIXED_DOMAINS:APP_GROUP|APP_PRIVATE",
                    instance_domains=[{"domain": "APP_GROUP"}, {"domain": "APP_PRIVATE"}])])

        suite_rec = dict(applicable_reason="CA92.1", operation="ACQUIRE", value_fate=["LOCAL_ONLY"],
                         is_api_use_reason=f"L12: {SUITE_LINE.strip()}",
                         fate_evidence=f"L12: {SUITE_LINE.strip()}",
                         notes=f"NO_VALUE：ACQUIRE 没有可追的值。L12: {SUITE_LINE.strip()}")
        in_dir = tmp / "in"
        in_dir.mkdir()
        # FIX_C2_STANDARD: the 229-pair shape
        write(in_dir, "b0001.out.jsonl", [rec("std", constraint_verdicts={
            "R1C8F_C1": "CONFLICT", "R1C8F_C2": "UNKNOWN", "R1C8F_C3": "SUPPORTED",
            "R1C8F_C4": "SUPPORTED", "R1C8F_C5": "UNKNOWN"})])
        # FIX_1_11_DOMAIN: a resolved non-group suite, annotated per 1.10
        write(in_dir, "b0002.out.jsonl", [rec("suite", constraint_verdicts={
            "RCA92_C1": "CONFLICT", "RCA92_C2": "SUPPORTED", "RCA92_C3": "SUPPORTED"},
            **suite_rec)])
        # nothing to do: the scanner could not fold the suite
        write(in_dir, "b0003.out.jsonl", [rec("unres", constraint_verdicts={
            "RCA92_C1": "CONFLICT", "RCA92_C2": "SUPPORTED", "RCA92_C3": "SUPPORTED"},
            **suite_rec)])
        # per-domain verdicts
        write(in_dir, "b0004.out.jsonl", [rec("mixed", constraint_verdicts={
            "R1C8F_C1": {"APP_GROUP": "SUPPORTED", "APP_PRIVATE": "CONFLICT"},
            "R1C8F_C2": "SUPPORTED", "R1C8F_C3": "SUPPORTED", "R1C8F_C4": "SUPPORTED",
            "R1C8F_C5": "UNKNOWN"})])

        # ---------------------------------------------------------------- dry run
        payload = F.run(bdir, in_dir, None, None, True)
        assert payload["run"]["dry_run"] is True and payload["run"]["out_dir"] is None
        assert not list(tmp.glob("out*")), "dry-run 不该写任何东西"
        c2 = counts(payload, "FIX_C2_STANDARD")
        assert c2["FLIPPED"] == 1, payload["fixes"]["FIX_C2_STANDARD"]
        d11 = counts(payload, "FIX_1_11_DOMAIN")
        assert d11["FLIPPED"] == 1, payload["fixes"]["FIX_1_11_DOMAIN"]
        #  - the unresolved suite is named, not silently skipped
        assert d11["NOT_APPLICABLE"] >= 1
        assert payload["fixes"]["FIX_1_11_DOMAIN"]["not_applicable_by_domain"]["SUITE_UNRESOLVED"] == 1
        #  - a per-domain verdict carries its own domain in the key, so both tables agree
        assert d11["SAME_UNDER_BOTH"] >= 2, d11
        ch = payload["fixes"]["FIX_1_11_DOMAIN"]["changes"][0]
        assert (ch["site_id"], ch["constraint"], ch["old"], ch["new"]) == \
               ("suite", "RCA92_C1", "CONFLICT", "SUPPORTED"), ch
        assert ch["domain"]["suite"] == "test.demo.popup.abc" and ch["domain"]["in_entitlements"] is False

        # the input is untouched by a dry run
        assert json.loads((in_dir / "b0002.out.jsonl").read_text(encoding="utf-8"))[
            "constraint_verdicts"]["RCA92_C1"] == "CONFLICT"

        # -------------------------------------------------------- ALREADY_NEW / DIVERGENT
        alt_in = tmp / "in2"
        alt_in.mkdir()
        write(alt_in, "b0002.out.jsonl", [rec("suite", constraint_verdicts={
            "RCA92_C1": "SUPPORTED", "RCA92_C2": "SUPPORTED", "RCA92_C3": "SUPPORTED"},
            **suite_rec)])
        p2 = F.run(bdir, alt_in, None, ["FIX_1_11_DOMAIN"], True)
        assert counts(p2, "FIX_1_11_DOMAIN")["ALREADY_NEW"] == 1, counts(p2, "FIX_1_11_DOMAIN")
        assert "FIX_C2_STANDARD" not in p2["fixes"]          # --fix selects
        write(alt_in, "b0002.out.jsonl", [rec("suite", constraint_verdicts={
            "RCA92_C1": "UNKNOWN", "RCA92_C2": "SUPPORTED", "RCA92_C3": "SUPPORTED"},
            **suite_rec)])
        p3 = F.run(bdir, alt_in, None, ["FIX_1_11_DOMAIN"], True)
        assert counts(p3, "FIX_1_11_DOMAIN")["DIVERGENT"] == 1
        div = p3["fixes"]["FIX_1_11_DOMAIN"]["divergent"][0]
        assert div["old"] == "UNKNOWN" and "1.10 期望 CONFLICT" in div["note"], div

        # a per-domain dict on the participants row is malformed; listed, untouched
        mal = tmp / "mal"
        mal.mkdir()
        write(mal, "b0001.out.jsonl", [rec("std", constraint_verdicts={
            "R1C8F_C1": "CONFLICT", "R1C8F_C2": {"APP_PRIVATE": "UNKNOWN"},
            "R1C8F_C3": "SUPPORTED", "R1C8F_C4": "SUPPORTED", "R1C8F_C5": "UNKNOWN"})])
        pm = F.run(bdir, mal, None, ["FIX_C2_STANDARD"], True)
        assert counts(pm, "FIX_C2_STANDARD") == {"FLIPPED": 0, "ALREADY_NEW": 0,
                                                  "SAME_UNDER_BOTH": 0, "DIVERGENT": 0,
                                                  "NOT_APPLICABLE": 1}, counts(pm, "FIX_C2_STANDARD")

        # --------------------------------------------------------------- real run
        out_dir = tmp / "out"
        payload = F.run(bdir, in_dir, out_dir, None, False)
        assert payload["run"]["out_dir"] == str(out_dir)
        fixed = {}
        for p in sorted(out_dir.glob("*.out.jsonl")):
            fixed[p.name] = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert fixed["b0001.out.jsonl"][0]["constraint_verdicts"]["R1C8F_C2"] == "SUPPORTED"
        assert fixed["b0002.out.jsonl"][0]["constraint_verdicts"]["RCA92_C1"] == "SUPPORTED"
        # untouched files still come along, byte for byte in content
        assert fixed["b0003.out.jsonl"][0]["constraint_verdicts"]["RCA92_C1"] == "CONFLICT"
        # the record says what happened to it
        note = fixed["b0001.out.jsonl"][0]["notes"]
        assert "RULE_FIX:FIX_C2_STANDARD: R1C8F_C2 UNKNOWN→SUPPORTED" in note, note
        assert "domain=STANDARD/" in note, note
        # nothing else moved
        before = json.loads((in_dir / "b0001.out.jsonl").read_text(encoding="utf-8"))
        after = fixed["b0001.out.jsonl"][0]
        for k in before:
            if k in ("constraint_verdicts", "notes"):
                continue
            assert before[k] == after[k], k
        assert {k: v for k, v in before["constraint_verdicts"].items() if k != "R1C8F_C2"} == \
               {k: v for k, v in after["constraint_verdicts"].items() if k != "R1C8F_C2"}

        # companion files travel with the pass
        (in_dir / "NOTES.md").write_text("# notes\n", encoding="utf-8")
        (in_dir / "PROGRESS.jsonl").write_text('{"batch":"b0001"}\n', encoding="utf-8")
        out2 = tmp / "out2"
        F.run(bdir, in_dir, out2, None, False)
        assert (out2 / "NOTES.md").exists() and (out2 / "PROGRESS.jsonl").exists()

        # --------------------------------------------------------------- idempotent
        again = F.run(bdir, out_dir, None, None, True)
        assert counts(again, "FIX_C2_STANDARD")["FLIPPED"] == 0, counts(again, "FIX_C2_STANDARD")
        assert counts(again, "FIX_1_11_DOMAIN")["FLIPPED"] == 0, counts(again, "FIX_1_11_DOMAIN")
        assert counts(again, "FIX_C2_STANDARD")["SAME_UNDER_BOTH"] >= 1

        # ------------------------------------- the corrected pass validates clean
        index, dup = AV.build_site_index(bdir)
        assert not dup
        total = 0
        for p in sorted(out_dir.glob("*.out.jsonl")):
            errors = AV.validate(None, p, rule_table_version="1.11", site_index=index)[1]
            total += len([e for e in errors if "§4.5 规则表" in e])
            assert errors == [], (p.name, errors)
        assert total == 0

        # ------------------------------------------------------------------ CLI
        out_json = tmp / "fixes.json"
        rc = F.main(["--batches", str(bdir), "--in", str(in_dir), "--dry-run",
                     "--json", str(out_json)])
        assert rc == 0
        payload = json.loads(out_json.read_text(encoding="utf-8"))
        assert set(payload["fixes"]) == {"FIX_1_11_DOMAIN", "FIX_C2_STANDARD"}
        assert payload["run"]["input_sha256"], "输入文件的指纹要进过程记录"
        assert all(len(v) == 64 for v in payload["run"]["input_sha256"].values())

        print("PASS  两条修正的五种结局（FLIPPED / ALREADY_NEW / SAME_UNDER_BOTH / DIVERGENT / "
              "NOT_APPLICABLE 按域证据分档）+ 只改 verdict 与 notes + RULE_FIX 戳 + --dry-run 不落盘 + "
              "--fix 选择 + 随行文件 + 幂等 + 修正后过 1.11 校验 0 错 + 输入 sha256 入记录")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
