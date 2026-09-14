#!/usr/bin/env python3
"""Fixture test reproducing run #1's duplicate-output failure.

The target names are the 21 that run #1's log actually named in its
`duplicate output file` warnings, so the `global` case here is the real one.
"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import decide_map_mode as D

#: verbatim from run #1 `7_Build with linker map` warnings
TARGETS = [
    "AppAccount", "MediaUI", "DesignSystem", "Gifu", "Models", "StatusKit",
    "NetworkClient", "Nuke", "WrappingHStack", "ButtonKit", "cmark-gfm",
    "NukeUI", "KeychainSwift", "CAtomic", "LRUCache", "Markdown", "SwiftSoup",
    "cmark-gfm-extensions", "TelemetryDeck", "EmojiText", "Env",
]
GLOBAL_PATH = "/Users/runner/work/_temp/out/link.map"


def dump(td, name, rows):
    p = pathlib.Path(td) / name
    p.write_text(json.dumps([
        {"action": "build", "target": t,
         "buildSettings": {k: v for k, v in
                           (("LD_GENERATE_MAP_FILE", g), ("LD_MAP_FILE_PATH", path))
                           if v is not None}}
        for (t, g, path) in rows
    ]))
    return str(p)


def main():
    fails = []
    with tempfile.TemporaryDirectory() as td:
        files = [
            # Xcode 自带的默认路径：本来就带 target 名，互不相同
            "default=" + dump(td, "d.json", [
                (t, "YES", f"/dd/{t}.build/{t}-LinkMap-normal-arm64.txt") for t in TARGETS]),
            # $(TARGET_TEMP_DIR) 被展开的情形
            "per_target=" + dump(td, "p.json", [
                (t, "YES", f"/dd/{t}.build/rra_link.map") for t in TARGETS]),
            # run #1 真实发生的：全部指向同一个绝对路径
            "global=" + dump(td, "g.json", [
                (t, "YES", GLOBAL_PATH) for t in TARGETS]),
            # $(...) 没被展开的情形
            "unexpanded=" + dump(td, "u.json", [
                (t, "YES", "$(TARGET_TEMP_DIR)/rra_link.map") for t in TARGETS]),
        ]
        out = str(pathlib.Path(td) / "out.json")
        rc = D.main(["--settings", *files, "--out", out])
        res = json.loads(open(out).read())

    m = res["modes"]
    if m["global"]["verdict"] != "DUPLICATE_PATHS":
        fails.append(f"global verdict={m['global']['verdict']}, 期望 DUPLICATE_PATHS")
    if m["global"]["duplicate_paths"].get(GLOBAL_PATH) is None:
        fails.append("global 没把冲突路径记下来")
    elif len(m["global"]["duplicate_paths"][GLOBAL_PATH]) != 21:
        fails.append(f"冲突 target 数={len(m['global']['duplicate_paths'][GLOBAL_PATH])}, 期望 21")
    if m["unexpanded"]["verdict"] != "UNEXPANDED_REFERENCE":
        fails.append(f"unexpanded verdict={m['unexpanded']['verdict']}")
    if m["per_target"]["verdict"] != "USABLE":
        fails.append(f"per_target verdict={m['per_target']['verdict']}")
    if m["default"]["verdict"] != "USABLE":
        fails.append(f"default verdict={m['default']['verdict']}")
    if res["chosen_mode"] != "per_target":
        fails.append(f"chosen={res['chosen_mode']}, 期望 per_target（偏好序第一个 USABLE）")
    if rc != 0:
        fails.append(f"rc={rc}")

    # 全都不可用时必须是硬停，不能挑一个凑合
    with tempfile.TemporaryDirectory() as td:
        f = ["global=" + dump(td, "g.json", [(t, "YES", GLOBAL_PATH) for t in TARGETS])]
        out = str(pathlib.Path(td) / "o.json")
        rc2 = D.main(["--settings", *f, "--out", out])
        res2 = json.loads(open(out).read())
    if res2["verdict"] != "NO_USABLE_MAP_MODE" or rc2 != 78:
        fails.append(f"全不可用时 verdict={res2['verdict']} rc={rc2}, 期望 NO_USABLE_MAP_MODE/78")

    if fails:
        print("FAIL")
        for f_ in fails:
            print("  -", f_)
        return 1
    print("PASS  4 种模式判定 + 全不可用硬停")
    print("      run #1 的 global 模式被判为 DUPLICATE_PATHS，"
          "21 个 target 撞同一路径 —— 与日志里的 21 条 warning 对上")
    return 0


if __name__ == "__main__":
    sys.exit(main())
