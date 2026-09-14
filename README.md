# RQ1 代码归属 Ground Truth —— 构建驱动仓库

链接器 map 给出「每个符号 → 来自哪个 .o / .a」的精确映射，那就是每个调用点
所属申报单元的免费 ground truth。本仓库用 GitHub Actions 的 macOS runner 在
多种构建配置下批量构建开源 iOS 应用，产出 map + 二进制。

## 为什么这么设计

* **不 fork 目标仓库**：job 里动态 clone，目标仓库一个都不用改。
* **仓库设为 public**：公开仓库的 Actions 分钟数免费，macOS runner 也免费
  （私有仓库 macOS 约 $0.062/分钟，是 Linux 的 10 倍）。
* **`fail-fast: false`**：构建失败率本身是要报的数
  （「N 个开源 iOS 应用里只有 M 个能复现构建」），失败必须被记录而不是中止全批。
* **钉死 commit sha**：ground truth 是某一次构建的产物，对不上 commit 就不可复现。

## 硬限制（决定矩阵怎么切）

| | |
|---|---|
| 矩阵上限 | 256 job / 每次 workflow run |
| macOS 并发 | 5（Free 与 Pro 相同） |
| 单 job 超时 | 6 小时（本 workflow 设 60 分钟） |
| 公开仓库分钟数 | 免费 |

50 个目标 × 5 配置 = 250 job，刚好在 256 以内；÷ 5 并发 × 约 10 分钟
≈ 8 小时挂机。

## 用法

```bash
# 1) 生成目标清单（在有 candidates.jsonl 的机器上跑）
export GITHUB_TOKEN=…
python3 tools/make_targets.py --candidates candidates.jsonl --total 100 --per-batch 50

# 2) 推上去
git add targets && git commit -m "targets" && git push

# 3) 触发（命令行，不用点网页）
gh workflow run build.yml -f batch=batch01 -f configs=all
gh run watch

# 4) 取产物
gh run download <run-id> -D artifacts/
```

没有 `gh` 时用 REST API 触发（token 需要对本仓库有 `Actions: read and write`）：

```bash
curl -X POST -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/<你>/<本仓库>/actions/workflows/build.yml/dispatches \
  -d '{"ref":"main","inputs":{"batch":"batch01","configs":"all"}}'
```

## 构建配置

每一项对应归属算法的一个具体依赖面：

| id | 设置 | 打什么 |
|---|---|---|
| `base` | Release / -Os | 基线 |
| `strip_all` | `STRIP_STYLE=all` | 符号名没了 → 定位阶梯与符号族证据。**最大杠杆** |
| `no_deadstrip` | `DEAD_CODE_STRIPPING=NO` | 哪些桩还在（路线 B 的导入面也依赖它） |
| `lto` | `LLVM_LTO=YES` | 跨 .o 内联。**会让 ground truth 本身变弱**，单独报 |
| `wholemodule` | `SWIFT_COMPILATION_MODE=wholemodule` | 模块内跨文件内联 |

## 产物

每个 job 上传一个 artifact，含：

| 文件 | 用途 |
|---|---|
| `maps/*` | ground truth 本体：符号 → .o/.a。**每个 target 一份**，原因见下 |
| `maps_index.json` | 每个 map 候选的路径、字节数、**头 12 行原文** |
| `binary` | 分析对象 |
| `uuid.txt` | `LC_UUID` —— map 与二进制是不是同一个的锚点 |
| `loadcmds.txt` / `size.txt` / `lipo.txt` | 排查用 |
| `schemes_classified.json` | 每个 scheme 的 `PRODUCT_TYPE` 判定过程 |
| `map_mode.json` | 三种 map 路径写法各自解析成什么 |
| `build.log` | 失败时的证据 |
| `manifest.json` | 这次跑的全部事实，**失败也记** |

map 由 `tools/parse_link_map.py` 解析成「地址 → 归属单元」。单元由一条**有序
规则梯**从 object file 路径推出,每条记录带上生效的规则名；一条都匹配不上就是
`NO_RULE_MATCHED`,不往邻近单元里塞：

| 规则 | 匹配 | 单元 |
|---|---|---|
| `INDEX_ZERO` | `[0] linker synthesized` | 无 |
| `ARCHIVE_MEMBER` | `libX.a(Y.o)` | `libX`（CocoaPods 走这条） |
| `TARGET_INTERMEDIATE` | `Intermediates.noindex/<P>.build/<C>/<T>.build/.../y.o` | `<T>` |
| `TBD` / `DYLIB` / `FRAMEWORK_BINARY` | 系统库 | 库名 |
| `BUILD_PRODUCT_OBJECT` | `Build/Products/**/X.o` | `X`（SwiftPM 走这条） |
| `NO_RULE_MATCHED` | 其余 | 无,原样记下来 |

`unit_at(addr)` 返回 `None` 表示**这个地址没被 map 覆盖**,不表示它不属于任何
单元。零长符号是别名,不占区间,不参与归属。

解析器**不判**第一方/第三方：map 分不出本地包和拉取的包,两者都落在
`Build/Products`。那一层要 `Package.resolved` / `Podfile.lock`,归有这些文件的
那一步管。

`maps_index.json` 里存头 12 行，是因为 **runner 上是 Xcode 26.6，本项目从没读过
这个工具链产出的 map**。解析器要照着观测到的真实格式写，不照着记忆里的 ld64
格式写。

## run #1 暴露的四个缺陷

第一次 P1 跑网页上显示 **Success**，总时长 1 分 39 秒，产物 3547 字节。四个缺陷，
都是驱动仓库自己的，不是目标仓库的：

| | 缺陷 | 证据 | 修法 |
|---|---|---|---|
| B1 | `xcodebuild \| tee \| tail` 没开 `pipefail`，退出码取自 `tail` 恒为 0 | 日志里 `** BUILD FAILED **`，而 `M_OUTCOME: success` | `set -o pipefail` |
| B2 | scheme 按字母序取 `schemes[0]` | 28 个 scheme 里取到 `Account`（SPM 库），app 是第 10 位的 `IceCubesApp` | `pick_app_scheme.py` 按 `PRODUCT_TYPE` 判 |
| B3 | `LD_MAP_FILE_PATH` 在命令行上是全局覆盖 | 21 条 `duplicate output file` warning，XCBuild 计划阶段拒编 | `decide_map_mode.py` 逐仓库量 |
| B4 | `ls **/Podfile` —— 不开 globstar 时 `**` 等于 `*`，只看一层 | `Package.resolved` 在四层深，依赖解析从没跑过 | 改用 `find` |

B1 尤其要记住：`steps.<id>.outcome` 是**应用 `continue-on-error` 之前**的结果。
它记成 success 说明那一步真的返回了 0 —— 是管道吞码，不是 `continue-on-error`。

## 判定都由事实决定，不由名字决定

| 判什么 | 判据 | 不用什么 |
|---|---|---|
| 哪个 scheme 是 app | `xcodebuild -showBuildSettings` 报的 `PRODUCT_TYPE == com.apple.product-type.application` | scheme 名、字母序 |
| map 路径怎么写 | 三种写法各跑一次 `-showBuildSettings`，看每个 target 解析出的路径是否互不相同、有无残留 `$(` | 「`$(TARGET_TEMP_DIR)` 应该会展开」 |
| 这个 job 算不算成功 | `maps_with_requested_basename > 0` **且** `binary_bytes > 0` | job 的绿勾 |

拿不到主判据时退到次判据（如 `WRAPPER_EXTENSION`），并把**实际生效的是哪条规则**
写进结果文件 —— 「判出来的」和「退而求其次猜的」不能长得一样。

## 构建可复现率

`report` job 把全部 manifest 聚合成分桶表。分桶是**有序且互斥**的，一个 job 只
算进最早卡住它的那一档，且**分桶合计必须等于本批应有 job 数**：

```
OK / BUILT_NO_BINARY / BUILT_NO_MAP / BUILD_FAILED /
BUILD_NOT_ATTEMPTED / NO_USABLE_MAP_MODE / NO_APP_SCHEME / MANIFEST_MISSING
```

没回收到 manifest 的 job 单独一档，并且**留在分母里**。把它们从分母拿掉会让
失败率被做低 —— 「未观测到」不是零结果。

这个率是论文外部效度段要写的数（「N 个开源 iOS 应用里只有 K 个能复现构建」），
所以由流水线自己算，不靠人事后数绿勾。

## 还没验的三个前提（P1 闸门）

方案成立依赖这三条，任一不成立就要改形态：

1. **strip 前后地址不变** —— 连接键是地址，不是符号名
2. **map 对应的就是分析的那个二进制** —— 靠 `LC_UUID` 比对
3. **`.a(x.o)` → pod 名查得回去** —— 歧义时记 `GT_AMBIGUOUS`，排除出分母，不猜

## 探针 run #1 实测（2026-09-14，macos-latest / Xcode 26.6 / Swift 6.3.3）

**map 格式是经典 ld64,ld_prime 没改。** 26 个 map、369,818 个符号行,
`parse_link_map.py` 解析失败 **0**,地址区间重叠 **0**,`__text` 覆盖 **100%**
（appex 那个 map:3,341,096 字节全覆盖,差值 0,不是四舍五入）。

appex 主二进制的 map:

| | |
|---|---|
| 符号行 | 33,998 |
| size>0 的符号 | 28,130 |
| 其中归不到真实 `.o` | **175 = 0.62%** |
| Dead Stripped Symbols | 9,225（单独成段,各带来源索引） |
| object file 命中具名规则 | 56/56,`NO_RULE_MATCHED` = 0 |

**SwiftPM 依赖是合并后的 `<产品名>.o`**,不是 `.a(成员.o)`：

```
[ 4] .../Build/Products/Release-iphoneos/Models.o          ← 11,338 符号
[ 1] .../IceCubesActionExtension.build/.../ActionRequestHandler.o  ← app 自己的代码
[10] .../libclang_rt.ios.a(os_version_check.c.o)           ← 系统静态库才是 .a(x.o)
```

所以 **P1 前提 3** 对 SPM 仓库而言是平凡的：单元名就是文件名,没有歧义。
CocoaPods 仓库走 `ARCHIVE_MEMBER` 规则,那一条还没验过。

**scheme 普查 62.6 秒 / 28 个（中位 1.9s）。** 占本次 8m44s 的 12%,
占一次成功的 app 构建（20~40 分钟）的 3~5%。**不加提前退出** —— 为省这一分钟
放弃歧义检测不划算,而这次正是歧义检测拦住了错误的 scheme。

**构建失败原因不是配置,是编译器崩了：**

```
Apple Swift version 6.3.3
While running pass #65824 SILFunctionTransform "EarlyPerfInliner"
  on SILFunction "...MediaUIZoomableContainerV...CoordinatorCfD"
  for 'deinit' (at Packages/MediaUI/Sources/MediaUI/MediaUIZoomableContainer.swift:96:11)
```

## 工具链：钉哪一版，由语料库自己说

两个 runner 镜像上的工具链（实测，probe run #1 / #3）：

| 镜像 | 可选 Xcode | 默认 |
|---|---|---|
| `macos-latest` | 26.0.1 ~ 26.6 | 26.6 / iphoneos26.5 / Swift 6.3.3 |
| `macos-15` | 16.0 ~ 16.4，**外加** 26.0.1 ~ 26.3 | 16.4 / iphoneos18.5 / Swift 6.1 |

语料库 7,714 个包的 `DTXcode` 分布（`pool_inventory_v12361.jsonl`）：

| | 包数 | 占比 |
|---|---:|---:|
| Xcode 26.x | 5,268 | **68.3%** |
| Xcode 16.x | 1,889 | 24.5% |
| Xcode 15.x | 556 | 7.2% |

按 SDK 看得更细：`iphoneos26.2` **2,009 = 26.0%**（单一最大桶），
`iphoneos26.5` 1,295 = 16.8%，`iphoneos26.0` 825 = 10.7%。

**结论：主配置钉 `macos-latest` + `Xcode_26.2.app`（SDK iphoneos26.2）。**
不是因为它新，是因为它命中语料库最大的那个桶。钉 16.x 会把域差从最小拉到
最大，同时还编不动一批仓库 —— 近期维护的开源 iOS 仓库和语料库一样都在 26.x
上，两边诉求一致，这里没有取舍。

**但必须如实写进论文的一句：没有任何单一工具链能覆盖超过 26% 的语料库。**
语料库在工具链上是异质的，选一版消不掉这件事，只能把敏感度量出来 —— 所以
除主矩阵外，用 `base` 配置在另一版工具链上再跑一遍，报出「换工具链后 P/R
变了多少」。

### 工具链不匹配会直接表现成构建失败

不是抽象顾虑，两次都撞上了：

* **Xcode 26.6 / Swift 6.3.3**：SIL 优化器编 `MediaUI` 时 crash
  （`EarlyPerfInliner` on `MediaUIZoomableContainerV...CoordinatorCfD`）。
* **Xcode 16.4 / Swift 6.1**：IceCubesApp 的 13 个本地包声明
  `swift-tools-version: 6.2.0`，包图**解析不了**，`exit 74`，连 scheme
  列表都拿不到。

两者都被记成这一档工具链下的失败，计入构建可复现率的分母 —— 流水线没坏，
这就是要报的数。
