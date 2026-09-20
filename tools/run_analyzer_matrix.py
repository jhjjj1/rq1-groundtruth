#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the analyser over every packaged app × config × strip style, and account for every cell.

    run_analyzer_matrix.py --bundles DIR --out DIR --rules PATH [--ghidra-dir DIR]
                           [--styles none,all] [--configs base,lto,…] [--limit N]
                           [--jobs N] [--clean-workdir] [--timeout 3600] [--dry-run]

The matrix is 46 apps × 4 configs × 2 strip styles = 368 cells.  The one thing this script
exists to prevent is the denominator quietly shrinking: a cell that was never attempted, one
that crashed, and one that produced nothing must stay three different states, and their sum
must equal the cells the matrix says should exist.  `RUN_INDEX.json` is rewritten after every
cell, so a run that dies half way still leaves a complete account of what it did.

Resume is the default: a cell whose output directory already holds files from a completed run
is skipped and marked `DONE_CACHED`.  `--force` re-runs everything.

`--dry-run` prints the exact command for each cell without running it -- use it first, and
read one command out loud before committing an overnight run.

`--jobs N` runs N cells at once.  Every cell is a separate analyser process with its own
output directory and its own Ghidra work directory, so they do not share state -- but they do
share the machine, and two resources bind before the CPU does:

* **memory** -- the analyser sets no `-Xmx`, so each JVM inherits the default maximum heap
  (a quarter of physical RAM).  That is a ceiling, not a reservation, but it means nothing
  stops N processes from all growing.  Size `--jobs` from a measured peak RSS, not from the
  core count.
* **disk** -- the analyser never deletes its work directory, so each cell leaves a Ghidra
  project behind.  `--clean-workdir` removes that cell's work directory once the cell is
  accounted for (state, timing and output files are all recorded before the delete), which is
  what makes a long matrix survive a small disk.  It never touches `--out`.

Ordering is deliberate: cells are dispatched in matrix order, but `RUN_INDEX.json` keeps one
record per cell in matrix order regardless of which finished first, so the file reads the same
at `--jobs 1` and `--jobs 12`.  It is still rewritten after every completion.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import shlex
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

STATES = ("DONE", "DONE_CACHED", "EMPTY_OUTPUT", "CONFIG_NOT_APPLIED",
          "FAILED", "TIMEOUT", "NO_BUNDLE", "NOT_ATTEMPTED")
WHY = {
    "DONE": "跑完，退出码 0，输出目录非空，请求的调度配置确实生效",
    "DONE_CACHED": "上一次已经跑完，这次跳过（--force 可强制重跑）",
    "EMPTY_OUTPUT": "退出码 0 但输出目录是空的 —— 和成功不是一回事",
    "CONFIG_NOT_APPLIED": "退出码 0、有输出，但 summary.json 说请求的调度没有生效 —— "
                          "reason 级那一半没跑，这一格的证据层是空的",
    "FAILED": "退出码非 0",
    "TIMEOUT": "超过 --timeout 被杀",
    "NO_BUNDLE": "这一格没有对应的 bundle.<style>.ipa",
    "NOT_ATTEMPTED": "这一格在矩阵里，但这次没轮到（--limit / 中断）",
}

#: 为什么要有 CONFIG_NOT_APPLIED 这一档
#: ------------------------------------
#: 分析器的 reason 级证据采集只在 `--capability-scheduling evidence` 下运行，默认
#: 值是 `off`。这个脚本此前**一个调度参数都没传**，于是按默认跑出来的每一格都是
#: 绿的、有输出的，而 reason_evidence / constraint_evidence / cross_binary_* 七个
#: 文件全是 0 字节 —— 评估要量的那几层根本没产出。分析器自己在
#: `summary["reason_level_collection"]` 里把这件事写明了；这里把它读出来，对不上
#: 就单列一档，不让它混进 DONE。
#:
#: 同理 `--undeclared-facts`（分析器 0.123.83 起）：请求 probe 而 summary 说 off，
#: 说明跑的是一个旧版分析器，产物里没有未申报类目的事实，同样不算成功。


def dir_size_mb(path):
    total = 0
    for p in pathlib.Path(path).rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return round(total / (1024 * 1024), 1)


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


def cell_tag(cell):
    """一格的唯一名字。

    `cell_dir(...).name` 只是 strip 档位（none / all），拿它当 work-dir 名字，所有
    同档位的格子就共用一个 Ghidra 工程目录 —— 串行时只是被反复覆盖，**并发时是
    数据竞争**：两个格子同时往一个工程里写，分析结果会互相污染，而且谁都不会报错。
    """
    return f"{cell['repo'].replace('/', '-')}__{cell['config']}__{cell['style']}"


def cell_work_dir(a, cell):
    return pathlib.Path(a.work_dir) / cell_tag(cell) if a.work_dir else None


def build_cmd(a, cell, odir):
    cmd = [*shlex.split(a.analyzer), cell["bundle"], "--rules", a.rules, "--output", str(odir)]
    if a.ghidra_dir:
        cmd += ["--ghidra-dir", a.ghidra_dir]
    wd = cell_work_dir(a, cell)
    if wd:
        cmd += ["--work-dir", str(wd)]
    # 这两个不是可选项：整个矩阵就是为评估跑的，而评估要量的证据层只在这两个
    # 开关下才产出。写死在命令里，再由 check_config_applied() 逐格验收。
    cmd += ["--capability-scheduling", a.capability_scheduling]
    cmd += ["--undeclared-facts", a.undeclared_facts]
    cmd += shlex.split(a.extra_args or "")
    return cmd


def check_config_applied(odir, a):
    """读这一格的 summary.json，确认请求的调度真的生效了。

    返回 (ok, note)。读不到 summary.json 时返回 ok=True —— 那是另一类问题
    （EMPTY_OUTPUT / FAILED 已经管了），这里不越权判。
    """
    sp = pathlib.Path(odir) / "summary.json"
    if not sp.is_file():
        return True, None
    try:
        summary = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return True, f"summary.json 读不出来：{type(exc).__name__}"
    problems = []
    if a.capability_scheduling == "evidence":
        state = summary.get("reason_level_collection") or {}
        if not state.get("enabled"):
            problems.append(
                "reason 级采集未启用（summary.reason_level_collection.enabled="
                f"{state.get('enabled')!r}，capability_scheduling="
                f"{state.get('capability_scheduling')!r}）"
            )
    requested = a.undeclared_facts
    block = summary.get("undeclared_facts")
    if block is None:
        if requested != "off":
            problems.append(
                f"请求 --undeclared-facts {requested}，但 summary.json 里没有 "
                "undeclared_facts 块 —— 这是 0.123.83 之前的分析器"
            )
    elif str(block.get("mode")) != requested:
        problems.append(
            f"请求 --undeclared-facts {requested}，summary 说 {block.get('mode')!r}"
        )
    return (not problems), ("；".join(problems) if problems else None)


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
    ap.add_argument("--capability-scheduling", default="evidence",
                    choices=("off", "surface", "evidence"),
                    help="分析器的 --capability-scheduling。默认 evidence —— reason 级证据"
                         "采集只在这一档下运行，改成别的等于这一轮不产出要评的那几层")
    ap.add_argument("--undeclared-facts", default="probe", choices=("off", "probe"),
                    help="分析器的 --undeclared-facts（0.123.83 起）。默认 probe —— 真值里"
                         "三分之二的站点所在单元没有申报，off 的话那部分没有事实可比")
    ap.add_argument("--extra-args", default=None, help="原样追加给分析器的参数")
    ap.add_argument("--styles", default="none,all")
    ap.add_argument("--configs", default=None, help="逗号分隔；默认用产物里出现过的全部配置")
    ap.add_argument("--jobs", type=int, default=1,
                    help="同时跑几格。每格是一个独立的分析器进程与独立的 Ghidra 工程，"
                         "但共用这台机器；先量出单格峰值 RSS 与 work-dir 体积再定这个数，"
                         "不要按核数拍 —— 分析器不设 -Xmx，JVM 默认上限是物理内存的 1/4")
    ap.add_argument("--clean-workdir", action="store_true",
                    help="每格记账完成后删掉该格的 --work-dir（Ghidra 工程），只回收工作区，"
                         "不动 --out。work-dir 不会自己清理，长矩阵在小磁盘上必须开这个")
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
               "run_config": {"capability_scheduling": a.capability_scheduling,
                              "undeclared_facts": a.undeclared_facts,
                              "extra_args": a.extra_args,
                              "jobs": a.jobs, "clean_workdir": bool(a.clean_workdir),
                              "work_dir": a.work_dir, "timeout_s": a.timeout},
               "analyzer_version": None if a.dry_run else probe_version(shlex.split(a.analyzer)),
               "styles": styles, "configs": cfgs, "repos": repos,
               "cells_expected": len(cells), "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "cells": []}

    if a.dry_run:
        for c in cells[: (a.limit or len(cells))]:
            odir = cell_dir(out, c)
            print("  " + (" ".join(shlex.quote(x) for x in build_cmd(a, c, odir))
                          if c["bundle"] else f"（没有包）{c['repo']}/{c['config']}/{c['style']}"))
        print(f"\n调度：--capability-scheduling {a.capability_scheduling} "
              f"--undeclared-facts {a.undeclared_facts}（每格都会验收 summary.json 是否真的生效）")
        print(f"并发：--jobs {a.jobs}，每格独立 work-dir"
              + ("，跑完即清理" if a.clean_workdir else "，**不清理**（work-dir 会一直涨）"))
        payload["dry_run"] = True
        write_index(index_path, payload)
        print(f"\n→ {index_path}（dry-run，没有执行任何一格）")
        return 0

    # 第一遍只**决定**每格干什么，不执行。这样 --limit 的取舍是确定的（不受哪个
    # 线程先跑完影响），payload["cells"] 也在这里按矩阵顺序一次排好 —— 于是
    # RUN_INDEX.json 在 --jobs 1 和 --jobs 12 下读起来完全一样。
    done = 0
    todo = []
    for i, c in enumerate(cells):
        rec = {**c, "state": "NOT_ATTEMPTED", "rc": None, "elapsed_s": None,
               "output_files": None, "log": None, "note": None,
               "capability_scheduling": a.capability_scheduling,
               "undeclared_facts": a.undeclared_facts, "config_applied": None}
        payload["cells"].append(rec)
        if a.limit and done >= a.limit:
            continue
        if not c["bundle"]:
            rec["state"] = "NO_BUNDLE"; continue
        odir = cell_dir(out, c); odir.mkdir(parents=True, exist_ok=True)
        existing = [q.name for q in odir.iterdir() if q.is_file() and q.name != "run.log"]
        if existing and not a.force:
            rec.update(state="DONE_CACHED", output_files=sorted(existing)); done += 1; continue
        todo.append((i, c, rec, odir)); done += 1
    write_index(index_path, payload)

    jobs = max(1, int(a.jobs))
    if todo:
        print(f"要跑 {len(todo)} 格，并发 {jobs}")

    lock = threading.Lock()
    finished = [0]

    def run_one(item):
        i, c, rec, odir = item
        cmd = build_cmd(a, c, odir)
        log = odir / "run.log"
        t0 = time.time()
        try:
            with open(log, "w", encoding="utf-8") as fh:
                fh.write("$ " + " ".join(shlex.quote(x) for x in cmd) + "\n\n")
                fh.flush()
                proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                      timeout=a.timeout, check=False)
            rec["rc"] = proc.returncode
        except subprocess.TimeoutExpired:
            rec["state"] = "TIMEOUT"; rec["note"] = f"超过 {a.timeout}s"
        except OSError as exc:
            rec["state"] = "FAILED"; rec["note"] = f"{type(exc).__name__}: {exc}"
        rec["elapsed_s"] = round(time.time() - t0, 1)
        rec["log"] = str(log)
        if rec["state"] == "NOT_ATTEMPTED":
            files = sorted(q.name for q in odir.iterdir() if q.is_file() and q.name != "run.log")
            rec["output_files"] = files
            rec["state"] = "DONE" if (rec["rc"] == 0 and files) else \
                           "EMPTY_OUTPUT" if rec["rc"] == 0 else "FAILED"
            if rec["state"] == "DONE":
                applied, why = check_config_applied(odir, a)
                rec["config_applied"] = applied
                if not applied:
                    rec["state"] = "CONFIG_NOT_APPLIED"
                    rec["note"] = why
        # 先记账，再删工作目录 —— 状态、耗时、输出清单都已经落在 rec 上，
        # 清理只回收 Ghidra 工程，动不到 --out 里的任何东西。
        if a.clean_workdir:
            wd = cell_work_dir(a, c)
            if wd and wd.exists():
                before = dir_size_mb(wd)
                shutil.rmtree(wd, ignore_errors=True)
                rec["workdir_freed_mb"] = before
        with lock:
            finished[0] += 1
            write_index(index_path, payload)
            print(f"  [{finished[0]}/{len(todo)}] {c['repo']}/{c['config']}/{c['style']}: "
                  f"{rec['state']} rc={rec['rc']} {rec['elapsed_s']}s "
                  f"{len(rec['output_files'] or [])} 个输出文件", flush=True)

    if jobs == 1:
        for item in todo:
            run_one(item)
    else:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            list(pool.map(run_one, todo))
    write_index(index_path, payload)

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
            serial_h = left * e["mean_s"] / 3600
            print(f"按平均值估，剩下 {left} 格约需 {serial_h:.1f} 小时（串行）"
                  + (f"、{serial_h / max(1, int(a.jobs)):.1f} 小时（--jobs {a.jobs}，理想线性）"
                     if int(a.jobs) > 1 else ""))
        freed = [x.get("workdir_freed_mb") for x in payload["cells"] if x.get("workdir_freed_mb")]
        if freed:
            print(f"work-dir：本次清理 {len(freed)} 格，回收 {sum(freed) / 1024:.1f} GB，"
                  f"单格平均 {sum(freed) / len(freed):.0f} MB")
    bad = [x for x in payload["cells"]
           if x["state"] in ("FAILED", "TIMEOUT", "EMPTY_OUTPUT", "CONFIG_NOT_APPLIED")]
    for x in bad[:20]:
        print(f"  {x['state']} {x['repo']}/{x['config']}/{x['style']} rc={x['rc']} {x['note'] or ''} 日志 {x['log']}")
    print(f"\n→ {index_path}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
