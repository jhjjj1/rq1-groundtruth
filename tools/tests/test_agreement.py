#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Closed-form values for the four coefficients, the three-rater generalisation,
and the item constructions that are easy to get generously wrong (exact sets,
ABSENT verdicts, the both-YES conditional universe).

Set `AGREEMENT_R1` / `AGREEMENT_R2` to the two merged pass files to also re-run
the regression against the measured corpus numbers.
"""
import io
import json
import math
import os
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import agreement as A  # noqa: E402


def close(got, want, tol=1e-9):
    assert abs(got - want) < tol, (got, want)


def rec(sid, **kw):
    r = {"site_id": sid, "site_class": "RRA", "is_api_use": "YES", "operation": "READ",
         "unit_role": "THIRD_PARTY", "declaring_unit": "U", "value_fate": ["LOCAL_ONLY"],
         "escape": None, "applicable_reason": "NONE", "constraint_verdicts": {},
         "alt_equivalence": None, "exceeds_all_reasons": None, "is_api_use_reason": "",
         "notes": "", "fate_evidence": "", "needs_context": None, "flows": [],
         "unit_confirmed": "YES"}
    r.update(kw)
    return r


def write_pass(root, name, recs, fname="b0001__repos__demo-app.out.jsonl"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    with io.open(d / fname, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return d


def main():
    # ------------------------------------------------- closed form, two raters
    rows = [("A", "A")] * 40 + [("A", "B")] * 10 + [("B", "A")] * 5 + [("B", "B")] * 45
    s = A.agreement_stats(rows)
    close(s["Po"], 0.85)
    close(s["Pe_fleiss"], 0.475 ** 2 + 0.525 ** 2)
    close(s["fleiss"], (0.85 - 0.50125) / (1 - 0.50125))
    close(s["cohen"], 0.7)
    close(s["PABAK"], 0.7)
    close(s["AC1"], (0.85 - 0.49875) / (1 - 0.49875))
    assert s["scott_pi_equivalent"] is True and s["raters"] == 2 and s["k"] == 2
    assert s["marginals"]["A"]["pooled"] == 95 and s["marginals"]["A"]["rater1"] == 45

    # ------------------------------------------------ three raters generalise
    s = A.agreement_stats([("X", "X", "X")] * 10 + [("Y", "Y", "Y")] * 10)
    close(s["Po"], 1.0)
    close(s["fleiss"], 1.0)
    assert s["scott_pi_equivalent"] is False and s["cohen"] is None
    assert set(s["cohen_pairwise"]) == {"0-1", "0-2", "1-2"}
    # every rater a different category on every item: Po = 0, Pe = 1/3
    s = A.agreement_stats([("X", "Y", "Z")] * 12)
    close(s["Po"], 0.0)
    close(s["fleiss"], -0.5)
    # one dissenter out of three: one agreeing pair of the three
    s = A.agreement_stats([("X", "X", "Y")] * 9)
    close(s["Po"], 1 / 3)
    # a single category leaves kappa undefined (0/0) and the module says nan rather
    # than 1.0 -- "everyone said the only thing there was to say" is not agreement
    assert math.isnan(A.agreement_stats([("X", "X")] * 5)["fleiss"])

    # ------------------------------------------------------------- categorisers
    assert A.cat_value_fate({"value_fate": ["B", "A"]}) == A.cat_value_fate({"value_fate": ["A", "B"]})
    assert A.cat_value_fate({"value_fate": ["A"]}) != A.cat_value_fate({"value_fate": ["A", "B"]})
    assert A.cat_value_fate({"value_fate": []}) == "{}"
    assert A.cat_escape({"escape": None}) == "NO_ESCAPE"
    assert A.cat_escape({"escape": {"kind": "RETURNED", "value": "RAW"}}) == "RETURNED|RAW"
    assert A.cat_scalar("exceeds_all_reasons")({"exceeds_all_reasons": None}) == "NONE"
    assert A.cat_verdict({"APP_GROUP": "SUPPORTED"}) == "MIXED"

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="agr_"))
    try:
        # ---------------------------------------------------- universes & verdicts
        r1 = [rec("s1"), rec("s2", is_api_use="NO", operation="NA", value_fate=[]),
              rec("s3", constraint_verdicts={"RCA92_C1": "SUPPORTED", "RCA92_C2": "SUPPORTED"}),
              rec("s4", site_class="ALT", alt_equivalence="NEAR_EQUIVALENT",
                  exceeds_all_reasons="YES")]
        r2 = [rec("s1", operation="WRITE"),
              rec("s2", is_api_use="NO", operation="NA", value_fate=[]),
              rec("s3", constraint_verdicts={"RCA92_C1": "CONFLICT"}),
              rec("s4", site_class="ALT", alt_equivalence="NEAR_EQUIVALENT",
                  exceeds_all_reasons="NO")]
        d1 = write_pass(tmp, "p1", r1)
        d2 = write_pass(tmp, "p2", r2)
        out, names = A.run([f"R1={d1}", f"R2={d2}"], None, None, [], 50, 1, str(tmp / "diff"))
        assert names == ["R1", "R2"]
        f = out["fields"]
        assert f["is_api_use"]["n"] == 4
        # the site both passes call NO carries no reason / fate / escape layer
        assert f["applicable_reason"]["n"] == 3 and f["value_fate"]["n"] == 3
        # only the site both call YES *and* every pass calls YES enters the conditional
        assert f["operation|bothYES"]["n"] == 3, f["operation|bothYES"]
        assert f["alt_equivalence"]["n"] == 1 and f["exceeds_all_reasons"]["n"] == 1
        # verdict items: RCA92_C1 rated by both; RCA92_C2 only by R1
        assert f["constraint_verdicts|all_rated"]["n"] == 1
        assert f["constraint_verdicts|union_ABSENT"]["n"] == 2
        conf = {tuple(c["marks"]): c["n"] for c in f["constraint_verdicts|union_ABSENT"]["confusion_top"]}
        assert conf[("SUPPORTED", "ABSENT")] == 1 and conf[("SUPPORTED", "CONFLICT")] == 1, conf
        # the unit came from the file name when no batches were given
        assert out["run"]["unit_source"] == {"pass_filename": 4}, out["run"]["unit_source"]
        # diffs are written per field, with the site and both values
        diff = [json.loads(l) for l in (tmp / "diff" / "operation.jsonl").read_text(encoding="utf-8").splitlines()]
        assert diff == [{"site_id": "s1", "unit": "repos/demo-app", "R1": "READ", "R2": "WRITE"}], diff

        # -------------------------------------------- excluded constraints
        out2, _ = A.run([f"R1={d1}", f"R2={d2}"], None, None, ["RCA92_C1"], 50, 1, None)
        assert out2["fields"]["constraint_verdicts|all_rated"] is None
        assert out2["fields"]["constraint_verdicts|union_ABSENT"]["n"] == 1

        # -------------------------------------------- restrict & non-overlap
        (tmp / "keep.txt").write_text("s1\ns3\nzz\n", encoding="utf-8")
        out3, _ = A.run([f"R1={d1}", f"R2={d2}"], None, str(tmp / "keep.txt"), [], 50, 1, None)
        assert out3["fields"]["is_api_use"]["n"] == 2
        assert out3["run"]["restrict_ids_not_in_all_passes"] == 1
        d3 = write_pass(tmp, "p3", r1 + [rec("extra")])
        out4, _ = A.run([f"R1={d3}", f"R2={d2}"], None, None, [], 50, 1, None)
        assert out4["run"]["sites_only_in_one_pass"] == {"R1": 1, "R2": 0}
        assert out4["fields"]["is_api_use"]["n"] == 4

        # -------------------------------------------------- three passes end to end
        r3 = [rec("s1", operation="OBSERVE"), rec("s2", is_api_use="NO", operation="NA", value_fate=[]),
              rec("s3", constraint_verdicts={"RCA92_C1": "UNKNOWN"}),
              rec("s4", site_class="ALT", alt_equivalence="CONDITIONAL", exceeds_all_reasons="NO")]
        d4 = write_pass(tmp, "p4", r3)
        out5, names5 = A.run([f"R1={d1}", f"R2={d2}", f"R3={d4}"], None, None, [], 50, 1, None)
        assert names5 == ["R1", "R2", "R3"]
        assert out5["fields"]["operation"]["raters"] == 3
        assert out5["fields"]["operation"]["cohen"] is None
        assert len(out5["fields"]["operation"]["cohen_pairwise"]) == 3
        md = A.to_markdown(out5, names5)
        assert "m=3" in md and "| R1 | R2 | R3 | n |" in md

        # ------------------------------------------------------- reproducibility
        # …over two units, because the interval resamples units and a single-unit
        # corpus honestly has none to resample
        e1 = write_pass(tmp, "p5", r1)
        write_pass(tmp, "p5", [rec("t1"), rec("t2", operation="WRITE")],
                   fname="b0002__deps__demo-lib@1.out.jsonl")
        e2 = write_pass(tmp, "p6", r2)
        write_pass(tmp, "p6", [rec("t1"), rec("t2")],
                   fname="b0002__deps__demo-lib@1.out.jsonl")
        a1, _ = A.run([f"R1={e1}", f"R2={e2}"], None, None, [], 200, 7, None)
        a2, _ = A.run([f"R1={e1}", f"R2={e2}"], None, None, [], 200, 7, None)
        ci = a1["fields"]["operation"]["fleiss_ci95"]
        assert ci == a2["fields"]["operation"]["fleiss_ci95"] and not math.isnan(ci[0]), ci
        assert a1["run"]["units"] == 2
        assert set(a1["strata"]["unit_kind"]) == {"repos", "deps"}
        # a single unit gives no interval, and says so with nan rather than a fake one
        assert math.isnan(out["fields"]["operation"]["fleiss_ci95"][0])

        # ------------------------------------------------------------ CLI
        rc = A.main(["--pass", f"R1={d1}", "--pass", f"R2={d2}", "--boot", "20",
                     "--json", str(tmp / "o.json"), "--md", str(tmp / "o.md")])
        assert rc == 0 and json.loads((tmp / "o.json").read_text(encoding="utf-8"))["fields"]
        assert "Scott" in A.__doc__ or "Scott's pi" in A.__doc__
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ----------------------------------- optional: the measured corpus numbers
    p1, p2 = os.environ.get("AGREEMENT_R1"), os.environ.get("AGREEMENT_R2")
    if p1 and p2 and pathlib.Path(p1).exists() and pathlib.Path(p2).exists():
        out, _ = A.run([f"R1={p1}", f"R2={p2}"], None, None, [], 20, 20260919, None)
        want = {"is_api_use": 0.589, "operation": 0.882, "applicable_reason": 0.874,
                "value_fate": 0.431, "constraint_verdicts|all_rated": 0.748,
                "constraint_verdicts|union_ABSENT": 0.601, "exceeds_all_reasons": -0.678}
        for k, v in want.items():
            got = out["fields"][k]["fleiss"]
            assert abs(got - v) < 5e-4, (k, got, v)
        assert out["fields"]["is_api_use"]["n"] == 4538
        print("      （并复现了语料实测：is_api_use 0.589 / operation 0.882 / 约束裁决 0.748）")
    else:
        print("      （跳过语料回归：未设 AGREEMENT_R1 / AGREEMENT_R2）")

    print("PASS  四个系数的闭式值 + 三评分者通式（含两两 Cohen）+ 精确集合/ABSENT/条件全集的条目构造 + "
          "排除约束与 --restrict + 单元来源 + 逐字段不一致明细 + 同 seed 可复现 + Markdown")


if __name__ == "__main__":
    main()
