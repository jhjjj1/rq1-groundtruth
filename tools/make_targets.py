#!/usr/bin/env python3
"""`candidates.jsonl` → `targets/batchNN.json`，按链接方式分层、钉死 commit。

两件事必须在这里做完
--------------------
1. **钉死 sha**。清单里只有 repo 名是不够的——ground truth 是某一次构建的
   产物，对不上 commit 就不可复现。这里用 GitHub API 取当前默认分支的 HEAD。
2. **分层配额**。`attribution.py:60` 把 `APP` 排除在 `CONTAINMENT_HOSTS`
   之外（"主二进制会把静态链接进来的 SDK 一起装着，挑战 C1"），全池 APP 侧
   结构归属只有 1.5%。**靶心是静态链接**，所以 STATIC/SPM 那两档要保量，
   不能让 DYNAMIC 把名额占光。
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_matrix import CONFIGS, MATRIX_LIMIT   # noqa: E402

API = "https://api.github.com"
#: **这些标签只用于选样，不进最终读数。**
#:
#: `pick_groundtruth_repos.py` 的 `classify_linkage` 把「包管理器」和「链接
#: 方式」混成了一个维度，标签有误导性：`SPM_ONLY` 不是一种链接方式，它是
#: 一种包管理器，而 **SPM 默认就是静态链接**——它和 `STATIC_LIKELY` 一样
#: 会把依赖装进主二进制，同属 C1 难点。实测 181 个候选里
#: STATIC_LIKELY 7 + SPM_ONLY 141 = 148（81.8%）都落在这一类。
#:
#: 真正的链接方式**由链接器 map 说了算**（符号来自 `.a(x.o)` 还是独立 dylib
#: 是确定事实）。最终 P/R 报告的分层必须用 map 推出来的真值，不许用下面这些
#: 启发式标签——本项目在「拿查找路径当证据的代理」上栽过两次
#: （`attribution.py` 顶部记着 0.123.44 / 0.123.45 两次方向相反的事故）。
HARD_CASE = ("STATIC_LIKELY", "SPM_ONLY")     # 静态进主二进制 —— 靶心

#: 配比按「是不是靶心」分，不按包管理器分。动态那档是对照组：
#: A1′ 容纳证据在 FRAMEWORK 宿主上该接近满分，它用来验证算法没有系统性偏差。
QUOTA = {"STATIC_LIKELY": 0.25, "SPM_ONLY": 0.45,
         "BOTH": 0.15, "DYNAMIC_LIKELY": 0.15}


def head_sha(owner: str, repo: str, branch: str, token: str | None) -> str:
    request = urllib.request.Request(f"{API}/repos/{owner}/{repo}/commits/{branch}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "rq1-gt")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return str(json.loads(response.read()).get("sha") or "")
    except urllib.error.HTTPError:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", type=Path, required=True)
    ap.add_argument("--total", type=int, default=100, help="总共挑多少个仓库")
    ap.add_argument("--per-batch", type=int, default=0,
                    help="每个 batch 多少个目标；0 = 按矩阵上限自动取最大值")
    ap.add_argument("--out-dir", type=Path, default=Path("targets"))
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    rows = [json.loads(line) for line in
            args.candidates.read_text(encoding="utf-8").splitlines() if line.strip()]
    cands = [r for r in rows if r.get("state") == "CANDIDATE"]
    print(f"候选 {len(cands)} 个")

    by_link: dict[str, list] = collections.defaultdict(list)
    for row in cands:
        by_link[str(row.get("linkage") or "UNKNOWN")].append(row)
    for link, group in sorted(by_link.items()):
        group.sort(key=lambda r: -(r.get("stars") or 0))
        print(f"  {link:18} {len(group)}")

    # 目标数不能超过候选数 —— 想要 181 而只有 150 个候选时，分母是 150。
    target_n = min(args.total, len(cands))

    picked: list[dict] = []
    shortfall: dict[str, int] = {}
    for link, share in QUOTA.items():
        want = int(target_n * share)
        have = by_link.get(link, [])
        take = have[:want]
        picked.extend(take)
        if len(take) < want:
            shortfall[link] = want - len(take)
    # 配额没填满的，用剩下的补——但把缺口报出来，别静默凑数
    hard = sum(len(by_link.get(k, [])) for k in HARD_CASE)
    total_c = len(cands) or 1
    print(f"\n靶心（静态进主二进制 = STATIC_LIKELY + SPM_ONLY）："
          f"{hard}/{total_c} = {hard / total_c * 100:.1f}%")
    if shortfall:
        print(f"!! 分层缺口（想要 vs 实际）：{shortfall}")
        picked_hard = sum(1 for r in picked if r.get("linkage") in HARD_CASE)
        print(f"   选中里靶心占 {picked_hard}/{len(picked) or 1}"
              f" = {picked_hard / (len(picked) or 1) * 100:.1f}%")
        print("   靶心低于 60% 的话 benchmark 会打偏（APP 侧结构归属只有 1.5%）。")
    # 补位补到 target_n，不是补到 sum(shortfall)。
    #
    # 上一版补 sum(shortfall) 个，结果 181 个候选只产出 180 个目标：四档配额
    # 各做一次 int() 向下取整，45+81+27+27=180，合计比 181 少 1，那一个
    # SPM_ONLY 就没人要了。丢一个样本不算大事，**丢了却没人知道是谁**才是问题。
    chosen_ids = {(r["owner"], r["repo"]) for r in picked}
    spare = [r for r in cands if (r["owner"], r["repo"]) not in chosen_ids]
    spare.sort(key=lambda r: -(r.get("stars") or 0))
    need = max(0, target_n - len(picked))
    picked.extend(spare[:need])

    # 没被选中的，逐个留名字和理由。选样过程必须可回查，不能只剩一个总数。
    chosen_ids = {(r["owner"], r["repo"]) for r in picked}
    excluded = [{"repo": f"{r['owner']}/{r['repo']}",
                 "linkage": r.get("linkage"), "stars": r.get("stars"),
                 "reason": "NOT_SELECTED__OVER_TARGET_N"}
                for r in cands if (r["owner"], r["repo"]) not in chosen_ids]

    print(f"\n选中 {len(picked)} 个，正在钉 commit sha …")
    targets = []
    for index, row in enumerate(picked, 1):
        sha = head_sha(row["owner"], row["repo"],
                       row.get("default_branch") or "main", token)
        if not sha:
            print(f"  取不到 sha，跳过：{row['owner']}/{row['repo']}")
            excluded.append({"repo": f"{row['owner']}/{row['repo']}",
                             "linkage": row.get("linkage"),
                             "stars": row.get("stars"),
                             "branch": row.get("default_branch") or "main",
                             "reason": "SHA_UNAVAILABLE"})
            continue
        targets.append({"repo": f"{row['owner']}/{row['repo']}", "sha": sha,
                        "linkage": row.get("linkage"), "stars": row.get("stars"),
                        "locks": row.get("locks")})
        if index % 20 == 0:
            print(f"  {index}/{len(picked)} …")

    args.out_dir.mkdir(parents=True, exist_ok=True)

    # 守恒检查：目标 + 落选 必须等于候选。对不上就是有样本在中间蒸发了，
    # 直接失败，不许带着一个说不清的差额往下跑。
    if len(targets) + len(excluded) != len(cands):
        print(f"守恒失败：目标 {len(targets)} + 落选 {len(excluded)} "
              f"= {len(targets) + len(excluded)} ≠ 候选 {len(cands)}",
              file=sys.stderr)
        return 1
    (args.out_dir / "_excluded.json").write_text(
        json.dumps({"candidates": len(cands), "targets": len(targets),
                    "excluded": excluded}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    # 配置数不写死：它由 make_matrix.CONFIGS 决定，改配置时这里自动跟着变。
    # 上一版硬编码 5，而配置已经从 5 档变成 4 档（strip 不再是配置，是变体）,
    # 硬编码会让「每批多少个才不超限」这句话悄悄说错。
    n_configs = len(CONFIGS)
    max_per_batch = MATRIX_LIMIT // n_configs
    per_batch = args.per_batch or max_per_batch
    if per_batch > max_per_batch:
        print(f"--per-batch {per_batch} × {n_configs} 配置 = "
              f"{per_batch * n_configs} job，超过上限 {MATRIX_LIMIT}；"
              f"最大可用 {max_per_batch}", file=sys.stderr)
        return 1

    batches = [targets[i:i + per_batch]
               for i in range(0, len(targets), per_batch)]
    for number, batch in enumerate(batches, 1):
        path = args.out_dir / f"batch{number:02d}.json"
        path.write_text(json.dumps({"targets": batch}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        links = collections.Counter(t.get("linkage") for t in batch)
        print(f"  {path}  {len(batch)} 个目标  {dict(links)}")
    n_variants = sum(len(v[1].split(",")) for v in CONFIGS.values())
    by_reason = collections.Counter(e["reason"] for e in excluded)
    print(f"\n候选 {len(cands)} = 目标 {len(targets)} + 落选 {len(excluded)}"
          f"  {dict(by_reason) if by_reason else ''}")
    print(f"  落选名单已写入 {args.out_dir / '_excluded.json'}")
    print(f"共 {len(targets)} 个目标 / {len(batches)} 个 batch"
          f"（每批 {per_batch} 个）")
    print(f"配置 {n_configs} 档 → 每批 {per_batch * n_configs} job"
          f"（上限 {MATRIX_LIMIT}）")
    print(f"全部 {len(targets)} 个目标合计 {len(targets) * n_configs} job、"
          f"{len(targets) * n_variants} 个待评分变体")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
