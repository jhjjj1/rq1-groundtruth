#!/usr/bin/env python3
"""守恒检查：分桶必须刚好加回总数，一个 job 都不能丢，也不能重复计。

这个项目一贯的口径是「未观测到 ≠ 不存在」。聚合这一步最容易犯的错，是把
没产出 manifest 的 job 从分母里悄悄拿掉，失败率就被做低了。所以这里专门
测一个 expected > observed 的场景。
"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import aggregate_manifests as A


def write(root, name, m):
    d = pathlib.Path(root) / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")


def base(**kw):
    m = {"repo": "o/r", "config_id": "base", "scheme_verdict": "APP_SCHEME_FOUND",
         "map_verdict": "MAP_MODE_CHOSEN", "build_outcome": "success",
         "maps_with_requested_basename": 3, "binary_bytes": 12345}
    m.update(kw)
    return m


def main():
    fails = []
    with tempfile.TemporaryDirectory() as td:
        write(td, "j1", base(repo="a/a"))
        write(td, "j2", base(repo="b/b", build_outcome="failure",
                             maps_with_requested_basename=0, binary_bytes=None))
        write(td, "j3", base(repo="c/c", maps_with_requested_basename=0,
                             binary_bytes=None))
        write(td, "j4", base(repo="d/d", binary_bytes=None))
        write(td, "j5", base(repo="e/e", scheme_verdict="NO_APP_SCHEME_OBSERVED"))
        write(td, "j6", base(repo="f/f", map_verdict="NO_USABLE_MAP_MODE"))
        write(td, "j7", base(repo="a/a", config_id="strip_all",
                             scheme_verdict="APP_SCHEME_AMBIGUOUS", scheme="Zeta"))
        # scheme 和 map mode 都拿到了，但构建那步被跳过（mapmode 步骤中途崩了）
        write(td, "j8", base(repo="g/g", build_outcome="skipped",
                             maps_with_requested_basename=0, binary_bytes=None))
        out = str(pathlib.Path(td) / "agg.json")
        # 本批本应有 10 个 job，只回收到 8 份 manifest
        A.main(["--artifacts-dir", td, "--expected", "10", "--out", out])
        res = json.loads(open(out).read())

    t = res["totals"]
    got = sum(t.values())
    if got != 10:
        fails.append(f"分桶合计 {got} != jobs_expected 10（丢了 job）")
    if res["manifests_unaccounted"] != 2:
        fails.append(f"unaccounted={res['manifests_unaccounted']}, 期望 2")
    if t["MANIFEST_MISSING"] != 2:
        fails.append(f"MANIFEST_MISSING={t['MANIFEST_MISSING']}, 期望 2")
    for b, want in (("OK", 2), ("BUILD_FAILED", 1), ("BUILT_NO_MAP", 1),
                    ("BUILT_NO_BINARY", 1), ("NO_APP_SCHEME", 1),
                    ("NO_USABLE_MAP_MODE", 1), ("BUILD_NOT_ATTEMPTED", 1)):
        if t[b] != want:
            fails.append(f"{b}={t[b]}, 期望 {want}")
    # 代码里 ok_rate 是 round(x, 4)，容差不能比它还紧
    if abs(res["ok_rate"] - round(2 / 10, 4)) > 1e-9:
        fails.append(f"ok_rate={res['ok_rate']}, 期望 {round(2/10,4)}（分母是 10 不是 8）")
    if len(res["ambiguous_app_scheme"]) != 1:
        fails.append(f"ambiguous 记了 {len(res['ambiguous_app_scheme'])} 条, 期望 1")
    # a/a 两个配置，base 成功 strip_all 也成功 -> 仓库口径算 1 个成功
    if res["repos_with_at_least_one_ok"] != 1:
        fails.append(f"repos_with_at_least_one_ok={res['repos_with_at_least_one_ok']}, 期望 1")

    if fails:
        print("FAIL")
        for f in fails:
            print("  -", f)
        return 1
    print("PASS  分桶守恒：8 份 manifest + 2 个没回收 = 10，与 jobs_expected 一致")
    print(f"      ok_rate={res['ok_rate']} （分母用应有 job 数，不是回收数）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
