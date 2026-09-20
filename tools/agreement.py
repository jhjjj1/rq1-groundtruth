#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inter-pass agreement over annotation passes: Fleiss' kappa and what it hides.

    agreement.py --pass R1=annot_r1/out --pass R2=merged_1_11/out [--pass R3=r3/out] \\
        [--batches all_batches] [--restrict sites.txt] [--exclude-constraints RCA92_C1] \\
        --json report/agreement.json --md report/agreement.md

Four coefficients, because they answer different questions and reporting only the
first invites a wrong reading:

    Po      observed agreement -- for m > 2 raters, the mean over items of the
            proportion of agreeing rater pairs, which is Fleiss' P-bar
    Fleiss  (Po - Pe) / (1 - Pe) with Pe from the **pooled** marginals.  With two
            raters this is exactly Scott's pi; the module says so rather than
            implying that a two-rater number is something it is not
    Cohen    Pe from each rater's own marginals.  Defined for a pair, so with
            three raters it is reported pairwise
    PABAK   (k*Po - 1) / (k - 1) -- kappa with the marginals forced uniform
    AC1     Gwet's chance term, Pe = sum p_j (1 - p_j) / (k - 1)

`is_api_use` on the first two passes is why all four are here: Po = 0.930 with
Fleiss 0.589, because 90.6% of the pooled marks are YES.  PABAK 0.860 and AC1
0.916 are the same data read without the prevalence term.  Reporting 0.589 alone
says "unreliable"; reporting 0.930 alone says "fine"; both are misreadings.

Confidence intervals resample **annotation units**, not sites.  Dozens of sites
in one file routinely share a judgement, so a site-level bootstrap would report
an interval several times too narrow.

Set-valued fields (`value_fate`) are compared as exact sets, not by membership:
a membership comparison scores `{LOCAL_ONLY}` against `{LOCAL_ONLY, OFF_DEVICE}`
as partial agreement, which is generous in exactly the direction that matters.
Constraint verdicts are one item per (site, constraint id), counted two ways --
over pairs every pass judged, and over the union with `ABSENT` for the ones only
some pass judged, which folds "did you rule on this at all" into the number.
"""

from __future__ import annotations

import argparse
import collections
import io
import json
import math
import pathlib
import random
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import annotate_validate as AV

#: `b0164__deps__tree-sitter@98be227227af.out.jsonl` -> ("deps", "tree-sitter@98be227227af")
RE_PASS_FILE = re.compile(r"^[ab]\d+__([a-z]+)__(.+?)(?:\.out)?\.jsonl$")
#: the leading code of an `is_api_use: NO` reason (§4.1)
RE_NO_CODE = re.compile(r"^\s*([A-Z_]{4,})")


# --------------------------------------------------------------------------- #
# statistics
# --------------------------------------------------------------------------- #
def _marginals(rows):
    """Pooled category proportions over all raters and items."""
    cnt = collections.Counter()
    total = 0
    for marks in rows:
        for c in marks:
            cnt[c] += 1
            total += 1
    return {c: v / total for c, v in cnt.items()}, cnt, total


def agreement_stats(rows):
    """`rows`: one list of per-rater marks per item, all the same length m >= 2."""
    rows = [tuple(r) for r in rows if r]
    n = len(rows)
    if n == 0:
        return None
    m = len(rows[0])
    if any(len(r) != m for r in rows):
        raise ValueError("每个条目的评分者数必须一致")
    if m < 2:
        raise ValueError("至少两名评分者")
    pj, counts, _ = _marginals(rows)
    cats = sorted(pj, key=str)
    k = len(cats)
    # Fleiss' P-bar: the share of agreeing rater pairs, averaged over items.
    po = 0.0
    for marks in rows:
        c = collections.Counter(marks)
        po += (sum(v * v for v in c.values()) - m) / (m * (m - 1))
    po /= n
    pe_fleiss = sum(v * v for v in pj.values())
    pe_ac1 = sum(v * (1 - v) for v in pj.values()) / (k - 1) if k > 1 else 0.0

    def kap(pe):
        return (po - pe) / (1 - pe) if pe < 1 else float("nan")

    out = {"n": n, "raters": m, "k": k, "Po": po, "Pe_fleiss": pe_fleiss,
           "fleiss": kap(pe_fleiss), "AC1": kap(pe_ac1),
           "PABAK": (k * po - 1) / (k - 1) if k > 1 else float("nan"),
           "scott_pi_equivalent": m == 2}
    # Cohen needs each rater's own marginals, so it is a pairwise statistic.
    cohen = {}
    for a in range(m):
        for b in range(a + 1, m):
            ma = collections.Counter(r[a] for r in rows)
            mb = collections.Counter(r[b] for r in rows)
            pe = sum((ma[c] / n) * (mb[c] / n) for c in cats)
            po_ab = sum(1 for r in rows if r[a] == r[b]) / n
            cohen[f"{a}-{b}"] = (po_ab - pe) / (1 - pe) if pe < 1 else float("nan")
    out["cohen_pairwise"] = cohen
    out["cohen"] = cohen.get("0-1") if m == 2 else None
    out["marginals"] = {str(c): {"pooled": counts[c]} for c in cats}
    for i in range(m):
        per = collections.Counter(r[i] for r in rows)
        for c in cats:
            out["marginals"][str(c)][f"rater{i}"] = per[c]
    return out


def cluster_bootstrap(items, stat_key, boot, seed):
    """95% CI by resampling annotation units with replacement."""
    by_unit = collections.defaultdict(list)
    for unit, marks in items:
        by_unit[unit].append(marks)
    units = sorted(by_unit)
    if len(units) < 2:
        return [float("nan"), float("nan")]
    rng = random.Random(seed)
    vals = []
    for _ in range(boot):
        rows = []
        for _ in units:
            rows.extend(by_unit[rng.choice(units)])
        s = agreement_stats(rows)
        v = s[stat_key] if s else float("nan")
        if not math.isnan(v):
            vals.append(v)
    if not vals:
        return [float("nan"), float("nan")]
    vals.sort()
    return [vals[int(0.025 * (len(vals) - 1))], vals[int(0.975 * (len(vals) - 1))]]


# --------------------------------------------------------------------------- #
# field definitions
# --------------------------------------------------------------------------- #
def cat_scalar(field):
    def f(rec):
        v = rec.get(field)
        return "NONE" if v is None else str(v)
    return f


def cat_value_fate(rec):
    return "{" + ",".join(sorted(rec.get("value_fate") or ())) + "}"


def cat_escape(rec):
    e = rec.get("escape")
    if not isinstance(e, dict):
        return "NO_ESCAPE"
    return f"{e.get('kind')}|{e.get('value')}"


def _all_no(recs):
    return all(str(r.get("is_api_use")) == "NO" for r in recs)


def _all_yes(recs):
    return all(str(r.get("is_api_use")) == "YES" for r in recs)


def _is_alt(recs):
    return any(str(r.get("site_class")) == "ALT" for r in recs)


#: (name, universe predicate over the per-pass records, categoriser, note)
FIELDS = (
    ("is_api_use", lambda rs: True, cat_scalar("is_api_use"), "全部站点"),
    ("operation", lambda rs: True, cat_scalar("operation"), "全部站点；NO 站点按协议是 NA，保留为一类"),
    ("unit_role", lambda rs: True, cat_scalar("unit_role"), "全部站点"),
    ("declaring_unit", lambda rs: True, cat_scalar("declaring_unit"), "全部站点；名义类别"),
    ("applicable_reason", lambda rs: not _all_no(rs), cat_scalar("applicable_reason"),
     "排除所有轮都判 NO 的站点（那里没有这一层）"),
    ("value_fate", lambda rs: not _all_no(rs), cat_value_fate, "精确集合作为一个类别"),
    ("escape", lambda rs: not _all_no(rs), cat_escape, "kind|value；null → NO_ESCAPE"),
    ("alt_equivalence", lambda rs: _is_alt(rs), cat_scalar("alt_equivalence"), "ALT 站点"),
    ("exceeds_all_reasons", lambda rs: _is_alt(rs), cat_scalar("exceeds_all_reasons"),
     "ALT 站点；null → NONE"),
    ("operation|bothYES", _all_yes, cat_scalar("operation"), "各轮都判 YES 的站点"),
    ("applicable_reason|bothYES", _all_yes, cat_scalar("applicable_reason"), "各轮都判 YES 的站点"),
    ("value_fate|bothYES", _all_yes, cat_value_fate, "各轮都判 YES 的站点"),
    ("escape|bothYES", _all_yes, cat_escape, "各轮都判 YES 的站点"),
)


def cat_verdict(v):
    if isinstance(v, dict):
        return "MIXED"
    return str(v)


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #
def load_pass(spec):
    """`NAME=path` where path is a directory of `*.out.jsonl` or one jsonl file."""
    if "=" not in spec:
        raise SystemExit(f"--pass 要写成 NAME=路径：{spec!r}")
    name, path = spec.split("=", 1)
    p = pathlib.Path(path)
    recs, unit_of = {}, {}
    files = sorted(p.glob("*.jsonl")) if p.is_dir() else [p]
    if not files:
        raise SystemExit(f"{p} 里没有 jsonl")
    for f in files:
        if f.name in ("PROGRESS.jsonl",):
            continue
        m = RE_PASS_FILE.match(f.name)
        unit = f"{m.group(1)}/{m.group(2)}" if m else None
        rows, bad = AV.read_output(f)
        if bad:
            raise SystemExit(f"{f.name}: {len(bad)} 行不是 JSON")
        for _, rec in rows:
            sid = rec.get("site_id")
            if sid is None:
                continue
            if sid in recs:
                raise SystemExit(f"{name}: site_id 重复 {sid}")
            recs[sid] = rec
            if unit:
                unit_of[sid] = unit
    return name.strip(), recs, unit_of


def build_units(passes, unit_maps, batch_index):
    """Unit for every site: the batch header first, then the file a pass shipped it in."""
    units, source = {}, collections.Counter()
    for sid in passes[0][1]:
        entry = batch_index.get(sid) if batch_index else None
        if entry is not None and entry[0].get("unit_location"):
            units[sid] = str(entry[0]["unit_location"])
            source["batch_header"] += 1
            continue
        for um in unit_maps:
            if sid in um:
                units[sid] = um[sid]
                source["pass_filename"] += 1
                break
        else:
            units[sid] = "(unknown)"
            source["unknown"] += 1
    return units, dict(source)


# --------------------------------------------------------------------------- #
# analysis
# --------------------------------------------------------------------------- #
def run(pass_specs, batches, restrict, exclude_constraints, boot, seed, diff_dir):
    passes = []
    unit_maps = []
    for spec in pass_specs:
        name, recs, um = load_pass(spec)
        passes.append((name, recs))
        unit_maps.append(um)
    names = [n for n, _ in passes]
    if len(set(names)) != len(names):
        raise SystemExit(f"--pass 名字要互不相同：{names}")
    sets = [set(r) for _, r in passes]
    common = set.intersection(*sets)
    only = {names[i]: sorted(sets[i] - common)[:20] for i in range(len(names))}
    only_counts = {names[i]: len(sets[i] - common) for i in range(len(names))}
    if restrict:
        keep = {l.strip() for l in io.open(restrict, encoding="utf-8").read().splitlines() if l.strip()}
        restricted_missing = len(keep - common)
        common &= keep
    else:
        restricted_missing = 0
    sites = sorted(common)
    if not sites:
        raise SystemExit("各轮的 site_id 交集为空")
    batch_index = None
    if batches:
        batch_index, dup = AV.build_site_index(batches)
        if dup:
            raise SystemExit(f"批次目录里有重复 site_id：{sorted(set(dup))[:5]}")
    units, unit_source = build_units([(n, {s: passes[0][1][s] for s in sites}) for n, _ in passes[:1]],
                                     unit_maps, batch_index)
    exclude_constraints = set(exclude_constraints or ())

    out = {
        "run": {"passes": {n: spec for n, spec in zip(names, pass_specs)},
                "batches": batches, "restrict": restrict,
                "exclude_constraints": sorted(exclude_constraints),
                "bootstrap": {"B": boot, "seed": seed, "cluster": "annotation unit"},
                "sites_compared": len(sites), "units": len(set(units.values())),
                "unit_source": unit_source,
                "sites_only_in_one_pass": only_counts,
                "sites_only_in_one_pass_examples": only,
                "restrict_ids_not_in_all_passes": restricted_missing},
        "fields": {}, "strata": {}, "extras": {},
    }

    def recs_for(sid):
        return [p[sid] for _, p in passes]

    diffs = collections.defaultdict(list)

    def measure(name, items, note=""):
        """`items`: list of (site_id, unit, tuple-of-marks)."""
        rows = [m for _, _, m in items]
        s = agreement_stats(rows)
        if s is None:
            out["fields"][name] = None
            return
        s["note"] = note
        s["units"] = len({u for _, u, _ in items})
        s["fleiss_ci95"] = cluster_bootstrap([(u, m) for _, u, m in items], "fleiss", boot, seed)
        conf = collections.Counter(m for _, _, m in items)
        s["confusion_top"] = [{"marks": list(k), "n": v} for k, v in conf.most_common(40)]
        s["disagreements"] = sum(v for k, v in conf.items() if len(set(k)) > 1)
        out["fields"][name] = s
        for sid, _, m in items:
            if len(set(m)) > 1:
                diffs[name].append({"site_id": sid, "unit": units.get(sid),
                                    **{names[i]: m[i] for i in range(len(names))}})

    for fname, universe, catf, note in FIELDS:
        items = []
        for sid in sites:
            rs = recs_for(sid)
            if not universe(rs):
                continue
            items.append((sid, units[sid], tuple(catf(r) for r in rs)))
        measure(fname, items, note)

    # ---- constraint verdicts: item = (site, constraint id) ------------------
    both, union = [], []
    for sid in sites:
        rs = recs_for(sid)
        cvs = [(r.get("constraint_verdicts") or {}) for r in rs]
        for cid in sorted(set().union(*[set(c) for c in cvs])):
            if cid.split("/")[-1] in exclude_constraints or cid in exclude_constraints:
                continue
            marks = tuple(cat_verdict(c[cid]) if cid in c else "ABSENT" for c in cvs)
            key = f"{sid}#{cid}"
            union.append((key, units[sid], marks))
            if all(cid in c for c in cvs):
                both.append((key, units[sid], marks))
    measure("constraint_verdicts|all_rated", both, "各轮都给了裁决的 (站点, 约束)")
    measure("constraint_verdicts|union_ABSENT", union, "并集；只有部分轮给了裁决的记 ABSENT")

    # ---- strata -------------------------------------------------------------
    def stratify(key, keyfunc):
        rows = collections.defaultdict(list)
        for sid in sites:
            g = keyfunc(sid)
            if g is None:
                continue
            rs = recs_for(sid)
            rows[g].append((sid, units[sid], tuple(cat_scalar("is_api_use")(r) for r in rs)))
        out["strata"][key] = {}
        for g, items in sorted(rows.items()):
            s = agreement_stats([m for _, _, m in items])
            if s:
                out["strata"][key][str(g)] = {"n": s["n"], "Po": s["Po"], "fleiss": s["fleiss"],
                                              "k": s["k"]}

    stratify("unit_kind", lambda sid: units[sid].split("/")[0] if "/" in units[sid] else "(unknown)")
    stratify("site_class", lambda sid: str(passes[0][1][sid].get("site_class")))
    if batch_index:
        stratify("category", lambda sid: (batch_index.get(sid) or ({}, {}))[1].get("category"))
    stratify("unit", lambda sid: units[sid])

    # ---- is_api_use flips, by direction and by the NO code that explains them
    if len(passes) == 2:
        a, b = names
        flips = collections.Counter()
        codes = collections.Counter()
        for sid in sites:
            ra, rb = recs_for(sid)
            ua, ub = str(ra.get("is_api_use")), str(rb.get("is_api_use"))
            flips[f"{a}={ua}/{b}={ub}"] += 1
            if ua == "YES" and ub == "NO":
                m = RE_NO_CODE.match(str(rb.get("is_api_use_reason") or ""))
                codes[m.group(1) if m else "(no code)"] += 1
        out["extras"]["is_api_use_confusion"] = dict(flips)
        out["extras"]["YES_to_NO_by_code"] = dict(codes.most_common())

    # ---- per-file observed agreement, for "did a whole batch collapse"
    per_file = collections.defaultdict(lambda: [0, 0])
    for sid in sites:
        rs = recs_for(sid)
        marks = tuple(cat_scalar("is_api_use")(r) for r in rs)
        u = units[sid]
        per_file[u][1] += 1
        if len(set(marks)) == 1:
            per_file[u][0] += 1
    out["extras"]["per_unit_is_api_use_agreement"] = {
        u: {"agree": v[0], "n": v[1], "Po": v[0] / v[1]} for u, v in sorted(per_file.items())}

    # ---- how precise a kappa a smaller sample would buy
    rng = random.Random(seed + 1)
    guidance = {}
    for n_sub in (300, 450, 900):
        g = {}
        for fname in ("is_api_use", "operation"):
            universe = [(sid, units[sid], tuple(cat_scalar(fname)(r) for r in recs_for(sid)))
                        for sid in sites]
            vals = []
            for _ in range(200):
                sub = rng.sample(universe, min(n_sub, len(universe)))
                vals.append(agreement_stats([m for _, _, m in sub])["fleiss"])
            mean = sum(vals) / len(vals)
            g[fname] = {"mean": mean,
                        "sd": math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1))}
        if both:
            vals = []
            for _ in range(200):
                sub = rng.sample(both, min(n_sub, len(both)))
                vals.append(agreement_stats([m for _, _, m in sub])["fleiss"])
            mean = sum(vals) / len(vals)
            g["constraint_verdicts"] = {
                "mean": mean,
                "sd": math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1))}
        guidance[str(n_sub)] = g
    out["extras"]["subsample_sd"] = guidance

    if diff_dir:
        d = pathlib.Path(diff_dir)
        d.mkdir(parents=True, exist_ok=True)
        for fname, rows in diffs.items():
            with io.open(d / f"{fname.replace('|', '_')}.jsonl", "w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return out, names


def to_markdown(out, names):
    L = []
    r = out["run"]
    L.append("# 标注轮次一致性\n")
    L.append(f"轮次：{'、'.join(f'{k} = {v}' for k, v in r['passes'].items())}；"
             f"比对 {r['sites_compared']} 个站点、{r['units']} 个单元；"
             f"bootstrap B={r['bootstrap']['B']}、seed={r['bootstrap']['seed']}、按单元重抽。\n")
    if any(r["sites_only_in_one_pass"].values()):
        L.append(f"各轮独有的 site_id：{r['sites_only_in_one_pass']}（不进比对）。\n")
    m = len(names)
    L.append(f"两名评分者时 Fleiss κ 与 Scott's π 相同（当前 m={m}）。\n")
    L.append("| 字段 | n | 类别数 | Po | Fleiss κ | 95% CI | Cohen κ | PABAK | AC1 | 口径 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for name, s in out["fields"].items():
        if not s:
            continue
        lo, hi = s["fleiss_ci95"]
        cohen = f"{s['cohen']:.3f}" if s.get("cohen") is not None else "—"
        L.append(f"| {name} | {s['n']} | {s['k']} | {s['Po']:.3f} | {s['fleiss']:.3f} | "
                 f"[{lo:.3f}, {hi:.3f}] | {cohen} | {s['PABAK']:.3f} | {s['AC1']:.3f} | {s.get('note','')} |")
    L.append("")
    for name, s in out["fields"].items():
        if not s or not s["disagreements"]:
            continue
        L.append(f"## {name}：{s['disagreements']} 条不一致，主对角外前 10 格\n")
        L.append("| " + " | ".join(names) + " | n |")
        L.append("|" + "---|" * (len(names) + 1))
        shown = 0
        for cell in s["confusion_top"]:
            if len(set(cell["marks"])) == 1:
                continue
            L.append("| " + " | ".join(str(x) for x in cell["marks"]) + f" | {cell['n']} |")
            shown += 1
            if shown >= 10:
                break
        L.append("")
    if out["extras"].get("is_api_use_confusion"):
        L.append("## is_api_use 的翻转方向\n")
        L.append("```\n" + json.dumps(out["extras"]["is_api_use_confusion"], ensure_ascii=False, indent=1) + "\n```\n")
        L.append("YES→NO 按第二轮给出的排除代码：\n")
        L.append("```\n" + json.dumps(out["extras"]["YES_to_NO_by_code"], ensure_ascii=False, indent=1) + "\n```\n")
    for key, rows in out["strata"].items():
        if key == "unit":
            continue
        L.append(f"## 分层（is_api_use）：{key}\n")
        L.append("| 组 | n | Po | Fleiss κ |")
        L.append("|---|---|---|---|")
        for g, s in rows.items():
            L.append(f"| {g} | {s['n']} | {s['Po']:.3f} | {s['fleiss']:.3f} |")
        L.append("")
    L.append("## 子样本能买到多少精度（同一数据重抽，200 次）\n")
    L.append("| n | is_api_use κ 的 SD | operation κ 的 SD | 约束裁决 κ 的 SD |")
    L.append("|---|---|---|---|")
    for n_sub, g in out["extras"]["subsample_sd"].items():
        L.append(f"| {n_sub} | {g['is_api_use']['sd']:.3f} | {g['operation']['sd']:.3f} | "
                 f"{g.get('constraint_verdicts', {}).get('sd', float('nan')):.3f} |")
    L.append("")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pass", dest="passes", action="append", required=True,
                    metavar="NAME=PATH", help="一轮标注：目录（*.out.jsonl）或单个 jsonl；给 2–3 次")
    ap.add_argument("--batches", default=None, help="批次目录，用来取单元归属与类目（可选）")
    ap.add_argument("--restrict", default=None, help="只比这些 site_id（每行一个）")
    ap.add_argument("--exclude-constraints", default=None,
                    help="约束裁决里排除这些 id（逗号分隔），用于敏感性口径")
    ap.add_argument("--boot", type=int, default=2000, help="bootstrap 次数")
    ap.add_argument("--seed", type=int, default=20260919)
    ap.add_argument("--diff-dir", default=None, help="逐字段的不一致明细写到这个目录")
    ap.add_argument("--json", dest="out", default=None)
    ap.add_argument("--md", dest="md", default=None)
    a = ap.parse_args(argv)
    if not 2 <= len(a.passes) <= 3:
        ap.error("--pass 要给 2 到 3 次")
    excl = [x.strip() for x in (a.exclude_constraints or "").split(",") if x.strip()]
    out, names = run(a.passes, a.batches, a.restrict, excl, a.boot, a.seed, a.diff_dir)
    for name, s in out["fields"].items():
        if not s:
            continue
        lo, hi = s["fleiss_ci95"]
        print(f"{name:34s} n={s['n']:6d} k={s['k']:4d} Po={s['Po']:.3f} "
              f"Fleiss={s['fleiss']:+.3f} [{lo:+.3f},{hi:+.3f}] PABAK={s['PABAK']:+.3f} AC1={s['AC1']:+.3f}")
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.md:
        pathlib.Path(a.md).write_text(to_markdown(out, names), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
