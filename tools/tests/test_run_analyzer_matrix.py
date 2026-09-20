#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""矩阵跑批的守恒与状态区分。

用一个假分析器脚本代替真的那个（真的要 Ghidra，跑一格几十分钟），把四种结局都造出来：
正常产出、退出码非 0、退出码 0 但什么都没写、以及这一格根本没有包。

要钉住的是两件事：分桶合计必须等于矩阵格数（分母不许悄悄变小），以及「退出码 0 但输出
为空」不能混进「成功」—— 那正是这个项目反复栽的那个形状。
"""
import json
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import run_analyzer_matrix as R  # noqa: E402

FAKE = '''#!/usr/bin/env python3
import json, sys, pathlib
ipa = sys.argv[1].lower()
out = pathlib.Path(sys.argv[sys.argv.index("--output") + 1])
out.mkdir(parents=True, exist_ok=True)
if "boom" in ipa:
    print("analysis blew up"); sys.exit(3)
if "empty" in ipa:
    sys.exit(0)                      # 退出码 0，但什么都不写


def opt(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


# 真分析器会自己建 --work-dir 并在里面留下 Ghidra 工程；假的也留一份，
# 这样 work-dir 唯一性与 --clean-workdir 才有东西可验。
wd = opt("--work-dir", "")
if wd:
    w = pathlib.Path(wd); (w / "ghidra-projects-x").mkdir(parents=True, exist_ok=True)
    (w / "ghidra-projects-x" / "project.gpr").write_text("x" * 4096)

sched = opt("--capability-scheduling", "off")
undeclared = opt("--undeclared-facts", "off")
summary = {"sites": 1,
           "reason_level_collection": {"enabled": sched == "evidence",
                                       "capability_scheduling": sched}}
# 包名里带 stale 的假装成 0.123.83 之前的分析器：认识参数，但 summary 里
# 没有 undeclared_facts 块。
if "stale" not in ipa:
    summary["undeclared_facts"] = {"mode": undeclared}
# 包名里带 ignores 的假装成「收了参数但没照做」。
if "ignores" in ipa:
    summary["reason_level_collection"]["enabled"] = False
(out / "summary.json").write_text(json.dumps(summary))
(out / "evidence.json").write_text("[]")
'''


def job(root, repo, cfg, styles, tag=""):
    d = root / f"gt-{repo.replace('/', '-')}-{cfg}"; d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({"repo": repo, "config_id": cfg}), encoding="utf-8")
    for s in styles:
        with zipfile.ZipFile(d / f"bundle.{s}.ipa", "w") as zf:
            zf.writestr(f"Payload/{tag or 'A'}.app/A", b"\xcf\xfa\xed\xfe")
    return d


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ram_"))
    try:
        fake = tmp / "fake_analyzer.py"; fake.write_text(FAKE, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        rules = tmp / "rra_rules.yaml"; rules.write_text("schema: 5\n", encoding="utf-8")
        b = tmp / "bundles"; b.mkdir()
        job(b, "acme/Good", "base", ["none", "all"])
        job(b, "acme/Good", "lto", ["none", "all"])
        job(b, "acme/Boom", "base", ["none", "all"], tag="boom")     # 包名里带 boom -> 退出码 3
        job(b, "acme/Empty", "base", ["none"], tag="empty")          # all 档位缺包
        job(b, "acme/Stale", "base", ["none"], tag="stale")          # 旧版分析器
        job(b, "acme/Ignores", "base", ["none"], tag="ignores")      # 收了参数没照做
        out = tmp / "analyzer"

        argv = ["--bundles", str(b), "--out", str(out), "--rules", str(rules),
                "--analyzer", f"{sys.executable} {fake}", "--styles", "none,all"]

        # dry-run 不执行任何一格，但要把命令打出来、把 RUN_INDEX 写出来
        assert R.main([*argv, "--dry-run"]) == 0
        idx = json.loads((out / "RUN_INDEX.json").read_text(encoding="utf-8"))
        assert idx["dry_run"] is True and idx["cells"] == [] and idx["cells_expected"] == 20
        rc_ = idx["run_config"]
        assert rc_["capability_scheduling"] == "evidence"
        assert rc_["undeclared_facts"] == "probe"
        assert rc_["jobs"] == 1 and rc_["clean_workdir"] is False
        assert idx["rules_sha256"] and len(idx["rules_sha256"]) == 64

        rc = R.main(argv)
        idx = json.loads((out / "RUN_INDEX.json").read_text(encoding="utf-8"))
        t = idx["totals"]
        # 5 个 App × 2 配置 × 2 档位 = 20 格（Good 两个配置，其余各只有 base/none）
        assert idx["cells_expected"] == 20 and sum(t.values()) == 20, t
        assert t["DONE"] == 4, t                     # Good base/lto × none/all
        assert t["FAILED"] == 2, t                   # Boom base × none/all
        assert t["EMPTY_OUTPUT"] == 1, t             # Empty base/none：退出码 0 但没输出
        assert t["CONFIG_NOT_APPLIED"] == 2, t       # Stale（旧版）与 Ignores（没照做）
        assert t["NO_BUNDLE"] == 11, t
        assert rc == 1                               # 有失败的格子，退出码非 0

        cells = {(c["repo"], c["config"], c["style"]): c for c in idx["cells"]}
        g = cells[("acme/Good", "base", "none")]
        assert g["state"] == "DONE" and g["rc"] == 0
        assert g["output_files"] == ["evidence.json", "summary.json"], g["output_files"]
        assert (out / "acme-Good" / "base" / "none" / "summary.json").is_file()
        assert pathlib.Path(g["log"]).read_text(encoding="utf-8").startswith("$ ")
        assert cells[("acme/Empty", "base", "none")]["state"] == "EMPTY_OUTPUT"
        assert cells[("acme/Boom", "base", "none")]["rc"] == 3
        assert idx["elapsed_summary"]["n"] == 9 and idx["elapsed_summary"]["mean_s"] is not None

        # 命令里必须带上两个调度参数 —— 不带，reason 级那一半根本不跑
        cmdline = pathlib.Path(g["log"]).read_text(encoding="utf-8").splitlines()[0]
        assert "--capability-scheduling evidence" in cmdline, cmdline
        assert "--undeclared-facts probe" in cmdline, cmdline
        assert g["config_applied"] is True

        # 两种「绿的但配置没生效」各自被单列出来，而且说得出是哪一种
        stale = cells[("acme/Stale", "base", "none")]
        assert stale["state"] == "CONFIG_NOT_APPLIED" and stale["rc"] == 0
        assert stale["config_applied"] is False
        assert "0.123.83" in (stale["note"] or ""), stale["note"]
        ignores = cells[("acme/Ignores", "base", "none")]
        assert ignores["state"] == "CONFIG_NOT_APPLIED"
        assert "reason 级采集未启用" in (ignores["note"] or ""), ignores["note"]

        # 显式关掉时，同样的产物就不该再报 CONFIG_NOT_APPLIED
        out_off = tmp / "analyzer_off"
        R.main(["--bundles", str(b), "--out", str(out_off), "--rules", str(rules),
                "--analyzer", f"{sys.executable} {fake}", "--styles", "none",
                "--capability-scheduling", "off", "--undeclared-facts", "off"])
        idx_off = json.loads((out_off / "RUN_INDEX.json").read_text(encoding="utf-8"))
        off_cells = {(c["repo"], c["config"], c["style"]): c for c in idx_off["cells"]}
        assert off_cells[("acme/Ignores", "base", "none")]["state"] == "DONE"
        assert off_cells[("acme/Stale", "base", "none")]["state"] == "DONE"

        # 断点续传：再跑一次，已完成的格子变 DONE_CACHED，失败的还会重试
        R.main(argv)
        idx2 = json.loads((out / "RUN_INDEX.json").read_text(encoding="utf-8"))
        assert idx2["totals"]["DONE_CACHED"] == 6, idx2["totals"]
        assert sum(idx2["totals"].values()) == 20

        # --jobs：并发跑出来的 RUN_INDEX 必须与串行逐格相同（顺序、状态、格数）
        def run_fresh(tag, extra):
            o = tmp / tag
            R.main(["--bundles", str(b), "--out", str(o), "--rules", str(rules),
                    "--analyzer", f"{sys.executable} {fake}", "--styles", "none,all",
                    "--work-dir", str(tmp / f"work_{tag}"), *extra])
            return json.loads((o / "RUN_INDEX.json").read_text(encoding="utf-8"))

        one = run_fresh("serial", ["--jobs", "1"])
        many = run_fresh("par", ["--jobs", "6"])
        shape = lambda idx: [(c["repo"], c["config"], c["style"], c["state"]) for c in idx["cells"]]
        assert shape(one) == shape(many), "并发改变了 RUN_INDEX 的顺序或状态"
        assert one["totals"] == many["totals"], (one["totals"], many["totals"])
        assert many["run_config"]["jobs"] == 6

        # work-dir 每格唯一：拿它当共用目录，并发时两格会往同一个 Ghidra 工程里写
        tags = {R.cell_tag(c) for c in R.discover(str(b), ["none", "all"], None)[0]}
        assert len(tags) == 20, f"cell_tag 不唯一，只有 {len(tags)} 个"
        wd = tmp / "work_par"
        assert wd.is_dir() and len(list(wd.iterdir())) > 1, "每格应当各有一个 work-dir"

        # --clean-workdir：跑完把该格的 work-dir 删掉，--out 一个字节不动
        idx_c = run_fresh("clean", ["--jobs", "3", "--clean-workdir"])
        wdc = tmp / "work_clean"
        left = [x for x in (wdc.iterdir() if wdc.is_dir() else [])]
        assert left == [], f"--clean-workdir 之后还剩 {left}"
        assert idx_c["run_config"]["clean_workdir"] is True
        assert (tmp / "clean" / "acme-Good" / "base" / "none" / "summary.json").is_file()
        assert shape(idx_c) == shape(one)

        # --limit：只跑前 N 格，其余如实记 NOT_ATTEMPTED，合计仍然守恒
        out2 = tmp / "analyzer2"
        R.main(["--bundles", str(b), "--out", str(out2), "--rules", str(rules),
                "--analyzer", f"{sys.executable} {fake}", "--styles", "none,all", "--limit", "2"])
        idx3 = json.loads((out2 / "RUN_INDEX.json").read_text(encoding="utf-8"))
        assert idx3["totals"]["NOT_ATTEMPTED"] > 0 and sum(idx3["totals"].values()) == 20

        print("PASS  矩阵跑批：20 格守恒（DONE 4 / FAILED 2 / EMPTY_OUTPUT 1 / "
              "CONFIG_NOT_APPLIED 2 / NO_BUNDLE 11）/「退出码 0 但没输出」与"
              "「绿的但调度没生效」各单列一档 / 命令带 --capability-scheduling evidence "
              "与 --undeclared-facts probe / 每格写日志与耗时 / dry-run 不执行 / "
              "断点续传记 DONE_CACHED / --limit 下分母不变 / "
              "--jobs 6 与串行逐格同形 / 每格 work-dir 唯一 / --clean-workdir 只回收工作区")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
