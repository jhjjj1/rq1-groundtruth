# 交给标注模型的任务说明(随每个批次附上)

你会收到一个批次包 `all_batches.zip`,里面有:

1. `annot/ANNOTATION_PRINCIPLES.md` —— 标注原则 1.9,是唯一的判定依据。先完整读一遍,再开始。
2. 批次文件 `b####__<unit>.jsonl` —— 第一行是批次头(单元级事实、工程文件事实、理由约束、要输出的 `site_ids` 顺序、本批次可用的源文件清单),之后每行一个站点。
3. `src/<unit_location>/…` —— 源码补充目录。批次头 `source_files` / `unit_files` 列出的文件(站点所在的完整源文件、被调扩展成员的定义文件、实例构造处、键常量、清单、`Package.swift` / podspec / `project.pbxproj`)都在这里,可以打开。**清单之外的文件不存在。**
4. `MANIFEST.json` —— 批次清单与去重映射,你不用读。

## 你要输出什么

每个批次一个 JSONL 文本块,**每个站点恰好一行**,顺序与批次头的 `site_ids` 一致,字段按原则 §3;`site_id` 原样回传。代码块第一行是 `# <批次文件名>`。不要输出解释、前言、总结或 Markdown 表格——只有 JSONL。批次头有 `n_sites` 个站点,你就输出 `n_sites` 行。

每行的字段(缺一不可;不适用的填原则规定的 `NA` / `null` / `{}`):

```
site_id, site_class, is_api_use, is_api_use_reason, unit_confirmed, unit_role, declaring_unit,
operation, value_fate, fate_evidence, escape, applicable_reason, constraint_verdicts,
alt_equivalence, exceeds_all_reasons, needs_context, notes
```

## 五条硬规则(原则 §1 的复述,违反任何一条整批打回)

1. 只写你在批次和补充目录里能看到的。看不到的填 `UNSURE` / `UNKNOWN`,并在 `needs_context` 写清要什么(§4.6 的结构化请求)。不要猜。
2. 输入 N 条,输出 N 条,`site_id` 不许改、不许合并、不许跳过。
3. 每个 `SUPPORTED` / `CONFLICT` 都要有证据:站点文件的行写 `L<行号>: <片段>`,补充目录里别的文件的行写 `<文件路径> L<行号>: <片段>`,单元级事实写字段(`callers: n=11 全部 APP_PRIVATE`、`key=… @ …`、`target.app_groups=…`)。
4. 枚举字段只能取原则附录 A 的值,大小写一致。
5. `SUPPORTED` 要正面证据,`CONFLICT` 要明确反证,两者都没有就是 `UNKNOWN`。

## 批次里脚本预填的字段怎么用

`hint` / `operation_prefill` / `domain_hint` / `instance_domains` / `guard_live_on_ios` / `build_flags` / `unit_role_prefill` / `declaring_unit_prefill` 是脚本给的**事实或提示**:同意就照填,不同意就按你看到的填并在 `notes` 说明理由。`site_class` 和 `declared_for_mapped_category` 不要改(原则 §0.1、§4.7)。

三个链接字段是这一版新加的,先看它们再决定要不要开文件:

- `wrapper_ref`(WRAPPED 站点):所调扩展成员的定义位置、getter/setter、`access`(GET / SET / CALL)、成员体内读写的键。`SET` → `value_fate: ["PERSISTED_LOCAL"]` + notes `WRITTEN_VALUE:`。
- `callers`(扩展体站点):调用该成员的全部 WRAPPED 站点及各自的域。`by_domain` 只有一种 → 域就是它;两种 → MIXED_DOMAINS 字典;`n: 0` → UNKNOWN + notes `NO_CALLERS_IN_SCOPE`。
- `key`(直接读写站点):`forKey:` 实参解析出的字符串。`NoReadFrom` 类约束看这个,不看常量名。

`constraint_verdicts` 的键从批次头 `reasons[<理由码>].constraints[*].id` 取,站点 `declared_reasons` 里有几个理由码就看几个;`declared_reasons` 为空 → `applicable_reason: "NONE"`,`constraint_verdicts: {}`。

`is_api_use` 为 `NO` 的站点:`operation: "NA"`、`value_fate: []`、`fate_evidence: ""`、`escape: null`、`applicable_reason: "NA"`、`constraint_verdicts: {}`,`is_api_use_reason` 以原则 §4.1 表里的代码开头(`COMPILE_GUARD_EXCLUDES_IOS_RELEASE` / `NAME_COLLISION` / `WRAPPER_CALL` / `DECLARATION` / `STRING_LITERAL` / `WRAPPER_TYPE_MEMBER` / `OTHER`),再写说明;ALT 站点判 NO 时 `alt_equivalence` 照附录 C 填、`exceeds_all_reasons: null`。

没有可追数据值的操作(SYNC / ACQUIRE / REMOVE / OBSERVE)`value_fate: ["LOCAL_ONLY"]`、`escape: null`、notes `NO_VALUE`。

值在可见范围内进了网络 / UI / 日志 / 持久化就是终点:标对应 fate,`escape` 填 `null`;`escape` 只用于值离开函数而去向不可见的情形(原则 §4.3)。**派生值(布尔、差值)离开函数也是逃逸**,`escape.value: "DERIVED"`,notes `DERIVED_AS:`。

`needs_context` 只在补充目录里也找不到、且有字段等着定稿时填,而且要写成工具能取的请求:`{"what": "…", "why": "…", "requests": [{"kind": "DEFINITION|CALLERS|BODY|TYPE|FILE", "symbol": "…", "file": "…或 null"}]}`。EXCEPTION 类约束未触发的 UNKNOWN 不要上下文。

## 一个输出行的样子(原则附录 B.1 的 Alamofire 例子,压成一行)

```json
{"site_id":"…","site_class":"RRA","is_api_use":"YES","is_api_use_reason":"ProcessInfo.processInfo.systemUptime;无平台守卫;非字符串","unit_confirmed":"YES","unit_role":"THIRD_PARTY","declaring_unit":"Alamofire","operation":"READ","value_fate":["LOCAL_ONLY","PASSED_OUT"],"fate_evidence":"L248 start、L258 end 两次读数;L266 end - start 作为 serializationDuration 装入 DataResponse;L268 交给 eventMonitor;L270 completionHandler(response) 交给宿主闭包。原值 start/end 仅参与相减,未离开闭包","escape":{"kind":"PASSED_OUT","value":"DERIVED","target":"DataResponse.serializationDuration → completionHandler / eventMonitor(宿主单元)"},"applicable_reason":"35F9.1","constraint_verdicts":{"R35F9_C1":"SUPPORTED","R35F9_C2":"SUPPORTED","R35F9_C3":"SUPPORTED","R35F9_C4":"SUPPORTED"},"alt_equivalence":null,"exceeds_all_reasons":null,"needs_context":null,"notes":"C1 两次读数相减求耗时;C2 可能离开设备的只有时长;C3 原值无逃逸;C4 时长解密条款成立;DERIVED_AS: 两次读数之差"}
```
