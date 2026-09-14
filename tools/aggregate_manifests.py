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
    if m.get("scheme_verdict") == "NO_APP_SCHEME_OBSERVED":
        return "NO_APP_SCHEME"
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
    if not m.get("binary_bytes"):
        return "BUILT_NO_BINARY"
    return "OK"


BUCKETS = ("OK", "BUILT_NO_BINARY", "BUILT_NO_MAP", "BUILD_FAILED",
           "BUILD_NOT_ATTEMPTED", "NO_USABLE_MAP_MODE", "NO_APP_SCHEME",
           "MANIFEST_MISSING")

#: Explanations carried into the summary table so the failure column is
#: readable by someone who has not read this file.
WHY = {
    "OK": "map 和主二进制都有，可进入评分",
    "BUILT_NO_BINARY": "编过了、有 map，但 .app 里没捞到主可执行文件",
    "BUILT_NO_MAP": "编过了，但没产出 # Path: 指向 .app/ 的那个 map",
    "BUILD_FAILED": "xcodebuild 退出码非 0",
    "BUILD_NOT_ATTEMPTED": "前置步骤没给出 scheme 或 map mode，构建这步被跳过",
    "NO_USABLE_MAP_MODE": "三种 map 路径写法都会撞，工程没法要 map",
    "NO_APP_SCHEME": "没观测到 PRODUCT_TYPE 为 application 的 scheme",
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
    ambiguous = []

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

    observed = len(rows)
    n_jobs = args.expected or observed
    # 「本批应有 N 个 job，回收到 M 份 manifest」——差值必须显式记账，
    # 不能靠分母悄悄变小把失败率做低。
    unaccounted = max(0, n_jobs - observed)
    if unaccounted:
        totals["MANIFEST_MISSING"] += unaccounted

    repos_ok = sum(1 for r, c in per_repo.items() if c["OK"])
    result = {
        "jobs_expected": n_jobs,
        "manifests_observed": observed,
        "manifests_unaccounted": unaccounted,
        "totals": {b: totals[b] for b in BUCKETS},
        "ok_rate": round(totals["OK"] / n_jobs, 4) if n_jobs else 0.0,
        "repos_seen": len(per_repo),
        "repos_with_at_least_one_ok": repos_ok,
        "repo_ok_rate": round(repos_ok / len(per_repo), 4) if per_repo else 0.0,
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
