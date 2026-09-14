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
| `link.map` | ground truth 本体：符号 → .o/.a |
| `binary` | 分析对象 |
| `uuid.txt` | `LC_UUID` —— map 与二进制是不是同一个的锚点 |
| `loadcmds.txt` | 排查用 |
| `build.log` | 失败时的证据 |
| `manifest.json` | 这次跑的全部事实，**失败也记** |

## 还没验的三个前提（P1 闸门）

方案成立依赖这三条，任一不成立就要改形态：

1. **strip 前后地址不变** —— 连接键是地址，不是符号名
2. **map 对应的就是分析的那个二进制** —— 靠 `LC_UUID` 比对
3. **`.a(x.o)` → pod 名查得回去** —— 歧义时记 `GT_AMBIGUOUS`，排除出分母，不猜
