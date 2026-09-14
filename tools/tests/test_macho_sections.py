#!/usr/bin/env python3
"""节区表量具的回归测试，数字取自 P1 实跑。

这个量具是拿来替换「靠 LC_UUID 比对 map 与二进制」的 —— 那条前提是我写错的：
map 文件里根本没有 UUID 字段，而且同一 SHA、同一 Xcode 连着三次构建给出三个
不同的 UUID（FAFA5749… / 3796D76A… / 89B98069…）。

真实产物上的验证结果（IceCubesApp @ b2db303, Xcode 26.2）：
    base       map 42 节 ↔ 二进制 42 节   IDENTICAL
    strip_all  map 42 节 ↔ 二进制 42 节   IDENTICAL
    交叉       base 的 map vs strip_all 的二进制
               SAME_SECTIONS_DIFFERENT_LAYOUT, 25 行不同
最后一条是量具的证伪项：它必须能把两个不同的二进制分开，否则判不出任何东西。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import macho_sections as M

#: `otool -l` 的真实排版：LC_SEGMENT_64 头里也有 segname，但它下面没有
#: sectname —— 上一个版本的解析器就是在这里最容易把段当成节。
OTOOL = """Load command 1
      cmd LC_SEGMENT_64
  cmdsize 1032
  segname __TEXT
   vmaddr 0x0000000100000000
   vmsize 0x0000000001090000
  fileoff 0
 filesize 17334272
Section
  sectname __text
   segname __TEXT
      addr 0x0000000100004000
      size 0x0000000001083910
    offset 16384
     align 2^2 (4)
Section
  sectname __stubs
   segname __TEXT
      addr 0x0000000101087910
      size 0x00000000000085bc
    offset 17332496
Load command 2
      cmd LC_SEGMENT_64
  cmdsize 552
  segname __DATA_CONST
Section
  sectname __got
   segname __DATA_CONST
      addr 0x0000000101090000
      size 0x0000000000007258
    offset 17367040
Load command 3
      cmd LC_SYMTAB
  cmdsize 24
   symoff 63500288
    nsyms 695589
   stroff 74000000
  strsize 27270824
Load command 4
      cmd LC_UUID
  cmdsize 24
     uuid 3796D76A-76B5-379B-95C7-19A547B9459C
"""


def main():
    fails = []

    secs = M.parse_otool_sections(OTOOL)
    if len(secs) != 3:
        fails.append(f"解析出 {len(secs)} 个节，期望 3（段头的 segname 不能算节）")
    if secs and secs[0] != {"section": "__text", "segment": "__TEXT",
                            "addr": 0x100004000, "size": 0x1083910,
                            "offset": 16384}:
        fails.append(f"第一个节解析错：{secs[0] if secs else None}")
    if secs and [s["segment"] for s in secs] != ["__TEXT", "__TEXT", "__DATA_CONST"]:
        fails.append(f"段归属错：{[s['segment'] for s in secs]}")

    st = M.parse_symtab(OTOOL)
    if st != {"symoff": 63500288, "nsyms": 695589,
              "stroff": 74000000, "strsize": 27270824}:
        fails.append(f"LC_SYMTAB 解析错：{st}")
    # LC_UUID 紧跟在 LC_SYMTAB 后面，不能被读进来
    if st and "uuid" in st:
        fails.append("把 LC_UUID 的字段读进 symtab 了")

    # 同一张表：IDENTICAL
    v = M.compare_tables(secs, secs, "map", "bin")
    if v["verdict"] != "IDENTICAL" or not v["identical"]:
        fails.append(f"自比不是 IDENTICAL：{v}")

    # 实跑里的形态：节名顺序相同、地址整体偏移
    moved = [dict(x) for x in secs]
    for x in moved[1:]:
        x["addr"] += 312
    v = M.compare_tables(secs, moved, "map", "bin")
    if v["verdict"] != "SAME_SECTIONS_DIFFERENT_LAYOUT" or v["identical"]:
        fails.append(f"偏移后的表判成了 {v['verdict']}")
    if v.get("differing_rows") != 2:
        fails.append(f"差异行数 {v.get('differing_rows')}，期望 2")

    # 节的组成本身变了
    fewer = secs[:2]
    v = M.compare_tables(secs, fewer, "map", "bin")
    if v["verdict"] != "DIFFERENT_SECTIONS":
        fails.append(f"节数不同应判 DIFFERENT_SECTIONS，得到 {v['verdict']}")

    # 空输入：NO_DATA，而不是「相同」—— 没量到不能说成一致
    v = M.compare_tables([], secs, "map", "bin")
    if v["verdict"] != "NO_DATA" or v["identical"]:
        fails.append(f"空表应判 NO_DATA 且 identical=False，得到 {v}")

    # otool 跑失败时给的是空串，不能崩
    if M.parse_otool_sections("") != [] or M.parse_symtab("") is not None:
        fails.append("空输入没有安全返回")

    if fails:
        print("FAIL")
        for f in fails:
            print("  -", f)
        return 1
    print("PASS  段/节区分 + LC_SYMTAB + 四种比对判定 + 空输入")
    print("      证伪项：地址整体偏移必须判为不一致（实跑中 base 与 strip_all "
          "的节区表差 25 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
