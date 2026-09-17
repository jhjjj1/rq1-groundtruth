#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cut an annotation batch pack into upload-sized packs that carry the full unit source.

    make_packs.py --batches DIR --src DIR --out DIR [--max-mb 28] [--max-batches 10] [--annot DIR]

One pack = one annotator conversation, so besides the upload cap a pack holds at
most --max-batches batches (a conversation that reads 140 batches of ~100 KB
each runs out of context long before the zip runs out of room): `annot/` (principles + task contract), the
batch files of a run of consecutive units (MANIFEST order: repos → deps → pods),
and under `src/<unit_location>/` **every file the scanner scanned for those
units** -- the same suffix and directory rules as scan_source_rra.py (tests,
build products, dependency examples excluded) plus the manifests, project,
package and podspec files.  `SOURCE_INDEX.json` lists every packed file with
its sha1, so what the annotator could open is on record; the batch headers get
`source_scope` = `FULL_UNIT`.

A unit whose scoped source alone does not fit in one pack is reduced, in this
order, and the header says so: `SITE_MODULES` (only the top-level module
directories that contain sites, plus unit-level files), then
`REFERENCED_FILES` (only what the batch headers already list).  A unit is
never split across packs, so a pack may run under the cap.
"""

import argparse
import collections
import hashlib
import io
import json
import pathlib
import sys
import zipfile
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scan_source_rra as S  # noqa: E402

EXTRA_SUFFIX = {".xcprivacy", ".entitlements", ".xcconfig", ".pbxproj", ".podspec"}
EXTRA_NAMES = {"Package.swift", "Package.resolved", "Info.plist", "Podfile", "Podfile.lock", "Cartfile", "Cartfile.resolved"}
SCOPES = ("FULL_UNIT", "SITE_MODULES", "REFERENCED_FILES")


def scoped_files(udir, tree):
    """Relative paths of the files the scanner would read in this unit directory, plus project files."""
    out = []
    for f in sorted(udir.rglob("*")):
        if not f.is_file(): continue
        rel = str(f.relative_to(udir))
        keep = (f.suffix in S.SRC_EXT or f.suffix in EXTRA_SUFFIX or f.name in EXTRA_NAMES or f.name.endswith(".podspec.json"))
        if not keep: continue
        rel_s = rel + "/"
        if S.EXCLUDE_DIR.search(rel_s) or (tree != "repos" and S.EXCLUDE_DIR_DEP.search(rel_s)): continue
        out.append(rel)
    return out


def module_prefixes(site_files, tree):
    """Top-level module directories that contain sites (`Sources/NIOPosix`, `Wikipedia`, `WMF Framework`)."""
    pref = set()
    for f in site_files:
        parts = pathlib.PurePosixPath(f).parts
        if len(parts) >= 3 and parts[0] in ("Sources", "Source", "Tests"): pref.add("/".join(parts[:2]))
        elif len(parts) >= 2: pref.add(parts[0])
    return pref


_DEFLATED = {}


def deflated(path):
    """Compressed size of one file as a zip entry (deflate level 6 + entry overhead), cached."""
    key = str(path)
    if key not in _DEFLATED:
        _DEFLATED[key] = len(zlib.compress(path.read_bytes(), 6)) + 2 * len(key) + 90
    return _DEFLATED[key]


def file_record(path):
    data = path.read_bytes()
    return {"sha1": hashlib.sha1(data).hexdigest()[:12], "n_lines": data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1)}


class Pack:
    def __init__(self, out, n, annot):
        self.n = n; self.path = out / f"pack_{n:02d}.zip"
        self.zf = zipfile.ZipFile(self.path, "w", zipfile.ZIP_DEFLATED, compresslevel=6)
        self.units = []; self.batches = []; self.index = {}
        for name in ("ANNOTATION_PRINCIPLES.md", "OPUS_PROMPT.md"):
            p = pathlib.Path(annot) / name
            if p.is_file(): self.zf.write(p, f"pack_{n:02d}/annot/{name}")

    def size(self):
        self.zf.fp.flush(); return self.zf.fp.tell()

    def add_unit(self, loc, files, src, scope, batch_paths, headers):
        self.units.append({"unit_location": loc, "source_scope": scope, "n_files": len(files), "batches": [b.name for b in batch_paths]})
        self.index[loc] = {"source_scope": scope, "files": {}}
        for rel in files:
            p = src / loc / rel
            self.zf.write(p, f"pack_{self.n:02d}/src/{loc}/{rel}")
            self.index[loc]["files"][rel] = file_record(p)
        for bp, h in zip(batch_paths, headers):
            lines = io.open(bp, encoding="utf-8").read().splitlines()
            h = dict(h); h["source_scope"] = scope; h["source_index"] = "SOURCE_INDEX.json"; h["pack"] = self.path.name
            lines[0] = json.dumps(h, ensure_ascii=False)
            self.zf.writestr(f"pack_{self.n:02d}/{bp.name}", "\n".join(lines) + "\n")
            self.batches.append(bp.name)

    def close(self, manifest):
        sub = dict(manifest); sub["batches"] = [b for b in manifest["batches"] if b["file"] in self.batches]; sub["pack"] = self.path.name
        self.zf.writestr(f"pack_{self.n:02d}/MANIFEST.json", json.dumps(sub, ensure_ascii=False, indent=1))
        self.zf.writestr(f"pack_{self.n:02d}/SOURCE_INDEX.json", json.dumps(self.index, ensure_ascii=False, indent=1))
        self.zf.close()
        return {"pack": self.path.name, "bytes": self.path.stat().st_size, "units": self.units, "n_batches": len(self.batches)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batches", required=True, help="make_batches 的 --out（含 MANIFEST.json）")
    ap.add_argument("--src", required=True, help="scan_source_rra 的 --src")
    ap.add_argument("--out", required=True, help="pack_NN.zip 输出目录")
    ap.add_argument("--annot", default=None, help="annot/ 目录（默认 <batches>/annot 或 tools/../annot）")
    ap.add_argument("--max-mb", type=float, default=28.0, help="每个 zip 的上限（MB；claude.ai 单文件 30MB，留余量）")
    ap.add_argument("--max-batches", type=int, default=10, help="每个包最多批次数（一个包一个对话；单元不拆，超过的单元独占一包）")
    a = ap.parse_args(argv)

    bdir = pathlib.Path(a.batches); src = pathlib.Path(a.src); out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    annot = pathlib.Path(a.annot) if a.annot else ((bdir / "annot") if (bdir / "annot").is_dir() else pathlib.Path(__file__).resolve().parents[1] / "annot")
    if not annot.is_dir(): ap.error(f"annot 目录不存在: {annot}")
    manifest = json.loads((bdir / "MANIFEST.json").read_text(encoding="utf-8"))
    cap = int(a.max_mb * 1024 * 1024)
    annot_bytes = sum(deflated(annot / name) for name in ("ANNOTATION_PRINCIPLES.md", "OPUS_PROMPT.md") if (annot / name).is_file())

    by_unit = collections.OrderedDict()
    for b in manifest["batches"]: by_unit.setdefault(b["unit_location"], []).append(b)

    packs = []; cur = None; n = 0; reduced = []
    for loc, batches in by_unit.items():
        tree = loc.split("/")[0]; udir = src / loc
        batch_paths = [bdir / b["file"] for b in batches]
        headers = [json.loads(io.open(p, encoding="utf-8").readline()) for p in batch_paths]
        referenced = set()
        for h in headers:
            referenced |= {f["path"] for f in (h.get("source_files") or []) if not f.get("missing")}
            referenced |= {f["path"] for f in (h.get("unit_files") or []) if not f.get("missing")}
        site_files = {f for b in batches for f in b["files"]}
        candidates = []
        if udir.is_dir():
            full = scoped_files(udir, tree)
            pref = module_prefixes(site_files, tree)
            site_mod = [f for f in full if any(f == p or f.startswith(p + "/") for p in pref) or len(pathlib.PurePosixPath(f).parts) <= 2 and f.rsplit(".", 1)[-1] in ("xcprivacy", "pbxproj", "podspec", "entitlements", "xcconfig") or f in referenced]
            candidates = [("FULL_UNIT", sorted(set(full) | referenced)), ("SITE_MODULES", sorted(set(site_mod) | referenced))]
        candidates.append(("REFERENCED_FILES", sorted(referenced)))
        # the compressed size of a unit is measured (deflate per file, like the zip does) before anything is
        # written, so the scope is chosen and the pack boundary drawn on real numbers; a unit is never split
        batch_bytes = sum(deflated(bp) for bp in batch_paths)
        chosen = None
        for scope, files in candidates:
            est = batch_bytes + sum(deflated(src / loc / f) for f in files if (src / loc / f).is_file())
            if est + annot_bytes <= cap or scope == "REFERENCED_FILES": chosen = (scope, files, est); break
        scope, files, est = chosen
        if scope != "FULL_UNIT": reduced.append((loc, scope, len(files), est))
        if cur is not None and (cur.size() + est > cap or len(cur.batches) + len(batch_paths) > a.max_batches):
            packs.append(cur.close(manifest)); cur = None
        if cur is None:
            n += 1; cur = Pack(out, n, annot)
        cur.add_unit(loc, files, src, scope, batch_paths, headers)
    if cur is not None and cur.units: packs.append(cur.close(manifest))
    (out / "PACKS.json").write_text(json.dumps({"max_mb": a.max_mb, "packs": packs}, ensure_ascii=False, indent=1), encoding="utf-8")
    for p in packs:
        print(f"{p['pack']}: {p['bytes'] / 1048576:5.1f} MB  批次 {p['n_batches']:3d}  单元 {len(p['units'])}  " +
              ", ".join(f"{u['unit_location']}[{u['source_scope'][0]}]" for u in p["units"])[:150])
    if reduced:
        print("超过上限而缩小范围的单元（F=FULL_UNIT S=SITE_MODULES R=REFERENCED_FILES）:")
        for loc, scope, k, est in reduced: print(f"  {loc}: {scope}, {k} 个文件, 压缩后约 {est / 1048576:.1f} MB")
    print(f"{len(packs)} 个包 → {out}/pack_NN.zip；索引 {out}/PACKS.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
