#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mechanically correct annotation verdicts that a rule table settles, and log every one.

    apply_rule_fixes.py --batches DIR --in merged/out --out-dir merged_1_11/out --json report/rule_fixes.json
    apply_rule_fixes.py --batches DIR --in merged/out --dry-run --json report/rule_fixes_dryrun.json

This is the only place annotation content is edited after a pass is returned, so
it is built as a registry rather than a script: each correction is a `RuleFix`
with an id, the clause it rests on, and a function that proposes changes.  Two
are registered today.

    FIX_1_11_DOMAIN   Protocol 1.11 §4.5 rewrote the `DefaultsDomainIs` rows to
                      judge a domain by reachability (App Group membership per
                      entitlement) instead of by spelling.  The second pass was
                      annotated under 1.10 and is not re-annotated; the rows the
                      rewrite touches are flipped here.
    FIX_C2_STANDARD   §4.5's participants row says `.standard` -> SUPPORTED (the
                      only participant is this app), identically in 1.9, 1.10 and
                      1.11.  The second pass wrote UNKNOWN on that row wherever
                      the domain is `.standard` ("连 group 都没用上，参与者集合无从
                      谈起").  Under FALSIFICATION_ONLY this changes no violation
                      -- neither value falsifies -- but the analyser emits
                      SUPPORTED there, so leaving UNKNOWN in the ground truth
                      would score a correct output as a disagreement.

Four outcomes are counted for every (site, constraint) a fix looks at, and the
last two are listed one by one rather than summarised, because they are the
cases where the annotation and the table disagree and nobody has adjudicated:

    FLIPPED          the annotation matched the old rule and is now the new one
    ALREADY_NEW      the annotation already matched the new rule; nothing to do
    SAME_UNDER_BOTH  both rules give the same verdict here; nothing to do
    DIVERGENT        the annotation matches neither rule -- left alone, listed
    NOT_APPLICABLE   the table gives no verdict for this site's domain evidence

Nothing else in the record is touched.  A flipped verdict appends one segment to
`notes`: `RULE_FIX:<id>: <constraint> <old>→<new>, domain=…`, so the change is
visible in the record itself and not only in the report.  Re-running is a no-op:
a flipped verdict no longer matches the old rule.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import pathlib
import shutil
import sys
from dataclasses import dataclass
from typing import Any, Callable, Mapping

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import annotate_validate as AV
import rule_table as RT

OUTCOMES = ("FLIPPED", "ALREADY_NEW", "SAME_UNDER_BOTH", "DIVERGENT", "NOT_APPLICABLE")


@dataclass(frozen=True)
class Change:
    site_id: str
    constraint: str
    domain_label: str | None
    old: Any
    new: Any
    outcome: str
    evidence: dict
    where: str
    note: str = ""

    def to_dict(self) -> dict:
        return {"site_id": self.site_id, "constraint": self.constraint,
                "domain_label": self.domain_label, "old": self.old, "new": self.new,
                "outcome": self.outcome, "where": self.where, "note": self.note,
                "domain": {k: self.evidence.get(k) for k in
                           ("kind", "source", "suite", "in_entitlements")}}


@dataclass(frozen=True)
class RuleFix:
    id: str
    clause: str
    why: str
    propose: Callable[[Mapping, Mapping, Mapping], list[Change]]


def _verdict_branches(value):
    """`"SUPPORTED"` -> [(None, "SUPPORTED")]; a per-domain dict -> one pair per domain."""
    if isinstance(value, dict):
        return [(k, v) for k, v in value.items()]
    return [(None, value)]


def _evidence_for(site, rec, header, label):
    groups = RT.group_facts_of(header, site)
    if label is not None:
        return RT.evidence_for_domain_label(label, groups), groups
    return RT.resolve_domain(site, rec, header), groups


def _where(site):
    return f"{site.get('unit', '?')}/{site.get('file', '?')}:{site.get('line', '?')}"


# --------------------------------------------------------------------------- #
# FIX_1_11_DOMAIN
# --------------------------------------------------------------------------- #
def _propose_1_11(header, site, rec) -> list[Change]:
    out: list[Change] = []
    cv = rec.get("constraint_verdicts") or {}
    ar = rec.get("applicable_reason")
    declared = [c for _, codes in (site.get("declared_reasons") or {}).items() for c in codes]
    reasons = header.get("reasons") or {}
    for cid, value in cv.items():
        pred = AV.predicate_of(reasons, ar, declared, cid)
        if not pred.startswith(("DefaultsDomainIs(APP_PRIVATE", "DefaultsDomainIs(APP_GROUP")):
            continue
        for label, got in _verdict_branches(value):
            ev, groups = _evidence_for(site, rec, header, label)
            old = RT.expected_domain_verdict(pred, ev, groups, "1.10")
            new = RT.expected_domain_verdict(pred, ev, groups, "1.11")
            d = ev.to_dict()
            if old is None or new is None:
                out.append(Change(rec["site_id"], cid, label, got, got, "NOT_APPLICABLE", d, _where(site)))
            elif old == new:
                out.append(Change(rec["site_id"], cid, label, got, got, "SAME_UNDER_BOTH", d, _where(site)))
            elif got == old:
                out.append(Change(rec["site_id"], cid, label, got, new, "FLIPPED", d, _where(site),
                                  note=f"1.10 期望 {old}，1.11 期望 {new}"))
            elif got == new:
                out.append(Change(rec["site_id"], cid, label, got, got, "ALREADY_NEW", d, _where(site)))
            else:
                out.append(Change(rec["site_id"], cid, label, got, got, "DIVERGENT", d, _where(site),
                                  note=f"标注 {got!r}，1.10 期望 {old}，1.11 期望 {new}"))
    return out


# --------------------------------------------------------------------------- #
# FIX_C2_STANDARD
# --------------------------------------------------------------------------- #
def _propose_c2_standard(header, site, rec) -> list[Change]:
    out: list[Change] = []
    cv = rec.get("constraint_verdicts") or {}
    ar = rec.get("applicable_reason")
    declared = [c for _, codes in (site.get("declared_reasons") or {}).items() for c in codes]
    reasons = header.get("reasons") or {}
    for cid, value in cv.items():
        pred = AV.predicate_of(reasons, ar, declared, cid)
        if not pred.startswith("AllIntendedParticipantsAreMembersOfSameAppGroup"):
            continue
        if isinstance(value, dict):
            # The validator only allows a per-domain dict on `DefaultsDomainIs`
            # rows; one here is malformed input, and this fix does not repair
            # shape.  Listed as NOT_APPLICABLE so it is visible, never edited.
            out.append(Change(rec["site_id"], cid, None, value, value, "NOT_APPLICABLE",
                              {"kind": "MIXED", "source": "verdict_dict"}, _where(site),
                              note="参与者行不允许按域分叉的字典值（校验器会拦）；不改"))
            continue
        for label, got in _verdict_branches(value):
            ev, groups = _evidence_for(site, rec, header, label)
            d = ev.to_dict()
            if ev.kind != RT.DOM_STANDARD:
                # The row only has one reading this fix is sure of.  Everything
                # else -- an unresolved suite, an extension body whose callers
                # decide, contradictory evidence -- is left to a human.
                out.append(Change(rec["site_id"], cid, label, got, got, "NOT_APPLICABLE", d, _where(site)))
                continue
            want = RT.expected_domain_verdict(pred, ev, groups, "1.11")
            assert want == "SUPPORTED", (pred, want)     # identical in 1.9 / 1.10 / 1.11
            if got == want:
                out.append(Change(rec["site_id"], cid, label, got, got, "SAME_UNDER_BOTH", d, _where(site)))
            elif got == "UNKNOWN":
                out.append(Change(rec["site_id"], cid, label, got, want, "FLIPPED", d, _where(site),
                                  note="§4.5 参与者行：`.standard` 的参与者只有本 App → SUPPORTED（1.9/1.10/1.11 同）"))
            else:
                out.append(Change(rec["site_id"], cid, label, got, got, "DIVERGENT", d, _where(site),
                                  note=f"标注 {got!r}，规则表期望 {want}"))
    return out


FIXES = (
    RuleFix("FIX_1_11_DOMAIN", "原则 1.11 §4.5（DefaultsDomainIs 两行按可达性重写）",
            "1.10 看拼写（.standard? group. 前缀?），1.11 看 entitlement 里的 App Group 成员资格",
            _propose_1_11),
    RuleFix("FIX_C2_STANDARD", "原则 §4.5 参与者行（1.9 / 1.10 / 1.11 文本相同）",
            "`.standard` 的参与者只有本 App，应记 SUPPORTED；第二轮在这一行写了 UNKNOWN",
            _propose_c2_standard),
)
FIX_BY_ID = {f.id: f for f in FIXES}


def _apply_change(rec, ch: Change, fix_id: str) -> None:
    cv = rec["constraint_verdicts"]
    if ch.domain_label is None:
        cv[ch.constraint] = ch.new
    else:
        cv[ch.constraint][ch.domain_label] = ch.new
    where = f"{ch.constraint}[{ch.domain_label}]" if ch.domain_label else ch.constraint
    ev = ch.evidence
    stamp = (f"RULE_FIX:{fix_id}: {where} {ch.old}→{ch.new}, "
             f"domain={ev.get('kind')}/{ev.get('source')}, suite={ev.get('suite')}, "
             f"in_entitlements={ev.get('in_entitlements')}")
    notes = rec.get("notes") or ""
    rec["notes"] = (notes + ("；" if notes and not notes.endswith(("；", ";")) else "") + stamp)


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def run(batches_dir, in_dir, out_dir, fix_ids, dry_run):
    index, dup = AV.build_site_index(batches_dir)
    if dup:
        raise SystemExit(f"批次目录里有重复 site_id（前 5 个）：{sorted(set(dup))[:5]}")
    in_dir = pathlib.Path(in_dir)
    files = sorted(in_dir.glob("*.out.jsonl"))
    if not files:
        raise SystemExit(f"{in_dir} 里没有 *.out.jsonl")
    fixes = [FIX_BY_ID[i] for i in fix_ids] if fix_ids else list(FIXES)
    report = {f.id: {"clause": f.clause, "why": f.why,
                     "counts": collections.Counter(), "changes": [], "divergent": [],
                     "not_applicable_by_domain": collections.Counter()} for f in fixes}
    seen_sites = 0
    missing_site = []
    touched_files = 0
    if out_dir and not dry_run:
        out_dir = pathlib.Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
    for path in files:
        rows, bad = AV.read_output(path)
        if bad:
            raise SystemExit(f"{path.name}: {len(bad)} 行不是 JSON，先跑校验器")
        changed = False
        for _, rec in rows:
            sid = rec.get("site_id")
            entry = index.get(sid)
            if entry is None:
                missing_site.append(sid)
                continue
            header, site = entry
            seen_sites += 1
            for fix in fixes:
                for ch in fix.propose(header, site, rec):
                    slot = report[fix.id]
                    slot["counts"][ch.outcome] += 1
                    if ch.outcome == "NOT_APPLICABLE":
                        slot["not_applicable_by_domain"][ch.evidence.get("kind") or "?"] += 1
                    elif ch.outcome == "DIVERGENT":
                        slot["divergent"].append(ch.to_dict())
                    if ch.outcome == "FLIPPED":
                        slot["changes"].append(ch.to_dict())
                        _apply_change(rec, ch, fix.id)
                        changed = True
        if out_dir and not dry_run:
            target = pathlib.Path(out_dir) / path.name
            with io.open(target, "w", encoding="utf-8") as f:
                for _, rec in rows:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            touched_files += 1 if changed else 0
    if out_dir and not dry_run:
        # Everything that travelled with the pass travels with the corrected pass.
        for extra in sorted(in_dir.iterdir()):
            if extra.is_file() and not extra.name.endswith(".out.jsonl"):
                shutil.copy2(extra, pathlib.Path(out_dir) / extra.name)
    payload = {
        "run": {"batches": str(batches_dir), "in": str(in_dir),
                "out_dir": str(out_dir) if (out_dir and not dry_run) else None,
                "dry_run": bool(dry_run), "fixes": [f.id for f in fixes],
                "input_files": len(files), "files_changed": touched_files,
                "sites_seen": seen_sites, "sites_not_in_any_batch": len(missing_site),
                "input_sha256": {p.name: _sha256(p) for p in files}},
        "fixes": {},
    }
    for fid, slot in report.items():
        payload["fixes"][fid] = {
            "clause": slot["clause"], "why": slot["why"],
            "counts": {k: slot["counts"].get(k, 0) for k in OUTCOMES},
            "not_applicable_by_domain": dict(sorted(slot["not_applicable_by_domain"].items())),
            "changes": slot["changes"], "divergent": slot["divergent"],
        }
    payload["run"]["sites_not_in_any_batch_ids"] = sorted(set(missing_site))[:50]
    return payload


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batches", required=True, help="批次目录（含 MANIFEST.json）")
    ap.add_argument("--in", dest="in_dir", required=True, help="标注结果目录（*.out.jsonl）")
    ap.add_argument("--out-dir", default=None, help="修正后的结果目录；--dry-run 时忽略")
    ap.add_argument("--fix", action="append", choices=sorted(FIX_BY_ID), default=None,
                    help="只跑这些修正（可给多次）；默认全部")
    ap.add_argument("--dry-run", action="store_true", help="只出清单，不写文件")
    ap.add_argument("--json", dest="out", default=None, help="把修正结果写成 JSON")
    a = ap.parse_args(argv)
    if not a.dry_run and not a.out_dir:
        ap.error("要么 --dry-run，要么给 --out-dir")
    payload = run(a.batches, a.in_dir, a.out_dir, a.fix, a.dry_run)
    print(f"# {payload['run']['input_files']} 个文件，{payload['run']['sites_seen']} 个站点"
          f"（{payload['run']['sites_not_in_any_batch']} 个不在任何批次里，跳过）"
          f"{'；DRY-RUN，未写文件' if a.dry_run else ''}")
    for fid, slot in payload["fixes"].items():
        c = slot["counts"]
        print(f"== {fid}  {slot['clause']}")
        print("   " + "  ".join(f"{k}={c[k]}" for k in OUTCOMES))
        if slot["not_applicable_by_domain"]:
            print("   不表态的域证据：" + ", ".join(f"{k}={v}" for k, v in slot["not_applicable_by_domain"].items()))
        for d in slot["divergent"][:20]:
            print(f"   DIVERGENT {d['site_id']} {d['constraint']}"
                  f"{'[' + d['domain_label'] + ']' if d['domain_label'] else ''} "
                  f"={d['old']!r} @ {d['where']}：{d['note']}")
        if len(slot["divergent"]) > 20:
            print(f"   …还有 {len(slot['divergent']) - 20} 条，看 JSON")
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
