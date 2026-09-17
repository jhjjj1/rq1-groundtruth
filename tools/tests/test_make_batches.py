#!/usr/bin/env python3
"""make_batches on the scanner test fixtures: unit order, --exclude-units, the source supplement,
dedup ignoring line numbers, and the header fields the annotator relies on."""
import io
import json
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import make_batches as M  # noqa: E402
import make_packs as P  # noqa: E402
import zipfile  # noqa: E402
import scan_source_rra as S  # noqa: E402
import test_scan_source_rra as T  # noqa: E402


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="mb_"))
    try:
        src = tmp / "src"; ws = tmp / "ws"; out = tmp / "batches"
        T.build(src)
        # a second revision of swift-nio with the same System.swift plus a trailing comment (outside every site's
        # context window): its sites are duplicates of the first revision's
        nio2 = src / "deps" / "swift-nio@aaaaaaaaaaaa" / "Sources" / "NIOPosix"
        nio2.mkdir(parents=True)
        orig = (src / "deps" / "swift-nio@558f24a46471" / "Sources" / "NIOPosix" / "System.swift").read_text(encoding="utf-8")
        (nio2 / "System.swift").write_text(orig + "\n" * 20 + "// trailing change far below the sites\n", encoding="utf-8")
        shutil.copy(src / "deps" / "swift-nio@558f24a46471" / "Sources" / "NIOPosix" / "PrivacyInfo.xcprivacy", nio2 / "PrivacyInfo.xcprivacy")
        buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
        try:
            assert S.main(["--rules", str(T.RULES), "--src", str(src), "--out-dir", str(ws)]) == 0
            assert M.main(["--worksheets", str(ws), "--out", str(out), "--src", str(src), "--size", "5",
                           "--exclude-units", "repos/saxobroko-SaxWeather"]) == 0
        finally:
            sys.stdout = old
        man = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
        names = [b["file"] for b in man["batches"]]
        trees = [n.split("__")[1] for n in names]
        assert trees == sorted(trees, key=lambda t: {"repos": 0, "deps": 1, "pods": 2}[t]), trees          # repos, then deps, then pods
        assert not any("saxobroko" in n for n in names) and man["excluded_units"] == ["repos/saxobroko-SaxWeather"]
        # dedup: the shifted copy is mapped to the representative, not batched
        dups = [d for d in man["duplicates"] if d["dup_unit"] == "deps/swift-nio@aaaaaaaaaaaa"]
        assert len(dups) == 4 and all(d["same_declared_reasons"] and d["same_guard"] for d in dups), man["duplicates"]
        assert not any("aaaaaaaaaaaa" in n for n in names)
        assert all(d["dup_unit"] != d["rep_unit"] for d in man["duplicates"]), man["duplicates"]   # never within one unit
        # header: build facts, member names, source supplement with sha1, unit files
        wiki = [n for n in names if "wikimedia" in n]
        h = json.loads(io.open(out / wiki[0], encoding="utf-8").readline())
        assert h["source_dir"] == "src/repos/wikimedia-wikipedia-ios" and h["ud_members"] and "wmf_dateForKey" in h["ud_members"]
        sf = {f["path"]: f for f in h["source_files"]}
        assert all("sha1" in f and f["n_lines"] > 0 for f in sf.values()), sf
        allsf = {f["path"] for n in wiki for f in json.loads(io.open(out / n, encoding="utf-8").readline())["source_files"]}
        caller_batch = [n for n in wiki if "Caller.swift" in json.loads(io.open(out / n, encoding="utf-8").readline())["source_dir"] or
                        any(f["path"].endswith("Caller.swift") for f in json.loads(io.open(out / n, encoding="utf-8").readline())["source_files"])]
        hc = json.loads(io.open(out / caller_batch[0], encoding="utf-8").readline())
        assert any(f["path"].endswith("NSUserDefaults+WMFExtensions.swift") for f in hc["source_files"]), hc["source_files"]   # wrapper definitions travel with the batch that calls them
        assert "Wikipedia/Code/NSUserDefaults+WMFExtensions.swift" in allsf
        assert "Wikipedia/Resources/PrivacyInfo.xcprivacy" in {f["path"] for f in h["unit_files"]}
        for f in sf.values():
            assert (out / "src" / "repos" / "wikimedia-wikipedia-ios" / f["path"]).is_file(), f
        acme = [n for n in names if "acme" in n]
        h = json.loads(io.open(out / acme[0], encoding="utf-8").readline())
        assert h["build_facts"]["primary_app_target"] == ["Demo.xcodeproj", "Demo"]
        assert h["build_facts"]["projects"][0]["targets"][0]["app_groups"] == ["group.com.acme.demo"]
        assert "Demo.xcodeproj/project.pbxproj" in {f["path"] for f in h["unit_files"]}
        # site records: constraints stripped, links kept, context numbered
        rows = [json.loads(l) for l in io.open(out / wiki[0], encoding="utf-8")][1:]
        assert all("reason_constraints" not in r and "hosts" not in r for r in rows)
        assert all(any(l.startswith(f"{r['line']:5d}>>") for l in r["context"]["lines"]) for r in rows)
        # flow_todo: §5 的搜索在打批次时就做掉，标注者拿到的是候选清单而不是一句「去 grep」
        allrows = [r for n in names for r in [json.loads(l) for l in io.open(out / n, encoding="utf-8")][1:]]
        assert all("flow_todo" in r for r in allrows)
        deps_rows = [r for n in names if "__deps__" in n or "__pods__" in n
                     for r in [json.loads(l) for l in io.open(out / n, encoding="utf-8")][1:]]
        assert deps_rows and all("TRIGGER" in r["flow_todo"]["owes_hint"] for r in deps_rows)      # 库里每个站点都欠触发点
        assert all("hosts_to_search" in r["flow_todo"] for r in deps_rows)
        wr = [r for r in allrows if "CHANNEL" in r["flow_todo"]["owes_hint"]]
        assert wr, "写入站点应当欠 CHANNEL"
        assert all((r.get("key") or {}).get("value") or (r.get("wrapper_ref") or [{}])[0].get("keys") for r in wr)
        # 同一个键在别的单元里出现过，就作为候选列出来（这里 wikipedia 与 Cache 的键不重合，候选可以为空，
        # 但字段必须在，且 exported 由 enclosing_function 的可见性算出来）
        assert all(isinstance(r["flow_todo"]["channel_candidates"], list) for r in allrows)
        assert any(r["flow_todo"]["enclosing_exported"] for r in allrows)
        # 同一个键跨单元时，候选必须列出另一个单元的读取点（这一步是把人肉 grep 换成算出来的）
        w = {"site_id": "w" * 16, "file": "A.swift", "line": 3, "operation_prefill": "WRITE", "domain_hint": "APP_GROUP",
             "key": {"expr": "k", "value": "lastSync", "source": "LITERAL"}, "wrapper_ref": None,
             "enclosing_function": "public func save() {"}
        rd = {"site_id": "r" * 16, "file": "B.swift", "line": 9, "operation_prefill": "READ", "domain_hint": "APP_GROUP",
              "key": {"expr": "k", "value": "lastSync", "source": "LITERAL"}, "wrapper_ref": None,
              "enclosing_function": "func load() {"}
        idx = M.key_index({"repos/app-a": [w], "repos/app-b": [rd]})
        td = M.flow_todo(w, "repos/app-a", idx, None)
        assert td["owes_hint"] == ["CHANNEL"] and td["enclosing_exported"]
        assert [(c["unit"], c["key"], c["sites"][0]["site_id"]) for c in td["channel_candidates"]] == \
               [("repos/app-b", "lastSync", "r" * 16)], td["channel_candidates"]
        assert M.flow_todo(rd, "repos/app-b", idx, None)["owes_hint"] == []          # 读方不欠 CHANNEL，写方欠
        # packs: full unit source per pack, units never split, headers rewritten with source_scope
        annot = tmp / "annot"; annot.mkdir(); (annot / "ANNOTATION_PRINCIPLES.md").write_text("# 原则\n", encoding="utf-8"); (annot / "OPUS_PROMPT.md").write_text("# 契约\n", encoding="utf-8")
        buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
        try:
            assert P.main(["--batches", str(out), "--src", str(src), "--out", str(tmp / "packs"), "--annot", str(annot), "--max-mb", "0.03"]) == 0
        finally:
            sys.stdout = old
        packs = json.loads((tmp / "packs" / "PACKS.json").read_text(encoding="utf-8"))["packs"]
        assert len(packs) >= 2 and sum(len(p["units"]) for p in packs) == len({b["unit_location"] for b in man["batches"]}), packs
        assert [b for p in packs for u in p["units"] for b in u["batches"]] == names            # every batch exactly once, MANIFEST order
        z = zipfile.ZipFile(tmp / "packs" / packs[0]["pack"])
        entries = z.namelist()
        assert f"{packs[0]['pack'][:-4]}/annot/ANNOTATION_PRINCIPLES.md" in entries and f"{packs[0]['pack'][:-4]}/SOURCE_INDEX.json" in entries
        first = [e for e in entries if e.endswith(".jsonl")][0]
        hz = json.loads(z.read(first).decode("utf-8").splitlines()[0])
        assert hz["source_scope"] in ("FULL_UNIT", "SITE_MODULES", "REFERENCED_FILES") and hz["source_index"] == "SOURCE_INDEX.json"
        idx = json.loads(z.read(f"{packs[0]['pack'][:-4]}/SOURCE_INDEX.json").decode("utf-8"))
        wiki_units = [u for p in packs for u in p["units"] if u["unit_location"] == "repos/wikimedia-wikipedia-ios"]
        assert wiki_units and wiki_units[0]["source_scope"] == "FULL_UNIT"
        wz = zipfile.ZipFile(tmp / "packs" / [p for p in packs if any(u["unit_location"] == "repos/wikimedia-wikipedia-ios" for u in p["units"])][0]["pack"])
        assert any(e.endswith("Wikipedia/Code/Caller.swift") for e in wz.namelist()) and any(e.endswith("Wikipedia/Resources/PrivacyInfo.xcprivacy") for e in wz.namelist())
        assert all("sha1" in r for r in list(idx.values())[0]["files"].values())
        # --only-new-vs: a second scan with an extra unit yields batches for the new sites only, under another prefix
        src2 = tmp / "src2"; shutil.copytree(src, src2)
        extra = src2 / "deps" / "newpkg@000000000000" / "Sources" / "NewPkg"; extra.mkdir(parents=True)
        (extra / "Up.swift").write_text("let t = mach_approximate_time()\n", encoding="utf-8")
        ws2 = tmp / "ws2"; out2 = tmp / "addendum"
        buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
        try:
            assert S.main(["--rules", str(T.RULES), "--src", str(src2), "--out-dir", str(ws2)]) == 0
            assert M.main(["--worksheets", str(ws2), "--out", str(out2), "--only-new-vs", str(ws), "--prefix", "a"]) == 0
        finally:
            sys.stdout = old
        man2 = json.loads((out2 / "MANIFEST.json").read_text(encoding="utf-8"))
        assert [b["file"] for b in man2["batches"]] == ["a0001__deps__newpkg@000000000000.jsonl"] and man2["counts"]["sites_batched"] == 1, man2["batches"]
        assert man2["n_old_site_ids"] == sum(1 for f in ws.glob("*.jsonl") for _ in io.open(f, encoding="utf-8"))
        # the validator finds an `a####` batch too
        import annotate_validate as V
        rows = [json.loads(l) for l in io.open(out2 / "a0001__deps__newpkg@000000000000.jsonl", encoding="utf-8")][1:]
        assert V.find_batch(out2, [(1, rows[0])]).name.startswith("a0001__")
        print(f"PASS  {len(names)} 个批次：顺序 repos→deps→pods / --exclude-units / 去重忽略行号 / 源码补充目录 / 批次头工程事实 / "
              f"flow_todo 预算欠账与跨单元键候选 / {len(packs)} 个全源码包 / --only-new-vs 补充批次")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
