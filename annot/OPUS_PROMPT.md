# 交给标注模型的任务说明(每个对话开头附上)

每个对话里你会收到三样东西:

1. `annot/ANNOTATION_PRINCIPLES.md` —— 标注原则 1.9,是唯一的判定依据。先完整读一遍,再开始。
2. `all_batches.zip` —— 全部批次。解开后每个 `b####__<unit>.jsonl` 是一个批次:第一行是批次头(单元级事实、工程文件事实、理由约束、要输出的 `site_ids` 顺序、直接相关的文件清单),之后每行一个站点;`MANIFEST.json` 是批次顺序与去重映射。
3. `src_all.tgz.00`、`src_all.tgz.01`、… —— **整个语料的源码**,按 28 MB 切开的同一个 tar.gz。先拼回去再解开:

```bash
cat src_all.tgz.* > src_all.tgz && tar xzf src_all.tgz && unzip -q all_batches.zip
ls src/            # repos/ deps/ pods/ —— 与批次头 unit_location 同名;SOURCE_INDEX.txt 列出每个文件的 sha1
```

`src/<unit_location>/<file>` 就是站点记录里 `file` 字段指的文件。范围与扫描器一致:源码后缀加清单、`Package.swift`、podspec、`project.pbxproj`、xcconfig、entitlements;测试目录、构建产物、依赖的示例工程不在其中。`src/` 之外的文件不存在。

## 工作方式:自己连续做完,结果写文件,不等指令

1. 解开材料后,按 `all_batches/MANIFEST.json` 的顺序**连续**处理批次,不要停下来等确认。每个批次的结果写到 `out/<批次文件名去掉 .jsonl>.out.jsonl`(例如 `out/b0001__repos__0xCUB3-wBlock.out.jsonl`),**每个站点恰好一行**,顺序与批次头的 `site_ids` 一致,字段按原则 §3;`site_id` 原样回传。批次头有 `n_sites` 个站点,文件里就有 `n_sites` 行。
2. 每写完一个批次,先跑校验器:`python3 all_batches/tools/annotate_validate.py --batches all_batches out/<该文件>`。有 ERR 就改到 0 个 ERR 再进入下一批;WARN 看一眼,该改的改,不该改的在 notes 里说明。
3. 每个批次结束后向 `out/PROGRESS.jsonl` 追加一行:`{"batch": "…", "n": 40, "yes": …, "no": …, "flows": …, "needs_context": …, "errors": 0, "warnings": …}`;标注过程中发现原则没说清的地方,随时追加到 `out/NOTES.md`(情形、涉及 site_id、原则哪一节、你按什么判),不要攒到最后。
4. 聊天里每个批次只打印**一行**进度(`b0001 … n=40 YES=… NO=… flows=… ERR=0`),不要把 JSONL 贴到聊天里。
5. 做完全部批次,或者你判断本对话快撑不住了(上下文将满),把 `out/` 整个打成 zip 交给我(用你环境里交付文件的方式),并说明做到了哪个批次。下一个对话我会把这个 zip 和材料一起传回来,你从 `out/PROGRESS.jsonl` 之后的批次继续。
6. 不要输出解释、前言、总结或 Markdown 表格;需要说的写进 `out/NOTES.md`。

每行的字段(缺一不可;不适用的填原则规定的 `NA` / `null` / `{}`):

```
site_id, site_class, is_api_use, is_api_use_reason, unit_confirmed, unit_role, declaring_unit,
operation, value_fate, fate_evidence, escape, applicable_reason, constraint_verdicts,
alt_equivalence, exceeds_all_reasons, needs_context, flows, notes
```

## 五条硬规则(原则 §1 的复述,违反任何一条整批打回)

1. 只写你在批次和 `src/` 里能看到的。看不到的填 `UNSURE` / `UNKNOWN`,并在 `needs_context` 写清要什么(§4.6 的结构化请求)。不要猜。站点字段只描述本单元;值出了单元之后的事逐跳写进 `flows`(§5),引用写 `<unit_location>/<文件> L<n>`。
2. 输入 N 条,输出 N 条,`site_id` 不许改、不许合并、不许跳过。
3. 每个 `SUPPORTED` / `CONFLICT` 都要有证据:站点文件的行写 `L<行号>: <片段>`,本单元别的文件写 `<文件路径> L<行号>: <片段>`,别的单元写 `<unit_location>/<文件路径> L<行号>: <片段>`,单元级事实写字段(`callers: n=11 全部 APP_PRIVATE`、`key=… @ …`、`target.app_groups=…`)。
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

值在可见范围内进了网络 / UI / 日志 / 持久化就是终点:标对应 fate,`escape` 填 `null`;`escape` 用于值离开函数的情形(原则 §4.3),`target` 写 `<接收单元>::<类型>.<成员>`,`symbols` 列出你 grep 过的标识符。**派生值(布尔、差值)离开函数也是逃逸**,`escape.value: "DERIVED"`,notes `DERIVED_AS:`。

**流(§5)与站点同一轮标。** 值出了本单元就顺着 `src/` 走到接收单元,逐行看它拿值做了什么,每一条写进 `flows`:`path_type`(RETURN_VALUE / TRIGGER / CHANNEL)、`sink_unit`、逐跳的 `hops`(每跳 unit / symbol / `<文件>:<行>`)、`sink_use`、`sink_value`、`sink_declared`、`verdict`、`evidence`、`stuck_at`。要查的范围不打折:SDK 站点看批次头 `hosts` 里的**每一个**宿主(`owner/name` → `src/repos/owner-name/`);deps / pods 里每个 YES 站点都要找 TRIGGER(公开入口 ← 站点,再到宿主的调用行);WRITE 站点的键值先在 `all_batches/*.jsonl` 里 grep `"value": "<键>"` 找别的单元的 READ 站点,再 grep `src/`。查过没找到也要记一条 `sink_unit: null` + `stuck_at.why: NO_CONSUMER_FOUND: …`。没有逃逸 `flows: []`。站点级字段不因流的结果改判。

`needs_context` 只在 `src/` 里也找不到、且有字段等着定稿时填(整个语料的源码都给了,它应当很少;跨单元的追踪不是 needs_context,是 flows),而且要写成工具能取的请求:`{"what": "…", "why": "…", "requests": [{"kind": "DEFINITION|CALLERS|BODY|TYPE|FILE", "symbol": "…", "file": "…或 null"}]}`。EXCEPTION 类约束未触发的 UNKNOWN 不要上下文。

## 一个输出行的样子(原则附录 B.1 的 Alamofire 例子,压成一行)

```json
{"site_id":"…","site_class":"RRA","is_api_use":"YES","is_api_use_reason":"ProcessInfo.processInfo.systemUptime;无平台守卫;非字符串","unit_confirmed":"YES","unit_role":"THIRD_PARTY","declaring_unit":"Alamofire","operation":"READ","value_fate":["LOCAL_ONLY","PASSED_OUT"],"fate_evidence":"L248 start、L258 end 两次读数;L266 end - start 作为 serializationDuration 装入 DataResponse;L268 交给 eventMonitor;L270 completionHandler(response) 交给宿主闭包。原值 start/end 仅参与相减,未离开闭包","escape":{"kind":"PASSED_OUT","value":"DERIVED","target":"HOST::completionHandler(DataResponse)","symbols":["DataResponse","serializationDuration","completionHandler","eventMonitor"]},"applicable_reason":"35F9.1","constraint_verdicts":{"R35F9_C1":"SUPPORTED","R35F9_C2":"SUPPORTED","R35F9_C3":"SUPPORTED","R35F9_C4":"SUPPORTED"},"alt_equivalence":null,"exceeds_all_reasons":null,"needs_context":null,"flows":[{"path_type":"RETURN_VALUE","sink_unit":"pISSStream","hops":[{"unit":"Alamofire","symbol":"DataResponse.serializationDuration","loc":"Source/Core/DataRequest.swift:266"},{"unit":"pISSStream","symbol":"<宿主读 response 的函数>","loc":"<文件>:<行>"}],"sink_use":["LOCAL_ONLY"],"sink_value":"DERIVED","sink_declared":false,"verdict":"SUPPORTED","evidence":"repos/<owner-name>/<文件> L<n>: <片段>","stuck_at":null,"notes":""}],"notes":"C1 两次读数相减求耗时;C2 可能离开设备的只有时长;C3 原值无逃逸;C4 时长解密条款成立;DERIVED_AS: 两次读数之差"}
```
