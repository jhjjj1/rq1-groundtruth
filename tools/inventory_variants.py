#!/usr/bin/env python3
"""清点可评分变体：每一档有多少样本，以及哪些仓库在所有档都齐。

为什么不只是数个数
------------------
P/R 要按配置分档报。如果 `base/none` 有 21 个仓库而 `lto/all` 只有 19 个，
那「LTO 让 P/R 掉了多少」这句话里就混进了**样本构成的差异** —— 掉的那部分
可能只是因为两档评的不是同一批 app。

所以除了逐档计数，这里还给出**全档齐全的子集**：只有这个子集内的跨档比较
是干净的。两个数都要报，因为它们回答不同的问题：

  * 逐档样本数 —— 各档各自的 P/R 有多少样本支撑
  * 齐全子集   —— 跨档比较（strip 的影响、LTO 的影响）的合法分母

缺档的仓库逐个列出来，连同它在那一档卡在哪 —— 「这一档少两个样本」和
「少的是哪两个、为什么」不是一回事。

「齐全」按什么算
----------------
按 `make_matrix.CONFIGS` **应该有**的档算，不按产物里**碰巧有**的档算。
后者有两个坑：一档还没构建时它会从要求里消失（齐全数虚高）；一档撤销后
（`wholemodule`）它的产物还在盘上，会被当成一档继续要求。撤销的档在这里
直接跳过并说明。

基准集成员资格的**权威实现**是 `compare_configs.gate`：它还验 map 非空、
各档同一 commit。这里是变体层面的快速清点，两边的「齐全」集合应当一致；
不一致就是 bug，不是两种口径。
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_matrix                                           # noqa: E402

#: 应该有的档：config/strip_style。撤销的配置不在里面。
EXPECTED_SLOTS = tuple(sorted(
    f"{cfg}/{style}"
    for cfg, (_settings, styles) in make_matrix.CONFIGS.items()
    for style in styles.split(",")))


#: scheme 这一步判成功的两种判定。落到这两种却仍然没样本，卡点在后面。
SCHEME_OK = frozenset({"APP_SCHEME_FOUND", "APP_SCHEME_AMBIGUOUS"})


def scan(dirs):
    """扫多个批次目录，返回 (变体记录, 每仓库每配置的失败成因)。"""
    variants, causes, repos = [], {}, set()
    for root in dirs:
        for path in sorted(pathlib.Path(root).rglob("manifest.json")):
            try:
                m = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            repo, cfg = m.get("repo"), m.get("config_id")
            if not repo or not cfg:
                continue
            repos.add(repo)
            usable_here = False
            # 构建没成功的 job 不算样本，哪怕它留下了配套的 map + 二进制
            # （部分链接会这样）。与 compare_configs.gate 的 BUILD_FAILED 一致。
            built = m.get("build_outcome") == "success"
            for v in (m.get("variants") or []):
                if built and v.get("map_matches_binary") is True and v.get("binary_bytes"):
                    usable_here = True
                    variants.append({
                        "repo": repo, "config_id": cfg,
                        "strip_style": v.get("strip_style"),
                        "slot": f"{cfg}/{v.get('strip_style')}",
                        "binary_bytes": v.get("binary_bytes") or 0,
                        "nsyms_after": v.get("nsyms_after"),
                        "app_map_bytes": m.get("app_map_bytes") or 0,
                        "batch": pathlib.Path(root).name,
                        "scheme": m.get("scheme"),
                        "scheme_verdict": m.get("scheme_verdict"),
                    })
            if not usable_here:
                # 成因要报**真正卡住它的那一步**。scheme 判定成功却没样本，
                # 说明卡在构建 —— 这时报 APP_SCHEME_FOUND 是在答非所问。
                sv = m.get("scheme_verdict")
                if sv in SCHEME_OK:
                    bo = str(m.get("build_outcome") or "unknown").upper()
                    causes[(repo, cfg)] = f"BUILD_{bo}"
                else:
                    causes[(repo, cfg)] = sv or "NO_XCODE_CONTAINER"
    return variants, causes, repos


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+", help="一个或多个已下载的批次目录")
    ap.add_argument("--out", "--json", dest="out", default=None)
    args = ap.parse_args(argv)

    variants, causes, all_repos = scan(args.dirs)
    if not variants:
        print("没有可评分变体", file=sys.stderr)
        return 1

    retired = sorted({v["config_id"] for v in variants if v["config_id"] in make_matrix.RETIRED})
    if retired:
        n_ret = sum(1 for v in variants if v["config_id"] in retired)
        print(f"产物里有已撤销配置 {retired} 的 {n_ret} 个变体，不计入："
              + "；".join(f"{c}: {make_matrix.RETIRED[c]}" for c in retired))
        print()
        variants = [v for v in variants if v["config_id"] not in retired]
    stray = sorted({v["slot"] for v in variants} - set(EXPECTED_SLOTS))
    if stray:
        print(f"产物里有 CONFIGS 之外的档 {stray}；矩阵变过就要同步改 make_matrix.CONFIGS",
              file=sys.stderr)
        return 1

    slots = list(EXPECTED_SLOTS)              # 应该有的，不是碰巧有的
    by_slot = collections.defaultdict(list)
    for v in variants:
        by_slot[v["slot"]].append(v)

    repos_by_slot = {s: {v["repo"] for v in by_slot.get(s, [])} for s in slots}
    complete = set.intersection(*repos_by_slot.values()) if repos_by_slot else set()
    any_repo = set.union(*repos_by_slot.values()) if repos_by_slot else set()

    print(f"{'配置/变体':<24}{'样本':>6}{'仓库':>6}{'二进制合计':>16}{'map 合计':>14}")
    print("-" * 68)
    for s in slots:
        rows = by_slot[s]
        print(f"{s:<24}{len(rows):>6}{len(repos_by_slot[s]):>6}"
              f"{sum(r['binary_bytes'] for r in rows):>16,}"
              f"{sum(r['app_map_bytes'] for r in rows):>14,}")
    print("-" * 68)
    print(f"{'合计':<24}{len(variants):>6}{len(any_repo):>6}"
          f"{sum(v['binary_bytes'] for v in variants):>16,}"
          f"{sum(v['app_map_bytes'] for v in variants):>14,}")

    print()
    print(f"扫描到的仓库           {len(all_repos)}")
    print(f"至少一档有样本的仓库   {len(any_repo)}")
    print(f"**全档齐全的仓库**     {len(complete)}"
          f"  ← 跨档比较（strip / LTO / singlefile 的影响）唯一合法的分母")
    empty = [s for s in slots if not by_slot.get(s)]
    if empty:
        print(f"还没有任何样本的档     {empty}  ← 这一档没跑，齐全数因此为 {len(complete)}")

    if any_repo - complete:
        print()
        print("缺档的仓库（跨档比较时必须排除，或单独说明）：")
        for repo in sorted(any_repo - complete):
            missing = [s for s in slots if repo not in repos_by_slot[s]]
            why = {causes.get((repo, s.split("/")[0])) for s in missing}
            why.discard(None)
            print(f"  {repo:<44} 缺 {','.join(missing)}"
                  + (f"  成因 {sorted(why)}" if why else ""))

    result = {
        "slots": slots,
        "per_slot": {s: {"variants": len(by_slot[s]),
                         "repos": sorted(repos_by_slot[s]),
                         "binary_bytes": sum(r["binary_bytes"] for r in by_slot[s]),
                         "app_map_bytes": sum(r["app_map_bytes"] for r in by_slot[s])}
                     for s in slots},
        "variants_total": len(variants),
        "repos_scanned": len(all_repos),
        "repos_with_any_variant": sorted(any_repo),
        "repos_complete_across_slots": sorted(complete),
        "incomplete": {r: {"missing": [s for s in slots if r not in repos_by_slot[s]]}
                       for r in sorted(any_repo - complete)},
        "variants": variants,
    }
    if args.out:
        pathlib.Path(args.out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n写入 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
