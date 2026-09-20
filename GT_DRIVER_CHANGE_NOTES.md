# gt_driver 工具改动说明（一致性度量 / 规则性修正 / 第三遍抽样 / 冻结）

对应 `CODE_CHANGE_PLAN_agreement_rulefix_gt.md` 的 M1–M7。分析器核心未改（计划 §0 的程序依据仍成立）。
所有数字都是在本地全量数据上跑出来的，与计划里的预期不符的地方**没有改预期去凑**，逐条写在 §3。

## 1. 文件

| 文件 | 类型 | 说明 |
|---|---|---|
| `tools/rule_table.py` | 新建 | §4.5 域判定与 §4.7 ALT 判定的唯一可执行实现；`resolve_domain` / `group_facts_of` / `expected_domain_verdict` / `expected_alt_exceeds` |
| `tools/annotate_validate.py` | 修改 | `--rule-table {1.10,1.11}`、`--rule-checks {on,off}`、`--site-index`；两条阻塞规则 R‑UD‑DOMAIN / R‑ALT‑EXCEEDS；`predicate_of` 抽出成模块函数；`--json` 改为带 `run`（运行口径）+ `totals`（合计）+ `files` |
| `tools/apply_rule_fixes.py` | 新建 | 修正登记簿：`FIX_1_11_DOMAIN`、`FIX_C2_STANDARD`；五种结局逐条计数；`--dry-run`；幂等 |
| `tools/agreement.py` | 新建 | 2–3 轮的 Fleiss κ（m 名通式）/ Cohen（两两）/ PABAK / Gwet AC1 + 按单元簇 bootstrap CI；精确集合与 (站点,约束) 条目；分层、翻转方向、子样本 SD、逐字段不一致明细 |
| `tools/sample_third_pass.py` | 新建 | 样本 A（按单元分层均匀随机 + 每单元下限）与样本 B（四类全取，不进 κ） |
| `tools/subset_batches.py` | 新建 | 从已发出的批次切子集，站点与头逐字节保留，`MANIFEST.json` 仍可被 `make_packs.py` 读 |
| `tools/freeze_gt.py` | 新建 | §7 判据逐条检查（任一不过什么都不写）；`gt_confidence` 四档；`FREEZE.json` 的 `process` 过程记录段 |
| `tools/tests/test_*.py` | 新建 5 个 | 与既有测试同风格：独立脚本，`python3 tools/tests/test_x.py` 直接跑，通过打印 `PASS …` |

## 2. 三条与计划不同的判定原则（都改到更保守的方向，各有实测依据）

计划里的域解析是"先到先得"（`domain_hint` → `instance_domains` → notes）。在全量数据上跑出来有三类
**标注是对的、预填或文本是错的**的情形，会被报成违规。所以改成下面三条：

**① 扫描器解不出 suite 时不表态。** §4.5 的"suite 名未解析 → UNKNOWN"是写给标注者的，标注者打开了整个
单元，扫描器没有。按"UNKNOWN 是期望值"判，KeePassium 的 `UserDefaults.appGroupShared`（suite 是另一个
文件里的 `AppGroup.id`，标注方解出来了）这类被误报 **265 次**。现在 `SUITE_UNRESOLVED → None`。

**② 标注文字只能否决，不能表决。** 批次里的结构化事实（`domain_hint` / `instance_domains` /
`callers.by_domain`）是唯一能**确立**读数的来源；标注的 `notes` 经常引用别的文件的代码（helper 的定义、
键常量、兄弟站点），当作对本站点的断言读会两头出错。现在文字只做一件事：与批次读数矛盾时把结果打成
UNKNOWN（不判、不修）。wBlock 的 `UserDefaults(suiteName: GroupIdentifier.shared.value) ?? .standard`
有 59 个站点正是这样——预填看见 `?? .standard` 判 APP_PRIVATE，标注方跨文件解出 group suite 判 CONFLICT，
两个读数并存 ⇒ 不表态。

**③ `.standard` 必须带接收者。** 裸 `\.standard\b` 会命中字符串字面量里的 suite 名
（wBlock 有个一次性 suite 就叫 `test.wblock.…cloudhosts.standard.\(UUID())`），误报 **28 次**。
现在只认 `(?:NS)?UserDefaults\s*\.\s*standard` / `standardUserDefaults` / `UserDefaults()` /
`[[NSUserDefaults alloc] init]`。另加一条否决信号：标注里写出具体 App Group id（`group.<...>`）而预填说
`.standard` 时不表态——量过，这条对要修的那 229 条零影响（它们的 notes 一个 `group.` id 都没提）。

**④ 计划里没有的出口：`DOMAIN_OVERRIDE:` / `ALT_OVERRIDE:`。** 照搬已有的 `GUARD_OVERRIDE:` 惯例
（§4.1，编译守卫事实要显式推翻）。上面①②③之外仍有 15 个 wBlock 站点，标注写的是"域见 L75 的 group suite"
——对的，但不是机器可核验的写法。有了出口，第三遍可以改判并写明理由；没有出口，校验器会把正确的标注
报成错，而那会训练标注者忽略校验器。

## 3. 实测数字（本地全量；与计划预期的差额逐条解释）

### 3.1 规则表校验

| 跑法 | R‑UD‑DOMAIN 违规 | R‑ALT‑EXCEEDS 违规 | 备注 |
|---|---|---|---|
| R1 / 1.10 | 15 | **123** | 计划预期 ALT 132 |
| R2 / 1.10 | 231 | 0 | 计划预期 ≈337 |
| R2 / 1.11 | **229** | 0 | 全部是 C2 的 `.standard`→UNKNOWN 一类 |
| R2 修正后 / 1.11 | **0** | 0 | 验收判据 |

- **337 → 229**：其余 108 条里 104 条站点是 `SELF_INSTANCE`（UserDefaults 扩展体，域由调用方决定），
  4 条是 `MIXED_DOMAINS`。本地这份旧格式批次没有 `callers.by_domain`，服务器上的 183 批有；`rule_table.py`
  已经实现了这一级解析，到服务器上会多判出一部分，判不出的列进 `NOT_APPLICABLE` 不改。
- **132 → 123**：本地 `batches_all/` 只覆盖 4,538 个站点里的 4,474（少 64 个，同样是批次格式代差），
  差的 8 个 ALT 站点不在索引里；另 1 条 `value_fate=["RETURNED"]` 规则表本来就不表态。
- R2 在 1.10 下比 1.11 多的 2 条，正是 1.11 要重写的那一行（自定义非 group suite）。

### 3.2 规则性修正（dry-run 与实跑一致）

```
FIX_C2_STANDARD   FLIPPED=229  ALREADY_NEW=0  SAME_UNDER_BOTH=0  DIVERGENT=0  NOT_APPLICABLE=260
                  不表态的域证据：UNKNOWN 243（多为 SELF_INSTANCE）、SUITE 8、SUITE_UNRESOLVED 5、MIXED 4
FIX_1_11_DOMAIN   FLIPPED=0    ALREADY_NEW=2  SAME_UNDER_BOTH=533 DIVERGENT=0  NOT_APPLICABLE=611
                  不表态的域证据：UNKNOWN 549、SUITE_UNRESOLVED 60、MIXED 2
```

**`FIX_1_11_DOMAIN` 机械翻转 0 条**，这是本次最需要发起人知道的结果。原因是程序事实不够：要翻转必须有
解析出来的 suite 值，而那 280 个 `RCA92_C1=CONFLICT` 站点的 suite 大多是**字符串插值**
（`"test.wblock.…\(UUID().uuidString)"`），扫描器判 unresolved 是对的——那本来就不是常量。按 `domain_hint`
分档：UNKNOWN 128 / APP_PRIVATE 59（`?? .standard`）/ APP_GROUP 39（真 group，两版都 CONFLICT，本就不该翻）/
`SUITE_CONST:… unresolved` 54。其中 24 条能从站点自带的上下文窗口里找到那条 `let <ident> = "<含插值的字面量>"`
绑定——这是可用的程序事实，但只覆盖 24/280，未实现，等发起人决定。

顺带查到一条与评估有关的事实：分析器对自定义非 group suite 在 `DefaultsDomainIs(APP_PRIVATE)` 上输出
**indeterminate（UNKNOWN）**（`analysis/falsification.py` L226–L234 的 `indeterminate` 字段，v0.86 由发起人
裁决："自定义 domain 既不必然跨 app 可达，也不必然只有本 app 能访问，静态分析在这里只能说判不了"）。
所以这 280 条无论真值写 CONFLICT 还是 SUPPORTED，在 L5 的三列读法里都落在**弃权**列，不影响分析器得分；
它影响的是真值与 1.11 协议自身是否自洽。

### 3.3 一致性（`agreement.py`，B=2000，seed 20260919，按单元簇）

`kappa_r1_r2.py` 的全部数字原样复现，连 CI 都一致（回归测试里钉住：is_api_use 0.589 / operation 0.882 /
applicable_reason 0.874 / value_fate 0.431 / 约束裁决 0.748 / 并集 0.601 / exceeds −0.678）。

**修正把约束层的一致性抬上来了**，其余字段一个不动（修正只碰 verdict）：

| 口径 | 修正前 Po / κ | 修正后 Po / κ | 修正后 95% CI |
|---|---|---|---|
| constraint_verdicts（各轮都裁决） | 0.884 / 0.748 | **0.934 / 0.846** | [0.703, 0.967] |
| constraint_verdicts（并集含 ABSENT） | 0.803 / 0.601 | **0.848 / 0.676** | [0.233, 0.818] |

（与 9‑19 那份一致性文档里"去掉 R1C8F_C2 后 0.894"不是同一件事：那是把整条约束从分母里拿掉，
这里是把 229 条改成规则表说的 SUPPORTED——而第一轮在这一行写的正是 SUPPORTED，所以两轮由此对上。
论文要报的是后者。）

分层也跑出来了，与评估方案 §1.2 d 的形状一致：按单元类型 repos 0.974 / pods 0.935 / deps 0.730，
按站点类别 RRA 0.935 / ALT 0.780（都是 `is_api_use` 的 Po）。YES→NO 的 311 条按第二轮的排除代码分：
NAME_COLLISION 95、COMPILE_GUARD_EXCLUDES_IOS_RELEASE 77、STRING_LITERAL 47、DECLARATION 42、
OTHER 32、WRAPPER_CALL 16、WRAPPER_TYPE_MEMBER 2。

### 3.4 第三遍抽样（seed 20260919，n=450，每单元下限 2）

样本 A 450 个站点，覆盖 171 个批次；样本 B 210 个站点（ALT_IN_USE 103、OFF_DEVICE_FATE 58、
NON_DOMAIN_CONFLICT 50、NEEDS_CONTEXT 1），与 A 重叠 32 个。

**一个打包上的提醒**：按单元分层会把 450 个站点摊到 171 个批次（每批约 2.6 个站点），
`make_packs.py` 默认 `--max-batches 10` 会切成约 18 个包 = 18 个对话。这些子集批次很小，
打包时把 `--max-batches` 调大（如 60）就是 3 个包。抽样口径不因此改动——按单元分层是 κ 有总体解释的前提。

## 4. 两条与本次改动无关、但查到要报的事

1. **`test_make_batches.py` 在合并树上不过**（`a0001__deps__newpkg` 那条断言拿到空批次列表）。原因是
   `gt_rework_v2.tgz` 里的 `make_batches.py`（md5 `e3d428cc…`）与 `gt_scan_v3_5e.tgz` 里的测试是两个版本；
   用 `gt_scan_v3_5e` 自带的 `make_batches.py`（md5 `0b39b3df…`）跑同一个测试是 PASS。`make_batches.py`
   本次一个字节都没改。服务器上部署的是哪一份，要核一下。
2. **本地 `batches_all/` 不是 R2 用的那份批次**：4,494 个站点（覆盖 R2 的 4,474/4,538），旧格式，
   没有站点级 `target`、`key`、`build_flags`，头里没有 `build_facts`。所有脚本都以 `--batches` 为准，
   服务器上必须用 `annot_r2/materials/all_batches.zip` 解开的 183 批；旧格式只走 `hosts[*].app_facts` 回退路径。

## 5. 在服务器上怎么跑（与计划 §3 一致，命令按本次实现校正）

```bash
cd ~/autodl-tmp/gt_driver
B=~/autodl-tmp/annot_r2/materials/all_batches     # 183 批，带 build_facts / target / key
R=report; mkdir -p $R

# 1 规则表校验（R1 需要 --site-index：它的批次划分与 R2 不同）
python3 -u tools/annotate_validate.py --batches $B --site-index --rule-table 1.10 \
    --flow-rules off --quoted-evidence off --json $R/validate_r1_rt110.json \
    annot_r1/out/*.out.jsonl > $R/validate_r1_rt110.log 2>&1
python3 -u tools/annotate_validate.py --batches $B --rule-table 1.11 \
    --json $R/validate_r2_rt111.json annot_r2/merged/out/*.out.jsonl > $R/validate_r2_rt111.log 2>&1

# 2 规则性修正：先 dry-run 看清单，再落盘
python3 -u tools/apply_rule_fixes.py --batches $B --in annot_r2/merged/out --dry-run \
    --json $R/rule_fixes_dryrun.json > $R/rule_fixes_dryrun.log 2>&1
python3 -u tools/apply_rule_fixes.py --batches $B --in annot_r2/merged/out \
    --out-dir annot_r2/merged_1_11/out --json $R/rule_fixes.json > $R/rule_fixes.log 2>&1
python3 -u tools/annotate_validate.py --batches $B --rule-table 1.11 \
    --json $R/validate_r2_1_11.json annot_r2/merged_1_11/out/*.out.jsonl > $R/validate_r2_1_11.log 2>&1
#   期望：0 错

# 3 一致性：修正前后各一份
python3 -u tools/agreement.py --pass R1=annot_r1/out --pass R2=annot_r2/merged/out --batches $B \
    --boot 2000 --diff-dir $R/diff_pre --json $R/agreement_R1_R2_pre.json --md $R/agreement_R1_R2_pre.md \
    > $R/agreement_pre.log 2>&1
python3 -u tools/agreement.py --pass R1=annot_r1/out --pass R2=annot_r2/merged_1_11/out --batches $B \
    --boot 2000 --diff-dir $R/diff_post --json $R/agreement_R1_R2_post.json --md $R/agreement_R1_R2_post.md \
    > $R/agreement_post.log 2>&1

# 4 第三遍：抽样 → 切批次 → 打包（子集批次很小，--max-batches 调大）
python3 -u tools/sample_third_pass.py --batches $B --pass R2=annot_r2/merged_1_11/out \
    --n 450 --min-per-unit 2 --seed 20260919 --out-dir $R --json $R/r3_sample.json
python3 -u tools/subset_batches.py --batches $B --site-ids $R/r3_sites_A.txt \
    --out annot_r3/batches --json $R/r3_subset.json
python3 -u tools/make_packs.py --batches annot_r3/batches --src src --out annot_r3/packs --max-batches 60

# 5 收回第三遍后：校验 → 一致性 → 冻结
python3 -u tools/annotate_validate.py --batches annot_r3/batches --rule-table 1.11 \
    --json $R/validate_r3.json annot_r3/out/*.out.jsonl > $R/validate_r3.log 2>&1
python3 -u tools/agreement.py --pass R2=annot_r2/merged_1_11/out --pass R3=annot_r3/out \
    --batches $B --restrict $R/r3_sites_A.txt --boot 2000 \
    --json $R/agreement_R2_R3.json --md $R/agreement_R2_R3.md > $R/agreement_r3.log 2>&1
python3 -u tools/freeze_gt.py --in annot_r2/merged_1_11/out --batches $B --out gt_v1 \
    --protocol-version 1.11 --report $R --r3 annot_r3/out --r1 annot_r1/out \
    --agreement-r3 $R/agreement_R2_R3.json --principles annot/ANNOTATION_PRINCIPLES.md \
    --protocols annot/versions --prompts annot/prompts --tools tools \
    --candidates PRINCIPLES_1.12_CANDIDATES.md --unit-bundle-map $R/unit_bundles.json
```

每一步的数字与 §3 不符时，不改预期去凑，写进 `report/EVAL_NOTES.md` 再查工具。

## 6. 测试

```bash
export RRA_RULES=~/autodl-tmp/cross_rra_analyzer_0.123.83/rra_rules.yaml
export AGREEMENT_R1=…/r1_all.jsonl AGREEMENT_R2=…/r2_all.jsonl   # 可选：开启语料回归
for t in tools/tests/test_*.py; do python3 "$t" || echo "FAIL $t"; done
```

本次 5 个新测试全绿；既有测试里只有 `test_make_batches.py` 不过，原因见 §4 第 1 条（与本次改动无关）。

## 7. 验收记录（Fable 5.1，对上面 Opus 5 的实现做独立复核）

**复算的事实**（不看日志，从 JSON 与文件重算）：`FIX_C2_STANDARD` 翻转的 229 个 (站点, 约束) 与校验器在 1.11 下报的 229 条违规**集合相等**；修正前后 4,538 条记录逐条 diff，只有 229 条变化，变化只在 `constraint_verdicts.R1C8F_C2` 与 `notes`（追加段），其余字段字节不动；对修正后的输出再跑 dry‑run，两条修正 FLIPPED 均为 0（实数据幂等）；`freeze_gt.py` 在 4,538 条 / 183 文件上走通写入路径，`FREEZE.json` 里 183 个冻结文件的 sha256 与磁盘一致，裁决计数自洽（SUPPORTED 3,362 = 3,133 + 229；UNKNOWN 624 = 853 − 229）；用本地旧格式批次冻结时被"站点数与 MANIFEST 一致"判据**正确拒绝**（4,494 ≠ 4,538），这正是服务器上必须用 183 批的原因。

**静态审查找到并修掉的 5 处缺陷**（每处都先用最小输入复现，再改，再加测试钉住）：

| # | 位置 | 缺陷 | 影响方向 | 修法 |
|---|---|---|---|---|
| 1 | `rule_table.combine_signals` | 批次读数是 SUITE、标注文字引用了 `UserDefaults(suiteName: suite)`（不带字面量）时被当成矛盾，打成 UNKNOWN | 少判（服务器上 `SUITE:<v>` 预填的站点会全部不表态） | 不带字面量的 `suiteName:` 只与 STANDARD 矛盾，对 SUITE 是重述 |
| 2 | `rule_table.app_groups_of` | 站点的 target 写了 `app_groups: []`（明确不加入任何 group）时被当成"未知"，取了全部 target 的并集 | **误判**（可能把不在 group 里的 target 判成成员） | `"app_groups" in target` 即采信，空表就是空 |
| 3 | `rule_table._suite_in_groups` | 前缀匹配双向：`group.org` 会被判成 `group.org.demo.app` 的成员 | **误判** | 只保留占位符后缀方向（suite 以 entitlement 值开头且更长） |
| 4 | `sample_third_pass.sample_b` | 不在批次索引里的站点拿不到谓词，任何 CONFLICT 都会被记成 NON_DOMAIN_CONFLICT | 样本 B 混入域约束站点 | 这类站点单列 `not_classifiable_no_batch`，不分类 |
| 5 | `apply_rule_fixes._propose_c2_standard` | 参与者行遇到按域分叉的字典值会逐分支改写（校验器本不允许这种形状） | 改到畸形输入里 | 记 NOT_APPLICABLE 并列出，不改 |

另一处收紧：`.global` 只在不是函数调用时算 `NSGlobalDomain` 类信号（`DispatchQueue.global()` 在 notes 里太常见）；它本来就只有否决权，影响只是少一些不必要的弃权。

**修完后重跑**：R2/1.11 违规 229、修正后 0、五种结局计数与修正前一致、修正输出与第一次逐字节相同、样本 A 同 seed 同清单；9 个测试脚本全绿（`test_make_batches.py` 的失败与本次无关，见 §4）。

**留意但未改的两点**：（a）`evidence_for_domain_label("APP_GROUP")` 把标注方按域分叉时写的 `APP_GROUP` 标签当作"已按当时规则认定为 App Group"，校验器对这一分支不做二次判断——标签本身就是分类结论，无从核对；4 个 MIXED 站点受此影响。（b）`FREEZE.json` 里 R1 的文件指纹按 `*.jsonl` 取，会把 `PROGRESS.jsonl` 一并算进去（189 = 188 + 1），是记录粒度问题，不是错。

## 8. v1.2：服务器（183 批）首跑暴露的两处解析缺陷

本地旧格式批次没有 `target.kind` 和 `callers`，这两条路径只有单测覆盖；在 183 批上跑出来的
336 条域违规里有 12 条、FIX_1_11_DOMAIN 的 6 条翻转，全部落在同一处：wikipedia-ios `MWKDataStore.m`
的 group suite 站点（`group.org.wikimedia.wikipedia`，与 entitlement 完全一致），却判成"不在 entitlement 里"。

1. **entitlement 要看宿主进程，不看代码所在的 target。** 这些站点编进 WMF **framework**，其 target 记录
   写着 `app_groups: []`——那是签名事实（framework 不带 entitlements），不是可达性事实。v1.1 §7 第 2 条
   "空表就是空"对 APP/APP_EXTENSION 是对的，对 framework/SPM/pod 是错的。现在：站点在 APP / APP_EXTENSION
   target 里用该 target（∪ `also_in` 的宿主）的 groups；否则取批次里全部宿主 target 的并集；没有 target 同前。
2. **`callers.by_domain` 里只要有一个调用方没分类就不表态。** 之前把 UNKNOWN 键剔掉后取剩下那一个，
   一个传 group 实例的未分类调用方会藏在传 `.standard` 的后面。现在 UNKNOWN / 未解析 SUITE_CONST 任一
   出现 → 无读数；`n=0` 同。

两处都有测试钉住（wikipedia 形状、有 UNKNOWN 调用方的形状）。本地全量数字不变（229 / 0 / 五种结局同），
修正输出与 v1.1 逐字节相同。服务器上预期：R2/1.11 的域违规回到 ≈ 324 + 0（那 12 条消失），
FIX_1_11_DOMAIN 翻转回到 0，FIX_C2_STANDARD 的 324 里经 `callers` 判出的 95 条可能减少（有 UNKNOWN
调用方的那些改为不表态）——减多少以 dry-run 为准。

另记：R1 审计里 7 条 wBlock `RCA92_C1` 违规，R1 的 notes 写的是 "scanner domain_hint=APP_PRIVATE overridden
to APP_GROUP per GroupIdentifier.swift L46"——标注是对的、也说了理由，只是没用机器可核验的写法
（既无 `group.<id>` 字面量也无 `DOMAIN_OVERRIDE:`）。R1 只做审计不进真值，不处理；R3 的提示词里要把
`DOMAIN_OVERRIDE:` 写进"推翻预填必须这样写"那一条。
