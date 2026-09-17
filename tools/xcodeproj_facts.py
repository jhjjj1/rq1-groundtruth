#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build facts from project files: which target owns a source file, and what the
Release build defines.

The source scanner evaluates `#if` chains for the benchmark build (the driver
builds every app with `xcodebuild -configuration Release -destination
generic/platform=iOS`, see .github/workflows/build.yml).  Whether `#if TEST`
is live is therefore not a convention to assume but a fact to read:

  * `project.pbxproj`   XCBuildConfiguration blocks per target and per project,
                        their `SWIFT_ACTIVE_COMPILATION_CONDITIONS`,
                        `OTHER_SWIFT_FLAGS -D…`, `GCC_PREPROCESSOR_DEFINITIONS`,
                        plus any `.xcconfig` they include; and the file → target
                        membership (PBXBuildFile / PBXSourcesBuildPhase, or the
                        Xcode 16 synchronized folders).
  * `Package.swift`     `.define("FLAG")` / `.define("FLAG", .when(configuration: .debug))`
                        per target; SwiftPM itself defines `SWIFT_PACKAGE`.
  * `*.podspec[.json]`  `pod_target_xcconfig` / `xcconfig` definitions; CocoaPods
                        itself defines `COCOAPODS`.

wikipedia-ios, read this way: `TEST` is defined only in the `Test`
configuration (OTHER_SWIFT_FLAGS `-DNDEBUG -DTEST`, GCC `TEST=1`), `UITEST`
only in `UITests`; the project-level `Release` configuration sets
`WMF_APP_GROUP_IDENTIFIER = group.org.wikimedia.wikipedia`, which the
`GCC_PREPROCESSOR_DEFINITIONS` entry `WMF_APP_GROUP_IDENTIFIER=$(…)` turns into
the C macro behind `NSString *const WMFApplicationGroupIdentifier =
@QUOTE(WMF_APP_GROUP_IDENTIFIER);`.  Both were "unresolved" for the annotator
before this module existed.

Nothing here is a judgment: unknown stays unknown (a missing included xcconfig
marks the target's flag set incomplete, and an identifier is then never
declared false).

    python3 tools/xcodeproj_facts.py <repo-dir> [--json out.json]
"""

import argparse
import collections
import io
import json
import pathlib
import posixpath
import re
import sys

PRODUCT_KIND = {
    "com.apple.product-type.application": "APP",
    "com.apple.product-type.application.on-demand-install-capable": "APP_CLIP",
    "com.apple.product-type.app-extension": "APP_EXTENSION",
    "com.apple.product-type.app-extension.messages": "APP_EXTENSION",
    "com.apple.product-type.app-extension.messages-sticker-pack": "APP_EXTENSION",
    "com.apple.product-type.app-extension.intents-service": "APP_EXTENSION",
    "com.apple.product-type.extensionkit-extension": "APP_EXTENSION",
    "com.apple.product-type.application.watchapp2": "WATCH_APP",
    "com.apple.product-type.application.watchapp2-container": "WATCH_APP",
    "com.apple.product-type.watchkit2-extension": "WATCH_EXTENSION",
    "com.apple.product-type.framework": "FRAMEWORK",
    "com.apple.product-type.framework.static": "FRAMEWORK",
    "com.apple.product-type.library.static": "LIBRARY",
    "com.apple.product-type.library.dynamic": "LIBRARY",
    "com.apple.product-type.bundle": "BUNDLE",
    "com.apple.product-type.bundle.unit-test": "TEST",
    "com.apple.product-type.bundle.ui-testing": "TEST",
    "com.apple.product-type.tool": "TOOL",
}
SRC_SUFFIX = {".swift", ".m", ".mm", ".c", ".cc", ".cpp", ".h", ".hpp"}
#: the configuration the driver builds (build.yml: `-configuration Release`)
RELEASE = "Release"
#: `#if` identifiers that are not project flags: SwiftPM / CocoaPods define them
#: for every target they build, Xcode defines none of them.
INTEGRATION_FLAGS = {"SPM": {"SWIFT_PACKAGE"}, "LOCAL_PKG": {"SWIFT_PACKAGE"}, "POD": {"COCOAPODS"}, "LOCAL_POD": {"COCOAPODS"}}


# --------------------------------------------------------------------------
# 1. the old-style plist that project.pbxproj is written in
# --------------------------------------------------------------------------
_TOKEN = re.compile(r"""
    /\*.*?\*/ | //[^\n]* | \s+ |               # comments, whitespace (skipped)
    (?P<str>"(?:\\.|[^"\\])*") |               # quoted string
    (?P<word>[A-Za-z0-9_$./:+\-@~]+) |         # bare word
    (?P<punct>[{}()=;,])
""", re.S | re.X)


def parse_pbxproj(text):
    """dict for the whole file (values are dict / list / str)."""
    toks = []
    for m in _TOKEN.finditer(text):
        if m.group("str") is not None:
            s = m.group("str")[1:-1]
            toks.append(("s", re.sub(r"\\(.)", lambda k: {"n": "\n", "t": "\t"}.get(k.group(1), k.group(1)), s)))
        elif m.group("word") is not None:
            toks.append(("s", m.group("word")))
        elif m.group("punct") is not None:
            toks.append((m.group("punct"), m.group("punct")))
    pos = [0]

    def peek(): return toks[pos[0]] if pos[0] < len(toks) else (None, None)

    def take(kind=None):
        t = peek()
        if kind and t[0] != kind: raise ValueError(f"pbxproj: expected {kind!r} at token {pos[0]} got {t!r}")
        pos[0] += 1
        return t[1]

    def value():
        k, v = peek()
        if k == "{":
            take(); d = {}
            while peek()[0] != "}":
                key = take("s"); take("="); d[key] = value()
                if peek()[0] == ";": take()
            take("}"); return d
        if k == "(":
            take(); lst = []
            while peek()[0] != ")":
                lst.append(value())
                if peek()[0] == ",": take()
            take(")"); return lst
        return take("s")

    if peek()[0] != "{": raise ValueError("pbxproj: no root dictionary")
    return value()


# --------------------------------------------------------------------------
# 2. xcconfig
# --------------------------------------------------------------------------
RE_XCCONFIG_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(\[[^\]]*\])?\s*=\s*(.*?)\s*;?\s*$")
RE_XCCONFIG_INCLUDE = re.compile(r'^\s*#include(\?)?\s*"([^"]+)"')


def read_xcconfig(path, seen=None):
    """{KEY: value} of one xcconfig with its includes applied first (lower precedence).
    Returns (settings, complete): complete is False when a non-optional include is missing."""
    seen = seen if seen is not None else set()
    path = pathlib.Path(path)
    if path in seen: return {}, True
    seen.add(path)
    settings = {}; complete = True
    try:
        lines = io.open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return {}, False
    for raw in lines:
        line = raw.split("//")[0].strip()
        if not line: continue
        inc = RE_XCCONFIG_INCLUDE.match(line)
        if inc:
            target = inc.group(2)
            cand = (path.parent / target) if not target.startswith("<") else None
            if cand and cand.is_file():
                sub, sub_ok = read_xcconfig(cand, seen)
                complete = complete and sub_ok
                for k, v in sub.items(): settings[k] = _inherit(v, settings.get(k))
            elif not inc.group(1):                       # `#include?` is optional; a plain include that is missing is a gap
                complete = False
            continue
        m = RE_XCCONFIG_LINE.match(line)
        if not m: continue
        key, cond, val = m.group(1), m.group(2), m.group(3)
        if cond and not re.search(r"sdk=iphoneos|sdk=\*|config=Release|arch=arm64", cond): continue   # a condition that does not cover the benchmark build
        settings[key] = _inherit(val, settings.get(key))
    return settings, complete


def _inherit(value, lower):
    """Replace `$(inherited)` in a value by the value one layer below (or drop it)."""
    if isinstance(value, list): value = " ".join(str(x) for x in value)
    low = "" if lower is None else (lower if isinstance(lower, str) else " ".join(lower))
    return value.replace("$(inherited)", low).replace("${inherited}", low).strip()


def substitute(value, settings, depth=0):
    """Expand `$(NAME)` / `${NAME}` from the same settings dict; unknown names stay as written."""
    if depth > 6 or "$" not in value: return value
    def rep(m):
        name = m.group(1) or m.group(2)
        return substitute(settings[name], settings, depth + 1) if name in settings else m.group(0)
    return re.sub(r"\$\((\w+)(?::[^)]*)?\)|\$\{(\w+)\}", rep, value)


# --------------------------------------------------------------------------
# 3. one .xcodeproj
# --------------------------------------------------------------------------
class Project:
    def __init__(self, proj_dir):
        self.proj_dir = pathlib.Path(proj_dir)                      # X.xcodeproj
        self.root_dir = self.proj_dir.parent                         # where paths are relative to
        self.pbx = parse_pbxproj(io.open(self.proj_dir / "project.pbxproj", encoding="utf-8", errors="replace").read())
        self.objects = self.pbx.get("objects", {})
        self.project = self.objects.get(self.pbx.get("rootObject"), {})
        pdp = self.project.get("projectDirPath") or ""
        if pdp: self.root_dir = (self.root_dir / pdp)
        self.notes = []
        self._paths = {}
        self._index_groups()

    # ---- paths of file references --------------------------------------
    def _index_groups(self):
        parent = {}
        for oid, o in self.objects.items():
            if o.get("isa") in ("PBXGroup", "PBXVariantGroup", "PBXVersionGroup", "XCVersionGroup"):
                for c in o.get("children", []): parent[c] = oid
        self._parent = parent

    def path_of(self, oid):
        """Path of a file reference / group relative to root_dir, or None (SDK, built products …)."""
        if oid in self._paths: return self._paths[oid]
        o = self.objects.get(oid, {})
        st = o.get("sourceTree", "<group>"); p = o.get("path")
        if st in ("SDKROOT", "DEVELOPER_DIR", "BUILT_PRODUCTS_DIR"):
            res = None
        elif st == "<absolute>":
            try: res = str(pathlib.Path(p).resolve().relative_to(self.root_dir.resolve()))
            except (ValueError, TypeError): res = None
        elif st == "SOURCE_ROOT":
            res = p or ""
        else:                                                   # <group>: relative to the parent group
            par = self._parent.get(oid)
            base = self.path_of(par) if par else ""
            if base is None: res = None
            else: res = str(pathlib.PurePosixPath(base) / p) if p else base
        if res is not None:
            res = posixpath.normpath(res) if res else ""              # `WMF Framework/../Wikipedia/Code/X.swift`
            if res == ".": res = ""
        self._paths[oid] = res
        return res

    # ---- targets ---------------------------------------------------------
    def targets(self):
        out = []
        for tid in self.project.get("targets", []):
            t = self.objects.get(tid, {})
            if t.get("isa") not in ("PBXNativeTarget",): continue
            pt = t.get("productType", "")
            out.append({"id": tid, "name": t.get("name", "?"), "product_type": pt, "kind": PRODUCT_KIND.get(pt, "OTHER")})
        return out

    def source_files(self, tid):
        """Relative paths of the target's compiled sources (build phases + synchronized folders)."""
        t = self.objects.get(tid, {})
        files = set(); synced = []
        for ph in t.get("buildPhases", []):
            p = self.objects.get(ph, {})
            if p.get("isa") != "PBXSourcesBuildPhase": continue
            for bf in p.get("files", []):
                ref = self.objects.get(bf, {}).get("fileRef")
                if not ref: continue
                rp = self.path_of(ref)
                if rp: files.add(rp)
        # Xcode 16: whole folders are members, minus per-target exceptions
        for gid in t.get("fileSystemSynchronizedGroups", []):
            g = self.objects.get(gid, {})
            base = self.path_of(gid)
            if base is None: continue
            excluded = set()
            for eid in g.get("exceptions", []):
                e = self.objects.get(eid, {})
                if e.get("target") == tid or e.get("isa") == "PBXFileSystemSynchronizedBuildFileExceptionSet":
                    for x in e.get("membershipExceptions", []): excluded.add(str(pathlib.PurePosixPath(base) / x))
            synced.append((base, excluded))
        return files, synced

    def resource_files(self, tid):
        """Relative paths of the target's bundled resources (its own PrivacyInfo.xcprivacy is one of them)."""
        t = self.objects.get(tid, {})
        files = set()
        for ph in t.get("buildPhases", []):
            p = self.objects.get(ph, {})
            if p.get("isa") != "PBXResourcesBuildPhase": continue
            for bf in p.get("files", []):
                ref = self.objects.get(bf, {}).get("fileRef")
                rp = self.path_of(ref) if ref else None
                if rp: files.add(rp)
        return files

    # ---- build settings --------------------------------------------------
    def _config_layers(self, list_id, name):
        """[(settings dict, complete)] for one configuration list entry: xcconfig then buildSettings."""
        cl = self.objects.get(list_id, {})
        for cid in cl.get("buildConfigurations", []):
            c = self.objects.get(cid, {})
            if c.get("name") != name: continue
            layers = []
            base = c.get("baseConfigurationReference")
            if base:
                bp = self.path_of(base)
                if bp is not None and (self.root_dir / bp).is_file():
                    layers.append(read_xcconfig(self.root_dir / bp))
                else:
                    layers.append(({}, False)); self.notes.append(f"xcconfig missing: {bp} ({name})")
            layers.append(({k: v for k, v in (c.get("buildSettings") or {}).items()}, True))
            return layers
        return None

    def config_names(self):
        cl = self.objects.get(self.project.get("buildConfigurationList"), {})
        return [self.objects.get(c, {}).get("name") for c in cl.get("buildConfigurations", [])]

    def settings(self, tid, name):
        """Merged settings of target `tid` in configuration `name`, `$(inherited)` resolved layer by layer.
        Returns (settings, complete)."""
        merged = {}; complete = True
        proj_layers = self._config_layers(self.project.get("buildConfigurationList"), name) or []
        t = self.objects.get(tid, {})
        tgt_layers = self._config_layers(t.get("buildConfigurationList"), name) or []
        if not tgt_layers and t: complete = False
        for layer, ok in proj_layers + tgt_layers:
            complete = complete and ok
            for k, v in layer.items():
                merged[k] = _inherit(v, merged.get(k))
        for k in list(merged): merged[k] = substitute(merged[k], merged)
        return merged, complete


def flags_from_settings(st):
    """(swift_flags, c_macros) declared by one merged settings dict."""
    swift = set()
    for tok in (st.get("SWIFT_ACTIVE_COMPILATION_CONDITIONS") or "").split():
        if re.fullmatch(r"[A-Za-z_]\w*", tok): swift.add(tok)
    for m in re.finditer(r"(?:^|\s)-D\s*([A-Za-z_]\w*)", st.get("OTHER_SWIFT_FLAGS") or ""): swift.add(m.group(1))
    macros = {}
    for tok in re.findall(r'"[^"]*"|\S+', st.get("GCC_PREPROCESSOR_DEFINITIONS") or ""):
        tok = tok.strip('"')
        m = re.fullmatch(r"([A-Za-z_]\w*)(?:=(.*))?", tok)
        if m: macros[m.group(1)] = "1" if m.group(2) is None else m.group(2)
    return swift, macros


# --------------------------------------------------------------------------
# 4. Package.swift / podspec
# --------------------------------------------------------------------------
RE_PKG_TARGET = re.compile(r"\.(?:target|executableTarget|testTarget|macro|systemLibrary|binaryTarget|plugin)\s*\(")
RE_DEFINE = re.compile(r"\.define\s*\(\s*\"([A-Za-z_]\w*)\"\s*(?:,\s*to\s*:\s*\"([^\"]*)\")?\s*(?:,\s*(\.when\s*\(.*?\)))?\s*\)", re.S)


def _balanced(text, start):
    d = 0
    for k in range(start, len(text)):
        if text[k] == "(": d += 1
        elif text[k] == ")":
            d -= 1
            if d == 0: return text[start:k + 1]
    return text[start:]


def _when_release_ios(cond):
    """True / False / None for a `.when(...)` under Release, iOS."""
    if not cond: return True
    c = re.sub(r"\s+", "", cond)
    if "configuration:.debug" in c: return False
    if "configuration:.release" in c: pass
    pm = re.search(r"platforms:\[([^\]]*)\]", c)
    if pm: return ".iOS" in pm.group(1)
    if "configuration:.release" in c: return True
    return None


def package_swift_facts(path):
    """{target_name: {"path": p|None, "kind": target|testTarget|…, "swift": {FLAG: True|False|None}, "c": {NAME: (value, True|False|None)}}}"""
    text = io.open(path, encoding="utf-8", errors="replace").read()
    text = re.sub(r"//[^\n]*", "", text)
    out = {}
    for m in RE_PKG_TARGET.finditer(text):
        blk = _balanced(text, m.end() - 1)
        nm = re.search(r"name\s*:\s*\"([^\"]+)\"", blk)
        if not nm: continue
        pm = re.search(r"(?<![\w.])path\s*:\s*\"([^\"]+)\"", blk)
        kind = re.search(r"\.(\w+)\s*\($", text[m.start():m.end()]).group(1)
        swift = {}; c = {}
        for section, store in (("swiftSettings", swift), ("cSettings", c), ("cxxSettings", c)):
            sm = re.search(section + r"\s*:\s*\[", blk)
            if not sm: continue
            depth = 0; end = sm.end()
            for k in range(sm.end() - 1, len(blk)):
                if blk[k] == "[": depth += 1
                elif blk[k] == "]":
                    depth -= 1
                    if depth == 0: end = k; break
            for dm in RE_DEFINE.finditer(blk[sm.end():end]):
                live = _when_release_ios(dm.group(3))
                if store is swift: store[dm.group(1)] = live
                else: store[dm.group(1)] = (dm.group(2) if dm.group(2) is not None else "1", live)
        out[nm.group(1)] = {"path": pm.group(1) if pm else None, "kind": kind, "swift": swift, "c": c}
    return out


def podspec_facts(path):
    """Flags a podspec adds through xcconfig settings (unit-wide; subspecs are not separated)."""
    text = io.open(path, encoding="utf-8", errors="replace").read()
    swift = set(); macros = {}
    for key in ("SWIFT_ACTIVE_COMPILATION_CONDITIONS", "OTHER_SWIFT_FLAGS", "GCC_PREPROCESSOR_DEFINITIONS"):
        for vm in re.finditer(r"['\"]" + key + r"['\"]\s*(?:=>|:)\s*['\"]([^'\"]*)['\"]", text):
            s, m = flags_from_settings({key: vm.group(1)})
            swift |= s; macros.update(m)
    return swift, macros


# --------------------------------------------------------------------------
# 5. per-repo collection
# --------------------------------------------------------------------------
def _find_projects(root, exclude_rx):
    out = []
    for p in sorted(pathlib.Path(root).rglob("project.pbxproj")):
        rel = p.parent.relative_to(root)
        if exclude_rx and exclude_rx.search(str(rel) + "/"): continue
        if "Pods" in rel.parts or ".build" in rel.parts: continue
        if len(rel.parts) > 5: continue
        out.append(p.parent)
    return out


def collect(root, exclude_rx=None):
    """Facts for one checkout (an app repo): projects, targets, file → targets, Release flags per target.

    file_targets maps a path relative to `root` to [(project, target)].  A target's
    `release` entry has `swift_flags`, `c_macros`, `settings` (the few we keep) and
    `complete` (False when an included xcconfig was not found -- then absence of a
    flag proves nothing)."""
    root = pathlib.Path(root)
    facts = {"projects": [], "file_targets": collections.defaultdict(list), "packages": {}, "notes": []}
    for pd in _find_projects(root, exclude_rx):
        try:
            prj = Project(pd)
        except Exception as e:                                         # a project we cannot read is a recorded gap, not a crash
            facts["notes"].append(f"{pd.relative_to(root)}: unreadable ({e})"); continue
        prel = str(pd.relative_to(root))
        base_rel = pathlib.PurePosixPath(str(prj.root_dir.relative_to(root))) if prj.root_dir != root else pathlib.PurePosixPath("")
        entry = {"path": prel, "configurations": prj.config_names(), "release_config": RELEASE if RELEASE in prj.config_names() else None, "targets": []}
        for t in prj.targets():
            files, synced = prj.source_files(t["id"])
            rel_files = sorted(str(base_rel / f) if str(base_rel) else f for f in files if pathlib.Path(f).suffix in SRC_SUFFIX)
            for f in rel_files: facts["file_targets"][f].append((prel, t["name"]))
            n_synced = 0
            for base, excluded in synced:
                bdir = root / base_rel / base if str(base_rel) else root / base
                if not bdir.is_dir(): continue
                for f in bdir.rglob("*"):
                    if f.suffix not in SRC_SUFFIX or not f.is_file(): continue
                    rf = str(f.relative_to(root))
                    if str(pathlib.PurePosixPath(rf).relative_to(base_rel) if str(base_rel) else rf) in excluded: continue
                    facts["file_targets"][rf].append((prel, t["name"])); n_synced += 1
            manifests = sorted(str(base_rel / f) if str(base_rel) else f for f in prj.resource_files(t["id"]) if f.endswith("PrivacyInfo.xcprivacy"))
            for base, excluded in synced:                             # a synchronized folder bundles its manifest too
                bdir = root / base_rel / base if str(base_rel) else root / base
                if bdir.is_dir():
                    manifests += [str(f.relative_to(root)) for f in bdir.rglob("PrivacyInfo.xcprivacy")]
            rec = {"name": t["name"], "product_type": t["product_type"], "kind": t["kind"], "n_files": len(rel_files) + n_synced,
                   "manifests": sorted(set(manifests))}
            if entry["release_config"]:
                st, ok = prj.settings(t["id"], RELEASE)
                swift, macros = flags_from_settings(st)
                rec["release"] = {"swift_flags": sorted(swift), "c_macros": macros, "complete": ok,
                                  "settings": {k: st[k] for k in ("PRODUCT_BUNDLE_IDENTIFIER", "INFOPLIST_FILE", "CODE_SIGN_ENTITLEMENTS", "SDKROOT") if k in st},
                                  "all_settings": st}
                rec["entitlements"], rec["app_groups"] = entitlement_groups(root, base_rel, st)
            entry["targets"].append(rec)
        entry["notes"] = prj.notes
        facts["projects"].append(entry)
    for p in sorted(root.rglob("Package.swift")):
        rel = p.parent.relative_to(root)
        if exclude_rx and exclude_rx.search(str(rel) + "/"): continue
        if "Pods" in rel.parts or ".build" in rel.parts or "checkouts" in rel.parts: continue
        try:
            facts["packages"][str(rel) or "."] = package_swift_facts(p)
        except Exception as e:
            facts["notes"].append(f"{rel}/Package.swift: unreadable ({e})")
    facts["file_targets"] = dict(facts["file_targets"])
    apps = [(e["path"], t["name"]) for e in facts["projects"] for t in e["targets"] if t["kind"] == "APP"]
    primary = None
    for e in facts["projects"]:
        pname = pathlib.PurePosixPath(e["path"]).stem
        for t in e["targets"]:
            if t["kind"] == "APP" and t["name"] == pname: primary = (e["path"], t["name"])
    facts["app_targets"] = apps
    facts["primary_app_target"] = primary or (apps[0] if apps else None)
    return facts


def expand_placeholders(value, settings):
    """`group.org.wikimedia.wikipedia$(SIGNING_DISAMBIGUATOR)` with the Release settings applied; a
    setting the project never defines expands to "" (what xcodebuild does).  Returns (value, notes)."""
    notes = []
    def rep(m):
        name = m.group(1) or m.group(2)
        if name in settings: return substitute(settings[name], settings)
        notes.append(f"{name} unset → \"\""); return ""
    return re.sub(r"\$\((\w+)(?::[^)]*)?\)|\$\{(\w+)\}", rep, value), notes


def entitlement_groups(root, base_rel, settings):
    """(entitlements path, [app group ids]) of a target from its CODE_SIGN_ENTITLEMENTS in Release."""
    import plistlib
    ent = settings.get("CODE_SIGN_ENTITLEMENTS")
    if not ent: return None, []
    rel = str(base_rel / ent) if str(base_rel) else ent
    p = pathlib.Path(root) / rel
    if not p.is_file(): return rel, []
    try:
        d = plistlib.load(open(p, "rb"))
    except Exception:
        return rel, []
    groups = []
    for g in d.get("com.apple.security.application-groups") or []:
        v, _ = expand_placeholders(str(g), settings); groups.append(v)
    return rel, groups


def target_record(facts, prel, tname):
    for e in facts["projects"]:
        if e["path"] != prel: continue
        for t in e["targets"]:
            if t["name"] == tname: return t
    return None


def flags_for_file(facts, rel, unit_kind, pkg_rel=None, pkg_target=None):
    """What the benchmark build defines for one source file.

    Returns {"targets": [...], "swift_true": set, "swift_false_closed": bool, "c_macros": {…},
             "source": "…"} -- swift_false_closed says the swift flag set is complete, so an
    identifier not in swift_true is false for `#if` in Swift.  Files in several targets get
    the intersection of the true flags and are closed only if every target is complete and
    agrees."""
    res = {"targets": [], "swift_true": set(), "swift_false_closed": False, "c_macros": {}, "source": None}
    if unit_kind in ("SPM", "LOCAL_PKG") and pkg_rel is not None:
        pk = facts["packages"].get(pkg_rel) or {}
        t = pk.get(pkg_target) if pkg_target else None
        if t is None:
            # target not named: choose by path prefix
            for name, rec in pk.items():
                p = rec.get("path") or f"Sources/{name}"
                sub = rel[len(pkg_rel) + 1:] if pkg_rel not in (".", "") else rel
                if sub.startswith(p.rstrip("/") + "/"): t = rec; pkg_target = name; break
        res["source"] = f"Package.swift:{pkg_rel}"
        res["swift_true"] = set(INTEGRATION_FLAGS["SPM"])
        if t:
            res["targets"] = [(pkg_rel, pkg_target)]
            for f, live in t["swift"].items():
                if live is True: res["swift_true"].add(f)
            res["c_macros"] = {k: v for k, (v, live) in t["c"].items() if live is True}
            res["swift_false_closed"] = all(live is not None for live in t["swift"].values())
        return res
    owners = facts["file_targets"].get(rel, [])
    if not owners: return res
    res["targets"] = owners
    trues = []; closed = True; macros = None
    for prel, tname in owners:
        t = target_record(facts, prel, tname)
        r = (t or {}).get("release")
        if not r: closed = False; continue
        trues.append(set(r["swift_flags"])); closed = closed and r["complete"]
        macros = dict(r["c_macros"]) if macros is None else {k: v for k, v in macros.items() if r["c_macros"].get(k) == v}
    if trues:
        inter = set.intersection(*trues); union = set.union(*trues)
        res["swift_true"] = inter; res["swift_false_closed"] = closed and inter == union
        res["c_macros"] = macros or {}
        res["source"] = "pbxproj:Release"
    return res


def macro_string_value(facts, rel, macro, unit_kind):
    """The string a C macro expands to in the file's Release build, when the pbxproj gives one
    (`NAME=$(SETTING)` resolved through the merged settings); else None."""
    for prel, tname in facts["file_targets"].get(rel, []):
        t = target_record(facts, prel, tname)
        r = (t or {}).get("release") or {}
        v = r.get("c_macros", {}).get(macro)
        if v is not None and "$(" not in v: return v.strip('"'), f"{prel}:{tname}:{RELEASE}"
    return None, None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root")
    ap.add_argument("--json", dest="out", default=None)
    a = ap.parse_args(argv)
    facts = collect(a.root)
    for e in facts["projects"]:
        print(f"{e['path']}: configurations {e['configurations']}, release_config={e['release_config']}")
        for t in e["targets"]:
            r = t.get("release") or {}
            print(f"  {t['name']:36s} {t['kind']:14s} files={t['n_files']:5d} swift={sorted(r.get('swift_flags', []))} "
                  f"c={ {k: v for k, v in r.get('c_macros', {}).items() if k not in ('$(inherited)',)} } complete={r.get('complete')}")
        for n in e["notes"]: print("  note:", n)
    for rel, pk in facts["packages"].items():
        print(f"Package.swift @ {rel}: " + ", ".join(f"{n}[{v['kind']}] swift={v['swift']} c={v['c']}" for n, v in pk.items()))
    print(f"files with a target: {len(facts['file_targets'])}; app targets: {facts['app_targets']}; primary: {facts['primary_app_target']}")
    if a.out:
        slim = dict(facts); slim["projects"] = [dict(e, targets=[{k: v for k, v in t.items()} for t in e["targets"]]) for e in facts["projects"]]
        for e in slim["projects"]:
            for t in e["targets"]:
                if "release" in t: t["release"] = {k: v for k, v in t["release"].items() if k != "all_settings"}
        slim["file_targets"] = {k: v for k, v in facts["file_targets"].items()}
        pathlib.Path(a.out).write_text(json.dumps(slim, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
