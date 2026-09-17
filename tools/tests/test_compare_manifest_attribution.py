#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""比对扫描器的清单归属与真包里的事实。

夹具里放了会决定结论的四种形状：一份清单源码与包里字节相同（该判 AGREE）、一份源码有
但没进包（该判 SCANNER_OVER）、一份包里有而源码清单之外（依赖自带的，该判 SCANNER_UNDER
并计入「无源码对应」）、以及一份嵌在 .appex 里的清单（组件归属要认得出来）。

最后一项是这个脚本存在的理由：「扫描器说覆盖」和「包里确实在那一层」如果不分开计数，
过报和漏报会互相抵消，看上去一切正常。
"""
import json
import pathlib
import plistlib
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import compare_manifest_attribution as M  # noqa: E402


def manifest(pairs):
    return plistlib.dumps({"NSPrivacyAccessedAPITypes": [
        {"NSPrivacyAccessedAPIType": "NSPrivacyAccessedAPICategory" + c,
         "NSPrivacyAccessedAPITypeReasons": rs} for c, rs in pairs]})


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cma_"))
    try:
        unit = "repos/acme-Demo"
        src = tmp / "src" / unit
        (src / "Demo" / "Resources").mkdir(parents=True)
        (src / "Legacy").mkdir(parents=True)
        # 进了包的那一份
        shipped = manifest([("UserDefaults", ["1C8F.1"]), ("FileTimestamp", ["C617.1"])])
        (src / "Demo" / "Resources" / "PrivacyInfo.xcprivacy").write_bytes(shipped)
        # 源码里有、没进包的那一份（旧 target 的资源，扫描器仍把它算进了单元）
        orphan = manifest([("DiskSpace", ["E174.1"])])
        (src / "Legacy" / "PrivacyInfo.xcprivacy").write_bytes(orphan)
        # 依赖自带、源码清单之外的那一份，嵌在扩展里
        vendor = manifest([("SystemBootTime", ["35F9.1"])])

        ws = tmp / "ws"; ws.mkdir()
        (ws / "_units.json").write_text(json.dumps({"units": {unit: {
            "declares": {"UserDefaults": ["1C8F.1"], "FileTimestamp": ["C617.1"], "DiskSpace": ["E174.1"]},
            "manifests": ["Demo/Resources/PrivacyInfo.xcprivacy", "Legacy/PrivacyInfo.xcprivacy"]}}}),
            encoding="utf-8")
        sites = [
            {"site_id": "a" * 16, "site_class": "RRA", "manifest_scope": "TARGET_RESOURCE",
             "declared_reasons": {"UserDefaults": ["1C8F.1"]}},
            {"site_id": "b" * 16, "site_class": "RRA", "manifest_scope": "TARGET_RESOURCE",
             "declared_reasons": {"FileTimestamp": ["C617.1"]}},
            # 这一条的归属来自那份没进包的清单 —— 正是要被抓出来的过报
            {"site_id": "c" * 16, "site_class": "RRA", "manifest_scope": "APP_LEVEL_UNION",
             "declared_reasons": {"DiskSpace": ["E174.1"]}},
            {"site_id": "d" * 16, "site_class": "ALT", "manifest_scope": "TARGET_RESOURCE",
             "declared_reasons": None},
        ]
        (ws / "repos__acme-Demo.jsonl").write_text(
            "\n".join(json.dumps(s) for s in sites) + "\n", encoding="utf-8")

        def make_job(root, cfg, extra_vendor=True):
            d = tmp / "bundles" / f"gt-acme-Demo-{cfg}"; d.mkdir(parents=True)
            (d / "manifest.json").write_text(json.dumps(
                {"repo": "acme/Demo", "config_id": cfg}), encoding="utf-8")
            with zipfile.ZipFile(d / "bundle.none.ipa", "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("Payload/Demo.app/Demo", b"\xcf\xfa\xed\xfe")
                zf.writestr("Payload/Demo.app/Info.plist", b"x")
                zf.writestr("Payload/Demo.app/PrivacyInfo.xcprivacy", shipped)
                zf.writestr("Payload/Demo.app/PlugIns/W.appex/W", b"\xcf\xfa\xed\xfe")
                if extra_vendor:
                    zf.writestr("Payload/Demo.app/PlugIns/W.appex/PrivacyInfo.xcprivacy", vendor)
            return d
        make_job(tmp, "base")
        make_job(tmp, "lto", extra_vendor=False)      # 故意造一处跨配置差异

        out = tmp / "r.json"
        rc = M.main(["--bundles", str(tmp / "bundles"), "--worksheets", str(ws),
                     "--src", str(tmp / "src"), "--json", str(out)])
        assert rc == 0
        r = json.loads(out.read_text(encoding="utf-8"))
        app = r["apps"][0]

        # 申报一级：三类各自归位，不互相抵消
        assert app["verdicts"]["AGREE"] == ["FileTimestamp/C617.1", "UserDefaults/1C8F.1"], app["verdicts"]
        assert app["verdicts"]["SCANNER_OVER"] == ["DiskSpace/E174.1"], app["verdicts"]
        assert app["verdicts"]["SCANNER_UNDER"] == ["SystemBootTime/35F9.1"], app["verdicts"]

        # 文件一级：按 sha256 认身份，进没进包、落在哪个组件
        files = {f["path"]: f for f in app["source_manifests"]}
        assert files["Demo/Resources/PrivacyInfo.xcprivacy"]["shipped"] is True
        assert files["Demo/Resources/PrivacyInfo.xcprivacy"]["shipped_components"] == ["."]
        assert files["Legacy/PrivacyInfo.xcprivacy"]["shipped"] is False
        assert len(app["shipped_without_source"]) == 1
        assert app["shipped_without_source"][0]["component"] == "PlugIns/W.appex"

        # 推断机制一级：过报落在 APP_LEVEL_UNION 上，TARGET_RESOURCE 干净
        assert app["by_scope"]["TARGET_RESOURCE"]["over"] == []
        assert app["by_scope"]["APP_LEVEL_UNION"]["over"] == ["DiskSpace/E174.1"]
        assert app["by_scope"]["TARGET_RESOURCE"]["sites"] == 2      # ALT 站点不计入申报比对

        # 组件识别：根 + 扩展
        assert app["components"] == [".", "PlugIns/W.appex"], app["components"]

        # 跨配置差异必须报出来，不能当噪声
        assert len(r["config_differences"]) == 1, r["config_differences"]
        assert r["config_differences"][0]["config"] == "lto"

        t = r["totals"]
        assert t["apps"] == 1 and t["apps_clean"] == 0
        assert t["src_manifests"] == 2 and t["src_manifests_shipped"] == 1

        # 坏包不能静默：Payload 根不唯一时要带 error 而不是空结果
        bad = tmp / "bad.ipa"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("Payload/A.app/A", b"x"); zf.writestr("Payload/B.app/B", b"x")
        assert M.read_bundle(bad)["error"] == "PAYLOAD_ROOTS=2"
        assert M.read_bundle(tmp / "nope.ipa")["error"].startswith("UNREADABLE")
        # 坏清单记成 UNPARSEABLE，不当作「没有申报」
        assert M.declares_of(b"not a plist")[1].startswith("UNPARSEABLE")

        print("PASS  清单归属比对：AGREE / SCANNER_OVER / SCANNER_UNDER 三类分开 / "
              "按 sha256 认文件身份与组件层级 / 过报归到具体的 manifest_scope / "
              "跨配置清单差异报出来 / 坏包与坏清单不静默")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
