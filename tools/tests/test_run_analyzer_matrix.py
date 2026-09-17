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
import sys, pathlib
ipa = sys.argv[1].lower()
out = pathlib.Path(sys.argv[sys.argv.index("--output") + 1])
out.mkdir(parents=True, exist_ok=True)
if "boom" in ipa:
    print("analysis blew up"); sys.exit(3)
if "empty" in ipa:
    sys.exit(0)                      # 退出码 0，但什么都不写
(out / "summary.json").write_text('{"sites": 1}')
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
        out = tmp / "analyzer"

        argv = ["--bundles", str(b), "--out", str(out), "--rules", str(rules),
                "--analyzer", f"{sys.executable} {fake}", "--styles", "none,all"]

        # dry-run 不执行任何一格，但要把命令打出来、把 RUN_INDEX 写出来
        assert R.main([*argv, "--dry-run"]) == 0
        idx = json.loads((out / "RUN_INDEX.json").read_text(encoding="utf-8"))
        assert idx["dry_run"] is True and idx["cells"] == [] and idx["cells_expected"] == 12
        assert idx["rules_sha256"] and len(idx["rules_sha256"]) == 64

        rc = R.main(argv)
        idx = json.loads((out / "RUN_INDEX.json").read_text(encoding="utf-8"))
        t = idx["totals"]
        # 3 个 App × 2 配置 × 2 档位 = 12 格（Good 两个配置、Boom 与 Empty 各只有 base）
        assert idx["cells_expected"] == 12 and sum(t.values()) == 12, t
        assert t["DONE"] == 4, t                     # Good base/lto × none/all
        assert t["FAILED"] == 2, t                   # Boom base × none/all
        assert t["EMPTY_OUTPUT"] == 1, t             # Empty base/none：退出码 0 但没输出
        assert t["NO_BUNDLE"] == 5, t                # Boom/lto ×2、Empty/lto ×2、Empty/base/all ×1
        assert rc == 1                               # 有失败的格子，退出码非 0

        cells = {(c["repo"], c["config"], c["style"]): c for c in idx["cells"]}
        g = cells[("acme/Good", "base", "none")]
        assert g["state"] == "DONE" and g["rc"] == 0
        assert g["output_files"] == ["evidence.json", "summary.json"], g["output_files"]
        assert (out / "acme-Good" / "base" / "none" / "summary.json").is_file()
        assert pathlib.Path(g["log"]).read_text(encoding="utf-8").startswith("$ ")
        assert cells[("acme/Empty", "base", "none")]["state"] == "EMPTY_OUTPUT"
        assert cells[("acme/Boom", "base", "none")]["rc"] == 3
        assert idx["elapsed_summary"]["n"] == 7 and idx["elapsed_summary"]["mean_s"] is not None

        # 断点续传：再跑一次，已完成的格子变 DONE_CACHED，失败的还会重试
        R.main(argv)
        idx2 = json.loads((out / "RUN_INDEX.json").read_text(encoding="utf-8"))
        assert idx2["totals"]["DONE_CACHED"] == 4, idx2["totals"]
        assert sum(idx2["totals"].values()) == 12

        # --limit：只跑前 N 格，其余如实记 NOT_ATTEMPTED，合计仍然守恒
        out2 = tmp / "analyzer2"
        R.main(["--bundles", str(b), "--out", str(out2), "--rules", str(rules),
                "--analyzer", f"{sys.executable} {fake}", "--styles", "none,all", "--limit", "2"])
        idx3 = json.loads((out2 / "RUN_INDEX.json").read_text(encoding="utf-8"))
        assert idx3["totals"]["NOT_ATTEMPTED"] > 0 and sum(idx3["totals"].values()) == 12

        print("PASS  矩阵跑批：12 格守恒（DONE 4 / FAILED 2 / EMPTY_OUTPUT 1 / NO_BUNDLE 5）/ "
              "「退出码 0 但没输出」单列一档 / 每格写日志与耗时 / dry-run 不执行 / "
              "断点续传记 DONE_CACHED / --limit 下分母不变")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
