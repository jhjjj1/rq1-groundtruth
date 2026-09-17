#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve every CocoaPods dependency to its upstream source at the LOCKED
version and fetch it, so pods get the same treatment as SwiftPM deps:
fetched whether or not they end up linked into any binary.

WHY THIS IS NEEDED AT ALL
-------------------------
Podfile.lock names a pod and its exact version but never its git URL.  The URL
lives in the CocoaPods trunk Specs index, sharded by the MD5 of the pod name:

    https://cdn.cocoapods.org/Specs/<m[0]>/<m[1]>/<m[2]>/<Name>/<Ver>/<Name>.podspec.json
    m = md5(Name).hexdigest()      # verified: SDWebImage -> 1/1/7

(The CDN 302-redirects to a jsdelivr mirror; curl -L follows it.)  The
podspec's `source` is one of: git+tag, git+commit, or http+zip.  A pod
published only on a private Specs repo 404s on trunk and is recorded, not
guessed.

UNIFORM TREATMENT
-----------------
Every pod in every repo's Podfile.lock is resolved and fetched, including the
handful whose source the repo happened to commit.  Fetched source is upstream
at the locked version -- the authoritative form -- so the scanner can prefer
it and treat all pods identically, falling back to committed in-repo source
only when upstream cannot be reached.

CONSERVATION
------------
requested = fetched + failed, per (pod, version).  Subspecs (`Pod/Sub`)
collapse to their root pod.  Every failure keeps its reason
(PODSPEC_404 / NO_GIT_SOURCE / FETCH_FAILED / HTTP_SOURCE_NOT_FETCHED) so the
gap is a number, not a silence.
"""

import argparse
import hashlib
import io
import json
import pathlib
import re
import subprocess
import sys
import urllib.request


def sh(cmd, cwd=None, timeout=600):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"


def podspec_urls(name, ver):
    """Trunk CDN first, then the jsdelivr mirror the CDN 302s to directly.
    Trying the mirror explicitly means a proxy that mangles the redirect
    (mine 403s it) does not turn a resolvable pod into a false 404."""
    m = hashlib.md5(name.encode()).hexdigest()
    shard = f"{m[0]}/{m[1]}/{m[2]}"
    return [
        f"https://cdn.cocoapods.org/Specs/{shard}/{name}/{ver}/{name}.podspec.json",
        f"https://cdn.jsdelivr.net/cocoa/Specs/{shard}/{name}/{ver}/{name}.podspec.json",
    ]


def get_podspec(name, ver, timeout=30):
    """Return (spec_dict, error).  404 on both hosts is authoritative
    (pod/version not on trunk); any other error is reported distinctly."""
    last = None
    for url in podspec_urls(name, ver):
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r), None
        except urllib.error.HTTPError as e:
            last = f"PODSPEC_HTTP_{e.code}"
            if e.code != 404:
                continue                 # try the mirror on 403/5xx
        except Exception as e:           # noqa: BLE001
            last = f"PODSPEC_ERR_{type(e).__name__}"
    return None, last


def git_fetch(url, refs, dest, submodules=False, timeout=300):
    """Try each ref in order; return (used_ref, error).  Shallow, no history."""
    dest = pathlib.Path(dest)
    ok = dest / ".fetched_ok"
    if ok.is_file():
        return ok.read_text().strip(), None
    dest.mkdir(parents=True, exist_ok=True)
    rc, out = sh(["git", "init", "-q"], cwd=dest, timeout=60)
    if rc != 0:
        return None, f"git init rc={rc}"
    sh(["git", "remote", "add", "origin", url], cwd=dest, timeout=60)
    tried, last = [], ""
    seen = set()
    for ref in [r for r in refs if r and not (r in seen or seen.add(r))]:
        tried.append(ref)
        rc, out = sh(["git", "fetch", "-q", "--depth", "1", "origin", ref],
                     cwd=dest, timeout=timeout)
        if rc != 0:
            last = f"fetch {ref}: {out.strip()[-120:]}"
            continue
        rc, out = sh(["git", "checkout", "-q", "FETCH_HEAD"], cwd=dest, timeout=120)
        if rc != 0:
            last = f"checkout {ref}: {out.strip()[-120:]}"
            continue
        if submodules:
            # couchbase 的源码要子模块才完整；失败只记不致命
            sh(["git", "submodule", "update", "--init", "--recursive", "--depth", "1"],
               cwd=dest, timeout=timeout)
        ok.write_text(ref)
        return ref, None
    return None, f"试过 {tried} 都不行；最后一次：{last}" if tried else "no ref to try"


def resolve_source(spec):
    """(kind, ref, url) from a podspec source dict.  kind in git/http/none."""
    src = spec.get("source") or {}
    if src.get("git"):
        return "git", (src.get("tag") or src.get("commit") or src.get("branch")), src["git"]
    if src.get("http"):
        return "http", None, src["http"]
    return "none", None, None


#: Pod 名 -> 上游 git 仓库。**只提供地址，不提供版本**：版本一律取盘上
#: Podfile.lock（按钉死 SHA 拉下来的那份），因为这张表是照各仓库 master 分支
#: 的锁文件整理的，与基准集钉的 commit 可能不是同一批 pod/版本。
#: 用途是 CocoaPods 索引查不到时的兜底，索引查得到时以索引为准。
#: 来源：用户提供的逐项核对表（2026-09），未经本脚本独立验证。
FALLBACK_GIT = {
    "Alamofire": "https://github.com/Alamofire/Alamofire.git",
    "CryptoSwift": "https://github.com/krzyzanowskim/CryptoSwift.git",
    "DefaultsKit": "https://github.com/nmdias/DefaultsKit.git",
    "Differentiator": "https://github.com/RxSwiftCommunity/RxDataSources.git",
    "DropDown": "https://github.com/AssistoLab/DropDown.git",
    "FDFullscreenPopGesture": "https://github.com/forkingdog/FDFullscreenPopGesture.git",
    "ImageViewer.swift": "https://github.com/Finb/ImageViewer.swift.git",
    "IQKeyboardCore": "https://github.com/hackiftekhar/IQKeyboardCore.git",
    "IQKeyboardManagerSwift": "https://github.com/hackiftekhar/IQKeyboardManager.git",
    "IQKeyboardNotification": "https://github.com/hackiftekhar/IQKeyboardNotification.git",
    "IQKeyboardToolbar": "https://github.com/hackiftekhar/IQKeyboardToolbar.git",
    "IQKeyboardToolbarManager": "https://github.com/hackiftekhar/IQKeyboardToolbarManager.git",
    "IQTextInputViewNotification": "https://github.com/hackiftekhar/IQTextInputViewNotification.git",
    "Kingfisher": "https://github.com/onevcat/Kingfisher.git",
    "Material": "https://github.com/CosmicMind/Material.git",
    "MercariQRScanner": "https://github.com/Finb/QRScanner.git",
    "MJRefresh": "https://github.com/CoderMJLee/MJRefresh.git",
    "Motion": "https://github.com/CosmicMind/Motion.git",
    "Moya": "https://github.com/Moya/Moya.git",
    "NSObject+Rx": "https://github.com/RxSwiftCommunity/NSObject-Rx.git",
    "ObjectMapper": "https://github.com/tristanhimmelman/ObjectMapper.git",
    "Realm": "https://github.com/realm/realm-swift.git",
    "RealmSwift": "https://github.com/realm/realm-swift.git",
    "RxCocoa": "https://github.com/ReactiveX/RxSwift.git",
    "RxDataSources": "https://github.com/RxSwiftCommunity/RxDataSources.git",
    "RxGesture": "https://github.com/RxSwiftCommunity/RxGesture.git",
    "RxRelay": "https://github.com/ReactiveX/RxSwift.git",
    "RxSwift": "https://github.com/ReactiveX/RxSwift.git",
    "SnapKit": "https://github.com/SnapKit/SnapKit.git",
    "SVProgressHUD": "https://github.com/SVProgressHUD/SVProgressHUD.git",
    "SwiftyJSON": "https://github.com/SwiftyJSON/SwiftyJSON.git",
    "SwiftyStoreKit": "https://github.com/bizz84/SwiftyStoreKit.git",
    "Cache": "https://github.com/hyperoslo/Cache.git",
    "Charts": "https://github.com/danielgindi/Charts.git",
    "CombineExt": "https://github.com/CombineCommunity/CombineExt.git",
    "CoreGPX": "https://github.com/vincentneo/CoreGPX.git",
    "CoreStore": "https://github.com/JohnEstropia/CoreStore.git",
    "SwiftAlgorithms": "https://github.com/apple/swift-algorithms.git",
    "iOSSnapshotTestCase": "https://github.com/uber/ios-snapshot-test-case.git",
    # CocoaPods 分发的是预编译 zip，但源码仓库存在且带 tag；需要递归子模块
    "couchbase-lite-ios": "https://github.com/couchbase/couchbase-lite-ios.git",
}
#: 源码需要子模块才完整的 pod
NEEDS_SUBMODULES = {"couchbase-lite-ios"}

#: podspec 给不出 tag 时，按版本号猜的候选（仅用于兜底路径，会记录实际命中的）
def tag_candidates(ver):
    return [ver, f"v{ver}", ver.lstrip("v")]


RE_EXT_HEAD = re.compile(r"^(EXTERNAL SOURCES|CHECKOUT OPTIONS):\s*$")
RE_EXT_POD = re.compile(r'^  "?([^\s":]+)"?:\s*$')     # 名字可能被 YAML 加引号
RE_EXT_KV = re.compile(r"^    :(\w+):\s*(.+?)\s*$")


def parse_external_sources(lock_text):
    """pod -> {path|git|commit|tag|branch} from EXTERNAL SOURCES / CHECKOUT OPTIONS.

    Podfile.lock records where a non-trunk pod actually comes from.  Missing
    this section is how kudoleh/iOS-Modular-Architecture's three DevPods
    (Networking, Authentication, MoviesSearch) were classified NOT_IN_REPO:
    they are `:path: DevPods/<name>` -- source committed in the repo, and not
    on trunk at all, so a CDN lookup would 404 and invent a gap that is not
    there.  `SPEC REPOS: trunk:` in that same lock lists only the two pods
    that really are published.
    """
    ext, cur, inside = {}, None, False
    for line in lock_text.splitlines():
        if RE_EXT_HEAD.match(line):
            inside, cur = True, None
            continue
        if inside and line and not line.startswith(" "):
            inside, cur = False, None
            continue
        if not inside:
            continue
        m = RE_EXT_POD.match(line)
        if m:
            cur = m.group(1).split("/")[0]
            ext.setdefault(cur, {})
            continue
        m = RE_EXT_KV.match(line)
        if m and cur:
            ext[cur][m.group(1)] = m.group(2)
    return ext


def find_lock(repo_dir):
    for p in pathlib.Path(repo_dir).rglob("Podfile.lock"):
        if "Pods" not in p.parts:
            return p
    return None


def collect_pods(report, repos_dir):
    """(name, version) -> {"repos": [...], "external": {...}}.

    Subspecs are already collapsed by fetch_sources.  External-source info is
    read from each repo's Podfile.lock on disk, so this does not need
    fetch_sources to be re-run.
    """
    pods = {}
    for repo, rec in report["repos"].items():
        if not rec.get("pods"):
            continue
        slug = re.sub(r"[^A-Za-z0-9._-]", "-", repo)
        lock = find_lock(pathlib.Path(repos_dir) / slug)
        ext = parse_external_sources(lock.read_text(encoding="utf-8", errors="replace")) if lock else {}
        for p in rec["pods"]:
            e = pods.setdefault((p["name"], p["version"]), {"repos": [], "external": {}})
            e["repos"].append(repo)
            if p["name"] in ext:
                e["external"][repo] = ext[p["name"]]
    for e in pods.values():
        e["repos"] = sorted(set(e["repos"]))
    return pods


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, help="fetch_sources.py 的 --out 目录（含 fetch_report.json）")
    ap.add_argument("--out", default=None, help="pod 源码落地目录，默认 <src>/pods")
    a = ap.parse_args(argv)

    src = pathlib.Path(a.src)
    report = json.loads((src / "fetch_report.json").read_text(encoding="utf-8"))
    pods = collect_pods(report, src / "repos")
    out = pathlib.Path(a.out) if a.out else (src / "pods")
    out.mkdir(parents=True, exist_ok=True)

    result = {}
    counts = {"git_fetched": 0, "local_path": 0, "http_source": 0, "podspec_404": 0,
              "no_git_source": 0, "fetch_failed": 0}
    print(f"要解析的 (pod, 版本) 共 {len(pods)} 个\n")
    for i, ((name, ver), info) in enumerate(sorted(pods.items()), 1):
        key = f"{name}@{ver}"
        repos, ext = info["repos"], info["external"]
        rec = {"name": name, "version": ver, "repos": repos, "external": ext,
               "status": None, "git": None, "ref": None, "http": None,
               "local_path": None, "error": None}

        # 本地路径 pod：源码就在仓库里，不在 trunk 上，查 CDN 必然 404
        paths = {r: e["path"] for r, e in ext.items() if e.get("path")}
        if paths:
            rec["status"] = "LOCAL_PATH_IN_REPO"
            rec["local_path"] = paths
            counts["local_path"] += 1
            print(f"[{i}/{len(pods)}] {key:44s} LOCAL_PATH_IN_REPO  "
                  + "; ".join(f"{r}:{p}" for r, p in paths.items()), flush=True)
            result[key] = rec; continue

        # 地址解析顺序：锁文件内 :git: → CocoaPods 索引 → 内置兜底表。
        # 版本永远来自锁文件，绝不来自兜底表 —— 兜底表照 master 分支整理，
        # 与基准集钉的 commit 可能不是同一批 pod/版本。
        url = ref_hint = None; how = None
        gits = [e for e in ext.values() if e.get("git")]
        if gits:
            g = gits[0]
            url = g["git"]; ref_hint = g.get("commit") or g.get("tag") or g.get("branch")
            how = "lock"
        else:
            spec, err = get_podspec(name, ver)
            if spec:
                kind, t, u = resolve_source(spec)
                if kind == "git":
                    url, ref_hint, how = u, t, "podspec"
                elif kind == "http":
                    rec["http"] = u
                    if name in FALLBACK_GIT:          # couchbase 走这里
                        url, how = FALLBACK_GIT[name], "fallback(http源)"
                    else:
                        rec["status"] = "HTTP_SOURCE_NOT_FETCHED"
                        counts["http_source"] += 1
                        print(f"[{i}/{len(pods)}] {key:44s} HTTP_SOURCE_NOT_FETCHED  {u}", flush=True)
                        result[key] = rec; continue
            else:
                rec["error"] = err
                if name in FALLBACK_GIT:
                    url, how = FALLBACK_GIT[name], "fallback(索引取不到)"
                else:
                    rec["status"] = "PODSPEC_404" if (err or "").endswith("404") else "PODSPEC_UNREACHABLE"
                    counts["podspec_404"] += 1
                    print(f"[{i}/{len(pods)}] {key:44s} {rec['status']}  ({err})", flush=True)
                    result[key] = rec; continue

        if not url:
            rec["status"] = "NO_GIT_SOURCE"; counts["no_git_source"] += 1
            print(f"[{i}/{len(pods)}] {key:44s} NO_GIT_SOURCE", flush=True)
            result[key] = rec; continue

        used, ferr = git_fetch(url, [ref_hint] + tag_candidates(ver),
                               out / key, submodules=(name in NEEDS_SUBMODULES))
        rec.update(git=url, ref=used, resolved_by=how)
        if ferr:
            rec["status"] = "FETCH_FAILED"; rec["error"] = ferr; counts["fetch_failed"] += 1
        else:
            rec["status"] = "FETCHED"; counts["git_fetched"] += 1
        print(f"[{i}/{len(pods)}] {key:44s} {rec['status']} [{how}]  {url} @ {used or ref_hint}"
              + (f"  ({ferr})" if ferr else ""), flush=True)
        result[key] = rec
        with io.open(out / "pods_report.json", "w", encoding="utf-8") as fh:
            json.dump({"in_progress": True, "pods": result}, fh, ensure_ascii=False, indent=1)

    fetched = sum(1 for r in result.values() if r["status"] == "FETCHED")
    if fetched + (len(pods) - fetched) != len(pods):
        sys.exit("CONSERVATION FAILED")
    summary = {"pods_requested": len(pods), **counts}
    with io.open(out / "pods_report.json", "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "pods": result}, fh, ensure_ascii=False, indent=2)
    print(f"\n(pod,版本) {len(pods)} 个：git 拉到 {counts['git_fetched']}，"
          f"本地路径(源码已在仓库里) {counts['local_path']}，http 源 {counts['http_source']}，"
          f"podspec 取不到 {counts['podspec_404']}，无 git 源 {counts['no_git_source']}，"
          f"拉取失败 {counts['fetch_failed']}")
    nosrc = [k for k, r in result.items()
             if r["status"] in ("HTTP_SOURCE_NOT_FETCHED", "NO_GIT_SOURCE",
                                "PODSPEC_404", "PODSPEC_UNREACHABLE", "FETCH_FAILED")]
    print(f"仍然没有源码的 pod {len(nosrc)} 个" + (f"：{nosrc}" if nosrc else ""))
    print(f"报告 {out/'pods_report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
