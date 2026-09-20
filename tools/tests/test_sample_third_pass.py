#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The sampling frame (A) and the census (B), and that the subset a third pass
receives is byte-identical to what the earlier passes saw."""
import io
import json
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import annotate_validate as AV  # noqa: E402
import make_packs  # noqa: E402,F401  (import-only: the MANIFEST must stay readable by it)
import sample_third_pass as S  # noqa: E402
import subset_batches as SB  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_annotate_validate_rules import C_CA92, batch, rec, site, write  # noqa: E402


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="s3p_"))
    try:
        # ------------------------------------------------------ largest remainder
        seats = S.largest_remainder({"a": 50, "b": 30, "c": 20}, 10)
        assert sum(seats.values()) == 10 and seats == {"a": 5, "b": 3, "c": 2}, seats
        assert sum(S.largest_remainder({"a": 1, "b": 1, "c": 1}, 5).values()) == 5
        assert S.largest_remainder({}, 5) == {}

        # ------------------------------------------------------------- sample A
        by_unit = {"repos/app": [f"a{i}" for i in range(100)],
                   "deps/lib@1": [f"b{i}" for i in range(10)],
                   "pods/x@1": ["c0", "c1"]}
        picked = S.sample_a(by_unit, 30, 2, 7)
        assert sum(len(v) for v in picked.values()) == 30
        assert all(len(picked[u]) >= min(2, len(by_unit[u])) for u in by_unit)
        assert all(set(picked[u]) <= set(by_unit[u]) for u in by_unit)
        assert len(picked["pods/x@1"]) == 2            # a unit cannot give more than it has
        assert picked == S.sample_a(by_unit, 30, 2, 7)          # same seed, same draw
        assert picked != S.sample_a(by_unit, 30, 2, 8)
        # the whole corpus is a valid request
        whole = S.sample_a(by_unit, 112, 2, 7)
        assert sum(len(v) for v in whole.values()) == 112
        try:
            S.sample_a(by_unit, 3, 2, 7)
            raise AssertionError("下限装不下时应当报错而不是悄悄少抽")
        except SystemExit:
            pass

        # ----------------------------------------------------- end to end on batches
        bdir = tmp / "batches"
        bdir.mkdir()
        sites_a = [site(f"a{i}") for i in range(8)]
        batch(bdir, "b0001__repos__demo-app", sites_a)
        sites_b = [site(f"c{i}", code="CA92.1") for i in range(4)]
        batch(bdir, "b0002__deps__demo-lib@1", sites_b, code="CA92.1", constraints=C_CA92,
              unit_location="deps/demo-lib@1")
        (bdir / "MANIFEST.json").write_text(json.dumps({"batch_size": 40, "batches": [], "counts": {}}),
                                            encoding="utf-8")
        pdir = tmp / "p"
        pdir.mkdir()
        recs_a = [rec(f"a{i}") for i in range(8)]
        # one of each thing sample B must take
        recs_a[0]["value_fate"] = ["OFF_DEVICE", "RETURNED"]
        recs_a[1]["needs_context"] = {"what": "x", "why": "y",
                                      "requests": [{"kind": "CALLERS", "symbol": "s"}]}
        recs_a[2]["site_class"] = "ALT"
        recs_a[2]["applicable_reason"] = "NA"
        recs_a[2]["constraint_verdicts"] = {}
        recs_b = [rec(f"c{i}", applicable_reason="CA92.1",
                      constraint_verdicts={"RCA92_C1": "SUPPORTED", "RCA92_C2": "SUPPORTED",
                                           "RCA92_C3": "SUPPORTED"}) for i in range(4)]
        # a domain-row CONFLICT is *not* sample B; a behavioural one is
        recs_b[0]["constraint_verdicts"]["RCA92_C1"] = "CONFLICT"
        recs_b[1]["constraint_verdicts"]["RCA92_C3"] = "CONFLICT"
        write(pdir, "b0001__repos__demo-app.out.jsonl", recs_a)
        write(pdir, "b0002__deps__demo-lib@1.out.jsonl", recs_b)

        payload = S.run(bdir, f"R2={pdir}", 6, 2, 3)
        A_, B_ = payload["sample_A"], payload["sample_B"]
        assert A_["n"] == 6 and set(A_["per_unit"]) == {"repos/demo-app", "deps/demo-lib@1"}
        assert all(v >= 2 for v in A_["per_unit"].values())
        by = {k: v["site_ids"] for k, v in B_["by_reason"].items()}
        assert by["OFF_DEVICE_FATE"] == ["a0"], by
        assert by["NEEDS_CONTEXT"] == ["a1"], by
        assert by["ALT_IN_USE"] == ["a2"], by
        assert by["NON_DOMAIN_CONFLICT"] == ["c1"], by      # c0's CONFLICT is a domain row
        assert payload["run"]["seed"] == 3
        # a site the batches do not know cannot be split into domain / behavioural:
        # it is listed under its own key and never counted as sample B
        (tmp / "pstray").mkdir()
        stray = write(tmp / "pstray", "b0001__repos__demo-app.out.jsonl",
                      recs_a + [rec("ghost", applicable_reason="CA92.1",
                                    constraint_verdicts={"RCA92_C1": "CONFLICT"})])
        payload2 = S.run(bdir, f"R2={stray.parent}", 6, 2, 3)
        assert payload2["sample_B"]["not_classifiable_no_batch"]["site_ids"] == ["ghost"]
        assert "ghost" not in payload2["sample_B"]["site_ids"]

        # ---------------------------------------------------------- subset_batches
        ids = tmp / "ids.txt"
        ids.write_text("\n".join(A_["site_ids"]) + "\nnot-a-site\n", encoding="utf-8")
        out = tmp / "r3"
        r = SB.run(bdir, [str(ids)], out)
        assert r["found"] == 6 and r["missing_count"] == 1, r
        total = 0
        for p in sorted(out.glob("*__*.jsonl")):
            header, kept = AV.read_batch(p)
            total += len(kept)
            assert header["n_sites"] == len(kept) == len(header["site_ids"])
            assert set(header["site_ids"]) == set(kept)
            assert header["_subset_of"] and header["_subset_site_id_files"] == ["ids.txt"]
            # every surviving site is byte-identical to the one the earlier pass saw
            orig_header, orig_sites = AV.read_batch(bdir / p.name)
            for sid, s in kept.items():
                assert s == orig_sites[sid], sid
            # and the header keeps the unit's facts
            for k in ("reasons", "hosts", "build_facts", "unit_location"):
                assert header.get(k) == orig_header.get(k), k
        assert total == 6
        man = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
        assert sum(b["n"] for b in man["batches"]) == 6
        assert all(set(b) >= {"file", "unit_location", "n", "site_ids"} for b in man["batches"])
        assert man["subset"]["missing"] == ["not-a-site"]
        assert man["batch_size"] == 40, "原 MANIFEST 的其余键要留着"

        # a batch that contributes nothing is dropped, not emptied
        one = tmp / "one.txt"
        one.write_text("a0\n", encoding="utf-8")
        out2 = tmp / "r3b"
        r = SB.run(bdir, [str(one)], out2)
        assert r["counts"]["batches_kept"] == 1 and r["counts"]["batches_dropped"] == 1
        assert not (out2 / "b0002__deps__demo-lib@1.jsonl").exists()

        # ------------------------------------------------------------------ CLI
        assert S.main(["--batches", str(bdir), "--pass", f"R2={pdir}", "--n", "6",
                       "--min-per-unit", "2", "--seed", "3", "--out-dir", str(tmp / "o"),
                       "--json", str(tmp / "s.json")]) == 0
        assert (tmp / "o" / "r3_sites_A.txt").read_text(encoding="utf-8").strip().split("\n") == A_["site_ids"]
        rc = SB.main(["--batches", str(bdir), "--site-ids", str(one), "--out", str(tmp / "r3c"),
                      "--json", str(tmp / "sb.json")])
        assert rc == 0, "全部找到时退出码应为 0"

        print("PASS  样本 A（按单元分层、下限、最大余数、同 seed 同抽、下限装不下时报错）+ "
              "样本 B 四类全取（域约束的 CONFLICT 不算）+ 子集批次逐站点逐头字节一致、"
              "空批次丢弃、MANIFEST 可被 make_packs 读")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
