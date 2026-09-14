#!/usr/bin/env python3
"""Read a Mach-O's section table out of `otool -l`, and compare it to a map's.

Why this exists
---------------
The P1 gate originally said "map 与二进制同一性靠 `LC_UUID`".  That was wrong on
two counts, both measured:

* A linker map contains no UUID.  Parsing 315,059 lines of one turned up
  `# Path:`, `# Arch:`, `# Sections:`, `# Symbols:`, `# Dead Stripped Symbols:`
  and nothing else.
* `LC_UUID` is not stable across builds anyway.  The same repo at the same SHA
  built three times with the same Xcode gave three different UUIDs
  (`FAFA5749…`, `3796D76A…`, `89B98069…`).

But the identity question is real, and the data already answers it better: a
map's `# Sections:` block lists every section's segment, name, address and
size, and `otool -l` lists the same for the binary.  Those two tables agreeing
is evidence about *content*, not about a label -- and they are known to
discriminate, because the `base` and `strip_all` binaries of the same commit
differ in exactly this table.

The second use is checking what `strip` does.  `strip` is supposed to be a
post-link operation that removes symbol-table entries without moving code.  If
that holds, the section table is byte-identical before and after, and the map
produced at link time stays valid for the stripped binary.  This module
measures that rather than assuming it.
"""

from __future__ import annotations

import re

RE_KV = re.compile(r"^\s*(sectname|segname|addr|size|offset)\s+(\S+)\s*$")


def parse_otool_sections(text):
    """Parse `otool -l` output into [{segment, section, addr, size, offset}].

    Only blocks introduced by a bare ``Section`` line are read; the ``segname``
    that appears in an ``LC_SEGMENT_64`` header has no ``sectname`` beside it
    and must not be mistaken for a section.
    """
    out, cur = [], None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "Section":
            if cur and "section" in cur:
                out.append(cur)
            cur = {}
            continue
        if cur is None:
            continue
        if stripped.startswith("Load command") or stripped.startswith("Section"):
            if "section" in cur:
                out.append(cur)
            cur = None if stripped.startswith("Load command") else {}
            continue
        m = RE_KV.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        try:
            if key == "sectname":
                cur["section"] = val
            elif key == "segname":
                cur["segment"] = val
            elif key in ("addr", "size", "offset"):
                cur[key] = int(val, 16) if val.startswith("0x") else int(val)
        except ValueError:
            cur[key] = None
    if cur and "section" in cur:
        out.append(cur)
    return out


def _key(rows):
    return [(r.get("segment"), r.get("section"), r.get("addr"), r.get("size"))
            for r in rows]


def compare_tables(a, b, label_a="a", label_b="b"):
    """Compare two section tables.  Returns a verdict dict, never raises.

    `IDENTICAL` means same sections in the same order with the same addresses
    and sizes.  Anything else is reported with the first few differing rows --
    「不一致」要能看出差在哪，而不是只给一个布尔。
    """
    ka, kb = _key(a), _key(b)
    if not ka or not kb:
        return {"verdict": "NO_DATA", "identical": False,
                f"{label_a}_rows": len(ka), f"{label_b}_rows": len(kb)}
    if ka == kb:
        return {"verdict": "IDENTICAL", "identical": True, "rows": len(ka)}

    names_a = [(s, n) for (s, n, _a, _z) in ka]
    names_b = [(s, n) for (s, n, _a, _z) in kb]
    diffs = [{"index": i, label_a: x, label_b: y}
             for i, (x, y) in enumerate(zip(ka, kb)) if x != y]
    return {
        "verdict": ("SAME_SECTIONS_DIFFERENT_LAYOUT" if names_a == names_b
                    else "DIFFERENT_SECTIONS"),
        "identical": False,
        f"{label_a}_rows": len(ka), f"{label_b}_rows": len(kb),
        "differing_rows": len(diffs),
        "first_diffs": diffs[:6],
    }


def parse_symtab(text):
    """Pull LC_SYMTAB's nsyms/strsize out of `otool -l` output.

    These are the numbers that say whether a strip actually happened: the
    `strip_all` config that only set STRIP_INSTALLED_PRODUCT left them at
    695,589 vs 695,594 -- i.e. it stripped nothing.
    """
    out, seen = {}, False
    for line in text.splitlines():
        s = line.strip()
        if s == "cmd LC_SYMTAB":
            seen = True
            continue
        if seen:
            if s.startswith("cmd "):
                break
            m = re.match(r"^(nsyms|strsize|symoff|stroff)\s+(\d+)$", s)
            if m:
                out[m.group(1)] = int(m.group(2))
    return out or None
