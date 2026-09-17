#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fetch the source of every benchmark repo at its pinned SHA, plus every
SwiftPM dependency at the revision its Package.resolved pins.

WHY BY SHA / REVISION
---------------------
The binaries and maps were built from one commit per repo and one revision per
package (Xcode resolves from Package.resolved).  Source fetched at any other
revision would describe a different program.  GitHub serves arbitrary reachable
commits with `git fetch --depth 1 origin <sha>`, so no history is downloaded.

LAYOUT
------
  <out>/repos/<slug>/                  repo at pinned SHA
  <out>/deps/<identity>@<rev12>/       one checkout per (package, revision),
                                       shared across repos that pin the same
  <out>/fetch_report.json              what was asked for, what landed, what
                                       failed -- conservation is checked

WHAT IS NOT FETCHED
-------------------
CocoaPods.  Podfile.lock names pods and versions but not git URLs; resolving
those needs the Specs index.  Pods are recorded per repo (name + version) and
reported as NOT_FETCHED so the gap is visible, not silent.  Pods vendored as
binaries have no source anywhere.
"""

import argparse
import io
import json
import pathlib
import re
import subprocess
import sys


def sh(cmd, cwd=None, timeout=600):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"


def fetch_at(url, rev, dest):
    """Shallow-fetch one commit into dest.  Returns error string or None."""
    dest = pathlib.Path(dest)
    if (dest / ".git").is_dir() and (dest / ".fetched_ok").is_file():
        return None                                   # resumable
    dest.mkdir(parents=True, exist_ok=True)
    for cmd in (["git", "init", "-q"],
                ["git", "remote", "add", "origin", url],
                ["git", "fetch", "-q", "--depth", "1", "origin", rev],
                ["git", "checkout", "-q", "FETCH_HEAD"]):
        rc, out = sh(cmd, cwd=dest)
        if rc != 0 and not (cmd[1] == "remote" and "already exists" in out):
            return f"{' '.join(cmd[:3])}: rc={rc} {out.strip()[-200:]}"
    (dest / ".fetched_ok").write_text(rev)
    return None


def slug(repo):
    return re.sub(r"[^A-Za-z0-9._-]", "-", repo)


def find_resolved(root):
    out = []
    for p in pathlib.Path(root).rglob("Package.resolved"):
        if any(part in (".build", "Pods", "Carthage", "node_modules") for part in p.parts):
            continue
        out.append(p)
    return out


def parse_pins(path):
    """Package.resolved v1 (object.pins) and v2/v3 (pins).  Yields (identity, url, rev)."""
    try:
        d = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    pins = d.get("pins") or (d.get("object") or {}).get("pins") or []
    out = []
    for p in pins:
        url = p.get("location") or p.get("repositoryURL")
        rev = (p.get("state") or {}).get("revision")
        ident = p.get("identity") or p.get("package") or (url or "").rstrip("/").split("/")[-1].removesuffix(".git")
        if url and rev:
            out.append((ident.lower(), url, rev))
    return out


def parse_podfile_lock(path):
    """Top-level PODS: entries -> [(name, version)].  Subspecs collapse to the pod.

    CocoaPods writes an entry as YAML, so a pod whose name needs quoting gets
    the WHOLE entry quoted:  `  - "NSObject+Rx (5.2.2)"`.  The first version of
    this regex let the opening quote into the name group and produced the pod
    `"NSObject+Rx`, which then 404'd against the Specs index -- a fabricated
    gap.  Quotes are excluded from the name and allowed around the entry.
    """
    try:
        txt = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    pods = []
    in_pods = False
    for line in txt.splitlines():
        if line.startswith("PODS:"):
            in_pods = True; continue
        if in_pods and line and not line.startswith(" "):
            break
        m = re.match(r'^  - "?([^\s"(/]+)(?:/[^\s"(]+)? \(([^)]+)\)"?', line)
        if in_pods and m:
            pods.append((m.group(1), m.group(2)))
    return sorted(set(pods))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-compare", required=True, help="compare_configs 输出，取 complete")
    ap.add_argument("--targets", required=True, nargs="+", help="targets/*.json，取 SHA")
    ap.add_argument("--out", "--json", dest="out", required=True, help="源码落地目录")
    ap.add_argument("--only", default=None, help="只拉这些仓库（逗号分隔），调试用")
    a = ap.parse_args(argv)

    wanted = json.loads(pathlib.Path(a.from_compare).read_text(encoding="utf-8"))["complete"]
    if a.only:
        keep = {s.strip() for s in a.only.split(",")}
        wanted = [r for r in wanted if r in keep]
    sha = {}
    for t in a.targets:
        doc = json.loads(pathlib.Path(t).read_text(encoding="utf-8"))
        for r in (doc if isinstance(doc, list) else doc.get("targets") or []):
            if r.get("repo") and r.get("sha"):
                sha.setdefault(r["repo"], r["sha"])
    missing = [r for r in wanted if r not in sha]
    if missing:
        sys.exit(f"targets 里找不到 SHA：{missing}")

    out = pathlib.Path(a.out); (out / "repos").mkdir(parents=True, exist_ok=True); (out / "deps").mkdir(exist_ok=True)
    report = {"repos": {}, "deps": {}}
    dep_cache = {}
    for i, repo in enumerate(wanted, 1):
        dest = out / "repos" / slug(repo)
        err = fetch_at(f"https://github.com/{repo}.git", sha[repo], dest)
        rec = {"sha": sha[repo], "fetched": err is None, "error": err,
               "resolved_files": [], "pins": [], "pins_fetched": 0, "pins_failed": [],
               "podfile_lock": None, "pods": []}
        print(f"[{i}/{len(wanted)}] {repo} @ {sha[repo][:10]}  " + ("OK" if not err else f"FAIL {err[:80]}"), flush=True)
        if not err:
            seen = {}
            for rp in find_resolved(dest):
                rec["resolved_files"].append(str(rp.relative_to(dest)))
                for ident, url, rev in parse_pins(rp):
                    seen[(ident, rev)] = url
            for (ident, rev), url in sorted(seen.items()):
                key = f"{ident}@{rev[:12]}"
                rec["pins"].append(key)
                if key not in dep_cache:
                    derr = fetch_at(url, rev, out / "deps" / key)
                    dep_cache[key] = derr
                    report["deps"][key] = {"url": url, "revision": rev, "fetched": derr is None, "error": derr}
                if dep_cache[key] is None:
                    rec["pins_fetched"] += 1
                else:
                    rec["pins_failed"].append(key)
            pl = next(iter(p for p in dest.rglob("Podfile.lock") if "Pods" not in p.parts), None)
            if pl:
                rec["podfile_lock"] = str(pl.relative_to(dest))
                rec["pods"] = [{"name": n, "version": v, "status": "NOT_FETCHED"} for n, v in parse_podfile_lock(pl)]
            print(f"        pins {rec['pins_fetched']}/{len(rec['pins'])} fetched"
                  + (f"，{len(rec['pins_failed'])} 失败" if rec["pins_failed"] else "")
                  + (f"；Podfile.lock 有 {len(rec['pods'])} 个 pod（未拉）" if rec["pods"] else ""), flush=True)
        report["repos"][repo] = rec

    ok = sum(1 for r in report["repos"].values() if r["fetched"])
    fail = len(report["repos"]) - ok
    if ok + fail != len(wanted):
        sys.exit("CONSERVATION FAILED")
    ndep_ok = sum(1 for d in report["deps"].values() if d["fetched"])
    npods = sum(len(r["pods"]) for r in report["repos"].values())
    report["summary"] = {"repos_requested": len(wanted), "repos_fetched": ok, "repos_failed": fail,
                         "deps_unique": len(report["deps"]), "deps_fetched": ndep_ok,
                         "pods_listed_not_fetched": npods}
    with io.open(out / "fetch_report.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"\n仓库 {ok}/{len(wanted)} 拉到；SwiftPM 依赖去重后 {len(report['deps'])} 个，拉到 {ndep_ok}；"
          f"CocoaPods 列出 {npods} 个未拉。报告 {out/'fetch_report.json'}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
