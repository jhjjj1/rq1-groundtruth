#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cut scan_source_rra worksheets into annotation batches.

A batch is one JSONL file: the first line is the batch header (unit-level
facts that every site of the unit shares), every following line is one site.
Batches never mix units, and sites are ordered by file / line / column so an
annotator sees one function's sites together.  Units are emitted app repos
first, then SPM dependencies, then pods (`repos/` → `deps/` → `pods/`).

Per-site records are the worksheet records minus what moved into the header
(`reason_constraints`, `hosts`, `unit_manifests_all`, `unit_location`,
`unit_macros`, `wrapper_methods`): the constraint boilerplate alone was 56% of
every record (2.6 KB of 4.6 KB) and is identical for every site declaring the
same reason.

Source supplement (`--src DIR`, the scanner's --src): every file a batch refers
to -- the sites' own files, the extension members they call (`wrapper_ref`),
the constructions their instances resolve to (`instance_domains`), the files
the callers of an extension body live in, the constants their keys resolve to
-- plus the unit's manifests, Package.swift / podspec / project.pbxproj, is
copied to `<out>/src/<unit_location>/<path>` and listed in the batch header
with its sha1.  The annotator may open exactly these files and cites them as
`<path>:L<n>`; nothing outside the listing was available to it, which keeps
the evidence set of every batch reproducible.

Deduplication (default on): a dependency site whose (product, file, column,
api, context lines without their line numbers) are identical to one in another
revision / package identity of the same product is not put in a batch (two
sites of the same unit are never copies of each other, and a unit that has a
different number of such identical sites than the representative keeps them all);
MANIFEST.json maps it to the representative so the annotation can be copied
back.  Only exact copies qualify -- a shifted line number is fine, a changed
character is not -- and the copy is only valid when the two units declare the
same reasons, which MANIFEST.json records per pair.
"""

import argparse
import collections
import hashlib
import io
import json
import pathlib
import re
import shutil
import sys

#: package identities that are the same product (same files, same sites)
SAME_PRODUCT = {"purchases-ios": "revenuecat", "purchases-ios-spm": "revenuecat"}

SITE_DROP = ("reason_constraints", "hosts", "unit_manifests_all", "unit_location")
TREE_ORDER = {"repos": 0, "deps": 1, "pods": 2}
RE_NUM = re.compile(r"^\s*\d+(?:>>|  ) ")


def ident(loc):
    tree, name = loc.split("/", 1)
    idn = name.split("@")[0].lower()
    return tree, SAME_PRODUCT.get(idn, idn)


def context_text(s):
    """Context lines without their line-number prefix (dedup must ignore shifted lines)."""
    return "\n".join(RE_NUM.sub("", l) for l in s["context"]["lines"])


def referenced_files(sites):
    """Every file path a set of site records points at (besides their own)."""
    files = set()
    for s in sites:
        files.add(s["file"])
        for w in s.get("wrapper_ref") or []: files.add(w["def_file"])
        for d in s.get("instance_domains") or []:
            if d.get("loc"): files.add(d["loc"].rsplit(":", 1)[0])
        for c in ((s.get("callers") or {}).get("sites") or []): files.add(c["file"])
        for src in ([(s.get("key") or {}).get("source", "")] + [k.get("source", "") for w in (s.get("wrapper_ref") or []) for k in w.get("keys", [])]):
            m = re.search(r"@ (.+?):\d+", src or "")
            if m: files.add(m.group(1))
        for d in s.get("instance_domains") or []:
            m = re.search(r"@ (.+?):\d+", d.get("note") or "")
            if m: files.add(m.group(1))
    return files


def unit_level_files(loc, u, src):
    """Manifests and project / package / podspec files of a unit directory."""
    out = set(u.get("manifests") or [])
    udir = src / loc
    for e in ((u.get("build_facts") or {}).get("projects") or []): out.add(str(pathlib.PurePosixPath(e["path"]) / "project.pbxproj"))
    for rel in ((u.get("build_facts") or {}).get("packages") or {}): out.add(str(pathlib.PurePosixPath(rel) / "Package.swift") if rel != "." else "Package.swift")
    if udir.is_dir():
        for p in list(udir.glob("*.podspec")) + list(udir.glob("*.podspec.json")): out.add(p.name)
    return out


def copy_files(src, loc, files, out_src):
    """Copy `files` of unit `loc` into out_src/loc/…; return [{path, sha1, n_lines}] for the ones found."""
    rec = []
    for rel in sorted(files):
        p = src / loc / rel
        if not p.is_file(): rec.append({"path": rel, "missing": True}); continue
        dst = out_src / loc / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists(): shutil.copyfile(p, dst)
        data = p.read_bytes()
        rec.append({"path": rel, "sha1": hashlib.sha1(data).hexdigest()[:12], "n_lines": data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1)})
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--worksheets", required=True, help="scan_source_rra 的 --out-dir")
    ap.add_argument("--out", required=True, help="批次输出目录")
    ap.add_argument("--src", default=None, help="scan_source_rra 的 --src；给了就把批次引用到的源文件复制到 <out>/src/ 作为补充")
    ap.add_argument("--size", type=int, default=40, help="每批最多站点数")
    ap.add_argument("--units", default=None, help="只切这些单元（unit_location，逗号分隔），如 repos/x,deps/y@rev")
    ap.add_argument("--exclude-units", default=None, help="不切这些单元（unit_location，逗号分隔）")
    ap.add_argument("--no-dedup", action="store_true", help="不做跨版本去重")
    ap.add_argument("--only-new-vs", default=None, help="只切这个工作表目录里没有的 site_id（增补扫描后的补充批次）")
    ap.add_argument("--prefix", default="b", help="批次文件名前缀（补充批次用 a，避免与 b#### 撞名）")
    a = ap.parse_args(argv)
    old_ids = set()
    if a.only_new_vs:
        for f in sorted(pathlib.Path(a.only_new_vs).glob("*.jsonl")):
            for line in io.open(f, encoding="utf-8"): old_ids.add(json.loads(line)["site_id"])

    ws = pathlib.Path(a.worksheets); out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    src = pathlib.Path(a.src) if a.src else None
    units_json = json.loads((ws / "_units.json").read_text(encoding="utf-8"))
    only = set(a.units.split(",")) if a.units else None
    skip = set(a.exclude_units.split(",")) if a.exclude_units else set()

    by_unit = {}
    for f in sorted(ws.glob("*.jsonl")):
        for line in io.open(f, encoding="utf-8"):
            s = json.loads(line)
            if only and s["unit_location"] not in only: continue
            if s["unit_location"] in skip: continue
            if old_ids and s["site_id"] in old_ids: continue
            by_unit.setdefault(s["unit_location"], []).append(s)
    by_unit = collections.OrderedDict(sorted(by_unit.items(), key=lambda kv: (TREE_ORDER.get(kv[0].split("/")[0], 9), kv[0].lower())))

    # --- dedup across revisions / identities of the same product (deps and pods only)
    dup_of = {}; pairs = []
    if not a.no_dedup:
        groups = collections.defaultdict(list)
        for loc, sites in by_unit.items():
            tree, idn = ident(loc)
            if tree == "repos": continue
            for s in sites:
                groups[(idn, s["file"], s["col"], s["api"], context_text(s))].append(s)
        for v in groups.values():
            per_unit = collections.OrderedDict()
            for s in sorted(v, key=lambda s: (s["unit_location"], s["line"])): per_unit.setdefault(s["unit_location"], []).append(s)
            if len(per_unit) < 2: continue                       # two sites of the same unit are never copies of each other
            (rep_loc, reps), *others = per_unit.items()
            for loc, sites in others:
                if len(sites) != len(reps): continue             # a unit with a different number of identical sites: not a clean copy, keep all
                for s, rep in zip(sites, reps):
                    dup_of[s["site_id"]] = rep["site_id"]
                    pairs.append({"dup": s["site_id"], "rep": rep["site_id"], "dup_unit": loc, "rep_unit": rep_loc,
                                  "same_declared_reasons": s["declared_reasons"] == rep["declared_reasons"],
                                  "same_guard": s["guard_live_on_ios"] == rep["guard_live_on_ios"]})

    # --- batches
    manifest = {"provenance": units_json.get("provenance"), "batch_size": a.size, "batches": [], "duplicates": pairs,
                "excluded_units": sorted(skip), "only_new_vs": a.only_new_vs, "n_old_site_ids": len(old_ids), "supplement": {},
                "counts": {"sites_total": 0, "sites_batched": 0, "sites_copied_from_duplicates": len(dup_of)}}
    n = 0
    for loc, sites in by_unit.items():
        sites.sort(key=lambda s: (s["file"], s["line"], s["col"]))
        todo = [s for s in sites if s["site_id"] not in dup_of]
        manifest["counts"]["sites_total"] += len(sites)
        if not todo: continue
        u = units_json["units"].get(loc, {})
        reasons = {}
        for s in sites:
            for cat, rr in (s.get("reason_constraints") or {}).items():
                for code, r in rr.items():
                    reasons.setdefault(code, {"category": cat, "title": r.get("title"), "restrictions": r.get("restrictions"),
                                              "constraints": [{k: c.get(k) for k in ("id", "type", "predicate")} for c in r.get("constraints", [])]})
        bf = u.get("build_facts") or {}
        header = {"_batch": None, "unit_location": loc, "unit_kind_default": sites[0]["unit_kind"],
                  "repo": sites[0].get("repo"), "sha": sites[0].get("sha"),
                  "manifests": u.get("manifests", sites[0].get("unit_manifests_all")), "declares": u.get("declares"),
                  "hosts": sites[0].get("hosts"), "unit_macros": u.get("unit_macros"), "wrapper_methods": u.get("wrapper_methods"),
                  "build_facts": {"primary_app_target": bf.get("primary_app_target"),
                                  "projects": [{"path": e["path"], "release_config": e.get("release_config"),
                                                "targets": [{k: t.get(k) for k in ("name", "kind", "manifests", "release_swift_flags", "flags_complete", "bundle_id", "entitlements", "app_groups")} for t in e.get("targets", [])]}
                                               for e in bf.get("projects", [])],
                                  "packages": bf.get("packages"), "podspec": bf.get("podspec"), "notes": bf.get("notes")} if bf else None,
                  "ud_members": sorted(u.get("ud_members", {}).keys()) if u.get("ud_members") else None,
                  "reasons": reasons, "principles": "annot/ANNOTATION_PRINCIPLES.md"}
        unit_files = None
        if src is not None:
            unit_files = copy_files(src, loc, unit_level_files(loc, u, src), out / "src")
            manifest["supplement"][loc] = {"unit_files": unit_files, "files": []}
        for i in range(0, len(todo), a.size):
            chunk = todo[i:i + a.size]; n += 1
            name = f"{a.prefix}{n:04d}__{loc.replace('/', '__')}.jsonl"
            h = dict(header); h["_batch"] = name; h["n_sites"] = len(chunk); h["site_ids"] = [s["site_id"] for s in chunk]
            if src is not None:
                h["source_dir"] = f"src/{loc}"
                h["source_files"] = copy_files(src, loc, referenced_files(chunk), out / "src")
                h["unit_files"] = unit_files
                manifest["supplement"][loc]["files"] = sorted({f["path"] for f in manifest["supplement"][loc]["files"]} | {f["path"] for f in h["source_files"]})
                manifest["supplement"][loc]["files"] = [{"path": p} for p in manifest["supplement"][loc]["files"]]
            with io.open(out / name, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(h, ensure_ascii=False) + "\n")
                for s in chunk:
                    r = {k: v for k, v in s.items() if k not in SITE_DROP}
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            manifest["batches"].append({"file": name, "unit_location": loc, "n": len(chunk), "site_ids": h["site_ids"],
                                        "files": sorted({s["file"] for s in chunk})})
            manifest["counts"]["sites_batched"] += len(chunk)
    (out / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    c = manifest["counts"]
    print(f"单元 {len(by_unit)}，站点 {c['sites_total']}，去重复制 {c['sites_copied_from_duplicates']}，进批次 {c['sites_batched']}，批次 {n} 个（每批 ≤{a.size}）"
          + (f"；补充源文件 {sum(len(v['files']) + len(v['unit_files']) for v in manifest['supplement'].values())} 个 → {out}/src/" if src is not None else ""))
    print(f"输出 {out}/{a.prefix}####__<unit>.jsonl + MANIFEST.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
