#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check the scanner's manifest attribution against the manifests that actually shipped.

    compare_manifest_attribution.py --bundles DIR --worksheets DIR --src DIR
                                    [--config base] [--json report/manifest_attribution.json]

Until now "which PrivacyInfo.xcprivacy covers this code" has been an *inference*:
`covering_manifest()` picks the nearest ancestor manifest in the source tree, falls back to
the app-level one, and records how it decided in `manifest_scope`.  Nothing has ever checked
that inference, because the shipped package -- the only place where the answer is a fact --
was not collected.  Now it is.

Three comparisons, each answering a different question and reported separately:

1. **文件一级** -- every `PrivacyInfo.xcprivacy` in the source tree, matched to the shipped
   package by sha256 (Xcode copies the file verbatim, so identity is exact).  Answers: which
   source manifests shipped, where they landed, and which shipped manifests have no source
   counterpart (a dependency whose source is not in the corpus).
2. **申报一级** -- the (category, reason) pairs the scanner attributes to this app's own code
   versus the pairs the shipped package declares anywhere.  AGREE / SCANNER_OVER /
   SCANNER_UNDER, never merged: over-attribution and under-attribution are different defects
   with different fixes.
3. **推断机制一级** -- the same verdict broken down by `manifest_scope`, so a systematically
   wrong mechanism (say, every LOCAL_POD falling back to the app manifest) shows up as a
   pattern rather than as scattered noise.

This script judges nothing about the annotation and rewrites nothing.  A mismatch is recorded
with both sides' evidence; deciding whether the scanner or the build is right is a separate
step, and any scanner change belongs to v3.7.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import plistlib
import sys
import zipfile

#: Directory suffixes that make a bundle inside the package.
BUNDLE_SUFFIXES = (".app", ".appex", ".framework", ".bundle", ".systemextension")
MANIFEST_NAME = "PrivacyInfo.xcprivacy"


def declares_of(data):
    """{category: [reasons]} from one manifest's bytes -- same reading as the scanner's
    `read_manifest`, so the two sides are not compared through different parsers."""
    try:
        d = plistlib.loads(data)
    except Exception as exc:                                  # a malformed manifest is a fact
        return None, f"UNPARSEABLE: {type(exc).__name__}"
    out = {}
    for e in d.get("NSPrivacyAccessedAPITypes") or []:
        cat = (e.get("NSPrivacyAccessedAPIType") or "").replace("NSPrivacyAccessedAPICategory", "")
        if cat:
            out[cat] = sorted(e.get("NSPrivacyAccessedAPITypeReasons") or [])
    return out, None


def owning_component(member, app_root):
    """Which bundle a packaged manifest belongs to, as a path relative to the .app.

    `Payload/X.app/PlugIns/Y.appex/PrivacyInfo.xcprivacy` -> `PlugIns/Y.appex`;
    a manifest at the app root -> `.` (the app bundle itself).
    """
    rel = pathlib.PurePosixPath(member).relative_to(app_root)
    for i in range(len(rel.parts) - 1, 0, -1):
        part = rel.parts[i - 1]
        if part.endswith(BUNDLE_SUFFIXES):
            return "/".join(rel.parts[:i])
    return "."


def read_bundle(zpath):
    """Every manifest in one packaged app: component, declares, sha256."""
    out = {"app_root": None, "manifests": [], "components": [], "error": None}
    try:
        with zipfile.ZipFile(zpath) as zf:
            names = zf.namelist()
            roots = sorted({"/".join(n.split("/")[:2]) for n in names
                            if n.startswith("Payload/") and n.count("/") >= 2})
            if len(roots) != 1:
                out["error"] = f"PAYLOAD_ROOTS={len(roots)}"
                return out
            out["app_root"] = roots[0]
            out["components"] = sorted({owning_component(n, roots[0]) for n in names
                                        if any(p.endswith(BUNDLE_SUFFIXES) for p in n.split("/"))})
            for n in names:
                if not n.endswith("/" + MANIFEST_NAME):
                    continue
                data = zf.read(n)
                decl, err = declares_of(data)
                out["manifests"].append({
                    "member": n,
                    "component": owning_component(n, roots[0]),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "declares": decl, "parse_error": err,
                })
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        out["error"] = f"UNREADABLE: {type(exc).__name__}: {exc}"
    return out


def source_manifests(src, unit_location, paths):
    """The unit's manifests as they exist in the source tree, with sha256 for identity."""
    out = []
    for rel in paths or []:
        p = pathlib.Path(src) / unit_location / rel
        rec = {"unit": unit_location, "path": rel, "sha256": None, "declares": None, "error": None}
        try:
            data = p.read_bytes()
            rec["sha256"] = hashlib.sha256(data).hexdigest()
            rec["declares"], rec["error"] = declares_of(data)
        except OSError as exc:
            rec["error"] = f"MISSING_IN_SRC: {exc}"
        out.append(rec)
    return out


def pairs(declares):
    return {(c, r) for c, rs in (declares or {}).items() for r in (rs or [])}


def scanner_view(ws, unit_location):
    """What the scanner believes about this app: unit-level `declares` / `manifests`, and the
    per-site attribution (`manifest_scope`, `declared_reasons`) that RQ② is actually built on."""
    units = json.loads((pathlib.Path(ws) / "_units.json").read_text(encoding="utf-8"))["units"]
    u = units.get(unit_location) or {}
    view = {"declares": u.get("declares") or {}, "manifests": u.get("manifests") or [],
            "by_scope": collections.defaultdict(lambda: {"sites": 0, "pairs": set()}),
            "site_pairs": set(), "n_sites": 0, "unit_found": unit_location in units}
    f = pathlib.Path(ws) / (unit_location.replace("/", "__") + ".jsonl")
    if f.is_file():
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            s = json.loads(line)
            if s.get("site_class") != "RRA":
                continue
            view["n_sites"] += 1
            scope = s.get("manifest_scope") or "NONE"
            p = pairs(s.get("declared_reasons"))
            view["by_scope"][scope]["sites"] += 1
            view["by_scope"][scope]["pairs"] |= p
            view["site_pairs"] |= p
    return view


def compare_app(repo, unit_location, bundle, scan, src):
    """One app: file-level identity, declaration-level verdicts, and per-mechanism breakdown."""
    srcm = source_manifests(src, unit_location, scan["manifests"])
    by_sha = collections.defaultdict(list)
    for m in bundle["manifests"]:
        by_sha[m["sha256"]].append(m)

    files = []
    for m in srcm:
        hits = by_sha.get(m["sha256"]) or []
        files.append({**m, "shipped": bool(hits),
                      "shipped_as": [h["member"] for h in hits],
                      "shipped_components": sorted({h["component"] for h in hits})})
    src_shas = {m["sha256"] for m in srcm}
    extra = [{"member": m["member"], "component": m["component"], "sha256": m["sha256"],
              "declares": m["declares"]}
             for m in bundle["manifests"] if m["sha256"] not in src_shas]

    shipped_pairs = set()
    for m in bundle["manifests"]:
        shipped_pairs |= pairs(m["declares"])
    scan_pairs = scan["site_pairs"] or pairs(scan["declares"])

    verdicts = {"AGREE": sorted(f"{c}/{r}" for c, r in scan_pairs & shipped_pairs),
                "SCANNER_OVER": sorted(f"{c}/{r}" for c, r in scan_pairs - shipped_pairs),
                "SCANNER_UNDER": sorted(f"{c}/{r}" for c, r in shipped_pairs - scan_pairs)}

    by_scope = {}
    for scope, d in scan["by_scope"].items():
        p = d["pairs"]
        by_scope[scope] = {"sites": d["sites"],
                           "agree": sorted(f"{c}/{r}" for c, r in p & shipped_pairs),
                           "over": sorted(f"{c}/{r}" for c, r in p - shipped_pairs)}
    return {"repo": repo, "unit_location": unit_location,
            "bundle_error": bundle["error"], "app_root": bundle["app_root"],
            "n_components": len(bundle["components"]), "components": bundle["components"],
            "n_shipped_manifests": len(bundle["manifests"]),
            "n_source_manifests": len(srcm),
            "source_manifests": files,
            "shipped_without_source": extra,
            "scanner_sites": scan["n_sites"],
            "verdicts": verdicts, "by_scope": by_scope}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundles", required=True, help="fetch_run.py 拉回的目录，下面是 gt-<slug>-<config>/")
    ap.add_argument("--worksheets", required=True, help="scan_source_rra 的 --out-dir")
    ap.add_argument("--src", required=True, help="整个语料的源码根")
    ap.add_argument("--config", default="base", help="用哪个配置的包做比对（清单与配置无关，默认 base）")
    ap.add_argument("--style", default="none", help="用哪个 strip 档位的包（清单一样，默认 none）")
    ap.add_argument("--json", dest="out", default=None)
    ap.add_argument("--max-print", type=int, default=20)
    a = ap.parse_args(argv)

    jobs = {}
    for d in sorted(pathlib.Path(a.bundles).iterdir()):
        mp = d / "manifest.json"
        if not mp.is_file():
            continue
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if m.get("repo"):
            jobs.setdefault(m["repo"], {})[m.get("config_id") or "?"] = (d, m)
    print(f"产物目录里 {len(jobs)} 个 App")

    rows = []; skipped = []
    #: 清单不该随构建配置变。变了就是个发现，不能当噪声吞掉。
    cfg_diff = []
    for repo, byc in sorted(jobs.items()):
        unit_location = "repos/" + repo.replace("/", "-")
        scan = scanner_view(a.worksheets, unit_location)
        if not scan["unit_found"]:
            skipped.append({"repo": repo, "why": f"工作表里没有 {unit_location}"}); continue
        picked = byc.get(a.config) or next(iter(byc.values()))
        d, _m = picked
        z = d / f"bundle.{a.style}.ipa"
        if not z.is_file():
            skipped.append({"repo": repo, "why": f"没有 {z.name}"}); continue
        b = read_bundle(z)
        rows.append(compare_app(repo, unit_location, b, scan, a.src))
        # 跨配置一致性：同一个 App 的其余配置里，清单集合应当一模一样
        base_set = {(m["component"], m["sha256"]) for m in b["manifests"]}
        for cfg, (dd, _) in sorted(byc.items()):
            if dd == d: continue
            zz = dd / f"bundle.{a.style}.ipa"
            if not zz.is_file(): continue
            other = read_bundle(zz)
            s2 = {(m["component"], m["sha256"]) for m in other["manifests"]}
            if s2 != base_set:
                cfg_diff.append({"repo": repo, "config": cfg,
                                 "only_in_base": sorted(f"{c}@{s[:8]}" for c, s in base_set - s2),
                                 "only_in_other": sorted(f"{c}@{s[:8]}" for c, s in s2 - base_set)})

    agg = collections.Counter()
    scope_agg = collections.defaultdict(lambda: collections.Counter())
    for r in rows:
        for k, v in r["verdicts"].items():
            agg[k] += len(v)
        agg["apps"] += 1
        agg["apps_clean"] += not (r["verdicts"]["SCANNER_OVER"] or r["verdicts"]["SCANNER_UNDER"])
        agg["src_manifests"] += r["n_source_manifests"]
        agg["src_manifests_shipped"] += sum(1 for f in r["source_manifests"] if f["shipped"])
        agg["shipped_without_source"] += len(r["shipped_without_source"])
        for scope, d in r["by_scope"].items():
            scope_agg[scope]["sites"] += d["sites"]
            scope_agg[scope]["agree"] += len(d["agree"])
            scope_agg[scope]["over"] += len(d["over"])

    n = max(1, agg["apps"])
    print(f"\n比对了 {agg['apps']} 个 App，其中 {agg['apps_clean']} 个完全一致"
          f"（{agg['apps_clean'] / n:.0%}）；跳过 {len(skipped)} 个")
    print(f"\n申报一级（category/reason 对）：AGREE {agg['AGREE']}，"
          f"SCANNER_OVER {agg['SCANNER_OVER']}（扫描器说有、包里没有），"
          f"SCANNER_UNDER {agg['SCANNER_UNDER']}（包里有、扫描器没算上）")
    print(f"文件一级：源码里 {agg['src_manifests']} 份清单，其中 {agg['src_manifests_shipped']} 份进了包；"
          f"包里另有 {agg['shipped_without_source']} 份在源码清单之外（依赖自带的）")
    if scope_agg:
        print(f"\n按扫描器的推断机制分（over 集中在哪个机制上，就改哪个）：")
        print(f"  {'manifest_scope':<44}{'站点':>8}{'一致':>8}{'过报':>8}")
        for scope, c in sorted(scope_agg.items(), key=lambda kv: -kv[1]["over"]):
            print(f"  {scope:<44}{c['sites']:>8}{c['agree']:>8}{c['over']:>8}")
    bad = [r for r in rows if r["verdicts"]["SCANNER_OVER"] or r["verdicts"]["SCANNER_UNDER"]]
    if bad:
        print(f"\n不一致的 App（前 {min(len(bad), a.max_print)} 个）：")
        for r in bad[:a.max_print]:
            print(f"  {r['repo']}  组件 {r['n_components']} 个，包里清单 {r['n_shipped_manifests']} 份")
            if r["verdicts"]["SCANNER_OVER"]:
                print(f"      扫描器说有、包里没有: {', '.join(r['verdicts']['SCANNER_OVER'])}")
            if r["verdicts"]["SCANNER_UNDER"]:
                print(f"      包里有、扫描器没算上: {', '.join(r['verdicts']['SCANNER_UNDER'])}")
    if cfg_diff:
        print(f"\n同一 App 不同配置的清单集合不一致 {len(cfg_diff)} 处（本不该发生，要查）：")
        for c in cfg_diff[:a.max_print]:
            print(f"  {c['repo']} / {c['config']}: 只在 base {c['only_in_base']}，只在它 {c['only_in_other']}")
    for s in skipped[:a.max_print]:
        print(f"  跳过 {s['repo']}: {s['why']}")

    if a.out:
        pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(a.out).write_text(json.dumps(
            {"config": a.config, "style": a.style, "totals": dict(agg),
             "by_scope": {k: dict(v) for k, v in scope_agg.items()},
             "apps": rows, "skipped": skipped, "config_differences": cfg_diff},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n→ {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
