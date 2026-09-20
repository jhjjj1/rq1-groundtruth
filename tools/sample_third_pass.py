#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Draw the third pass's workload: one sample for the kappa, one census for adjudication.

    sample_third_pass.py --batches all_batches --pass R2=merged_1_11/out \\
        --n 450 --min-per-unit 2 --seed 20260919 --json report/r3_sample.json --out-dir report/

Two draws, reported separately, because they answer different questions and
mixing them would make both unreadable.

Sample A -- the one the reported Fleiss kappa is computed on.  Uniform random
within each annotation unit, unit-stratified, with a floor per unit.  It is
deliberately **not** stratified by `is_api_use`, category or verdict: those are
the variables being measured, and stratifying on an outcome is what turns a
population estimate into a number with no population.  Sizing: at n = 450 the
measured SD of a two-rater kappa on this corpus is about 0.064 for `is_api_use`,
0.018 for `operation` and 0.031 for the constraint verdicts (`agreement.py`
prints the same table for whatever data you have); n = 900 buys roughly a third
off each.

Sample B -- every site whose label the analyser evaluation actually leans on,
taken whole, not sampled: an off-device fate, a CONFLICT on something other than
a UserDefaults domain row, a live ALT site, an open `needs_context`.  There are
few of them and they carry the positive cases, so they are adjudicated rather
than estimated.  A kappa computed on a purposive draw has no population to
generalise to, so this script keeps B out of the kappa and says so in the output.
"""

from __future__ import annotations

import argparse
import collections
import io
import json
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import annotate_validate as AV
import rule_table as RT

B_REASONS = ("OFF_DEVICE_FATE", "NON_DOMAIN_CONFLICT", "ALT_IN_USE", "NEEDS_CONTEXT")
#: a site the batch directory does not know is reported under this key and never
#: classified: the domain / non-domain split needs the constraint predicate
NOT_IN_ANY_BATCH = "NOT_IN_ANY_BATCH"


def largest_remainder(weights, total):
    """Hand out `total` seats proportionally to `weights`, largest remainder first."""
    if total <= 0 or not weights:
        return {k: 0 for k in weights}
    s = sum(weights.values())
    if s <= 0:
        return {k: 0 for k in weights}
    exact = {k: total * w / s for k, w in weights.items()}
    seats = {k: int(v) for k, v in exact.items()}
    left = total - sum(seats.values())
    for k, _ in sorted(exact.items(), key=lambda kv: (-(kv[1] - int(kv[1])), kv[0]))[:left]:
        seats[k] += 1
    return seats


def sample_a(sites_by_unit, n, min_per_unit, seed):
    """Unit-stratified uniform draw: a floor per unit, the rest proportional."""
    rng = random.Random(seed)
    units = sorted(sites_by_unit)
    floors = {u: min(min_per_unit, len(sites_by_unit[u])) for u in units}
    base = sum(floors.values())
    if base > n:
        raise SystemExit(f"--n {n} 装不下 {len(units)} 个单元的下限（{base}）；调大 --n 或调小 --min-per-unit")
    room = {u: len(sites_by_unit[u]) - floors[u] for u in units}
    extra = largest_remainder(room, n - base)
    # a unit cannot give more than it has; hand any shortfall back proportionally
    picked, short = {}, 0
    for u in units:
        want = floors[u] + extra[u]
        if want > len(sites_by_unit[u]):
            short += want - len(sites_by_unit[u])
            want = len(sites_by_unit[u])
        picked[u] = want
    while short > 0:
        room2 = {u: len(sites_by_unit[u]) - picked[u] for u in units}
        if not any(room2.values()):
            break
        add = largest_remainder(room2, short)
        moved = 0
        for u in units:
            take = min(add[u], room2[u])
            picked[u] += take
            moved += take
        short -= moved
        if moved == 0:
            break
    out = {}
    for u in units:
        pool = sorted(sites_by_unit[u])
        out[u] = sorted(rng.sample(pool, picked[u]))
    return out


def sample_b(records, index):
    """Every site the evaluation's decisive rows rest on, with why it was taken."""
    reasons = collections.defaultdict(list)
    for sid, rec in sorted(records.items()):
        entry = index.get(sid)
        if entry is None:
            # Without the batch there is no predicate to tell a domain-row CONFLICT
            # from a behavioural one, so the site is listed, not guessed.
            reasons["NOT_IN_ANY_BATCH"].append(sid)
            continue
        header, site = entry
        hit = []
        if "OFF_DEVICE" in set(rec.get("value_fate") or ()):
            hit.append("OFF_DEVICE_FATE")
        cv = rec.get("constraint_verdicts") or {}
        declared = [c for _, codes in (site.get("declared_reasons") or {}).items() for c in codes]
        for cid, v in cv.items():
            vals = list(v.values()) if isinstance(v, dict) else [v]
            if "CONFLICT" not in vals:
                continue
            pred = AV.predicate_of(header.get("reasons") or {}, rec.get("applicable_reason"),
                                   declared, cid)
            if not pred.startswith("DefaultsDomainIs"):
                hit.append("NON_DOMAIN_CONFLICT")
                break
        if str(rec.get("site_class")) == "ALT" and str(rec.get("is_api_use")) == "YES":
            hit.append("ALT_IN_USE")
        if rec.get("needs_context"):
            hit.append("NEEDS_CONTEXT")
        for h in hit:
            reasons[h].append(sid)
    return reasons


def run(batches, pass_spec, n, min_per_unit, seed):
    index, dup = AV.build_site_index(batches)
    if dup:
        raise SystemExit(f"批次目录里有重复 site_id：{sorted(set(dup))[:5]}")
    name, records, unit_from_file = _load(pass_spec)
    by_unit = collections.defaultdict(list)
    unit_of = {}
    for sid in records:
        entry = index.get(sid)
        unit = (entry[0].get("unit_location") if entry else None) or unit_from_file.get(sid) or "(unknown)"
        unit_of[sid] = str(unit)
        by_unit[str(unit)].append(sid)
    picked = sample_a(by_unit, n, min_per_unit, seed)
    a_ids = sorted(x for v in picked.values() for x in v)
    b = sample_b(records, index)
    unindexed = b.pop("NOT_IN_ANY_BATCH", [])
    b_ids = sorted({x for v in b.values() for x in v})
    a_set = set(a_ids)
    batch_of = {}
    for sid in set(a_ids) | set(b_ids):
        entry = index.get(sid)
        batch_of[sid] = (entry[0].get("_batch") if entry else None)
    return {
        "run": {"batches": str(batches), "pass": pass_spec, "n_requested": n,
                "min_per_unit": min_per_unit, "seed": seed,
                "sites_in_pass": len(records), "units": len(by_unit),
                "sites_not_in_any_batch": sum(1 for s in records if s not in index)},
        "sample_A": {
            "purpose": "报 Fleiss κ 的样本：按单元分层均匀随机，不按结果变量分层",
            "n": len(a_ids), "per_unit": {u: len(v) for u, v in sorted(picked.items())},
            "site_ids": a_ids,
            "batches": sorted({b for b in (batch_of[s] for s in a_ids) if b}),
        },
        "sample_B": {
            "purpose": "全取的裁决材料：评估里决定性的少数样本；有目的抽样，不进 κ",
            "n": len(b_ids),
            "by_reason": {k: {"n": len(v), "site_ids": v} for k, v in sorted(b.items())},
            "site_ids": b_ids,
            "overlap_with_A": sorted(set(b_ids) & a_set),
            "batches": sorted({b for b in (batch_of[s] for s in b_ids) if b}),
            "not_classifiable_no_batch": {"n": len(unindexed), "site_ids": unindexed[:50]},
        },
        "site_unit": {s: unit_of[s] for s in sorted(set(a_ids) | set(b_ids))},
    }


def _load(spec):
    if "=" not in spec:
        raise SystemExit(f"--pass 要写成 NAME=路径：{spec!r}")
    name, path = spec.split("=", 1)
    import agreement
    return agreement.load_pass(spec)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batches", required=True)
    ap.add_argument("--pass", dest="pass_spec", required=True, metavar="NAME=PATH",
                    help="已完成的那一轮（第三遍要独立于它，只用来定抽样框与样本 B）")
    ap.add_argument("--n", type=int, default=450, help="样本 A 的站点数")
    ap.add_argument("--min-per-unit", type=int, default=2)
    ap.add_argument("--seed", type=int, default=20260919)
    ap.add_argument("--out-dir", default=None, help="额外写 r3_sites_A.txt / r3_sites_B.txt")
    ap.add_argument("--json", dest="out", default=None)
    a = ap.parse_args(argv)
    payload = run(a.batches, a.pass_spec, a.n, a.min_per_unit, a.seed)
    A_, B_ = payload["sample_A"], payload["sample_B"]
    print(f"# {payload['run']['sites_in_pass']} 个站点 / {payload['run']['units']} 个单元，seed={a.seed}")
    print(f"样本 A：{A_['n']} 个站点，覆盖 {len(A_['batches'])} 个批次，每单元下限 {a.min_per_unit}")
    print(f"样本 B：{B_['n']} 个站点（" + "，".join(
        f"{k} {v['n']}" for k, v in B_["by_reason"].items()) + f"），与 A 重叠 {len(B_['overlap_with_A'])} 个")
    if a.out_dir:
        d = pathlib.Path(a.out_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "r3_sites_A.txt").write_text("\n".join(A_["site_ids"]) + "\n", encoding="utf-8")
        (d / "r3_sites_B.txt").write_text("\n".join(B_["site_ids"]) + "\n", encoding="utf-8")
        print(f"→ {d}/r3_sites_A.txt  {d}/r3_sites_B.txt")
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
