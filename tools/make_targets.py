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
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
#: 想要的分层配比。静态那两档占大头——难点在那儿。
QUOTA = {"STATIC_LIKELY": 0.40, "SPM_ONLY": 0.25, "BOTH": 0.20, "DYNAMIC_LIKELY": 0.15}


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
    ap.add_argument("--per-batch", type=int, default=50,
                    help="每个 batch 多少个目标（× 配置数 ≤ 256）")
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

    picked: list[dict] = []
    shortfall: dict[str, int] = {}
    for link, share in QUOTA.items():
        want = int(args.total * share)
        have = by_link.get(link, [])
        take = have[:want]
        picked.extend(take)
        if len(take) < want:
            shortfall[link] = want - len(take)
    # 配额没填满的，用剩下的补——但把缺口报出来，别静默凑数
    if shortfall:
        print(f"\n!! 分层缺口（想要 vs 实际）：{shortfall}")
        print("   靶心是 STATIC/SPM；这两档缺口大的话，benchmark 会打偏。")
        chosen_ids = {(r["owner"], r["repo"]) for r in picked}
        spare = [r for r in cands if (r["owner"], r["repo"]) not in chosen_ids]
        spare.sort(key=lambda r: -(r.get("stars") or 0))
        picked.extend(spare[: sum(shortfall.values())])

    print(f"\n选中 {len(picked)} 个，正在钉 commit sha …")
    targets = []
    for index, row in enumerate(picked, 1):
        sha = head_sha(row["owner"], row["repo"],
                       row.get("default_branch") or "main", token)
        if not sha:
            print(f"  取不到 sha，跳过：{row['owner']}/{row['repo']}")
            continue
        targets.append({"repo": f"{row['owner']}/{row['repo']}", "sha": sha,
                        "linkage": row.get("linkage"), "stars": row.get("stars"),
                        "locks": row.get("locks")})
        if index % 20 == 0:
            print(f"  {index}/{len(picked)} …")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    batches = [targets[i:i + args.per_batch]
               for i in range(0, len(targets), args.per_batch)]
    for number, batch in enumerate(batches, 1):
        path = args.out_dir / f"batch{number:02d}.json"
        path.write_text(json.dumps({"targets": batch}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        links = collections.Counter(t.get("linkage") for t in batch)
        print(f"  {path}  {len(batch)} 个目标  {dict(links)}")
    print(f"\n共 {len(targets)} 个目标 / {len(batches)} 个 batch")
    print(f"每个 batch × 5 配置 = {args.per_batch * 5} job，上限 256 —— "
          f"{'OK' if args.per_batch * 5 <= 256 else '**超限，减小 --per-batch**'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
