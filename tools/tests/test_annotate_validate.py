#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The two holes the first full annotation pass went through, pinned so they cannot reopen:
an empty flow layer that the 1.9 validator accepted, and evidence that cites a line without
quoting it (which no amount of source checking can refute).  Also the guard-liveness override
and the three §5 duties, each with the record that satisfies it and the record that does not."""
import io
import json
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import annotate_validate as V  # noqa: E402
import recheck_annotations as R  # noqa: E402

SIG_PUB = "public func duration() -> TimeInterval {"
#: 批次里上下文窗口的真实形态：行号右对齐到 5 位，站点那一行带 `>>`。check_quotes 拿它当
#: 「文件原文」用，所以夹具必须是真的窗口，否则那条检查在测试里根本没跑。
_SRC = {11: SIG_PUB, 12: "    let t = ProcessInfo.processInfo.systemUptime",
        13: '    os_log("%f", t)', 14: "}"}
CTX = [f"{n:5d}{'>>' if n == 12 else '  '} {_SRC.get(n, f'// {n}')}" for n in range(1, 21)]


def batch(tmp, name, unit_location, sites):
    header = {"_batch": name, "unit_location": unit_location, "source_dir": f"src/{unit_location}",
              "n_sites": len(sites), "site_ids": [s["site_id"] for s in sites],
              "reasons": {"35F9.1": {"category": "SystemBootTime", "constraints": [
                  {"id": "R35F9_C1", "type": "REQUIREMENT", "predicate": "MeasuresElapsedTime"}]}}}
    p = tmp / f"{name}.jsonl"
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(header, ensure_ascii=False) + "\n")
        for s in sites: f.write(json.dumps(s, ensure_ascii=False) + "\n")
    return p


def site(sid, **kw):
    s = {"site_id": sid, "file": "Source/Timing.swift", "line": 12, "col": 9, "api": "process_info.system_uptime",
         "category": "SystemBootTime", "site_class": "RRA", "declared_reasons": {}, "operation_prefill": "READ",
         "domain_hint": None, "key": None, "wrapper_ref": None, "callers": None, "guard_live_on_ios": True,
         "instance_domains": None, "unit_role_prefill": "THIRD_PARTY", "declaring_unit_prefill": "Timing",
         "enclosing_function": SIG_PUB, "compile_guard": [], "context": {"start_line": 1, "site_line": 12,
         "function_lines": [11, 14], "truncated": False, "lines": CTX}}
    s.update(kw); return s


def rec(sid, **kw):
    r = {"site_id": sid, "site_class": "RRA", "is_api_use": "YES",
         "is_api_use_reason": "L12: let t = ProcessInfo.processInfo.systemUptime", "unit_confirmed": "YES",
         "unit_role": "THIRD_PARTY", "declaring_unit": "Timing", "operation": "READ", "value_fate": ["LOCAL_ONLY"],
         "fate_evidence": "L12: let t = ProcessInfo.processInfo.systemUptime", "escape": None,
         "applicable_reason": "NONE", "constraint_verdicts": {}, "alt_equivalence": None,
         "exceeds_all_reasons": None, "needs_context": None, "flows": [], "notes": "TERMINAL: L13: os_log(\"%f\", t)"}
    r.update(kw); return r


def write(tmp, name, recs):
    p = tmp / name
    with io.open(p, "w", encoding="utf-8") as f:
        for r in recs: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def errs(bp, op):
    return V.validate(bp, op)[1]


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="av_"))
    try:
        # ---- §5 duty 1: every YES site of a pods unit owes a TRIGGER record
        bp = batch(tmp, "b0001__pods__Timing@1.0.0", "pods/Timing@1.0.0", [site("a" * 16)])
        op = write(tmp, "no_flow.jsonl", [rec("a" * 16)])
        e = errs(bp, op)
        assert any("TRIGGER" in x for x in e), e
        assert not V.validate(bp, op, flow_rules=False)[1], "1.9 的口径本来就放过了这种记录，测试要能复现这一点"
        # the searched-and-found-nothing record satisfies the duty
        nf = {"path_type": "TRIGGER", "sink_unit": None, "hops": [], "sink_use": [], "sink_value": None,
              "sink_declared": None, "verdict": "UNKNOWN", "evidence": "",
              "stuck_at": {"hop": 0, "why": "NO_CONSUMER_FOUND: grep duration( in src/repos/*"}, "notes": ""}
        assert not errs(bp, write(tmp, "nf.jsonl", [rec("a" * 16, flows=[nf])])), errs(bp, write(tmp, "nf.jsonl", [rec("a" * 16, flows=[nf])]))

        # ---- §5 duty 2: a write with a resolved key owes a CHANNEL record
        s2 = site("b" * 16, key={"expr": "kLast", "value": "lastSync", "source": "CONST"}, operation_prefill="WRITE")
        bp2 = batch(tmp, "b0002__repos__demo-app", "repos/demo-app", [s2])
        r2 = rec("b" * 16, operation="WRITE", value_fate=["PERSISTED_LOCAL"],
                 fate_evidence="L12: defaults.set(now, forKey: kLast)", notes="WRITTEN_VALUE: now")
        assert any("CHANNEL" in x for x in errs(bp2, write(tmp, "w.jsonl", [r2]))), errs(bp2, write(tmp, "w.jsonl", [r2]))
        # ---- a repo READ that owes nothing still has to say where the value stops
        s3 = site("c" * 16)
        bp3 = batch(tmp, "b0003__repos__demo-app", "repos/demo-app", [s3])
        assert not errs(bp3, write(tmp, "t.jsonl", [rec("c" * 16)]))                       # notes 有 TERMINAL:
        bare = rec("c" * 16, notes="读了一个值")
        assert any("停在这里" in x for x in errs(bp3, write(tmp, "b.jsonl", [bare]))), errs(bp3, write(tmp, "b.jsonl", [bare]))

        # ---- quoted evidence: a verdict backed by a paraphrase is rejected, the same verdict with the line is not
        s4 = site("d" * 16, declared_reasons={"SystemBootTime": ["35F9.1"]})
        bp4 = batch(tmp, "b0004__repos__demo-app", "repos/demo-app", [s4])
        para = rec("d" * 16, applicable_reason="35F9.1", constraint_verdicts={"R35F9_C1": "SUPPORTED"},
                   is_api_use_reason="SystemBootTime READ at L12 in Timing.swift.",
                   fate_evidence="L12 reads system boot time, used locally.", notes="TERMINAL: L13 logs it")
        assert any("代码片段" in x for x in errs(bp4, write(tmp, "p.jsonl", [para]))), errs(bp4, write(tmp, "p.jsonl", [para]))
        good = rec("d" * 16, applicable_reason="35F9.1", constraint_verdicts={"R35F9_C1": "SUPPORTED"},
                   fate_evidence="L12: let t = ProcessInfo.processInfo.systemUptime;L13: os_log(\"%f\", t)",
                   notes="TERMINAL: L13: os_log")
        assert not errs(bp4, write(tmp, "g.jsonl", [good])), errs(bp4, write(tmp, "g.jsonl", [good]))
        assert not V.validate(bp4, write(tmp, "p.jsonl", [para]), quoted_evidence=False)[1]   # 1.9 的口径放过复述

        # ---- 跨文件证据：§1 第 3 条的 `<路径> L<n>: <片段>` 与 hops 同形的 `<路径>:<n>（原文 …）`
        # 都不能拿站点自己的上下文窗口去比 —— 那个行号属于别的文件。第二轮标注被这个假错
        # 逼得只能改写证据形式，这里钉死两种形式都放行。
        xf = rec("d" * 16, applicable_reason="35F9.1", constraint_verdicts={"R35F9_C1": "SUPPORTED"},
                 fate_evidence="L12: let t = ProcessInfo.processInfo.systemUptime;"
                               "Other/Helper.swift L9001: let start = clock()",
                 notes='TERMINAL: L13: os_log;key=kLast @ Store/Keys.swift:4207（原文 static let kLast = "lastSync"）')
        assert not errs(bp4, write(tmp, "xf.jsonl", [xf])), errs(bp4, write(tmp, "xf.jsonl", [xf]))
        # 行号确实指站点自己的文件时，窗口比对照旧生效 —— 屏蔽不能把真错也放过
        wrong2 = rec("d" * 16, applicable_reason="35F9.1", constraint_verdicts={"R35F9_C1": "SUPPORTED"},
                     fate_evidence="L12: let t = CACurrentMediaTime()", notes="TERMINAL: L13: os_log")
        assert any("对不上" in x for x in errs(bp4, write(tmp, "w2.jsonl", [wrong2]))), errs(bp4, write(tmp, "w2.jsonl", [wrong2]))
        # `key=X @ a/B.swift L27` 里的文件名不该被连着 `X @ ` 一起吞掉
        m = R.RE_FILE_CITE.search("key=kLast @ Store/Keys.swift L27: static let kLast")
        assert m and m.group("path") == "Store/Keys.swift" and m.group("line") == "27", m and m.groupdict()

        # ---- guard liveness is a computed fact, not a hint
        s5 = site("e" * 16, guard_live_on_ios=False, compile_guard=["#if DEBUG"])
        bp5 = batch(tmp, "b0005__repos__demo-app", "repos/demo-app", [s5])
        assert any("GUARD_OVERRIDE" in x for x in errs(bp5, write(tmp, "gd.jsonl", [rec("e" * 16)])))
        ov = rec("e" * 16, notes="TERMINAL: L13: os_log;GUARD_OVERRIDE: DEBUG 在 Release 的 xcconfig 里也定义了")
        assert not errs(bp5, write(tmp, "ov.jsonl", [ov])), errs(bp5, write(tmp, "ov.jsonl", [ov]))
        no_ = rec("e" * 16, is_api_use="NO", is_api_use_reason="COMPILE_GUARD_EXCLUDES_IOS_RELEASE: #if DEBUG",
                  operation="NA", value_fate=[], fate_evidence="", applicable_reason="NA", notes="")
        assert not errs(bp5, write(tmp, "n.jsonl", [no_])), errs(bp5, write(tmp, "n.jsonl", [no_]))

        # ---- recheck against the corpus: the quoted fragment has to be on that line
        src = tmp / "src" / "repos" / "demo-app" / "Source"; src.mkdir(parents=True)
        (src / "Timing.swift").write_text("\n".join(
            [f"// {i}" for i in range(1, 12)] + ["    let t = ProcessInfo.processInfo.systemUptime", "    os_log(\"%f\", t)"]
        ) + "\n", encoding="utf-8")
        corpus = R.Corpus(tmp / "src")
        probs = []; tally = __import__("collections").Counter()
        R.check_citations(corpus, "repos/demo-app", s4, good, probs, tally)
        assert not probs, probs
        assert tally["cites"] >= 3 and tally["with_fragment"] >= 3, dict(tally)
        probs = []; tally2 = __import__("collections").Counter()
        R.check_citations(corpus, "repos/demo-app", s4, para, probs, tally2)
        assert not probs and tally2["with_fragment"] == 0, (probs, dict(tally2))   # 复述：无处可核
        wrong = dict(good); wrong["fate_evidence"] = "L12: let t = CACurrentMediaTime()"
        probs = []
        R.check_citations(corpus, "repos/demo-app", s4, wrong, probs, None)
        assert probs and "没有引用的片段" in probs[0][2], probs
        far = dict(good); far["fate_evidence"] = "L900: let t = ProcessInfo.processInfo.systemUptime"
        probs = []
        R.check_citations(corpus, "repos/demo-app", s4, far, probs, None)
        assert probs and "只有 13 行" in probs[0][2], probs

        print("PASS  §5 三种欠账（TRIGGER / CHANNEL / 说明值停在哪）+ NO_CONSUMER_FOUND 记账 + 证据必须引原文 + "
              "守卫事实要显式推翻 + 片段与源码逐字比对 + 跨文件证据两种写法都放行；并复现 1.9 的口径确实放过前两类")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
