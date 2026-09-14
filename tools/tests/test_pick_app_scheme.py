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


def fake_settings(container, scheme, configuration, destination, timeout):
    if scheme == "StatusKit-Package":            # SPM package scheme: not queryable
        return [], "rc=65 xcodebuild: error: scheme not buildable for this destination"
    if scheme == "IceCubesApp":
        pt = P.PT_APP
    elif scheme in APPEX:
        pt = P.PT_APPEX
    else:
        pt = "com.apple.product-type.library.static"
    return [{"target": scheme, "settings": {
        "PRODUCT_TYPE": pt,
        "LD_GENERATE_MAP_FILE": "NO",
        "LD_MAP_FILE_PATH": f"/dd/{scheme}.build/{scheme}-LinkMap-normal-arm64.txt",
        "TARGET_TEMP_DIR": f"/dd/{scheme}.build",
    }}], None


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
        rc = P.main(["--container", "-project IceCubesApp.xcodeproj",
                     "--schemes-json", str(lst), "--out", str(out)])
        return rc, json.loads(out.read_text())


def main():
    fails = []

    rc, res = run(SCHEMES)
    if res["chosen"] != "IceCubesApp":
        fails.append(f"chosen={res['chosen']!r}, 期望 IceCubesApp（字母序第一是 Account）")
    if res["chosen"] == "Account":
        fails.append("回归：又按字母序取了第一个")
    if res["app_rule_fired"] != ["PRODUCT_TYPE"]:
        fails.append(f"app_rule_fired={res['app_rule_fired']}")
    if res["ambiguous"]:
        fails.append("单 app 工程被判成 ambiguous")
    if res["kind_counts"].get("APPEX") != 4:
        fails.append(f"APPEX 计数={res['kind_counts'].get('APPEX')}, 期望 4")
    if len(res["probe_failures"]) != 1:
        fails.append(f"probe_failures={len(res['probe_failures'])}, 期望 1")
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
    all_app = lambda c, s, cf, d, t: (
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
    print("PASS  3 个场景：正常工程 / 纯库仓库 / 双 app scheme")
    print(f"      IceCubesApp 28 个 scheme -> chosen={res['chosen']} "
          f"(字母序第一是 {SCHEMES[0]})")
    print(f"      kind_counts={res['kind_counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
