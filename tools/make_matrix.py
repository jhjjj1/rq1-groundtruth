#!/usr/bin/env python3
"""把目标清单 × 构建配置展开成 GitHub Actions 的矩阵。

为什么单独一个脚本,而不是在 YAML 里拼
--------------------------------------
GitHub 的矩阵上限是 **256 job / 每次 run**。超了它的行为是报错,但错误信息
出现在 workflow 解析阶段,离"你该把清单切成几份"很远。这里超限就**立刻失败
并算出该切几份**,把问题挡在跑起来之前。

构建配置的选取依据
------------------
每一项对应归属算法的一个具体依赖面。这是**论证性**的依据,不是统计性的:
真实 App Store 应用用了哪些 build setting 从 IPA 里量不出来(和 map 一样,
build settings 不随 IPA 分发),所以论文只能写"常见开关",不能写"N% 的应用
这样构建"。

每一档还必须有一个**产物级的生效判据**,跑完就量。判据不成立的仓库,那一档
的 P/R 不进平均 —— 否则"配置写了、产物没变"的仓库会把稳健性平均得虚高。
这条规则是被两次同一类错误逼出来的:`STRIP_STYLE=all` 曾经两格全绿但一个
符号没剥;`SWIFT_COMPILATION_MODE=wholemodule` 曾经是一档,48/48 个仓库
四个产物量零变化 —— 因为 Release 本来就是 wholemodule,那一档等于把 base
再链一遍。

  base          Release + -Os。基线。
                产 none + all 两个 strip 变体;none 是诊断上界,不是一种配置。
  no_deadstrip  DEAD_CODE_STRIPPING=NO —— 影响哪些桩还在,这也是路线 B
                (导入面)依赖的东西。
                判据:map 的 # Dead Stripped Symbols 归零。实测 47/48。
  lto           LLVM_LTO=YES —— 跨 .o 内联。实测它**不是**把 .o 合并:对象
                数只 +1,多出来的是 <Target>_lto.o,被 LTO 吸收的符号全部改
                指向它。解析器把它记成 LTO_MERGED / 单元 None(链接器自己也
                说不清),评分时排除出分母 —— 见 parse_link_map.RE_LTO_OBJ。
                判据:_lto.o 出现且吸收了 __text 字节。实测 22/48 出现;
                吸收量差异很大(IceCubesApp 0.11% 字节,TLDR 26%),多半由
                clang 编译的代码占比决定,这一点待从 base map 验证。
  singlefile    SWIFT_COMPILATION_MODE=singlefile —— 关掉 Swift 模块内的跨
                文件优化,往 Release 默认的**反方向**拨。它是 counterfactual
                ablation(实测 0/48 个仓库这样发布),和 base/none 同属诊断
                组,量的是 whole-module optimization 让归属难了多少。
                判据:**未定**。先在少数仓库上探针,看四个噪声稳定量哪个动,
                动的那个才是判据;四个都不动就说明 key 没进到 Swift 编译里,
                要查 build.log 里的 argv,不能把这一档当成功。

strip 为什么不是一档配置
------------------------
它曾经是(`strip_all`),写法是 `STRIP_STYLE=all STRIP_INSTALLED_PRODUCT=YES`。
实测**完全没生效**:base 与 strip_all 的 LC_SYMTAB 是 695,589 / 695,594 条,
二进制反而大了 200 字节 —— STRIP_INSTALLED_PRODUCT 只在安装阶段生效,而
`xcodebuild build` 不走那一步。一个什么都没做的配置却两格全绿。

改正后又量到一件事:`strip` 是**保布局**的。同一次链接的产物 strip 之后,
LC_SYMTAB 从 695,589 掉到 5,186、文件从 62,702,680 掉到 24,647,512 字节,而
节区表逐条不变,链接时产出的 map 仍与它配套(`map_matches_binary = True`)。

所以未 strip 和 strip 后是**同一次链接的两个视图**,不是两个实验。由一次构建
产出两份二进制、共用同一份 map,既去掉了"两次独立构建本身就有差异"这个混杂,
也省掉每个仓库一整次构建。

因此 `strip_variants` 是配置的一个字段,由收集阶段执行,不是 build setting。
只有 `base` 需要未 strip 的那一份(诊断用);其余三档只产 strip 后的,因为那才是
语料库里真实 App Store 二进制的形态。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MATRIX_LIMIT = 256

#: 每一档 = (传给 xcodebuild 的 build settings, 该次链接要产出的 strip 变体)。
#: 变体在收集阶段产生，一次链接多份二进制、共用一份 map —— 见上面的说明。
CONFIGS = {
    "base":         ("", "none,all"),
    "no_deadstrip": ("DEAD_CODE_STRIPPING=NO", "all"),
    "lto":          ("LLVM_LTO=YES", "all"),
    "singlefile":   ("SWIFT_COMPILATION_MODE=singlefile", "all"),
}
#: 已撤销的配置。列在这里是为了让旧的 dispatch 得到一句明确的错误,
#: 而不是"未知配置"。
RETIRED = {
    "wholemodule": "48/48 个仓库产物零变化:Release 默认已是 wholemodule。换成 singlefile。",
    "strip_all":   "STRIP_INSTALLED_PRODUCT 只在安装阶段生效,xcodebuild build 不走。strip 改为收集阶段的变体。",
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
        retired = [c for c in chosen if c in RETIRED]
        if retired:
            for c in retired:
                print(f"配置 {c} 已撤销：{RETIRED[c]}", file=sys.stderr)
            return 1
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
                "build_settings": CONFIGS[config][0],
                "strip_variants": CONFIGS[config][1],
                "linkage": target.get("linkage", "UNKNOWN"),
            })

    if len(include) > MATRIX_LIMIT:
        shards = -(-len(include) // MATRIX_LIMIT)
        print(f"矩阵 {len(include)} 个 job 超过上限 {MATRIX_LIMIT}。"
              f"把 {args.targets.name} 切成 {shards} 份再跑"
              f"（每份约 {-(-len(targets) // shards)} 个目标）。", file=sys.stderr)
        return 1

    # 评分单元是 variant 不是 job：strip 与否是配置维度之一。两个数都报，
    # 免得把「构建了多少次」和「能评多少个对象」混成一个。
    variants = sum(len(j["strip_variants"].split(",")) for j in include)
    print(f"matrix={json.dumps({'include': include}, ensure_ascii=False)}")
    print(f"count={len(include)}")
    print(f"variant_count={variants}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
