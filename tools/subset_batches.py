#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cut a subset of sites out of an existing batch set, keeping the batch contract.

    subset_batches.py --batches all_batches --site-ids r3_sites_A.txt \\
        [--site-ids r3_sites_B.txt] --out r3_batches

The third pass annotates a sample, not the corpus, and it has to see exactly what
the earlier passes saw: the same headers, the same context windows, the same
source supplement.  Re-running the scanner would not guarantee that, so this
takes the shipped batches apart and puts the wanted sites back together.

Each output batch keeps its source header verbatim except for `n_sites`,
`site_ids` and two added keys recording where it came from.  `MANIFEST.json` is
rewritten from the kept batches so `make_packs.py --batches <out>` works
unchanged.  The `src/` supplement is **not** filtered: a kept site may cite any
of it, and a batch whose evidence set shrank silently would not be the same
batch.
"""

from __future__ import annotations

import argparse
import collections
import io
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import annotate_validate as AV


def read_ids(paths):
    ids, order = set(), []
    for p in paths:
        for line in io.open(p, encoding="utf-8").read().splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s not in ids:
                ids.add(s)
                order.append(s)
    return ids, order


def run(batches, id_files, out_dir):
    wanted, _ = read_ids(id_files)
    bdir = pathlib.Path(batches)
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    src_manifest = {}
    mpath = bdir / "MANIFEST.json"
    if mpath.is_file():
        src_manifest = json.loads(mpath.read_text(encoding="utf-8"))
    kept_batches, counts = [], collections.Counter()
    seen = set()
    for p in sorted(bdir.glob("*__*.jsonl")):
        header, sites = AV.read_batch(p)
        order = [s for s in header.get("site_ids", []) if s in sites]
        keep = [s for s in order if s in wanted]
        if not keep:
            counts["batches_dropped"] += 1
            continue
        seen.update(keep)
        new_header = dict(header)
        new_header["site_ids"] = keep
        new_header["n_sites"] = len(keep)
        new_header["_subset_of"] = header.get("_batch") or p.name
        new_header["_subset_site_id_files"] = [pathlib.Path(x).name for x in id_files]
        with io.open(out / p.name, "w", encoding="utf-8") as f:
            f.write(json.dumps(new_header, ensure_ascii=False) + "\n")
            for sid in keep:
                f.write(json.dumps(sites[sid], ensure_ascii=False) + "\n")
        kept_batches.append({"file": p.name, "unit_location": header.get("unit_location"),
                             "n": len(keep), "site_ids": keep,
                             "files": sorted({sites[s].get("file") for s in keep
                                              if sites[s].get("file")})})
        counts["batches_kept"] += 1
        counts["sites_kept"] += len(keep)
    manifest = dict(src_manifest)
    manifest["batches"] = kept_batches
    manifest["counts"] = {"sites_total": counts["sites_kept"],
                          "sites_batched": counts["sites_kept"],
                          "sites_copied_from_duplicates": 0}
    manifest["subset"] = {"of": str(bdir), "site_id_files": [str(x) for x in id_files],
                          "requested": len(wanted), "found": len(seen),
                          "missing": sorted(wanted - seen)[:50],
                          "missing_count": len(wanted - seen)}
    (out / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    return {"counts": dict(counts), "requested": len(wanted), "found": len(seen),
            "missing_count": len(wanted - seen), "out": str(out)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batches", required=True)
    ap.add_argument("--site-ids", action="append", required=True,
                    help="每行一个 site_id（可给多次）")
    ap.add_argument("--out", required=True, help="子集批次目录")
    ap.add_argument("--json", dest="out_json", default=None)
    a = ap.parse_args(argv)
    r = run(a.batches, a.site_ids, a.out)
    print(f"# 要 {r['requested']} 个站点，找到 {r['found']} 个"
          + (f"，{r['missing_count']} 个在批次里找不到" if r["missing_count"] else "")
          + f"；保留 {r['counts'].get('batches_kept', 0)} 个批次，丢弃 {r['counts'].get('batches_dropped', 0)} 个 → {r['out']}")
    if a.out_json:
        pathlib.Path(a.out_json).write_text(json.dumps(r, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    return 0 if not r["missing_count"] else 1


if __name__ == "__main__":
    sys.exit(main())
