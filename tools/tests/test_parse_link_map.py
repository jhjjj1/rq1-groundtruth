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
    # CocoaPods 的两种形态，路径取自 DaidoujiChen/Dai-Hentai 的实测 map
    "[  8] /Users/runner/work/rq1-groundtruth/rq1-groundtruth/target/Pods/couchbase-lite-ios/Extras/libCBLJSViewCompiler.a(CBLJSFunction.o)",
    f"[  9] {R}/Build/Products/Release-iphoneos/SDWebImage/libSDWebImage.a(SDImageCache.o)",
    "[ 10] /x/Pods/Target Support Files/Pods-App/libscaffold.a(bar.o)",
    "[  5] /usr/lib/system/libdispatch.dylib",
    "[  6] /System/Library/PrivateFrameworks/UIKitCore.framework/UIKitCore",
    "[  7] /somewhere/totally/unexpected.bin",
    # LTO 输出对象。路径形状取自 Dimillian/IceCubesApp 的 lto 档实测 map
    # （[103] .../IceCubesApp.build/Objects-normal/arm64/Ice Cubes_lto.o，
    # 注意文件名里有空格）。它同时也匹配 RE_TARGET_OBJ —— 这条 fixture
    # 就是为了保证 LTO 规则先于 TARGET_INTERMEDIATE。
    f"[ 11] {R}/Build/Intermediates.noindex/IceCubesApp.build/Release-iphoneos/IceCubesActionExtension.build/Objects-normal/arm64/Ice Cubes_lto.o",
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
    "0x1000040C0\t0x00000020\t[ 11] _cmark_utf8proc_case_fold",
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
        # pod 自带的预编译库：pod 名在**路径**里，库名与它毫无关系。
        # 只看库名会把单元判成 libCBLJSViewCompiler —— 这条就是为此存在的。
        8: ("COCOAPOD", "couchbase-lite-ios", "POD_VENDORED_ARCHIVE"),
        # 从源码编出来的 pod：目录名与 lib<名>.a 互相印证
        9: ("COCOAPOD", "SDWebImage", "POD_BUILT_FROM_SOURCE"),
        # Pods/ 下的脚手架目录不是 pod
        10: ("STATIC_ARCHIVE", "libscaffold", "ARCHIVE_MEMBER"),
        # LTO 输出：单元必须是 None（链接器自己也说不清），不能是 app target
        11: ("LTO_MERGED", None, "LTO_OUTPUT"),
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
    # 0x1000040E0 是 __text *内部*的空洞：没有任何符号声明占着它。
    for addr in (0x100003FFF, 0x1000040E0, 0x1000040FF, 0x100004100, 0x200000000):
        if lm.unit_at(addr) is not None:
            fails.append(f"unit_at(0x{addr:x}) 应为 None，实际 {lm.unit_at(addr)}")

    # LTO 吸收的地址是另一回事：**覆盖了**，但单元是 None。
    # 「没覆盖」(unit_at → None) 和「覆盖了但说不清」(记录在、unit 为 None)
    # 评分时走不同的路 —— 前者是 map 的盲区，后者要排除出分母。
    got = lm.unit_at(0x1000040C8)
    if got is None:
        fails.append("LTO 地址被当成未覆盖了")
    elif (got.get("kind"), got.get("unit"), got.get("target")) != \
            ("LTO_MERGED", None, "IceCubesActionExtension"):
        fails.append(f"LTO 地址的记录不对：{got}")
    if (lm.objects.get(11) or {}).get("unit") == "IceCubesActionExtension":
        fails.append("回归：_lto.o 又被归到 app target 了")

    # size==0 的别名不能抢地址：0x100004040 属于 [2] 不属于 [0]
    if (lm.unit_at(0x100004040) or {}).get("index") != 2:
        fails.append("零长别名把地址抢走了")

    # 库名与 pod 名是否一致要如实记录：实测里不一致是常态
    agree = {i: (lm.objects.get(i) or {}).get("archive_name_agrees")
             for i in (8, 9, 10)}
    if agree[8] is not False:
        fails.append(f"[8] archive_name_agrees={agree[8]}，"
                     "期望 False（couchbase-lite-ios vs libCBLJSViewCompiler）")
    if agree[9] is not True:
        fails.append(f"[9] archive_name_agrees={agree[9]}，期望 True")
    if (lm.objects.get(8) or {}).get("unit") == "libCBLJSViewCompiler":
        fails.append("回归：又按库名判 pod 单元了")

    if lm.overlap_count != 0:
        fails.append(f"重叠 {lm.overlap_count}，本 fixture 不该有")
    if len(lm.dead) != 2:
        fails.append(f"dead stripped 解析出 {len(lm.dead)} 条，期望 2")
    if lm.dead[0][1] != 1:
        fails.append(f"dead[0] 的来源索引 {lm.dead[0][1]}，期望 1")

    s = P.summarize(lm)
    if s["symbols_zero_size"] != 2:
        fails.append(f"零长符号 {s['symbols_zero_size']}，期望 2")
    # 三段各 0x40 + LTO 一段 0x20 = 224 字节，__text 是 0x100 = 256 字节，
    # 0x1000040E0~0x100004100 这 32 字节没有符号声明 —— 覆盖率必须如实是 87.5%
    if s["text_size"] != 0x100 or s["text_bytes_covered"] != 0xE0:
        fails.append(f"__text 覆盖 {s['text_bytes_covered']}/{s['text_size']}，期望 224/256")
    if s["text_coverage"] != 0.875:
        fails.append(f"text_coverage={s['text_coverage']}，期望 0.875")
    # LTO 吸收量：1 个对象、1 个符号、0x20 字节，且全在 __text 里
    if (s["lto_objects"], s["lto_symbols"], s["lto_bytes"], s["lto_text_bytes"]) \
            != (1, 1, 0x20, 0x20):
        fails.append(f"LTO 计数 {(s['lto_objects'], s['lto_symbols'], s['lto_bytes'], s['lto_text_bytes'])}，"
                     "期望 (1, 1, 32, 32)")
    # units_seen 不能把 None 算成一个单元，top_units 里也不能出现 None
    if any(u["unit"] is None for u in s["top_units"]):
        fails.append("top_units 里出现了 None 单元")

    if fails:
        print("FAIL")
        for f in fails:
            print("  -", f)
        return 1
    print(f"PASS  {len(want)} 条单元规则（含 CocoaPods 两种形态、LTO 输出）+ 地址归属 "
          "+ 空洞返回 None + 零长别名不抢地址")
    print(f"      __text 覆盖 {s['text_bytes_covered']}/{s['text_size']} 字节 = "
          f"{s['text_coverage']:.0%}，空洞如实计入未覆盖")
    return 0


if __name__ == "__main__":
    sys.exit(main())
