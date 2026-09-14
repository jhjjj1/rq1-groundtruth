#!/usr/bin/env python3
"""选样守恒：候选 = 目标 + 落选，且每个落选都带理由。

这个测试是为一次实跑事故写的。181 个候选跑出 180 个目标：四档配额各做一次
`int()` 向下取整，45+81+27+27=180，比 181 少 1，补位时按 sum(shortfall) 补，
正好剩下一个 SPM_ONLY 没人要。丢一个样本不算大事，**丢了却没人知道是谁**
才是问题 —— 选样过程必须可回查，不能只剩一个总数。

分布取自实测：STATIC_LIKELY 7 / SPM_ONLY 141 / BOTH 14 / DYNAMIC_LIKELY 19。
"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import make_targets as T

DIST = (("STATIC_LIKELY", 7), ("SPM_ONLY", 141), ("BOTH", 14),
        ("DYNAMIC_LIKELY", 19))


def candidates(dist=DIST):
    rows = []
    for link, n in dist:
        for i in range(n):
            rows.append({"state": "CANDIDATE", "owner": f"o-{link}",
                         "repo": f"r{i}", "linkage": link, "stars": 1000 - i,
                         "default_branch": "main"})
    return rows


def run(total, sha_fn=lambda o, r, b, t: "deadbeef", dist=DIST):
    rows = candidates(dist)
    T.head_sha = sha_fn
    with tempfile.TemporaryDirectory() as td:
        c = pathlib.Path(td) / "c.jsonl"
        c.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        out = pathlib.Path(td) / "targets"
        sys.argv = ["make_targets.py", "--candidates", str(c),
                    "--total", str(total), "--per-batch", "0",
                    "--out-dir", str(out)]
        rc = T.main()
        ex = json.loads((out / "_excluded.json").read_text(encoding="utf-8"))
        batches = sorted(p.name for p in out.glob("batch*.json"))
        n_in_batches = sum(len(json.loads(p.read_text())["targets"])
                           for p in out.glob("batch*.json"))
    return rc, ex, batches, n_in_batches, len(rows)


def main():
    fails = []

    # 1) 全取：181 个候选必须产出 181 个目标，一个都不许蒸发
    rc, ex, batches, n_b, n_c = run(181)
    if rc != 0:
        fails.append(f"rc={rc}")
    if ex["targets"] != 181:
        fails.append(f"目标 {ex['targets']}，期望 181（向下取整不许吃掉样本）")
    if ex["targets"] + len(ex["excluded"]) != n_c:
        fails.append(f"守恒失败：{ex['targets']} + {len(ex['excluded'])} != {n_c}")
    if n_b != ex["targets"]:
        fails.append(f"batch 里合计 {n_b}，与目标数 {ex['targets']} 不符")
    if len(batches) != 3:
        fails.append(f"batch 数 {len(batches)}，期望 3（181 / 每批 64）")

    # 2) 只取一部分：落选的必须逐个留名字和理由
    rc, ex, _b, n_b, n_c = run(50)
    if ex["targets"] != 50:
        fails.append(f"--total 50 得到 {ex['targets']} 个目标")
    if ex["targets"] + len(ex["excluded"]) != n_c:
        fails.append("部分取样时守恒失败")
    reasons = {e["reason"] for e in ex["excluded"]}
    if reasons != {"NOT_SELECTED__OVER_TARGET_N"}:
        fails.append(f"落选理由 {reasons}")
    if any(not e.get("repo") for e in ex["excluded"]):
        fails.append("有落选条目没有 repo 名")

    # 3) 取不到 sha 的：记 SHA_UNAVAILABLE，守恒仍要成立
    dropped = {"r0", "r1", "r2"}
    rc, ex, _b, n_b, n_c = run(181,
                               sha_fn=lambda o, r, b, t: "" if r in dropped else "abc")
    n_sha = sum(1 for e in ex["excluded"] if e["reason"] == "SHA_UNAVAILABLE")
    if n_sha != len(DIST) * len(dropped):
        fails.append(f"SHA_UNAVAILABLE 记了 {n_sha}，期望 {len(DIST)*len(dropped)}")
    if ex["targets"] + len(ex["excluded"]) != n_c:
        fails.append("sha 取不到时守恒失败")

    # 4) 要的比有的多：分母以候选数为准，不能凭空造目标
    rc, ex, _b, _n, n_c = run(999)
    if ex["targets"] != n_c:
        fails.append(f"--total 999 得到 {ex['targets']} 个目标，候选只有 {n_c}")

    if fails:
        print("FAIL")
        for f in fails:
            print("  -", f)
        return 1
    print("PASS  4 个场景：全取 / 部分取 / sha 取不到 / 要的超过有的")
    print("      181 个候选 → 181 个目标 / 3 批（64+64+53），守恒成立")
    return 0


if __name__ == "__main__":
    sys.exit(main())
