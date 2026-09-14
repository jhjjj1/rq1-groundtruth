#!/usr/bin/env python3
"""把目标清单 × 构建配置展开成 GitHub Actions 的矩阵。

为什么单独一个脚本,而不是在 YAML 里拼
--------------------------------------
GitHub 的矩阵上限是 **256 job / 每次 run**。超了它的行为是报错,但错误信息
出现在 workflow 解析阶段,离"你该把清单切成几份"很远。这里超限就**立刻失败
并算出该切几份**,把问题挡在跑起来之前。

构建配置的选取依据
------------------
每一项都对应归属算法的一个具体依赖面,不是随手列的:

  base          Release + -Os。基线。
  strip_all     `STRIP_STYLE=all` —— 符号名没了,直接打 `attribution.py` 的
                L0/L1 定位阶梯和符号族证据。**最大杠杆**。
  no_deadstrip  关掉 dead code stripping —— 影响哪些桩还在,这也是路线 B
                (导入面)依赖的东西。
  lto           `LLVM_LTO=YES` —— 跨 .o 内联。**注意:它会让 ground truth
                本身变弱**,因为一个地址可能来自多个 .o 的内联结果。这一档
                必须单独报,并且把"链接器都说不清归属"的地址排除出分母。
  wholemodule   `SWIFT_COMPILATION_MODE=wholemodule` —— 模块内跨文件内联。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MATRIX_LIMIT = 256

CONFIGS = {
    "base": "",
    "strip_all": "STRIP_STYLE=all STRIP_INSTALLED_PRODUCT=YES",
    "no_deadstrip": "DEAD_CODE_STRIPPING=NO",
    "lto": "LLVM_LTO=YES",
    "wholemodule": "SWIFT_COMPILATION_MODE=wholemodule",
}


def slug(repo: str) -> str:
    """`owner/name` → 能当 artifact 名的串。artifact 名不许带 `/`。"""
    return re.sub(r"[^A-Za-z0-9._-]", "-", repo)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", type=Path, required=True)
    ap.add_argument("--configs", default="all")
    args = ap.parse_args()

    doc = json.loads(args.targets.read_text(encoding="utf-8"))
    targets = doc if isinstance(doc, list) else (doc.get("targets") or [])
    if not targets:
        print(f"{args.targets} 里没有目标。空输入不许报成功。", file=sys.stderr)
        return 1

    if args.configs.strip().lower() == "all":
        chosen = list(CONFIGS)
    else:
        chosen = [c.strip() for c in args.configs.split(",") if c.strip()]
        unknown = [c for c in chosen if c not in CONFIGS]
        if unknown:
            print(f"未知配置：{unknown}；可选：{sorted(CONFIGS)}", file=sys.stderr)
            return 1

    include = []
    for target in targets:
        repo = str(target.get("repo") or "")
        sha = str(target.get("sha") or "")
        if not repo or not sha:
            print(f"目标缺 repo 或 sha,跳过：{target}", file=sys.stderr)
            continue
        for config in chosen:
            include.append({
                "repo": repo,
                "sha": sha,                      # 钉死 commit —— 不钉就不可复现
                "slug": slug(repo),
                "config_id": config,
                "build_settings": CONFIGS[config],
                "linkage": target.get("linkage", "UNKNOWN"),
            })

    if len(include) > MATRIX_LIMIT:
        shards = -(-len(include) // MATRIX_LIMIT)
        print(f"矩阵 {len(include)} 个 job 超过上限 {MATRIX_LIMIT}。"
              f"把 {args.targets.name} 切成 {shards} 份再跑"
              f"（每份约 {-(-len(targets) // shards)} 个目标）。", file=sys.stderr)
        return 1

    print(f"matrix={json.dumps({'include': include}, ensure_ascii=False)}")
    print(f"count={len(include)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
