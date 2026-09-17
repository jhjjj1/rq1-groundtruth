#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Enumerate RRA and alternative-API call sites in source and emit worksheets.

This is the one tool annotation depends on.  The annotator (a model or a
person) never reads a repository; it reads one worksheet record per site.
So the job here is *enumeration and identity*, not judgment:

  * every site gets a stable `site_id` so output can be reconciled 1:1
  * every site carries the ±N lines an annotator needs, the enclosing
    function, the compile-guard chain, and -- when the site is reached
    through an alias -- a pointer back to where the object was obtained,
    plus every construction site of that object the tool can find
  * the manifest that covers the file (nearest ancestor, or the app-level
    one), its declared reasons, and the reason constraints from
    rra_rules.yaml are attached, so the annotator does not have to hunt

WHAT THE FIRST VERSION MISSED (all measured on wikimedia/wikipedia-ios,
swift-nio, Cache 6.0.0 -- see ANNOTATION_PRINCIPLES.md §4.1/§4.2):

  aliases        `NSUserDefaults *ud = [NSUserDefaults standardUserDefaults];
                  [ud boolForKey:…]` -- the read has no "UserDefaults" in it.
  injection      `private let defaults: UserDefaults` then `defaults.set(…)`
                 in every method: a whole file with one match on the type
                 declaration and every real read/write invisible.  The
                 instance's domain is decided by whoever constructs the
                 owning type, so constructions are searched unit-wide.
  extensions     `@objc public extension UserDefaults { var x: Bool {
                 bool(forKey:) } }`: implicit-self calls carry no type name
                 at all (525-line file, 12 hits, 0 real sites in v2 because
                 the `@objc public` prefix broke the extension regex).
  guard chains   `CNIOLinux/shim.c` wraps the entire file in `#ifdef
                 __linux__`; the `statfs` inside does not exist on iOS.
  wrapper types  `NIODeadline.uptimeNanoseconds` is the wrapper's own member,
                 not `DispatchTime`'s; 22 of 23 hits in EventLoop.swift were
                 this.  ALT members now require their receiver.
  suite consts   `initWithSuiteName:WMFApplicationGroupIdentifier` -- the
                 domain is in a constant defined in another file.  Simple
                 string constants are resolved unit-wide.

WHAT THE FIRST FULL-CORPUS RUN (5,030 sites) SHOWED WAS WRONG:

  value aliases  `let stationID = UserDefaults.standard.string(forKey:) ?? ""`
                 is a String; `stationID.isEmpty` was emitted as a site (118
                 false sites, 64 of them `.isEmpty`).  An alias now has to be
                 an *instance* expression.
  scope          an alias declared in one function was matched in every later
                 function of the file.  Declarations are now lexically scoped
                 (block for locals, body for parameters, file for properties).
  func params    `static func bridge(_ knobs:, to defaults: UserDefaults = .standard)`
                 -- 484 sites whose domain is decided by the callers; calls are
                 now searched by function name + external label (SaxWeather
                 274, RevenueCat 169 of them).

WHAT SCOPING THEN BROKE (second full run, 4,716 sites) AND HOW IT IS FIXED:

  wrapper closures  RevenueCat's DeviceCache reaches UserDefaults only through
                 `self.userDefaults.write { $0.set(…) }` / `{ userDefaults in … }`
                 (SynchronizedUserDefaults).  v3 had counted them by the accident
                 of a same-named init parameter; scoping removed all 126.  Methods
                 whose closure parameter is `(UserDefaults) -> …` are now found
                 per unit and their closure parameters (`$0` or the named one)
                 are aliases inside the closure body.
  multi-line     `private static let suite = UserDefaults(\n suiteName: …\n)!`
                 (foqos, 23 sites) failed the instance-expression test because
                 only the first line was read; continuation lines are joined.
  property vs local  decided by brace depth (who opened the block), not by "a
                 func header appears above": a one-line computed property above a
                 stored property had turned the property into a block-scoped local.

THIRD FULL RUN (4,859 sites), diffed site by site against the first:

  trailing comma `guard let defaults = UserDefaults(suiteName: g),` with the
                 next clause on the following line failed the instance test
                 (wBlock, PetNote: 7 sites).
  last parameter `static func f(\n    _ userDefaults: UserDefaults\n) {` -- the
                 parameter on its own line has no trailing `,` or `)`, so it was
                 not a parameter at all (RevenueCat, 6 sites).
  instance_domains  were collected from every same-named declaration in the
                 file, including out-of-scope ones; now only from the declaration
                 the site resolved to.

RRA patterns are derived from `rra_rules.yaml` (the analyzer's definition);
ALT patterns are Appendix C of the principles.  The two lists are disjoint.

Guard evaluation assumes the benchmark build: Release configuration, device
SDK (iphoneos).  `DEBUG` is therefore false.  What the project itself defines
for that build is read from its project files (xcodeproj_facts.py): a Swift
file whose target's Release flag set is fully known gets closed-world
evaluation (`#if TEST` is false when TEST is not among the flags); C/ObjC
identifiers that are neither platform atoms nor project macros stay unknown.

WHAT THE PILOT ANNOTATION (456 sites, principles 1.8) ASKED FOR AND v3.5 ADDS:

  wrapper links  268 of 306 needs_context requests were "the definition of
                 extension member X" (from the call site) or "every caller of
                 this member" (from inside the extension body).  Both are
                 closed facts inside one checkout, so every extension /
                 category member of UserDefaults is indexed (`ud_members`),
                 each WRAPPED site carries `wrapper_ref` (definition, GET/SET/
                 CALL, the keys the body reads or writes), and each body site
                 carries `callers` (the WRAPPED sites that reach it, summarised
                 by resolved domain).
  keys           `forKey:` arguments are resolved to strings where the
                 constant is a visible literal (`key`), so "is this a system
                 key" no longer depends on the constant's name.
  build facts    `#if TEST` (wikipedia-ios) is decided by the pbxproj, not by a
                 "custom flags are false" convention; `NSString *const X =
                 @QUOTE(MACRO)` is resolved through GCC_PREPROCESSOR_DEFINITIONS
                 and the Release build settings; the file → target map gives
                 `target` and `declaring_unit_prefill` (a file under
                 Wikipedia/Code can be compiled into the WMF framework).
  context        the window is the enclosing function (signature always
                 included, capped), not ±8 lines; every line carries its number.

v3.6 (2026-09-17): four ALT rows added after re-checking Appendix C -- Darwin's
CLOCK_MONOTONIC (time since boot incl. sleep; only the _RAW variants were
listed), mach_approximate_time, mach_continuous_approximate_time, and the
keyboard-extension form documentInputMode?.primaryLanguage.  RRA rules come
from cross_rra_analyzer 0.123.82, whose fileModificationDate entry now names
UIDocument (Apple's link) alongside NSDictionary; the API set is unchanged, so
existing site_ids are unaffected and the additions are batched with
`make_batches.py --only-new-vs <previous worksheets> --prefix a`.
"""

import argparse
import collections
import hashlib
import io
import json
import pathlib
import plistlib
import re
import sys
import warnings

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import xcodeproj_facts as xf  # noqa: E402

SRC_EXT = {".swift", ".m", ".mm", ".h", ".c", ".cc", ".cpp", ".hpp"}
#: Excluded everywhere: test targets never link into the app binary; build
#: products and checkouts are not source of this unit.
EXCLUDE_DIR = re.compile(r"(^|/)(Tests?|.*Tests|.*UITests|\.build|DerivedData|Carthage|node_modules|fastlane|\.git)(/|$)")
#: Excluded only under deps/ and pods/: a dependency's example / demo app is
#: never part of the host app.  For the app repos themselves nothing beyond
#: EXCLUDE_DIR is dropped (an app may legitimately live in a directory called
#: Demo); `linked_in_binary` settles what was actually linked later.
EXCLUDE_DIR_DEP = re.compile(r"(^|/)(Examples?|Demos?|Samples?|docs?|Playgrounds?|Benchmarks?)(/|$)", re.I)
#: A path component that suggests the file belongs to an app extension, a
#: watch / mac companion, or another non-main target.  Only a hint: the
#: annotator's `declaring_unit` and the link map decide.
OTHER_TARGET_HINT = re.compile(r"(^|/)([^/]*(?:Watch(?:OS)?|macOS|tvOS|visionOS|Widget|Extension|Intents?|ShareExt|NotificationService|"
                               r"NotificationContent|Clip|Stickers?|Siri|Safari|Keyboard|FileProvider|Today)[^/]*)/", re.I)
VENDOR_HINT = re.compile(r"(^|/)(Third[ _-]?Party|Vendors?|Vendored|External|3rd[ _-]?party)(/|$)", re.I)
CONTEXT = 8


# --------------------------------------------------------------------------
# 1. patterns
# --------------------------------------------------------------------------
def rra_patterns(rules):
    """[(site_class, api_key, categories, tier, tag, regex)] from rules['apis']."""
    pats = []
    for key, a in rules["apis"].items():
        cats = tuple(c.replace("NSPrivacyAccessedAPICategory", "") for c in a.get("apple_category_memberships", []))
        kind = a.get("kind")
        if kind == "api_family":                          # UserDefaults family
            for o in a.get("owners", []):
                bare = o.split(".")[-1]
                pats.append(("RRA", key, cats, "STRONG", bare, re.compile(rf"\b{re.escape(bare)}\b")))
            pats.append(("RRA", key, cats, "STRONG", "@AppStorage", re.compile(r"@AppStorage\s*\(")))
            continue
        for sym in a.get("symbols", []):
            pats.append(("RRA", key, cats, "STRONG", sym, re.compile(rf"\b{re.escape(sym)}\s*\(")))
            # function reference, no call parens: `private let sysStat = stat` (swift-nio NIOPosix binds
            # stat/lstat/fstat this way and calls through the binding), `fp = stat;` in C.  The pilot
            # annotation found these three uses were not candidates at all.  WEAK: `= stat` could also
            # copy a variable named stat.
            pats.append(("RRA", key, cats, "WEAK", f"={sym}",
                         re.compile(rf"(?:[=,(:]|\breturn)\s*\b{re.escape(sym)}\b\s*(?=[,;)\]]|$)")))
        for g in a.get("objc_globals", []):
            pats.append(("RRA", key, cats, "STRONG", g, re.compile(rf"\b{re.escape(g)}\b")))
        for sel in a.get("objc_selectors", []):
            pats.append(("RRA", key, cats, "STRONG", sel, re.compile(rf"\b{re.escape(sel)}\b")))
        for m in a.get("swift_members", []):
            owner, member = m.split(".")[-2], m.split(".")[-1]
            pats.append(("RRA", key, cats, "STRONG", f"{owner}.{member}",
                         re.compile(rf"\b{re.escape(owner)}\.{re.escape(member)}\b")))
            if member.endswith("Key") or member in ("systemUptime", "activeInputModes"):
                pats.append(("RRA", key, cats, "STRONG", f".{member}", re.compile(rf"\.{re.escape(member)}\b")))
            else:
                pats.append(("RRA", key, cats, "WEAK", f".{member}", re.compile(rf"\.{re.escape(member)}\b")))
            if member.endswith("Key") and "URLResourceKey" in m:
                val = member[:-3]
                pats.append(("RRA", key, cats, "WEAK", f".{val}", re.compile(rf"\.{re.escape(val)}\b(?!Key)")))
    seen, out = set(), []
    for p in pats:
        if (p[4], p[3]) in seen: continue
        seen.add((p[4], p[3]))
        kw = re.sub(r"^[.@=]", "", p[4]).split(".")[-1]         # ".contentModificationDateKey" / "=stat" -> literal core
        out.append(p + ([kw],))
    return out


#: Appendix C.  (api_key, mapped category, alt_tier, tag, regex).  Receivers
#: are part of the pattern where a bare member would collide with wrapper types.
#: ALT_KEYWORDS lists, per key, a literal that every match must contain (the
#: line prefilter); a pattern with alternatives lists one literal per branch.
ALT_KEYWORDS = {'alt.clock_gettime_nsec_np.uptime_raw': ['CLOCK_UPTIME_RAW'],
                'alt.clock_gettime.uptime_raw': ['CLOCK_UPTIME_RAW'],
                'alt.clock_gettime.uptime_raw_approx': ['CLOCK_UPTIME_RAW_APPROX'],
                'alt.clock_gettime.monotonic_raw': ['CLOCK_MONOTONIC_RAW'],
                'alt.clock_gettime.monotonic_raw_approx': ['CLOCK_MONOTONIC_RAW_APPROX'],
                'alt.clock_gettime.monotonic': ['CLOCK_MONOTONIC'],
                'alt.mach_continuous_time': ['mach_continuous_time'],
                'alt.mach_approximate_time': ['mach_approximate_time'],
                'alt.mach_continuous_approximate_time': ['mach_continuous_approximate_time'],
                'alt.dispatch_time.uptime_nanoseconds': ['DispatchTime'],
                'alt.ca_current_media_time': ['CACurrentMediaTime'],
                'alt.sysctl.kern_boottime': ['KERN_BOOTTIME', 'kern.boottime'],
                'alt.suspending_clock.now': ['SuspendingClock'],
                'alt.continuous_clock.now': ['ContinuousClock'],
                'alt.getfsstat': ['getfsstat'],
                'alt.getmntinfo': ['getmntinfo'],
                'alt.text_input_mode.primary_language': ['primaryLanguage'],
                'alt.text_document_proxy.primary_language': ['primaryLanguage'],
                'alt.cfpreferences.app': ['CFPreferences'],
                'alt.cfpreferences.domain': ['CFPreferences']}
ALT_PATTERNS = [
    ("alt.clock_gettime_nsec_np.uptime_raw", "SystemBootTime", "NEAR_EQUIVALENT", "clock_gettime_nsec_np(CLOCK_UPTIME_RAW",
     re.compile(r"\bclock_gettime_nsec_np\s*\(\s*CLOCK_UPTIME_RAW\b(?!_APPROX)")),
    ("alt.clock_gettime.uptime_raw", "SystemBootTime", "NEAR_EQUIVALENT", "clock_gettime(CLOCK_UPTIME_RAW",
     re.compile(r"\bclock_gettime\s*\(\s*CLOCK_UPTIME_RAW\b(?!_APPROX)")),
    ("alt.clock_gettime.uptime_raw_approx", "SystemBootTime", "NEAR_EQUIVALENT", "CLOCK_UPTIME_RAW_APPROX",
     re.compile(r"\bclock_gettime(_nsec_np)?\s*\(\s*CLOCK_UPTIME_RAW_APPROX\b")),
    ("alt.clock_gettime.monotonic_raw", "SystemBootTime", "NEAR_EQUIVALENT", "CLOCK_MONOTONIC_RAW",
     re.compile(r"\bclock_gettime(_nsec_np)?\s*\(\s*CLOCK_MONOTONIC_RAW\b(?!_APPROX)")),
    ("alt.clock_gettime.monotonic_raw_approx", "SystemBootTime", "NEAR_EQUIVALENT", "CLOCK_MONOTONIC_RAW_APPROX",
     re.compile(r"\bclock_gettime(_nsec_np)?\s*\(\s*CLOCK_MONOTONIC_RAW_APPROX\b")),
    # Darwin's CLOCK_MONOTONIC is time since boot including sleep (same source as mach_continuous_time);
    # the Linux reading of the name does not apply on iOS.  The _RAW / _RAW_APPROX variants have their own rows.
    ("alt.clock_gettime.monotonic", "SystemBootTime", "NEAR_EQUIVALENT", "CLOCK_MONOTONIC",
     re.compile(r"\bclock_gettime(_nsec_np)?\s*\(\s*CLOCK_MONOTONIC\b(?!_RAW)")),
    ("alt.mach_continuous_time", "SystemBootTime", "NEAR_EQUIVALENT", "mach_continuous_time(",
     re.compile(r"\bmach_continuous_time\s*\(")),
    ("alt.mach_approximate_time", "SystemBootTime", "NEAR_EQUIVALENT", "mach_approximate_time(",
     re.compile(r"\bmach_approximate_time\s*\(")),
    ("alt.mach_continuous_approximate_time", "SystemBootTime", "NEAR_EQUIVALENT", "mach_continuous_approximate_time(",
     re.compile(r"\bmach_continuous_approximate_time\s*\(")),
    ("alt.dispatch_time.uptime_nanoseconds", "SystemBootTime", "NEAR_EQUIVALENT", "DispatchTime…uptimeNanoseconds",
     re.compile(r"\bDispatchTime\s*(\.now\s*\(\s*\)|\([^()]*\))\s*\.\s*(uptimeNanoseconds|rawValue)\b")),
    ("alt.ca_current_media_time", "SystemBootTime", "NEAR_EQUIVALENT", "CACurrentMediaTime(",
     re.compile(r"\bCACurrentMediaTime\s*\(")),
    ("alt.sysctl.kern_boottime", "SystemBootTime", "CONDITIONAL", "KERN_BOOTTIME",
     re.compile(r"\bKERN_BOOTTIME\b|\"kern\.boottime\"")),
    ("alt.suspending_clock.now", "SystemBootTime", "PARTIAL_DATUM", "SuspendingClock",
     re.compile(r"\bSuspendingClock\b")),
    ("alt.continuous_clock.now", "SystemBootTime", "PARTIAL_DATUM", "ContinuousClock",
     re.compile(r"\bContinuousClock\b")),
    ("alt.getfsstat", "DiskSpace", "CONDITIONAL", "getfsstat(", re.compile(r"\bgetfsstat\s*\(")),
    ("alt.getmntinfo", "DiskSpace", "CONDITIONAL", "getmntinfo(", re.compile(r"\bgetmntinfo(_r_np)?\s*\(")),
    ("alt.text_input_mode.primary_language", "ActiveKeyboards", "PARTIAL_DATUM", "textInputMode…primaryLanguage",
     re.compile(r"\b(textInputMode|UITextInputMode)\b[^;\n]{0,60}?\.primaryLanguage\b")),
    # keyboard-extension side: UITextDocumentProxy.documentInputMode?.primaryLanguage
    ("alt.text_document_proxy.primary_language", "ActiveKeyboards", "PARTIAL_DATUM", "documentInputMode…primaryLanguage",
     re.compile(r"\bdocumentInputMode\b[^;\n]{0,40}?\.primaryLanguage\b")),
    ("alt.cfpreferences.app", "UserDefaults", "CONDITIONAL", "CFPreferences*App*",
     re.compile(r"\bCFPreferences(CopyAppValue|SetAppValue|AppSynchronize|CopyMultiple|SetMultiple)\s*\(")),
    ("alt.cfpreferences.domain", "UserDefaults", "CONDITIONAL", "CFPreferences{Copy,Set}Value",
     re.compile(r"\bCFPreferences(CopyValue|SetValue|Synchronize)\s*\(")),
]

#: UserDefaults family members -> operation (for alias / extension-body sites
#: and the operation prefill of direct sites).
UD_OPS_SWIFT = {
    "object": "READ", "string": "READ", "bool": "READ", "integer": "READ", "double": "READ", "float": "READ",
    "array": "READ", "dictionary": "READ", "data": "READ", "url": "READ", "stringArray": "READ", "value": "READ",
    "dictionaryRepresentation": "READ", "persistentDomain": "READ", "volatileDomain": "READ", "objectIsForced": "READ",
    "set": "WRITE", "setValue": "WRITE", "register": "WRITE", "setPersistentDomain": "WRITE", "setVolatileDomain": "WRITE",
    "removeObject": "REMOVE", "removePersistentDomain": "REMOVE", "removeVolatileDomain": "REMOVE",
    "addObserver": "OBSERVE", "removeObserver": "OBSERVE", "publisher": "OBSERVE",
    "synchronize": "SYNC", "addSuite": "SYNC", "removeSuite": "SYNC",
}
UD_OPS_OBJC = {
    "objectForKey": "READ", "stringForKey": "READ", "boolForKey": "READ", "integerForKey": "READ", "doubleForKey": "READ",
    "floatForKey": "READ", "arrayForKey": "READ", "dictionaryForKey": "READ", "dataForKey": "READ", "URLForKey": "READ",
    "stringArrayForKey": "READ", "valueForKey": "READ", "dictionaryRepresentation": "READ", "persistentDomainForName": "READ",
    "volatileDomainForName": "READ", "objectIsForcedForKey": "READ",
    "setObject": "WRITE", "setBool": "WRITE", "setInteger": "WRITE", "setDouble": "WRITE", "setFloat": "WRITE",
    "setURL": "WRITE", "setValue": "WRITE", "registerDefaults": "WRITE", "setPersistentDomain": "WRITE", "setVolatileDomain": "WRITE",
    "removeObjectForKey": "REMOVE", "removePersistentDomainForName": "REMOVE", "removeVolatileDomainForName": "REMOVE",
    "addObserver": "OBSERVE", "removeObserver": "OBSERVE",
    "synchronize": "SYNC", "addSuiteNamed": "SYNC", "removeSuiteNamed": "SYNC",
}
UD_MEMBERS_SWIFT = "|".join(sorted(UD_OPS_SWIFT, key=len, reverse=True))
UD_MEMBERS_OBJC = "|".join(sorted(UD_OPS_OBJC, key=len, reverse=True))


# --------------------------------------------------------------------------
# 2. comments, strings, guards
# --------------------------------------------------------------------------
RE_BLOCK = re.compile(r"/\*.*?\*/", re.S)
RE_LINE = re.compile(r"//[^\n]*")


def strip_comments(text):
    text = RE_BLOCK.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return RE_LINE.sub("", text)


def in_string(line, col):
    """Is `col` inside a double-quoted literal on this line (unescaped quotes)?"""
    q = 0; i = 0
    while i < col:
        if line[i] == "\\": i += 2; continue
        if line[i] == '"': q += 1
        i += 1
    return q % 2 == 1


#: Three-valued evaluation of one guard atom on iOS Release (device SDK).
TRUE_ATOMS = {"os(iOS)", "canImport(Darwin)", "canImport(UIKit)", "canImport(Foundation)", "canImport(Dispatch)",
              "canImport(ObjectiveC)", "canImport(Combine)", "canImport(SwiftUI)", "canImport(CoreFoundation)",
              "canImport(Network)", "canImport(CryptoKit)", "canImport(Security)", "canImport(CoreGraphics)",
              "canImport(QuartzCore)", "canImport(CoreData)", "canImport(CoreLocation)", "canImport(AVFoundation)",
              "canImport(WebKit)", "canImport(UserNotifications)", "canImport(os)", "canImport(OSLog)",
              "canImport(CoreText)", "canImport(CoreMedia)", "canImport(CoreImage)", "canImport(Metal)",
              "canImport(MapKit)", "canImport(StoreKit)", "canImport(CloudKit)", "canImport(Compression)",
              "canImport(zlib)", "canImport(CommonCrypto)", "canImport(MobileCoreServices)",
              "canImport(UniformTypeIdentifiers)", "canImport(SystemConfiguration)", "canImport(Photos)",
              "canImport(AuthenticationServices)", "canImport(LocalAuthentication)", "canImport(MachO)",
              "canImport(Observation)", "canImport(CoreTelephony)", "canImport(Accelerate)", "canImport(simd)",
              "__APPLE__", "TARGET_OS_IPHONE", "TARGET_OS_IOS", "TARGET_OS_MAC", "__MACH__",
              "__OBJC__", "__aarch64__", "__arm64__", "arch(arm64)", "_POSIX_VERSION"}
FALSE_ATOMS = {"os(Linux)", "os(macOS)", "os(watchOS)", "os(tvOS)", "os(visionOS)", "os(Windows)", "os(Android)",
               "os(WASI)", "os(FreeBSD)", "os(OpenBSD)", "canImport(Glibc)", "canImport(Musl)", "canImport(Android)",
               "canImport(WinSDK)", "canImport(WASILibc)", "canImport(Bionic)", "canImport(AppKit)", "canImport(Cocoa)",
               "canImport(WatchKit)", "canImport(Carbon)", "canImport(IOKit)", "canImport(ScreenCaptureKit)",
               "canImport(CRT)", "canImport(ucrt)",
               "targetEnvironment(simulator)", "targetEnvironment(macCatalyst)", "DEBUG",
               "__linux__", "__gnu_linux__", "__ANDROID__", "_WIN32", "_WIN64", "__FreeBSD__", "__EMSCRIPTEN__",
               "TARGET_OS_OSX", "TARGET_OS_SIMULATOR", "TARGET_OS_WATCH", "TARGET_OS_TV", "TARGET_OS_MACCATALYST",
               "TARGET_OS_VISION", "TARGET_OS_BRIDGE", "arch(x86_64)", "arch(i386)", "__x86_64__", "__i386__"}
RE_ATOM = re.compile(r"(os|canImport|targetEnvironment|arch|swift|compiler|defined)\s*\(\s*([^()]*)\s*\)|[A-Za-z_]\w*")


def eval_guard(cond):
    """True / False / None(unknown) for a #if condition on iOS Release."""
    cond = cond.strip()
    def atom(m):
        if m.group(1) in ("swift", "compiler"):
            return "True"
        if m.group(1) == "defined":
            w = m.group(2).strip()
            if w in unit_macros_ref[0]: return "True"      # defined, whatever its value (`#define X 0` is still defined)
            return atom_word(w)
        if m.group(1):
            key = f"{m.group(1)}({m.group(2).strip()})"
            return "True" if key in TRUE_ATOMS else "False" if key in FALSE_ATOMS else "None"
        return atom_word(m.group(0))
    def atom_word(w):
        if w in ("true", "1"): return "True"
        if w in ("false", "0"): return "False"
        if w in TRUE_ATOMS: return "True"
        if w in FALSE_ATOMS: return "False"
        um = unit_macros_ref[0].get(w)
        if um is None:
            return "False" if closed_flags_ref[0] else "None"   # closed world: a Swift flag the build does not define is false
        return "True" if um else "False"
    expr = RE_ATOM.sub(atom, cond)
    expr = expr.replace("&&", " and ").replace("||", " or ").replace("!", " not ")
    if not re.fullmatch(r"[\sTrueFalsNoandt()]*", expr):
        return None
    # three-valued: try both substitutions for None; agree -> that value
    vals = set()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for sub in ("True", "False"):
            try:
                vals.add(bool(eval(expr.replace("None", sub), {"__builtins__": {}})))
            except Exception:
                return None
    return vals.pop() if len(vals) == 1 else None


#: Project-defined 0/1 macros of the unit being scanned (SDWebImage's SD_UIKIT /
#: SD_MAC / SD_WATCH …), collected from `#define NAME 0|1` lines whose own guard
#: chain is live on iOS.  A name defined with conflicting values is dropped.
unit_macros_ref = [{}]
#: True while scanning a Swift file whose target's Release flag set is completely known
#: (xcodeproj_facts): then an identifier not in unit_macros_ref is false, not unknown.
closed_flags_ref = [False]
RE_DEFINE_01 = re.compile(r"^\s*#\s*define\s+(\w+)(?:\s+(0|1))?\s*$")


def collect_unit_macros(files, rounds=2):
    macros = {}
    for _ in range(rounds):
        unit_macros_ref[0] = dict(macros)
        found = {}; conflict = set()
        for f in files:
            if f.suffix not in (".h", ".hpp", ".swift"): continue
            try:
                lines = strip_comments(f.read_text(encoding="utf-8", errors="replace")).splitlines()
            except Exception:
                continue
            chains = guard_chains(lines)
            for (chain, live), l in zip(chains, lines):
                m = RE_DEFINE_01.match(l)
                if not m or live is not True: continue
                name, val = m.group(1), (m.group(2) is None) or m.group(2) == "1"
                if name in TRUE_ATOMS or name in FALSE_ATOMS: continue
                if name in found and found[name] != val: conflict.add(name)
                found[name] = val
        macros = {k: v for k, v in found.items() if k not in conflict}
    unit_macros_ref[0] = macros
    return macros


RE_PP = re.compile(r"^\s*#\s*(if|ifdef|ifndef|elseif|elif|else|endif)\b\s*(.*)$")


def guard_chains(lines):
    """For each line index, the enclosing guard chain [texts] and net liveness."""
    stack = []
    per_line = []
    for line in lines:
        m = RE_PP.match(line)
        if m:
            kw, cond = m.group(1), m.group(2).strip()
            if kw in ("if", "ifdef", "ifndef"):
                c = cond if kw == "if" else (f"defined({cond})" if kw == "ifdef" else f"!defined({cond})")
                live = eval_guard(c)
                stack.append({"texts": [f"#{kw} {cond}"], "live": live, "prev": [live]})
            elif kw in ("elseif", "elif") and stack:
                prev = stack[-1]["prev"]
                this = eval_guard(cond)
                if any(p is None for p in prev) or this is None:
                    live = None if (this is not False and not any(p is True for p in prev)) else False
                else:
                    live = (not any(prev)) and this
                stack[-1]["texts"] = stack[-1]["texts"] + [f"#elseif {cond}"]        # keep the whole branch history
                stack[-1]["live"] = live; stack[-1]["prev"].append(this)
            elif kw == "else" and stack:
                prev = stack[-1]["prev"]
                live = None if any(p is None for p in prev) else (not any(prev))
                stack[-1]["texts"] = stack[-1]["texts"] + ["#else"]
                stack[-1]["live"] = live
            elif kw == "endif" and stack:
                stack.pop()
        chain = [t for f in stack for t in f["texts"]]
        lives = [f["live"] for f in stack]
        net = False if any(l is False for l in lives) else (None if any(l is None for l in lives) else True)
        per_line.append((chain, net))
    return per_line


# --------------------------------------------------------------------------
# 3. enclosing function / type, aliases, extension bodies, constants
# --------------------------------------------------------------------------
MODS = r"(?:(?:public|private|internal|fileprivate|open|static|class|final|override|mutating|nonisolated|convenience|required|lazy|weak|unowned|dynamic|indirect|package)\s+)*"
ATTRS = r"(?:@\w+(?:\([^)]*\))?\s+)*"
RE_FUNC_SWIFT = re.compile(rf"^\s*{ATTRS}{MODS}(func\s+[\w`]+|init\b|deinit\b|subscript\b|(?:var|let)\s+\w+\s*:[^=]*\{{\s*$|(?:var|let)\s+\w+\s*\{{\s*$)")
RE_FUNC_OBJC = re.compile(r"^\s*[-+]\s*\(")
RE_FUNC_C = re.compile(r"^[A-Za-z_][\w\s\*]*\b\w+\s*\([^;{]*\)\s*\{?\s*$")
RE_TYPE_SWIFT = re.compile(rf"^\s*{ATTRS}{MODS}(class|struct|actor|enum|extension)\s+([\w.]+)")
RE_TYPE_OBJC = re.compile(r"^\s*@(implementation|interface)\s+(\w+)")


def enclosing_function(lines, idx, lang):
    for i in range(idx, -1, -1):
        l = lines[i]
        if lang == "swift" and RE_FUNC_SWIFT.match(l): return l.strip()[:120]
        if lang == "objc" and RE_FUNC_OBJC.match(l): return l.strip()[:120]
        if lang in ("c", "objc") and RE_FUNC_C.match(l) and not l.strip().startswith(("if", "for", "while", "switch", "return")):
            return l.strip()[:120]
    return None


def enclosing_type(lines, idx, lang):
    rx = RE_TYPE_SWIFT if lang == "swift" else RE_TYPE_OBJC
    for i in range(idx, -1, -1):
        m = rx.match(lines[i])
        if m: return m.group(2)
    return None


def brace_depths(lines):
    """depth_before[i] = net count of `{` minus `}` on lines before i (comments already stripped)."""
    out = []; d = 0
    for l in lines:
        out.append(d)
        d += l.count("{") - l.count("}")
    return out


def is_local_decl(lines, depth, idx, lang):
    """True when the block containing the declaration at idx is a function / closure /
    control-flow body rather than a type body or the file's top level.

    Found by brace depth: the nearest earlier line at a smaller depth is the one that
    opened the block; if that line is a type header (`class X {`, `extension X {`,
    `@implementation X`) the declaration is a property, otherwise it is local.  A
    one-line computed property above a stored property must not make it "local".
    """
    d0 = depth[idx]
    if d0 == 0: return False
    rx_t = RE_TYPE_SWIFT if lang == "swift" else RE_TYPE_OBJC
    for k in range(idx - 1, -1, -1):
        if depth[k] < d0:
            return not rx_t.match(lines[k])
    return False


def alias_scope_end(lines, idx, depth, form):
    """Last line index (inclusive) on which the alias declared at idx is in scope.

    local `let x = …`         until the enclosing block closes (an `if let … {` on the
                              same line opens its own block: scope is that block)
    parameter                 the function body: from its `{` until that block closes
    property / top level      the whole file (same-file extensions can reach a property)
    """
    n = len(lines)
    if form in ("INIT_PARAMETER", "FUNC_PARAMETER", "PARAMETER"):
        base = depth[idx] + 1
        k = idx
        while k < n and (depth[k + 1] if k + 1 < n else depth[k]) < base: k += 1       # to the body opener
        for j in range(k + 1, n):
            if depth[j] < base: return j - 1
        return n - 1
    net = lines[idx].count("{") - lines[idx].count("}")
    guard = re.search(r"\bguard\b|\belse\s*\{", lines[idx]) is not None   # `guard let x = … else {` binds x in the enclosing scope
    base = depth[idx] + (1 if net > 0 and not guard else 0)
    for j in range(idx + 1, n):
        if depth[j] < base: return j - 1
    return n - 1


# Alias declarations.  Each regex yields (name, init_expr|None, form).
RE_ALIAS_SWIFT = [
    # let ud = UserDefaults.standard / UserDefaults(suiteName:) / .standard (typed)
    (re.compile(r"\b(?:let|var)\s+(\w+)\s*(?::\s*(?:NS)?UserDefaults\??)?\s*=\s*((?:NS)?UserDefaults\b[^\n]*|\.standard\b[^\n]*|\.init\s*\(\s*suiteName[^\n]*)"), "LOCAL_OR_PROPERTY"),
    # computed property with visible construction:  var shared: UserDefaults? { UserDefaults(suiteName: …) }
    (re.compile(r"\b(?:let|var)\s+(\w+)\s*:\s*(?:NS)?UserDefaults\??\s*\{\s*(?:return\s+)?((?:NS)?UserDefaults\b[^\n]*|\.standard\b[^\n]*)"), "LOCAL_OR_PROPERTY"),
    # stored property without initializer (injection):  private let defaults: UserDefaults
    (re.compile(r"\b(?:let|var)\s+(\w+)\s*:\s*(?:NS)?UserDefaults\??\s*(?:$|\{|\n)"), "INJECTED_PROPERTY"),
    # parameter:  init(defaults: UserDefaults = .standard)   /   func f(to store: UserDefaults)
    # groups: (external label | None, name, default | None)
    (re.compile(r"(?<![\w.])(?:(\w+)\s+)?(\w+)\s*:\s*(?:NS)?UserDefaults\??\s*(?:=\s*([^,)]+?))?\s*(?=[,)]|$)"), "PARAMETER"),
]
#: An expression that *is* a UserDefaults instance (not a value read from one):
#: UserDefaults.standard / UserDefaults(suiteName: x) / UserDefaults() / .standard /
#: UserDefaults.init(suiteName:) / any of these with `!`, `?` or `?? <instance>`.
#: `UserDefaults.standard.string(forKey:)` is a String, so `let x = …` there is
#: a value alias and `x.isEmpty` is not a site (118 false sites in the first
#: full-corpus run, 64 of them `.isEmpty`).
_INST = r"(?:(?:NS)?UserDefaults(?:\.standard|\.init\s*\([^()]*(?:\([^()]*\)[^()]*)*\)|\s*\([^()]*(?:\([^()]*\)[^()]*)*\))?|\.standard|\.init\s*\([^()]*\))\s*[?!]?"
RE_INSTANCE_EXPR = re.compile(rf"^\s*{_INST}\s*(?:\?\?\s*{_INST}\s*)?$")
RE_CALLABLE = re.compile(r"\b(?:func\s+([\w`]+)|(init))\s*\(")


def instance_expr(init):
    """Trim `else { … }` / trailing `{` / comments and test for an instance expression."""
    if init is None: return None
    e = re.sub(r"\s*else\s*\{.*$", "", init.strip())
    e = re.sub(r"\s*[{,]\s*$", "", e).rstrip(" ;")          # `guard let d = UserDefaults(suiteName: g),` continues on the next line
    return e if RE_INSTANCE_EXPR.match(e) else None


def enclosing_callable(lines, idx):
    """('FUNC', name) | ('INIT', None) | None for a parameter declared at line idx (signature may span lines)."""
    for i in range(idx, max(-1, idx - 40), -1):
        m = RE_CALLABLE.search(lines[i])
        if m:
            return ("FUNC", m.group(1)) if m.group(1) else ("INIT", None)
        if i != idx and re.search(r"[{}]\s*$", lines[i]):
            return None
    return None
RE_ALIAS_OBJC = [
    (re.compile(r"\bNSUserDefaults\s*\*\s*(\w+)\s*=\s*(\[[^\n;]*)"), "LOCAL_OR_PROPERTY"),
    (re.compile(r"@property\s*\([^)]*\)\s*NSUserDefaults\s*\*\s*(\w+)"), "INJECTED_PROPERTY"),
    (re.compile(r"\bNSUserDefaults\s*\*\s*_?(\w+)\s*;"), "INJECTED_PROPERTY"),
]
RE_EXT_SWIFT = re.compile(rf"^\s*{ATTRS}{MODS}extension\s+(?:Foundation\.)?(NS)?UserDefaults\b")
RE_EXT_OBJC = re.compile(r"^\s*@(implementation|interface)\s+NSUserDefaults\s*\(")

#: Simple string constants:  let X = "…"  /  NSString *const X = @"…"  /  #define X @"…"  /  case x = "…"
RE_CONST = [
    re.compile(r"\b(?:let|var)\s+(\w+)\s*(?::\s*String)?\s*=\s*\"([^\"\\\n]*)\""),
    re.compile(r"\bNSString\s*\*\s*(?:const\s+)?(\w+)\s*=\s*@\"([^\"\\\n]*)\""),
    re.compile(r"^\s*#\s*define\s+(\w+)\s+@?\"([^\"\\\n]*)\""),
    re.compile(r"^\s*case\s+(\w+)\s*=\s*\"([^\"\\\n]*)\""),
]
#: `NSString *const X = @QUOTE(MACRO);` -- the string is a build macro stringized by a helper
#: macro (wikipedia-ios: WMF_APP_GROUP_IDENTIFIER=$(WMF_APP_GROUP_IDENTIFIER) in
#: GCC_PREPROCESSOR_DEFINITIONS, resolved by xcodeproj_facts).
RE_CONST_MACRO = re.compile(r"\bNSString\s*\*\s*(?:const\s+)?(\w+)\s*=\s*@\s*\w+\s*\(\s*(\w+)\s*\)")
#: where each constant value came from:  {name: {value: "file:line[ (via …)]"}}
const_where_ref = [collections.defaultdict(dict)]


def domain_of(init_expr, consts):
    """Domain of a UserDefaults construction expression. Returns (domain, note)."""
    if not init_expr: return "UNKNOWN", None
    e = init_expr.strip()
    if re.search(r"\.standard\b|standardUserDefaults\b", e): return "APP_PRIVATE", None
    if re.search(r"^(?:NS)?UserDefaults\s*\(\s*\)|\[\s*\[\s*NSUserDefaults\s+alloc\s*\]\s*init\s*\]", e): return "APP_PRIVATE", None
    m = re.search(r'(?:initWithSuiteName|suiteName)\s*:\s*@?"([^"]*)"', e)
    if m:
        return ("APP_GROUP" if m.group(1).startswith("group.") else f"SUITE:{m.group(1)}"), None
    m = re.search(r"(?:initWithSuiteName|suiteName)\s*:\s*([A-Za-z_][\w.]*)\s*[,)\]]", e)
    if m:
        name = m.group(1).split(".")[-1]
        vals = consts.get(name)
        if vals and len(vals) == 1:
            v = next(iter(vals))
            prov = const_provenance(name, v)
            return ("APP_GROUP" if v.startswith("group.") else f"SUITE:{v}"), f"SUITE_CONST:{name}={v!r}" + (f" @ {prov}" if prov else "")
        if vals: return "UNKNOWN", f"SUITE_CONST:{name} ambiguous {sorted(vals)[:4]}"
        return "UNKNOWN", f"SUITE_CONST:{name} unresolved"
    m = re.search(r"(?:initWithSuiteName|suiteName)\s*:\s*([^,)]+)", e)
    if m: return "UNKNOWN", f"SUITE_EXPR:{m.group(1).strip()[:60]}"
    return "UNKNOWN", None


#: `func read<T>(_ action: (UserDefaults) throws -> T)`: a method whose closure
#: parameter receives a UserDefaults.  RevenueCat's SynchronizedUserDefaults
#: has read/write like this, and DeviceCache does every access through
#: `self.userDefaults.write { $0.set(…) }` / `{ userDefaults in … }` --
#: 126 sites across its three package identities that no alias rule sees.
RE_WRAPPER_METHOD = re.compile(r"\bfunc\s+([\w`]+)\s*(?:<[^>]*>)?\s*\(\s*(?:_\s+)?\w+\s*:\s*(?:@escaping\s+)?(?:@Sendable\s+)?\(\s*(?:inout\s+)?(?:NS)?UserDefaults\s*\)\s*(?:re)?throws?\s*->")
RE_CLOSURE_PARAMS = re.compile(r"\{\s*(?:\(\s*)?(\w+)(?:\s*,\s*\w+)*\s*\)?\s*in\b")


def collect_wrappers(files):
    """{method_name: {owner types}} for methods whose closure parameter is a UserDefaults."""
    out = collections.defaultdict(set)
    for f in files:
        if f.suffix != ".swift": continue
        try:
            lines = strip_comments(f.read_text(encoding="utf-8", errors="replace")).splitlines()
        except Exception:
            continue
        for i, l in enumerate(lines):
            m = RE_WRAPPER_METHOD.search(l) or (RE_WRAPPER_METHOD.search(" ".join(lines[i:i + 3])) if "func " in l and "->" not in l else None)
            if m:
                out[m.group(1)].add(enclosing_type(lines, i, "swift") or "?")
    return dict(out)


wrappers_ref = [{}]


def closure_aliases(lines, depth, lang):
    """Alias declarations for closure parameters of wrapper methods (see RE_WRAPPER_METHOD).

    Only calls whose receiver is a property / parameter declared with the
    wrapper's type in this file count (`self.userDefaults.write { … }` where
    `userDefaults: SynchronizedUserDefaults`), so an unrelated `.write {` on a
    file handle is not an alias.  `{ x in` binds x; no `in` binds `$0`.
    """
    wrappers = wrappers_ref[0]
    if lang != "swift" or not wrappers: return []
    types = {t for ts in wrappers.values() for t in ts if t != "?"}
    if not types: return []
    recv_rx = re.compile(r"(\w+)\s*:\s*(" + "|".join(re.escape(t) for t in types) + r")\b")
    receivers = {m.group(1): m.group(2) for l in lines for m in recv_rx.finditer(l)}
    if not receivers: return []
    call_rx = re.compile(r"(?<![\w.])(?:self\s*\.\s*)?(" + "|".join(re.escape(r) for r in receivers) + r")\s*\.\s*(" +
                         "|".join(re.escape(m) for m in wrappers) + r")\s*(?:\([^()]*\))?\s*\{")
    out = []
    n = len(lines)
    for i, l in enumerate(lines):
        for m in call_rx.finditer(l):
            recv, meth = m.group(1), m.group(2)
            if receivers[recv] not in wrappers[meth]: continue
            pm = RE_CLOSURE_PARAMS.search(l[m.end() - 1:])
            name = pm.group(1) if pm else "$0"
            if l.count("{") - l.count("}") <= 0:
                end = i                                           # single-line closure
            else:
                base = depth[i] + 1; end = n - 1
                for j in range(i + 1, n):
                    if depth[j] < base: end = j - 1; break
            out.append({"name": name, "decl_line": i + 1, "decl": l.strip()[:140], "form": "CLOSURE_PARAMETER", "init": None,
                        "type": enclosing_type(lines, i, "swift"), "label": None, "callable": f"{receivers[recv]}.{meth}",
                        "local": True, "scope_end": end + 1, "receiver": recv})
    return out


def find_aliases(lines, lang):
    """name -> [ {decl_line, decl, form, init, type, label, callable} … ] in line order (all declarations).

    forms: LOCAL_OR_PROPERTY (instance expression visible), INJECTED_PROPERTY
    (typed, no initializer), INIT_PARAMETER / FUNC_PARAMETER (parameter of an
    init / func, `label` is the external argument label, `callable` the func
    name), PARAMETER (parameter whose callable could not be located).
    """
    out = collections.defaultdict(list)
    pats = RE_ALIAS_SWIFT if lang == "swift" else RE_ALIAS_OBJC
    depth = brace_depths(lines)
    for i, l in enumerate(lines):
        for rx, form in pats:
            for m in rx.finditer(l):
                label = callable_name = None
                if form == "PARAMETER":
                    label, name, init = m.group(1), m.group(2), m.group(3)
                    label = label or name
                    if label in ("let", "var", "case", "func", "init"): continue
                else:
                    name = m.group(1)
                    init = m.group(2) if (m.lastindex and m.lastindex >= 2) else None
                if name in ("self", "super", "let", "var", "return", "case"): continue
                if any(d["decl_line"] == i + 1 and d["name"] == name for d in out[name]): continue
                f = form
                if form == "LOCAL_OR_PROPERTY" and lang == "swift":
                    if init and init.count("(") > init.count(")"):          # construction continues on the next lines
                        for extra in lines[i + 1:i + 6]:
                            init = init.rstrip() + " " + extra.strip()
                            if init.count("(") <= init.count(")"): break
                    inst = instance_expr(init)
                    if inst is None: continue                 # a value read from defaults, not an instance
                    init = inst
                if form == "PARAMETER":
                    if re.search(r"\b(?:let|var)\s+" + re.escape(name) + r"\s*:", l): continue
                    c = enclosing_callable(lines, i)
                    if c and c[0] == "INIT": f = "INIT_PARAMETER"
                    elif c and c[0] == "FUNC": f, callable_name = "FUNC_PARAMETER", c[1]
                local = f in ("INIT_PARAMETER", "FUNC_PARAMETER", "PARAMETER") or (f == "LOCAL_OR_PROPERTY" and is_local_decl(lines, depth, i, lang))
                scope_end = alias_scope_end(lines, i, depth, f if local else "PROPERTY") if local else len(lines) - 1
                out[name].append({"name": name, "decl_line": i + 1, "decl": l.strip()[:140], "form": f, "init": init,
                                  "type": enclosing_type(lines, i, lang), "label": label, "callable": callable_name,
                                  "local": local, "scope_end": scope_end + 1})
    for d in closure_aliases(lines, depth, lang):
        out[d["name"]].append(d)
    for v in out.values():
        v.sort(key=lambda d: d["decl_line"])
    return {k: v for k, v in out.items() if v}


def extension_ranges(lines, lang):
    """[(start_idx, end_idx, decl_text)] of extension / category bodies of UserDefaults."""
    out = []
    if lang == "swift":
        i = 0
        while i < len(lines):
            if RE_EXT_SWIFT.match(lines[i]):
                depth = 0; j = i; opened = False
                while j < len(lines):
                    depth += lines[j].count("{") - lines[j].count("}")
                    if "{" in lines[j]: opened = True
                    if opened and depth <= 0: break
                    j += 1
                out.append((i, j, lines[i].strip()[:100])); i = j
            i += 1
    else:
        for i, l in enumerate(lines):
            if RE_EXT_OBJC.match(l) and "@implementation" in l:
                j = i
                while j < len(lines) and not lines[j].strip().startswith("@end"): j += 1
                out.append((i, j, l.strip()[:100]))
    return out


def collect_consts(lines, into, rel=None, macro_value=None):
    """String constants of one file into `into` (name -> {values}); provenance into const_where_ref.
    `macro_value(macro)` -> (string, source) resolves `@QUOTE(MACRO)` through the build facts."""
    where = const_where_ref[0]
    for i, l in enumerate(lines):
        for rx in RE_CONST:
            for m in rx.finditer(l):
                into[m.group(1)].add(m.group(2))
                where[m.group(1)].setdefault(m.group(2), f"{rel}:{i + 1}" if rel else f"L{i + 1}")
        m = RE_CONST_MACRO.search(l)
        if m and macro_value:
            val, src = macro_value(m.group(2))
            if val is not None:
                into[m.group(1)].add(val)
                where[m.group(1)].setdefault(val, f"{rel}:{i + 1} (build macro {m.group(2)} from {src})")


def const_provenance(name, value):
    return const_where_ref[0].get(name, {}).get(value)


def paren_args(text, start):
    """Text inside the balanced parentheses starting at text[start] == '('."""
    depth = 0
    for k in range(start, len(text)):
        if text[k] == "(": depth += 1
        elif text[k] == ")":
            depth -= 1
            if depth == 0: return text[start + 1:k]
    return text[start + 1:]


# --------------------------------------------------------------------------
# 4. units, manifests, hosts, app facts
# --------------------------------------------------------------------------
def slug(repo): return re.sub(r"[^A-Za-z0-9._-]", "-", repo)


def read_manifest(p):
    try:
        d = plistlib.load(open(p, "rb"))
    except Exception:
        return None
    out = {}
    for e in d.get("NSPrivacyAccessedAPITypes") or []:
        cat = (e.get("NSPrivacyAccessedAPIType") or "").replace("NSPrivacyAccessedAPICategory", "")
        if cat: out[cat] = list(e.get("NSPrivacyAccessedAPITypeReasons") or [])
    return out


def find_manifests(root):
    res = []
    for p in sorted(pathlib.Path(root).rglob("PrivacyInfo.xcprivacy")):
        rel = p.relative_to(root)
        if EXCLUDE_DIR.search(str(rel) + "/"): continue
        res.append({"path": str(rel), "dir": rel.parent, "declares": read_manifest(p)})
    return res


def local_packages(root):
    out = {}
    for p in pathlib.Path(root).rglob("Package.swift"):
        rel = p.parent.relative_to(root)
        if EXCLUDE_DIR.search(str(rel) + "/") or "Pods" in rel.parts: continue
        out[rel] = p.parent.name if rel.parts else pathlib.Path(root).name
    return out


def unit_for_repo_file(rel, pkgs):
    parts = rel.parts
    if "Pods" in parts:
        i = parts.index("Pods")
        return (f"POD:{parts[i+1]}" if len(parts) > i + 1 else "POD:?"), "LOCAL_POD"
    best = None
    for d, name in pkgs.items():
        if rel.is_relative_to(d) and (best is None or len(d.parts) > len(best[0].parts)): best = (d, name)
    if best:
        sub = rel.relative_to(best[0]).parts
        t = f"{best[1]}/{sub[1]}" if len(sub) >= 2 and sub[0] == "Sources" else best[1]
        return t, "LOCAL_PKG"
    return "APP", "APP"


def covering_manifest(rel, manifests, app_level, scope_dir=None, scope_name="UNIT"):
    """(declares, path, scope) for one file.

    Nearest ancestor manifest wins.  `scope_dir` limits the candidates to one
    sub-tree (a vendored pod under Pods/<Name>/, a local package directory).
    APP / LOCAL_PKG files without an ancestor manifest fall back to the
    app-level manifest(s): the app bundle is what ships them.
    """
    cands = [m for m in manifests if scope_dir is None or m["dir"].is_relative_to(scope_dir)]
    anc = [m for m in cands if rel.is_relative_to(m["dir"])]
    if anc:
        m = max(anc, key=lambda m: len(m["dir"].parts))
        return (m["declares"] or {}), m["path"], "NEAREST_ANCESTOR"
    if app_level:
        decl = {}
        for m in app_level:
            for c, rs in (m["declares"] or {}).items(): decl.setdefault(c, set()).update(rs)
        scope = "APP_LEVEL" if len(app_level) == 1 else "APP_LEVEL_UNION"
        if scope_name == "LOCAL_PKG": scope += "_FALLBACK_LOCAL_PKG"
        return {c: sorted(v) for c, v in decl.items()}, ";".join(m["path"] for m in app_level), scope
    return {}, None, (f"NO_COVERING_MANIFEST_IN_{scope_name}" if cands else f"NO_MANIFEST_IN_{scope_name}")


def app_facts(root):
    facts = {"app_group_ids": set(), "has_keyboard_extension": False, "extension_points": set(), "entitlement_files": 0}
    root = pathlib.Path(root)
    for p in root.rglob("*.entitlements"):
        rel = str(p.relative_to(root))
        if EXCLUDE_DIR.search(rel + "/") or "/Pods/" in "/" + rel: continue
        try:
            d = plistlib.load(open(p, "rb"))
        except Exception:
            continue
        facts["entitlement_files"] += 1
        for g in d.get("com.apple.security.application-groups") or []: facts["app_group_ids"].add(str(g))
    for p in root.rglob("Info.plist"):
        rel = str(p.relative_to(root))
        if EXCLUDE_DIR.search(rel + "/") or "/Pods/" in "/" + rel: continue
        try:
            d = plistlib.load(open(p, "rb"))
        except Exception:
            continue
        ext = (d.get("NSExtension") or {}).get("NSExtensionPointIdentifier")
        if ext:
            facts["extension_points"].add(ext)
            if ext == "com.apple.keyboard-service": facts["has_keyboard_extension"] = True
    facts["app_group_ids"] = sorted(facts["app_group_ids"]); facts["extension_points"] = sorted(facts["extension_points"])
    return facts


def lang_of(p):
    return {"swift": "swift", "m": "objc", "mm": "objc", "h": "objc", "c": "c", "cc": "c", "cpp": "c", "hpp": "c"}[p.suffix[1:]]


# --------------------------------------------------------------------------
# 4a. build facts per unit directory (xcodeproj_facts)
# --------------------------------------------------------------------------
EXCLUDE_PROJECTS = re.compile(EXCLUDE_DIR.pattern + "|" + EXCLUDE_DIR_DEP.pattern, re.I)


def build_facts_for(udir, tree):
    """Project / package / podspec facts of one unit directory, or None when nothing is readable."""
    try:
        facts = xf.collect(udir, EXCLUDE_DIR if tree == "repos" else EXCLUDE_PROJECTS)
    except Exception as e:                                             # never let a project file stop the scan
        facts = {"projects": [], "file_targets": {}, "packages": {}, "notes": [f"build facts failed: {e}"], "app_targets": [], "primary_app_target": None}
    if tree == "pods":
        swift, macros = set(), {}
        for spec in sorted(list(udir.glob("*.podspec")) + list(udir.glob("*.podspec.json"))):
            try:
                s, m = xf.podspec_facts(spec); swift |= s; macros.update(m)
            except Exception:
                pass
        facts["podspec"] = {"swift_flags": sorted(swift), "c_macros": macros}
    return facts


def target_facts(facts, rel, ukind, unit, udir_name, pkgs, tree):
    """(target record for the site, flags) for one file.

    flags = {"macros": {NAME: True|False}, "closed": bool, "summary": {...}, "declaring_unit": str}
    `closed` is True when every Swift `#if` identifier the build could define is known
    (the target's Release flag set is complete), so an identifier outside `macros` is false."""
    macros = {}; closed = False; summary = {"source": None}; target = None
    declaring = unit
    if tree == "repos" and ukind == "APP":
        owners = facts["file_targets"].get(rel, []) if facts else []
        if owners:
            names = [t for _, t in owners]
            prim = facts.get("primary_app_target")
            chosen = prim if prim and list(prim) in [list(o) for o in owners] else owners[0]
            rec = xf.target_record(facts, chosen[0], chosen[1]) or {}
            target = {"project": chosen[0], "name": chosen[1], "kind": rec.get("kind"), "manifests": rec.get("manifests", []),
                      "bundle_id": (rec.get("release") or {}).get("settings", {}).get("PRODUCT_BUNDLE_IDENTIFIER"),
                      "entitlements": rec.get("entitlements"), "app_groups": rec.get("app_groups", []),
                      "also_in": [n for n in names if n != chosen[1]]}
            declaring = chosen[1]
            ff = xf.flags_for_file(facts, rel, ukind)
            for f in ff["swift_true"]: macros[f] = True
            for k, v in ff["c_macros"].items(): macros[k] = v.strip() != "0"
            closed = ff["swift_false_closed"]; summary = {"source": ff["source"], "swift_true": sorted(ff["swift_true"]), "closed": closed}
        elif facts and facts["projects"]:
            target = {"project": None, "name": None, "kind": None, "manifests": [],
                      "note": "HEADER_NOT_COMPILED" if rel.endswith((".h", ".hpp")) else "NOT_IN_ANY_XCODE_TARGET"}
    elif ukind == "LOCAL_PKG":
        pkg_dir = max((d for d in pkgs if pathlib.Path(rel).is_relative_to(d)), key=lambda d: len(d.parts), default=None)
        pkg_rel = str(pkg_dir) if pkg_dir is not None and pkg_dir.parts else "."
        tname = unit.split("/")[-1] if "/" in unit else None
        ff = xf.flags_for_file(facts, rel, ukind, pkg_rel, tname) if facts else None
        if ff:
            for f in ff["swift_true"]: macros[f] = True
            for k, v in ff["c_macros"].items(): macros[k] = v.strip() != "0"
            closed = ff["swift_false_closed"]
            summary = {"source": ff["source"], "swift_true": sorted(ff["swift_true"]), "closed": closed}
            if ff["targets"]: tname = ff["targets"][0][1]
        target = {"package": pkg_rel, "name": tname, "kind": "SPM_TARGET", "manifests": []}
        declaring = tname or unit
    elif ukind == "SPM":
        tname = unit.split("/")[-1] if "/" in unit else None
        ff = xf.flags_for_file(facts, rel, ukind, ".", tname) if facts else None
        if ff:
            for f in ff["swift_true"]: macros[f] = True
            for k, v in ff["c_macros"].items(): macros[k] = v.strip() != "0"
            closed = ff["swift_false_closed"]
            summary = {"source": ff["source"], "swift_true": sorted(ff["swift_true"]), "closed": closed}
            if ff["targets"]: tname = ff["targets"][0][1]
        target = {"package": ".", "name": tname, "kind": "SPM_TARGET", "manifests": []}
        declaring = tname or unit
    elif ukind in ("POD", "LOCAL_POD"):
        ps = (facts or {}).get("podspec") or {"swift_flags": [], "c_macros": {}}
        for f in xf.INTEGRATION_FLAGS["POD"] | set(ps["swift_flags"]): macros[f] = True
        for k, v in ps["c_macros"].items(): macros[k] = v.strip() != "0"
        summary = {"source": "podspec", "swift_true": sorted(k for k, v in macros.items() if v), "closed": False}
        name = unit.split(":")[-1] if unit.startswith("POD:") else udir_name.split("@")[0]
        target = {"podspec": name, "name": name, "kind": "POD", "manifests": []}
        declaring = name
    return target, {"macros": macros, "closed": closed, "summary": summary, "declaring_unit": declaring}


def build_facts_summary(facts):
    if not facts: return None
    out = {"projects": [], "packages": {}, "podspec": facts.get("podspec"), "primary_app_target": facts.get("primary_app_target"),
           "notes": list(facts.get("notes", []))}
    for e in facts.get("projects", []):
        out["projects"].append({"path": e["path"], "configurations": e["configurations"], "release_config": e["release_config"],
                                "targets": [{"name": t["name"], "kind": t["kind"], "n_files": t["n_files"], "manifests": t.get("manifests", []),
                                             "entitlements": t.get("entitlements"), "app_groups": t.get("app_groups", []),
                                             "release_swift_flags": (t.get("release") or {}).get("swift_flags"),
                                             "release_c_macros": {k: v for k, v in list(((t.get("release") or {}).get("c_macros") or {}).items())[:16]},
                                             "flags_complete": (t.get("release") or {}).get("complete"),
                                             "bundle_id": ((t.get("release") or {}).get("settings") or {}).get("PRODUCT_BUNDLE_IDENTIFIER")}
                                            for t in e["targets"]], "notes": e.get("notes", [])})
    for rel, pk in facts.get("packages", {}).items():
        out["packages"][rel] = {n: {"kind": v["kind"], "swift": v["swift"], "c": {k: list(x) for k, x in v["c"].items()}} for n, v in pk.items()}
    return out


# --------------------------------------------------------------------------
# 4b. context window, keys, extension-member index
# --------------------------------------------------------------------------
CONTEXT_BEFORE, CONTEXT_AFTER = 12, 36


def function_range(lines, idx, lang):
    """(sig_idx, end_idx) of the function / method / computed property enclosing line idx, or None.
    The body is brace-matched from the signature; a signature without a body (a declaration,
    an unbalanced file) gives None."""
    sig = None
    for i in range(idx, -1, -1):
        l = lines[i]
        if lang == "swift" and RE_FUNC_SWIFT.match(l): sig = i; break
        if lang == "objc" and RE_FUNC_OBJC.match(l): sig = i; break
        if lang in ("c", "objc") and RE_FUNC_C.match(l) and not l.strip().startswith(("if", "for", "while", "switch", "return")): sig = i; break
    if sig is None: return None
    depth = 0; opened = False
    for j in range(sig, min(len(lines), sig + 4000)):
        for ch in lines[j]:
            if ch == "{": depth += 1; opened = True
            elif ch == "}": depth -= 1
        if opened and depth <= 0:
            return (sig, j) if j >= idx else None       # the site is after this body: not inside it
    return None


def context_window(raw_lines, lines, idx, lang):
    """Numbered lines around the site: the enclosing function (signature always kept, body
    capped to CONTEXT_BEFORE / CONTEXT_AFTER around the site) or ±CONTEXT at top level."""
    fr = function_range(lines, idx, lang)
    if fr:
        sig, end = fr
        lo, hi = max(sig, idx - CONTEXT_BEFORE), min(end, idx + CONTEXT_AFTER)
    else:
        sig = None
        lo, hi = max(0, idx - CONTEXT), min(len(raw_lines) - 1, idx + CONTEXT)
    out = []
    if sig is not None and sig < lo:
        out.append(f"{sig + 1:5d}   {raw_lines[sig]}")
        if sig + 1 < lo: out.append("      … ")
    for k in range(lo, hi + 1):
        out.append(f"{k + 1:5d}{'>>' if k == idx else '  '} {raw_lines[k]}")
    if fr and hi < end: out.append(f"      … (function continues to L{end + 1})")
    return {"start_line": lo + 1, "site_line": idx + 1, "function_lines": [sig + 1, end + 1] if fr else None,
            "truncated": bool(fr and (hi < end or lo > sig)), "lines": out}


RE_KEY_SWIFT = re.compile(r"forKey\s*:\s*")
RE_KEY_OBJC = re.compile(r"[a-zA-Z]*[fF]orKey\s*:\s*")


def key_expr(line, start, lang):
    """The key argument expression after the site on this line (`forKey:` / `…ForKey:`), or None."""
    rx = RE_KEY_SWIFT if lang == "swift" else RE_KEY_OBJC
    m = rx.search(line, start)
    if not m: return None
    seg = line[m.end():]
    depth = 0; out = []
    for ch in seg:
        if ch in "([{": depth += 1
        elif ch in ")]}":
            if depth == 0: break
            depth -= 1
        elif ch in ",;" and depth == 0: break
        out.append(ch)
    e = "".join(out).strip()
    return e or None


def resolve_key(expr, consts):
    """{expr, value, source} for a key expression: a literal, a resolvable constant, or unresolved."""
    if expr is None: return None
    m = re.fullmatch(r'@?"([^"\\]*)"', expr)
    if m: return {"expr": expr, "value": m.group(1), "source": "LITERAL"}
    ident = re.fullmatch(r"([A-Za-z_][\w.]*?)(?:\.rawValue)?", expr)
    if ident:
        name = ident.group(1).split(".")[-1]
        vals = consts.get(name)
        if vals and len(vals) == 1:
            v = next(iter(vals)); prov = const_provenance(name, v)
            return {"expr": expr, "value": v, "source": f"CONST:{name}" + (f" @ {prov}" if prov else "")}
        if vals: return {"expr": expr, "value": None, "source": f"CONST:{name} ambiguous {sorted(vals)[:4]}"}
        return {"expr": expr, "value": None, "source": "UNRESOLVED_IDENTIFIER"}
    return {"expr": expr[:80], "value": None, "source": "EXPRESSION"}


RE_MEMBER_VAR_SWIFT = re.compile(rf"^\s*{ATTRS}{MODS}var\s+([\w`]+)\s*:\s*[^{{=]+?\s*\{{")
RE_MEMBER_FUNC_SWIFT = re.compile(rf"^\s*{ATTRS}{MODS}func\s+([\w`]+)\s*(?:<[^>]*>)?\s*\(")
RE_MEMBER_OBJC = re.compile(r"^\s*[-+]\s*\([^)]*\)\s*(\w+)")
RE_SETTER = re.compile(r"^\s*(?:nonmutating\s+)?set\b")


def ud_member_index(per_file, exts_by_file, consts):
    """Every member declared in an extension / category of UserDefaults in the unit directory.

    {name: [{name, kind: VAR|FUNC|OBJC_METHOD, file, line, end_line, has_setter, public, keys, calls}]}
    keys: the `forKey:` arguments the body reads / writes, resolved when possible;
    calls: other indexed members the body refers to (a wrapper calling a wrapper)."""
    index = collections.defaultdict(list)
    bodies = []
    for rel, (lines, lang, _) in per_file.items():
        exts = exts_by_file.get(rel) or []
        if not exts: continue
        depth = brace_depths(lines)
        for a, b, decl in exts:
            ext_public = bool(re.search(r"\b(?:public|open)\b", lines[a]))
            base = depth[a] + (1 if lang == "swift" else 0)
            i = a + 1
            while i < b:
                l = lines[i]
                if depth[i] != base: i += 1; continue
                m = None; kind = None
                if lang == "swift":
                    mv = RE_MEMBER_VAR_SWIFT.match(l)
                    mf = RE_MEMBER_FUNC_SWIFT.match(l)
                    if mv: m, kind = mv, "VAR"
                    elif mf: m, kind = mf, "FUNC"
                else:
                    mo = RE_MEMBER_OBJC.match(l)
                    if mo: m, kind = mo, "OBJC_METHOD"
                if not m: i += 1; continue
                # body: from the first `{` at/after this line to its match
                j = i; d = 0; opened = False; end = i
                while j < min(b + 1, len(lines)):
                    for ch in lines[j]:
                        if ch == "{": d += 1; opened = True
                        elif ch == "}": d -= 1
                    if opened and d <= 0: end = j; break
                    j += 1
                else:
                    end = j - 1
                body = lines[i:end + 1]
                keys = []; seen = set()
                for k, bl in enumerate(body):
                    for km in (RE_KEY_SWIFT if lang == "swift" else RE_KEY_OBJC).finditer(bl):
                        e = key_expr(bl, km.start(), lang)
                        r = resolve_key(e, consts)
                        if r and (r["expr"], r["value"]) not in seen:
                            seen.add((r["expr"], r["value"])); keys.append(dict(r, line=i + k + 1))
                has_setter = kind == "VAR" and any(RE_SETTER.match(bl) for bl in body[1:])
                public = ext_public or bool(re.search(r"\b(?:public|open)\b", l))
                rec = {"name": m.group(1).strip("`"), "kind": kind, "file": rel, "line": i + 1, "end_line": end + 1,
                       "has_setter": has_setter, "public": public, "keys": keys, "calls": []}
                index[rec["name"]].append(rec); bodies.append((rec, body))
                if lang == "swift":
                    # ObjC callers use the selector: `@objc(themeCompatibleWith:)` renames, a property gets `setName:`
                    head = " ".join(lines[max(a, i - 2):i + 1])
                    om = re.search(r"@objc\s*\(\s*(\w+)", head)
                    if om and om.group(1) != rec["name"]:
                        rec["objc_name"] = om.group(1); index[om.group(1)].append(rec)
                    if kind == "VAR" and has_setter:
                        setter = "set" + rec["name"][:1].upper() + rec["name"][1:]
                        if setter not in index: index[setter].append(rec)
                i = end + 1
    names = set(index)
    for rec, body in bodies:
        text = "\n".join(body[1:])
        rec["calls"] = sorted({n for n in names if n != rec["name"] and re.search(rf"(?<![\w.$])(?:self\s*\.\s*)?{re.escape(n)}\b", text)})
    return dict(index)


RE_ASSIGN_AFTER = re.compile(r"^\s*(?:[-+*/]?=)(?!=)")


def link_wrappers(usites, members):
    """Attach `wrapper_ref` to WRAPPED call sites and `callers` to extension-body sites."""
    by_id = {}
    callers_direct = collections.defaultdict(list)
    for s in usites:
        h = s.get("hint") or ""
        name = None
        if h.startswith("POSSIBLE_WRAPPED_MEMBER:"): name = h.split(":", 1)[1]
        elif s.get("operation_prefill") == "WRAPPED?" and s.get("alias_of"): name = s["tag"].split(".")[-1]
        if not name: continue
        defs = members.get(name)
        if not defs:
            s["wrapper_ref"] = None; s["wrapper_note"] = f"no extension member named {name} in this unit directory"
            continue
        line = s["_line_text"]
        pos = line.find(name, s["col"])
        after = line[pos + len(name):] if pos >= 0 else ""
        access = "SET" if RE_ASSIGN_AFTER.match(after) else ("GET" if defs[0]["kind"] == "VAR" else "CALL")
        s["wrapper_ref"] = [{"member": d["name"], "kind": d["kind"], "access": access, "def_file": d["file"], "def_line": d["line"],
                             "def_end_line": d["end_line"], "has_setter": d["has_setter"], "keys": d["keys"], "calls": d["calls"]} for d in defs]
        dom = s.get("domain_hint") or ((s.get("alias_of") or {}).get("domain_hint")) or "UNKNOWN"
        by_id[s["site_id"]] = s
        for canon in {d["name"] for d in defs}:                       # an ObjC selector alias maps to the same member
            callers_direct[canon].append({"site_id": s["site_id"], "file": s["file"], "line": s["line"], "access": access, "domain": dom})
    canonical = {name: defs for name, defs in members.items() if any(d["name"] == name for d in defs)}
    # wrapper -> wrapper edges: callers of M' also reach M when M' calls M
    reached = {}
    def callers_of(name, depth=0, seen=()):
        if name in reached: return reached[name]
        out = list(callers_direct.get(name, [])); via = []
        if depth < 4:
            for other, defs in canonical.items():
                if other == name or other in seen: continue
                if any(name in d["calls"] for d in defs):
                    sub, sub_via = callers_of(other, depth + 1, seen + (name,))
                    if sub: via.append(other); out.extend(sub)
        reached[name] = (out, via)
        return reached[name]
    for s in usites:
        if (s.get("hint") or "") != "EXTENSION_BODY_IMPLICIT_SELF": continue
        rec = None
        for name, defs in canonical.items():
            for d in defs:
                if d["file"] == s["file"] and d["line"] <= s["line"] <= d["end_line"]: rec = d
        if rec is None:
            s["callers"] = None; continue
        lst, via = callers_of(rec["name"])
        uniq = {c["site_id"]: c for c in lst}.values()
        by_dom = collections.Counter(c["domain"] for c in uniq)
        s["member"] = {"name": rec["name"], "kind": rec["kind"], "public": rec["public"], "has_setter": rec["has_setter"]}
        s["callers"] = {"member": rec["name"], "n": len(uniq), "by_domain": dict(by_dom), "via_members": via,
                        "sites": [{k: c[k] for k in ("site_id", "file", "line", "access", "domain")} for c in list(uniq)[:12]],
                        "scope": "all scanned files of this unit directory"}
    for s in usites:
        s.pop("_line_text", None)


# --------------------------------------------------------------------------
# 5. scanning one file
# --------------------------------------------------------------------------
#: member access on an instance expression: `UserDefaults.standard.x`, `UserDefaults(suiteName: g)?.set(…)`, `UserDefaults().x`
RE_STD_MEMBER_SWIFT = re.compile(r"UserDefaults(?:\.standard|\s*\([^()]*(?:\([^()]*\)[^()]*)*\))\s*[?!]?\s*\.\s*(\w+)")
RE_STD_MEMBER_OBJC = re.compile(r"standardUserDefaults\s*(?:\]\s*|\.\s*|\s+)(\w+)")
RE_NESTED_TYPE = re.compile(r"(?:NS)?UserDefaults\.([A-Z]\w*)")
RE_FAMILY_STATIC = re.compile(r"UserDefaults\.(didChangeNotification|sizeLimitExceededNotification|completedInitialCloudSyncNotification|noCloudAccountNotification|didChangeCloudAccountsNotification|argumentDomain|globalDomain|registrationDomain)\b")


def ud_hint(line, s0, e0, tag, lang, ud_ops, cont=""):
    """(hint, operation_prefill, domain_hint) for one UserDefaults-family token on a line.

    Order matters: precise segment shapes first (nested type, family static),
    then declaration/parameter shapes, then member access after `.standard`.
    """
    seg, before = line[s0:], line[:s0]
    consts = consts_ref[0]
    if RE_EXT_SWIFT.match(line) or RE_EXT_OBJC.match(line):
        return "TYPE_EXTENSION_DECLARATION", "NA", None
    if tag == "@AppStorage":
        return "APPSTORAGE_PROPERTY_WRAPPER", "OBSERVE", (None if re.search(r"store\s*:", line) else "APP_PRIVATE")
    nested = RE_NESTED_TYPE.match(seg)
    if nested:
        return f"NESTED_TYPE_REFERENCE:{nested.group(1)}", "NA", None
    fam_static = RE_FAMILY_STATIC.match(seg)
    if fam_static:
        return f"FAMILY_STATIC:{fam_static.group(1)}", ("OBSERVE" if fam_static.group(1).endswith("Notification") else "READ"), None
    type_position = re.match(r"(?:NS)?UserDefaults\s*[?!]?\s*(?:[,)=>\]{]|$)", seg) is not None
    # parameter of init/func:  init(defaults: UserDefaults = .standard) / func f(store: UserDefaults)
    if type_position and re.search(r"\b(?:init|func)\s*[\w`]*\s*\(", before) and re.search(r"\w+\s*:\s*$", before):
        dm = re.match(r"(?:NS)?UserDefaults\s*\??\s*=\s*([^,)]+)", seg)
        d, note = domain_of(dm.group(1) if dm else None, consts)
        return "INJECTION_PARAMETER", "NA", ((d if d != "UNKNOWN" else note) if dm else None)
    # type annotation / property / pointer declaration / cast
    if (type_position and re.search(r"\b(?:let|var)\s+\w+\s*:\s*$|\b(?:as[?!]?|is)\s*$|\w+\s*:\s*$|[<\[(]\s*$|->\s*$", before)) \
            or re.match(r"NSUserDefaults\s*\*", seg) or "@property" in line \
            or re.search(r"\b(?:class|struct|protocol|typealias)\s+\w+\s*(?::|=)\s*$", before) \
            or (type_position and re.search(r"\b(?:let|var)\s+\w+\s*:\s*(?:NS)?UserDefaults\??\s*(?:$|\{)", line)):
        return "TYPE_ANNOTATION_OR_PROPERTY_DECL", "NA", None
    # member access after the instance
    mem = RE_STD_MEMBER_SWIFT.match(seg) if lang == "swift" else RE_STD_MEMBER_OBJC.search(seg[:len(tag) + 100])
    if lang == "objc" and mem is None:
        mem = re.match(r"\[\s*(?:NS)?UserDefaults\s+standardUserDefaults\s*\]\s*(\w+)", line[max(0, s0 - 2):])
    d, note = domain_of(seg + (" " + cont if cont and seg.count("(") > seg.count(")") else ""), consts)
    dom = d if d != "UNKNOWN" else (note or None)
    if mem and mem.group(1) in ud_ops:
        return f"FAMILY_MEMBER:{mem.group(1)}", ud_ops[mem.group(1)], dom
    if mem and mem.group(1) not in ("standard", "standardUserDefaults", "init", "alloc", "self", "shared", "as", "is", "else", "if", "return"):
        return f"POSSIBLE_WRAPPED_MEMBER:{mem.group(1)}", "WRAPPED", dom
    if re.search(r"=\s*(?:NS)?UserDefaults\b|=\s*\[\s*NSUserDefaults\s+(?:standardUserDefaults|alloc)|\(\s*suiteName\s*:|initWithSuiteName\s*:", line):
        return "ACQUIRE_CANDIDATE", "ACQUIRE", dom
    if re.search(r"UserDefaults\.standard\b|standardUserDefaults\b|\(\s*\)", seg[:len(tag) + 24]):
        return "ACQUIRE_CANDIDATE", "ACQUIRE", dom      # instance obtained and passed/stored, no member on this line
    return None, None, dom


_PREFILTER_CACHE = {}


def prefilter_for(pats):
    """One alternation of every literal keyword; a line without any of them has no site."""
    key = id(pats)
    if key not in _PREFILTER_CACHE:
        words = sorted({k for p in pats for k in p[6]}, key=len, reverse=True)
        _PREFILTER_CACHE[key] = re.compile("|".join(re.escape(w) for w in words))
    return _PREFILTER_CACHE[key]


def scan_file(path, rel, lang, pats):
    """Return (sites, aliases, lines).  Sites lack unit-level fields; main() adds them."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    text = strip_comments(raw)
    lines = text.splitlines()
    raw_lines = raw.splitlines()
    guards = guard_chains(lines)
    aliases = find_aliases(lines, lang) if lang in ("swift", "objc") else {}
    exts = extension_ranges(lines, lang) if lang in ("swift", "objc") else []
    sites = []

    def emit(i, col, end, site_class, api, cats, tier, tag, alias=None, hint=None, op=None, dom=None):
        chain, live = guards[i]
        decl_line = bool(RE_EXT_SWIFT.match(lines[i]) or RE_EXT_OBJC.match(lines[i]))
        key = None
        if api == "user_defaults_family" and op in ("READ", "WRITE", "REMOVE") and not decl_line:
            key = resolve_key(key_expr(lines[i], end, lang), consts_ref[0])
        sites.append({
            "site_class": site_class, "api": api, "category": cats[0], "categories": list(cats), "tier": tier, "tag": tag,
            "file": rel, "line": i + 1, "col": col, "language": lang,
            "compile_guard": chain or None, "guard_live_on_ios": live,
            "in_string_literal": in_string(lines[i], col),
            "enclosing_function": None if decl_line else enclosing_function(lines, i, lang),
            "alias_of": alias, "instance_domains": None, "domain_hint": dom, "hint": hint, "operation_prefill": op,
            "key": key,
            "context": context_window(raw_lines, lines, i, lang),
            "_line_text": lines[i],
        })

    ud_ops = UD_OPS_SWIFT if lang == "swift" else UD_OPS_OBJC
    prefilter = prefilter_for(pats)
    alias_rx = {}
    for name in aliases:
        alias_rx[name] = (re.compile(rf"(?<![\w.$])(?:self\.)?{re.escape(name)}\s*[?!]?\s*\.\s*(\w+)") if lang == "swift"
                          else re.compile(rf"\[\s*(?:self\.|self->|_)?{re.escape(name)}\s+(\w+)"))
    alias_decl_rx = {name: re.compile(rf"\s*(?:{ATTRS})?(?:{MODS})?(let|var)\s+{re.escape(name)}\b|.*\b{re.escape(name)}\s*:\s*(?:NS)?UserDefaults") for name in aliases}
    ext_rx = (re.compile(rf"(?<![\w.])(?:self\.)?({UD_MEMBERS_SWIFT})\s*\(") if lang == "swift"
              else re.compile(rf"\[\s*self\s+({UD_MEMBERS_OBJC})"))
    alias_names = list(aliases)
    for i, line in enumerate(lines):
        if not (prefilter.search(line) or (alias_names and any(n in line for n in alias_names)) or (exts and any(a < i <= b for a, b, _ in exts))):
            continue
        # (a) direct pattern hits, overlapping spans for the same api merged
        hits = collections.defaultdict(list)
        for site_class, key, cats, tier, tag, rx, kws in pats:
            if not any(k in line for k in kws): continue
            for m in rx.finditer(line):
                hits[key].append((m.start(), m.end(), site_class, cats, tier, tag))
        for key, spans in hits.items():
            spans.sort(); merged = []
            for s0, e0, sc, cats, tier, tag in spans:
                if merged and s0 < merged[-1][1]:
                    mm = merged[-1]
                    merged[-1] = (mm[0], max(mm[1], e0), sc, cats, "STRONG" if "STRONG" in (mm[4], tier) else "WEAK",
                                  tag if len(tag) > len(mm[5]) else mm[5])
                else:
                    merged.append((s0, e0, sc, cats, tier, tag))
            for s0, e0, sc, cats, tier, tag in merged:
                hint = op = dom = None
                if sc == "RRA" and key == "user_defaults_family":
                    cont = " ".join(x.strip() for x in lines[i + 1:i + 6]) if line.count("(") > line.count(")") else ""
                    hint, op, dom = ud_hint(line, s0, e0, tag, lang, ud_ops, cont)
                elif sc == "RRA" and key in c_function_keys_ref[0]:
                    hint, op = c_function_hint(line, s0, e0, tag.lstrip("="), lang)
                    if tag.startswith("="):
                        s0 = max(s0, line.find(tag[1:], s0))        # point the site at the symbol, not at the `=`
                elif sc == "RRA" and "FileTimestamp" in cats:
                    op = "WRITE" if re.search(r"\bsetAttributes\s*\(|\bsetResourceValues\s*\(|\bsetAttributes:|\bsetResourceValues:|\bsetResourceValue:", line) else "READ"
                elif sc == "RRA" or sc == "ALT":
                    op = "READ"
                emit(i, s0, e0, sc, key, cats, tier, tag, hint=hint, op=op, dom=dom)
        # (b) alias member calls (UserDefaults family)
        for name, decls in aliases.items():
            if name not in line: continue
            for m in alias_rx[name].finditer(line):
                if alias_decl_rx[name].match(line): continue
                if "user_defaults_family" in hits and any(s0 <= m.start() < e0 for s0, e0, *_ in hits["user_defaults_family"]): continue
                mem = m.group(1)
                if lang == "swift" and mem in ("standard", "init"): continue
                prev = [d for d in decls if d["decl_line"] <= i + 1 <= d["scope_end"]]
                if not prev: continue                       # no declaration of that name is in scope here
                d = prev[-1]
                if d["form"] == "CLOSURE_PARAMETER" and d["decl_line"] == i + 1 and m.start() < line.find("{"):
                    continue                                # the receiver before `{` is the wrapper, not the parameter
                op = ud_ops.get(mem, "WRAPPED?")
                emit(i, m.start(), m.end(), "RRA", "user_defaults_family", ("UserDefaults",), "STRONG",
                     f"{name}.{mem}", alias={"alias": name, "decl_line": d["decl_line"], "decl": d["decl"], "form": d["form"],
                                             "owner_type": d["type"], "callable": d.get("callable"), "label": d.get("label"),
                                             "domain_hint": "PENDING"},
                     hint="ALIAS_MEMBER_CALL", op=op)
        # (c) extension-body implicit self
        for (a, b, decl) in exts:
            if a < i <= b:
                for m in ext_rx.finditer(line):
                    emit(i, m.start(), m.end(), "RRA", "user_defaults_family", ("UserDefaults",), "STRONG",
                         f"self.{m.group(1)}", alias={"alias": "self", "decl_line": a + 1, "decl": decl, "form": "EXTENSION_BODY",
                                                      "owner_type": "UserDefaults", "domain_hint": "SELF_INSTANCE"},
                         hint="EXTENSION_BODY_IMPLICIT_SELF", op=ud_ops.get(m.group(1)), dom="SELF_INSTANCE")
    return sites, aliases, lines, exts


#: api keys whose kind is c_function (stat, statfs, mach_absolute_time, …); set by main from the rules.
c_function_keys_ref = [set()]
#: module qualifiers under which `X.stat(` is still the C function
C_MODULES = {"Darwin", "Glibc", "Musl", "Foundation", "System", "Bionic", "WASILibc", "CoreFoundation", "MachO", "Dispatch"}


def c_function_hint(line, s0, e0, sym, lang):
    """(hint, operation_prefill) for a C-function candidate.  swift-nio's b0001 pilot batch had 40
    candidates for the stat family of which 31 were the C struct `stat`, a Swift method *named* stat
    or a declaration of one; these shapes are mechanical, so they are prefilled (still sites -- the
    annotator confirms).  Real calls keep hint None / READ."""
    pos = line.find(sym, s0)                       # the "=stat" pattern starts at the `=` / `:` before the symbol
    if pos < 0: pos = s0
    before, seg = line[:pos], line[pos:]
    if re.search(r"\bfunc\s+$", before) or re.search(rf"\bfunc\s+{re.escape(sym)}\s*\(", line[max(0, pos - 40):e0 + 1]):
        return "FUNCTION_DECLARATION", "NA"
    if re.match(rf"{re.escape(sym)}\s*\(\s*\)", seg):
        return "ZERO_ARG_INIT_OR_CALL", "NA"           # `var s = stat()` is the C struct's zero initializer
    m = re.search(r"(\w+)\s*\.\s*$", before)
    if m:
        return ("QUALIFIED_C_FUNCTION:" + m.group(1), "READ") if m.group(1) in C_MODULES else ("QUALIFIED_MEMBER_CALL:" + m.group(1), "NA")
    if re.search(r"[(\[,:]\s*\.\s*$|=\s*\.\s*$|^\s*\.\s*$|\breturn\s+\.\s*$", before):
        return "IMPLICIT_MEMBER_CALL", "NA"          # `.failure(.stat(name, errno: …))`
    if re.match(rf"{re.escape(sym)}\s*[?!]?\s*[,;)\]]|{re.escape(sym)}\s*$", seg) and not re.match(rf"{re.escape(sym)}\s*\(", seg):
        return "FUNCTION_REFERENCE", "READ"          # `private let sysStat = stat`
    if lang == "swift" and re.search(r":\s*$|<\s*$|\bas[?!]?\s*$", before) and not re.match(rf"{re.escape(sym)}\s*\(", seg):
        return "TYPE_REFERENCE", "NA"                # `UnsafeMutablePointer<stat>`, `var sb: stat`
    return None, "READ"


#: consts of the unit being scanned (set by main before scanning its files);
#: a 1-element list so scan_file can read it without a global statement.
consts_ref = [collections.defaultdict(set)]


def find_calls(per_file, callee_rx, label, default, consts, require_label, skip_decl_rx, owner=None):
    """Every call matching callee_rx across the unit, with the domain passed for `label`.

    A call without the label counts as the parameter's default when there is
    one; with no default it is skipped when require_label (a func name is a
    weak key -- another overload is more likely than an omitted argument),
    otherwise recorded as UNKNOWN.  callee_rx group 1, when present, is the
    qualifier before the name (`Other.name(`): a qualifier that is neither the
    owner type nor self/Self is a different function with the same name.
    """
    found = []
    default_dom = domain_of(default, consts) if default else None
    for f2, (l2, lang2, aliases2) in per_file.items():
        if lang2 != "swift": continue
        for j, ln in enumerate(l2):
            for m in callee_rx.finditer(ln):
                if skip_decl_rx.match(ln): continue
                q = m.group(1) if m.re.groups else None
                if owner and q and q not in (owner, "Self", "self", "super"): continue
                args = paren_args(ln, m.end() - 1)
                if args == ln[m.end():]:                      # unbalanced on this line: join up to 5 more lines
                    args = paren_args(" ".join(l2[j:j + 6]), m.end() - 1)
                am = re.search(rf"(?<![\w.]){re.escape(label)}\s*:\s*(.+)", args) if label != "_" else None
                if am:
                    expr = am.group(1).strip()
                    dom, note = domain_of(expr, consts)
                    ident = re.match(r"([A-Za-z_]\w*)\s*(?:[,)]|$)", expr)
                    if dom == "UNKNOWN" and ident:            # bare variable: resolve through that file's aliases
                        cands = [dd for dd in aliases2.get(ident.group(1), []) if dd["decl_line"] <= j + 1 and dd.get("init")]
                        if cands:
                            dom, note2 = domain_of(cands[-1]["init"], consts)
                            note = f"arg {label}: {ident.group(1)} <- L{cands[-1]['decl_line']} {cands[-1]['init'].strip()[:60]}" + (f" ({note2})" if note2 else "")
                    found.append({"loc": f"{f2}:{j + 1}", "domain": dom, "note": note or f"arg {label}: {expr[:60]}", "form": "CALL"})
                elif default_dom:
                    found.append({"loc": f"{f2}:{j + 1}", "domain": default_dom[0], "note": f"default {label} = {default.strip()[:40]}", "form": "CALL_DEFAULT"})
                elif label == "_":
                    found.append({"loc": f"{f2}:{j + 1}", "domain": "UNKNOWN", "note": "positional parameter, argument not resolved", "form": "CALL"})
                elif not require_label:
                    found.append({"loc": f"{f2}:{j + 1}", "domain": "UNKNOWN", "note": f"call without {label}:", "form": "CALL"})
    return found


def resolve_instances(usites, per_file, consts):
    """Fill alias_of.domain_hint and instance_domains for alias sites.

    LOCAL_OR_PROPERTY     the declaration the site resolved to (nearest in scope).
    INJECTED_PROPERTY     every construction of the owning type in the unit
                          (`Type(` / `Type.init(`), through the init parameter
                          of that type, or its default when omitted.
    INIT_PARAMETER        same, keyed by the parameter's external label.
    FUNC_PARAMETER        every call of that function in the unit (`name(`),
                          keyed by the external label; the default when omitted.
    PARAMETER             callable not located: UNKNOWN.
    """
    cache = {}
    decl_rx = re.compile(rf"\s*{ATTRS}{MODS}(class|struct|actor|enum|extension|protocol|func|init)\b")
    for s in usites:
        a = s.get("alias_of")
        if not a or a.get("form") == "EXTENSION_BODY": continue
        aliases = per_file[s["file"]][2]
        # only the declaration the site resolved to (nearest in scope); other same-named declarations in the file are different bindings
        decls = [d for d in aliases.get(a["alias"], []) if d["decl_line"] == a["decl_line"]]
        inst = []
        for d in decls:
            here = f"{s['file']}:{d['decl_line']}"
            if d["form"] == "LOCAL_OR_PROPERTY":
                dom, note = domain_of(d["init"], consts)
                inst.append({"loc": here, "domain": dom, "note": note, "form": d["form"]})
                continue
            t = d.get("type")
            if d["form"] == "INJECTED_PROPERTY":
                # the init parameter that feeds this property (same type); its label and default
                params = [dd for e in aliases.values() for dd in e if dd["form"] == "INIT_PARAMETER" and dd.get("type") == t]
                if not t:
                    inst.append({"loc": here, "domain": "UNKNOWN", "note": "INJECTED, owner type not found", "form": d["form"]}); continue
                if not params:
                    inst.append({"loc": here, "domain": "UNKNOWN", "note": f"INJECTED, no init parameter of type UserDefaults in {t}", "form": d["form"]}); continue
                pd = params[0]
                key = ("INIT", t, pd["label"])
                if key not in cache:
                    cache[key] = find_calls(per_file, re.compile(rf"(?<![\w.]){re.escape(t)}(?:\.init)?\s*\("), pd["label"], pd.get("init"), consts, False, decl_rx)
                inst.extend(cache[key] or [{"loc": here, "domain": "UNKNOWN", "note": f"INJECTED via {t}({pd['label']}:), no construction found in unit", "form": d["form"]}])
            elif d["form"] == "INIT_PARAMETER":
                if not t:
                    inst.append({"loc": here, "domain": "UNKNOWN", "note": "init parameter, owner type not found", "form": d["form"]}); continue
                key = ("INIT", t, d["label"])
                if key not in cache:
                    cache[key] = find_calls(per_file, re.compile(rf"(?<![\w.]){re.escape(t)}(?:\.init)?\s*\("), d["label"], d.get("init"), consts, False, decl_rx)
                inst.extend(cache[key] or [{"loc": here, "domain": "UNKNOWN", "note": f"init parameter of {t}({d['label']}:), no construction found in unit", "form": d["form"]}])
            elif d["form"] == "FUNC_PARAMETER":
                fn = d["callable"]
                key = ("FUNC", fn, d["label"])
                if key not in cache:
                    cache[key] = find_calls(per_file, re.compile(rf"(?:(\w+)\s*\.\s*)?(?<![\w]){re.escape(fn)}\s*\("), d["label"], d.get("init"), consts, d.get("init") is None,
                                            re.compile(rf"\s*{ATTRS}{MODS}func\s+{re.escape(fn)}\s*\("), owner=t)
                inst.extend(cache[key] or [{"loc": here, "domain": "UNKNOWN", "note": f"parameter of func {fn}({d['label']}:), no call found in unit", "form": d["form"]}])
            elif d["form"] == "CLOSURE_PARAMETER":
                inst.append({"loc": here, "domain": "UNKNOWN", "note": f"closure parameter of wrapper {d['callable']}, receiver `{d.get('receiver')}`; domain is the wrapper's (§4.8)", "form": d["form"]})
            else:
                inst.append({"loc": here, "domain": "UNKNOWN", "note": "parameter, enclosing callable not located", "form": d["form"]})
        # de-dup by (loc, domain), keep order; cap the list, keep the domain set complete
        seen = set(); uniq = []
        for x in inst:
            k = (x["loc"], x["domain"])
            if k in seen: continue
            seen.add(k); uniq.append(x)
        doms = sorted({x["domain"] for x in uniq})
        if len(uniq) > 12:
            uniq = uniq[:12] + [{"loc": None, "domain": None, "note": f"+{len(uniq) - 12} more", "form": "TRUNCATED"}]
        s["instance_domains"] = uniq or None
        a["domain_hint"] = doms[0] if len(doms) == 1 else ("MIXED_DOMAINS:" + "|".join(doms) if doms else "UNKNOWN")
        if s.get("domain_hint") is None: s["domain_hint"] = a["domain_hint"]


# --------------------------------------------------------------------------
# 6. driver
# --------------------------------------------------------------------------
def rules_provenance(rules_path):
    rp = pathlib.Path(rules_path).resolve(); data = rp.read_bytes()
    prov = {"rules_path": str(rp), "rules_sha256": hashlib.sha256(data).hexdigest(), "analyzer_version": "ANALYZER_VERSION_UNKNOWN"}
    py = rp.parent / "pyproject.toml"
    if py.is_file():
        m = re.search(r'^version\s*=\s*"([^"]+)"', py.read_text(encoding="utf-8"), re.M)
        if m: prov["analyzer_version"] = m.group(1)
    return prov


def constraints_for(rules, reasons):
    out = {}
    for r in reasons:
        rr = rules["reasons"].get(r)
        if not rr: continue
        out[r] = {"title": rr.get("title"), "restrictions": rr.get("apple_policy_restrictions"),
                  "constraints": [{k: c.get(k) for k in ("id", "type", "predicate", "support_evidence", "conflict_evidence", "unknown_evidence")}
                                  for c in rr.get("constraints", [])]}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--src", required=True, help="含 repos/ deps/ pods/ 的目录（fetch_sources 的 --out）")
    ap.add_argument("--out-dir", required=True, help="工作表输出目录")
    ap.add_argument("--only", default=None, help="只扫这些单元目录名（逗号分隔），调试用")
    a = ap.parse_args(argv)

    prov = rules_provenance(a.rules)
    print(f"分析器 {prov['analyzer_version']} · rules sha256 {prov['rules_sha256'][:12]}")
    rules = yaml.safe_load(open(a.rules, encoding="utf-8"))
    c_function_keys_ref[0] = {k for k, v in rules["apis"].items() if v.get("kind") == "c_function"}
    pats = rra_patterns(rules) + [("ALT", k, (c,), "STRONG", t, rx, ALT_KEYWORDS[k]) for k, c, tier, t, rx in ALT_PATTERNS]
    alt_tier = {k: tier for k, c, tier, t, rx in ALT_PATTERNS}
    src = pathlib.Path(a.src); out = pathlib.Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)

    # optional: fetch/pod reports for host mapping & fork detection
    report = {}; pods_report = {}
    if (src / "fetch_report.json").is_file(): report = json.loads((src / "fetch_report.json").read_text(encoding="utf-8"))
    if (src / "pods" / "pods_report.json").is_file(): pods_report = json.loads((src / "pods" / "pods_report.json").read_text(encoding="utf-8")).get("pods", {})
    repo_sha = {r: rec.get("sha") for r, rec in report.get("repos", {}).items()}
    repo_of_slug = {slug(r): r for r in report.get("repos", {})}

    # host (app-level) manifests and app facts per repo
    host_decl = {}; host_facts = {}
    for rdir in sorted((src / "repos").glob("*")) if (src / "repos").is_dir() else []:
        if not rdir.is_dir(): continue
        pkgs = local_packages(rdir)
        decl = {}
        for m in find_manifests(rdir):
            rel = pathlib.Path(m["path"])
            if "Pods" in rel.parts or any(rel.is_relative_to(d) for d in pkgs if d.parts): continue
            for cat, rs in (m["declares"] or {}).items(): decl.setdefault(cat, set()).update(rs)
        host_decl[rdir.name] = {c: sorted(v) for c, v in decl.items()}
        host_facts[rdir.name] = app_facts(rdir)
    hosts_of = collections.defaultdict(set)
    for r, rec in report.get("repos", {}).items():
        for pin in rec.get("pins", []): hosts_of[("deps", pin)].add(slug(r))
        for p in rec.get("pods", []): hosts_of[("pods", f"{p['name']}@{p['version']}")].add(slug(r))

    units_out = {}; totals = collections.Counter(); matrix = collections.Counter(); n_sites = 0; n_files = 0; n_zero = 0
    only = set(a.only.split(",")) if a.only else None
    for tree in ("repos", "deps", "pods"):
        base = src / tree
        if not base.is_dir(): continue
        for udir in sorted(base.iterdir()):
            if not udir.is_dir() or (only and udir.name not in only): continue
            manifests = find_manifests(udir)
            pkgs = local_packages(udir) if tree == "repos" else {}
            app_level = [m for m in manifests if tree == "repos" and "Pods" not in pathlib.Path(m["path"]).parts
                         and not any(pathlib.Path(m["path"]).is_relative_to(d) for d in pkgs if d.parts)]
            git_url = None
            if tree == "deps": git_url = (report.get("deps", {}).get(udir.name) or {}).get("url")
            if tree == "pods": git_url = (pods_report.get(udir.name) or {}).get("git")
            consts = collections.defaultdict(set); consts_ref[0] = consts
            const_where_ref[0] = collections.defaultdict(dict)
            per_file = {}; usites = []; exts_by_file = {}
            files = []; excluded = collections.Counter()
            for f in sorted(udir.rglob("*")):
                if not f.is_file() or f.suffix not in SRC_EXT: continue
                rel_s = str(f.relative_to(udir)) + "/"
                m = EXCLUDE_DIR.search(rel_s) or (EXCLUDE_DIR_DEP.search(rel_s) if tree != "repos" else None)
                if m: excluded[m.group(2)] += 1; continue
                files.append(f)
            # pass 0a: build facts (targets, Release flags, build-setting macros)
            facts = build_facts_for(udir, tree)
            # pass 0b: project macros (guard evaluation) and string constants (domain_of)
            unit_macros = collect_unit_macros(files)
            wrappers = collect_wrappers(files); wrappers_ref[0] = wrappers
            for f in files:
                rel = str(f.relative_to(udir))
                try:
                    collect_consts(strip_comments(f.read_text(encoding="utf-8", errors="replace")).splitlines(), consts, rel,
                                   macro_value=lambda macro, rel=rel: xf.macro_string_value(facts, rel, macro, tree) if facts else (None, None))
                except Exception:
                    pass
            for f in files:
                rel = f.relative_to(udir); n_files += 1
                lang = lang_of(f)
                th = OTHER_TARGET_HINT.search(str(rel)) if tree == "repos" else None
                target_hint = f"PATH_SUGGESTS_OTHER_TARGET:{th.group(2)}" if th else None
                if tree == "repos":
                    unit, ukind = unit_for_repo_file(rel, pkgs)
                    role = "THIRD_PARTY" if ukind == "LOCAL_POD" else ("VENDORED_THIRD_PARTY?" if VENDOR_HINT.search(str(rel)) else "FIRST_PARTY")
                else:
                    ident = udir.name.split("@")[0]
                    unit = f"{ident}/{rel.parts[1]}" if len(rel.parts) >= 2 and rel.parts[0] == "Sources" else ident
                    ukind = "SPM" if tree == "deps" else "POD"
                    role = "THIRD_PARTY"
                    if git_url:
                        owner = re.sub(r"^https?://github\.com/", "", git_url).split("/")[0].lower()
                        hs = hosts_of.get((tree, udir.name), set())
                        if any(h.lower().startswith(owner + "-") for h in hs): role = "FORK_OF_THIRD_PARTY"
                target, flags = target_facts(facts, str(rel), ukind, unit, udir.name, pkgs, tree)
                # guard evaluation for this file: unit macros + what the build defines for its target
                unit_macros_ref[0] = dict(unit_macros, **flags["macros"])
                closed_flags_ref[0] = lang == "swift" and flags["closed"]
                sites, aliases, lines, exts = scan_file(f, str(rel), lang, pats)
                unit_macros_ref[0] = unit_macros; closed_flags_ref[0] = False
                per_file[str(rel)] = (lines, lang, aliases); exts_by_file[str(rel)] = exts
                if target and target.get("manifests"):
                    tm = target["manifests"][0]
                    decl = next((m["declares"] or {} for m in manifests if m["path"] == tm), {})
                    mpath, scope = tm, "TARGET_RESOURCE"
                elif ukind == "LOCAL_POD":
                    i0 = rel.parts.index("Pods"); scope_dir = pathlib.Path(*rel.parts[:i0 + 2])
                    decl, mpath, scope = covering_manifest(rel, manifests, [], scope_dir, "POD")
                elif ukind == "LOCAL_PKG":
                    pkg_dir = max((d for d in pkgs if rel.is_relative_to(d)), key=lambda d: len(d.parts))
                    decl, mpath, scope = covering_manifest(rel, manifests, app_level, pkg_dir, "LOCAL_PKG")
                elif ukind == "APP":
                    decl, mpath, scope = covering_manifest(rel, manifests, app_level, None, "APP")
                else:
                    decl, mpath, scope = covering_manifest(rel, manifests, [], None, ukind)
                if target and target.get("kind") in ("APP_EXTENSION", "WATCH_APP", "WATCH_EXTENSION", "APP_CLIP") and not target.get("manifests") and mpath:
                    scope += "_FALLBACK_EXTENSION_TARGET"       # the extension bundle has no manifest of its own; the app's is what covers it, if anything
                for s in sites:
                    s["alt_tier"] = alt_tier.get(s["api"]) if s["site_class"] == "ALT" else None
                    s["unit"] = unit; s["unit_kind"] = ukind; s["unit_role_prefill"] = role; s["target_hint"] = target_hint
                    s["target"] = target; s["declaring_unit_prefill"] = flags["declaring_unit"]
                    s["build_flags"] = flags["summary"]
                    s["unit_manifest"] = mpath; s["manifest_scope"] = scope
                    s["declared_reasons"] = {c: decl.get(c, []) for c in s["categories"]} if s["site_class"] == "RRA" else None
                    s["declared_for_mapped_category"] = any(decl.get(c) for c in s["categories"]) if s["site_class"] == "ALT" else None
                    s["reason_constraints"] = {c: constraints_for(rules, decl.get(c, [])) for c in s["categories"]} if s["site_class"] == "RRA" else None
                    usites.append(s)
            loc = f"{tree}/{udir.name}"
            build_summary = build_facts_summary(facts)
            if not usites:
                # recorded, not skipped: "scanned N files, 0 sites" is an observation
                units_out[loc] = {"sites": 0, "files_scanned": len(files), "files_excluded": dict(excluded),
                                  "manifests": [m["path"] for m in manifests], "unit_macros": unit_macros,
                                  "wrapper_methods": {k: sorted(v) for k, v in wrappers.items()}, "build_facts": build_summary,
                                  "hosts": sorted(hosts_of.get((tree, udir.name), set())) if tree != "repos" else [udir.name]}
                n_zero += 1
                continue
            resolve_instances(usites, per_file, consts)
            members = ud_member_index(per_file, exts_by_file, consts)
            unit_decl = {}
            for m in manifests:
                for cat, rs in (m["declares"] or {}).items(): unit_decl.setdefault(cat, set()).update(rs)
            unit_decl = {c: sorted(v) for c, v in unit_decl.items()}
            hs = sorted(hosts_of.get((tree, udir.name), set())) if tree != "repos" else [udir.name]
            for s in usites:
                s["unit_location"] = loc
                s["unit_manifests_all"] = [m["path"] for m in manifests]
                s["repo"] = repo_of_slug.get(udir.name, udir.name) if tree == "repos" else None
                s["hosts"] = [{"repo": repo_of_slug.get(h, h), "declares": {c: host_decl.get(h, {}).get(c, []) for c in s["categories"]},
                               "app_facts": host_facts.get(h)} for h in hs]
                s["sha"] = (repo_sha.get(repo_of_slug.get(udir.name)) if tree == "repos"
                            else (report.get("deps", {}).get(udir.name) or {}).get("revision") or udir.name.split("@")[-1])
                s["site_id"] = hashlib.sha1(f"{loc}|{s['file']}|{s['line']}|{s['col']}|{s['api']}".encode()).hexdigest()[:16]
                totals[(s["site_class"], s["category"], s["tier"])] += 1
                matrix[(s["category"], s["unit_kind"], s["site_class"])] += 1
            ids = [s["site_id"] for s in usites]
            assert len(ids) == len(set(ids)), f"site_id 冲突: {udir.name}"
            link_wrappers(usites, members)
            with io.open(out / f"{tree}__{udir.name}.jsonl", "w", encoding="utf-8") as fh:
                for s in usites: fh.write(json.dumps(s, ensure_ascii=False) + "\n")
            by = collections.Counter((s["site_class"], s["tier"]) for s in usites)
            dead = sum(1 for s in usites if s["guard_live_on_ios"] is False)
            alias_n = sum(1 for s in usites if s["alias_of"])
            units_out[loc] = {"sites": len(usites), "files_scanned": len(files), "files_excluded": dict(excluded),
                              "by": {f"{k[0]}/{k[1]}": v for k, v in by.items()},
                              "by_category": dict(collections.Counter(s["category"] for s in usites)),
                              "by_unit": dict(collections.Counter(s["unit"] for s in usites)),
                              "guard_dead": dead, "alias_sites": alias_n, "manifests": [m["path"] for m in manifests],
                              "unit_macros": unit_macros, "wrapper_methods": {k: sorted(v) for k, v in wrappers.items()},
                              "ud_members": {k: [{kk: d[kk] for kk in ("kind", "file", "line", "end_line", "has_setter", "public", "keys", "calls")} for d in v] for k, v in members.items()},
                              "build_facts": build_summary,
                              "declares": unit_decl, "hosts": hs,
                              "role_prefill": collections.Counter(s["unit_role_prefill"] for s in usites).most_common(1)[0][0],
                              "app_facts": host_facts.get(udir.name) if tree == "repos" else None}
            n_sites += len(usites)
            print(f"  {loc:52s} 站点 {len(usites):5d}  RRA {by[('RRA','STRONG')]+by[('RRA','WEAK')]:4d} (WEAK {by[('RRA','WEAK')]:3d})  "
                  f"ALT {by[('ALT','STRONG')]:3d}  别名 {alias_n:3d}  守卫排除 {dead:3d}  清单 {len(manifests)}", flush=True)

    print(f"\n合计 {n_sites} 个站点，{len(units_out)} 个单元目录（其中 {n_zero} 个零站点），扫描 {n_files} 个源文件")
    print(f"{'类别':22s}{'RRA STRONG':>11s}{'RRA WEAK':>10s}{'ALT':>6s}")
    for cat in sorted({c for _, c, _ in totals}):
        print(f"  {cat:20s}{totals[('RRA',cat,'STRONG')]:>11,}{totals[('RRA',cat,'WEAK')]:>10,}{totals[('ALT',cat,'STRONG')]:>6,}")
    kinds = ["APP", "LOCAL_PKG", "LOCAL_POD", "SPM", "POD"]
    print("\n站点矩阵（类别 × 单元类型；RRA/ALT）")
    print(f"  {'类别':18s}" + "".join(f"{k:>16s}" for k in kinds))
    for cat in sorted({c for c, _, _ in matrix}):
        print(f"  {cat:18s}" + "".join(f"{matrix[(cat,k,'RRA')]:>10,}/{matrix[(cat,k,'ALT')]:<5,}" for k in kinds))
    with io.open(out / "_units.json", "w", encoding="utf-8") as fh:
        json.dump({"provenance": prov, "n_sites": n_sites, "n_files": n_files, "units": units_out,
                   "matrix": [{"category": c, "unit_kind": k, "site_class": sc, "sites": v} for (c, k, sc), v in sorted(matrix.items())],
                   "patterns": [{"site_class": sc, "api": k, "categories": list(c), "tier": t, "tag": g} for sc, k, c, t, g, _, _ in pats]},
                  fh, ensure_ascii=False, indent=1)
    print(f"工作表：{out}/<tree>__<unit>.jsonl；单元汇总 {out}/_units.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
