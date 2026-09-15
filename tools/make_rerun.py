#!/usr/bin/env python3
"""把一批里没产出可评分变体的仓库挑出来，凑成一个新的 batch 重跑。

为什么要有这个
--------------
batch01 的 64 个仓库里只有 19 个产出了可用产物。诊断之后，失败分成三类：

  * **驱动仓库自己的缺陷** —— 容器路径没加引号（`./Little Go.xcworkspace` 被
    按空格切开）、`-maxdepth 2` 够不着子目录里的工程、`-list -json` 失败后
    脚本抛异常什么都不写。这些修完重跑就能救回来。
  * **工程本身不提供可直接构建的 Xcode 工程** —— Bazel、KMM 之类。
  * **平台不符 / 依赖装不上** —— tvOS、React Native。

后两类重跑仍然会失败，但**失败得快**（容器探测或 scheme 探测阶段就退出，
不进构建），代价很低；而把它们排除在重跑之外反而要多一层人工判断。所以默认
全部重跑，由新代码给出各自具名的判定 —— 谁该留在分母里，让数据说。

守恒：入选 + 未入选 = 原批次仓库数，未入选的逐个留理由。
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys


def load_manifests(artifacts_dir):
    """一个仓库的全部 job manifest，按 repo 归拢。"""
    by_repo = collections.defaultdict(list)
    for path in sorted(pathlib.Path(artifacts_dir).rglob("manifest.json")):
        try:
            m = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if m.get("repo"):
            by_repo[m["repo"]].append(m)
    return by_repo


def usable(manifests):
    """这个仓库有没有产出至少一个可评分变体。"""
    for m in manifests:
        for v in (m.get("variants") or []):
            if v.get("map_matches_binary") is True and v.get("binary_bytes"):
                return True
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--artifacts-dir", required=True,
                    help="已下载的那一批产物目录")
    ap.add_argument("--targets", required=True, nargs="+",
                    help="原批次的 targets/batchNN.json（可给多个）")
    ap.add_argument("--exclude-buckets", default="",
                    help="逗号分隔；这些成因的仓库不重跑（默认全部重跑）")
    ap.add_argument("--out", "--json", dest="out", required=True)
    args = ap.parse_args(argv)

    originals = {}
    for t in args.targets:
        doc = json.loads(pathlib.Path(t).read_text(encoding="utf-8"))
        for row in (doc if isinstance(doc, list) else doc.get("targets") or []):
            originals[row["repo"]] = row
    if not originals:
        print("原批次里没有目标", file=sys.stderr)
        return 1

    by_repo = load_manifests(args.artifacts_dir)
    skip = {x.strip() for x in args.exclude_buckets.split(",") if x.strip()}

    picked, left_out = [], []
    reasons = collections.Counter()
    for repo, row in originals.items():
        ms = by_repo.get(repo) or []
        if not ms:
            # 一份 manifest 都没回收 —— 未观测，必须重跑
            cause = "MANIFEST_MISSING"
        elif usable(ms):
            cause = "OK"
        else:
            # 取第一个非空的 scheme 判定当成因；都为空说明容器就没找到
            verdicts = [m.get("scheme_verdict") for m in ms if m.get("scheme_verdict")]
            outcomes = {m.get("build_outcome") for m in ms}
            cause = (verdicts[0] if verdicts
                     else "NO_XCODE_CONTAINER" if "skipped" in outcomes
                     else "BUILD_FAILED")
        reasons[cause] += 1
        if cause == "OK":
            left_out.append({"repo": repo, "reason": "ALREADY_OK"})
        elif cause in skip:
            left_out.append({"repo": repo, "reason": f"EXCLUDED_{cause}"})
        else:
            picked.append({**row, "rerun_cause": cause})

    if len(picked) + len(left_out) != len(originals):
        print(f"守恒失败：{len(picked)} + {len(left_out)} != {len(originals)}",
              file=sys.stderr)
        return 1

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"targets": picked}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    (out.parent / f"_{out.stem}_excluded.json").write_text(
        json.dumps({"originals": len(originals), "rerun": len(picked),
                    "left_out": left_out}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    print(f"原批次 {len(originals)} 个仓库 = 重跑 {len(picked)} + 不重跑 {len(left_out)}")
    print(f"\n{'成因':<28}{'仓库数':>7}")
    print("-" * 36)
    for cause, n in reasons.most_common():
        print(f"{cause:<28}{n:>7}")
    print(f"\n写入 {out}")
    print(f"落选名单 {out.parent / f'_{out.stem}_excluded.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
