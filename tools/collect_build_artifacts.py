#!/usr/bin/env python3
"""Collect everything one build job produced, and account for what it did not.

Replaces the shell `Collect artifacts` step.  Two reasons it is Python now:

* The shell version had to be right about BSD `find` precedence, SIGPIPE under
  `pipefail`, and not copying files into a directory it was still walking.  It
  was wrong about the last two.
* A linker map's *format* is an open question here.  The runner has Xcode 26.6;
  nothing in this project has ever read a map produced by that toolchain.  So
  this script records the head of every map it finds verbatim, and the map
  parser gets written from those bytes rather than from what a map is supposed
  to look like.

Nothing here raises on a failed build: `Collect` runs with `if: always()`, and
a job whose build died still has to report *that it died*, with the same fields
as one that succeeded.  A missing artifact is recorded as missing, never as
absent-from-the-record.
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import os
import pathlib
import plistlib
import shutil
import subprocess
import sys

#: Filenames that could be a linker map.  Deliberately wide: `rra_link.map` is
#: what per_target mode asks for, `*-LinkMap-*` is Xcode's own default name,
#: and `*.map` catches anything else so an unexpected name shows up as an
#: observation instead of vanishing.
MAP_GLOBS = ("*-LinkMap-*", "*.map")

#: How many leading lines of each map to keep.  Enough to see the header block
#: (output path, arch, the start of the object-file table) without dragging a
#: multi-megabyte file into the manifest.
MAP_HEAD_LINES = 12

#: Which maps are worth uploading.
#:
#: Measured on IceCubesApp @ Xcode 26.2: the job produced 39 maps totalling
#: ~140 MB, of which one -- the app bundle's -- is the ground truth.  The other
#: 34 are `ld -r` prelink merges whose `# Path:` ends in `<Product>.o`; they
#: describe an intermediate, not a binary anyone analyses.  At 905 matrix jobs
#: uploading everything is ~100 GB of transient storage for data nobody reads.
#:
#: So: bundle maps are kept (gzipped), prelink maps are recorded in
#: maps_index.json with their size and header and not copied.  Dropped is not
#: the same as never-existed, and the index is what keeps the two apart.
#:
#: `.app/` does not match `.appex/` -- the slash matters.
KIND_APP = "APP_BUNDLE"
KIND_APPEX = "APPEX_BUNDLE"
KIND_PRELINK = "PRELINK_OBJECT"
KIND_OTHER = "OTHER"


def run(cmd, timeout=120):
    """Run a tool, return (rc, stdout).  Never raises."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, check=False)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, f"{type(exc).__name__}: {exc}"


def map_output_kind(head):
    """Classify a map by the binary its `# Path:` names."""
    first = head[0] if head else ""
    if not first.startswith("# Path:"):
        return KIND_OTHER, None
    out = first[len("# Path:"):].strip()
    if ".app/" in out:
        return KIND_APP, out
    if ".appex/" in out:
        return KIND_APPEX, out
    if out.endswith(".o"):
        return KIND_PRELINK, out
    return KIND_OTHER, out


def gzip_copy(src, dst):
    """Copy with gzip.  Maps are text and shrink ~8x; binaries ~3x."""
    with open(src, "rb") as fi, gzip.open(dst, "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo, length=1 << 20)
    return os.path.getsize(dst)


def head_lines(path, n=MAP_HEAD_LINES):
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= n:
                    break
                out.append(line.rstrip("\n")[:400])
    except OSError as exc:
        out.append(f"<unreadable: {exc}>")
    return out


def find_maps(roots, exclude, map_basename):
    """Walk for map candidates.  `exclude` is pruned so the copies this script
    makes are never re-discovered on a later root."""
    exclude = {os.path.realpath(e) for e in exclude}
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if os.path.realpath(dirpath) in exclude:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames
                           if os.path.realpath(os.path.join(dirpath, d)) not in exclude]
            for fn in filenames:
                if fn == map_basename or any(
                        pathlib.PurePath(fn).match(g) for g in MAP_GLOBS):
                    p = os.path.join(dirpath, fn)
                    try:
                        size = os.path.getsize(p)
                    except OSError:
                        continue
                    # A real map is never a handful of bytes; but keep small
                    # ones in the record rather than filtering them out --
                    # "found, and it was 12 bytes" is itself a finding.
                    found.append((p, size))
    found.sort(key=lambda t: -t[1])
    return found


def collect_maps(roots, out_dir, map_basename, limit_copy_bytes, keep_kinds):
    maps_dir = out_dir / "maps"
    maps_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    copied_bytes = 0
    for path, size in find_maps(roots, exclude=[str(maps_dir)],
                                map_basename=map_basename):
        rel = os.path.relpath(path, start=str(out_dir.parent))
        flat = rel.replace(os.sep, "_")
        head = head_lines(path)
        kind, output = map_output_kind(head)
        entry = {
            "src": path, "bytes": size, "output_kind": kind,
            "output_path": output,
            "is_requested_basename": os.path.basename(path) == map_basename,
            "head": head,
            "copied_as": None, "copied_bytes": None,
        }
        if kind not in keep_kinds:
            # 记在册，但不上传 —— 「丢掉」和「从未存在」不能长得一样
            entry["copy_error"] = f"NOT_UPLOADED_KIND_{kind}"
        elif copied_bytes + size > limit_copy_bytes:
            entry["copy_error"] = "SKIPPED_OVER_BUDGET"
        else:
            try:
                gz = gzip_copy(path, maps_dir / (flat + ".gz"))
                entry["copied_as"] = flat + ".gz"
                entry["copied_bytes"] = gz
                copied_bytes += size
            except OSError as exc:
                entry["copy_error"] = str(exc)
        entries.append(entry)
    return entries, copied_bytes


def collect_binary(derived_data, out_dir):
    """Find the built .app and copy its main executable out.

    Returns a dict that always has the same keys, so a job with no app and a
    job with an app produce comparable records.
    """
    info = {"app_bundle": None, "executable_name": None, "binary_bytes": None,
            "uuid": None, "arch": None, "collect_note": None}
    products = pathlib.Path(derived_data) / "Build" / "Products"
    if not products.is_dir():
        info["collect_note"] = "NO_BUILD_PRODUCTS_DIR"
        return info
    apps = sorted(products.glob("*/*.app")) + sorted(products.glob("*.app"))
    if not apps:
        info["collect_note"] = "NO_APP_BUNDLE_FOUND"
        return info
    app = apps[0]
    info["app_bundle"] = str(app)
    if len(apps) > 1:
        info["collect_note"] = f"MULTIPLE_APP_BUNDLES({len(apps)})"

    exe_name = None
    plist = app / "Info.plist"
    if plist.is_file():
        try:
            with open(plist, "rb") as fh:
                exe_name = (plistlib.load(fh) or {}).get("CFBundleExecutable")
        except Exception as exc:                      # malformed plist is a fact
            info["collect_note"] = f"INFO_PLIST_UNREADABLE: {exc}"
    if not exe_name:
        exe_name = app.stem
    info["executable_name"] = exe_name

    src = app / exe_name
    if not src.is_file():
        info["collect_note"] = "EXECUTABLE_MISSING_IN_BUNDLE"
        return info

    # 分析阶段要的是原样字节，所以先留一份未压缩的给 dwarfdump/otool 用，
    # 上传的是 .gz。实测 Release 主二进制 62 MB，gzip 后约三分之一。
    dst = pathlib.Path(out_dir) / "binary"
    try:
        shutil.copy2(src, dst)
        info["binary_bytes"] = dst.stat().st_size
    except OSError as exc:
        info["collect_note"] = f"BINARY_COPY_FAILED: {exc}"
        return info

    # LC_UUID is the only anchor tying a map to the binary it describes.
    rc, out = run(["dwarfdump", "--uuid", str(dst)])
    (pathlib.Path(out_dir) / "uuid.txt").write_text(out, encoding="utf-8")
    if rc == 0:
        parts = out.split()
        if len(parts) >= 2 and parts[0] == "UUID:":
            info["uuid"] = parts[1]
            info["arch"] = parts[2].strip("()") if len(parts) > 2 else None

    for name, cmd in (("loadcmds.txt", ["otool", "-l", str(dst)]),
                      ("size.txt", ["size", "-m", str(dst)]),
                      ("lipo.txt", ["lipo", "-info", str(dst)])):
        _rc, text = run(cmd, timeout=180)
        (pathlib.Path(out_dir) / name).write_text(text, encoding="utf-8")

    # 元数据都抽完了，再换成 .gz；原件删掉，否则 artifact 里两份都有
    try:
        info["binary_gz_bytes"] = gzip_copy(dst, pathlib.Path(out_dir) / "binary.gz")
        dst.unlink()
    except OSError as exc:
        info["collect_note"] = f"BINARY_GZIP_FAILED: {exc}"
    return info


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--derived-data", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--map-basename", default="rra_link.map")
    ap.add_argument("--max-copy-mb", type=float, default=400.0,
                    help="upload budget for copied maps（按未压缩大小计）")
    ap.add_argument("--keep-map-kinds", default=f"{KIND_APP},{KIND_APPEX}",
                    help="上传哪几类 map；其余只记账不上传")
    # Everything below is recorded verbatim.  These are the job's own facts;
    # this script judges none of them.
    for flag in ("repo", "sha", "config-id", "build-settings",
                 "pods-did-run", "spm-did-run",
                 "scheme", "scheme-verdict", "scheme-elapsed-s",
                 "map-mode", "map-verdict",
                 "build-outcome", "toolchain"):
        ap.add_argument(f"--{flag}", default=None)
    ap.add_argument("--out", "--json", dest="out", default=None,
                    help="manifest path; default <out-dir>/manifest.json")
    args = ap.parse_args(argv)

    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dd = args.derived_data

    keep = {k.strip() for k in args.keep_map_kinds.split(",") if k.strip()}
    maps, copied = collect_maps(
        roots=[dd, str(out_dir)], out_dir=out_dir,
        map_basename=args.map_basename,
        limit_copy_bytes=int(args.max_copy_mb * 1024 * 1024),
        keep_kinds=keep,
    )
    binary = collect_binary(dd, out_dir)

    requested = [m for m in maps if m["is_requested_basename"]]
    app_maps = [m for m in maps if m["output_kind"] == KIND_APP]
    kinds = collections.Counter(m["output_kind"] for m in maps)
    manifest = {
        "repo": args.repo, "sha": args.sha, "config_id": args.config_id,
        "build_settings": args.build_settings,
        "toolchain": args.toolchain,
        "pods_did_run": args.pods_did_run, "spm_did_run": args.spm_did_run,
        "scheme": args.scheme, "scheme_verdict": args.scheme_verdict,
        "scheme_elapsed_s": args.scheme_elapsed_s,
        "map_mode": args.map_mode, "map_verdict": args.map_verdict,
        # The real exit status of xcodebuild.  Run #1 recorded "success" here
        # while the log said ** BUILD FAILED ** -- the pipeline swallowed the
        # code.  With `set -o pipefail` this field means what it says.
        "build_outcome": args.build_outcome,
        "maps_found": len(maps),
        "maps_by_output_kind": dict(kinds),
        "maps_uploaded": sum(1 for m in maps if m["copied_as"]),
        "map_bytes_uploaded_gz": sum(m["copied_bytes"] or 0 for m in maps),
        "app_map_bytes": app_maps[0]["bytes"] if app_maps else 0,
        "maps_with_requested_basename": len(requested),
        "map_bytes_max": maps[0]["bytes"] if maps else 0,
        "map_bytes_total": sum(m["bytes"] for m in maps),
        "map_bytes_copied": copied,
        **binary,
        # The single field downstream scoring keys on.  It is deliberately
        # conjunctive: a map without its binary cannot be checked for identity,
        # and a binary without a map has no ground truth.
        # ground truth 的充要条件：app bundle 的 map 在，且主二进制在。
        # 只有 map 没有二进制就无从比对同一性；只有二进制没有 map 就没有真值。
        "usable_for_groundtruth": bool(app_maps) and bool(binary.get("binary_bytes")),
    }

    with open(out_dir / "maps_index.json", "w", encoding="utf-8") as fh:
        json.dump(maps, fh, ensure_ascii=False, indent=2)
    manifest_path = args.out or str(out_dir / "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    print(json.dumps({k: v for k, v in manifest.items()},
                     ensure_ascii=False, indent=2))
    print()
    print(f"=== map 候选 {len(maps)} 个，按输出类型：{dict(kinds)} ===")
    print(f"    上传 {manifest['maps_uploaded']} 个，"
          f"压缩后 {manifest['map_bytes_uploaded_gz']:,} 字节；"
          f"其余只记在 maps_index.json 里")
    print("=== 前 5（按大小降序）===")
    for m in maps[:5]:
        print(f"  {m['bytes']:>12,}  {m['src']}")
        for line in m["head"][:3]:
            print(f"               | {line}")
    if not maps:
        print("  （一个都没有 —— 这是「没观测到」，不是「不存在」）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
