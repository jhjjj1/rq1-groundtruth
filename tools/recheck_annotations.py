#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Re-check a finished annotation pass against the batches and the corpus source.

    recheck_annotations.py --batches DIR [--batches DIR …] --out DIR --src DIR
                           [--json report/recheck.json] [--flow-rules on|off]

annotate_validate.py checks a record against itself and against its batch.  This
checks it against **the code**, which is the part an annotator can get wrong
without any field contradicting another:

  1. every `L<n>` cited in `fate_evidence` / `notes` / `is_api_use_reason` is a
     real line of the site's own file, and a citation written `L<n>: <片段>`
     actually has that fragment on that line (whitespace-normalised, and the
     fragment may be a prefix -- annotators elide the tail);
  2. every `<unit>/<file> L<n>` and every flow hop `<file>:<line>` resolves to
     a line that exists in `src/`;
  3. the §5 duties (TRIGGER / CHANNEL / RETURN_VALUE) are counted per unit
     tree, so "owed" and "met" are reported rather than assumed;
  4. CHANNEL candidates are computed here instead of grepped by hand: every
     key written in one unit is looked up in every other unit's read sites, so
     the cross-unit pairs the annotator was supposed to find are on record --
     a found pair with no flow record is a hole, and a flow record with no
     pair is something to look at;
  5. the escape claim is compared with what the enclosing signature allows: a
     site inside a `public` / `open` / `@objc` member of a deps / pods unit
     whose record says the value never leaves is listed as UNDER_REPORTED --
     not an error (the value may genuinely die in the body) but a population
     to sample.

Nothing here rewrites an annotation.  It produces counts and a list of sites
to look at again, so that "not observed" and "not looked at" stop being the
same bytes.
"""

import argparse
import collections
import io
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import annotate_validate as V  # noqa: E402

#: `L123` on its own, or `L123: <片段>` up to the next clause separator
RE_CITE = re.compile(r"(?:^|[\s（(；;，,、])L(\d+)\b(?:\s*[:：]\s*(.*?)(?=[;；。]|$))?")
#: A citation that names a file: `<path> L<n>: <片段>` (§1 rule 3) or `<path>:<n>（原文 <片段>）`
#: (the same shape as `flows[].hops[].loc`).  Both forms are accepted.
#:
#: The path may not contain spaces -- with a space in the class, `key=X @ a/B.swift L27`
#: swallowed `X @ a/B.swift` as the file name.  `@` stays, because unit directories carry it
#: (`deps/swift-nio@558f24a46471/...`).
RE_FILE_CITE = re.compile(
    r"(?P<path>[A-Za-z0-9_@./+\-]*[A-Za-z0-9_@+\-]\.[A-Za-z0-9_]+)"
    r"(?:\s+L|\s*[:：])\s*(?P<line>\d+)"
    r"(?:\s*[:：]\s*(?P<frag1>[^;；。\n]*)|\s*[（(]\s*原文\s*(?P<frag2>[^）)\n]*)[）)])?")
RE_HOP = re.compile(r"^(.*):(\d+)$")
VIS_PUBLIC = re.compile(r"\b(public|open)\b|@objc")


class Corpus:
    """src/<unit_location>/<file>, read once and kept as a line count plus the lines we touch."""

    def __init__(self, src):
        self.src = pathlib.Path(src)
        self._lines = {}
        self.missing = set()

    def lines(self, unit_location, rel):
        key = (unit_location, rel)
        if key not in self._lines:
            p = self.src / unit_location / rel
            try:
                self._lines[key] = io.open(p, encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                self._lines[key] = None
                self.missing.add(f"{unit_location}/{rel}")
        return self._lines[key]

    def line(self, unit_location, rel, n):
        ls = self.lines(unit_location, rel)
        if ls is None: return None
        return ls[n - 1] if 1 <= n <= len(ls) else ""


def norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip().strip("`\"'…").lower()


def check_citations(corpus, unit_location, site, rec, problems, tally=None):
    """Every line number the record cites has to exist; a cited fragment has to be on that line.

    `tally` counts citations and how many of them carry a fragment: a bare `L123` names a line
    but quotes nothing, so nothing about it can be checked against the code -- §1 rule 3 asks for
    `L<n>: <片段>` precisely so that the evidence is falsifiable.
    """
    own = site["file"]
    for field in ("fate_evidence", "notes", "is_api_use_reason"):
        text = rec.get(field) or ""
        if not isinstance(text, str): continue
        # `<file> L<n>: …` / `<file>:<n>（原文 …）` -- another file of this unit, or
        # `<unit_location>/<file>` of another unit.  Each match is blanked out of `text`
        # afterwards: a line number that belongs to another file must not then be checked
        # against the site's own file, which is how a correct cross-file citation was being
        # reported as "this file only has N lines".
        masked = list(text)
        for m in RE_FILE_CITE.finditer(text):
            raw, n = m.group("path").strip(), int(m.group("line"))
            frag = m.group("frag1") or m.group("frag2")
            masked[m.start():m.end()] = " " * (m.end() - m.start())
            loc, rel = unit_location, raw
            for tree in ("repos/", "deps/", "pods/"):
                if raw.startswith(tree):
                    parts = raw.split("/", 2)
                    if len(parts) == 3: loc, rel = "/".join(parts[:2]), parts[2]
                    break
            ls = corpus.lines(loc, rel)
            if ls is None:
                problems.append((rec["site_id"], field, f"引用的文件不在 src/ 里: {loc}/{rel}")); continue
            if not (1 <= n <= len(ls)):
                problems.append((rec["site_id"], field, f"{loc}/{rel} 只有 {len(ls)} 行，引用了 L{n}")); continue
            checkable = bool(frag) and len(norm(frag)) >= 8
            if tally is not None:
                tally["cites"] += 1; tally["with_fragment"] += checkable
            if checkable:
                got, want = norm(ls[n - 1]), norm(frag)
                if want not in got and got not in want and not want.startswith(got[:20]):
                    if tally is not None: tally["fragment_mismatch"] += 1
                    problems.append((rec["site_id"], field,
                                     f"{loc}/{rel} L{n} 上没有引用的片段：引 {frag.strip()[:60]!r}，实为 {ls[n - 1].strip()[:80]!r}"))
        text = "".join(masked)
        # bare `L<n>[: 片段]` -- the site's own file
        ls = corpus.lines(unit_location, own)
        if ls is None: continue
        for m in RE_CITE.finditer(text):
            n, frag = int(m.group(1)), m.group(2)
            checkable = bool(frag) and len(norm(frag)) >= 8
            if tally is not None:
                tally["cites"] += 1; tally["with_fragment"] += checkable
            if not (1 <= n <= len(ls)):
                problems.append((rec["site_id"], field, f"{own} 只有 {len(ls)} 行，引用了 L{n}")); continue
            if checkable:
                got = norm(ls[n - 1]); want = norm(frag)
                if want not in got and got not in want and not want.startswith(got[:20]):
                    if tally is not None: tally["fragment_mismatch"] += 1
                    problems.append((rec["site_id"], field, f"{own} L{n} 上没有引用的片段：引 {frag.strip()!r}，实为 {ls[n - 1].strip()[:80]!r}"))


def check_flow_locs(corpus, rec, problems):
    for i, fl in enumerate(rec.get("flows") or []):
        if not isinstance(fl, dict): continue
        for j, hop in enumerate(fl.get("hops") or []):
            m = RE_HOP.match(str(hop.get("loc", "")))
            if not m: continue
            raw, n = m.group(1), int(m.group(2))
            parts = raw.split("/", 2)
            if len(parts) == 3 and parts[0] in ("repos", "deps", "pods"):
                loc, rel = "/".join(parts[:2]), parts[2]
            else:
                loc, rel = None, raw
            if loc is None:
                # a hop inside the flow's own sink unit, written relative -- try every tree
                cands = [(f"{t}/{(fl.get('sink_unit') or '')}", rel) for t in ("repos", "deps", "pods")]
                if not any(corpus.lines(l, r) is not None for l, r in cands):
                    problems.append((rec["site_id"], f"flows[{i}].hops[{j}]", f"定位不到 {raw}:{n}"))
                continue
            ls = corpus.lines(loc, rel)
            if ls is None:
                problems.append((rec["site_id"], f"flows[{i}].hops[{j}]", f"引用的文件不在 src/ 里: {loc}/{rel}"))
            elif not (1 <= n <= len(ls)):
                problems.append((rec["site_id"], f"flows[{i}].hops[{j}]", f"{loc}/{rel} 只有 {len(ls)} 行，引用了 L{n}"))


def site_keys(site):
    """The UserDefaults keys this site touches, from the resolved key or the wrapper member's keys."""
    out = []
    k = (site.get("key") or {}).get("value")
    if k: out.append(k)
    for w in site.get("wrapper_ref") or []:
        for kk in w.get("keys") or []:
            if kk.get("value"): out.append(kk["value"])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batches", action="append", required=True, help="批次目录（可给多次：主批次 + 补充批次）")
    ap.add_argument("--out", required=True, help="标注结果目录（*.out.jsonl）")
    ap.add_argument("--src", required=True, help="整个语料的源码根（含 repos/ deps/ pods/）")
    ap.add_argument("--json", dest="out_json", default=None, help="把复核结果写成 JSON")
    ap.add_argument("--flow-rules", choices=("on", "off"), default="on")
    ap.add_argument("--max-print", type=int, default=40, help="每类问题最多打印几条")
    a = ap.parse_args(argv)

    # batch index: site_id -> (unit_location, header, site)
    index = {}; headers = {}
    for bd in a.batches:
        for p in sorted(pathlib.Path(bd).glob("*__*.jsonl")):
            lines = io.open(p, encoding="utf-8").read().splitlines()
            h = json.loads(lines[0]); headers[p.name] = h
            for l in lines[1:]:
                if l.strip():
                    s = json.loads(l); index[s["site_id"]] = (h.get("unit_location"), p.name, s)
    print(f"批次 {len(headers)} 个，站点 {len(index)} 个")

    corpus = Corpus(a.src)
    outs = sorted(pathlib.Path(a.out).glob("*.out.jsonl"))
    recs = {}; order_problems = []
    for p in outs:
        for l in io.open(p, encoding="utf-8"):
            if not l.strip() or l.lstrip().startswith("#"): continue
            r = json.loads(l); r["_file"] = p.name
            if r["site_id"] in recs: order_problems.append((r["site_id"], "duplicate", p.name))
            recs[r["site_id"]] = r
    print(f"结果 {len(outs)} 个文件，记录 {len(recs)} 条")

    not_in_batch = [k for k in recs if k not in index]
    not_annotated = [k for k in index if k not in recs]
    print(f"批次里有而没标的 {len(not_annotated)} 个；标了而批次里没有的 {len(not_in_batch)} 个")

    # 1-2. citations against the corpus
    problems = []; tally = collections.Counter()
    for sid, r in recs.items():
        if sid not in index: continue
        loc, _, s = index[sid]
        check_citations(corpus, loc, s, r, problems, tally)
        check_flow_locs(corpus, r, problems)

    # 3. §5 duties
    duty = collections.Counter(); met = collections.Counter(); owed_sites = collections.defaultdict(list)
    for sid, r in recs.items():
        if sid not in index: continue
        loc, bname, s = index[sid]
        ds = V.flow_duties(s, r, loc)
        got = {fl.get("path_type") for fl in (r.get("flows") or []) if isinstance(fl, dict)}
        for d in ds:
            duty[d] += 1
            if d in got: met[d] += 1
            else: owed_sites[d].append(sid)

    # 4. cross-unit key pairs, computed
    writers = collections.defaultdict(set); readers = collections.defaultdict(set)
    dom = {}
    for sid, (loc, _, s) in index.items():
        r = recs.get(sid)
        if not r or r.get("is_api_use") != "YES": continue
        op = r.get("operation"); wr = (s.get("wrapper_ref") or [{}])[0]
        for k in site_keys(s):
            if op in ("WRITE", "REMOVE") or (op == "WRAPPED" and wr.get("access") == "SET"):
                writers[k].add(loc)
            elif op in ("READ", "OBSERVE") or (op == "WRAPPED" and wr.get("access") in ("GET", "CALL")):
                readers[k].add(loc)
            dom.setdefault(k, set()).add(s.get("domain_hint"))
    pairs = []
    for k, ws in writers.items():
        for w in ws:
            for rd in readers.get(k, set()) - {w}:
                pairs.append({"key": k, "writer": w, "reader": rd, "domains": sorted(x for x in dom.get(k, set()) if x)})
    covered = 0
    for sid, r in recs.items():
        if any(fl.get("path_type") == "CHANNEL" for fl in (r.get("flows") or []) if isinstance(fl, dict)): covered += 1

    # 4b. a definite domain verdict on a site whose domain the scanner could not resolve
    #     (§4.5: 档位只能由站点上能观察到的事实决定 -- resolving it is allowed, defaulting it is not,
    #     so a definite verdict over an unresolved hint has to cite the line that resolved it)
    UNRESOLVED = ("UNKNOWN", "SELF_INSTANCE", "MIXED_DOMAINS", "SUITE_CONST")
    dom_guess = []
    for sid, r in recs.items():
        if sid not in index or r.get("is_api_use") != "YES": continue
        loc, bname, s = index[sid]
        hint = s.get("domain_hint")
        if hint is not None and not str(hint).startswith(UNRESOLVED): continue
        reasons = (headers.get(bname) or {}).get("reasons") or {}
        dom_ids = {c["id"] for code in reasons for c in reasons[code].get("constraints", [])
                   if str(c.get("predicate", "")).startswith("DefaultsDomainIs")}
        for k, v in (r.get("constraint_verdicts") or {}).items():
            if k.split("/")[-1] not in dom_ids or isinstance(v, dict) or v == "UNKNOWN": continue
            if not V.RE_QUOTED.search(f"{r.get('fate_evidence') or ''}\n{r.get('notes') or ''}"):
                dom_guess.append({"site_id": sid, "unit": loc, "file": s["file"], "line": s["line"],
                                  "domain_hint": hint, "constraint": k, "verdict": v})

    # 5. escape under-reporting: a public member of a library whose value supposedly never leaves
    under = []
    for sid, r in recs.items():
        if sid not in index or r.get("is_api_use") != "YES": continue
        loc, _, s = index[sid]
        if (loc or "").split("/")[0] not in ("deps", "pods"): continue
        if r.get("operation") in V.NO_VALUE_OPS or r.get("operation") == "WRITE": continue
        sig = s.get("enclosing_function") or ""
        if VIS_PUBLIC.search(sig) and "->" in sig and not r.get("escape") and not (r.get("flows") or []):
            under.append({"site_id": sid, "unit": loc, "file": s["file"], "line": s["line"], "sig": sig.strip()[:120]})

    # report
    by_kind = collections.Counter(p[2].split("：")[0].split(":")[0] for p in problems)
    c, wf = tally["cites"], tally["with_fragment"]
    print(f"\n引用核对：证据里共 {c} 处 L<行号>，其中 {wf} 处带可核对的代码片段（{wf / max(1, c):.1%}），"
          f"片段与源码对不上 {tally['fragment_mismatch']} 处")
    print(f"           只有行号没有片段的 {c - wf} 处无法核对——这类证据不可证伪（§1 第 3 条要求 `L<n>: <片段>`）")
    print(f"引用核对：{len(problems)} 处对不上（{len(corpus.missing)} 个文件在 src/ 里找不到）")
    for kind, n in by_kind.most_common(): print(f"  {n:6d}  {kind}")
    for sid, field, why in problems[:a.max_print]: print(f"    {sid} {field}: {why}")
    if len(problems) > a.max_print: print(f"    …还有 {len(problems) - a.max_print} 条，见 --json")

    print(f"\n§5 流覆盖：")
    for d in ("TRIGGER", "CHANNEL", "RETURN_VALUE"):
        print(f"  {d:13s} 应有 {duty[d]:5d} 条，实有 {met[d]:5d} 条，缺 {duty[d] - met[d]:5d}")
    print(f"  计算出的跨单元键对 {len(pairs)} 组（写方单元 ≠ 读方单元），带 CHANNEL 记录的站点 {covered} 个")
    for p in pairs[:a.max_print]: print(f"    key={p['key']!r} {p['writer']} → {p['reader']} 域={p['domains']}")
    if len(pairs) > a.max_print: print(f"    …还有 {len(pairs) - a.max_print} 组")

    print(f"\n域未解析却给了确定判定、且证据里没有引到解析那一行：{len(dom_guess)} 处")
    for g in dom_guess[:a.max_print]:
        print(f"    {g['site_id']} {g['unit']}/{g['file']}:{g['line']}  hint={g['domain_hint']} {g['constraint']}={g['verdict']}")
    if len(dom_guess) > a.max_print: print(f"    …还有 {len(dom_guess) - a.max_print} 处")

    print(f"\n逃逸可能漏报（deps/pods 的 public 返回值成员，却记为不出单元）：{len(under)} 个站点")
    for u in under[:a.max_print]: print(f"    {u['site_id']} {u['unit']}/{u['file']}:{u['line']}  {u['sig']}")

    if a.out_json:
        pathlib.Path(a.out_json).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(a.out_json).write_text(json.dumps({
            "n_batches": len(headers), "n_sites_in_batches": len(index), "n_records": len(recs),
            "not_annotated": not_annotated, "not_in_batch": not_in_batch,
            "missing_source_files": sorted(corpus.missing),
            "citation_tally": dict(tally),
            "citation_problems": [{"site_id": s, "field": f, "why": w} for s, f, w in problems],
            "flow_duty": {d: {"owed": duty[d], "met": met[d], "missing_sites": owed_sites[d]} for d in ("TRIGGER", "CHANNEL", "RETURN_VALUE")},
            "cross_unit_key_pairs": pairs,
            "domain_verdict_without_resolution": dom_guess,
            "escape_under_reported": under,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n→ {a.out_json}")
    return 1 if (problems or not_annotated or any(duty[d] > met[d] for d in duty)) else 0


if __name__ == "__main__":
    sys.exit(main())
