#!/usr/bin/env python3
"""Fixture is verbatim from a real map, not hand-written.

Every line below was copied out of
`IceCubesActionExtension.build/rra_link.map`, produced by Xcode 26.6 /
ld_prime on GitHub's macos-latest during probe run #1 (only the long
/Users/runner/... prefixes were kept intact so the path rules see what they
will really see).  Writing a plausible-looking map by hand would test the
parser against my idea of the format instead of the format.
"""
import io
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import parse_link_map as P

R = "/Users/runner/work/_temp/dd"
X = "/Applications/Xcode_26.6.app/Contents/Developer"

MAP = "\n".join([
    f"# Path: {R}/Build/Products/Release-iphoneos/IceCubesActionExtension.appex/IceCubesActionExtension",
    "# Arch: arm64",
    "# Object files:",
    "[  0] linker synthesized",
    f"[  1] {R}/Build/Intermediates.noindex/IceCubesApp.build/Release-iphoneos/IceCubesActionExtension.build/Objects-normal/arm64/ActionRequestHandler.o",
    f"[  2] {R}/Build/Products/Release-iphoneos/NetworkClient.o",
    f"[  3] {X}/Platforms/iPhoneOS.platform/Developer/SDKs/iPhoneOS26.5.sdk/System/Library/Frameworks/Foundation.framework/Foundation.tbd",
    f"[  4] {X}/Toolchains/XcodeDefault.xctoolchain/usr/lib/swift/iphoneos/libswiftCompatibility51.a(Overrides.cpp.o)",
    "[  5] /usr/lib/system/libdispatch.dylib",
    "[  6] /System/Library/PrivateFrameworks/UIKitCore.framework/UIKitCore",
    "[  7] /somewhere/totally/unexpected.bin",
    "# Sections:",
    "# Address\tSize    \tSegment\tSection",
    "0x100004000\t0x00000100\t__TEXT\t__text",
    "0x100004100\t0x00000010\t__TEXT\t__stubs",
    "# Symbols:",
    "# Address\tSize    \tFile  Name",
    "0x100000000\t0x00000000\t[  0] __mh_execute_header",
    "0x100004000\t0x00000040\t[  1] _handler",
    "0x100004040\t0x00000040\t[  2] _$s13NetworkClientAAV3fooyyF",
    "0x100004040\t0x00000000\t[  0] _alias_at_same_address",
    "0x100004080\t0x00000040\t[  4] _swift_compat_shim",
    "# Dead Stripped Symbols:",
    "#        \tSize    \tFile  Name",
    "<<dead>>\t0x0000003C\t[  1] _$sxIeAgHr_xs5Error_pIegHrzo_Tg5TA",
    "<<dead>>\t0x00000010\t[  2] _unused_helper",
    "",
])


def main():
    fails = []
    lm = P.parse(io.StringIO(MAP))

    if lm.parse_errors:
        fails.append(f"解析失败 {len(lm.parse_errors)} 行: {lm.parse_errors[:3]}")
    if lm.arch != "arm64":
        fails.append(f"arch={lm.arch}")
    if not (lm.path or "").endswith("IceCubesActionExtension"):
        fails.append(f"path={lm.path}")

    want = {
        0: ("LINKER_SYNTHESIZED", None,             "INDEX_ZERO"),
        1: ("TARGET_OBJECT",  "IceCubesActionExtension", "TARGET_INTERMEDIATE"),
        2: ("MERGED_PRODUCT", "NetworkClient",      "BUILD_PRODUCT_OBJECT"),
        3: ("DYLIB_STUB",     "Foundation",         "TBD"),
        4: ("STATIC_ARCHIVE", "libswiftCompatibility51", "ARCHIVE_MEMBER"),
        5: ("DYLIB",          "libdispatch",        "DYLIB"),
        6: ("FRAMEWORK",      "UIKitCore",          "FRAMEWORK_BINARY"),
        7: ("UNKNOWN",        None,                 "NO_RULE_MATCHED"),
    }
    for i, (kind, unit, rule) in want.items():
        o = lm.objects.get(i)
        if not o:
            fails.append(f"[{i}] 没解析出来"); continue
        if (o["kind"], o["unit"], o["rule"]) != (kind, unit, rule):
            fails.append(f"[{i}] 得到 {(o['kind'], o['unit'], o['rule'])}，期望 {(kind, unit, rule)}")

    # 归属：地址落在谁的区间里
    for addr, unit in ((0x100004000, "IceCubesActionExtension"),
                       (0x10000403F, "IceCubesActionExtension"),
                       (0x100004040, "NetworkClient"),
                       (0x100004080, "libswiftCompatibility51")):
        got = lm.unit_at(addr)
        if (got or {}).get("unit") != unit:
            fails.append(f"unit_at(0x{addr:x}) = {(got or {}).get('unit')}，期望 {unit}")

    # 覆盖范围之外必须返回 None —— 「没覆盖到」不能被说成「属于某个单元」。
    # 0x1000040C0 是 __text *内部*的空洞：没有任何符号声明占着它。
    for addr in (0x100003FFF, 0x1000040C0, 0x1000040FF, 0x100004100, 0x200000000):
        if lm.unit_at(addr) is not None:
            fails.append(f"unit_at(0x{addr:x}) 应为 None，实际 {lm.unit_at(addr)}")

    # size==0 的别名不能抢地址：0x100004040 属于 [2] 不属于 [0]
    if (lm.unit_at(0x100004040) or {}).get("index") != 2:
        fails.append("零长别名把地址抢走了")

    if lm.overlap_count != 0:
        fails.append(f"重叠 {lm.overlap_count}，本 fixture 不该有")
    if len(lm.dead) != 2:
        fails.append(f"dead stripped 解析出 {len(lm.dead)} 条，期望 2")
    if lm.dead[0][1] != 1:
        fails.append(f"dead[0] 的来源索引 {lm.dead[0][1]}，期望 1")

    s = P.summarize(lm)
    if s["symbols_zero_size"] != 2:
        fails.append(f"零长符号 {s['symbols_zero_size']}，期望 2")
    # 三段各 0x40 = 192 字节，__text 是 0x100 = 256 字节，
    # 0x1000040C0~0x100004100 这 64 字节没有符号声明 —— 覆盖率必须如实是 75%
    if s["text_size"] != 0x100 or s["text_bytes_covered"] != 0xC0:
        fails.append(f"__text 覆盖 {s['text_bytes_covered']}/{s['text_size']}，期望 192/256")
    if s["text_coverage"] != 0.75:
        fails.append(f"text_coverage={s['text_coverage']}，期望 0.75")

    if fails:
        print("FAIL")
        for f in fails:
            print("  -", f)
        return 1
    print("PASS  8 条单元规则 + 地址归属 + 空洞返回 None + 零长别名不抢地址")
    print(f"      __text 覆盖 {s['text_bytes_covered']}/{s['text_size']} 字节 = "
          f"{s['text_coverage']:.0%}，空洞如实计入未覆盖")
    return 0


if __name__ == "__main__":
    sys.exit(main())
