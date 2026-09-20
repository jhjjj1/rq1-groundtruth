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
import hashlib
import json
import os
import pathlib
import plistlib
import shutil
import subprocess
import sys
import zipfile

import macho_sections
import parse_link_map

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
#: `strip` 的三种档位，对应 Xcode 的 STRIP_STYLE。
#:
#: 上一版把 strip 写成 build setting（`STRIP_STYLE=all STRIP_INSTALLED_PRODUCT=YES`），
#: 实测**完全没生效**：base 与 strip_all 两格的 LC_SYMTAB 是 695,589 / 695,594
#: 条，二进制反而大了 200 字节。STRIP_INSTALLED_PRODUCT 只在安装阶段生效，
#: 而 `xcodebuild build` 不走那一步 —— 一个什么都没做的配置却两格全绿，正是
#: 「没测」和「测过且通过」长得一样。
#:
#: 所以 strip 改在收集阶段显式执行：可控、可观测，而且能在**同一次链接内部**
#: 做 strip 前后对照 —— 这才是「strip 不重排代码」该有的验证方式（用两次独立
#: 构建去比是错的：同一 SHA 同一工具链两次构建就有 67% 的符号地址不同）。
STRIP_FLAGS = {
    "none": None,
    "all": [],            # 对可执行文件：能删的全删，最接近 App Store 形态
    "non_global": ["-x"],
    "debug": ["-S"],
}

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


def describe_binary(path):
    """节区表 + LC_SYMTAB + 字节数。三者都取自 `otool -l`，不做任何推断。"""
    rc, text = run(["otool", "-l", str(path)], timeout=300)
    sections = macho_sections.parse_otool_sections(text) if rc == 0 else []
    symtab = macho_sections.parse_symtab(text) if rc == 0 else None
    try:
        size = os.path.getsize(path)
    except OSError:
        size = None
    return {"sections": sections, "symtab": symtab, "bytes": size,
            "otool_rc": rc, "_raw": text}


#: Mach-O magics, both endiannesses, thin and fat.  Used to tell an executable
#: from a resource without trusting the file name (`.dylib` is a convention,
#: `Frameworks/Foo.framework/Foo` has no suffix at all).
MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",      # 32-bit
    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",      # 64-bit
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",      # fat
}
#: What the analyser opens inside an IPA, read off its own source rather than
#: guessed: `Info.plist` and `Entitlements.plist` (inventory/ipa_zip.py),
#: `PrivacyInfo.xcprivacy` (inventory/manifests.py), the Mach-O of every
#: `.app` / `.appex` / `.framework` / `.bundle`, and the bundle tree itself.
#: Everything else in a built `.app` -- Assets.car, images, audio, fonts, nibs,
#: storyboards, localisation tables -- it never reads.  Dropping exactly those
#: keeps the package honest (every byte the tool consumes is the byte the build
#: produced) while taking a 500 MB bundle down to a few MB.
BUNDLE_KEEP_SUFFIXES = {".plist", ".xcprivacy", ".entitlements", ".mobileprovision", ".dylib"}
BUNDLE_KEEP_NAMES = {"PrivacyInfo.xcprivacy", "Info.plist", "Entitlements.plist",
                     "embedded.mobileprovision", "CodeResources"}
#: Directories whose *structure* the analyser walks; kept even when empty of code.
BUNDLE_DIR_SUFFIXES = (".app", ".appex", ".framework", ".bundle", ".systemextension")
#: A zip entry date fixed so two collections of the same build produce the same bytes.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def is_macho(path, _cache={}):
    """True if the first four bytes are a Mach-O magic.  Unreadable file → False, recorded upstream."""
    try:
        with open(path, "rb") as fh:
            return fh.read(4) in MACHO_MAGICS
    except OSError:
        return False


def bundle_keep(rel, path):
    """(keep?, why).  Name and suffix decide first; anything else is opened to check for Mach-O."""
    name = rel.name
    if name in BUNDLE_KEEP_NAMES: return True, "NAME"
    if rel.suffix in BUNDLE_KEEP_SUFFIXES: return True, "SUFFIX"
    if is_macho(path): return True, "MACHO"
    return False, "RESOURCE"


def pack_bundle(app, out_dir, exe_name, max_bytes, strip_styles=("none",)):
    """Zip the built `.app` into `Payload/<App>.app/…` -- an IPA in shape, so the analyser
    can open it with `zipfile` exactly as it opens a decrypted App Store package.

    Why this exists at all: the RRA question has two halves, "the code calls it" and "the app
    declared a reason for it".  The second half lives in `PrivacyInfo.xcprivacy` files inside
    the bundle, and *which* manifest covers *which* binary is decided by where the build put
    them.  Run #1 uploaded only the main executable, so that half of the question had no data
    and the nesting of extensions and embedded frameworks was gone with it.

    Resources the analyser never opens are left out (see BUNDLE_KEEP_*), and what was left out
    is counted by extension so the omission is on the record rather than invisible.  Nothing is
    added, renamed or moved: every kept byte is the byte the build wrote.

    One package per strip style, mirroring `binary.<style>.gz`.  Symbol loss is a dimension of
    the experiment, not an afterthought: a binary analyser is easy on a symbol-rich build and
    hard on a stripped one, so evaluating only the unstripped package would measure the easy
    half.  `strip` runs over **every** Mach-O in the package (the app executable, each .appex,
    each embedded framework) with the same flags the binary path uses, so the two agree.
    """
    rec = {"bundle_zip": None, "bundle_zip_bytes": None, "bundle_full_bytes": 0,
           "bundle_kept_files": 0, "bundle_kept_bytes": 0,
           "bundle_dropped_files": 0, "bundle_dropped_bytes": 0,
           "bundle_dropped_by_ext": {}, "bundle_macho_files": 0,
           "bundle_privacy_manifests": [], "bundle_nested_bundles": [],
           "bundle_symlinks": 0, "bundle_unreadable": [], "bundle_note": None,
           "bundle_verify": None, "bundle_variants": []}
    app = pathlib.Path(app)
    root = f"Payload/{app.name}"
    dropped = collections.Counter(); dropped_bytes = collections.Counter()
    keep_list = []

    for dirpath, dirnames, filenames in os.walk(app, followlinks=False):
        dirnames.sort(); filenames.sort()
        d = pathlib.Path(dirpath)
        if d != app and d.suffix in BUNDLE_DIR_SUFFIXES:
            rec["bundle_nested_bundles"].append(str(d.relative_to(app)))
        for fn in filenames:
            p = d / fn
            rel = p.relative_to(app)
            if p.is_symlink():
                rec["bundle_symlinks"] += 1
                try:
                    target = p.resolve()
                    if app.resolve() not in target.parents: continue   # points out of the bundle: skip, counted
                    p = target
                except OSError as exc:
                    rec["bundle_unreadable"].append(f"{rel}: {exc}"); continue
            try:
                size = p.stat().st_size
            except OSError as exc:
                rec["bundle_unreadable"].append(f"{rel}: {exc}"); continue
            rec["bundle_full_bytes"] += size
            keep, why = bundle_keep(rel, p)
            if keep:
                keep_list.append((rel, p, size, why))
                rec["bundle_kept_files"] += 1; rec["bundle_kept_bytes"] += size
                if why == "MACHO" or rel.suffix == ".dylib": rec["bundle_macho_files"] += 1
                if rel.name == "PrivacyInfo.xcprivacy": rec["bundle_privacy_manifests"].append(str(rel))
            else:
                ext = rel.suffix.lower() or "(无后缀)"
                dropped[ext] += 1; dropped_bytes[ext] += size
                rec["bundle_dropped_files"] += 1; rec["bundle_dropped_bytes"] += size

    rec["bundle_dropped_by_ext"] = {k: {"n": v, "bytes": dropped_bytes[k]}
                                    for k, v in dropped.most_common(20)}
    if not keep_list:
        rec["bundle_note"] = "NOTHING_TO_PACK"
        return rec

    tmp = pathlib.Path(out_dir) / "_bundle_strip"
    for style in strip_styles:
        flags = STRIP_FLAGS.get(style)
        zpath = pathlib.Path(out_dir) / f"bundle.{style}.ipa"
        var = {"strip_style": style, "file": None, "bytes": None,
               "n_stripped": 0, "strip_failed": [], "verify": None, "note": None}
        try:
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                for rel, p, _size, why in keep_list:
                    data = None
                    if flags is not None and (why == "MACHO" or rel.suffix == ".dylib"):
                        # strip 是原地操作，所以先复制一份再动它 —— 与二进制那条路同样的做法
                        tmp.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            shutil.copy2(p, tmp)
                            rc, out = run(["strip", *flags, str(tmp)], timeout=900)
                            if rc != 0:
                                var["strip_failed"].append(f"{rel}: rc={rc} {out.strip()[:120]}")
                            else:
                                var["n_stripped"] += 1
                            data = tmp.read_bytes()
                        except OSError as exc:
                            var["strip_failed"].append(f"{rel}: {exc}")
                        finally:
                            tmp.unlink(missing_ok=True)
                    if data is None:
                        with open(p, "rb") as fh:
                            data = fh.read()
                    info = zipfile.ZipInfo(f"{root}/{rel.as_posix()}", date_time=ZIP_EPOCH)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o644 << 16
                    zf.writestr(info, data)
        except OSError as exc:
            var["note"] = f"BUNDLE_ZIP_FAILED: {exc}"
            zpath.unlink(missing_ok=True)
            rec["bundle_variants"].append(var)
            continue
        var["bytes"] = zpath.stat().st_size
        if max_bytes and var["bytes"] > max_bytes:
            # Recorded and still uploaded: a bundle over budget is a fact about that app,
            # and silently dropping it would look exactly like an app with no manifests.
            var["note"] = f"BUNDLE_OVER_BUDGET: {var['bytes']:,} > {max_bytes:,}"
        var["file"] = zpath.name
        var["verify"] = verify_bundle(zpath, app.name, exe_name)
        rec["bundle_variants"].append(var)

    # 代表变体：第一个成功的那个，供汇总表与聚合脚本引用；逐档位的数在 bundle_variants 里
    first = next((v for v in rec["bundle_variants"] if v["file"]), None)
    if first is None:
        rec["bundle_note"] = rec["bundle_variants"][0]["note"] if rec["bundle_variants"] else "NO_STRIP_STYLES"
        return rec
    rec["bundle_zip"] = first["file"]; rec["bundle_zip_bytes"] = first["bytes"]
    rec["bundle_verify"] = first["verify"]; rec["bundle_note"] = first["note"]
    return rec


def verify_bundle(zpath, app_name, exe_name):
    """Reopen the zip the way the analyser will, and say what is in it.

    Checked here rather than downstream because a bundle that cannot be opened is a fact
    about *this job*, and the runner is the only place that still has the build to compare with.
    """
    v = {"opens": False, "payload_roots": [], "executable_member": None,
         "executable_bytes": None, "executable_sha256": None,
         "n_members": 0, "n_privacy_manifests": 0, "verdict": None}
    try:
        with zipfile.ZipFile(zpath) as zf:
            names = zf.namelist()
            v["opens"] = True; v["n_members"] = len(names)
            v["payload_roots"] = sorted({n.split("/")[1] for n in names
                                         if n.startswith("Payload/") and "/" in n[8:]})
            v["n_privacy_manifests"] = sum(1 for n in names if n.endswith("/PrivacyInfo.xcprivacy"))
            member = f"Payload/{app_name}/{exe_name}"
            if member in names:
                data = zf.read(member)
                v["executable_member"] = member
                v["executable_bytes"] = len(data)
                v["executable_sha256"] = hashlib.sha256(data).hexdigest()
    except (OSError, zipfile.BadZipFile) as exc:
        v["verdict"] = f"UNREADABLE: {exc}"
        return v
    if len(v["payload_roots"]) != 1:
        v["verdict"] = f"PAYLOAD_ROOTS={len(v['payload_roots'])}"
    elif not v["executable_member"]:
        v["verdict"] = "EXECUTABLE_MISSING_IN_ZIP"
    elif v["n_privacy_manifests"] == 0:
        # Not an error: an app may genuinely ship none.  Named so the two cases
        # ("declared nothing" vs "we failed to pack it") stay apart downstream.
        v["verdict"] = "OK_NO_PRIVACY_MANIFEST"
    else:
        v["verdict"] = "OK"
    return v


# --------------------------------------------------------------------------- #
# DWARF line tables
# --------------------------------------------------------------------------- #
#: `dwarfdump --debug-line` output for one executable can run to hundreds of MB
#: of text for a large Swift app; gzip takes it to a few MB.  Above this many
#: gzipped bytes per job the tables are still uploaded, and the manifest says so
#: -- dropping them silently would look the same as "this app has no debug info".
DWARF_GZ_BUDGET_BYTES = 300 * 1024 * 1024


def _dsym_binaries(products_root):
    """Every DWARF payload under Build/Products/**/*.dSYM/Contents/Resources/DWARF/."""
    out = []
    root = pathlib.Path(products_root)
    if not root.is_dir():
        return out
    for ds in sorted(root.rglob("*.dSYM")):
        dw = ds / "Contents" / "Resources" / "DWARF"
        if not dw.is_dir():
            continue
        for b in sorted(dw.iterdir()):
            if b.is_file():
                out.append((ds, b))
    return out


def _stream_gz(cmd, dst, timeout):
    """Run `cmd`, gzip its stdout straight to `dst`; return (rc, rows, note)."""
    rows = 0
    try:
        with open(dst, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6) as gz:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                for line in proc.stdout:
                    gz.write(line)
                    if line.startswith(b"0x"):
                        rows += 1
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                return -1, rows, f"timeout after {timeout}s"
            err = proc.stderr.read().decode("utf-8", "replace").strip()
            return proc.returncode, rows, err[:300] or None
    except OSError as exc:
        return -1, rows, f"{type(exc).__name__}: {exc}"


def collect_dwarf_line_tables(derived_data, out_dir, budget_bytes=DWARF_GZ_BUDGET_BYTES,
                              timeout=1800):
    """dSYM -> gzipped `dwarfdump --debug-line` per executable, with UUIDs.

    Records only; judges nothing.  A missing dSYM is recorded as an empty index,
    which is "not observed", not "no debug info".
    """
    products = pathlib.Path(derived_data) / "Build" / "Products"
    ddir = pathlib.Path(out_dir) / "dwarf"
    ddir.mkdir(parents=True, exist_ok=True)
    index = []
    total = 0
    for ds, b in _dsym_binaries(products):
        rec = {"dsym": ds.name, "binary": b.name, "dwarf_bytes": b.stat().st_size,
               "uuid": None, "arch": None, "line_table": None, "bytes_gz": None,
               "rows": None, "rc": None, "note": None}
        rc_u, out_u = run(["dwarfdump", "--uuid", str(b)])
        if rc_u == 0:
            parts = out_u.split()
            if len(parts) >= 2 and parts[0] == "UUID:":
                rec["uuid"] = parts[1]
                rec["arch"] = parts[2].strip("()") if len(parts) > 2 else None
        flat = f"{ds.name}__{b.name}".replace("/", "_").replace(" ", "_")
        dst = ddir / f"{flat}.debug-line.txt.gz"
        rc, rows, note = _stream_gz(["dwarfdump", "--debug-line", str(b)], dst, timeout)
        rec.update(rc=rc, rows=rows, note=note)
        if rc == 0:
            rec["line_table"] = f"dwarf/{dst.name}"
            rec["bytes_gz"] = dst.stat().st_size
            total += rec["bytes_gz"]
        else:
            try:
                dst.unlink()
            except OSError:
                pass
        index.append(rec)
    with open(ddir / "dwarf_index.json", "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=2)
    return index, total


def collect_variants(derived_data, out_dir, map_sections=None,
                     strip_styles=("all",), bundle_max_bytes=0):
    """Find the built .app and emit one binary per strip style, from ONE link.

    Why variants instead of one binary per job
    ------------------------------------------
    P1 measured that `strip` is layout-preserving: stripping the IceCubesApp
    binary took LC_SYMTAB from 695,589 entries to 5,186 and the file from
    62,702,680 to 24,647,512 bytes, while `strip_preserves_layout` came back
    True and the link-time map still matched the stripped binary section for
    section.

    That makes the unstripped and stripped forms two *views of one link*, not
    two experiments.  Producing them from a single build removes a confound
    (two independent builds of the same commit can differ) and saves one whole
    build per repo.

    Returns (variants, shared) where `shared` holds the facts that belong to
    the link itself (app bundle, executable name) and `variants` is one record
    per strip style.  A style that could not be produced is recorded with its
    reason, never dropped.
    """
    shared = {"app_bundle": None, "executable_name": None, "collect_note": None,
              "bundle_zip": None, "bundle_verify": None, "bundle_variants": []}
    out_dir = pathlib.Path(out_dir)

    products = pathlib.Path(derived_data) / "Build" / "Products"
    if not products.is_dir():
        shared["collect_note"] = "NO_BUILD_PRODUCTS_DIR"
        return [], shared
    apps = sorted(products.glob("*/*.app")) + sorted(products.glob("*.app"))
    if not apps:
        shared["collect_note"] = "NO_APP_BUNDLE_FOUND"
        return [], shared
    app = apps[0]
    shared["app_bundle"] = str(app)
    if len(apps) > 1:
        shared["collect_note"] = f"MULTIPLE_APP_BUNDLES({len(apps)})"

    exe_name = None
    plist = app / "Info.plist"
    if plist.is_file():
        try:
            with open(plist, "rb") as fh:
                exe_name = (plistlib.load(fh) or {}).get("CFBundleExecutable")
        except Exception as exc:                      # malformed plist is a fact
            shared["collect_note"] = f"INFO_PLIST_UNREADABLE: {exc}"
    exe_name = exe_name or app.stem
    shared["executable_name"] = exe_name

    src = app / exe_name
    if not src.is_file():
        shared["collect_note"] = "EXECUTABLE_MISSING_IN_BUNDLE"
        return [], shared

    # 未经改动的一份，每个变体都从它复制出来 —— strip 是原地操作
    pristine = out_dir / "_pristine"
    try:
        shutil.copy2(src, pristine)
    except OSError as exc:
        shared["collect_note"] = f"BINARY_COPY_FAILED: {exc}"
        return [], shared
    before = describe_binary(pristine)

    variants = []
    #: strip 档位 -> 该档位产出的主二进制 sha256，用来和同档位包里的那一份对账
    binary_sha = {}
    for style in strip_styles:
        rec = {"strip_style": style, "binary_file": None,
               "binary_bytes": None, "binary_gz_bytes": None,
               "binary_bytes_before_strip": before["bytes"],
               "nsyms_before": (before["symtab"] or {}).get("nsyms"),
               "strsize_before": (before["symtab"] or {}).get("strsize"),
               "nsyms_after": None, "strsize_after": None,
               "strip_did_run": False, "strip_rc": None,
               "strip_preserves_layout": None, "strip_layout_detail": None,
               "map_matches_binary": None, "map_binary_detail": None,
               "uuid": None, "arch": None, "note": None}
        flags = STRIP_FLAGS.get(style)
        if flags is None and style not in STRIP_FLAGS:
            rec["note"] = f"UNKNOWN_STRIP_STYLE_{style}"
            variants.append(rec)
            continue

        work = out_dir / f"_work_{style}"
        try:
            shutil.copy2(pristine, work)
        except OSError as exc:
            rec["note"] = f"COPY_FAILED: {exc}"
            variants.append(rec)
            continue

        if flags is not None:
            rc_s, out_s = run(["strip", *flags, str(work)], timeout=900)
            rec["strip_did_run"] = True
            rec["strip_rc"] = rc_s
            if rc_s != 0:
                rec["note"] = f"STRIP_FAILED_rc{rc_s}: {out_s.strip()[:200]}"

        after = describe_binary(work)
        rec["binary_bytes"] = after["bytes"]
        rec["nsyms_after"] = (after["symtab"] or {}).get("nsyms")
        rec["strsize_after"] = (after["symtab"] or {}).get("strsize")

        if flags is not None:
            cl = macho_sections.compare_tables(
                before["sections"], after["sections"], "pre_strip", "post_strip")
            # NO_DATA 是「没量到」，不是「量了且不保持」
            rec["strip_preserves_layout"] = (
                None if cl["verdict"] == "NO_DATA" else cl["identical"])
            rec["strip_layout_detail"] = {k: v for k, v in cl.items()
                                          if k != "first_diffs"}
            if not cl["identical"]:
                rec["strip_layout_detail"]["first_diffs"] = cl.get("first_diffs")

        # 三种「比不了」分开记：合并成「没得比」就看不出该修哪一头
        if map_sections is None:
            rec["map_binary_detail"] = {"verdict": "NO_APP_MAP"}
        elif not map_sections:
            rec["map_binary_detail"] = {"verdict": "MAP_HAS_NO_SECTIONS"}
        elif not after["sections"]:
            rec["map_binary_detail"] = {"verdict": "BINARY_SECTIONS_UNREADABLE",
                                        "otool_rc": after["otool_rc"]}
        else:
            cm = macho_sections.compare_tables(
                map_sections, after["sections"], "map", "binary")
            rec["map_matches_binary"] = cm["identical"]
            rec["map_binary_detail"] = cm

        (out_dir / f"loadcmds.{style}.txt").write_text(after["_raw"],
                                                       encoding="utf-8")
        rc_u, out_u = run(["dwarfdump", "--uuid", str(work)])
        (out_dir / f"uuid.{style}.txt").write_text(out_u, encoding="utf-8")
        if rc_u == 0:
            parts = out_u.split()
            if len(parts) >= 2 and parts[0] == "UUID:":
                rec["uuid"] = parts[1]
                rec["arch"] = parts[2].strip("()") if len(parts) > 2 else None
        for name, cmd in ((f"size.{style}.txt", ["size", "-m", str(work)]),
                          (f"lipo.{style}.txt", ["lipo", "-info", str(work)])):
            _rc, text = run(cmd, timeout=180)
            (out_dir / name).write_text(text, encoding="utf-8")

        try:
            with open(work, "rb") as fh:
                binary_sha[style] = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            pass
        try:
            rec["binary_gz_bytes"] = gzip_copy(work, out_dir / f"binary.{style}.gz")
            rec["binary_file"] = f"binary.{style}.gz"
        except OSError as exc:
            rec["note"] = (rec["note"] or "") + f" GZIP_FAILED: {exc}"
        finally:
            work.unlink(missing_ok=True)
        variants.append(rec)

    pristine.unlink(missing_ok=True)

    # 包与二进制是同一个问题的两半，必须出自同一次构建：每个 strip 档位打一份包，
    # 再拿包里那份主可执行文件的 sha256 和同档位的 binary.<style>.gz 对账。两条路
    # 各自独立地跑了 strip，对得上才说明它们描述的是同一个东西。
    shared.update(pack_bundle(app, out_dir, exe_name, bundle_max_bytes, strip_styles))
    for var in shared.get("bundle_variants") or []:
        v = var.get("verify") or {}
        want = binary_sha.get(var["strip_style"])
        if v.get("executable_sha256") and want:
            v["matches_binary"] = (v["executable_sha256"] == want)
            if not v["matches_binary"]:
                v["verdict"] = "EXECUTABLE_DIFFERS_FROM_COLLECTED_BINARY"
        elif v.get("executable_sha256"):
            v["matches_binary"] = None                 # 该档位没产出二进制，无从对账
    first = next((x for x in (shared.get("bundle_variants") or []) if x["file"]), None)
    if first is not None:
        shared["bundle_verify"] = first["verify"]
    return variants, shared


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
    ap.add_argument("--strip-variants", default="all",
                    help="逗号分隔的 strip 档位；一次链接产出多份二进制，"
                         "共用同一份 map。例：none,all")
    ap.add_argument("--max-dwarf-mb", type=float, default=300.0,
                    help="DWARF 行号表（gzip 后）的每 job 预算（MB）；超了照传，manifest 记 DWARF_OVER_BUDGET")
    ap.add_argument("--max-bundle-mb", type=float, default=200.0,
                    help="app bundle 压缩包的预算（MB）；超了照传，只在 manifest 里记 "
                         "BUNDLE_OVER_BUDGET —— 悄悄丢掉会和「这个 App 没有清单」长得一样")
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
    # app bundle 的 map 的节区表 —— 只读 Sections 段，不全量解析那 51 MB
    app_map_src = next((m["src"] for m in maps
                        if m["output_kind"] == KIND_APP), None)
    map_sections = None
    if app_map_src:
        try:
            map_sections = parse_link_map.parse_sections_only(app_map_src)
        except OSError as exc:
            print(f"读 map 节区表失败: {exc}", file=sys.stderr)

    styles = [x.strip() for x in args.strip_variants.split(",") if x.strip()]
    unknown = [x for x in styles if x not in STRIP_FLAGS]
    if unknown:
        print(f"未知 strip 档位 {unknown}；可选 {sorted(STRIP_FLAGS)}", file=sys.stderr)
        return 2
    variants, shared = collect_variants(dd, out_dir, map_sections=map_sections,
                                        strip_styles=styles,
                                        bundle_max_bytes=int(args.max_bundle_mb * 1024 * 1024))
    dwarf_index, dwarf_gz_total = collect_dwarf_line_tables(
        dd, out_dir, budget_bytes=int(args.max_dwarf_mb * 1024 * 1024))

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
        **shared,
        # 一次链接 → 多个 strip 变体，共用上面那份 map。
        # 评分单元是 variant，不是 job：strip 与否是配置维度之一。
        "strip_variants_requested": styles,
        "variants": variants,
        "usable_variants": [v["strip_style"] for v in variants
                            if v["map_matches_binary"] is True
                            and v["binary_bytes"]],
        # The single field downstream scoring keys on.  It is deliberately
        # conjunctive: a map without its binary cannot be checked for identity,
        # and a binary without a map has no ground truth.
        # job 级判据：app bundle 的 map 在，且**至少一个** strip 变体经节区表
        # 比对确认与它配套。「文件都在」不等于「配套」，后者才是可评分的条件。
        # 每个变体自己的可用性在 variants[] 里，聚合按变体展开。
        "usable_for_groundtruth": bool(app_maps) and bool([
            v for v in variants
            if v["map_matches_binary"] is True and v["binary_bytes"]]),
        # Kept separate from `usable_for_groundtruth` on purpose: that field means
        # "map and binary are a matched pair" and run #1's numbers are reported against
        # it.  Widening it now would silently change what the old numbers meant.  This
        # one answers the new question — can the analyser be pointed at this job at all.
        "bundle_usable": bool((shared.get("bundle_verify") or {}).get("verdict", "").startswith("OK")),
        # DWARF line tables (dSYM -> `dwarfdump --debug-line`, gzipped), one per
        # executable, matched to bundle.*.ipa binaries by UUID.  `dsyms_found`
        # counts DWARF payloads seen; `dwarf_line_tables` counts the ones whose
        # dump succeeded.  The two differing is a fact worth keeping.
        "dsyms_found": len(dwarf_index),
        "dwarf_line_tables": sum(1 for d in dwarf_index if d["line_table"]),
        "dwarf_bytes_gz": dwarf_gz_total,
        "dwarf_over_budget": dwarf_gz_total > int(args.max_dwarf_mb * 1024 * 1024),
        "dwarf_uuids": {d["binary"]: d["uuid"] for d in dwarf_index},
    }

    with open(out_dir / "maps_index.json", "w", encoding="utf-8") as fh:
        json.dump(maps, fh, ensure_ascii=False, indent=2)
    manifest_path = args.out or str(out_dir / "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    print(json.dumps({k: v for k, v in manifest.items()},
                     ensure_ascii=False, indent=2))
    print()
    print(f"=== strip 变体 {len(variants)} 个（同一次链接，共用一份 map）===")
    hdr = ("style", "strip?", "nsyms 前→后", "字节 前→后", "保布局", "map 配套")
    print("    {:<12}{:<8}{:<26}{:<30}{:<10}{}".format(*hdr))
    for v in variants:
        nb, na = v["nsyms_before"], v["nsyms_after"]
        bb, ba = v["binary_bytes_before_strip"], v["binary_bytes"]
        nsyms = f"{nb:,} → {na:,}" if (nb is not None and na is not None) else "-"
        size = f"{bb:,} → {ba:,}" if (bb is not None and ba is not None) else "-"
        print("    {:<12}{:<8}{:<26}{:<30}{:<10}{}".format(
            v["strip_style"], str(v["strip_did_run"]), nsyms, size,
            str(v["strip_preserves_layout"]), str(v["map_matches_binary"])))
        if v.get("note"):
            print(f"        note: {v['note']}")
        d = v.get("map_binary_detail") or {}
        if d.get("verdict") not in (None, "IDENTICAL"):
            print(f"        map/binary: {d.get('verdict')}")
    print(f"    可用变体: {manifest['usable_variants']}")
    print()
    print("=== app bundle 包 ===")
    if not shared.get("bundle_zip"):
        print(f"    没打出来：{shared.get('bundle_note') or shared.get('collect_note') or '（没有 .app）'}")
    else:
        print(f"    内容（各档位相同）：bundle 原始 {shared['bundle_full_bytes']:,} 字节，"
              f"留 {shared['bundle_kept_files']} 个文件 / {shared['bundle_kept_bytes']:,} 字节，"
              f"其中 Mach-O {shared['bundle_macho_files']} 个、PrivacyInfo.xcprivacy "
              f"{len(shared['bundle_privacy_manifests'])} 份；内嵌 bundle {len(shared['bundle_nested_bundles'])} 个")
        print(f"    丢 {shared['bundle_dropped_files']} 个资源文件 / {shared['bundle_dropped_bytes']:,} 字节 —— "
              f"分析器不读这些，按后缀分布：")
        for ext, d in list((shared.get("bundle_dropped_by_ext") or {}).items())[:8]:
            print(f"        {ext:<16} {d['n']:>6} 个  {d['bytes']:>14,} 字节")
        print(f"    {'档位':<10}{'文件':<22}{'字节':>12}  {'strip 了几个':>12}  复检 / 与同档位二进制一致")
        for var in shared.get("bundle_variants") or []:
            vv = var.get("verify") or {}
            print(f"    {var['strip_style']:<10}{str(var['file']):<22}{(var['bytes'] or 0):>12,}"
                  f"  {var['n_stripped']:>12}  {vv.get('verdict')} / {vv.get('matches_binary')}")
            for line in (var.get("strip_failed") or [])[:3]:
                print(f"        strip 失败：{line}")
            if var.get("note"):
                print(f"        note: {var['note']}")
        for line in (shared.get("bundle_unreadable") or [])[:5]:
            print(f"    读不了：{line}")
    print()
    print(f"=== DWARF 行号表 {manifest['dwarf_line_tables']}/{manifest['dsyms_found']} 份，"
          f"gzip 后 {manifest['dwarf_bytes_gz']:,} 字节"
          f"{'（超预算）' if manifest['dwarf_over_budget'] else ''} ===")
    for d in dwarf_index:
        print(f"    {d['dsym']:<40} {d['binary']:<28} uuid={d['uuid']} rows={d['rows']} "
              f"{'gz=' + format(d['bytes_gz'], ',') if d['bytes_gz'] else 'rc=' + str(d['rc']) + ' ' + str(d['note'])}")
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
