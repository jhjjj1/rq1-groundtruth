#!/usr/bin/env python3
"""Decide how to ask for a linker map, from what xcodebuild says it resolves to.

The failure this exists to prevent
----------------------------------
P1 run #1 passed ``LD_MAP_FILE_PATH=<one absolute path>`` on the xcodebuild
command line.  A command-line build setting is a *global* override: every
target's Ld task inherited the same output path.  IceCubesApp links ~25 SPM
targets, so XCBuild saw 21 duplicate outputs and refused the build during
planning -- 15 seconds, before a single file was compiled::

    warning: duplicate output file '/Users/runner/work/_temp/out/link.map'
             on task: Ld .../AppAccount.o normal (in target 'AppAccount')
    ** BUILD FAILED **

Any project with more than one linking target hits this, which is every real
app.  So the map path must resolve to a *distinct* value per target.

Three candidate modes, and the rule
-----------------------------------
``default``     LD_GENERATE_MAP_FILE=YES only; whatever Xcode defaults
                LD_MAP_FILE_PATH to.
``per_target``  LD_MAP_FILE_PATH='$(TARGET_TEMP_DIR)/<name>' -- relies on
                build-setting references being expanded per target.
``global``      one absolute path for the whole build (what run #1 did).

A mode is USABLE iff, across the targets that will generate a map, every
resolved path is distinct **and** no path still contains a literal ``$(``
(an unexpanded reference would become a directory named ``$(TARGET_TEMP_DIR)``
and collide right back).

Whether ``$(...)`` expands in a command-line setting is not asserted here --
it is read off the ``-showBuildSettings`` dump the caller supplies.  That is
the whole point: the decision is made from the toolchain's own answer on the
runner that will do the building, not from what the mode is supposed to do.
"""

from __future__ import annotations

import argparse
import json
import sys

#: Preference order among usable modes.  `per_target` first because we choose
#: the filename and can therefore collect maps by a known pattern; `default`
#: second because Xcode's own path is stable but version-dependent; `global`
#: last because it is only ever usable on a single-link-target project.
PREFERENCE = ("per_target", "default", "global")


def load_targets(path):
    """Read one `xcodebuild -showBuildSettings -json` dump.

    Returns a list of (target, LD_GENERATE_MAP_FILE, LD_MAP_FILE_PATH).
    Tolerates leading warnings before the JSON array.
    """
    text = open(path, encoding="utf-8", errors="replace").read()
    start = text.find("[")
    if start < 0:
        return None, "no JSON array in dump"
    try:
        blocks = json.loads(text[start:])
    except json.JSONDecodeError as exc:
        return None, f"JSON parse failed: {exc}"
    rows = []
    for block in blocks:
        s = block.get("buildSettings") or {}
        rows.append((
            block.get("target"),
            s.get("LD_GENERATE_MAP_FILE"),
            s.get("LD_MAP_FILE_PATH"),
        ))
    return rows, None


def judge(rows):
    """Classify one mode from its resolved per-target paths."""
    if rows is None:
        return {"verdict": "NO_DATA"}

    generating = [(t, p) for (t, g, p) in rows if (g or "").upper() == "YES"]
    paths = [p for (_t, p) in generating]

    unexpanded = sorted({p for p in paths if p and "$(" in p})
    missing = [t for (t, p) in generating if not p]

    seen, dupes = {}, {}
    for t, p in generating:
        if not p:
            continue
        seen.setdefault(p, []).append(t)
    for p, targets in seen.items():
        if len(targets) > 1:
            dupes[p] = sorted(targets)

    if not generating:
        verdict = "NO_TARGET_GENERATES_MAP"
    elif missing:
        verdict = "PATH_UNSET_ON_SOME_TARGETS"
    elif unexpanded:
        verdict = "UNEXPANDED_REFERENCE"
    elif dupes:
        verdict = "DUPLICATE_PATHS"
    else:
        verdict = "USABLE"

    return {
        "verdict": verdict,
        "targets_total": len(rows),
        "targets_generating_map": len(generating),
        "distinct_paths": len(seen),
        "duplicate_paths": dupes,
        "unexpanded_paths": unexpanded,
        "targets_with_unset_path": sorted(missing),
        "sample_paths": paths[:5],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--settings", nargs="+", required=True, metavar="MODE=FILE",
                    help="e.g. default=bs_default.json per_target=bs_per.json")
    ap.add_argument("--github-output", default=None,
                    help="append map_mode=... to this file (usually $GITHUB_OUTPUT)")
    ap.add_argument("--out", "--json", dest="out", required=True)
    args = ap.parse_args(argv)

    modes = {}
    for spec in args.settings:
        if "=" not in spec:
            ap.error(f"--settings 需要 MODE=FILE，收到 {spec!r}")
        mode, path = spec.split("=", 1)
        rows, error = load_targets(path)
        modes[mode] = judge(rows)
        modes[mode]["source"] = path
        if error:
            modes[mode]["load_error"] = error

    usable = [m for m in PREFERENCE if modes.get(m, {}).get("verdict") == "USABLE"]
    # Anything not in PREFERENCE still counts if it is usable -- the caller
    # may add modes later, and silently ignoring them would hide a fact.
    usable += [m for m in modes if m not in PREFERENCE
               and modes[m]["verdict"] == "USABLE"]
    chosen = usable[0] if usable else None

    result = {
        "modes": modes,
        "usable_modes": usable,
        "chosen_mode": chosen,
        # No usable mode is a hard stop, not something to paper over: a build
        # without a per-target map path either fails at planning or silently
        # produces one map for the wrong target.
        "verdict": "MAP_MODE_CHOSEN" if chosen else "NO_USABLE_MAP_MODE",
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    width = max((len(m) for m in modes), default=6)
    print(f"{'mode':<{width}}  {'verdict':<26}  {'gen':>4}  {'distinct':>8}")
    print("-" * (width + 46))
    for mode in list(PREFERENCE) + [m for m in modes if m not in PREFERENCE]:
        j = modes.get(mode)
        if not j:
            continue
        print(f"{mode:<{width}}  {j['verdict']:<26}  "
              f"{j.get('targets_generating_map', 0):>4}  {j.get('distinct_paths', 0):>8}")
        for p in j.get("sample_paths", [])[:2]:
            print(f"{'':<{width}}    e.g. {p}")
        for p, ts in list(j.get("duplicate_paths", {}).items())[:1]:
            print(f"{'':<{width}}    冲突 {p}  <- {len(ts)} 个 target")
    print()
    print(f"verdict = {result['verdict']}")
    print(f"chosen  = {chosen}")

    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as fh:
            fh.write(f"map_mode={chosen or ''}\n")
            fh.write(f"map_verdict={result['verdict']}\n")

    return 0 if chosen else 78


if __name__ == "__main__":
    sys.exit(main())
