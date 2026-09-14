#!/usr/bin/env python3
"""Parse an Xcode linker map into address -> attribution unit, the RQ1 ground truth.

Format
------
Written against real output, not from memory.  Probe run #1 on GitHub's
macos-latest (Xcode 26.6, Swift 6.3.3, ld_prime) produced maps in the classic
ld64 layout::

    # Path: <output binary>
    # Arch: arm64
    # Object files:
    [  0] linker synthesized
    [  1] /.../Objects-normal/arm64/ActionRequestHandler.o
    [  4] /.../Build/Products/Release-iphoneos/Models.o
    [ 10] /.../libclang_rt.ios.a(os_version_check.c.o)
    # Sections:
    # Address<TAB>Size    <TAB>Segment<TAB>Section
    0x100004000<TAB>0x0032FB28<TAB>__TEXT<TAB>__text
    # Symbols:
    # Address<TAB>Size    <TAB>File  Name
    0x100004000<TAB>0x00000014<TAB>[  1] _$s23IceCubes...
    # Dead Stripped Symbols:
    #        <TAB>Size    <TAB>File  Name
    <<dead>><TAB>0x0000003C<TAB>[  1] _$sxIeAgHr_...

Measured on that map: 33,998 symbol lines, 0 parse failures; of the 28,130
symbols with non-zero size, 175 (0.62%) carry file index 0.

Units
-----
An object-file path is turned into an *attribution unit* by an ordered ladder
of rules.  Every entry records which rule fired, and a path that matches none
is `UNKNOWN` -- never guessed into a neighbouring unit.  The distinction that
matters for RQ1: SwiftPM dependencies arrive as one merged ``<Product>.o``
under ``Build/Products/``, while the app's own code arrives as per-source
``.o`` under ``Intermediates.noindex/<Project>.build/<Config>/<Target>.build/``.
CocoaPods instead yields ``lib<Pod>.a(File.o)``, which rule ARCHIVE_MEMBER
covers.

This module does **not** decide first-party vs third-party.  The map cannot
tell a local package from a fetched one -- both land in ``Build/Products``.
That classification needs ``Package.resolved`` / ``Podfile.lock`` and belongs
to whatever step has them.
"""

from __future__ import annotations

import argparse
import bisect
import collections
import json
import os
import re
import sys

SEC_OBJECTS = "# Object files:"
SEC_SECTIONS = "# Sections:"
SEC_SYMBOLS = "# Symbols:"
SEC_DEAD = "# Dead Stripped Symbols:"

RE_OBJ = re.compile(r"^\[\s*(\d+)\]\s(.*)$")
RE_SYM = re.compile(r"^(0x[0-9A-Fa-f]+)\t(0x[0-9A-Fa-f]+)\t\[\s*(\d+)\]\s(.*)$")
RE_DEAD = re.compile(r"^<<dead>>\t(0x[0-9A-Fa-f]+)\t\[\s*(\d+)\]\s(.*)$")
RE_SECT = re.compile(r"^(0x[0-9A-Fa-f]+)\t(0x[0-9A-Fa-f]+)\t(\S+)\t(\S+)$")
RE_ARCHIVE = re.compile(r"^(?P<archive>.*/(?P<name>[^/]+)\.a)\((?P<member>[^)]+)\)$")
RE_TARGET_OBJ = re.compile(
    r"/Intermediates\.noindex/(?P<project>[^/]+)\.build/(?P<config>[^/]+)/"
    r"(?P<target>[^/]+)\.build/.*?/(?P<obj>[^/]+\.o)$")


def derive_unit(index, path):
    """Ordered rule ladder: first match wins, and the rule name is recorded."""
    if index == 0 or path == "linker synthesized":
        return {"unit": None, "kind": "LINKER_SYNTHESIZED", "rule": "INDEX_ZERO"}

    m = RE_ARCHIVE.match(path)
    if m:
        # libPods-App.a(Foo.o) / libswiftCompatibility51.a(Overrides.cpp.o)
        return {"unit": m.group("name"), "kind": "STATIC_ARCHIVE",
                "rule": "ARCHIVE_MEMBER", "member": m.group("member")}

    m = RE_TARGET_OBJ.search(path)
    if m:
        # 该 target 自己编出来的 .o：单元是 target，不是源文件
        return {"unit": m.group("target"), "kind": "TARGET_OBJECT",
                "rule": "TARGET_INTERMEDIATE", "member": m.group("obj"),
                "project": m.group("project")}

    base = os.path.basename(path)
    if path.endswith(".tbd"):
        return {"unit": base[:-4], "kind": "DYLIB_STUB", "rule": "TBD"}
    if path.endswith(".dylib"):
        return {"unit": base[:-6], "kind": "DYLIB", "rule": "DYLIB"}
    if "/Build/Products/" in path and path.endswith(".o"):
        # SwiftPM 静态产品被合并成单个 .o，文件名就是产品名
        return {"unit": base[:-2], "kind": "MERGED_PRODUCT",
                "rule": "BUILD_PRODUCT_OBJECT"}
    if ".framework/" in path:
        fw = path.split(".framework/")[0].split("/")[-1]
        return {"unit": fw, "kind": "FRAMEWORK", "rule": "FRAMEWORK_BINARY"}
    if path.endswith(".o"):
        return {"unit": base[:-2], "kind": "OBJECT", "rule": "BARE_OBJECT"}
    # 匹配不上就是匹配不上，不往邻近单元里塞
    return {"unit": None, "kind": "UNKNOWN", "rule": "NO_RULE_MATCHED"}


class LinkMap:
    def __init__(self):
        self.path = None
        self.arch = None
        self.objects = {}          # index -> {path, unit, kind, rule, ...}
        self.sections = []         # {addr, size, segment, section}
        self.symbols = []          # (addr, size, index, name)
        self.dead = []             # (size, index, name)
        self.parse_errors = []     # 解析不了的行，原样留着
        self._starts = []          # 供 bisect 的区间起点
        self._ranges = []          # (start, end, index)

    # ---- address -> unit ---------------------------------------------------
    def build_index(self):
        """Build non-overlapping address ranges from size>0 symbols.

        Zero-size symbols are aliases at another symbol's address; they carry no
        extent and so cannot own an address.  They are counted, not used.
        """
        spans = sorted(((a, a + s, i) for (a, s, i, _n) in self.symbols if s > 0))
        merged, overlaps = [], 0
        for start, end, idx in spans:
            if merged and start < merged[-1][1]:
                overlaps += 1
                if end <= merged[-1][1]:
                    continue                      # 完全被包住，丢弃
                start = merged[-1][1]             # 截掉重叠部分
            merged.append((start, end, idx))
        self._ranges = merged
        self._starts = [r[0] for r in merged]
        self.overlap_count = overlaps
        return overlaps

    def unit_at(self, address):
        """Return the attribution record for `address`, or None if uncovered.

        None means the map does not cover that address -- not that the address
        belongs to nobody.  Callers must keep the two apart.
        """
        i = bisect.bisect_right(self._starts, address) - 1
        if i < 0:
            return None
        start, end, idx = self._ranges[i]
        if not (start <= address < end):
            return None
        return self.objects.get(idx)

    def section_at(self, address):
        for s in self.sections:
            if s["addr"] <= address < s["addr"] + s["size"]:
                return s
        return None


def parse(path_or_fh):
    lm = LinkMap()
    fh = (open(path_or_fh, encoding="utf-8", errors="replace")
          if isinstance(path_or_fh, str) else path_or_fh)
    section = None
    try:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")
            if not line:
                continue
            if line.startswith("# Path:"):
                lm.path = line[len("# Path:"):].strip(); continue
            if line.startswith("# Arch:"):
                lm.arch = line[len("# Arch:"):].strip(); continue
            if line.startswith(SEC_OBJECTS):
                section = "obj"; continue
            if line.startswith(SEC_SECTIONS):
                section = "sec"; continue
            if line.startswith(SEC_SYMBOLS):
                section = "sym"; continue
            if line.startswith(SEC_DEAD):
                section = "dead"; continue
            if line.startswith("#"):
                continue                              # 段内的列头

            if section == "obj":
                m = RE_OBJ.match(line)
                if m:
                    idx, p = int(m.group(1)), m.group(2)
                    lm.objects[idx] = {"index": idx, "path": p, **derive_unit(idx, p)}
                else:
                    lm.parse_errors.append((lineno, "obj", line[:200]))
            elif section == "sec":
                m = RE_SECT.match(line)
                if m:
                    lm.sections.append({"addr": int(m.group(1), 16),
                                        "size": int(m.group(2), 16),
                                        "segment": m.group(3), "section": m.group(4)})
                else:
                    lm.parse_errors.append((lineno, "sec", line[:200]))
            elif section == "sym":
                m = RE_SYM.match(line)
                if m:
                    lm.symbols.append((int(m.group(1), 16), int(m.group(2), 16),
                                       int(m.group(3)), m.group(4)))
                else:
                    lm.parse_errors.append((lineno, "sym", line[:200]))
            elif section == "dead":
                m = RE_DEAD.match(line)
                if m:
                    lm.dead.append((int(m.group(1), 16), int(m.group(2)), m.group(3)))
                else:
                    lm.parse_errors.append((lineno, "dead", line[:200]))
    finally:
        if isinstance(path_or_fh, str):
            fh.close()
    lm.build_index()
    return lm


def parse_sections_only(path):
    """只读 `# Sections:` 段就返回。

    app 的 map 有 51 MB、31.5 万条符号；收集阶段只需要那 42 行节区表去和
    二进制比对，全量解析是白花时间和内存。读到 `# Symbols:` 就停。
    """
    sections = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        section = None
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith(SEC_SECTIONS):
                section = "sec"; continue
            if line.startswith(SEC_SYMBOLS):
                break
            if section != "sec" or line.startswith("#") or not line:
                continue
            m = RE_SECT.match(line)
            if m:
                sections.append({"addr": int(m.group(1), 16),
                                 "size": int(m.group(2), 16),
                                 "segment": m.group(3), "section": m.group(4)})
    return sections


def summarize(lm, src=None):
    nz = [s for s in lm.symbols if s[1] > 0]
    by_rule = collections.Counter(o["rule"] for o in lm.objects.values())
    by_kind = collections.Counter(o["kind"] for o in lm.objects.values())
    sym_by_unit = collections.Counter()
    bytes_by_unit = collections.Counter()
    for _a, s, i, _n in nz:
        o = lm.objects.get(i) or {}
        sym_by_unit[o.get("unit")] += 1
        bytes_by_unit[o.get("unit")] += s
    text = next((s for s in lm.sections if s["section"] == "__text"), None)
    covered = sum(e - b for b, e, _ in lm._ranges)
    text_covered = 0
    if text:
        lo, hi = text["addr"], text["addr"] + text["size"]
        text_covered = sum(min(e, hi) - max(b, lo)
                           for b, e, _ in lm._ranges if e > lo and b < hi)
    return {
        "source": src,
        "output_path": lm.path,
        "arch": lm.arch,
        "object_files": len(lm.objects),
        "sections": len(lm.sections),
        "symbols_total": len(lm.symbols),
        "symbols_nonzero_size": len(nz),
        "symbols_zero_size": len(lm.symbols) - len(nz),
        "symbols_unattributed": sum(1 for s in nz if s[2] == 0),
        "dead_stripped": len(lm.dead),
        "parse_errors": len(lm.parse_errors),
        "parse_error_samples": lm.parse_errors[:5],
        "overlapping_spans": lm.overlap_count,
        "address_ranges": len(lm._ranges),
        "bytes_covered": covered,
        "text_size": text["size"] if text else 0,
        "text_bytes_covered": text_covered,
        "text_coverage": round(text_covered / text["size"], 4) if text and text["size"] else None,
        "object_rules": dict(by_rule),
        "object_kinds": dict(by_kind),
        "units_seen": len([u for u in sym_by_unit if u]),
        "top_units": [{"unit": u, "symbols": n, "bytes": bytes_by_unit[u]}
                      for u, n in sym_by_unit.most_common(15)],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("maps", nargs="+", help="一个或多个 link map 文件")
    ap.add_argument("--out", "--json", dest="out", default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    results = []
    for m in args.maps:
        lm = parse(m)
        results.append(summarize(lm, src=m))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=2)

    if not args.quiet:
        print(f"{'map':<34} {'objs':>5} {'syms':>7} {'err':>4} "
              f"{'未归属':>7} {'重叠':>6} {'__text 覆盖':>11}")
        print("-" * 84)
        for r in results:
            name = os.path.basename(r["source"])[-33:]
            cov = f"{r['text_coverage']:.1%}" if r["text_coverage"] is not None else "-"
            print(f"{name:<34} {r['object_files']:>5} {r['symbols_total']:>7} "
                  f"{r['parse_errors']:>4} {r['symbols_unattributed']:>7} "
                  f"{r['overlapping_spans']:>6} {cov:>11}")
        tot_err = sum(r["parse_errors"] for r in results)
        tot_sym = sum(r["symbols_total"] for r in results)
        print()
        print(f"合计 {len(results)} 个 map，{tot_sym:,} 个符号行，解析失败 {tot_err}")
    return 0 if all(r["parse_errors"] == 0 for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
