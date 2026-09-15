#!/usr/bin/env python3
"""Fixture test built from the real P1 failure, not from an invented project.

The scheme list below is verbatim from run #1's `6_Discover scheme` log for
Dimillian/IceCubesApp @ b2db303.  The old code took schemes[0] == "Account";
this test exists to keep that from coming back.
"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pick_app_scheme as P

#: verbatim from logs/build (...)/6_Discover scheme.txt
SCHEMES = [
    "Account", "AccountTests", "AppAccount", "Conversations", "DesignSystem",
    "Env", "EnvTests", "Explore", "IceCubesActionExtension", "IceCubesApp",
    "IceCubesAppWidgetsExtensionExtension", "IceCubesNotifications",
    "IceCubesShareExtension", "Lists", "MediaUI", "Models", "ModelsTests",
    "NetworkClient", "NetworkTests", "Notifications", "RevenueCatUI",
    "RevenueCatUI-Stripped", "RevenueCatUITests", "StatusKit",
    "StatusKit-Package", "StatusKitTests", "Timeline", "TimelineTests",
]

APPEX = {"IceCubesActionExtension", "IceCubesAppWidgetsExtensionExtension",
         "IceCubesNotifications", "IceCubesShareExtension"}

#: 探针 run #1 在 runner 上实测到的形状，不是编的：
#:   IceCubesApp              1 个 target : [application]
#:   IceCubesActionExtension  2 个 target : [app-extension, application]
#: 扩展 scheme 把宿主 app 拖进了构建图，所以「集合里有 application」判不出来。
#: 这个 fixture 存在的意义就是别让那个判法回来。
LIB = "com.apple.product-type.library.static"


def _t(name, pt):
    return {"target": name, "settings": {
        "PRODUCT_TYPE": pt,
        "LD_GENERATE_MAP_FILE": "NO",
        "LD_MAP_FILE_PATH": f"/dd/{name}.build/{name}-LinkMap-normal-arm64.txt",
        "TARGET_TEMP_DIR": f"/dd/{name}.build",
    }}


def fake_settings(flag, path, scheme, configuration, destination,
                  timeout, selector="-scheme"):
    if scheme == "StatusKit-Package":            # SPM package scheme: not queryable
        return [], "rc=65 xcodebuild: error: scheme not buildable for this destination"
    if scheme == "IceCubesApp":
        return [_t("IceCubesApp", P.PT_APP)], None
    if scheme in APPEX:
        # 顺序照实测：自己的产物在前，宿主 app 在后
        return [_t(scheme, P.PT_APPEX), _t("IceCubesApp", P.PT_APP)], None
    if scheme.endswith("Tests"):
        return [], "rc=64 xcodebuild: error: Unable to find a destination"
    return [_t(scheme, LIB)], None


def run(schemes, stub=None):
    """stub 必须显式传入：上一版在 run() 里无条件覆盖 P.show_build_settings，
    把调用方刚设好的桩又冲掉了，导致「双 app scheme」那个用例测的其实还是
    fake_settings。测试自己出过的错也要留在代码里。"""
    with tempfile.TemporaryDirectory() as td:
        lst = pathlib.Path(td) / "schemes.json"
        out = pathlib.Path(td) / "out.json"
        lst.write_text(json.dumps({"project": {"name": "IceCubesApp",
                                               "schemes": schemes}}))
        P.show_build_settings = stub or fake_settings
        rc = P.main(["--container-flag", "project",
                     # 路径带空格：batch01 里三个仓库因此全军覆没
                     "--container-path", "./Ice Cubes/IceCubesApp.xcodeproj",
                     "--schemes-json", str(lst), "--out", str(out)])
        return rc, json.loads(out.read_text())


def main():
    fails = []

    rc, res = run(SCHEMES)
    if res["chosen"] != "IceCubesApp":
        fails.append(f"chosen={res['chosen']!r}, 期望 IceCubesApp（字母序第一是 Account）")
    if res["chosen"] == "Account":
        fails.append("回归：又按字母序取了第一个")
    if res["ambiguous"]:
        fails.append("单 app 工程被判成 ambiguous")
    if res["kind_counts"].get("APPEX") != 4:
        fails.append(f"APPEX 计数={res['kind_counts'].get('APPEX')}, 期望 4")
    if res["kind_counts"].get("APP") != 1:
        fails.append(f"APP 计数={res['kind_counts'].get('APP')}, 期望 1"
                     "（扩展 scheme 不能算 app）")
    if res["app_schemes"] != ["IceCubesApp"]:
        fails.append(f"app_schemes={res['app_schemes']}, 期望只有 IceCubesApp")
    if res["app_rule_fired"] != ["PRIMARY_TARGET"]:
        fails.append(f"app_rule_fired={res['app_rule_fired']}")
    if res["rule_disagreement_count"] != 0:
        fails.append(f"主/次判据分歧 {res['rule_disagreement_count']}，期望 0")
    # 7 个 *Tests scheme 探测失败 + StatusKit-Package，共 8
    if len(res["probe_failures"]) != 8:
        fails.append(f"probe_failures={len(res['probe_failures'])}, 期望 8")
    if rc != 0:
        fails.append(f"rc={rc}, 期望 0")

    # 没有 app scheme 的纯库仓库：必须是 NO_APP_SCHEME_OBSERVED + rc 78,
    # 不能崩，也不能谎称找到了。
    rc2, res2 = run(["Account", "Models", "StatusKitTests"])
    if res2["chosen_verdict"] != "NO_APP_SCHEME_OBSERVED":
        fails.append(f"纯库仓库 verdict={res2['chosen_verdict']}")
    if rc2 != 78:
        fails.append(f"纯库仓库 rc={rc2}, 期望 78")

    # 两个 app scheme：必须记 ambiguous，不能静默取第一个当定论。
    all_app = lambda fl, pa, s, cf, d, t, sel="-scheme": (
        [{"target": s, "settings": {"PRODUCT_TYPE": P.PT_APP}}], None)
    rc4, res4 = run(["Zeta", "Alpha"], stub=all_app)
    if not res4["ambiguous"]:
        fails.append("双 app scheme 没记 ambiguous")
    if res4["chosen_verdict"] != "APP_SCHEME_AMBIGUOUS":
        fails.append(f"双 app verdict={res4['chosen_verdict']}")

    if fails:
        print("FAIL")
        for f in fails:
            print("  -", f)
        return 1
    # 主判据与次判据分歧：app scheme 自己也把扩展列进构建图的工程
    def app_plus_appex(fl, pa, s_, cf, d, t, sel="-scheme"):
        return [_t("Host", P.PT_APP), _t("Ext", P.PT_APPEX)], None
    rc5, res5 = run(["Host"], stub=app_plus_appex)
    if res5["chosen"] != "Host":
        fails.append(f"app 带扩展的工程 chosen={res5['chosen']}, 期望 Host")
    if res5["rule_disagreement_count"] != 1:
        fails.append("app 带扩展时主/次判据应当分歧并被记下来")

    # 输入坏掉时必须给出判定，不能崩 —— batch01 里 12 个仓库的 scheme_verdict
    # 是空的，正是因为这一步抛了异常什么都没写
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        bad = pathlib.Path(td) / "schemes.json"
        bad.write_text("xcodebuild: error: the project is damaged", encoding="utf-8")
        out = pathlib.Path(td) / "o.json"
        rc6 = P.main(["--container-flag", "project", "--container-path", "./X.xcodeproj",
                      "--schemes-json", str(bad), "--list-rc", "65",
                      "--out", str(out)])
        r6 = json.loads(out.read_text())
    if r6["chosen_verdict"] != "SCHEMES_JSON_UNREADABLE" or rc6 != 78:
        fails.append(f"坏输入 verdict={r6['chosen_verdict']} rc={rc6}")
    if not r6.get("schemes_json_head"):
        fails.append("坏输入没有把原文头部记下来")

    # 没有共享 scheme 但有 target：project 容器要退到 -target
    def only_targets(fl, pa, name, cf, d, t, sel="-scheme"):
        assert sel == "-target", f"应当用 -target，实得 {sel}"
        return [{"target": name, "settings": {"PRODUCT_TYPE": P.PT_APP}}], None
    with _tf.TemporaryDirectory() as td:
        lst = pathlib.Path(td) / "s.json"
        lst.write_text(json.dumps({"project": {"name": "X", "schemes": [],
                                               "targets": ["XApp", "XTests"]}}))
        out = pathlib.Path(td) / "o.json"
        P.show_build_settings = only_targets
        P.main(["--container-flag", "project", "--container-path", "./X.xcodeproj",
                      "--schemes-json", str(lst), "--out", str(out)])
        r7 = json.loads(out.read_text())
    if r7.get("probe_mode") != "-target":
        fails.append(f"没退到 -target：probe_mode={r7.get('probe_mode')}")
    if r7["chosen_verdict"] != "APP_SCHEME_AMBIGUOUS" and r7["chosen"] != "XApp":
        fails.append(f"target 退路选中 {r7.get('chosen')}")

    # workspace 没有共享 scheme：没有 target 这一层，只能如实记
    with _tf.TemporaryDirectory() as td:
        lst = pathlib.Path(td) / "s.json"
        lst.write_text(json.dumps({"workspace": {"name": "X", "schemes": []}}))
        out = pathlib.Path(td) / "o.json"
        rc8 = P.main(["--container-flag", "workspace", "--container-path", "./X.xcworkspace",
                      "--schemes-json", str(lst), "--out", str(out)])
        r8 = json.loads(out.read_text())
    if r8["chosen_verdict"] != "NO_SHARED_SCHEMES" or rc8 != 78:
        fails.append(f"workspace 无 scheme verdict={r8['chosen_verdict']} rc={rc8}")

    if fails:
        print("FAIL")
        for f_ in fails:
            print("  -", f_)
        return 1
    print("PASS  7 个场景：正常 / 纯库 / 双 app / app 带扩展 / "
          "输入损坏 / 退到 -target / workspace 无 scheme")
    print(f"      IceCubesApp 28 个 scheme -> chosen={res['chosen']} "
          f"(字母序第一是 {SCHEMES[0]})")
    print(f"      kind_counts={res['kind_counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
