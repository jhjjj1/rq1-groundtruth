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

## 已知的工具链域差

runner 默认是 **Xcode 26.6 / iOS SDK 26.5**，而语料库筛的是 `DTXcode >= 1600`
（Xcode 16.x）。链接器不是同一个，产出的二进制形态也就不完全可比。

`probe-p1` 会列出 runner 上装了哪几个 Xcode；两个 workflow 都接受
`developer_dir` 输入，可以把工具链钉到指定版本。钉不钉、钉到哪个，等探针把
可选项列出来再定 —— 在看到列表之前做决定就是猜。
