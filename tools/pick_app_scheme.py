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
    """Return (kind, rule) for one scheme from its targets' settings.

    ``rule`` names the evidence that decided it, so the result file can be
    audited without re-running xcodebuild.
    """
    types = {t["settings"].get("PRODUCT_TYPE") for t in targets}
    types.discard(None)
    if types:
        if PT_APP in types:
            return "APP", "PRODUCT_TYPE"
        if PT_WATCHAPP in types:
            return "WATCHAPP", "PRODUCT_TYPE"
        if PT_APPEX in types:
            return "APPEX", "PRODUCT_TYPE"
        return "OTHER", "PRODUCT_TYPE"

    # PRODUCT_TYPE absent -> weaker evidence, and say so.
    wrappers = {t["settings"].get("WRAPPER_EXTENSION") for t in targets}
    if "app" in wrappers:
        return "APP", "WRAPPER_EXTENSION"
    if "appex" in wrappers:
        return "APPEX", "WRAPPER_EXTENSION"
    if not targets:
        return "NO_TARGETS", "NONE"
    return "OTHER", "WRAPPER_EXTENSION"


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
        kind, rule = classify(targets)
        rows.append({
            "scheme": scheme, "kind": kind, "rule": rule,
            "elapsed_s": elapsed, "targets": targets,
        })

    apps = [r["scheme"] for r in rows if r["kind"] == "APP"]
    rules = sorted({r["rule"] for r in rows if r["kind"] == "APP"})

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
    print(f"probed {len(candidates)} schemes in {result['total_elapsed_s']}s")

    # Exit 78 (neutral) only when nothing was observed; the caller turns that
    # into a recorded SKIP rather than a crash.
    return 0 if apps else 78


if __name__ == "__main__":
    sys.exit(main())
