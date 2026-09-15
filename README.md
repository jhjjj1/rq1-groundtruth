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

## 拉产物（`tools/fetch_run.py`）

一批 256 个 job 产出 257 个 artifact、7~9 GB，手动拉不现实。两个坑足以让
「拉完了」和「拉全了」长得一样：

* **分页**：`/actions/runs/{id}/artifacts` 默认每页 100 条，不翻页只拿到前
  100 个，**接口不会报错**，剩下 157 份静默消失。job 列表同理。
* **重定向**：下载地址跳到另一台主机，`curl` 默认不把 Authorization 带过去
  （这是对的），所以用 curl 下载、用 API 取元数据，不自己跟跳转。

脚本会翻页、断点续传（已下好的跳过）、并做**对账**：接口说多少个，本地就得
有多少个目录，对不上非零退出。

```bash
nohup python3 tools/fetch_run.py \
  --out-dir ~/autodl-tmp/gt/batch01 --wait \
  --aggregate tools/aggregate_manifests.py \
  > ~/autodl-tmp/gt/batch01.log 2>&1 &
```

## batch01 暴露的三个驱动缺陷（已修）

64 个仓库只有 19 个产出可用产物。诊断之后，失败分成**驱动自己的 bug** 和
**工程本身不可构建**两类 —— 这个区分直接决定「构建可复现率」的分子分母。

| 缺陷 | 实测证据 | 修法 |
|---|---|---|
| 容器路径**未加引号** | `xcodebuild -workspace ./Little Go.xcworkspace` 被 bash 按空格切开。实测三个仓库：`Little Go.xcworkspace`、`3. iOS app/DMT.xcworkspace`、`draggable slider/draggable slider.xcodeproj` | 容器拆成 `flag` + `path` 两个输出，用处一律加引号 |
| `-maxdepth 2` 够不着 | `simplex-chat`（`apps/ios/`）、`joreilly/BikeShare` 被判成「没有工程」 | 搜到第 4 层，浅的优先；排除 `Pods` / `node_modules` / `Carthage` / `DerivedData` |
| `-list -json` 失败后**脚本崩了** | 12 个仓库的产物里连 `schemes_classified.json` 都没有，manifest 的 `scheme_verdict` 是空字符串 —— 裸 `json.load` 抛异常，什么都没写 | 读输入永不抛异常；坏输入记成 `SCHEMES_JSON_UNREADABLE` 并留下原文头部与 `-list` 的退出码 |

还有一个只有跑起来才会暴露、被本地测试提前逮到的：`--container-flag "-project"`
会被 argparse 当成选项名拒绝消费（`expected one argument`）。容器 flag 现在
不带横杠传递，用的时候再加。

### 新增的具名判定

上一版把成因完全不同的失败压进了 `NO_APP_SCHEME` 和 `BUILD_NOT_ATTEMPTED`
两档。现在各自成档，因为「该不该算进分母」的答案不一样：

| 判定 | 含义 | 实例 |
|---|---|---|
| `NO_XCODE_CONTAINER` | 四层之内没有 `.xcodeproj` / `.xcworkspace` | `Telegram-iOS`（Bazel） |
| `NO_SHARED_SCHEMES` | 工程在但没有共享 scheme（scheme 常在 `xcuserdata` 里不进版本库） | `bitwarden/ios`、`AdguardForiOS` |
| `PLATFORM_MISMATCH` | 目标平台不是 iOS | `ATV-Bilibili-demo`（tvOS） |
| `SCHEMES_JSON_UNREADABLE` | `-list -json` 没给出可解析输出 | 12 个仓库 |
| `NO_APP_SCHEME` | 探测了全部 scheme，没有一个产物是 application | —— |

`NO_SHARED_SCHEMES` 有退路：`-list -json` 同时给 `targets`，
`xcodebuild -target` 不需要 scheme 就能查、能编。这条只对 `-project` 有效 ——
workspace 没有 targets 那一层，只能如实记。

### 重跑（`tools/make_rerun.py`）

把一批里没产出可评分变体的仓库凑成新 batch。默认**全部重跑**：不可构建的那些
重跑仍会失败，但失败得快（容器或 scheme 阶段就退出，不进构建），代价很低；
而事先把它们摘掉要多一层人工判断。守恒：入选 + 未入选 = 原批次仓库数。

## 分批（硬限制）

GitHub 的矩阵上限是 **256 job / 每次 run**。配置 4 档，所以每批最多 64 个目标。
181 个目标 → **3 个 batch，合计 724 job、905 个待评分变体**。

`make_targets.py --per-batch 0` 会按 `make_matrix.CONFIGS` 的档数自动取最大值，
不再硬编码 —— 上一版写死 5 档，而配置改成 4 档之后那句「每批多少个不超限」
就悄悄说错了。

### 选样守恒

`targets/_excluded.json` 逐个记下**没成为目标的候选**及其理由
（`NOT_SELECTED__OVER_TARGET_N` / `SHA_UNAVAILABLE`），并强制
**候选 = 目标 + 落选**，对不上直接失败。

这条是为一次实跑事故加的：181 个候选跑出 180 个目标 —— 四档配额各做一次
`int()` 向下取整（45+81+27+27=180），补位按 `sum(shortfall)` 补，正好剩一个
`SPM_ONLY` 没人要。丢一个样本不算大事，**丢了却没人知道是谁**才是问题。

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
| `base` | Release / -Os | 基线。**产 `none` + `all` 两个变体** |
| `no_deadstrip` | `DEAD_CODE_STRIPPING=NO` | 哪些桩还在（路线 B 的导入面也依赖它） |
| `lto` | `LLVM_LTO=YES` | 跨 .o 内联。**会让 ground truth 本身变弱**，单独报 |
| `wholemodule` | `SWIFT_COMPILATION_MODE=wholemodule` | 模块内跨文件内联 |

strip 不是配置，是**变体**：由收集阶段在链接之后执行，一次链接可产多份二进制、
共用一份 map。`none` = 未 strip（诊断用），`all` = 全 strip（与语料库形态一致）。
`strip_all` 这个配置名已废弃 —— 它曾经是个什么都没做的 build setting。

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
| `POD_VENDORED_ARCHIVE` | `**/Pods/<pod>/**/libX.a(Y.o)` | `<pod>` —— pod 名在**路径**里 |
| `POD_BUILT_FROM_SOURCE` | `Build/Products/<cfg>/<Pod>/lib<Pod>.a(Y.o)` | `<Pod>` |
| `BUILT_PRODUCT_ARCHIVE` | `Build/Products/<cfg>/<D>/lib*.a(Y.o)`，库名对不上 | `<D>`，并记下不一致 |
| `ARCHIVE_MEMBER` | 其余 `libX.a(Y.o)` | `libX`（系统库、工具链库） |
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
OK / MAP_BINARY_MISMATCH / BUILT_NO_BINARY / BUILT_NO_MAP / BUILD_FAILED /
BUILD_NOT_ATTEMPTED / NO_USABLE_MAP_MODE / NO_APP_SCHEME / MANIFEST_MISSING
```

`MAP_BINARY_MISMATCH` 是文件都在但节区表对不上号（或读不出来）—— 齐全不等于
配套，这种产物必须判负而不是当成 OK。

没回收到 manifest 的 job 单独一档，并且**留在分母里**。把它们从分母拿掉会让
失败率被做低 —— 「未观测到」不是零结果。

这个率是论文外部效度段要写的数（「N 个开源 iOS 应用里只有 K 个能复现构建」），
所以由流水线自己算，不靠人事后数绿勾。

## P1 闸门：三个前提的实测结论

原本写的是三条「还没验的前提」。跑完之后，**其中两条的措辞是错的**，错在我，
不在实现。修正后的版本和各自的证据：

> **三条全部验完（build run #3，Xcode 26.2）。**两条的措辞是错的，改了；
> 改对之后都成立。下面每条都带自己的实测证据。

### 前提 1（原：strip 前后地址不变）—— 表述错了，改对后成立

原来的验法是拿 `base` 和 `strip_all` 两次**独立构建**去比。实测：

```
__text  size 17,316,112 vs 17,316,424    起始地址相同，差 312 字节
同名符号 232,681 个，地址不同的 156,165 = 67.12%
```

同一 commit、同一 Xcode，两次构建 67% 的符号地址就不一样 —— 编译/链接本来就
不是确定性的。但这**不影响评分**：每个 job 自己构建、自己产 map，评分只在同
一次链接内部闭环，从不跨配置复用 map。所以这条约束是我当初多加的。

正确的表述是：**同一次链接产出的 map，对该次 `strip` 之后的二进制仍然有效**
—— 因为 `strip` 是链接后的后处理。实测（同一次链接内部）：

```
nsyms   695,589 → 5,186          降 99.25%
字节    62,702,680 → 24,647,512   降 60.7%
strip_preserves_layout = True     节区表逐条不变
map_matches_binary     = True     且是在 strip 之后仍然成立
```

最后一行是整个方案的地基：strip 后的二进制，节区表仍与链接时的 map 逐条相等，
所以「在 strip 后的二进制上跑归属算法、用链接时的 map 当真值」站得住。
每个 job 自带这三个数，不再是写在文档里的假设。

**这也改变了矩阵形态。**既然 strip 保布局，未 strip 和 strip 后就是**同一次链接
的两个视图**，不是两个实验。由一次构建产出两份二进制、共用一份 map：既去掉了
「两次独立构建本身就有差异」这个混杂，也省掉每个仓库一整次构建。

| | job | 待评分变体 |
|---|---:|---:|
| 原（5 配置 × 181） | 905 | 905 |
| 现（4 配置 × 181，base 产双变体） | **724** | 905 |

只有 `base` 需要未 strip 的那一份（诊断用）；其余三档只产 strip 后的，因为那
才是语料库里真实 App Store 二进制的形态。

### 前提 2（原：靠 `LC_UUID` 比对）—— 判据不存在，换成节区表后成立

map 文件里**没有 UUID 字段**（解析 315,059 行，只有 `# Path:` / `# Arch:` /
`# Sections:` / `# Symbols:` / `# Dead Stripped Symbols:`）。而且 `LC_UUID`
本身不稳定：同一 SHA、同一 Xcode 26.2 连着三次构建给出
`FAFA5749…` / `3796D76A…` / `89B98069…`。

正确的锚点是**节区表**：map 的 `# Sections:` 和二进制的 `otool -l` 各列一份
（段、节名、地址、大小），逐条比对。在真实产物上验过：

| | |
|---|---|
| `base`：map 42 节 ↔ 二进制 42 节 | `IDENTICAL` |
| `strip_all`：map 42 节 ↔ 二进制 42 节 | `IDENTICAL` |
| **证伪项**：`base` 的 map vs `strip_all` 的二进制 | `SAME_SECTIONS_DIFFERENT_LAYOUT`，25 行不同 |

第三行是量具的证伪项 —— 它必须能把两个不同的二进制分开，否则什么都判不出来。
现在每个 job 自动做这件事，结果写进 `map_matches_binary`，并且**它是
`usable_for_groundtruth` 的必要条件**：文件都在但对不上号，这份产物判负。

### 前提 3（`.a(x.o)` → 单元名可回查）—— 成立，对 SwiftPM 是平凡的

app 主二进制的 map 里 192 个 object file **全部**由具名规则推出单元，
`NO_RULE_MATCHED` = 0，共 119 个单元。SwiftPM 依赖以 `<产品名>.o` 形式进来，
单元名就是文件名。`ARCHIVE_MEMBER`（`.a(x.o)`）那条规则有 15 个命中，都是系统
静态库；**CocoaPods 仓库的那条路径还没验过**。

### 附带发现：`strip_all` 这档配置原本什么都没做

```
base       LC_SYMTAB  nsyms 695,589   strsize 27,270,824   binary 62,702,680
strip_all  LC_SYMTAB  nsyms 695,594   strsize 27,270,944   binary 62,702,880
```

strip 掉全部符号的二进制比不 strip 的还大 200 字节 —— 因为根本没 strip。
`STRIP_INSTALLED_PRODUCT=YES` 只在安装阶段生效，`xcodebuild build` 不走那一步。
而两格都显示 success。

这正是本项目一贯反对的形态：没测和测过长得一样。现在 strip 由收集脚本在链接
之后显式执行（`--strip-style`），并强制记录 `nsyms_before` / `nsyms_after`
—— 没降下来就是没 strip，一眼可见。

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

## 探针 run #4 实测（Xcode 26.2 / Swift 6.2.3，主二进制编出来了）

钉 `Xcode_26.2.app` 同时解决两件事：命中语料库最大的 SDK 桶（iphoneos26.2，
26.0%），以及绕开 26.6 的 `EarlyPerfInliner` 崩溃 —— 26.2 带 Swift 6.2.3，
正好满足那些本地包要的 `swift-tools-version: 6.2.0`。

主二进制 `Ice Cubes.app/Ice Cubes`，62,702,680 字节，arm64，
`LC_UUID = FAFA5749-BC3C-3553-994D-F92E5F7EEC50`。它的 map：

| | |
|---|---|
| 符号行 / 解析失败 | **315,059 / 0** |
| size>0 符号 | 280,437 |
| 归不到 `.o` | **791 = 0.28%** |
| dead-stripped | 135,037 |
| `__text` 覆盖 | 17,316,104 / 17,316,104 = **100.0000%**，差 0 字节 |
| 地址区间 / 重叠 | 280,437 / **0** |
| object file 命中具名规则 | **192 / 192**，`NO_RULE_MATCHED` = 0 |
| 归属单元 | **119** |

全部 39 个 map 合计 1,140,639 符号行，解析失败 0。**P1 前提 3（单元名可回查）
就此验完**；前提 1（strip 前后地址不变）和前提 2（`LC_UUID` 对得上）要 base 与
strip_all 两次构建对比，一个产物里做不到。

### 域差的第一个实证点：这个二进制里没有 `__objc_stubs`

节区表里有 `__stubs`、`__objc_methlist`、一整排 `__swift5_*`，**没有
`__objc_stubs`**。开源 iOS 仓库是 Swift 重、ObjC 轻的，而语料库里是真实
App Store 包，带着大量 ObjC 第三方 SDK。也就是说 **ground truth 语料可能根本
不覆盖 ObjC 那条检测路径**。这要写进外部效度段，而且现在有了可量的对照维度。

### 产物体量

单个 job：主二进制 62 MB + app map 51 MB。905 个 job 按原样上传是 ~100 GB。
所以 `collect_build_artifacts.py` 只上传 `# Path:` 指向 `.app/` 或 `.appex/`
的 map（其余 34 个是 `ld -r` 预链接合并产物，`# Path:` 以 `<产品名>.o` 结尾，
不是任何人会去分析的二进制），全部 gzip，保留期 7 天。

**没上传的 map 仍然逐个记在 `maps_index.json` 里**（路径、字节数、头 12 行）——
「丢掉」和「从未存在」不能长得一样。

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
