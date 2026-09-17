#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the analyser over every packaged app × config × strip style, and account for every cell.

    run_analyzer_matrix.py --bundles DIR --out DIR --rules PATH [--ghidra-dir DIR]
                           [--styles none,all] [--configs base,lto,…] [--limit N]
                           [--jobs 1] [--timeout 3600] [--dry-run]

The matrix is 46 apps × 4 configs × 2 strip styles = 368 cells.  The one thing this script
exists to prevent is the denominator quietly shrinking: a cell that was never attempted, one
that crashed, and one that produced nothing must stay three different states, and their sum
must equal the cells the matrix says should exist.  `RUN_INDEX.json` is rewritten after every
cell, so a run that dies half way still leaves a complete account of what it did.

Resume is the default: a cell whose output directory already holds files from a completed run
is skipped and marked `DONE_CACHED`.  `--force` re-runs everything.

`--dry-run` prints the exact command for each cell without running it -- use it first, and
read one command out loud before committing an overnight run.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import shlex
import subprocess
import sys
import time

STATES = ("DONE", "DONE_CACHED", "EMPTY_OUTPUT", "FAILED", "TIMEOUT", "NO_BUNDLE", "NOT_ATTEMPTED")
WHY = {
    "DONE": "跑完，退出码 0，输出目录非空",
    "DONE_CACHED": "上一次已经跑完，这次跳过（--force 可强制重跑）",
    "EMPTY_OUTPUT": "退出码 0 但输出目录是空的 —— 和成功不是一回事",
    "FAILED": "退出码非 0",
    "TIMEOUT": "超过 --timeout 被杀",
    "NO_BUNDLE": "这一格没有对应的 bundle.<style>.ipa",
    "NOT_ATTEMPTED": "这一格在矩阵里，但这次没轮到（--limit / 中断）",
}


def sha256_of(path):
    try:
        return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def probe_version(cmd):
    """分析器自己报的版本。取不到就如实记下取不到，不猜。"""
    try:
        p = subprocess.run([*cmd, "--version"], capture_output=True, text=True, timeout=120, check=False)
        return {"rc": p.returncode, "text": ((p.stdout or "") + (p.stderr or "")).strip()[:300]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"rc": -1, "text": f"{type(exc).__name__}: {exc}"}


def discover(bundles, styles, configs):
    """(repo, config, style, bundle 路径)，按 repo/config/style 排序，缺的格子也列出来。"""
    jobs = collections.defaultdict(dict)
    for d in sorted(pathlib.Path(bundles).iterdir()):
        mp = d / "manifest.json"
        if not mp.is_file():
            continue
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if m.get("repo"):
            jobs[m["repo"]][m.get("config_id") or "?"] = d
    cfgs = configs or sorted({c for byc in jobs.values() for c in byc})
    cells = []
    for repo in sorted(jobs):
        for cfg in cfgs:
            d = jobs[repo].get(cfg)
            for style in styles:
                z = (d / f"bundle.{style}.ipa") if d else None
                cells.append({"repo": repo, "config": cfg, "style": style,
                              "bundle": str(z) if z and z.is_file() else None})
    return cells, sorted(jobs), cfgs


def cell_dir(out, cell):
    return pathlib.Path(out) / cell["repo"].replace("/", "-") / cell["config"] / cell["style"]


def build_cmd(a, cell, odir):
    cmd = [*shlex.split(a.analyzer), cell["bundle"], "--rules", a.rules, "--output", str(odir)]
    if a.ghidra_dir:
        cmd += ["--ghidra-dir", a.ghidra_dir]
    if a.work_dir:
        cmd += ["--work-dir", str(pathlib.Path(a.work_dir) / odir.name)]
    cmd += shlex.split(a.extra_args or "")
    return cmd


def write_index(path, payload):
    tmp = pathlib.Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundles", required=True, help="fetch_run.py 拉回的目录")
    ap.add_argument("--out", required=True, help="分析器输出根目录，按 <repo>/<config>/<style>/ 摆")
    ap.add_argument("--rules", required=True, help="cross_rra_analyzer 的 rra_rules.yaml")
    ap.add_argument("--analyzer", default="rra-analyzer", help="分析器命令（可带参数，例 'python3 -m rra_analyzer.cli'）")
    ap.add_argument("--ghidra-dir", default=None)
    ap.add_argument("--work-dir", default=None, help="分析器的 --work-dir 根；autodl 上没有 /tmp，指到 ~/autodl-tmp 下")
    ap.add_argument("--extra-args", default=None, help="原样追加给分析器的参数")
    ap.add_argument("--styles", default="none,all")
    ap.add_argument("--configs", default=None, help="逗号分隔；默认用产物里出现过的全部配置")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 格（先试跑用）")
    ap.add_argument("--timeout", type=int, default=3600, help="单格超时（秒）")
    ap.add_argument("--force", action="store_true", help="已完成的格子也重跑")
    ap.add_argument("--dry-run", action="store_true", help="只打印每格的命令，不执行")
    ap.add_argument("--json", dest="out_json", default=None, help="默认 <out>/RUN_INDEX.json")
    a = ap.parse_args(argv)

    styles = [s.strip() for s in a.styles.split(",") if s.strip()]
    configs = [c.strip() for c in a.configs.split(",")] if a.configs else None
    cells, repos, cfgs = discover(a.bundles, styles, configs)
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    index_path = pathlib.Path(a.out_json) if a.out_json else out / "RUN_INDEX.json"

    print(f"矩阵：{len(repos)} 个 App × {len(cfgs)} 个配置 × {len(styles)} 个 strip 档位 = {len(cells)} 格")
    print(f"其中有包的 {sum(1 for c in cells if c['bundle'])} 格，没有包的 {sum(1 for c in cells if not c['bundle'])} 格")

    payload = {"bundles": a.bundles, "out": str(out), "rules": a.rules,
               "rules_sha256": sha256_of(a.rules),
               "analyzer_cmd": a.analyzer, "ghidra_dir": a.ghidra_dir,
               "analyzer_version": None if a.dry_run else probe_version(shlex.split(a.analyzer)),
               "styles": styles, "configs": cfgs, "repos": repos,
               "cells_expected": len(cells), "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "cells": []}

    if a.dry_run:
        for c in cells[: (a.limit or len(cells))]:
            odir = cell_dir(out, c)
            print("  " + (" ".join(shlex.quote(x) for x in build_cmd(a, c, odir))
                          if c["bundle"] else f"（没有包）{c['repo']}/{c['config']}/{c['style']}"))
        payload["dry_run"] = True
        write_index(index_path, payload)
        print(f"\n→ {index_path}（dry-run，没有执行任何一格）")
        return 0

    done = 0
    for i, c in enumerate(cells):
        rec = {**c, "state": "NOT_ATTEMPTED", "rc": None, "elapsed_s": None,
               "output_files": None, "log": None, "note": None}
        if a.limit and done >= a.limit:
            payload["cells"].append(rec); continue
        if not c["bundle"]:
            rec["state"] = "NO_BUNDLE"; payload["cells"].append(rec); write_index(index_path, payload); continue
        odir = cell_dir(out, c); odir.mkdir(parents=True, exist_ok=True)
        existing = [p.name for p in odir.iterdir() if p.is_file() and p.name != "run.log"]
        if existing and not a.force:
            rec.update(state="DONE_CACHED", output_files=sorted(existing))
            payload["cells"].append(rec); write_index(index_path, payload); done += 1; continue
        cmd = build_cmd(a, c, odir)
        log = odir / "run.log"
        t0 = time.time()
        try:
            with open(log, "w", encoding="utf-8") as fh:
                fh.write("$ " + " ".join(shlex.quote(x) for x in cmd) + "\n\n")
                fh.flush()
                p = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                   timeout=a.timeout, check=False)
            rec["rc"] = p.returncode
        except subprocess.TimeoutExpired:
            rec["state"] = "TIMEOUT"; rec["note"] = f"超过 {a.timeout}s"
        except OSError as exc:
            rec["state"] = "FAILED"; rec["note"] = f"{type(exc).__name__}: {exc}"
        rec["elapsed_s"] = round(time.time() - t0, 1)
        rec["log"] = str(log)
        if rec["state"] == "NOT_ATTEMPTED":
            files = sorted(p.name for p in odir.iterdir() if p.is_file() and p.name != "run.log")
            rec["output_files"] = files
            rec["state"] = "DONE" if (rec["rc"] == 0 and files) else \
                           "EMPTY_OUTPUT" if rec["rc"] == 0 else "FAILED"
        payload["cells"].append(rec); write_index(index_path, payload); done += 1
        print(f"  [{i + 1}/{len(cells)}] {c['repo']}/{c['config']}/{c['style']}: "
              f"{rec['state']} rc={rec['rc']} {rec['elapsed_s']}s "
              f"{len(rec['output_files'] or [])} 个输出文件")

    tally = collections.Counter(x["state"] for x in payload["cells"])
    payload["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    payload["totals"] = {s: tally[s] for s in STATES}
    ok = tally["DONE"] + tally["DONE_CACHED"]
    elapsed = [x["elapsed_s"] for x in payload["cells"] if x["elapsed_s"] is not None]
    payload["elapsed_summary"] = {
        "n": len(elapsed), "total_s": round(sum(elapsed), 1),
        "mean_s": round(sum(elapsed) / len(elapsed), 1) if elapsed else None,
        "max_s": max(elapsed) if elapsed else None}
    write_index(index_path, payload)

    print(f"\n{'状态':<16}{'格数':>6}  含义")
    print("-" * 78)
    for s in STATES:
        if tally[s]:
            print(f"{s:<16}{tally[s]:>6}  {WHY[s]}")
    assert sum(tally.values()) == len(cells), "分桶合计与矩阵格数不符"
    print(f"\n合计 {sum(tally.values())} 格 = 矩阵 {len(cells)} 格（守恒）；可用 {ok} 格 = {ok / max(1, len(cells)):.0%}")
    if elapsed:
        e = payload["elapsed_summary"]
        print(f"耗时：本次跑了 {e['n']} 格，合计 {e['total_s']}s，平均 {e['mean_s']}s，最慢 {e['max_s']}s")
        left = len(cells) - ok
        if left and e["mean_s"]:
            print(f"按平均值估，剩下 {left} 格约需 {left * e['mean_s'] / 3600:.1f} 小时（串行）")
    bad = [x for x in payload["cells"] if x["state"] in ("FAILED", "TIMEOUT", "EMPTY_OUTPUT")]
    for x in bad[:20]:
        print(f"  {x['state']} {x['repo']}/{x['config']}/{x['style']} rc={x['rc']} {x['note'] or ''} 日志 {x['log']}")
    print(f"\n→ {index_path}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
