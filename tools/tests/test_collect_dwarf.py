#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""collect_dwarf_line_tables with a fake `dwarfdump` on PATH: two dSYMs, one of
which fails, streamed to gzip, indexed with UUIDs, budget flag computed."""
import gzip, json, os, pathlib, shutil, stat, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import collect_build_artifacts as C  # noqa: E402

FAKE = r'''#!/usr/bin/env python3
import sys
args = sys.argv[1:]
path = args[-1]
if "--uuid" in args:
    print(f"UUID: 1234ABCD-0000-0000-0000-{abs(hash(path)) % 10**12:012d} (arm64) {path}"); sys.exit(0)
if "--debug-line" in args:
    if path.endswith("Broken"):
        sys.stderr.write("error: no debug_line\n"); sys.exit(1)
    print("debug_line[0x00000000]"); print("file_names[  1]:"); print('           name: "main.swift"')
    for i in range(5):
        print(f"0x{0x100003f60 + 4*i:016x}      {10+i}      0      1   0             0  is_stmt")
    sys.exit(0)
sys.exit(2)
'''

def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="dw_"))
    try:
        bindir = tmp / "bin"; bindir.mkdir()
        fake = bindir / "dwarfdump"; fake.write_text(FAKE, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        os.environ["PATH"] = f"{bindir}:{os.environ['PATH']}"
        dd = tmp / "dd"; prod = dd / "Build" / "Products" / "Release-iphoneos"
        for name, exe in (("Demo.app.dSYM", "Demo"), ("Broken.framework.dSYM", "Broken")):
            d = prod / name / "Contents" / "Resources" / "DWARF"; d.mkdir(parents=True)
            (d / exe).write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 100)
        out = tmp / "out"; out.mkdir()
        index, total = C.collect_dwarf_line_tables(dd, out, timeout=30)
        assert len(index) == 2, index
        ok = [d for d in index if d["line_table"]]; bad = [d for d in index if not d["line_table"]]
        assert len(ok) == 1 and ok[0]["binary"] == "Demo" and ok[0]["rows"] == 5, ok
        assert ok[0]["uuid"] and ok[0]["uuid"].startswith("1234ABCD"), ok[0]
        assert bad[0]["binary"] == "Broken" and bad[0]["rc"] == 1 and "no debug_line" in bad[0]["note"], bad
        gz = out / ok[0]["line_table"]
        assert gz.is_file() and total == gz.stat().st_size > 0
        text = gzip.open(gz, "rt", encoding="utf-8").read()
        assert text.count("is_stmt") == 5 and "main.swift" in text
        assert not (out / "dwarf" / "Broken.framework.dSYM__Broken.debug-line.txt.gz").exists(), "失败的不留半截文件"
        idx = json.loads((out / "dwarf" / "dwarf_index.json").read_text(encoding="utf-8"))
        assert [d["binary"] for d in idx] == ["Broken", "Demo"]
        # no dSYM at all: an empty index, written, not an exception
        out2 = tmp / "out2"; out2.mkdir()
        index2, total2 = C.collect_dwarf_line_tables(tmp / "nowhere", out2)
        assert index2 == [] and total2 == 0 and (out2 / "dwarf" / "dwarf_index.json").is_file()
        print("PASS  dSYM → gzip 行号表（流式）+ UUID 索引 + 失败不留文件 + 没有 dSYM 时空索引")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    main()
