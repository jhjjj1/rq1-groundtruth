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
import pathlib
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


def show_build_settings(container_flag, container_path, name, configuration,
                        destination, timeout, selector="-scheme"):
    """Run ``xcodebuild -showBuildSettings -json`` for one scheme or target.

    The container is passed as **flag + path, as two argv elements**.  The old
    version took one string and `shlex.split` it, which silently broke every
    project whose path contains a space -- measured in batch01 on
    ``./Little Go.xcworkspace``, ``./3. iOS app/DMT.xcworkspace`` and
    ``./draggable slider/draggable slider.xcodeproj``: bash split the injected
    string and xcodebuild got a path that does not exist.

    ``selector`` is ``-scheme`` normally, or ``-target`` for the fallback used
    when a project has no shared schemes (schemes live in ``xcuserdata`` and
    are frequently not committed).

    Returns ``(targets, error)``.  Never raises -- a name that cannot be
    queried is a fact to record, not a reason to abort the sweep.
    """
    cmd = [
        "xcodebuild", "-showBuildSettings", "-json",
        container_flag, container_path,
        selector, name,
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


#: 非 iOS 平台的痕迹。`-destination generic/platform=iOS` 打在 tvOS/watchOS/
#: macOS 工程上时，xcodebuild 会把它**能接受**的平台列出来 —— batch01 里
#: yichengchen/ATV-Bilibili-demo 报的是 `{ platform:tvOS Simulator, ... }`。
OTHER_PLATFORMS = ("tvOS", "watchOS", "macOS", "visionOS", "DriverKit")


def platform_mismatch(failures):
    """所有探测都失败，且失败信息里只提到别的平台。"""
    text = " ".join(f.get("error") or "" for f in failures)
    return any(p in text for p in OTHER_PLATFORMS) and "iOS " not in text


def read_head(path, n=800):
    if not path:
        return None
    try:
        return pathlib.Path(path).read_text(encoding="utf-8",
                                            errors="replace")[:n]
    except OSError:
        return None


def write_result(path, result):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)


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
    # 不带横杠传。argparse 会把以 `-` 开头的值当成选项名而拒绝消费，
    # `--container-flag -project` 直接报 "expected one argument" —— 这条是
    # 本地测试逮到的，否则会在 CI 上原样炸掉每一个 job。
    ap.add_argument("--container-flag", required=True,
                    choices=("project", "workspace"))
    ap.add_argument("--container-path", required=True,
                    help="工程/工作区路径。单独一个参数，因为路径可能带空格")
    ap.add_argument("--list-rc", default=None,
                    help="`xcodebuild -list -json` 的退出码，原样记账")
    ap.add_argument("--list-stderr-file", default=None,
                    help="`xcodebuild -list -json` 的 stderr 文件")
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

    # `-list -json` 失败时会写出空文件或半截输出。上一版用裸 json.load 去读，
    # 一坏就抛异常、什么都不写 —— batch01 里 12 个仓库因此连 manifest 的
    # scheme_verdict 都是空的，「判不了」变成了「没记录」。现在它是一个判定。
    container_flag = "-" + args.container_flag
    raw, load_error = "", None
    try:
        raw = pathlib.Path(args.schemes_json).read_text(encoding="utf-8",
                                                        errors="replace")
        listing = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        listing, load_error = {}, f"{type(exc).__name__}: {exc}"

    root = listing.get("workspace") or listing.get("project") or {}
    schemes = list(root.get("schemes") or [])
    targets_listed = list(root.get("targets") or [])
    container_kind = ("workspace" if "workspace" in listing
                      else "project" if "project" in listing else None)

    if load_error is not None:
        result = {
            "container_flag": container_flag,
            "container_path": args.container_path,
            "chosen": None,
            "chosen_verdict": "SCHEMES_JSON_UNREADABLE",
            "load_error": load_error,
            "schemes_json_head": raw[:600],
            "list_rc": args.list_rc,
            "list_stderr": read_head(args.list_stderr_file),
            "schemes_listed": 0, "schemes_probed": 0,
            "app_schemes": [], "app_rule_fired": [], "ambiguous": False,
            "rows": [], "probe_failures": [],
            "rule_disagreements": [], "rule_disagreement_count": 0,
            "kind_counts": {}, "ld_map_defaults": [],
            "total_elapsed_s": 0.0,
        }
        write_result(args.out, result)
        print(f"verdict  = SCHEMES_JSON_UNREADABLE  ({load_error})")
        print(f"xcodebuild -list rc = {args.list_rc}")
        print((result["list_stderr"] or "")[:400])
        return 78

    # Pods-* are CocoaPods' own aggregate schemes; they are never the app and
    # probing them costs real seconds on big projects.
    skipped_by_prefix = [s for s in schemes if s.startswith("Pods")]
    candidates = [s for s in schemes if not s.startswith("Pods")]

    # 没有共享 scheme 时退到 target。
    #
    # scheme 默认存在 `xcuserdata` 里、不进版本库，用 XcodeGen/Tuist 生成工程的
    # 仓库尤其如此 —— batch01 里 bitwarden/ios 与 AdguardTeam/AdguardForiOS 的
    # `-list -json` 都返回了工程但 schemes 为空。这不是「没有 app」，是「没有
    # scheme」，而 `-target` 不需要 scheme 就能查、能编。
    #
    # `-target` 只对 `-project` 有效；workspace 没有 targets 这一层，所以那种
    # 情形只能如实记成 NO_SHARED_SCHEMES。
    selector = "-scheme"
    if not candidates:
        if targets_listed and container_flag == "-project":
            selector = "-target"
            candidates = [t for t in targets_listed if not t.startswith("Pods")]
            skipped_by_prefix += [t for t in targets_listed if t.startswith("Pods")]
        else:
            result = {
                "container_flag": container_flag,
                "container_path": args.container_path,
                "container_kind": container_kind,
                "chosen": None,
                "chosen_verdict": "NO_SHARED_SCHEMES",
                "schemes_listed": 0, "targets_listed": len(targets_listed),
                "schemes_probed": 0, "probe_mode": selector,
                "list_rc": args.list_rc,
                "list_stderr": read_head(args.list_stderr_file),
                "app_schemes": [], "app_rule_fired": [], "ambiguous": False,
                "rows": [], "probe_failures": [],
                "rule_disagreements": [], "rule_disagreement_count": 0,
                "kind_counts": {}, "ld_map_defaults": [],
                "total_elapsed_s": 0.0,
            }
            write_result(args.out, result)
            print(f"verdict  = NO_SHARED_SCHEMES  "
                  f"（容器是 {container_kind}，scheme 0 个，target "
                  f"{len(targets_listed)} 个）")
            return 78

    if args.max_schemes:
        candidates = candidates[: args.max_schemes]

    started = time.monotonic()
    rows, failures = [], []
    for scheme in candidates:
        t0 = time.monotonic()
        targets, error = show_build_settings(
            container_flag, args.container_path, scheme,
            args.configuration, args.destination, args.timeout, selector
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
        "container_flag": container_flag,
        "container_path": args.container_path,
        "container_kind": container_kind,
        "probe_mode": selector,
        "list_rc": args.list_rc,
        "list_stderr": read_head(args.list_stderr_file),
        "targets_listed": len(targets_listed),
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
            # 一个都没探成功、且失败原因里提到别的平台 —— 这是平台不符，
            # 不是「没观测到 app scheme」。后者暗示探测失败，前者是事实。
            else "PLATFORM_MISMATCH" if (not rows and failures
                                         and platform_mismatch(failures))
            else "NO_APP_SCHEME_OBSERVED"
        ),
        "rule_disagreements": disagree,
        "rule_disagreement_count": len(disagree),
        "ld_map_defaults": ld_defaults,
        "rows": rows,
        "total_elapsed_s": round(time.monotonic() - started, 1),
    }

    write_result(args.out, result)

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
