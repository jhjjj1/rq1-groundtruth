#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure whether each build config actually changed the product.

WHY THIS EXISTS
---------------
`xcodebuild -showBuildSettings` reports a setting's **resolved** value, which
includes Xcode's own built-in defaults.  A resolved value is not evidence that
the setting did anything.  There is a counterexample on record: `STRIP_STYLE`
resolves to `all` on the app target, yet the built product came out with
nsyms 695,589 -> 695,594 -- not one symbol removed, because `xcodebuild build`
never runs the install phase that honours it.  The same scan also showed
`STRIP_STYLE` differing *within one repo* (app target `all`, its extensions
`non-global`), which is Xcode's per-product-type default, not an author's
choice.  So a settings scan cannot answer "did this config do anything".

Only the products can, which is what this script measures.

THE COMPLETENESS GATE
---------------------
A repo is compared only if **every** config in CONFIGS gave a successful build,
a parseable app-bundle map, and all the strip variants that config was asked
for.  Comparing a repo that has `base` and `wholemodule` but lost `lto` would
put different repo sets behind each column, and the columns would no longer be
about the settings.  v1 had no such gate and no build-outcome check: it
compared two repos whose builds had *failed* (SwissCovid, CodeAgentsMobile)
because a partial link still left a parseable map behind, which is how its
denominator came out 50 against the benchmark's 48.

Every repo that does not make the gate is kept in `excluded` with the config
and reason that blocked it -- dropping them silently is what makes a clean
denominator meaningless.

CHOOSING METRICS THAT SURVIVE BUILD NOISE
-----------------------------------------
Two independent builds of the same commit with the same settings were measured
to differ in 67.12% of same-named symbol addresses, and `__text` differed by
312 bytes.  Each config is a *separate build*, so any address- or hash-level
comparison would report "different" from noise alone and prove nothing.

These four are moved by the settings under test but not by address noise:

  object_files          Whole-module compilation emits one .o per module
                        instead of one per source file; LTO merges further.
                        A permuted layout does not change this count.
  dead_stripped         DEAD_CODE_STRIPPING=NO should drive this to 0.
                        Read from the map's `# Dead Stripped Symbols:` section.
  symbols_nonzero_size  Function-level merging removes symbols.
  units_seen            Attribution units the map can distinguish -- the
                        quantity RQ1 is actually scored against.

`text_size`, `symbols_total`, `bytes_covered` and `binary_bytes` are recorded
and printed but never used for a verdict: they were measured to drift between
identical builds.

VERDICTS (per repo x config, against that repo's own `base`)
    NO_OBSERVED_DIFFERENCE  every noise-stable metric identical to base
    DIFFERS                 at least one moved; `deltas` says which

"NO_OBSERVED_DIFFERENCE" is deliberately not called "no-op": it says these
four metrics did not move, not that nothing anywhere changed.  Absolute values
for every config are written to the JSON so that claim can be re-checked
without re-running the parse.
"""

import argparse
import collections
import gzip
import io
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import parse_link_map                                        # noqa: E402

#: Matrix, imported from make_matrix so the two can never drift.
#: config_id -> strip styles that config is expected to yield.
import make_matrix                                           # noqa: E402
CONFIGS = {k: tuple(v[1].split(",")) for k, v in make_matrix.CONFIGS.items()}
BASE = "base"

#: Per-config effect criterion, evaluated on (base_summary, config_summary).
#: This is what decides whether a repo counts in that config's P/R denominator:
#: a config that left the product unchanged for a repo has nothing to say
#: about robustness there, and averaging such rows in inflates the number.
#: A criterion of None means "not established yet": the config is reported on
#: the four stable metrics only, and the probe run has to pick one.
CRITERIA = {
    "no_deadstrip": ("dead_stripped 归零",
                     lambda b, c: (b.get("dead_stripped") or 0) > 0
                     and c.get("dead_stripped") == 0),
    "lto":          ("_lto.o 出现且吸收了 __text 字节",
                     lambda b, c: (c.get("lto_objects") or 0) > 0
                     and (c.get("lto_text_bytes") or 0) > 0),
    "singlefile":   ("未定 —— 探针后从四个稳定量里挑", None),
}

#: Decide the verdict.  Insensitive to address permutation.
STABLE = ("object_files", "dead_stripped", "symbols_nonzero_size", "units_seen")
#: Reported, never decide anything -- measured to drift between builds.
NOISY = ("text_size", "symbols_total", "bytes_covered")
#: Reported per config as absolute values (not diffs): what LTO absorbed.
LTO_ABS = ("lto_objects", "lto_symbols", "lto_bytes", "lto_text_bytes")

KIND_APP = "APP_BUNDLE"

#: Ordered, mutually exclusive.  First match wins, so a repo has exactly one
#: reason per config and the totals conserve.
R_NO_MANIFEST = "NO_MANIFEST"
R_BUILD_FAILED = "BUILD_FAILED"
R_NO_APP_MAP = "NO_APP_MAP"
R_MAP_UNPARSEABLE = "MAP_UNPARSEABLE"
R_MAP_EMPTY = "MAP_EMPTY"
R_MAP_PARSE_ERRORS = "MAP_PARSE_ERRORS"
R_MISSING_VARIANTS = "MISSING_USABLE_VARIANTS"
R_OK = "OK"
REASONS = (R_NO_MANIFEST, R_BUILD_FAILED, R_NO_APP_MAP, R_MAP_UNPARSEABLE,
           R_MAP_EMPTY, R_MAP_PARSE_ERRORS, R_MISSING_VARIANTS)


def app_map_path(art_dir):
    """The uploaded app-bundle map inside one job artifact, or None."""
    idx = art_dir / "maps_index.json"
    if not idx.is_file():
        return None
    try:
        entries = json.loads(idx.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for e in entries:
        if e.get("output_kind") == KIND_APP and e.get("copied_as"):
            p = art_dir / "maps" / e["copied_as"]
            if p.is_file():
                return p
    return None


def summarize_map(path):
    try:
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
            lm = parse_link_map.parse(fh)
        return parse_link_map.summarize(lm, src=str(path))
    except (OSError, ValueError, EOFError) as exc:
        return {"_error": f"{type(exc).__name__}: {exc}"}


def load(dirs):
    """repo -> config -> record.  Returns (table, duplicates)."""
    table, dup = collections.defaultdict(dict), []
    for root in dirs:
        for mpath in sorted(pathlib.Path(root).rglob("manifest.json")):
            try:
                m = json.loads(mpath.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            repo, cfg = m.get("repo"), m.get("config_id")
            if not repo or not cfg:
                continue
            art = mpath.parent
            rec = {
                "artifact_dir": str(art),
                "build_outcome": m.get("build_outcome"),
                "usable_variants": list(m.get("usable_variants") or []),
                "binary_bytes_before_strip": None,
                "app_map": None,
                "summary": None,
            }
            for v in (m.get("variants") or []):
                if v.get("binary_bytes_before_strip"):
                    rec["binary_bytes_before_strip"] = v["binary_bytes_before_strip"]
                    break
            p = app_map_path(art)
            if p:
                rec["app_map"] = str(p)
                rec["summary"] = summarize_map(p)
            rec["reason"] = classify(cfg, rec)

            prev = table[repo].get(cfg)
            if prev is not None:
                # rerun 批次会重跑同一个 (repo, config)。静默覆盖等于让目录
                # 遍历顺序决定结果，所以定一条规则并记账：OK 的胜过不 OK 的。
                keep_new = (rec["reason"] == R_OK) or (prev["reason"] != R_OK)
                dup.append({"repo": repo, "config": cfg,
                            "kept": (rec if keep_new else prev)["artifact_dir"],
                            "kept_reason": (rec if keep_new else prev)["reason"],
                            "dropped": (prev if keep_new else rec)["artifact_dir"],
                            "dropped_reason": (prev if keep_new else rec)["reason"]})
                if not keep_new:
                    continue
            table[repo][cfg] = rec
    return table, dup


def classify(cfg, rec):
    """Why this (repo, config) is or is not usable.  Ordered, first match wins."""
    if rec is None:
        return R_NO_MANIFEST
    if rec.get("build_outcome") != "success":
        return R_BUILD_FAILED
    if not rec.get("app_map"):
        return R_NO_APP_MAP
    s = rec.get("summary")
    if not s or "_error" in s:
        return R_MAP_UNPARSEABLE
    # `parse_link_map.parse` is deliberately lenient: a file that is not a link
    # map at all comes back as an *empty* LinkMap rather than an exception.
    # The fixture proved this leaks -- a garbage map passed the gate and was
    # reported as `object_files 9 -> 0`, which reads as a huge real effect.
    # So the map must also be non-empty to count as ground truth.
    if not (s.get("object_files") and s.get("symbols_nonzero_size")
            and s.get("text_size")):
        return R_MAP_EMPTY
    # 39 real maps / 1,140,639 symbol lines parsed with 0 failures, so any
    # nonzero count here is anomalous and gets its own bucket rather than
    # being folded into MAP_EMPTY.
    if s.get("parse_errors"):
        return R_MAP_PARSE_ERRORS
    want = set(CONFIGS.get(cfg, ("all",)))
    if not want.issubset(set(rec.get("usable_variants") or [])):
        return R_MISSING_VARIANTS
    return R_OK


def gate(table, required):
    """Split repos into (complete, excluded).  complete = every required config OK."""
    complete, excluded = {}, {}
    for repo, cfgs in table.items():
        reasons = {c: classify(c, cfgs.get(c)) for c in required}
        bad = {c: r for c, r in reasons.items() if r != R_OK}
        if bad:
            excluded[repo] = bad
        else:
            complete[repo] = cfgs
    return complete, excluded


def compare(complete, required):
    rows = []
    for repo in sorted(complete):
        cfgs = complete[repo]
        b = cfgs[BASE]["summary"]
        for cfg in sorted(set(required) - {BASE}):
            c = cfgs[cfg]["summary"]
            deltas = {k: [b.get(k), c.get(k)] for k in STABLE if b.get(k) != c.get(k)}
            noisy = {k: [b.get(k), c.get(k)] for k in NOISY if b.get(k) != c.get(k)}
            bb = cfgs[BASE].get("binary_bytes_before_strip")
            cb = cfgs[cfg].get("binary_bytes_before_strip")
            if bb and cb and bb != cb:
                noisy["binary_bytes_before_strip"] = [bb, cb]
            label, fn = CRITERIA.get(cfg, ("无", None))
            holds = None if fn is None else bool(fn(b, c))
            rows.append({
                "repo": repo, "config": cfg,
                "verdict": "DIFFERS" if deltas else "NO_OBSERVED_DIFFERENCE",
                "deltas": deltas, "noisy_deltas": noisy,
                "base_abs": {k: b.get(k) for k in STABLE + LTO_ABS},
                "config_abs": {k: c.get(k) for k in STABLE + LTO_ABS},
                "criterion": label,
                "criterion_holds": holds,      # None = 判据未定
                "text_size_base": b.get("text_size"),
            })
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--artifacts-dir", action="append", required=True,
                    help="批次产物目录，可重复给")
    ap.add_argument("--configs", default="all",
                    help="要求齐全并比对的配置 id，逗号分隔；all = make_matrix.CONFIGS 全部。"
                         "还没构建的配置不要列进来，否则闸门把所有仓库都挡在外面（这是对的）")
    ap.add_argument("--out", "--json", dest="out", required=True)
    a = ap.parse_args(argv)

    if a.configs.strip().lower() == "all":
        required = list(CONFIGS)
    else:
        required = [c.strip() for c in a.configs.split(",") if c.strip()]
        bad = [c for c in required if c not in CONFIGS]
        if bad:
            sys.exit(f"--configs 里有不在 CONFIGS 的配置 {bad}；可选：{sorted(CONFIGS)}")
    if BASE not in required:
        required.insert(0, BASE)

    table, dup = load(a.artifacts_dir)
    seen = {c for cs in table.values() for c in cs}
    retired = sorted(seen & set(make_matrix.RETIRED))
    unknown = sorted(seen - set(CONFIGS) - set(make_matrix.RETIRED))
    if unknown:
        sys.exit(f"产物里有 CONFIGS 之外的配置 {unknown}；"
                 f"矩阵变过就要同步改 make_matrix.CONFIGS，不能默默忽略")
    for repo in table:                       # 撤销的配置不进比对，但要记账
        for c in retired:
            table[repo].pop(c, None)

    complete, excluded = gate(table, required)
    if len(complete) + len(excluded) != len(table):
        sys.exit("CONSERVATION FAILED: complete + excluded != repos")
    rows = compare(complete, required)
    expect = len(complete) * (len(required) - 1)
    if len(rows) != expect:
        sys.exit(f"CONSERVATION FAILED: {len(rows)} rows, expected {expect}")

    print(f"产物里共 {len(table)} 个仓库。")
    if retired:
        print(f"产物里还有已撤销的配置 {retired}，不进比对："
              + "；".join(f"{c}: {make_matrix.RETIRED[c]}" for c in retired))
    print(f"闸门：{len(required)} 个配置（{', '.join(required)}）全部构建成功、"
          f"app map 可解析、该配置要的 strip 变体齐全。")
    print(f"  过闸  {len(complete)} 个  ← 只有这些进比对")
    print(f"  未过  {len(excluded)} 个\n")

    blocked = collections.Counter()
    for bad in excluded.values():
        for c, r in bad.items():
            blocked[(c, r)] += 1
    if blocked:
        print("未过闸的卡点（一个仓库可能卡在多个配置上）：")
        w = max(len(c) for c in required)
        for c in sorted(required):
            per = {r: blocked[(c, r)] for r in REASONS if blocked[(c, r)]}
            if per:
                print(f"  {c.ljust(w)}  " +
                      "  ".join(f"{r}={n}" for r, n in per.items()))
        print()

    print("判定只看对构建噪声不敏感的四个量：" + " / ".join(STABLE))
    print("（同设置两次独立构建曾测到 67.12% 同名符号地址不同，"
          "所以 text_size、binary_bytes 只报不判）\n")

    counts = collections.Counter((r["config"], r["verdict"]) for r in rows)
    cfgs = sorted(set(required) - {BASE})
    w = max(len(c) for c in cfgs)
    n = len(complete)
    print("配置".ljust(w + 2) + "没变".rjust(14) + "变了".rjust(14) + "分母".rjust(8))
    print("-" * (w + 38))
    for c in cfgs:
        nd = counts[(c, "NO_OBSERVED_DIFFERENCE")]
        df = counts[(c, "DIFFERS")]
        print(c.ljust(w + 2)
              + f"{nd} ({nd / n:.1%})".rjust(14)
              + f"{df} ({df / n:.1%})".rjust(14)
              + str(n).rjust(8))

    for c in cfgs:
        moved = collections.Counter()
        for r in rows:
            if r["config"] == c:
                for k in r["deltas"]:
                    moved[k] += 1
        print(f"\n{c}: 各量动了几个仓库")
        for k in STABLE:
            print(f"    {k:22s} {moved[k]:3d}/{n}")
        ex = [r for r in rows if r["config"] == c and r["verdict"] == "DIFFERS"][:3]
        for r in ex:
            print("    例 " + r["repo"] + "  " +
                  ", ".join(f"{k} {v[0]:,}→{v[1]:,}" for k, v in r["deltas"].items()))

    print("\n每档的评分分母 = 判据成立的仓库（判据不成立 = 这档对该仓库没效应，不进平均）")
    for c in cfgs:
        label, fn = CRITERIA.get(c, ("无", None))
        sub = [r for r in rows if r["config"] == c]
        if fn is None:
            print(f"  {c:14s} 判据{label}；先看上面四个量哪个动")
            continue
        h = sum(1 for r in sub if r["criterion_holds"])
        print(f"  {c:14s} 判据「{label}」成立 {h}/{n}")
        if c == "lto" and h:
            fr = sorted(((r["config_abs"]["lto_text_bytes"] or 0) / (r["text_size_base"] or 1), r["repo"])
                        for r in sub if r["criterion_holds"])
            q = lambda k: fr[min(len(fr) - 1, int(k * len(fr)))][0]
            print(f"      _lto.o 吸收的 __text 占比：最小 {fr[0][0]:.2%} / 中位 {q(.5):.2%} / "
                  f"最大 {fr[-1][0]:.2%}（{fr[-1][1]}）")
            print("      注意：这是 __text 字节，不含 __profc / __llvm_prf_nm 那些元数据")

    if dup:
        print(f"\n{len(dup)} 个 (仓库, 配置) 在多个批次里都有产物，"
              f"按「OK 的优先」去重，明细在 duplicates 字段")

    with io.open(a.out, "w", encoding="utf-8") as fh:
        json.dump({"configs": {k: list(v) for k, v in CONFIGS.items()},
                   "required": required, "retired_seen": retired,
                   "criteria": {k: v[0] for k, v in CRITERIA.items()},
                   "stable_metrics": list(STABLE), "noisy_metrics": list(NOISY),
                   "repos_seen": len(table),
                   "repos_complete": len(complete),
                   "repos_excluded": len(excluded),
                   "excluded": excluded, "duplicates": dup, "rows": rows},
                  fh, ensure_ascii=False, indent=2)
    print(f"\n逐仓库明细写入 {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
