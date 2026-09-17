# B 线执行方案：补收 app bundle，直到产出 ① 需要的分析器输出

交给执行者的完整方案。**B1 / B2 / B3 的代码都已经写好并通过测试（12 个测试全 PASS），一律不要重写**；你要做的是按顺序跑命令、核判据、把输出贴回来。任何一步的输出与"期望"不符，停下来贴给发起人，不要绕过去。

为什么代码是现成的：这一轮之前的教训正是「看上去做完了、其实没做」，所以凡是能提前写死成脚本判据的，都写死了，执行阶段不留自由发挥的余地。

---

## 0. 为什么要补这一轮

`PrivacyInfo.xcprivacy` 确实是开发者写在仓库里的源文件，扫描器早就在读了。所以这一轮**不是为了取回清单内容**，是为了观测三件源码回答不了的事：

1. **扩展与内嵌框架的二进制，现在一份都没有。** 第一轮的收集只取了 `app / exe_name`，即主 App 的可执行文件。链接图那边默认保留 `APP_BUNDLE,APPEX_BUNDLE` 两类，于是出现了「有一份描述 widget 扩展的链接图，却没有它描述的那个二进制」。App 与 extension 不共享 `.standard`、只有 app group 跨得过去，这正是 1C8F 那条理由的核心，也是 RQ⑤ 的主要观测对象。
2. **清单的归属由构建决定，不由源码决定。** 仓库里有一堆 `.xcprivacy`：测试 target 的、示例工程的、没被链进来的包的。哪些进了发布包、进到哪一层（包根 / `Frameworks/X.framework/` / `PlugIns/Y.appex/` / 资源 `.bundle`），由 Xcode 的 Resources 阶段和内嵌关系决定。RQ① 的"调用点对应哪个申报单元"、RQ② 的"声明缺失"，答案都在这一层。静态链接的 SDK 尤其关键：代码并进了主二进制，它的清单跟不跟着进包，只有打开包才知道。
3. **被测工具的输入必须是发布包。** 分析器的命题是"给定一个 IPA，能不能查出这些"。用源码里捞出来的清单喂它，测的就是另一台仪器。真值可以来自源码，被测对象不能。

构建流程本身一个字没改。第一轮的链接图与主二进制继续有效，新产物是在旁边多一份。

---

## B1. 收产物时一并打包 bundle（代码已完成）

### B1.1 改了什么

**`tools/collect_build_artifacts.py`** —— 新增 `pack_bundle()` / `verify_bundle()` / `is_macho()`，在 strip 循环之后调用。

*只装分析器会打开的东西*，规则是读分析器源码定的，不是猜的：

| 它要什么 | 在分析器的哪里 |
|---|---|
| `Info.plist`、`Entitlements.plist` | `inventory/ipa_zip.py` |
| `PrivacyInfo.xcprivacy` | `inventory/manifests.py` |
| 各级 bundle 的 Mach-O 与 `.dylib` | `inventory/ipa.py`、`inventory/macho.py` |
| `.app` / `.appex` / `.framework` / `.bundle` 的目录结构 | 同上 |

保留判据：名字在 `BUNDLE_KEEP_NAMES`、后缀在 `BUNDLE_KEEP_SUFFIXES`、**或前四字节是 Mach-O magic**（`Frameworks/Foo.framework/Foo` 没有后缀，只能看 magic）。其余不进包：`Assets.car`、图片、音频、字体、`.nib`、`.storyboardc`、`.strings`。丢掉的按后缀记账写进 manifest。留下的每个字节都是构建写出来的那个字节，路径也是构建放的那个路径 —— 清单归属必须由 Xcode 决定，不能由我们拼。

*每个 strip 档位一份包*：`bundle.none.ipa` / `bundle.all.ipa`，与 `binary.<style>.gz` 对应。符号丢失是实验的一个维度，只评测未 strip 的包等于只测容易的那一半。`strip` 对包里**每一个** Mach-O 执行（主程序、每个 `.appex`、每个内嵌框架），用与二进制那条路相同的 flags。非代码文件（plist、xcprivacy）一字不动，两个包的成员清单完全相同。

*专门处理的情况*：指向包内的符号链接跟着解析并写入，指向包外的跳过只记数；读不了的文件记进 `bundle_unreadable` 继续走；超预算（`--max-bundle-mb`，默认 200）**照传**只记 `BUNDLE_OVER_BUDGET`（悄悄丢掉会和"这个 App 没有清单"长得一样）；zip 时间戳固定为 1980-01-01，同一份构建打两次字节一致。

*复检*：重开 zip，确认 `Payload/` 下只有一个 `.app` 根、主可执行文件在，并把它的 sha256 与**同一 strip 档位**的 `binary.<style>.gz` 对账。两条路各自独立跑了 strip，对得上才说明描述的是同一个东西；对不上判 `EXECUTABLE_DIFFERS_FROM_COLLECTED_BINARY`。`OK_NO_PRIVACY_MANIFEST` 单列一档：这个 App 确实一份清单都没有，是观测结果，和"打包失败"分得开。

manifest 新增：`bundle_variants`（逐档位：文件、字节、strip 了几个、`strip_failed`、`verify`）、`bundle_full_bytes`、`bundle_kept_files/bytes`、`bundle_dropped_files/bytes`、`bundle_dropped_by_ext`、`bundle_macho_files`、`bundle_privacy_manifests`（路径列表）、`bundle_nested_bundles`、`bundle_symlinks`、`bundle_unreadable`、`bundle_usable`，以及代表变体的 `bundle_zip` / `bundle_zip_bytes` / `bundle_verify` / `bundle_note`。

**`usable_for_groundtruth` 没有动。** 它的含义是"map 与二进制配套"，第一轮的数按它报的；并进 bundle 会让旧数悄悄换含义。新判据单列 `bundle_usable`。

**`.github/workflows/build.yml`** —— `Collect artifacts` 末尾加 `--max-bundle-mb 200`；`Job summary` 多一张 bundle 表（内容清单 + 逐档位一行）。上传那步不用改，`path: ${{ runner.temp }}/out` 本来就是整个目录。

**`tools/aggregate_manifests.py`** —— 加 `BUNDLE_BUCKETS` / `bundle_bucket()` / `BUNDLE_WHY`，与原有 `BUCKETS` 并列不合并。第一轮的 manifest 没有 bundle 字段，一律落 `NOT_COLLECTED` —— "那一轮没收"不是"收了是空的"。

**`tools/make_rerun.py`** —— 加 `--pick {failed,ok}`，默认 `failed` 即原行为。这一轮用 `--pick ok`：只重跑已经成功的 46 个，失败的重跑还是会失败，只烧 runner 时间。

### B1.2 推之前本地验

```bash
cd ~/autodl-tmp/gt_driver
tar xzf ~/autodl-tmp/gt_ci_bundle_v1.tgz
python3 tools/tests/test_collect_bundle.py
python3 tools/tests/test_aggregate_manifests.py
python3 tools/tests/test_compare_manifest_attribution.py
python3 tools/tests/test_run_analyzer_matrix.py
for t in tools/tests/test_*.py; do printf '%s: ' "$t"; python3 "$t" >/dev/null 2>&1 && echo PASS || echo FAIL; done
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/build.yml')); print('workflow YAML OK')"
```

十二个测试全 PASS。`test_collect_bundle.py` 用手搭的 bundle 覆盖了会决定结果的形状：内嵌框架 / 扩展 / 资源包各一份清单、无后缀的 Mach-O、`.dylib`、包内与包外两种符号链接、五类必须丢掉的资源；再按分析器的打开方式重新打开验一遍，并验两次打包字节一致、两个 strip 档位成员清单相同且非代码文件不动、"没有清单"与"打包失败"分得开、超预算照传。

### B1.3 生成只含那 46 个 App 的 targets

```bash
cd ~/autodl-tmp
python3 gt_driver/tools/make_rerun.py --artifacts-dir gt \
        --targets gt_driver/targets/batch01.json gt_driver/targets/batch02.json gt_driver/targets/batch03.json \
        --pick ok --out gt_driver/targets/bundle01.json > rerun_pick.log 2>&1
cat rerun_pick.log
python3 -c "import json;print('入选仓库',len(json.load(open('gt_driver/targets/bundle01.json'))['targets']))"
```

**期望入选 46。** 少了说明有 App 不在 batch01–03 里，把 `gt_driver/targets/rerun01.json` 也加进 `--targets` 再跑一次。

### B1.4 提交、触发、拉回

提交信息见本文末尾附录。推送后在 Actions 手动触发 `build-groundtruth`：`batch = bundle01`，`configs = all`，其余默认。矩阵应当是 **46 × 4 = 184 个 job**。

```bash
cd ~/autodl-tmp
python3 gt_driver/tools/fetch_run.py --out-dir gt_bundles --wait \
        --aggregate gt_bundles_agg.json > fetch_bundles.log 2>&1
tail -40 fetch_bundles.log
```

`fetch_run.py` 自己翻页、跟重定向、对账、断点续传，token 从 `~/.git-credentials` 取；中断了重跑同一条命令。

**不要把任何长跑命令接 `| head` / `| tail`** —— 管道提前关闭会用 BrokenPipe 杀掉进程，这个项目已经因此丢过一次扫描结果。一律重定向到文件再看。

### B1.5 验收

```bash
cd ~/autodl-tmp
python3 - > report/bundle_harvest.txt 2>&1 <<'PY'
import json, glob, collections
verd = collections.Counter(); per_style = collections.Counter(); mani = collections.Counter()
bad = []; nostrip = []; zipb = 0; jobs = 0
for p in sorted(glob.glob("gt_bundles/*/manifest.json")):
    m = json.load(open(p)); jobs += 1
    for var in (m.get("bundle_variants") or []):
        v = var.get("verify") or {}
        per_style[var["strip_style"]] += 1
        verd[v.get("verdict") or var.get("note") or "NO_BUNDLE"] += 1
        zipb += var.get("bytes") or 0
        if v.get("matches_binary") is False:
            bad.append(f"{m['repo']}/{m['config_id']}/{var['strip_style']}")
        if var["strip_style"] != "none" and var.get("strip_failed"):
            nostrip.append(f"{m['repo']}/{m['config_id']}: {len(var['strip_failed'])} 个没 strip 成")
    mani[len(m.get("bundle_privacy_manifests") or [])] += 1
print("作业数:", jobs, " 期望 184")
print("逐档位包数:", dict(per_style), " 期望 none/all 各 184")
print("复检结论:", dict(verd))
print("每个作业的 PrivacyInfo.xcprivacy 份数分布:", dict(sorted(mani.items())))
print("包合计:", f"{zipb/1048576:.1f} MB")
print("与同档位二进制对不上的:", bad or "无")
print("strip 没跑成的:", nostrip[:10] or "无")
PY
cat report/bundle_harvest.txt
```

**硬判据两条**：`与同档位二进制对不上的` 必须是 `无`（不是空就说明两半材料不是同一次构建，比少收一个包严重得多）；`逐档位包数` 两个档位都应当接近 184。

`每个作业的 PrivacyInfo.xcprivacy 份数分布` 里有 0 的是正常的 —— 那些 App 确实没申报，这本身就是 RQ② 的观测数据。

---

## B2. 用真包核扫描器的清单归属推断（工具已写好）

扫描器现在用 `covering_manifest()` **推断**哪份清单覆盖哪段代码，推断方式记在 `manifest_scope` 里（`TARGET_RESOURCE` / `NEAREST_ANCESTOR` / `APP_LEVEL` / `APP_LEVEL_UNION` / `*_FALLBACK_LOCAL_PKG` / `*_FALLBACK_EXTENSION_TARGET` / `NO_*`）。真包到手后第一次能核这个推断，这是一条能写进论文的验证。

`tools/compare_manifest_attribution.py` 做三层比对，三层的结论分开报，不合并：

- **文件一级**：源码里每一份 `PrivacyInfo.xcprivacy` 按 sha256 与包里的对身份（Xcode 是原样拷贝，所以身份是精确的）。答"哪些源码清单进了包、落在哪个组件、包里哪些清单在源码清单之外（依赖自带的）"。
- **申报一级**：扫描器归给这个 App 代码的 (类别, 理由码) 对，与包里任何一份清单声明的对，分成 `AGREE` / `SCANNER_OVER`（扫描器说有、包里没有）/ `SCANNER_UNDER`（包里有、扫描器没算上）。**过报和漏报必须分开** —— 合并计数会让两者互相抵消，看上去一切正常。
- **推断机制一级**：同样的判定按 `manifest_scope` 分桶。某个机制系统性出错（比如 LOCAL_POD 一律退回 App 级清单），会显示成一个集中的过报数，而不是散落的噪声。

另外它会横向核一件本不该发生的事：**同一个 App 的四个配置，清单集合应当完全相同**。不同就报出来。

```bash
cd ~/autodl-tmp
python3 gt_driver/tools/compare_manifest_attribution.py \
        --bundles gt_bundles --worksheets worksheets_v36 --src src \
        --json report/manifest_attribution.json > report/manifest_attribution.log 2>&1
cat report/manifest_attribution.log
```

判据：跑完要能回答"扫描器的清单归属在 46 个 App 上有多少完全一致，错的集中在哪个 `manifest_scope`"。若某个机制的过报数明显集中，单独列出来 —— 那要回头改扫描器，**属于 v3.7 的内容，这一轮不改**。

## B3. 跑分析器，产出 ① 的输入（工具已写好）

### B3.1 先摸清分析器要什么（不要猜路径）

```bash
cd ~/autodl-tmp
unzip -o -q gt_bundles/gt-Jaennaet-pISSStream-base/bundle.none.ipa -d ~/autodl-tmp/probe_ipa
find ~/autodl-tmp/probe_ipa -maxdepth 4 | head -30
ls -d ~/autodl-tmp/cross_rra_analyzer_0.123.82
python3 -m rra_analyzer.cli --help > ~/autodl-tmp/analyzer_help.txt 2>&1 ||   rra-analyzer --help > ~/autodl-tmp/analyzer_help.txt 2>&1
head -60 ~/autodl-tmp/analyzer_help.txt
echo "=== Ghidra 在哪:"; ls -d ~/autodl-tmp/ghidra* /opt/ghidra* 2>/dev/null; which analyzeHeadless 2>/dev/null
```

**Ghidra 的路径、分析器的调用方式（`rra-analyzer` 还是 `python3 -m rra_analyzer.cli`）、以及 `--work-dir` 该指到哪里（autodl 上没有 `/tmp`，指 `~/autodl-tmp/` 下），这三件确认了再往下。** 拿不准就把上面的输出贴给发起人，不要猜一个路径试。

### B3.2 先 dry-run，再试跑 3 格，最后铺全量

```bash
cd ~/autodl-tmp
A="python3 -m rra_analyzer.cli"          # 按 B3.1 的结果改
R=~/autodl-tmp/cross_rra_analyzer_0.123.82/rra_rules.yaml
G=<Ghidra 目录>                           # 按 B3.1 的结果填

# 1) 只打印命令，不执行 —— 念一遍第一条命令确认无误
python3 gt_driver/tools/run_analyzer_matrix.py --bundles gt_bundles --out analyzer \
        --rules "$R" --analyzer "$A" --ghidra-dir "$G" --work-dir ~/autodl-tmp/anwork \
        --dry-run > report/analyzer_dryrun.log 2>&1
head -5 report/analyzer_dryrun.log

# 2) 试跑 3 格，把耗时报回来再决定铺不铺
python3 gt_driver/tools/run_analyzer_matrix.py --bundles gt_bundles --out analyzer \
        --rules "$R" --analyzer "$A" --ghidra-dir "$G" --work-dir ~/autodl-tmp/anwork \
        --limit 3 --timeout 7200 > report/analyzer_try3.log 2>&1
tail -25 report/analyzer_try3.log
```

试跑那一段会打印平均耗时并按它估算剩余格数的串行总时长。**把这个估算贴给发起人再铺全量** —— 368 格如果单格半小时就是七天，那要先谈并发或砍配置，不能闷头开跑。

铺全量时去掉 `--limit`；中断了重跑同一条命令即可（已完成的格子记 `DONE_CACHED` 跳过）。

### B3.3 这个工具保证的事

- 矩阵是 **App × 配置 × strip 档位**，格数在开跑前就打印出来，`RUN_INDEX.json` 里 `cells_expected` 钉死；
- 七种状态互斥且合计等于格数（脚本里有断言）：`DONE` / `DONE_CACHED` / `EMPTY_OUTPUT` / `FAILED` / `TIMEOUT` / `NO_BUNDLE` / `NOT_ATTEMPTED`。**`EMPTY_OUTPUT` 单列一档** —— 退出码 0 但什么都没产出，和成功不是一回事，这正是这个项目反复栽的那个形状；
- 每格一个 `run.log`（第一行是完整命令）、一个耗时、一份输出文件清单；
- `RUN_INDEX.json` 每跑完一格重写一次，中途挂了也留下完整账目；
- 记下 `rules_sha256` 与分析器自报的版本（取不到就如实记取不到）。

## B4. 交回什么

- `report/bundle_harvest.txt`（B1.5 的输出）
- `report/manifest_attribution.json` 与 `report/manifest_attribution.log`（B2）
- `analyzer/RUN_INDEX.json`、`report/analyzer_try3.log` 的耗时估算与失败分档（B3）
- 过程中发现的、与本文档不符的事实，逐条写进 `report/B_NOTES.md`

---

## 这一轮不做的事

- 不动构建步骤、不动链接图的收法、不动 strip 变体的定义 —— 第一轮那 360 份链接图与主二进制继续用；
- 不重跑第一轮失败的仓库（`--pick ok` 已排除）；
- 不动 `usable_for_groundtruth` 的定义；
- **不从源码里找 `PrivacyInfo.xcprivacy` 往包里塞** —— 那是造数据，清单归属会变成我们编的，整条 B 线的意义就没了；
- 不改扫描器。B2 发现的不一致先记录，改动放到 v3.7 单独做。

## 附录：提交信息

```
收产物时一并打包 app bundle，补上申报那一半的材料

第一轮只上传了主 App 的可执行文件：扩展与内嵌框架的二进制、PrivacyInfo.xcprivacy
与包结构随 runner 销毁。于是既有一份描述 widget 扩展的链接图却没有对应的二进制，
也没有任何声明侧数据，分析器（入口要 IPA）更是跑不起来。

按分析器实际会打开的文件定保留规则：各级 Info.plist / Entitlements.plist /
PrivacyInfo.xcprivacy、全部 Mach-O 与 .dylib、bundle 目录结构；Assets.car 与图片
音频字体 nib 不进包，丢弃项按后缀记账。包内容一字不改，路径保持构建时的样子，
清单归属由 Xcode 决定而不是我们拼。

每个 strip 档位一份包（bundle.none.ipa / bundle.all.ipa），strip 对包里每一个
Mach-O 执行 —— 符号丢失是实验的一个维度，只评测未 strip 的包等于只测容易的一半。
复检重开 zip 比对 Payload 根唯一、主可执行文件存在、其 sha256 与同档位的
binary.<style>.gz 一致。usable_for_groundtruth 不动，新判据单列 bundle_usable。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Cmc58sBx2XbyB3NHvLHjER
```
