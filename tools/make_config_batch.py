#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a targets file for one config from the repos that passed the gate.

WHY
---
The benchmark is the set of repos where **every** config succeeded.  When a
config is added to the matrix after the others have run (singlefile was), it
only needs to be built for the repos that already pass the gate on the
existing configs -- building it for all 181 would spend ~130 runner-jobs on
repos that can never enter the benchmark anyway.

THE ONE THING THAT MUST NOT DRIFT
---------------------------------
The new config must be built at the **same pinned SHA** as the others.  This
script does not take SHAs from the command line or from the network; it copies
the target rows verbatim out of the original targets files, so the SHA is the
one every other config was built from.  `compare_configs.gate` then checks the
manifests agree (SHA_MISMATCH), so a slip here cannot go unnoticed.

CONSERVATION
------------
requested = written + missing.  A repo that is in the gate's `complete` list
but in none of the targets files is a bug somewhere upstream and fails the run;
it is never silently dropped.
"""

import argparse
import io
import json
import pathlib
import sys


def load_rows(paths):
    """repo -> row, from every targets file.  Fails on conflicting SHAs."""
    rows = {}
    for p in paths:
        doc = json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
        for r in (doc if isinstance(doc, list) else doc.get("targets") or []):
            repo, sha = r.get("repo"), r.get("sha")
            if not repo or not sha:
                continue
            prev = rows.get(repo)
            if prev and prev["sha"] != sha:
                sys.exit(f"{repo} 在不同 targets 文件里钉了不同的 SHA："
                         f"{prev['sha'][:10]} vs {sha[:10]}（{p}）。先弄清哪个是基准。")
            rows.setdefault(repo, r)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-compare", required=True,
                    help="compare_configs.py 的输出 JSON；取其 complete 列表")
    ap.add_argument("--targets", required=True, nargs="+",
                    help="原始 targets 文件（batch01.json …），行原样复制")
    ap.add_argument("--out", "--json", dest="out", required=True,
                    help="写到 targets/<name>.json")
    a = ap.parse_args(argv)

    cmp = json.loads(pathlib.Path(a.from_compare).read_text(encoding="utf-8"))
    wanted = list(cmp.get("complete") or [])
    if not wanted:
        sys.exit("compare 输出里 complete 为空：没有仓库过闸，没什么可建的")

    rows = load_rows(a.targets)
    picked = [rows[r] for r in wanted if r in rows]
    missing = [r for r in wanted if r not in rows]
    if len(picked) + len(missing) != len(wanted):
        sys.exit("CONSERVATION FAILED")
    if missing:
        sys.exit(f"{len(missing)} 个过闸仓库在 targets 文件里找不到：{missing}。"
                 "过闸的仓库一定来自某个 targets 文件，找不到说明给错文件了。")

    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with io.open(out, "w", encoding="utf-8") as fh:
        json.dump(picked, fh, ensure_ascii=False, indent=2)
    print(f"过闸 {len(wanted)} 个 → 写入 {len(picked)} 个目标到 {out}（SHA 原样复制）")
    print("配置齐全要求：" + ", ".join(cmp.get("required") or []))
    return 0


if __name__ == "__main__":
    sys.exit(main())
