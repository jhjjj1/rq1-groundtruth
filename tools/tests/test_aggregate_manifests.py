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


def variant(style="all", ok=True, **kw):
    v = {"strip_style": style, "binary_bytes": 12345,
         "map_matches_binary": ok, "strip_did_run": style != "none",
         "strip_rc": 0 if style != "none" else None,
         "strip_preserves_layout": True if style != "none" else None}
    v.update(kw)
    return v


def base(**kw):
    m = {"repo": "o/r", "config_id": "base", "scheme_verdict": "APP_SCHEME_FOUND",
         "map_verdict": "MAP_MODE_CHOSEN", "build_outcome": "success",
         "maps_by_output_kind": {"APP_BUNDLE": 1, "PRELINK_OBJECT": 30},
         "strip_variants_requested": ["none", "all"],
         "variants": [variant("none"), variant("all")]}
    m.update(kw)
    return m


def main():
    fails = []
    with tempfile.TemporaryDirectory() as td:
        write(td, "j1", base(repo="a/a"))
        write(td, "j2", base(repo="b/b", build_outcome="failure",
                             maps_by_output_kind={}, variants=[]))
        # 编过了、产出了 30 个 prelink map，但没有 app bundle 的那个
        write(td, "j3", base(repo="c/c",
                             maps_by_output_kind={"PRELINK_OBJECT": 30},
                             variants=[]))
        write(td, "j4", base(repo="d/d",
                             variants=[variant("all", binary_bytes=None)]))
        write(td, "j5", base(repo="e/e", scheme_verdict="NO_APP_SCHEME_OBSERVED"))
        write(td, "j6", base(repo="f/f", map_verdict="NO_USABLE_MAP_MODE"))
        write(td, "j7", base(repo="a/a", config_id="strip_all",
                             scheme_verdict="APP_SCHEME_AMBIGUOUS", scheme="Zeta"))
        # scheme 和 map mode 都拿到了，但构建那步被跳过（mapmode 步骤中途崩了）
        write(td, "j8", base(repo="g/g", build_outcome="skipped",
                             maps_by_output_kind={}, variants=[]))
        # 文件都在，但 map 的节区表和二进制对不上 —— 不能算 OK
        write(td, "j9", base(repo="h/h",
                             variants=[variant("none", ok=False),
                                       variant("all", ok=False)]))
        # 没量到（otool 读不出来）也不能算 OK，但它和「量了且不一致」是两件事
        write(td, "j10", base(repo="i/i",
                              variants=[variant("none", ok=None),
                                        variant("all", ok=None)]))
        out = str(pathlib.Path(td) / "agg.json")
        # 本批本应有 10 个 job，只回收到 8 份 manifest
        A.main(["--artifacts-dir", td, "--expected", "12", "--out", out])
        res = json.loads(open(out).read())

    t = res["totals"]
    got = sum(t.values())
    if got != 12:
        fails.append(f"分桶合计 {got} != jobs_expected 12（丢了 job）")
    if res["manifests_unaccounted"] != 2:
        fails.append(f"unaccounted={res['manifests_unaccounted']}, 期望 2")
    if t["MANIFEST_MISSING"] != 2:
        fails.append(f"MANIFEST_MISSING={t['MANIFEST_MISSING']}, 期望 2")
    for b, want in (("OK", 2), ("BUILD_FAILED", 1), ("BUILT_NO_MAP", 1),
                    ("BUILT_NO_BINARY", 1), ("NO_APP_SCHEME", 1),
                    ("NO_USABLE_MAP_MODE", 1), ("BUILD_NOT_ATTEMPTED", 1),
                    ("MAP_BINARY_MISMATCH", 2)):
        if t[b] != want:
            fails.append(f"{b}={t[b]}, 期望 {want}")
    # app bundle 一级：第一轮的 manifest 没有 bundle 字段，必须全部落在 NOT_COLLECTED
    # ——「那一轮没收」与「收了是空的」在数据里不能长得一样
    bt = res["bundle_totals"]
    if res["bundles_observed"] != sum(bt.values()):
        fails.append(f"bundle 合计 {sum(bt.values())} != bundles_observed {res['bundles_observed']}")
    if bt["NOT_COLLECTED"] != 10:
        fails.append(f"bundle NOT_COLLECTED={bt['NOT_COLLECTED']}, 期望 10（10 份老 manifest）")
    # 第二轮四种形态各自归位
    for name, m, want in (
        ("bok", base(bundle_zip="bundle.ipa", app_bundle="/x/A.app",
                     bundle_verify={"verdict": "OK"}), "OK"),
        ("bnone", base(bundle_zip="bundle.ipa", app_bundle="/x/A.app",
                       bundle_verify={"verdict": "OK_NO_PRIVACY_MANIFEST"}), "OK_NO_PRIVACY_MANIFEST"),
        ("bbad", base(bundle_zip="bundle.ipa", app_bundle="/x/A.app",
                      bundle_verify={"verdict": "EXECUTABLE_DIFFERS_FROM_COLLECTED_BINARY"}), "VERIFY_FAILED"),
        ("bfail", base(bundle_zip=None, app_bundle="/x/A.app",
                       bundle_note="BUNDLE_ZIP_FAILED: disk full"), "PACK_FAILED"),
        ("bnoapp", base(bundle_zip=None, app_bundle=None), "NO_APP_BUNDLE"),
    ):
        got = A.bundle_bucket(m)
        if got != want:
            fails.append(f"bundle_bucket({name})={got}, 期望 {want}")
    # 代码里 ok_rate 是 round(x, 4)，容差不能比它还紧
    if abs(res["ok_rate"] - round(2 / 12, 4)) > 1e-9:
        fails.append(f"ok_rate={res['ok_rate']}, 期望 {round(2/12,4)}（分母是 12 不是 10）")
    # 变体一级：j1(2 OK) + j5..j7(各 2 OK) = 8 个 OK
    #   j4 一个 NO_BINARY；j9/j10 各 2 个 MAP_BINARY_MISMATCH
    #   j2/j3/j8 的 variants 为空，不贡献变体
    vt = res["variant_totals"]
    if res["variants_observed"] != sum(vt.values()):
        fails.append(f"变体合计 {sum(vt.values())} != variants_observed "
                     f"{res['variants_observed']}")
    if vt["NO_BINARY"] != 1:
        fails.append(f"变体 NO_BINARY={vt['NO_BINARY']}, 期望 1")
    if vt["MAP_BINARY_MISMATCH"] != 4:
        fails.append(f"变体 MAP_BINARY_MISMATCH={vt['MAP_BINARY_MISMATCH']}, 期望 4")
    if res["variants_from_legacy_manifests"] != 0:
        fails.append("新格式不该触发老格式合成")

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
    # 老格式 manifest（无 variants 字段）必须能被合成进同一张表，并标记来源
    with tempfile.TemporaryDirectory() as td:
        legacy = {"repo": "old/old", "config_id": "base",
                  "scheme_verdict": "APP_SCHEME_FOUND",
                  "map_verdict": "MAP_MODE_CHOSEN", "build_outcome": "success",
                  "maps_by_output_kind": {"APP_BUNDLE": 1},
                  "binary_bytes": 999, "map_matches_binary": True,
                  "strip_style": "none"}
        write(td, "old", legacy)
        out = str(pathlib.Path(td) / "o.json")
        A.main(["--artifacts-dir", td, "--expected", "1", "--out", out])
        r = json.loads(open(out).read())
    if r["totals"]["OK"] != 1:
        fails.append(f"老格式 job 没判成 OK：{r['totals']}")
    if r["variants_observed"] != 1 or r["variants_from_legacy_manifests"] != 1:
        fails.append(f"老格式合成计数错：{r['variants_observed']} / "
                     f"{r['variants_from_legacy_manifests']}")

    if fails:
        print("FAIL")
        for f_ in fails:
            print("  -", f_)
        return 1
    print("PASS  分桶守恒：10 份 manifest + 2 个没回收 = 12，与 jobs_expected 一致")
    print("      app bundle 一级：老 manifest 全归 NOT_COLLECTED；新格式五种形态各自归位")
    print(f"      变体一级：{res['variants_observed']} 个，合计守恒；"
          f"老格式 manifest 能合成并标记来源")
    print(f"      ok_rate={res['ok_rate']} （分母用应有 job 数，不是回收数）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
