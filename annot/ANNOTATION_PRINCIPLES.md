# RRA 与替代 API 源码调用点标注原则

版本 1.9 · 词表来源 `cross_rra_analyzer 0.123.81 / rra_rules.yaml (schema 5)` · 适用对象:执行标注的模型或人

---

## 0. 你的角色

你在为一篇 iOS 隐私合规的实证研究生成**真值**。对象是 46 个开源 iOS 应用(钉死 commit)及其全部依赖源码中,每一处 Apple「需申报理由 API」(Required Reason API,RRA)的调用点。你的标注将被用来给一个静态分析器打分,并直接支撑四个研究问题:

| RQ | 问题 | 依赖你标的字段 |
|---|---|---|
| RQ1 | 用了 RRA 却没声明的情况,按类别 × App/SDK 怎么分布 | `is_api_use`, `unit_role`, `declaring_unit` |
| RQ2 | Apple 清单之外的替代 API 用得多不多、有没有声明、是否越界 | `site_class=ALT` 站点的 `is_api_use`, `declared_for_mapped_category`, `value_fate`, `exceeds_all_reasons` |
| RQ3 | 已声明的用法,实际行为是否满足获批理由的约束 | `value_fate`, `constraint_verdicts` |
| RQ4 | RRA 数据跨单元传播后,接收方是否违反源头理由 | 第二轮流层字段 |

**你不是在找问题,你是在记录事实。** 一个站点"没有问题"和"有问题"是同等重要的结果。

代码归属(某个调用点属于哪个代码单元)不是研究问题,但四个 RQ 都建立在它上面,所以每个站点也要标归属字段(§6)。

### 0.1 站点分三类,前两类要标,第三类不标

| `site_class` | 是什么 | 怎么标 |
|---|---|---|
| `RRA` | Apple 清单上的 API(附录 A 五类) | 全部字段 |
| `ALT` | 清单之外、但**返回同一数据或其近似派生值**的 API(附录 C) | 全部字段,外加 §4.7;`applicable_reason` / `constraint_verdicts` 记 NA |
| (不生成站点) | 只是**换一种方式完成同一任务、不暴露该数据**的 API(附录 D):`Timer`、`fileExists`、`PropertyListEncoder`、Keychain… | 不标。它们不携带隐私相关数据,标了只会把 RQ2 的分母灌水 |

`ALT` 站点**永远不能**算进 RRA 的计数——它不受申报要求约束,"没声明"对它不是缺失。工作表里 `site_class` 已由脚本按 API 名分好,你只需确认 `is_api_use`。两类站点的 API 名单互斥(附录 A 与附录 C 没有交集),一个标识只可能属于其中一类;若你认为某个 `RRA` 站点其实是 ALT(或反之),**不要改 `site_class`**,在 `notes` 写明,由规则维护方处理。

---

### 0.2 计数单位

你标的是**站点**:源码里对所列 API 标识的一次出现(一行两次出现算两个)。SDWebImage 在一个函数里对 `NSURLContentModificationDateKey` 有四次出现(键请求一次、取值一次、排序比较器里两次),就是四个站点。RQ 报数时需要的"某单元使用了某类别"(单元 × 类别)由工具从站点聚合,你不用管——所以不要为了"别重复"而漏标。

## 1. 五条铁律

1. **只写你在给定材料里能看到的。** 材料之外的东西一律 `UNSURE` / `UNKNOWN`,并在 `needs_context` 里写明你缺什么。猜一个答案比留空更糟,因为猜的和看到的在数据里长得一样。**Apple 与 Swift 标准库 API 的公开语义算"看得到"**:`systemUptime` 返回什么、`ContinuousClock.Instant` 打印出来是什么(没有自定义 description 的标准库 struct 按默认反射输出,含全部存储属性)、`URLSession` 把数据发到哪——这些是公开规范,直接用;"看不到"只指**本仓库代码**里不在批次内的部分。批次自带一个**源码补充目录**(批次头 `source_dir`,文件清单在 `source_files` / `unit_files`,各带 sha1):站点所在的完整源文件、被调用的扩展成员定义所在文件、实例构造处所在文件、键常量所在文件,以及单元的清单 / `Package.swift` / podspec / `project.pbxproj`。**这些文件里的内容也算看得到**,引用时写 `<文件路径> L<n>: <片段>`(站点自己文件的行仍写 `L<n>`)。清单之外的文件不存在——不要凭记忆引用仓库里的其他文件。
2. **每个站点必须有且只有一条输出,`site_id` 原样回传。** 不许合并、不许跳过、不许新造 `site_id`。输入 N 条,输出 N 条。
3. **每个判断附证据行。** `fate_evidence` 里写 `L<行号>: <那一行的关键片段>`。没有证据行的 `SUPPORTED` / `CONFLICT` 视为无效,会被校验脚本打回。
4. **词表之外的值不许出现。** 所有枚举字段只能取附录 A 列出的值,大小写一致。需要表达词表没有的情况,写进 `notes`,枚举字段填 `UNSURE` / `UNKNOWN`。
5. **`SUPPORTED` 要正面证据,`CONFLICT` 要明确反证,两者都没有就是 `UNKNOWN`。** "没看见坏事"不等于"没有坏事"——尤其当值离开了你能看到的范围。

---

## 2. 输入:工作表记录(每站点一行 JSON)

由 `tools/scan_source_rra.py` 枚举、`tools/make_batches.py` 切成批次。下面是一条真实记录的形状(wikipedia-ios `WMFUserDefaultsStore.swift:18`,注入实例经别名写入);单元级的信息(清单、宿主、理由约束)在批次头里,见后:

```json
{
  "site_id": "a3f9…",                      // 原样回传
  "repo": "wikimedia/wikipedia-ios",        // 站点在哪个基准仓库的树里;deps/ pods/ 下的单元为 null,看 hosts
  "sha": "2f1c…",                           // 仓库 commit;deps/pods 为锁定的 revision / 版本
  "unit": "WMFData/WMFData",                // 代码单元:APP | <本地包>/<target> | POD:<名> | <包 identity>/<target> | <pod 名>
  "unit_kind": "LOCAL_PKG",                 // APP | LOCAL_PKG | LOCAL_POD | SPM | POD
  "unit_role_prefill": "FIRST_PARTY",       // 脚本按位置预填:FIRST_PARTY | THIRD_PARTY | VENDORED_THIRD_PARTY? | FORK_OF_THIRD_PARTY
  "file": "WMFData/Sources/WMFData/Store/WMFUserDefaultsStore.swift", "line": 18, "col": 12,
  "language": "swift",                      // swift | objc | c
  "site_class": "RRA",                      // RRA | ALT
  "api": "user_defaults_family",            // 规则文件里的 API 键;ALT 站点用附录 C 的键
  "category": "UserDefaults",               // 主类别;ALT:它替代的 RRA 类别
  "categories": ["UserDefaults"],           // getattrlist 族同时属于 FileTimestamp 与 DiskSpace,这里给全
  "alt_tier": null,                         // ALT 站点:NEAR_EQUIVALENT | CONDITIONAL | PARTIAL_DATUM
  "declared_for_mapped_category": null,     // ALT 站点:该单元清单是否声明了被替代的那个 RRA 类别
  "tier": "STRONG",                         // STRONG 标识唯一;WEAK 是可能撞名的裸成员
  "tag": "defaults.set",                    // 命中的字面标识(别名站点为 <别名>.<成员>,扩展体为 self.<成员>)
  "hint": "ALIAS_MEMBER_CALL",              // 脚本对站点形态的判断,见下表;只是提示,不是结论
  "operation_prefill": "WRITE",             // 脚本按成员名预填的 operation;`WRAPPED?` 表示别名可能根本不是 defaults
  "domain_hint": "MIXED_DOMAINS:APP_GROUP|APP_PRIVATE",   // 实例的域(见 §4.2);SUITE_CONST:… 表示常量未能解析
  "compile_guard": null,                    // 包住站点的条件编译链,如 ["#if os(Linux)", "#else"] 表示站点在 Linux 分支的 else 里
  "guard_live_on_ios": true,                // 脚本按 Release/真机 SDK 判的活性:true | false | null(判不出)
  "in_string_literal": false,
  "enclosing_function": "private func save<T: Codable>(defaultsKey: String, value: T) throws {",
  "alias_of": { "alias": "defaults", "decl_line": 5, "decl": "private let defaults: UserDefaults",
                "form": "INJECTED_PROPERTY", "owner_type": "WMFUserDefaultsStore", "domain_hint": "MIXED_DOMAINS:APP_GROUP|APP_PRIVATE" },
  "instance_domains": [                     // 该实例的全部构造处及各自的域(脚本在整个单元里搜 `WMFUserDefaultsStore(`)
    { "loc": "WMFData/Sources/WMFData/Environment/WMFDataEnvironment.swift:89", "domain": "APP_PRIVATE", "note": "default defaults = .standard", "form": "CONSTRUCTION_DEFAULT" },
    { "loc": "WMFData/Sources/WMFData/Environment/WMFDataEnvironment.swift:99", "domain": "APP_GROUP",   "note": "arg defaults: defaults <- L96 UserDefaults(suiteName: \"group.org.wikimedia.wikipedia\")", "form": "CONSTRUCTION" }
  ],
  "key": { "expr": "defaultsKey", "value": null, "source": "UNRESOLVED_IDENTIFIER" },   // forKey: 实参;value 是解析出的字符串(字面量或可见常量),source 记来源(LITERAL | CONST:<名> @ 文件:行 | UNRESOLVED_IDENTIFIER | EXPRESSION)
  "wrapper_ref": null,                      // WRAPPED 站点:所调扩展成员的定义(见下),不是 WRAPPED 为 null
  "callers": null,                          // 扩展体站点:调用该成员的全部 WRAPPED 站点及各自的域(见下)
  "target": { "project": "Wikipedia.xcodeproj", "name": "WMF", "kind": "FRAMEWORK", "bundle_id": "org.wikimedia.WMF",
              "manifests": ["Wikipedia/Resources/PrivacyInfo.xcprivacy"], "entitlements": null, "app_groups": [], "also_in": [] },   // 工程文件里这个文件编进哪个 target(app 仓库);SPM/pod 为 {"package"/"podspec", "name", "kind"};不在任何 target 里时 name 为 null 且带 note
  "declaring_unit_prefill": "WMF",          // 按 target 预填的 declaring_unit(§6):target 名 / SPM target 名 / pod 名
  "build_flags": { "source": "pbxproj:Release", "swift_true": ["NDEBUG"], "closed": true },   // 该文件在基准构建(Release)下为真的条件编译标志;closed=true 表示标志集完整,不在其中的自定义标志为假
  "context": { "start_line": 115, "site_line": 122, "function_lines": [115, 129], "truncated": false,
               "lines": ["  115   func endPageLoadStartTime() {", "…", "  122>> let milliseconds = …", "…"] },   // 包住站点的整个函数(签名行必在;站点前 12 行、后 36 行之外截断,截断处有 "…" 标记);顶层站点为前后各 8 行;每行前缀是行号,站点行标 ">>"
  "unit_manifest": "Wikipedia/Resources/PrivacyInfo.xcprivacy",   // 覆盖这个文件的清单;没有则 null
  "manifest_scope": "APP_LEVEL_FALLBACK_LOCAL_PKG",   // 清单是怎么找到的,见下表
  "declared_reasons": { "UserDefaults": ["1C8F.1"] },   // 覆盖清单里各类别声明的理由码;未声明 [];ALT 站点为 null
  "target_hint": null                       // 路径像 extension / watch / widget 目标时给 "PATH_SUGGESTS_OTHER_TARGET:<目录>";有 target 字段时以 target 为准
}
```

`wrapper_ref`(WRAPPED 站点)和 `callers`(扩展体站点)是同一条链接的两端,由脚本在整个单元目录里算出:

```json
"wrapper_ref": [ { "member": "defaultTabType", "kind": "VAR", "access": "SET",          // access: GET | SET(扩展属性被赋值)| CALL(扩展方法)
                   "def_file": "Wikipedia/Code/NSUserDefaults+WMFExtensions.swift", "def_line": 353, "def_end_line": 368,
                   "has_setter": true,
                   "keys": [ { "expr": "UserDefaults.Key.defaultTabType", "value": "WMFDefaultTabTypeKey", "source": "CONST:defaultTabType @ …:50", "line": 355 } ],   // 成员体内读写的键
                   "calls": [] } ]                                                      // 成员体内又调了哪些别的扩展成员
"callers": { "member": "wmf_migrateFontSizeMultiplier", "n": 1, "by_domain": { "APP_PRIVATE": 1 }, "via_members": [],   // via_members:经别的扩展成员间接到达
             "sites": [ { "site_id": "b1e5…", "file": "Wikipedia/Code/AppDelegate.swift", "line": 34, "access": "CALL", "domain": "APP_PRIVATE" } ],
             "scope": "all scanned files of this unit directory" }
"member": { "name": "wmf_migrateFontSizeMultiplier", "kind": "FUNC", "public": true, "has_setter": false }   // 扩展体站点所在的成员
```

`wrapper_ref` 为 `null` 且有 `wrapper_note` 的 WRAPPED 站点:单元目录里没有这个名字的扩展成员(定义在依赖里,或该文件根本不在任何 target 里)。`callers.n` 为 0:扫描范围内没有任何调用方。

你拿到的是**批次文件**(`b0007__repos__wikimedia-wikipedia-ios.jsonl`):第一行是批次头,之后每行一个站点。一个批次只含一个单元、至多 40 个站点、按文件和行号排好序。批次头长这样:

```json
{
  "_batch": "b0007__repos__wikimedia-wikipedia-ios.jsonl", "n_sites": 40,
  "site_ids": ["a3f9…", "…"],                 // 你要输出的 site_id,按这个顺序,一个不多一个不少
  "unit_location": "repos/wikimedia-wikipedia-ios", "unit_kind_default": "APP",
  "repo": "wikimedia/wikipedia-ios", "sha": "2f1c…",
  "manifests": ["Wikipedia/Resources/PrivacyInfo.xcprivacy"],     // 单元目录里的全部清单
  "declares": { "UserDefaults": ["1C8F.1"], "DiskSpace": ["7D9E.1"] },   // 全部清单声明的并集;站点自己的覆盖清单看站点的 declared_reasons
  "hosts": [                                  // 链接了该单元的基准仓库(repos/ 下就是它自己),各自 App 级清单声明与应用级事实
    { "repo": "wikimedia/wikipedia-ios", "declares": { "UserDefaults": ["1C8F.1"] },
      "app_facts": { "app_group_ids": ["group.org.wikimedia.wikipedia"], "has_keyboard_extension": false,
                     "extension_points": ["com.apple.widgetkit-extension"], "entitlement_files": 3 } }
  ],
  "unit_macros": {},                          // 脚本从单元头文件解出的 0/1 宏(SDWebImage 的 SD_UIKIT 等)
  "wrapper_methods": {},                      // 闭包参数为 UserDefaults 的封装方法(RevenueCat 的 read/write)
  "build_facts": {                            // 工程文件事实(tools/xcodeproj_facts.py):基准构建 = Release 配置、真机 SDK
    "primary_app_target": ["Wikipedia.xcodeproj", "Wikipedia"],
    "projects": [ { "path": "Wikipedia.xcodeproj", "release_config": "Release",
                    "targets": [ { "name": "Wikipedia", "kind": "APP", "bundle_id": "org.wikimedia.wikipedia", "manifests": ["Wikipedia/Resources/PrivacyInfo.xcprivacy"],
                                   "entitlements": "Wikipedia/Wikipedia.entitlements", "app_groups": ["group.org.wikimedia.wikipedia", "…"],
                                   "release_swift_flags": ["NDEBUG"], "flags_complete": true }, { "name": "WidgetsExtension", "kind": "APP_EXTENSION", "…": "…" } ] } ],
    "packages": {}, "podspec": null, "notes": [] },
  "ud_members": ["wmf_isImageDimmingEnabled", "…"],   // 本单元目录里 UserDefaults 扩展/分类成员的名字
  "source_dir": "src/repos/wikimedia-wikipedia-ios",   // 源码补充目录(相对批次包根)
  "source_files": [ { "path": "Wikipedia/Code/AppDelegate.swift", "sha1": "c905445e5ebb", "n_lines": 261 } ],   // 本批次引用到的文件
  "unit_files": [ { "path": "Wikipedia/Resources/PrivacyInfo.xcprivacy", "sha1": "…", "n_lines": 20 }, { "path": "Wikipedia.xcodeproj/project.pbxproj", "…": "…" } ],
  "reasons": {                                // 本单元出现过的每个理由码的约束,判 constraint_verdicts 时按站点的 declared_reasons 来这里查
    "1C8F.1": { "category": "UserDefaults", "title": "User Defaults Shared Within the Same App Group",
                "restrictions": ["Do not intentionally read information written by members outside the App Group or by the system.", "…"],
                "constraints": [ { "id": "R1C8F_C1", "type": "REQUIRED", "predicate": "DefaultsDomainIs(APP_GROUP)" },
                                 { "id": "R1C8F_C2", "type": "REQUIRED", "predicate": "AllIntendedParticipantsAreMembersOfSameAppGroup()" },
                                 { "id": "R1C8F_C3", "type": "FORBIDDEN", "predicate": "NoReadFrom(OUTSIDE_APP_GROUP | SYSTEM)" } ] }
  }
}
```

`constraint_verdicts` 的键就是 `reasons[理由码].constraints[*].id`。规则文件里每条约束还带三段 evidence 说明文字(`support_evidence` / `conflict_evidence` / `unknown_evidence`),都是"有充分静态证据支持 / 有明确静态证据反驳 / 证据不足"的模板句,没有放进批次;判法以 §4.5 为准。

```

`context.lines` 是**原文**(注释和字符串都在);匹配是在剥掉注释后做的,所以注释里的 API 名不会成为站点,但字符串里的会——`in_string_literal` 为 true 的站点判 `NO`。

**`hint` 的取值**(只对 UserDefaults 家族站点给;其他类别为 null):

| `hint` | 意思 | 通常的 `operation` |
|---|---|---|
| `FAMILY_MEMBER:<成员>` | `UserDefaults.standard.<家族成员>(…)` / `[[NSUserDefaults standardUserDefaults] <成员>:…]` | 按成员:READ / WRITE / REMOVE / OBSERVE / SYNC |
| `POSSIBLE_WRAPPED_MEMBER:<成员>` | `.standard` 后面跟的不是家族成员(`wmf_isImageDimming`) | WRAPPED;定义、getter/setter、体内的键在 `wrapper_ref` 里 |
| `ALIAS_MEMBER_CALL` | 经别名(局部变量 / 存储属性 / 注入)调用,`alias_of` 指回声明行 | 按成员;`WRAPPED?` 要先确认别名确实是 defaults |
| `EXTENSION_BODY_IMPLICIT_SELF` | `extension UserDefaults { … bool(forKey:) … }` 体内的隐式 self 调用 | 按成员;域由 `callers.by_domain` 决定(§4.2) |
| `ACQUIRE_CANDIDATE` | 取得实例、当行无读写(`let ud = UserDefaults.standard`、`UserDefaults(suiteName:)`、把 `.standard` 作为参数传出) | ACQUIRE |
| `INJECTION_PARAMETER` | `init(defaults: UserDefaults = .standard)` 这类参数声明行 | NA(构造处才是 ACQUIRE) |
| `TYPE_ANNOTATION_OR_PROPERTY_DECL` | `private let defaults: UserDefaults`、`NSUserDefaults *ud`、`as? UserDefaults` | NA |
| `TYPE_EXTENSION_DECLARATION` | `extension UserDefaults {` / `@implementation NSUserDefaults (WMF)` 声明行 | NA |
| `NESTED_TYPE_REFERENCE:<名>` | `UserDefaults.Key.x`——引用扩展里定义的嵌套类型 | NA |
| `FAMILY_STATIC:<名>` | `UserDefaults.didChangeNotification` 等类型级成员 | OBSERVE / READ |
| `APPSTORAGE_PROPERTY_WRAPPER` | `@AppStorage("k")` 声明 | OBSERVE |
| null | 脚本判不出形态 | 自己看 |

**`manifest_scope` 的取值**:`TARGET_RESOURCE`(工程文件里该文件所属 target 的 Resources 阶段带的清单——最准)| `NEAREST_ANCESTOR`(文件所在目录向上最近的清单,SwiftPM 按 target 放清单时就是它)| `APP_LEVEL` / `APP_LEVEL_UNION`(App 级清单,多个则并集)| `APP_LEVEL_FALLBACK_LOCAL_PKG`(仓库内本地包自己没有清单,按 App 包覆盖)| 后缀 `_FALLBACK_EXTENSION_TARGET`(文件编进一个没有自己清单的 extension target,暂按 App 清单覆盖——`declaring_unit` 仍是该 extension)| `NO_COVERING_MANIFEST_IN_<单元类型>`(单元里有清单但都不覆盖这个文件,如 swift-nio 的 CNIOLinux)| `NO_MANIFEST_IN_<单元类型>`(单元里一个清单都没有)。后两种的 `declared_reasons` 全空——这就是 RQ1 的"未声明",但 `declaring_unit`(§6)仍要你判:代码最终进哪个 bundle,清单就该在哪。

**`guard_live_on_ios` 的口径**:基准构建 = Release 配置、真机 SDK(驱动仓库 `build.yml` 用 `xcodebuild -configuration Release -destination generic/platform=iOS` 构建)。`DEBUG` 判假。工程自定义标志**不靠约定,靠工程文件**:脚本从 `project.pbxproj` / xcconfig(app 仓库)、`Package.swift` 的 `.define`(SwiftPM)、podspec 的 xcconfig(pod)读出该文件所属 target 在 Release 下定义的标志,写在站点的 `build_flags`;`closed: true` 表示这个集合是完整的,于是 Swift 文件里不在集合内的自定义标志判假(wikipedia-ios 的 `TEST` 只在 Test 配置定义,`#if TEST` 的 `#else` 分支在 Release 下为活)。C/ObjC 的宏还可能来自看不到的头文件,不做封闭假设。SwiftPM 一律定义 `SWIFT_PACKAGE`,CocoaPods 一律定义 `COCOAPODS`。单元头文件里用可判条件定义的 0/1 宏(SDWebImage 的 `SD_UIKIT`)在 `unit_macros`。仍为 `null` 的站点才需要你判;判不了就 UNSURE。

---

## 3. 输出:标注记录(每站点一行 JSON)

```json
{
  "site_id": "a3f9…",
  "site_class": "RRA",                      // 原样回传
  "is_api_use": "YES",                      // YES | NO | UNSURE —— 这真的是所列 API 的使用吗
  "is_api_use_reason": "ProcessInfo.processInfo.systemUptime,Foundation 类型,无平台守卫",
  "unit_confirmed": "YES",                  // YES | NO | UNSURE;NO 时在 notes 写正确单元
  "unit_role": "THIRD_PARTY",               // FIRST_PARTY | THIRD_PARTY | VENDORED_THIRD_PARTY | FORK_OF_THIRD_PARTY | UNSURE
  "declaring_unit": "alamofire",            // 该站点应由哪个单元的清单覆盖,默认照抄 unit
  "operation": "READ",                      // READ | WRITE | REMOVE | OBSERVE | SYNC | WRAPPED | ACQUIRE | NA
  "value_fate": ["LOCAL_ONLY"],             // 多选,见 §4.3
  "fate_evidence": "L248: let start = ProcessInfo.processInfo.systemUptime; L258: elapsed = … - start",
  "escape": null,                           // 值离开函数时填:{"kind": "RETURNED|PASSED_OUT|PERSISTED_LOCAL|STORED", "value": "RAW|DERIVED", "target": "…"}
  "applicable_reason": "35F9.1",            // 理由码 | NONE(未声明) | UNSURE(多条声明分不清)
  "constraint_verdicts": { "R35F9_C1": "SUPPORTED", "R35F9_C2": "SUPPORTED",
                           "R35F9_C3": "UNKNOWN", "R35F9_C4": "SUPPORTED" },
  "alt_equivalence": null,                  // ALT 站点必填:NEAR_EQUIVALENT | CONDITIONAL | PARTIAL_DATUM
  "exceeds_all_reasons": null,              // ALT 站点必填:YES | NO | UNKNOWN,见 §4.7
  "needs_context": null,                    // 或 {"what": "…", "why": "…", "requests": [{"kind": "DEFINITION", "symbol": "NIODeadline", "file": "Sources/NIOCore/EventLoop.swift"}]},见 §4.6
  "notes": ""
}
```

`constraint_verdicts` 的键必须与批次头 `reasons[applicable_reason].constraints[*].id` 一一对应,一个不能少。值通常是一个字符串;**只有**在 `instance_domains` 给出多个域时(§4.2 的 MIXED_DOMAINS),`DefaultsDomainIs` 类约束的值才是按域分开的字典 `{"APP_PRIVATE": "CONFLICT", "APP_GROUP": "SUPPORTED"}`。`applicable_reason` 为 `NONE` 时 `constraint_verdicts` 为 `{}`。`site_class` 为 `ALT` 时两者分别为 `"NA"` 和 `{}`。

---

## 4. 逐字段判定规则

### 4.1 `is_api_use` —— 这真的是所列的那个 API 吗

判 **YES** 需同时满足:
- 标识确实指向 Apple 的那个 API:`FileAttributeKey.creationDate` / `URLResourceKey.…Key` / `NSFile…` 全局 / `ProcessInfo…systemUptime` / `mach_absolute_time(` / `statfs(` 等 C 函数 / `UITextInputMode.activeInputModes` / `UserDefaults` `NSUserDefaults` `@AppStorage` 的任何成员访问。
- 所在的条件编译分支在 **iOS Release** 下是活的。工作表的 `compile_guard` 给出包住站点的条件链;判活规则:`os(iOS)` / `canImport(Darwin)` / `canImport(UIKit)` 为真;`os(Linux)` / `os(macOS)` / `os(watchOS)` / `os(tvOS)` / `canImport(Glibc|Musl|Android)` 为假,它们的 `#else` 分支为真;`DEBUG` 为假(基准二进制与 App Store 二进制都是 Release);自定义标志(swift-nio 的 `ENABLE_MOCKING`、wikipedia-ios 的 `TEST`)看站点的 `build_flags`——在 `swift_true` 里为真,`closed: true` 且不在里面为假;`closed: false` 又不在里面 → 判不出,UNSURE 并在 notes 写出条件。**不要用"自定义标志默认为假"这种约定代替工程事实。**死分支里的站点 `is_api_use` 填 NO,`is_api_use_reason` 写 `COMPILE_GUARD_EXCLUDES_IOS_RELEASE: <条件>`。

  完整的判假清单(凡不是 iOS / Darwin 的都为假):`os(Linux)` `os(macOS)` `os(watchOS)` `os(tvOS)` `os(visionOS)` `os(Windows)` `os(Android)` `os(WASI)` `os(FreeBSD)` `os(OpenBSD)`、`canImport(Glibc|Musl|Bionic|Android|WinSDK|WASILibc|AppKit|Cocoa|WatchKit)`、`targetEnvironment(simulator)` `targetEnvironment(macCatalyst)` `DEBUG`;它们的 `#else` 分支为真;`#elseif` 链逐条看,前面的都为假、这一条为真才活。工作表的 `guard_live_on_ios` 就是按这张表算出来的,你核对而不是重算;它为 `null` 的(自定义标志、`NDEBUG`、看不懂的表达式)才需要你判,判不了就 UNSURE。

  C / ObjC 的预处理宏同理:`__linux__` `__ANDROID__` `_WIN32` 为假;`__APPLE__` `TARGET_OS_IPHONE` `TARGET_OS_IOS` 为真;`TARGET_OS_OSX` 为假;`TARGET_OS_SIMULATOR` 为假(基准是真机 SDK)。**陷阱:`TARGET_OS_MAC` 在 iOS 上也是 1**(它表示"所有 Apple 平台"),不能当成 macOS 专属。swift-nio 的 `CNIOLinux/shim.c` 整个文件包在 `#ifdef __linux__` 里,其中的 `statfs(path, &fs)` 在 iOS 上不存在——这与它的清单不声明 DiskSpace 是一致的;判成 YES 就会制造一个假的"声明缺失"。守卫要看**整个包裹链**,不只是最近的一层。
- 不在字符串字面量里。

**把 API 绑定成函数值也是使用。** `private let sysStat = stat`、`let f = fstat`(hint `FUNCTION_REFERENCE`,tag `=stat`)——三个 YES 条件全部满足,六个 NO 代码无一适配,判 **YES**,`operation: READ`;真正发出调用的是经别名的 `sysStat(path, &info)`(那些行不是站点,在 `fate_evidence` 里引用它们),`value_fate` 追别名调用处的返回值。swift-nio 的 NIOPosix 声明了 0A2A.1,它全部的 `stat(2)` 调用都经这三个绑定发出;判 NO 会让这个单元在源码侧出现"零 RRA 使用"的假象。

**WEAK 档不是"多半不是",是"必须看接收者"。** 在 Swift 里 RRA 的惯用写法恰恰是裸成员:`attributes[.modificationDate]`(`attributes` 来自 `attributesOfItem(atPath:)` / `attributesOfFileSystem`)、`resourceValues.contentModificationDate`(来自 `url.resourceValues(forKeys:)`)、`setAttributes([.modificationDate: …])`。Cache 6.0.0 的 `DiskStorage.swift` 里 7 处 WEAK 命中全是真 RRA。看到这几种接收者直接 YES;看不到接收者来源才 UNSURE。

判 **NO** 的典型情形:
- WEAK 档撞名:`.creationDate` / `.modificationDate` 是自定义模型的属性(接收者是 Core Data 实体、自定义 struct、`Date` 之外的业务对象),`.systemSize` 是自己的字段。
- `extension UserDefaults { … }` / `@interface NSUserDefaults (WMF)` 这类**类型扩展的声明行**——那是在给类型加方法,不是使用。扩展体内用隐式 `self` 发出的 `bool(forKey:)`、`set(_:forKey:)` 才是站点(工作表会单独列出)。
- 封装类型自己的同名成员:`NIODeadline.uptimeNanoseconds`、`Scheduler.now`——数据进入封装类型的那一处才是站点(§4.8),封装类型的成员访问不是。
- `stat` 出现在 `struct stat` 声明里而不是调用。
- 被 `#if os(macOS)` / `#if os(Linux)` / `#if DEBUG` / `#if !os(iOS)` / 未定义的自定义标志包裹(见上)。
- 字符串或日志文本里的 API 名。

**上下文不足以判接收者类型时填 UNSURE,不要按"多半是"判。**

**判 NO 之后其余字段怎么填**:`operation: "NA"`、`value_fate: []`、`fate_evidence: ""`、`escape: null`、`applicable_reason: "NA"`、`constraint_verdicts: {}`——没有 RRA 使用就没有理由可适用、没有约束可评,不要把已声明的理由码和一串 UNKNOWN 填进去。`is_api_use_reason` **以一个代码开头**,后面再写具体说明,代码只能是:

| 代码 | 情形 |
|---|---|
| `COMPILE_GUARD_EXCLUDES_IOS_RELEASE` | 站点在 iOS Release 下为死的条件编译分支里 |
| `NAME_COLLISION` | 同名但不是那个 API:C 的 `struct stat` 类型或零值初始化 `stat()`、错误工厂 `.stat(name, errno:)`、自定义模型的 `.creationDate`、`UserDefaults.Key.x` 嵌套类型 |
| `WRAPPER_CALL` | 调用的是本单元自定义的同名封装(`Syscall.stat(path:)`、`Posix.lstat(pathname:outStat:)`),真实调用在封装体内的另一个站点上 |
| `DECLARATION` | 函数、类型、扩展、分类的声明行(`static func stat(…)`、`extension UserDefaults {`) |
| `STRING_LITERAL` | API 名出现在字符串或日志文本里 |
| `WRAPPER_TYPE_MEMBER` | 封装类型自己的同名成员(`NIODeadline.uptimeNanoseconds`),见 §4.8 |
| `OTHER` | 以上都不是,说明写清楚 |

swift-nio 的试标批次里 40 个候选有 31 个是 NO,几乎全落在 `NAME_COLLISION` / `WRAPPER_CALL` / `DECLARATION` 三档——`stat` 与 C 的 `struct stat` 同名是 FileTimestamp 类别特有的噪声,统计候选精度时按代码分开报。

`target.note` 为 `NOT_IN_ANY_XCODE_TARGET` 的文件(wikipedia-ios 的 `WMFSettingsViewController.m`:在仓库树里,但工程文件不把它编进任何 target)**不影响 `is_api_use`**——它是源码层面的使用,只是不会进二进制;在 `notes` 写 `NOT_IN_ANY_XCODE_TARGET`,`unit_confirmed` 照常判。二进制侧由链接 map 定夺。

ObjC 的家族形态(wikipedia-ios 实测):`[[NSUserDefaults standardUserDefaults] setBool:YES forKey:@"…"]` → WRITE;`[[NSUserDefaults standardUserDefaults] wmf_appResignActiveDate]` → WRAPPED(`wmf_` 是 Swift 扩展经 `@objc` 暴露);`[[NSUserDefaults alloc] initWithSuiteName:…]` → ACQUIRE;`[NSUserDefaults standardUserDefaults]` 单独成行 → ACQUIRE;`@interface NSUserDefaults (WMF)` 分类声明行 → NO;分类实现体内 `[self objectForKey:]` → 站点。

ALT 站点同样按此判,并额外注意两处最容易误判的:`DispatchTime.now()` 本身**不是**站点(那是调度),只有读取 `.uptimeNanoseconds` / `.rawValue` 才暴露数据;`clock_gettime` 系列要看第一个参数——`CLOCK_UPTIME_RAW` / `CLOCK_MONOTONIC_RAW` 及其 `_APPROX` 变体是 ALT 站点,`CLOCK_REALTIME` 是日历时间、`CLOCK_PROCESS_CPUTIME_ID` 是 CPU 时间,都不是。参数不可见时 UNSURE。

### 4.2 `operation`

- UserDefaults:`string(forKey:)` `object(forKey:)` `bool(forKey:)` `dictionaryRepresentation` … → READ;`set(_:forKey:)` `setValue` `register(defaults:)` → WRITE;`removeObject(forKey:)` `removePersistentDomain` → REMOVE;`addObserver` / KVO / `@AppStorage` 声明处 → OBSERVE;`synchronize()` 及其他**不带键的家族调用** → SYNC。一行里既读又写填主要动作,`notes` 写另一个。**没有可追数据值的操作——SYNC、ACQUIRE、REMOVE、OBSERVE(注册观察)——`value_fate` 一律 `["LOCAL_ONLY"]`,`escape: null`,`fate_evidence` 仍写站点行,`notes` 写 `NO_VALUE`**。空数组 `[]` 只属于 `is_api_use: NO` 的站点。
- FileTimestamp:`setAttributes([.modificationDate: …])` / `setResourceValues` 写时间戳 → WRITE;其余读取 → READ。**WRITE 站点的 `value_fate` 一律 `["PERSISTED_LOCAL"]`**(写进去的东西就是被持久化了),`escape: null`;被写入的值是什么写在 notes:`WRITTEN_VALUE: 本单元自算的过期时间` / `WRITTEN_VALUE: 设备 token`——RQ3 关心的是写进去的是不是设备数据,这在 notes 里,不在 fate 里。UserDefaults 的 WRITE 同理。
- SystemBootTime / DiskSpace / ActiveKeyboards:一律 READ(只有读取语义)。
- 调用的是**本单元自定义的扩展成员**(wikipedia-ios 的 `UserDefaults.standard.wmf_isImageDimming`,367 处)→ `WRAPPED`;定义在 `wrapper_ref` 里(文件、行、getter/setter、体内的键)。按 `wrapper_ref[0].access` 分:**`SET`**(`UserDefaults.standard.defaultTabType = .settings`)语义上就是一次写入 → `value_fate: ["PERSISTED_LOCAL"]`,`escape: null`,notes 写 `WRITTEN_VALUE: …`;赋 `nil` 的 setter 仍是 `WRAPPED`(形态决定 operation),notes 写 `WRITTEN_VALUE: nil —— 语义是清除该键`,不改成 REMOVE。**`GET` / `CALL`** → 追封装返回的值,与 READ 同法。真实读写在扩展体内的站点上另标。`wrapper_ref` 为 null(单元里没有这个成员)→ `operation: WRAPPED`、`value_fate` 按可见去向追,notes 写 `WRAPPER_DEFINITION_NOT_IN_UNIT`,不填 needs_context(补充目录里也没有)。
- 只是**取得 defaults 对象**、当行没有读写(`let ud = UserDefaults.standard`、`NSUserDefaults *ud = [NSUserDefaults standardUserDefaults]`、`UserDefaults(suiteName:)` 赋给变量或存进属性)→ `ACQUIRE`。二进制侧这仍是一次家族调用,所以它是站点。

**别名之后的调用才是真正的读写,它们也是站点——哪怕那一行没有 `UserDefaults` 四个字。** 三种别名形态,工作表都会把它们作为站点列出(`alias_of` 字段指回取得对象的那一行):

| 形态 | 例 | 站点 |
|---|---|---|
| 局部变量 | `NSUserDefaults *ud = [NSUserDefaults standardUserDefaults]; … [ud boolForKey:@"x"]` | 赋值行 ACQUIRE;`[ud boolForKey:]` 行 READ |
| 存储属性 / 注入 | wikipedia-ios `WMFUserDefaultsStore`:`private let defaults: UserDefaults`,`init(defaults:)`,方法里 `defaults.set(data, forKey:)` | 构造处 ACQUIRE(在调用方文件);`defaults.set` 行 WRITE |
| 扩展体内隐式 self | `extension UserDefaults { var wmf_x: Bool { bool(forKey: "x") } }` | `bool(forKey:)` 行 READ |

**扩展体内的站点,域由调用方集合决定——工作表把它算好了。** `callers` 列出扫描范围内调用该成员的全部 WRAPPED 站点(含经别的扩展成员间接到达的,`via_members`)及各自解析出的域。判 `DefaultsDomainIs` 类约束:`by_domain` 只有一种已解析的域 → 按它判,证据写 `callers: n=<n> 全部 <域>`(单元级证据,不需要行号);两种 → 按 MIXED_DOMAINS 写成字典;含 `UNKNOWN`/`MIXED_DOMAINS:…` 的调用方 → 那部分算 UNKNOWN,notes 写 `CALLER_DOMAIN_UNKNOWN: <site_id…>`,**不填 needs_context**(这些调用方本身是站点,它们的域在它们自己的记录里定);`n: 0` → UNKNOWN,notes `NO_CALLERS_IN_SCOPE`,不填 needs_context。`member.public` 为 true 且单元是依赖(deps/pods)时,宿主也可能调用 → 域集合不封闭,notes 写 `CALLERS_NOT_CLOSED`。

**实例的域由构造方决定,不由方法体决定。** `WMFUserDefaultsStore` 在 `WMFDataEnvironment.swift` 里被构造了两次:L89 用默认的 `.standard`,L99 用 `group.org.wikimedia.wikipedia` 的 suite。于是它方法体里同一行 `defaults.set(…)` 对第一个实例是 APP_PRIVATE 域、对第二个是 APP_GROUP 域。判 `DefaultsDomainIs` 时:工作表会附上所有构造处及其域;域只有一种 → 照判;**多种 → 每种域各给一个 verdict,写成 `"R1C8F_C1": {"APP_PRIVATE": "CONFLICT", "APP_GROUP": "SUPPORTED"}`,notes 写 `MIXED_DOMAINS`**;构造处不可见 → UNKNOWN + needs_context。

**suite 名是常量时要解析。** ObjC 的 `[[NSUserDefaults alloc] initWithSuiteName:WMFApplicationGroupIdentifier]`——`WMFApplicationGroupIdentifier` 是别处定义的常量。工作表解析字符串常量(字面量、`case x = "…"`、`#define`、以及 `NSString *const X = @QUOTE(MACRO)` 这种从构建设置来的值)并在 `domain_hint` / `instance_domains[*].note` 里写明出处(`SUITE_CONST:X='group.…' @ 文件:行 (build macro … from Wikipedia.xcodeproj:WMF:Release)`);没解析出来(`SUITE_CONST:X unresolved`)就 UNKNOWN + needs_context 要该常量的定义,**不要按名字猜它是不是 group**。

**读回自己写的值。** Cache 把过期时间写进文件 mtime(WRITE),再从 mtime 读回(READ)——读到的不是真实文件系统时间戳,是本单元自己写的值。API 层面仍是 RRA 使用(`is_api_use: YES`,Apple 按 API 计),但 notes 必须写 `SELF_WRITTEN_VALUE: <写入站点 文件:行>`,RQ3 分析时这类读取不携带设备信息。覆盖面是**单元级**:同一单元里**看得到**写入(Cache 的 L83),且读取端把读到的值当作自己写的东西用(命名为 `expiryDate`、按 `inThePast` 判过期)→ 该单元所有这样读回的站点都标,**包括读取的聚合体是参数传入、构造处不可见的**(DiskStorage 的 `removeResourceObjects(_ objects:)` 排序比较器)——这条标的是"这个单元的这个存储里放的是什么",不是 §4.3 那种站点到站点的值流连接,两者不冲突;写入看不到、或读取端把它当真实时间戳用(显示"上次修改于")→ 不标。
- `is_api_use` 为 NO 时填 NA。

### 4.3 `value_fate` —— 返回值(及其派生值)在**你能看到的范围内**去了哪

多选。追踪对象是 API 返回值以及由它算出来的任何东西(差值、格式化字符串、字典里的一个字段都算派生)。追到函数边界为止;越过边界的,记 `escape` 并把对应 fate 标上。

| 值 | 含义 | 典型证据 |
|---|---|---|
| `LOCAL_ONLY` | 在函数内用完即弃:比较、算术、控制流条件、临时变量——**且比较/算术的结果本身也没离开函数** | `if free < threshold`、`elapsed = now - start` 后只用于本地逻辑 |
| `UI_DISPLAY` | 渲染给用户:赋给 label/text、格式化后进 SwiftUI 视图、进 alert | `label.text = formatter.string(from: date)` |
| `LOGGED` | 进日志/控制台/本地日志文件 | `print`、`os_log`、`NSLog`、写入 `.log` |
| `PERSISTED_LOCAL` | 写入本地持久化:UserDefaults、文件、Keychain、数据库 | `defaults.set(uptime, forKey:)`、`try data.write(to:)` |
| `STORED` | 写入实例属性 / 静态变量 / 全局变量,而读取方不在可见行内 | `self.resumeTime = start`、`metrics.duration = elapsed` |
| `RETURNED` | 作为返回值离开函数 | `return attrs[.modificationDate]` |
| `PASSED_OUT` | 作为参数传给**另一个单元**的函数 / 存进会被别的单元读到的对象 | `analytics.track(props: ["boot": t])`(analytics 是别的包) |
| `OFF_DEVICE` | 进网络请求体/URL/头、进第三方分析或崩溃上报 SDK 的调用、进推送负载 | `URLRequest` body 含它、`Crashlytics.log`、`params["disk"] = free` 随请求发出 |
| `DERIVED_ID` | 与其他设备信号拼接/哈希成标识、写进"deviceId/fingerprint"一类的变量或键 | `sha256(bootTime + model + …)`、`fingerprint["boot"] = …` |
| `UNSURE` | 变量流向在给定行内断了 | 值存进一个属性后再无引用 |

判定顺序:先看值(或派生值)在可见范围内有没有到达**终点**——网络发送、UI 渲染、日志、本地持久化都是终点:标对应的 fate,`escape` 为 `null`(`escape.kind` 故意没有 `OFF_DEVICE`:能看到它进网络,就不存在"去向不明"的逃逸)。本单元自己的传输层 API 也是网络终点:swift-nio 的 `outbound.write(frame)`、`channel.writeAndFlush`,Alamofire 的 `URLRequest` 组装,都算 `OFF_DEVICE`,不是 `PASSED_OUT`。没到终点、但离开了函数——`return`、传给别的单元、写入属性/静态/全局——标 `RETURNED` / `PASSED_OUT` / `STORED` 之一并填 `escape`;两者都有(先进了网络,又被 return)就都标。没离开 → 在函数内按上表标。

`STORED` 的例外:属性写入后**在可见行内**又被同一函数/同一类型读回并用完,读回那一段按 `LOCAL_ONLY` 计,`STORED` 仍要标——因为属性的其他读取方你看不见。

**派生值离开函数也是逃逸,包括布尔。** `return read() == .explore`、`self.isEnabled = bool(forKey:) != nil`、把比较结果装进 Event —— 离开的是由该值算出来的东西,记 `RETURNED` / `STORED` / `PASSED_OUT` 并填 `escape`,`escape.value` 为 `DERIVED`,notes 写 `DERIVED_AS: <一句话,如 与常量比较得到的布尔>`。不要因为"只是个控制标志"就记 `LOCAL_ONLY`:那是对下游用途的推测,而 `escape.value = DERIVED` 已经把"不是原值"这个事实记下了,分析时按它区分。只有结果在函数内用完(进了 `if` 就丢弃)才是 `LOCAL_ONLY`。RQ4 的流层评的正是"派生值到了谁手里",真值里把它记成 LOCAL_ONLY 会把分析器正确的追踪算成误报。

**`escape.value` 必填:逃逸的是原值(`RAW`)还是派生值(`DERIVED`)。** 这不是细节——SystemBootTime 的两条理由(35F9、8FFB)明文区分"原始开机时间不得离开设备"和"应用内事件的时长/绝对时间戳可以离开设备"。Alamofire 把两次 `systemUptime` 相减得到的 `serializationDuration` 交给宿主,逃逸的是 `DERIVED`;若把 `start` 本身交出去,才是 `RAW`。两者都有就填 `RAW`(更严的那个)并在 `notes` 说明。

**`RAW` 与 `DERIVED` 的分界是语义,不是形式。** 原值换单位、转字符串、字符串插值、格式化(`"\(theTime)"`、`String(uptime)`、`Int(uptime * 1000)`)仍是 `RAW`——它仍是那一次读数;只有**与另一读数相减得到时长**、**与 `Date()` 组合得到事件的绝对时间戳**这两种 Apple 理由里认可的变换才是 `DERIVED`。

**聚合体只追里面那个字段。** `struct stat`、`URLResourceValues`、`attributesOfItem` 返回的字典、装着该值的自定义 model——`value_fate` 追的是其中的时间戳/偏好字段,不是整个聚合体。聚合体整体被 `return` / `inout` 交回 → `escape.kind = RETURNED`、`escape.value = RAW`;整体存进一个局部数组或属性、之后的去向不在可见行内 → `value_fate` 加 `UNSURE` 并 `needs_context` 要剩余函数体;**不要**因为同批另一个站点所在函数的参数类型相同就把两边接上——只有可见的调用行才算连接。

**`stat` 族追踪的是结构体里的时间戳字段,不是整个结构体。** `stat` / `fstat` / `lstat` / `fstatat` 返回的 `struct stat` 同时装着大小、权限、时间戳。`value_fate` 只追 `st_mtimespec` / `st_ctimespec` / `st_birthtimespec` / `st_atimespec`(及 `st_mtime` 等旧名):可见范围内只读了 `st_size` / `st_mode` → `["LOCAL_ONLY"]` 并 notes `TIMESTAMP_FIELDS_UNUSED`;整个结构体被 `return` 或 `inout` 交回调用方(swift-nio 的 `system_stat` 把 `info` 原样交回)→ `escape.kind = RETURNED`,`escape.value = RAW`。`is_api_use` 不受影响——Apple 按 API 计,调了 `stat` 就是用了。

**`getattrlist` 族按请求的属性位定类别。** `getattrlist` / `fgetattrlist` / `getattrlistat` / `getattrlistbulk` 同时列在 FileTimestamp 和 DiskSpace 下,工作表的 `category` 给的是两者。看 `struct attrlist` 里设置的位:`ATTR_CMN_CRTIME` / `ATTR_CMN_MODTIME` / `ATTR_CMN_CHGTIME` / `ATTR_CMN_ACCTIME` → 时间戳;`ATTR_VOL_SPACEFREE` / `ATTR_VOL_SPACEAVAIL` / `ATTR_VOL_SPACEUSED` / `ATTR_VOL_SIZE` → 磁盘空间。在 notes 里写 `ATTR_BITS: <看到的位>`,`value_fate` 只追对应字段;attrlist 是参数传入、看不到位 → 两类都保留,notes 写 `ATTR_BITS_UNKNOWN`。**位提示只能补充,不能减掉 Apple 列的类别**(本批样本里没有 `getattrlist`,此条未经真实代码压过)。

**键请求与值读取是同一次访问的两个站点。** `foundation_key` 类 API(`.contentModificationDateKey`、`NSURLVolumeAvailableCapacityKey`…)常先出现在 keys 数组里(请求),几行后才 `resourceValues[key]` 取值(读取)。两处都是站点,`value_fate` 都追踪**取值那一行之后**的去向;若同一函数内找不到取值行,键请求站点填 `UNSURE` 并 `notes` 说明。

**Apple 系统框架不是单元。** 把值交给 UIKit / CoreAnimation / Foundation / Combine 的 API(`anim.beginTime = CACurrentMediaTime() + delay`、`label.text = …`)算 `LOCAL_ONLY` 或 `UI_DISPLAY`,不算 `PASSED_OUT`;只有网络、分析、崩溃上报类 API(`URLSession`、`URLRequest`、`os_log` 之外的远程日志、`Analytics`/`Crashlytics`/自家的 `Funnel`/`EventLogging`)才是 `OFF_DEVICE` 的入口。交给**另一个单元**的函数才是 `PASSED_OUT`——单元按 `unit` / `target` 划分,**第一方本地包(wikipedia-ios 的 WMFData、WMFComponents)和仓库内 framework target 也是不同单元**,不只第三方 SDK;`escape.target` 写明接收单元。这样 RQ4 的流层才能把 App ↔ 本地包 ↔ extension 的传播接上。

`PASSED_OUT` 与 `OFF_DEVICE` 的区别:传给同单元内的辅助函数不算 `PASSED_OUT`(那仍是本地流转,继续追);传给**不同单元**的函数才算;而 `OFF_DEVICE` 要求你能看到它进了网络/上报——传给一个名叫 `Analytics.track` 的东西,如果那个单元不在你面前,标 `PASSED_OUT` 并在 `escape.target` 写 `Analytics.track`,不要直接标 `OFF_DEVICE`。第二轮会顺着 `escape.target` 追。

### 4.4 `applicable_reason`

- `is_api_use` 为 `NO` → `NA`(见 §4.1 末尾);下面各条只对 YES / UNSURE 的站点。
- 该站点类别的 `declared_reasons` 为空 → `NONE`(这就是 RQ1 的"声明缺失",无论宿主 `hosts[*].declares` 里有没有——宿主代报是**独立一档**,由工具按 `hosts` 字段聚合,你不用管)。
- 恰好一条 → 就是它。
- 多条 → 按站点语境选最贴切的一条;分不清填 `UNSURE`,`constraint_verdicts` 对**每条**已声明理由都给,键前加理由码:`"35F9.1/R35F9_C1": …`。

### 4.5 `constraint_verdicts` —— 对每条约束给 SUPPORTED / CONFLICT / UNKNOWN

约束的 `predicate` 分几个家族,判法如下。**通用规则:`REQUIRED` 型约束,正面证据→SUPPORTED,明确反证→CONFLICT;`FORBIDDEN` 型约束,看到被禁止的事→CONFLICT,在可见范围内确认没发生且值未逃逸→SUPPORTED;值逃逸了→UNKNOWN(留给第二轮)。**

| predicate 家族 | 怎么判 |
|---|---|
| `NoOffDeviceFlow(X)` `NoInternetFlow` `NoOffDeviceFlowBySDK` | 先看括号里禁的是什么:`RAW_BOOT_TIME` 只管原值。`value_fate` 含 `OFF_DEVICE` 且流出的正是被禁的那种值 → CONFLICT;可见范围内被禁的值既未流出也未逃逸 → SUPPORTED(哪怕派生值逃逸了);被禁的值本身逃逸(`escape.value` 命中)→ UNKNOWN |
| `PurposeIs(…)` `PurposeIsOneOf(…)` | 从用法判目的。35F9(测时长):两次 uptime 相减、作定时器基准 → SUPPORTED;uptime 原值作为字段发出/存储 → CONFLICT。8FFB(算绝对时间戳):uptime 与 Date 组合求事件时刻 → SUPPORTED。C617/3B52/DDA9 等看 `ResourceBelongsTo` 与 `ReachesUserVisibleUI`。DiskSpace 的 7D9E / SystemBootTime 的 3D61(错误报告):可见用法必须是把该值**装进报告内容** → SUPPORTED;可见用法是写入前的容量检查、按空间决定行为(wikipedia-ios `HelpViewController` 导出日志前的 `userHasEnoughSpace()`)——那是 E174 的语义,不是报告内容 → CONFLICT;看不出用途 → UNKNOWN |
| `ReachesUserVisibleUI(…)` | `UI_DISPLAY` → SUPPORTED;值只做比较/控制流从不显示 → CONFLICT;逃逸 → UNKNOWN |
| `ReachesProminentReportUI` `UserAffirmativelySubmits` `TriggeredBy(USER)` `Informs` | 需要 UI 流程证据。站点在明显的"发送错误报告"动作回调里且报告内容可见 → SUPPORTED;否则 UNKNOWN。**不要因为文件名叫 BugReport 就 SUPPORTED** |
| `ResourceBelongsTo(APP_CONTAINER…)` (C617) | 路径来自 `FileManager.urls(for: .documentDirectory/.cachesDirectory…)`、`Bundle.main`、`NSTemporaryDirectory`、app group 容器 → SUPPORTED;来自 document picker / 用户选择 / 外部 URL → CONFLICT(那是 3B52 的情形);路径是参数传入、来源不可见 → UNKNOWN |
| `ResourceIsUserGranted` `GrantCovers` (3B52) | 与上一条互为镜像 |
| `DefaultsDomainIs(APP_PRIVATE)` (CA92) | `UserDefaults.standard` / `UserDefaults()` → SUPPORTED;`UserDefaults(suiteName: "group.…")` → CONFLICT(属 1C8F);`suiteName:` 非 group 前缀、`NSGlobalDomain`、`.global` → CONFLICT |
| `DefaultsDomainIs(APP_GROUP)` (1C8F C1) | `suiteName: "group.…"` → SUPPORTED;`.standard` → CONFLICT。**这是刻意的**:wikipedia-ios 只声明了 1C8F.1,却有 367 处 `UserDefaults.standard.…`——每一处对 1C8F 都是 CONFLICT,因为声明的理由不覆盖这个域;它本该同时声明 CA92.1。"某类别声明了、但每条已声明理由在此站点都被证伪"由工具聚合成 `CONTRARY_REASON_DECLARATION`,你只管逐站点判 |
| `AllIntendedParticipantsAreMembersOfSameAppGroup` (1C8F C2) | 问的是**参与者**(谁读写这个域),不是域本身——域不匹配已经由 C1 记了,不在这里记第二次。`.standard`:参与者只有本 App,没有群组外成员 → SUPPORTED,notes `PARTICIPANTS: 仅本 App`;group suite 且名字在该 target 的 `target.app_groups`(entitlements)里 → SUPPORTED;group suite 名字不在 entitlements 里、或可见的参与者(另一个 target / 键盘扩展)不是该 group 成员 → CONFLICT;suite 名未解析 → UNKNOWN。与分析器一致:它只在"参与者在声明的 App Group 之外"时证伪这条 |
| `NoReadFrom(OTHER_APPS_OR_SYSTEM)` `NoWriteAccessibleBy(…)` | 看**键**,不看键常量的名字:站点的 `key.value`(直接站点)或 `wrapper_ref[*].keys[*].value`(WRAPPED 站点)是解析出的字符串。系统写入的键(`AppleLanguages` `AppleLocale` `AppleKeyboards` `AppleInterfaceStyle` `AppleICUForce24HourTime` `AppleTemperatureUnit` `AppleMeasurementUnits` `AppleMetricUnits` `AppleFirstWeekday` `NSLanguages` `AppleTextDirection`、任何 `com.apple.*` / `NSGlobalDomain` 下的键;不穷举——`Apple*`/`NS*` 前缀且不是本 App 定义的字面量就按系统键处理)→ CONFLICT(除非声明的是 AC6B);解析出的键是本 App 的字面量 → SUPPORTED,证据写 `key=<值> @ <出处>`;`dictionaryRepresentation()` / `volatileDomain(forName: NSGlobalDomain)` / `persistentDomain(forName:)` 传的不是自己的 bundle id → 读到的是整条搜索链(含系统域)→ CONFLICT(Apple 公开语义,§1 第 1 条);`persistentDomain(forName: <自己的 bundle id>)` → SUPPORTED;键未解析(`UNRESOLVED_IDENTIFIER` / `EXPRESSION`)→ UNKNOWN + needs_context 要该常量的定义;WRAPPED 站点 `wrapper_ref.keys` 为空(体内没有 forKey:)→ UNKNOWN,notes `WRAPPER_KEYS_NOT_VISIBLE` |
| `OperationAndIdentifierIs(…)` (AC6B) | 读 `com.apple.configuration.managed` / 写 `com.apple.feedback.managed` → SUPPORTED;其他键 → CONFLICT |
| `DeclaredBy(THIRD_PARTY_SDK)` `AppRoleIs(SDK)` (0A2A/C56D) | `unit_kind` ≠ APP 且 `unit_role` 为 THIRD_PARTY / FORK → SUPPORTED;APP 或 FIRST_PARTY 本地包 → CONFLICT(App 不得声明这两条) |
| `TriggeredBy(HOST_APP_WRAPPER_CALL)` (0A2A/C56D) | 要**看得到从 SDK 公开 API 到站点的完整链**才 SUPPORTED(swift-nio NIOPosix:`public func lstat(path:eventLoop:)` L686 → `Posix.lstat` L689 → `sysLstat` L939 → 绑定 L164,链在补充目录的同一文件里);SDK 在自己的初始化/后台任务里主动调 → CONFLICT;上游只到 `@_spi(Testing)` / `private` 的内部函数、公开入口不在可见范围 → UNKNOWN + needs_context CALLERS |
| `SDKDoesNotUseForOwnPurpose(Flow(u))` (0A2A/C56D) | 时间戳/偏好值原样交回**宿主**(可见链到达 SDK 的公开 API、中途没被 SDK 自己读取)→ SUPPORTED;SDK 自己拿它做判断、缓存、上报 → CONFLICT;交回的只是 SDK **内部**的调用方、后续不可见(swift-nio `system_stat` → `Syscall.stat` → `private func _info`)→ UNKNOWN + needs_context,与附录 B.6 一致 |
| `SDKPrimaryPurposeIsNotRRAWrapper` `SDKDoesNotUseForOwnPurpose` | 单元级判断:该包的主要功能是不是"帮宿主读 RRA"。看包名/README 可判则判,否则 UNKNOWN |
| `AppPrimaryFunctionIs(KEYBOARD)` `AppHas(TEXT_FIELDS)` `AppOnlyProvides(HEALTH_RESEARCH)` `AppCompliesWithReviewGuideline` | 应用级事实。`app_facts` 给了就用;没给 → UNKNOWN |
| `Controls(…)` `UserObservableBehaviorDiffersByDiskSpace` `DownloadAvoidance…` | 值进入 `if` / `guard` 且分支改变用户可见行为(弹提示、拒绝写入、跳过下载)→ SUPPORTED;值不影响任何分支 → CONFLICT;逃逸 → UNKNOWN |
| `IfOffDevice(v): DataSemanticIs(v, …)` | 条件型:凡是可能离开设备的 v 都是允许的语义(如时长)→ SUPPORTED;有原值可能离开 → 按 `escape.value` 判,RAW 逃逸 → UNKNOWN |
| `RemotePurposeIs(…)` `IfLocalNetworkOffDevice` `OccursAfter` `EventScopeIs` | 条件型:前提不成立(没有 off-device)→ SUPPORTED;前提成立再看条件 |
| `Declassify(X, OFF_DEVICE_ALLOWED)` | 解密条款:逃逸/流出的值属于 X(如时长)→ SUPPORTED;不属于 → 不适用,记 SUPPORTED 并 `notes` 写"未触发" |
| `MayAvoid…` `SystemGlobalFallback…` (EXCEPTION) | 例外条款:例外情形成立时记 SUPPORTED 并在 `notes` 写明触发了例外;否则 UNKNOWN——这个 UNKNOWN 的意思是"例外未触发",**不是缺上下文,不触发 needs_context**(§4.6) |

每个 `verdict` 都要能在 `fate_evidence` 或 `notes` 里找到对应的证据。站点级约束的证据是 `L<n>: <片段>`;引用**其他文件**的行写成 `<文件路径> L<n>: <片段>`;**单元级约束**(`DeclaredBy` / `AppRoleIs` / `SDKPrimaryPurposeIsNotRRAWrapper` / `AppPrimaryFunctionIs` 一类)没有代码行,证据写字段:`unit_kind=SPM, unit_location=deps/swift-nio@…, 覆盖清单 Sources/NIOFS/PrivacyInfo.xcprivacy 声明 0A2A.1`,或 `hosts[0].app_facts.has_keyboard_extension=false`;扩展体站点的域证据写 `callers: n=11 全部 APP_PRIVATE`,键证据写 `key=WMFAppThemeName @ NSUserDefaults+WMFExtensions.swift:13`——这些引用了工作表字段,也算证据。

### 4.6 `needs_context`

值逃逸、接收者类型不明、路径来源不明、`escape.target` 是同单元函数需要继续追——这些情况填 `needs_context`。先在**补充目录**里找:站点文件的其余部分、扩展成员定义、构造处所在文件都在那里,找到了就直接用(引用写 `<文件> L<n>`),不填 needs_context。补充目录里也没有的才填,且必须是**工具能按名字取的东西**:

```json
"needs_context": {"what": "一句话", "why": "哪个字段等着它",
                  "requests": [ {"kind": "DEFINITION", "symbol": "WMFApplicationGroupIdentifier", "file": null},
                                {"kind": "CALLERS", "symbol": "Posix.lstat", "file": "Sources/NIOPosix/System.swift"},
                                {"kind": "BODY", "symbol": "removeExpiredObjects()", "file": "Source/Shared/Storage/DiskStorage.swift"},
                                {"kind": "TYPE", "symbol": "NIODeadline", "file": null} ] }
```

`kind` 只能是 `DEFINITION`(一个常量/变量/函数的定义)、`CALLERS`(一个函数/成员的全部调用处)、`BODY`(某函数的完整函数体)、`TYPE`(某类型的定义,含公开成员)、`FILE`(整个文件)。工具按 `requests` 抽取片段生成补充批次再回给你;写不成这四种之一的请求没法满足。宁可多要一次,不要猜。

只在**有字段等着定稿**时才填:`is_api_use` / `operation` 为 UNSURE;RRA 站点 `applicable_reason` 是理由码且某个 verdict 为 UNKNOWN **且这个 UNKNOWN 是缺代码造成的**;ALT 站点 `exceeds_all_reasons` 为 UNKNOWN;`value_fate` 含 UNSURE(哪怕 `applicable_reason` 是 NONE——fate 本身是 RQ4 的输入)。**不填**的 UNKNOWN:EXCEPTION 类约束未触发的 UNKNOWN(补多少代码也消不掉);`NO_CALLERS_IN_SCOPE` / `CALLER_DOMAIN_UNKNOWN`(域在别的站点上定);`WRAPPER_DEFINITION_NOT_IN_UNIT`(定义不在本单元)。`applicable_reason` 为 NONE、fate 已定、只是值 RETURNED/RAW 离开了(Cache L67)→ `needs_context: null`,那条逃逸由 §5 的流层接手,不在这里追。

**一个站点的 verdict 只有在它的逃逸被追到头之后才算定稿**,不管逃逸是不是跨单元。Lightstreamer 的 `Scheduler.now` 返回原始 uptime 毫秒值,读取方全在同一单元的 `LightstreamerClient.swift` 里——这不是 RQ4 的跨单元流,但不追完就无法判 `exceeds_all_reasons`。所以扩展回路(§4.6)和流层(§5)是两件事:前者为了把站点自己的 UNKNOWN 消掉,后者为了 RQ4。

### 4.7 ALT 站点的附加字段

替代 API 不受申报要求约束,所以对它**不判理由、不判约束**——`applicable_reason` 填 `"NA"`,`constraint_verdicts` 填 `{}`。它要回答的是 RQ2 的三个子问题,对应三个字段:

**`alt_equivalence`** —— 它和被替代的 RRA 在数据层面有多接近。取值随附录 C 给出,你只在上下文表明**实际用法**与附录分级不符时改:

| 值 | 含义 |
|---|---|
| `NEAR_EQUIVALENT` | 拿到同一数据(可能换了单位):`clock_gettime_nsec_np(CLOCK_UPTIME_RAW)` 之于 `mach_absolute_time` |
| `CONDITIONAL` | 技术上能取得同类数据,但 Apple 未明确认可为免申报替代:`getfsstat`、`CFPreferences*`、`sysctl kern.boottime` |
| `PARTIAL_DATUM` | 只暴露该数据的一部分:`textInputMode.primaryLanguage` 之于活跃键盘列表 |

**`declared_for_mapped_category`** —— 脚本预填(该单元清单有没有声明被替代的那个 RRA 类别),你不改。它回答"用替代 API 的 App 会不会顺手声明"。**即使为 true,也不要给 ALT 站点填理由和约束**——声明是对 RRA 的,ALT 不受它约束;把两者混起来正是 RRA/ALT 分离要防的事。Lightstreamer 是现成的例子:清单声明了 `SystemBootTime/35F9.1`,源码里却没有一处 RRA 开机时间调用,只有 `DispatchTime.now().uptimeNanoseconds` 这一个 ALT 站点——它声明的其实是这个替代用法。工具会在单元层面单独统计"声明了某类别但该类别零 RRA 站点"的情形,你在 `notes` 里点一句即可。

ALT 站点判 `is_api_use: NO` 时:`alt_equivalence` 照附录 C 填(它是 API 层面的数据等价性,与站点活不活无关),`exceeds_all_reasons` 填 `null`(没有用法就没有越界可言),其余按 §4.1 的 NO 规则清零。

**`exceeds_all_reasons`** —— 用法是否超出了该类别**所有**获批理由的边界。判法:

- `escape.value = RAW` 且流向 `OFF_DEVICE`,或 `value_fate` 含 `DERIVED_ID` → **YES**。该类别没有任何理由允许原值离开设备或参与指纹(全局规则 G5 对所有类别成立)。
- 离开设备的只有**派生量**(两次读数相减的时长、时长换算的毫秒数)→ **NO**。wikipedia-ios 的 `SessionsFunnel` 把 `(CACurrentMediaTime() - pageLoadStartTime) * 1000` 送进事件日志,是 NO。
- `value_fate` 全为本地且无 `escape` → **NO**。
- 原值 `escape` 且去向不明 → **UNKNOWN**,扩展回路后再判。

---

### 4.8 封装类型:数据进入类型的那一处是站点,之后按"暴露面"处理

很多库把 RRA/ALT 的值装进一个类型再到处传:swift-nio 的 `NIODeadline`(`now()` 里读 `DispatchTime.now().uptimeNanoseconds`,全库几百处 `.now()`)、Lightstreamer 的 `Scheduler.now`。逐个消费方追不完,也不该追——按下面处理:

1. **站点只有一处**:数据进入类型的那一行(`NIODeadline.timeNow()` 里的 `DispatchTime.now().uptimeNanoseconds`)。类型自己的成员访问(`lhs.uptimeNanoseconds < rhs.uptimeNanoseconds`)、消费方对类型的比较/加减,都不是站点。
2. 该站点 `value_fate` 填 `["STORED"]`,`escape = {"kind": "STORED", "value": "RAW", "target": "<类型名>"}`。
3. 在 `notes` 里写**类型的暴露面**,四选一:`WRAPPER_EXPOSES_RAW`(有公开访问器能取回原值,如 `public var uptimeNanoseconds`)、`WRAPPER_EXPOSES_DERIVED_ONLY`(只暴露比较、时长差等派生量)、`WRAPPER_OPAQUE`(什么都不暴露)、`WRAPPER_EXPOSURE_UNKNOWN`(类型定义在补充目录里也找不到——NIODeadline 定义在站点自己的文件 `EventLoop.swift` 里,补充目录有整个文件,所以 B.7 的 `L886 public var uptimeNanoseconds` 现在是看得到的证据;引用它,不要引用附录)。
4. 中间两种:该站点的 off-device 类约束和 `exceeds_all_reasons` 直接按"原值未逃逸"判(SUPPORTED / NO)——原值被类型封死了。`WRAPPER_EXPOSES_RAW`:UNKNOWN,`needs_context` 写"读取 `<类型>.<原值访问器>` 的全部位置"——工具只需 grep 那个访问器,不用追 `.now()`。`WRAPPER_EXPOSURE_UNKNOWN`:UNKNOWN,`needs_context` 要类型定义。

## 5. 第二轮:流层(RQ4)

第一轮完成后,所有 `escape` 非空的站点会生成流工作表:除站点记录外,附上 `escape.target` 对应的函数体、跨单元的调用者/被调者候选片段。你要输出**流记录**:

```json
{
  "flow_id": "f-…", "source_site_id": "a3f9…",
  "path_type": "RETURN_VALUE",              // RETURN_VALUE | TRIGGER | CHANNEL
  "hops": [ {"unit": "Alamofire", "function": "startTimer()", "loc": "Source/…:248"},
            {"unit": "APP", "function": "reportMetrics()", "loc": "App/…:91"} ],
  "sink_unit": "APP", "sink_declared": false,
  "sink_use": ["OFF_DEVICE"],               // 词表同 value_fate
  "verdict": "CONFLICT",                    // 终点用法对**源头理由**约束的结论
  "evidence": "…", "needs_context": null, "notes": ""
}
```

三条路径的定义(以声明单元 A、未声明单元 B 为例):

| 路径 | 定义 | 从哪里播种 | 判什么 |
|---|---|---|---|
| `RETURN_VALUE` | A 调用 RRA,把返回值(或派生值)传给 B | 站点 `escape.kind` ∈ RETURNED / PASSED_OUT | B 拿到后的用法,对 A 声明理由的约束 |
| `TRIGGER` | B 调用 A 的函数,间接触发了 RRA | 含 RRA 站点的函数被**别的单元**调用 | B 是不是这次 RRA 使用的实际受益方;B 自己有没有声明 |
| `CHANNEL` | A 把值写进共有域(UserDefaults 键、文件、Keychain、数据库、可被外部读的属性/全局),B 再读出来 | 站点 `escape.kind` ∈ PERSISTED_LOCAL / STORED | 找同一键/路径/属性的读取方 B,判 B 的用法 |

`sink_declared` 与 `source_declared` 都要记,两种口径("接收方受源头理由约束" vs "接收方必须自己声明")在数据里都能算。

流追不到终点(值进了你看不到的单元)→ `verdict` = UNKNOWN,`needs_context` 写明卡在哪一跳。

---

## 6. 代码归属字段

二进制侧的归属真值来自链接器 map,**不来自你**;你标的是源码侧的归属,用来和 map 对齐、以及回答"谁该声明"。三个字段:

**`unit_confirmed`** —— 脚本按路径分配的 `unit` 对不对。绝大多数 YES。填 NO 的情形:`Package.swift` 用 `path:` 把 target 指到了非常规目录,文件实际属于另一个 target;文件在 `Sources/<A>/` 下但 `#if` 守卫表明它只编进别的产物。NO 时在 `notes` 写正确单元名。判不了 UNSURE。

**`unit_role`** —— 这个单元对宿主 App 而言是谁的代码:

| 值 | 判据 |
|---|---|
| `FIRST_PARTY` | App 自己的 target;仓库内的本地 Swift 包(`unit_location` 在 repo 树内);`:path:` 本地 pod |
| `THIRD_PARTY` | `deps/` 或 `pods/` 里的外部依赖 |
| `VENDORED_THIRD_PARTY` | 第三方源码被直接拷进仓库树(wikipedia-ios 的 `WMF Framework/Third Party/FLAnimatedImage/`,文件头是 Flipboard 的版权)。脚本按路径里的 `Third Party` / `Vendor` / `External` 预填候选,你在补充目录里打开该文件看文件头版权确认,证据写 `<文件> L1-L5: Copyright …` |
| `FORK_OF_THIRD_PARTY` | 外部依赖,但 `git` 地址的 owner 与 App 仓库 owner 相同(Bark 的 `Finb/ImageViewer.swift`、`Finb/QRScanner`)。它既不是纯第一方也不是纯第三方,单独一档 |
| `UNSURE` | 判不了 |

脚本按位置预填前两种;`VENDORED_THIRD_PARTY?`(路径里有 Third Party / Vendor / External)和 `FORK_OF_THIRD_PARTY`(依赖的 git owner 与某个宿主仓库的 owner 相同)是脚本给的候选,带问号的要你按文件头版权确认后去掉问号或改回 FIRST_PARTY。

**`declaring_unit`** —— 这个站点应由哪个单元的隐私清单覆盖。默认值**照抄站点的 `declaring_unit_prefill`**,它是脚本从工程文件读出的**编译目标名**:app 仓库里是 pbxproj 里该文件所属 target 的名字(`Wikipedia`、`WMF`、`WidgetsExtension`;一个文件在多个 app target 里时取主 app target,`target.also_in` 列出其余),本地 Swift 包是 target 名(`WMFData`),SPM 依赖是 target 名(`NIOFS`、`Alamofire`),pod 是 pod 名(`Cache`)。链接 map 里的归属就是按这个粒度记的,两边要对得上。Apple 的规则是**按 bundle**:每个 app、每个 app extension、每个动态 framework 各自一份清单;静态链进某个 bundle 的代码归那个 bundle 的清单管,但第三方 SDK 须自带清单由 Xcode 聚合。落到字段上:

| 站点所在 | `declaring_unit` |
|---|---|
| App target、仓库内静态链接的本地 Swift 包、`:path:` 本地 pod、vendored 第三方源码 | 预填的 target 名(本地包的 target 名保留——链接 map 按它记;清单归属由工具按"静态链进 App"折算) |
| App extension target(widget、share、notification…) | 该 extension 的 target 名(它是独立 bundle,要自己的清单;`manifest_scope` 带 `_FALLBACK_EXTENSION_TARGET` 时说明它没有自己的清单) |
| 仓库内的动态 framework target(wikipedia-ios 的 `WMF`) | 该 framework 的 target 名。注意 `Wikipedia/Code/NSUserDefaults+WMFExtensions.swift` 按路径像 App,按工程文件编进 `WMF`——以 `target` 为准 |
| 第三方 SwiftPM / pod(含 fork) | 该 SDK 的 target / pod 名 |

只在 `target` 缺失(`name: null`)或你有证据认为工程文件说的不对时改写,并在 notes 说明依据;`target.note` 为 `NOT_IN_ANY_XCODE_TARGET` 的文件照抄预填(它没有 target,预填是按路径的 `unit`),notes 记这个事实。头文件 inline 函数 / 宏展开在调用方编译单元里的,填调用方。

RQ4 的 `TRIGGER` 路径里还有一个归属判断——"这次 RRA 使用的实际受益方是谁"——那是流层字段 `beneficiary_unit`,第二轮填。

## 附录 A. 词表

**类别**:`FileTimestamp` `SystemBootTime` `DiskSpace` `ActiveKeyboards` `UserDefaults`

**理由码**(标题与 Apple 限制摘要;完整约束随工作表给出):

| 码 | 类别 | 标题 | Apple 限制摘要 |
|---|---|---|---|
| `DDA9.1` | FileTimestamp | 向用户显示文件时间戳 | 信息及派生信息不得离开设备 |
| `C617.1` | FileTimestamp | 访问 App 自己容器内文件的元数据 | — |
| `3B52.1` | FileTimestamp | 访问用户授权的文件/目录的元数据 | — |
| `0A2A.1` | FileTimestamp | 第三方 SDK 的文件时间戳封装 | 仅 SDK 可声明;主业是封装 RRA 的 SDK 不得声明;SDK 不得自用 |
| `35F9.1` | SystemBootTime | 测量应用内经过的时间 / 定时器 | 原始开机时间及派生值不得离开设备;应用内事件的间隔可以 |
| `8FFB.1` | SystemBootTime | 计算应用内事件的绝对时间戳 | 绝对时间戳可以离开设备;原始开机时间不得 |
| `3D61.1` | SystemBootTime | 用户主动提交的错误报告含开机时间 | 必须显著展示在报告内容中;用户须主动提交 |
| `85F4.1` | DiskSpace | 向用户显示磁盘空间 | 信息不得离开设备;经用户明确许可可经本地网络发给同一人的其他设备 |
| `E174.1` | DiskSpace | 写入前检查空间 / 空间不足时删文件 | 必须有随磁盘空间变化的用户可见行为;不得离开设备 |
| `7D9E.1` | DiskSpace | 用户主动提交的错误报告含磁盘空间 | 同 3D61 |
| `B728.1` | DiskSpace | 健康研究的低空间提醒 | 应用只能提供健康研究功能;须符合审核指南 5.1.3 |
| `3EC4.1` | ActiveKeyboards | 系统级自定义键盘 App | 自定义键盘须是主功能;不得离开设备 |
| `54BD.1` | ActiveKeyboards | 按活动键盘定制 UI | 须有文本输入框;须有随键盘变化的可见行为;不得离开设备 |
| `CA92.1` | UserDefaults | App 私有的 UserDefaults | 不读其他 App/系统写的;不写其他 App 可读的 |
| `1C8F.1` | UserDefaults | 同 App Group 内共享 | 不读写 Group 外成员的 |
| `C56D.1` | UserDefaults | 第三方 SDK 的 UserDefaults 封装 | 同 0A2A |
| `AC6B.1` | UserDefaults | MDM 托管配置与反馈 | 仅 `com.apple.configuration.managed` 读 / `com.apple.feedback.managed` 写 |

**`site_class`**:`RRA` `ALT`
**`is_api_use`** / **`unit_confirmed`**:`YES` `NO` `UNSURE`
**`unit_role`**:`FIRST_PARTY` `THIRD_PARTY` `VENDORED_THIRD_PARTY` `FORK_OF_THIRD_PARTY` `UNSURE`
**`alt_equivalence`**:`NEAR_EQUIVALENT` `CONDITIONAL` `PARTIAL_DATUM`
**`exceeds_all_reasons`**:`YES` `NO` `UNKNOWN`
**`operation`**:`READ` `WRITE` `REMOVE` `OBSERVE` `SYNC` `WRAPPED` `ACQUIRE` `NA`
**`value_fate` / `sink_use`**:`LOCAL_ONLY` `UI_DISPLAY` `LOGGED` `PERSISTED_LOCAL` `STORED` `RETURNED` `PASSED_OUT` `OFF_DEVICE` `DERIVED_ID` `UNSURE`
**`escape.kind`**:`RETURNED` `PASSED_OUT` `PERSISTED_LOCAL` `STORED`
**`applicable_reason`**:理由码 | `NONE` | `UNSURE` | `NA`(ALT 站点)
**verdict 域**:`SUPPORTED` `CONFLICT` `UNKNOWN`
**`path_type`**:`RETURN_VALUE` `TRIGGER` `CHANNEL`
**`needs_context.requests[].kind`**:`DEFINITION` `CALLERS` `BODY` `TYPE` `FILE`

---

## 附录 B. 真实站点的完整标注(十例)

以下四例取自基准集真实源码(pISSStream 的依赖 Alamofire 5.10.2、Lightstreamer 6.2.0;Dai-Hentai 的 pod SDWebImage 4.0.0),行号以源文件为准。每例先给关键行,再给按本原则得出的记录。它们覆盖了:派生值逃逸(B.1)、未声明的 RRA(B.2)、单元声明了类别却只用 ALT(B.3)、键请求/取值/比较多站点(B.4)。

### B.1 Alamofire `Source/Core/DataRequest.swift:248` — SystemBootTime,已声明 35F9.1

```
248            let start = ProcessInfo.processInfo.systemUptime
249            let result: AFResult<…> = Result { try responseSerializer.serialize(…) }…
258            let end = ProcessInfo.processInfo.systemUptime
262            let response = DataResponse(request: self.request, …,
266                                        serializationDuration: end - start,
267                                        result: result)
268            self.eventMonitor?.request(self, didParseResponse: response)
270            self.responseSerializerDidComplete { queue.async { completionHandler(response) } }
```
单元清单:`Source/PrivacyInfo.xcprivacy` 声明 `SystemBootTime: [35F9.1]`。

```json
{"site_id":"…","site_class":"RRA","is_api_use":"YES",
 "is_api_use_reason":"ProcessInfo.processInfo.systemUptime;无平台守卫;非字符串",
 "unit_confirmed":"YES","unit_role":"THIRD_PARTY","declaring_unit":"Alamofire",
 "operation":"READ",
 "value_fate":["LOCAL_ONLY","PASSED_OUT"],
 "fate_evidence":"L248 start、L258 end 两次读数;L266 end - start 作为 serializationDuration 装入 DataResponse;L268 交给 eventMonitor(宿主可注册自己的监视器);L270 completionHandler(response) 交给宿主闭包。原值 start/end 仅参与相减,未离开闭包",
 "escape":{"kind":"PASSED_OUT","value":"DERIVED","target":"DataResponse.serializationDuration → completionHandler / eventMonitor(宿主单元)"},
 "applicable_reason":"35F9.1",
 "constraint_verdicts":{"R35F9_C1":"SUPPORTED","R35F9_C2":"SUPPORTED","R35F9_C3":"SUPPORTED","R35F9_C4":"SUPPORTED"},
 "alt_equivalence":null,"exceeds_all_reasons":null,"needs_context":null,
 "notes":"C1 两次读数相减求耗时;C2 可能离开设备的只有时长,属允许语义;C3 原值无逃逸;C4 时长解密条款成立。派生值进入宿主 → 第二轮生成 RETURN_VALUE 流,但不影响本站点四条约束,因为逃逸的不是 RAW"}
```
**这一例决定了 `escape.value` 字段的存在**:按 v1.1 的"有逃逸即 UNKNOWN",C3 会被误判为 UNKNOWN;实际上 35F9 明文允许时长离开设备,禁的只是原值。

### B.2 Lightstreamer `Sources/LightstreamerClient/MPNDevice.swift:219–224` — UserDefaults,**未声明**

```
219        guard let appId = UserDefaults.standard.string(forKey: "LS_appID") ?? Bundle.main.bundleIdentifier else {
222        let prevDeviceToken = UserDefaults.standard.string(forKey: "LS_deviceToken")
223        UserDefaults.standard.set(deviceToken, forKey: "LS_deviceToken")
224        UserDefaults.standard.synchronize()
226        self.applicationId = appId
227        self.deviceToken = deviceToken
228        self.previousDeviceToken = prevDeviceToken
```
单元清单:`Sources/LightstreamerClient/PrivacyInfo.xcprivacy` 只声明 `SystemBootTime: [35F9.1]`,**没有 UserDefaults**。宿主 pISSStream 无清单。

四个站点,L219 的记录:
```json
{"site_id":"…","site_class":"RRA","is_api_use":"YES",
 "is_api_use_reason":"UserDefaults.standard.string(forKey:)",
 "unit_confirmed":"YES","unit_role":"THIRD_PARTY","declaring_unit":"LightstreamerClient",
 "operation":"READ",
 "value_fate":["STORED"],
 "fate_evidence":"L219 appId ← string(forKey:\"LS_appID\") ?? bundleIdentifier;L226 self.applicationId = appId。MPNDevice 是 public 类,applicationId 的读取方不在可见行内",
 "escape":{"kind":"STORED","value":"RAW","target":"MPNDevice.applicationId(public 属性)"},
 "applicable_reason":"NONE","constraint_verdicts":{},
 "alt_equivalence":null,"exceeds_all_reasons":null,
 "needs_context":{"what":"MPNDevice.applicationId / deviceToken / previousDeviceToken 的读取方(LightstreamerClient 里构造推送设备注册报文的代码)","why":"三个值存入 public 属性,从类名(MPN = 移动推送)看很可能进入发往服务器的注册请求"},
 "notes":"单元清单未声明 UserDefaults → RQ1 缺失(SDK 级);宿主 hosts[0].declares.UserDefaults 为空 → 不属宿主代报档。域为 .standard,键为自有前缀 LS_"}
```
L222 同上但 `operation: READ`、目标 `previousDeviceToken`;L223 `operation: WRITE`(写入的是宿主传入的 deviceToken,`value_fate` 追踪的是被写入的值本身:`["PERSISTED_LOCAL"]`);L224 `operation: SYNC`,`value_fate: ["LOCAL_ONLY"]`。

### B.3 Lightstreamer `Sources/LightstreamerClient/platform/Scheduler.swift:59` — **ALT**,单元声明了被替代的类别

```
58    var now: Timestamp {
59        DispatchTime.now().uptimeNanoseconds / NSEC_PER_MSEC
60    }
```
读取方(同单元 `LightstreamerClient.swift`):`connectTs = scheduler.now`(L6442 等六处)、`recoverTs = scheduler.now`(L4715)、`diffMs = scheduler.now - connectTs`(L4756)。

```json
{"site_id":"…","site_class":"ALT","is_api_use":"YES",
 "is_api_use_reason":"DispatchTime.now().uptimeNanoseconds 读出了数值,不是单纯的 .now() 调度",
 "unit_confirmed":"YES","unit_role":"THIRD_PARTY","declaring_unit":"LightstreamerClient",
 "operation":"READ",
 "value_fate":["RETURNED"],
 "fate_evidence":"L59 计算属性直接返回 uptimeNanoseconds/NSEC_PER_MSEC —— 开机以来的毫秒数,原值",
 "escape":{"kind":"RETURNED","value":"RAW","target":"Scheduler.now → LightstreamerClient.swift connectTs/recoverTs 赋值与相减"},
 "applicable_reason":"NA","constraint_verdicts":{},
 "alt_equivalence":"NEAR_EQUIVALENT",
 "exceeds_all_reasons":"UNKNOWN",
 "needs_context":{"what":"LightstreamerClient.swift 中 connectTs / recoverTs 的全部用途,尤其是否把原值放进发往服务器的报文","why":"原值 RETURNED;可见的 L4756 只是相减,但六处赋值的下游不可见"},
 "notes":"该单元清单声明了 SystemBootTime/35F9.1,源码中却无任何 RRA 开机时间调用——声明对应的是这个 ALT 用法(declared_for_mapped_category=true),RQ2.2 正例。不给它填理由约束"}
```
**这一例说明了两件事**:ALT 站点即使单元声明了类别也不评理由;站点的 `exceeds_all_reasons` 在单元内逃逸被追完之前只能是 UNKNOWN,扩展回路不只服务跨单元。

### B.4 SDWebImage 4.0.0 `SDWebImage/SDImageCache.m:489 / 516 / 541` — FileTimestamp(ObjC pod),未声明

```
489        NSArray<NSString *> *resourceKeys = @[NSURLIsDirectoryKey, NSURLContentModificationDateKey, NSURLTotalFileAllocatedSizeKey];
516            NSDate *modificationDate = resourceValues[NSURLContentModificationDateKey];
517            if ([[modificationDate laterDate:expirationDate] isEqualToDate:expirationDate]) {
518                [urlsToDelete addObject:fileURL];
524            cacheFiles[fileURL] = resourceValues;
541                 return [obj1[NSURLContentModificationDateKey] compare:obj2[NSURLContentModificationDateKey]];
```
SDWebImage 4.0.0(2017)无清单;宿主 Dai-Hentai 无清单。

L516 的记录(L489 是同一次访问的键请求站点,`value_fate` 同此;L541 一行两个站点,`value_fate: ["LOCAL_ONLY"]`):
```json
{"site_id":"…","site_class":"RRA","is_api_use":"YES",
 "is_api_use_reason":"resourceValues[NSURLContentModificationDateKey],Foundation 全局键",
 "unit_confirmed":"YES","unit_role":"THIRD_PARTY","declaring_unit":"SDWebImage",
 "operation":"READ",
 "value_fate":["LOCAL_ONLY"],
 "fate_evidence":"L516 取值;L517 与 expirationDate 比较决定是否删除;L524 整个 resourceValues 存入本地字典 cacheFiles;L541 排序比较器再次取键比较——全部是同函数内的比较与控制流,函数结束即丢弃",
 "escape":null,
 "applicable_reason":"NONE","constraint_verdicts":{},
 "alt_equivalence":null,"exceeds_all_reasons":null,"needs_context":null,
 "notes":"缓存目录 LRU 清理;若声明应为 C617.1(diskCachePath 由 App 容器内目录拼出)。SDK 与宿主都无清单 → RQ1 缺失(SDK 级)。L524 的 cacheFiles 是函数内局部字典,不算 STORED"}
```

### B.5 Cache 6.0.0(pod)`Source/Shared/Storage/DiskStorage.swift:67 / 83` — WEAK 档全是真 RRA;时间戳 WRITE;读回自己写的值

```
64      let attributes = try fileManager.attributesOfItem(atPath: filePath)
67      guard let date = attributes[.modificationDate] as? Date else { throw … }
71      return Entry(object: object, expiry: Expiry.date(date), filePath: filePath)
83      try fileManager.setAttributes([.modificationDate: expiry.date], ofItemAtPath: filePath)
```
无清单。宿主 OutRun 的清单另查。

L67:
```json
{"site_id":"…","site_class":"RRA","is_api_use":"YES",
 "is_api_use_reason":"attributes 来自 attributesOfItem(atPath:),[.modificationDate] 是 FileAttributeKey.modificationDate",
 "unit_confirmed":"YES","unit_role":"THIRD_PARTY","declaring_unit":"Cache",
 "operation":"READ",
 "value_fate":["RETURNED"],
 "fate_evidence":"L67 取 mtime 作 date;L71 装进 Entry.expiry 返回给调用方",
 "escape":{"kind":"RETURNED","value":"RAW","target":"Entry.expiry(公开返回值)"},
 "applicable_reason":"NONE","constraint_verdicts":{},
 "alt_equivalence":null,"exceeds_all_reasons":null,
 "needs_context":null,
 "notes":"SELF_WRITTEN_VALUE:L83 把过期时间写进 mtime,这里读回的是本库自己写的过期时间,不是真实文件系统时间戳。API 层面仍是 RRA 使用。无清单 → RQ1 缺失"}
```
L83:`operation: WRITE`,`value_fate: ["PERSISTED_LOCAL"]`,`escape: null`,notes `WRITTEN_VALUE: expiry.date,本库自算的过期时间,不是设备数据`。

### B.6 swift-nio `_NIOFileSystem/Internal/System Calls/Syscalls.swift:72` — `stat` 封装,已声明 0A2A.1,自定义编译标志

```
63  func system_stat(_ path: UnsafePointer<CInterop.PlatformChar>, _ info: inout CInterop.Stat) -> CInt {
67      #if ENABLE_MOCKING
68      if mockingEnabled { return mock(path) }
71      #endif
72      return stat(path, &info)
73  }
```
单元清单 `Sources/_NIOFileSystem/PrivacyInfo.xcprivacy`:`FileTimestamp: [0A2A.1]`。`Package.swift` 未为该 target 定义 `ENABLE_MOCKING`。

```json
{"site_id":"…","site_class":"RRA","is_api_use":"YES",
 "is_api_use_reason":"stat(path, &info) C 调用;不在 ENABLE_MOCKING 分支内(该标志默认未定义)",
 "unit_confirmed":"YES","unit_role":"THIRD_PARTY","declaring_unit":"_NIOFileSystem",
 "operation":"READ",
 "value_fate":["RETURNED"],
 "fate_evidence":"L72 stat 填充 inout 参数 info,整个 struct stat 原样交回调用方;可见范围内本函数未读任何时间戳字段",
 "escape":{"kind":"RETURNED","value":"RAW","target":"inout info → 调用方(FileSystem.info(forFileAt:) 等公开 API)"},
 "applicable_reason":"0A2A.1",
 "constraint_verdicts":{"R0A2A_C1":"SUPPORTED","R0A2A_C2":"UNKNOWN","R0A2A_C3":"SUPPORTED","R0A2A_C4":"UNKNOWN","R0A2A_C5":"UNKNOWN"},
 "alt_equivalence":null,"exceeds_all_reasons":null,
 "needs_context":{"what":"system_stat 的调用方链:哪些是公开 API 被宿主调用,哪些是 NIO 内部自用(如 NonBlockingFileIO 取文件大小)","why":"C2/C4 取决于触发方是谁、NIO 自己有没有读时间戳字段"},
 "notes":"C1:第三方 SPM;C3:swift-nio 主业是网络,不是 RRA 封装;C2/C4/C5 要看调用链。TIMESTAMP_FIELDS_UNUSED 在本函数成立,但结构体整个逃逸,不能据此判 C4"}
```

### B.7 swift-nio `NIOCore/EventLoop.swift:928` — **ALT**,封装类型 `NIODeadline`

```
913      #if os(Linux)
915      clock_gettime(CLOCK_MONOTONIC, &ts)          ← Linux 分支,iOS 上死代码
927      #else
928      return DispatchTime.now().uptimeNanoseconds   ← 站点
929      #endif
933  public static func now() -> NIODeadline { NIODeadline.uptimeNanoseconds(timeNow()) }
886  public var uptimeNanoseconds: UInt64 { .init(self._uptimeNanoseconds) }
```
NIOCore 无清单(ALT 不需要)。

```json
{"site_id":"…","site_class":"ALT","is_api_use":"YES",
 "is_api_use_reason":"DispatchTime.now().uptimeNanoseconds,接收者是 DispatchTime;在 #if os(Linux) 的 #else 分支,iOS 上是活的",
 "unit_confirmed":"YES","unit_role":"THIRD_PARTY","declaring_unit":"NIOCore",
 "operation":"READ",
 "value_fate":["STORED"],
 "fate_evidence":"L928 返回给 timeNow();L934 装进 NIODeadline;L886 public var uptimeNanoseconds 可取回原值",
 "escape":{"kind":"STORED","value":"RAW","target":"NIODeadline"},
 "applicable_reason":"NA","constraint_verdicts":{},
 "alt_equivalence":"NEAR_EQUIVALENT",
 "exceeds_all_reasons":"UNKNOWN",
 "needs_context":{"what":"所有读取 NIODeadline.uptimeNanoseconds 原值访问器的位置(NIO 内部与宿主)","why":"WRAPPER_EXPOSES_RAW;比较/加减不用追,只追取回原值的地方"},
 "notes":"WRAPPER_EXPOSES_RAW。L915 的 clock_gettime(CLOCK_MONOTONIC) 是 Linux 分支,不是站点(且 CLOCK_MONOTONIC 不在附录 C)。EventLoop.swift 里另外 22 处 .uptimeNanoseconds 都是 NIODeadline 自己的成员,不是站点"}
```

### B.8 wikipedia-ios `Wikipedia/Code/AppearanceSettingsViewController.swift:181` 与 `SessionsFunnel.swift:122` — App 只声明 1C8F 却用 `.standard`;第一方封装;ALT 时长上报

App 清单 `Wikipedia/Resources/PrivacyInfo.xcprivacy`:`UserDefaults: [1C8F.1]`,`DiskSpace: [7D9E.1]`。

L181 `dimming.isImageDimmed = UserDefaults.standard.wmf_isImageDimming`:
```json
{"site_id":"…","site_class":"RRA","is_api_use":"YES",
 "is_api_use_reason":"UserDefaults.standard 家族访问;wmf_isImageDimming 是本 App 在 NSUserDefaults+WMFApplicationDefaults.swift 里加的扩展成员",
 "unit_confirmed":"YES","unit_role":"FIRST_PARTY","declaring_unit":"Wikipedia",
 "operation":"WRAPPED",
 "value_fate":["STORED"],
 "fate_evidence":"L181 读出的布尔赋给 dimming.isImageDimmed(另一个对象的属性,读取方不在可见行内)",
 "escape":{"kind":"STORED","value":"RAW","target":"dimming.isImageDimmed"},
 "applicable_reason":"1C8F.1",
 "constraint_verdicts":{"R1C8F_C1":"CONFLICT","R1C8F_C2":"…","R1C8F_C3":"…","R1C8F_C4":"…","R1C8F_C5":"…"},
 "alt_equivalence":null,"exceeds_all_reasons":null,"needs_context":null,
 "notes":"域是 .standard,不是 app group;App 只声明了 1C8F.1,未声明 CA92.1 → DefaultsDomainIs(APP_GROUP) CONFLICT。真实读写在扩展体内(隐式 self 的 bool(forKey:)),那里另有站点"}
```
(`R1C8F_C2…C5` 的具体判法随工作表给出的约束文本填,此处省略。)

`Wikipedia/Code/SessionsFunnel.swift:122` `let milliseconds = (CACurrentMediaTime() - pageLoadStartTime) * 1000`(ALT,`alt.ca_current_media_time`;函数窗口 L115–L129 全部可见):L128 `pageLoadTimes.append(milliseconds)` 存进实例属性数组,读取方不在函数内 → `value_fate: ["LOCAL_ONLY","STORED"]`,`escape: {"kind":"STORED","value":"DERIVED","target":"SessionsFunnel.pageLoadTimes"}`,notes `DERIVED_AS: 两次读数相减的毫秒时长`;`exceeds_all_reasons: "NO"`——能离开的只有时长。上报发生在同文件别的函数里,在补充目录里能看到就引用 `Wikipedia/Code/SessionsFunnel.swift L<n>`,看不到也不猜。L106 `pageLoadStartTime = CACurrentMediaTime()` 是另一个站点:`["STORED"]`,`escape.value = RAW`,但 L122 可见其唯一用途是相减,notes 写 `RAW_CONSUMED_BY_SUBTRACTION_ONLY`,`exceeds_all_reasons: "NO"`。

`WMF Framework/Third Party/FLAnimatedImage/FLAnimatedImage.m:450`(ALT):`unit_role: "VENDORED_THIRD_PARTY"`(补充目录里该文件 L1–L8 是 Flipboard 的版权头),`declaring_unit: "WMF"`(`target.name`:它编进的是仓库内的动态 framework target,不是 App 主二进制)。

### B.9 两个"判 NO / 判不定"的真实情形

**swift-nio `CNIOLinux/shim.c:231`** `if (statfs(path, &fs) == 0)` —— 文件第 19 行 `#ifdef __linux__` 包住全文。
```json
{"site_id":"…","site_class":"RRA","is_api_use":"NO",
 "is_api_use_reason":"COMPILE_GUARD_EXCLUDES_IOS_RELEASE: #ifdef __linux__ (L19,包住整个文件)",
 "unit_confirmed":"YES","unit_role":"THIRD_PARTY","declaring_unit":"CNIOLinux",
 "operation":"NA","value_fate":[],"fate_evidence":"","escape":null,
 "applicable_reason":"NA","constraint_verdicts":{},
 "alt_equivalence":null,"exceeds_all_reasons":null,"needs_context":null,
 "notes":"iOS 上不编译。swift-nio 三份清单都不声明 DiskSpace,与此一致——这不是缺失"}
```

**wikipedia-ios `WMFData/Sources/WMFData/Store/WMFUserDefaultsStore.swift:46`** `defaults.set(data, forKey: defaultsKey)` —— `defaults` 是存储属性,由 `init(defaults: UserDefaults = .standard)` 注入;构造处两个:`WMFDataEnvironment.swift:89`(默认 `.standard`)、`:99`(`group.org.wikimedia.wikipedia`)。App 清单只声明 1C8F.1。
```json
{"site_id":"…","site_class":"RRA","is_api_use":"YES",
 "is_api_use_reason":"defaults 是 UserDefaults 类型的存储属性(L5),set(_:forKey:) 是家族写入",
 "unit_confirmed":"YES","unit_role":"FIRST_PARTY","declaring_unit":"WMFData",
 "operation":"WRITE",
 "value_fate":["PERSISTED_LOCAL"],
 "fate_evidence":"L46 写入的是 L44 JSONEncoder 编出的 data(调用方传入的 Codable 值)",
 "escape":null,
 "applicable_reason":"1C8F.1",
 "constraint_verdicts":{"R1C8F_C1":{"APP_PRIVATE":"CONFLICT","APP_GROUP":"SUPPORTED"},"R1C8F_C2":"SUPPORTED","R1C8F_C3":"UNKNOWN","R1C8F_C4":"UNKNOWN","R1C8F_C5":"UNKNOWN"},
 "alt_equivalence":null,"exceeds_all_reasons":null,
 "needs_context":{"what":"两个 store 实例各自被谁用、写了哪些键","why":"MIXED_DOMAINS;C3/C4 要看具体键是否被 group 外成员读写",
                  "requests":[{"kind":"CALLERS","symbol":"WMFUserDefaultsStore.load","file":null},{"kind":"CALLERS","symbol":"WMFUserDefaultsStore.save","file":null}]},
 "notes":"MIXED_DOMAINS:同一行对 .standard 实例是 APP_PRIVATE(1C8F 不覆盖 → CONFLICT),对 group 实例是 APP_GROUP。C2:.standard 实例的参与者只有本 App,group 实例的 suite 名 group.org.wikimedia.wikipedia 在 target.app_groups 里 → 两种域都 SUPPORTED,只写一个值。C5 未触发,不要上下文。group 实例同时被 Widgets/RandomWidget.swift:83 读——第二轮 CHANNEL 流的种子(Wikipedia ↔ WidgetsExtension 两个声明单元)"}
```

## 附录 C. ALT 站点的 API 名单(数据级替代 —— 要标)

入选标准只有一条:**调用它能拿到某个 RRA 类别的同一数据,或其近似派生值**。名单核对日期 2026-09-16,依据 Apple 公开文档与 Apple 开源实现;"清单外"仅指本次核对未见于 Apple RRA 条目,不代表 Apple 承诺免申报。扫描器按此表生成 `site_class=ALT` 的站点,`api` 键即第一列。

### C.1 替代 SystemBootTime(`mach_absolute_time` / `systemUptime`)

| `api` 键 | 匹配标识 | `alt_equivalence` | 备注 |
|---|---|---|---|
| `alt.clock_gettime_nsec_np.uptime_raw` | `clock_gettime_nsec_np(CLOCK_UPTIME_RAW` | NEAR_EQUIVALENT | **Apple 明确建议的替换**;纳秒,睡眠不计 |
| `alt.clock_gettime.uptime_raw` | `clock_gettime(CLOCK_UPTIME_RAW` | NEAR_EQUIVALENT | 同一时钟,`timespec` |
| `alt.clock_gettime.uptime_raw_approx` | `…(CLOCK_UPTIME_RAW_APPROX` | NEAR_EQUIVALENT | 缓存值,可滞后数毫秒 |
| `alt.clock_gettime.monotonic_raw` | `clock_gettime(CLOCK_MONOTONIC_RAW` / `clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW` | NEAR_EQUIVALENT | 含睡眠时间;与 `mach_continuous_time` 同源 |
| `alt.clock_gettime.monotonic_raw_approx` | `…(CLOCK_MONOTONIC_RAW_APPROX` | NEAR_EQUIVALENT | 同上,缓存值 |
| `alt.mach_continuous_time` | `mach_continuous_time(` | NEAR_EQUIVALENT | ticks,含睡眠 |
| `alt.dispatch_time.uptime_nanoseconds` | **接收者是 `DispatchTime`** 的 `.uptimeNanoseconds` / `.rawValue`:`DispatchTime.now().uptimeNanoseconds`、`let t: DispatchTime = …; t.uptimeNanoseconds` | NEAR_EQUIVALENT | **`DispatchTime.now()` 本身不是站点**,只有读出数值才是;封装类型的同名成员(`NIODeadline.uptimeNanoseconds`)不是站点,见 §4.8 |
| `alt.ca_current_media_time` | `CACurrentMediaTime(` | NEAR_EQUIVALENT | Apple 文档明言其值来自 `mach_absolute_time` |
| `alt.sysctl.kern_boottime` | `KERN_BOOTTIME` / `"kern.boottime"` | CONDITIONAL | 返回**开机时刻**而非清醒时长——对指纹识别而言信息量更大;未见 Apple 免申报说明 |
| `alt.suspending_clock.now` | `SuspendingClock` 的 `.now` / `Instant` | PARTIAL_DATUM | 单调瞬时值,公开 API 不直接暴露自启动以来的数值;标 PARTIAL |
| `alt.continuous_clock.now` | `ContinuousClock` 的 `.now` / `Instant` | PARTIAL_DATUM | 同上,含睡眠 |

不是站点:`CLOCK_REALTIME`、`CLOCK_PROCESS_CPUTIME_ID`、`CLOCK_THREAD_CPUTIME_ID`、`Date()`、`CFAbsoluteTimeGetCurrent()`——它们不携带开机时间信息。

### C.2 替代 DiskSpace(`statfs` 族 / volume capacity 键)

| `api` 键 | 匹配标识 | `alt_equivalence` | 备注 |
|---|---|---|---|
| `alt.getfsstat` | `getfsstat(` | CONDITIONAL | 返回全部已挂载文件系统的块统计;基础容量口径,**不等价于** `…ForImportantUsageKey` / `…ForOpportunisticUsageKey` 的可用容量语义 |
| `alt.getmntinfo` | `getmntinfo(` / `getmntinfo_r_np(` | CONDITIONAL | 同上 |

不是站点:`getfsstat64` / `getmntinfo64`(iOS 不可用)。`Data.write` 捕获 `fileWriteOutOfSpace` 属附录 D。

### C.3 替代 FileTimestamp

**本次核对未发现任何清单外 API 能返回任意文件的真实创建/修改时间。** 这一类 ALT 站点为空——这是 RQ2 的**范围事实**(替代 API 的存在是 RQ2 的前提,哪些类别有、哪些类别没有,划定的是研究范围,不是研究结果)。`fileSizeKey`、`fileAllocatedSizeKey`、`isDirectoryKey` 等只暴露大小/类型,不暴露时间戳,属附录 D。

### C.4 替代 ActiveKeyboards(`activeInputModes`)

| `api` 键 | 匹配标识 | `alt_equivalence` | 备注 |
|---|---|---|---|
| `alt.text_input_mode.primary_language` | **接收者是 `UITextInputMode`** 的 `.primaryLanguage`(`responder.textInputMode?.primaryLanguage`) | PARTIAL_DATUM | 只暴露**当前**输入模式的语言,不能枚举全部键盘;可能为 nil。其他类型的 `.primaryLanguage` 不是站点 |

不是站点:`currentInputModeDidChangeNotification`(事件,无数据)、`needsInputModeSwitchKey`(布尔)、`Locale.preferredLanguages`、`Bundle.preferredLocalizations`(语言偏好,不是键盘)。

### C.5 替代 UserDefaults

| `api` 键 | 匹配标识 | `alt_equivalence` | 备注 |
|---|---|---|---|
| `alt.cfpreferences.app` | `CFPreferencesCopyAppValue(` / `CFPreferencesSetAppValue(` / `CFPreferencesAppSynchronize(` / `CFPreferencesCopyMultiple(` / `CFPreferencesSetMultiple(` | CONDITIONAL | 与 `UserDefaults` 读写**同一个偏好存储**;未见 Apple 免申报说明 |
| `alt.cfpreferences.domain` | `CFPreferencesCopyValue(` / `CFPreferencesSetValue(` / `CFPreferencesSynchronize(` | CONDITIONAL | 可指定任意偏好域——`operation` 字段照 UserDefaults 的规则填 READ/WRITE,`notes` 记域参数 |

`CFPreferences*` 的 `operation` 按函数名判:`Copy*` → READ,`Set*` → WRITE(值为 NULL 即删除 → REMOVE),`*Synchronize` → WRITE。

---

## 附录 D. 不生成站点的"用途级替代"与"伪替代"

标注方看到这些**不要**标成站点,也不要在 `notes` 里当作 ALT 记录;它们的存在说明"这段代码换了方式做同一件事",但没有暴露 RRA 数据。

**用途级替代(不暴露数据)**

| 类别 | API |
|---|---|
| SystemBootTime | `Timer.scheduledTimer`、`DispatchQueue.asyncAfter`、`DispatchTime.now()`(未读数值)、`Date()` / `Date.now`、`CFAbsoluteTimeGetCurrent()` |
| FileTimestamp | `FileManager.fileExists(atPath:)` 及 `isDirectory:` 变体、`URL.checkResourceIsReachable()`、`resourceValues` 读 `.fileSizeKey` / `.fileAllocatedSizeKey` / `.totalFileAllocatedSizeKey` / `.isRegularFileKey` / `.isDirectoryKey` / `.isSymbolicLinkKey`、`isReadableFile` / `isWritableFile` / `isExecutableFile`、`DispatchSource.makeFileSystemObjectSource`、`NSFilePresenter.presentedItemDidChange`、自存 `createdAt` / `updatedAt` 业务字段 |
| DiskSpace | `Data.write(to:options:)` 捕获 `CocoaError.fileWriteOutOfSpace` |
| ActiveKeyboards | `currentInputModeDidChangeNotification`、`needsInputModeSwitchKey`、`Locale.preferredLanguages`、`Bundle.preferredLocalizations` |
| UserDefaults | `PropertyListEncoder` / `Decoder` + 文件、`JSONEncoder` / `Decoder` + 文件、Core Data `NSManagedObjectContext`、SwiftData `ModelContext`、SQLite `sqlite3_*`、Keychain `SecItem*`、`NSUbiquitousKeyValueStore`、`FileManager.containerURL(forSecurityApplicationGroupIdentifier:)` |

**伪替代(仍是 RRA,按 `site_class=RRA` 标)**

| 说法 | 事实 |
|---|---|
| `stat` → `fstat` / `lstat` / `fstatat` / `getattrlist` | 全在清单内 |
| `statfs` → `statvfs` / `fstatfs` / `fstatvfs` | 全在清单内 |
| `resourceValues(forKeys:)` 读 `.creationDateKey` / `.contentModificationDateKey` / 四个 volume capacity 键 | 键本身在清单内,读取方式不改变这一点 |
| Swift 名 ↔ `NS…` ObjC 名 | 同一 API |
| `@AppStorage` | Apple 明言它反映 `UserDefaults` 的值,按 UserDefaults 标 |

**迁移期的双份使用**:一个版本里既有 `UserDefaults` 读旧值又有新存储写新值,`UserDefaults` 那处仍是 RRA 站点,照标;不因"正在迁移"而降级。
