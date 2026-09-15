#!/usr/bin/env python3
"""Aggregate every build job's manifest into the numbers the paper has to state.

"We took N open-source iOS apps" is not a claim a reviewer will accept without
the next sentence: how many of them actually built.  That rate is a property of
the corpus, not an implementation detail, and it belongs in the paper's external
validity paragraph.  So the matrix has to *count its own failures*, by reason,
rather than let them disappear behind a green check.

The reason taxonomy below is ordered and exclusive: a job is charged to the
earliest stage that stopped it.  A job that produced no manifest at all is its
own bucket -- that is "not observed", which is not the same as "built and
produced nothing", and the two must never be added together.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

#: Ordered: first matching rule wins.  Each entry is (bucket, predicate).
#: Every job lands in exactly one bucket, and the buckets sum to the total.
def bucket_of(m):
    if m is None:
        return "MANIFEST_MISSING"
    # 上一批把「探测失败」「没有共享 scheme」「平台不符」「工程都没找到」
    # 全压进了 NO_APP_SCHEME 或 BUILD_NOT_ATTEMPTED。它们的成因完全不同，
    # 能不能修也完全不同，合在一起就看不出该改哪里。
    sv = m.get("scheme_verdict")
    if not sv:
        # scheme 那一步永远会写 verdict；写不出来说明它没跑 ——
        # 唯一的可能是更前面的容器探测失败了。
        return "NO_XCODE_CONTAINER"
    bucket = SCHEME_VERDICT_BUCKET.get(sv)
    if bucket:
        return bucket
    if m.get("map_verdict") == "NO_USABLE_MAP_MODE":
        return "NO_USABLE_MAP_MODE"
    # `skipped` 是 GitHub 对「前置步骤没给出 scheme/map mode，这一步压根没跑」
    # 的记法。它和「跑了但失败」是两件事，合并会把没试过的算成试过且失败。
    if (m.get("build_outcome") or "").lower() == "skipped":
        return "BUILD_NOT_ATTEMPTED"
    if (m.get("build_outcome") or "").lower() != "success":
        return "BUILD_FAILED"
    # 判据是「有没有 app bundle 的 map」，不是「有没有叫那个名字的文件」：
    # 一次构建会产出几十个 map，其中只有 # Path: 指向 .app/ 的那个是真值。
    # 老 manifest 没有这个字段时退回文件名口径，并且这是退化不是等价。
    kinds = m.get("maps_by_output_kind")
    app_maps = (kinds or {}).get("APP_BUNDLE") if kinds is not None \
        else m.get("maps_with_requested_basename")
    if not app_maps:
        return "BUILT_NO_MAP"
    vs = variants_of(m)
    if not any(v.get("binary_bytes") for v in vs):
        return "BUILT_NO_BINARY"
    # map 和二进制都在，但没有任何一个变体的节区表对得上号 ——
    # 文件齐全不等于配套。
    if not any(v.get("map_matches_binary") is True for v in vs):
        return "MAP_BINARY_MISMATCH"
    return "OK"


def variants_of(m):
    """一个 manifest 的 strip 变体列表。

    老格式（每 job 一个二进制）没有 `variants` 字段，这里合成一个，使两代
    产物能放进同一张表。合成出来的记录标了 `_synthesized`，因为「老格式补出来
    的」和「新格式真产出的」不该长得一模一样。
    """
    vs = m.get("variants")
    if isinstance(vs, list):
        return vs
    return [{
        "strip_style": m.get("strip_style") or "none",
        "binary_bytes": m.get("binary_bytes"),
        "map_matches_binary": m.get("map_matches_binary"),
        "strip_did_run": m.get("strip_did_run"),
        "strip_rc": m.get("strip_rc"),
        "strip_preserves_layout": m.get("strip_preserves_layout"),
        "_synthesized": True,
    }]


#: 变体一级的分桶。同样有序互斥，合计等于观测到的变体数。
VARIANT_BUCKETS = ("OK", "MAP_BINARY_MISMATCH", "STRIP_FAILED",
                   "NO_BINARY", "STRIP_CHANGED_LAYOUT")


def variant_bucket(v):
    if v.get("strip_did_run") and v.get("strip_rc") not in (0, None):
        return "STRIP_FAILED"
    if not v.get("binary_bytes"):
        return "NO_BINARY"
    # strip 若改了节区布局，链接时的 map 就不再描述这个二进制 —— 单列一档，
    # 因为它证伪的是方案的一条地基，不是某个仓库编不过。
    if v.get("strip_preserves_layout") is False:
        return "STRIP_CHANGED_LAYOUT"
    if v.get("map_matches_binary") is not True:
        return "MAP_BINARY_MISMATCH"
    return "OK"


#: scheme 判定 → 分桶。每一档都对应一个能单独回答「该不该算进分母」的成因。
SCHEME_VERDICT_BUCKET = {
    "NO_APP_SCHEME_OBSERVED": "NO_APP_SCHEME",
    "NO_SHARED_SCHEMES": "NO_SHARED_SCHEMES",
    "PLATFORM_MISMATCH": "PLATFORM_MISMATCH",
    "SCHEMES_JSON_UNREADABLE": "SCHEMES_JSON_UNREADABLE",
    "SCHEME_STEP_CRASHED": "SCHEMES_JSON_UNREADABLE",
}

BUCKETS = ("OK", "MAP_BINARY_MISMATCH", "BUILT_NO_BINARY", "BUILT_NO_MAP",
           "BUILD_FAILED", "BUILD_NOT_ATTEMPTED", "NO_USABLE_MAP_MODE",
           "NO_APP_SCHEME", "NO_SHARED_SCHEMES", "PLATFORM_MISMATCH",
           "SCHEMES_JSON_UNREADABLE", "NO_XCODE_CONTAINER",
           "MANIFEST_MISSING")

#: Explanations carried into the summary table so the failure column is
#: readable by someone who has not read this file.
WHY = {
    "OK": "map 和主二进制都有，可进入评分",
    "MAP_BINARY_MISMATCH": "map 和二进制都在，但节区表对不上号（或读不出来）",
    "BUILT_NO_BINARY": "编过了、有 map，但 .app 里没捞到主可执行文件",
    "BUILT_NO_MAP": "编过了，但没产出 # Path: 指向 .app/ 的那个 map",
    "BUILD_FAILED": "xcodebuild 退出码非 0",
    "BUILD_NOT_ATTEMPTED": "前置步骤没给出 scheme 或 map mode，构建这步被跳过",
    "NO_USABLE_MAP_MODE": "三种 map 路径写法都会撞，工程没法要 map",
    "NO_APP_SCHEME": "探测了全部 scheme，没有一个的产物是 application",
    "NO_SHARED_SCHEMES": "工程里没有共享 scheme（scheme 常在 xcuserdata 里不进版本库）",
    "PLATFORM_MISMATCH": "工程目标平台不是 iOS（tvOS / watchOS / macOS 等）",
    "SCHEMES_JSON_UNREADABLE": "`xcodebuild -list -json` 没给出可解析的输出",
    "NO_XCODE_CONTAINER": "四层之内没有 .xcodeproj / .xcworkspace（Bazel、KMM 等）",
    "MANIFEST_MISSING": "job 连 manifest 都没产出（超时/崩溃）—— 未观测，非零结果",
}


def load(root):
    """Find every manifest.json under `root`, one per job artifact directory."""
    rows = []
    for path in sorted(pathlib.Path(root).rglob("manifest.json")):
        try:
            m = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            rows.append({"_path": str(path), "_error": str(exc), "_manifest": None})
            continue
        rows.append({"_path": str(path), "_manifest": m})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--artifacts-dir", required=True,
                    help="目录，下面是各 job 下载回来的 artifact")
    ap.add_argument("--expected", type=int, default=0,
                    help="本批应有的 job 数；与实际 manifest 数对不上时报出来")
    ap.add_argument("--markdown", default=None,
                    help="额外写一份 markdown 表（通常是 $GITHUB_STEP_SUMMARY）")
    ap.add_argument("--out", "--json", dest="out", required=True)
    args = ap.parse_args(argv)

    rows = load(args.artifacts_dir)
    per_config = collections.defaultdict(collections.Counter)
    per_repo = collections.defaultdict(collections.Counter)
    totals = collections.Counter()
    vtotals = collections.Counter()
    per_variant_style = collections.defaultdict(collections.Counter)
    ambiguous, synthesized = [], 0

    for row in rows:
        m = row.get("_manifest")
        b = bucket_of(m)
        cfg = (m or {}).get("config_id") or "?"
        repo = (m or {}).get("repo") or row["_path"]
        totals[b] += 1
        per_config[cfg][b] += 1
        per_repo[repo][b] += 1
        if m and m.get("scheme_verdict") == "APP_SCHEME_AMBIGUOUS":
            ambiguous.append({"repo": repo, "config": cfg, "scheme": m.get("scheme")})
        if m:
            for v in variants_of(m):
                vb = variant_bucket(v)
                vtotals[vb] += 1
                per_variant_style[f"{cfg}/{v.get('strip_style')}"][vb] += 1
                synthesized += bool(v.get("_synthesized"))

    observed = len(rows)
    n_jobs = args.expected or observed
    # 「本批应有 N 个 job，回收到 M 份 manifest」——差值必须显式记账，
    # 不能靠分母悄悄变小把失败率做低。
    unaccounted = max(0, n_jobs - observed)
    if unaccounted:
        totals["MANIFEST_MISSING"] += unaccounted

    repos_ok = sum(1 for r, c in per_repo.items() if c["OK"])
    variants_observed = sum(vtotals.values())
    result = {
        "jobs_expected": n_jobs,
        "manifests_observed": observed,
        "manifests_unaccounted": unaccounted,
        "totals": {b: totals[b] for b in BUCKETS},
        "ok_rate": round(totals["OK"] / n_jobs, 4) if n_jobs else 0.0,
        "repos_seen": len(per_repo),
        "repos_with_at_least_one_ok": repos_ok,
        "repo_ok_rate": round(repos_ok / len(per_repo), 4) if per_repo else 0.0,
        # 变体一级：评分单元是变体，不是 job。两个数分开报，免得把
        # 「构建了多少次」和「能评多少个对象」混成一个。
        "variants_observed": variants_observed,
        "variant_totals": {b: vtotals[b] for b in VARIANT_BUCKETS},
        "variant_ok_rate": (round(vtotals["OK"] / variants_observed, 4)
                            if variants_observed else 0.0),
        "variants_from_legacy_manifests": synthesized,
        "per_config_variant": {c: dict(v) for c, v in per_variant_style.items()},
        "per_config": {c: dict(v) for c, v in per_config.items()},
        "per_repo": {c: dict(v) for c, v in per_repo.items()},
        "ambiguous_app_scheme": ambiguous,
    }
    pathlib.Path(args.out).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = []
    lines.append("## 构建可复现率")
    lines.append("")
    lines.append(f"本批 job 数 **{n_jobs}**，回收 manifest **{observed}**"
                 + (f"，**{unaccounted} 个 job 没有产出 manifest**" if unaccounted else ""))
    lines.append("")
    lines.append("| 结果 | job 数 | 占比 | 含义 |")
    lines.append("|---|---:|---:|---|")
    for b in BUCKETS:
        n = totals[b]
        if not n and b != "OK":
            continue
        lines.append(f"| `{b}` | {n} | {n / n_jobs:.1%} | {WHY[b]} |")
    lines.append("")
    lines.append(f"**仓库口径**：{len(per_repo)} 个仓库里，"
                 f"至少一个配置可用的有 **{repos_ok}**（{result['repo_ok_rate']:.1%}）")
    lines.append("")
    lines.append("### 可评分变体")
    lines.append("")
    lines.append(f"评分单元是 strip 变体，不是 job：一次链接可产出多份二进制、"
                 f"共用一份 map。本批观测到 **{variants_observed}** 个变体。")
    if synthesized:
        lines.append(f"其中 {synthesized} 个是从老格式 manifest 合成的（老格式每 job "
                     f"只有一个二进制）—— 合成出来的和真产出的不是一回事。")
    lines.append("")
    lines.append("| 变体结果 | 个数 | 占比 |")
    lines.append("|---|---:|---:|")
    for b in VARIANT_BUCKETS:
        n = vtotals[b]
        if not n and b != "OK":
            continue
        lines.append(f"| `{b}` | {n} | "
                     f"{n / variants_observed if variants_observed else 0:.1%} |")
    if per_variant_style:
        lines.append("")
        lines.append("| 配置/变体 | " + " | ".join(f"`{b}`" for b in VARIANT_BUCKETS) + " |")
        lines.append("|---" * (len(VARIANT_BUCKETS) + 1) + "|")
        for k in sorted(per_variant_style):
            c = per_variant_style[k]
            lines.append(f"| `{k}` | " + " | ".join(str(c[b]) for b in VARIANT_BUCKETS) + " |")
    if ambiguous:
        lines.append("")
        lines.append(f"**{len(ambiguous)} 个 job 的 app scheme 有歧义**"
                     "（工程里不止一个 application target），选了哪个记在各自 manifest 里：")
        for a in ambiguous[:10]:
            lines.append(f"- `{a['repo']}` / `{a['config']}` -> `{a['scheme']}`")
    if per_config:
        lines.append("")
        if unaccounted:
            lines.append(f"> 下表按配置分，只统计回收到 manifest 的 {observed} 个 job。"
                         f"另外 {unaccounted} 个没有产出 manifest，归不到具体配置，"
                         f"只计入上面的总表 —— 两张表的合计本就不相等。")
            lines.append("")
        lines.append("| 配置 | " + " | ".join(f"`{b}`" for b in BUCKETS) + " |")
        lines.append("|---" * (len(BUCKETS) + 1) + "|")
        for cfg in sorted(per_config):
            c = per_config[cfg]
            lines.append(f"| `{cfg}` | " + " | ".join(str(c[b]) for b in BUCKETS) + " |")

    text = "\n".join(lines)
    print(text)
    if args.markdown:
        with open(args.markdown, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
