#!/usr/bin/env python3
"""Decide which Xcode scheme builds the app, from PRODUCT_TYPE -- never from the name.

Why not pick by name
--------------------
The first P1 run was killed by a name guess. ``xcodebuild -list -json`` returns
schemes in alphabetical order; the code took ``schemes[0]``.  For IceCubesApp
that is ``Account`` -- an SPM library scheme whose product is ``Account.o``, not
a ``.app``.  The build therefore never intended to produce an app bundle, and
the downstream ``find -name '*.app'`` came back empty.  The name carried no
information; the build settings do.

The only admissible evidence is what xcodebuild itself reports:

    PRODUCT_TYPE == com.apple.product-type.application

When PRODUCT_TYPE is absent from the output the script falls back to
WRAPPER_EXTENSION == "app", and records which rule actually fired, so that
"determined" and "guessed" never look alike in the result file.

Ambiguity (more than one app scheme) is recorded, not silently resolved: the
caller decides whether to take the first or to drop the repo from the cohort.

This script also harvests the *default* values of LD_GENERATE_MAP_FILE and
LD_MAP_FILE_PATH per target, because those decide whether a linker map can be
requested globally at all (a single global path collides across targets and
XCBuild refuses the whole build at planning time).
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time

#: PRODUCT_TYPE values.  Only the first one is an app main binary; the rest are
#: recorded so the result file shows what else was in the project rather than
#: lumping everything into "other".
PT_APP = "com.apple.product-type.application"
PT_APPEX = "com.apple.product-type.app-extension"
PT_WATCHAPP = "com.apple.product-type.application.watchapp2"

#: 所有「扩展」类产品。一个 scheme 只要自己的产物是这些之一，它就不是 app
#: scheme —— 哪怕它的构建图里带着宿主 app。
APPEX_TYPES = frozenset({
    PT_APPEX,
    "com.apple.product-type.extensionkit-extension",
    "com.apple.product-type.app-extension.messages",
    "com.apple.product-type.app-extension.messages-sticker-pack",
    "com.apple.product-type.watchkit2-extension",
    "com.apple.product-type.xpc-service",
})

KIND_BY_TYPE = {PT_APP: "APP", PT_WATCHAPP: "WATCHAPP"}
KIND_BY_TYPE.update({t: "APPEX" for t in APPEX_TYPES})

#: Settings worth carrying out of the probe.  LD_* are the ground-truth levers;
#: TARGET_TEMP_DIR is what a per-target map path would expand to.
HARVEST = (
    "PRODUCT_TYPE",
    "WRAPPER_EXTENSION",
    "FULL_PRODUCT_NAME",
    "MACH_O_TYPE",
    "EXECUTABLE_NAME",
    "LD_GENERATE_MAP_FILE",
    "LD_MAP_FILE_PATH",
    "TARGET_TEMP_DIR",
    "SWIFT_COMPILATION_MODE",
    "DEAD_CODE_STRIPPING",
    "STRIP_STYLE",
)


def show_build_settings(container, scheme, configuration, destination, timeout):
    """Run ``xcodebuild -showBuildSettings -json`` for one scheme.

    Returns ``(targets, error)``.  ``targets`` is a list of
    ``{"target": str, "settings": {...}}``; ``error`` is None on success.
    Never raises -- a scheme that cannot be queried is a fact to record, not a
    reason to abort the sweep.
    """
    cmd = [
        "xcodebuild", "-showBuildSettings", "-json",
        *shlex.split(container),
        "-scheme", scheme,
        "-configuration", configuration,
        "-destination", destination,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return [], f"TIMEOUT after {timeout}s"
    except OSError as exc:                                   # xcodebuild missing
        return [], f"OSERROR {exc}"

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return [], f"rc={proc.returncode} " + " | ".join(tail[-3:])

    # xcodebuild prepends warnings before the JSON array on some projects.
    text = proc.stdout
    start = text.find("[")
    if start < 0:
        return [], "no JSON array in stdout"
    try:
        blocks = json.loads(text[start:])
    except json.JSONDecodeError as exc:
        return [], f"JSON parse failed: {exc}"

    targets = []
    for block in blocks:
        settings = block.get("buildSettings") or {}
        targets.append({
            "target": block.get("target"),
            "settings": {k: settings[k] for k in HARVEST if k in settings},
        })
    return targets, None


def classify(targets):
    """Classify one scheme from its targets' settings.

    Why the *first* target and not the set
    --------------------------------------
    The first version asked "does this scheme's target set contain an
    application target?".  Measured on IceCubesApp (probe run #1)::

        IceCubesApp              1 target : [application]
        IceCubesActionExtension  2 targets: [app-extension, application]
        IceCubesShareExtension   2 targets: [app-extension, application]

    An extension scheme drags its host app into the build graph, so the
    set-wise test says APP for all three, and the alphabetically-first of
    those -- `IceCubesActionExtension` -- got built.  No `.app` was ever
    produced.

    `-showBuildSettings -json` emits the scheme's build-action entries in
    order, and the first entry is the scheme's own product.  That is the
    primary rule here.

    It is a rule about xcodebuild's output ordering, which this project has
    observed on exactly one project, so it ships with its own falsifier: the
    set-wise verdict is computed too and `agrees_with_setwise` records whether
    they matched.  Disagreements across the corpus are the signal that the
    ordering assumption does not hold somewhere; silence is not.
    """
    if not targets:
        return {"kind": "NO_TARGETS", "rule": "NONE"}

    types = [t["settings"].get("PRODUCT_TYPE") for t in targets]
    known = [t for t in types if t]

    if known:
        primary = known[0]
        has_app = PT_APP in known or PT_WATCHAPP in known
        has_appex = any(t in APPEX_TYPES for t in known)
        kind = KIND_BY_TYPE.get(primary, "OTHER")
        setwise = ("APPEX" if has_appex else
                   "APP" if has_app else
                   KIND_BY_TYPE.get(primary, "OTHER"))
        return {
            "kind": kind, "rule": "PRIMARY_TARGET",
            "primary_product_type": primary,
            "has_app_target": has_app, "has_appex_target": has_appex,
            "setwise_kind": setwise,
            "agrees_with_setwise": kind == setwise,
        }

    # PRODUCT_TYPE absent -> weaker evidence, and say so.
    wrappers = [t["settings"].get("WRAPPER_EXTENSION") for t in targets]
    first = next((w for w in wrappers if w), None)
    kind = {"app": "APP", "appex": "APPEX"}.get(first, "OTHER")
    return {"kind": kind, "rule": "WRAPPER_EXTENSION",
            "primary_product_type": None,
            "has_app_target": "app" in wrappers,
            "has_appex_target": "appex" in wrappers,
            "setwise_kind": kind, "agrees_with_setwise": True}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--container", required=True,
                    help="e.g. '-project X.xcodeproj' or '-workspace X.xcworkspace'")
    ap.add_argument("--schemes-json", required=True,
                    help="output of `xcodebuild <container> -list -json`")
    ap.add_argument("--configuration", default="Release")
    ap.add_argument("--destination", default="generic/platform=iOS")
    ap.add_argument("--timeout", type=float, default=180.0,
                    help="per-scheme xcodebuild timeout, seconds")
    ap.add_argument("--max-schemes", type=int, default=0,
                    help="0 = no cap; probe at most N schemes (cost control)")
    ap.add_argument("--out", "--json", dest="out", required=True,
                    help="where to write the result JSON")
    args = ap.parse_args(argv)

    listing = json.load(open(args.schemes_json, encoding="utf-8"))
    root = listing.get("workspace") or listing.get("project") or {}
    schemes = list(root.get("schemes") or [])

    # Pods-* are CocoaPods' own aggregate schemes; they are never the app and
    # probing them costs real seconds on big projects.
    skipped_by_prefix = [s for s in schemes if s.startswith("Pods")]
    candidates = [s for s in schemes if not s.startswith("Pods")]
    if args.max_schemes:
        candidates = candidates[: args.max_schemes]

    started = time.monotonic()
    rows, failures = [], []
    for scheme in candidates:
        t0 = time.monotonic()
        targets, error = show_build_settings(
            args.container, scheme, args.configuration, args.destination, args.timeout
        )
        elapsed = round(time.monotonic() - t0, 1)
        if error is not None:
            failures.append({"scheme": scheme, "error": error, "elapsed_s": elapsed})
            continue
        verdict = classify(targets)
        rows.append({"scheme": scheme, "elapsed_s": elapsed,
                     "targets": targets, **verdict})

    apps = [r["scheme"] for r in rows if r["kind"] == "APP"]
    rules = sorted({r["rule"] for r in rows if r["kind"] == "APP"})
    # 证伪项：主判据（第一个 target）与次判据（集合里有 app 无 appex）不一致的
    # scheme。全语料上这个数若不为零，说明 -showBuildSettings 的顺序在某些工程
    # 上不等于 scheme 顺序，主判据就得换。
    disagree = [{"scheme": r["scheme"], "primary": r["kind"],
                 "setwise": r.get("setwise_kind"),
                 "primary_product_type": r.get("primary_product_type")}
                for r in rows if r.get("agrees_with_setwise") is False]

    # The default LD_* values, read off whichever targets reported them.  This
    # is the measurement that decides whether a global LD_MAP_FILE_PATH is
    # usable at all.
    ld_defaults = []
    for row in rows:
        for t in row["targets"]:
            s = t["settings"]
            if "LD_MAP_FILE_PATH" in s or "LD_GENERATE_MAP_FILE" in s:
                ld_defaults.append({
                    "scheme": row["scheme"], "target": t["target"],
                    "LD_GENERATE_MAP_FILE": s.get("LD_GENERATE_MAP_FILE"),
                    "LD_MAP_FILE_PATH": s.get("LD_MAP_FILE_PATH"),
                    "TARGET_TEMP_DIR": s.get("TARGET_TEMP_DIR"),
                })

    result = {
        "container": args.container,
        "configuration": args.configuration,
        "destination": args.destination,
        "schemes_listed": len(schemes),
        "schemes_skipped_pods_prefix": skipped_by_prefix,
        "schemes_probed": len(candidates),
        "probe_failures": failures,
        "kind_counts": {
            k: sum(1 for r in rows if r["kind"] == k)
            for k in sorted({r["kind"] for r in rows})
        },
        "app_schemes": apps,
        "app_rule_fired": rules,
        "ambiguous": len(apps) > 1,
        "chosen": apps[0] if apps else None,
        # `chosen` being None is not "no app" -- it is "no app scheme observed
        # under this container/destination".  The caller must not read it as
        # evidence that the repo has no app.
        "chosen_verdict": (
            "APP_SCHEME_FOUND" if len(apps) == 1
            else "APP_SCHEME_AMBIGUOUS" if len(apps) > 1
            else "NO_APP_SCHEME_OBSERVED"
        ),
        "rule_disagreements": disagree,
        "rule_disagreement_count": len(disagree),
        "ld_map_defaults": ld_defaults,
        "rows": rows,
        "total_elapsed_s": round(time.monotonic() - started, 1),
    }

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    width = max((len(r["scheme"]) for r in rows), default=6)
    print(f"{'scheme':<{width}}  {'kind':<9}  {'rule':<18}  elapsed")
    print("-" * (width + 42))
    for r in sorted(rows, key=lambda r: (r["kind"] != "APP", r["scheme"])):
        print(f"{r['scheme']:<{width}}  {r['kind']:<9}  {r['rule']:<18}  {r['elapsed_s']:>5.1f}s")
    for f in failures:
        print(f"{f['scheme']:<{width}}  {'PROBE_FAIL':<9}  {f['error'][:40]:<18}  {f['elapsed_s']:>5.1f}s")
    print()
    print(f"verdict  = {result['chosen_verdict']}")
    print(f"chosen   = {result['chosen']}")
    print(f"app_rule = {rules or '-'}")
    print(f"主判据与次判据分歧 = {len(disagree)}"
          + (f"  {disagree}" if disagree else ""))
    print(f"probed {len(candidates)} schemes in {result['total_elapsed_s']}s")

    # Exit 78 (neutral) only when nothing was observed; the caller turns that
    # into a recorded SKIP rather than a crash.
    return 0 if apps else 78


if __name__ == "__main__":
    sys.exit(main())
