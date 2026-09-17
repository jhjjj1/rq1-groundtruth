#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Packaging the built .app as `Payload/<App>.app/…`.

A bundle is built by hand with the shapes that actually decide the outcome: an app with an
embedded framework, an appex, a resource `.bundle`, a privacy manifest at each of those
levels, a Mach-O with no suffix, a `.dylib`, a symlink, an unreadable file, and the media
(Assets.car, png, nib, strings, ttf) that must not travel.  Then the zip is reopened the way
the analyser opens it.

The one that matters most: the executable inside the zip has to be byte-identical to the
binary collected beside it, or the two halves of the RRA question describe different builds.
"""
import hashlib
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import collect_build_artifacts as C  # noqa: E402

MACHO = b"\xcf\xfa\xed\xfe" + b"\x00" * 200          # 64-bit thin
PLIST = b'<?xml version="1.0"?><plist version="1.0"><dict><key>CFBundleExecutable</key><string>%s</string></dict></plist>'
XCPRIV = b'<?xml version="1.0"?><plist version="1.0"><dict><key>NSPrivacyAccessedAPITypes</key><array/></dict></plist>'


def build_app(root):
    app = root / "Release-iphoneos" / "Demo.app"
    (app).mkdir(parents=True)
    (app / "Demo").write_bytes(MACHO + b"main")                       # 无后缀的 Mach-O
    (app / "Info.plist").write_bytes(PLIST % b"Demo")
    (app / "PrivacyInfo.xcprivacy").write_bytes(XCPRIV)
    (app / "Assets.car").write_bytes(b"\x00" * 5000)                  # 分析器不读
    (app / "icon@3x.png").write_bytes(b"\x89PNG" + b"\x00" * 3000)
    (app / "Main.nib").write_bytes(b"\x00" * 400)
    (app / "Localizable.strings").write_bytes(b"\x00" * 300)
    (app / "Font.ttf").write_bytes(b"\x00" * 900)
    fw = app / "Frameworks" / "Net.framework"; fw.mkdir(parents=True)
    (fw / "Net").write_bytes(MACHO + b"net")
    (fw / "Info.plist").write_bytes(PLIST % b"Net")
    (fw / "PrivacyInfo.xcprivacy").write_bytes(XCPRIV)
    (app / "Frameworks" / "libswiftCore.dylib").write_bytes(MACHO + b"swift")
    ex = app / "PlugIns" / "Widget.appex"; ex.mkdir(parents=True)
    (ex / "Widget").write_bytes(MACHO + b"widget")
    (ex / "Info.plist").write_bytes(PLIST % b"Widget")
    (ex / "PrivacyInfo.xcprivacy").write_bytes(XCPRIV)
    (ex / "Assets.car").write_bytes(b"\x00" * 4000)
    rb = app / "Analytics.bundle"; rb.mkdir()
    (rb / "Info.plist").write_bytes(PLIST % b"")
    (rb / "PrivacyInfo.xcprivacy").write_bytes(XCPRIV)               # SDK 以资源包形式发布时的申报
    (app / "embedded.mobileprovision").write_bytes(b"\x00" * 120)
    (app / "archived-expanded-entitlements.xcent").write_bytes(b"\x00" * 60)
    os.symlink(app / "Demo", app / "DemoAlias")                       # 指向包内，跟着走
    os.symlink(root / "outside.bin", app / "Dangling")                # 指向包外，只记数
    return app


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cb_"))
    try:
        (tmp / "outside.bin").write_bytes(b"x")
        app = build_app(tmp)
        out = tmp / "out"; out.mkdir()
        rec = C.pack_bundle(app, out, "Demo", max_bytes=10 * 1024 * 1024, strip_styles=("none",))

        # 该留的都留下了：每个层级的 Mach-O 与清单
        assert rec["bundle_zip"] == "bundle.none.ipa", rec
        # Demo / Net / Widget / dylib，加上指向包内的符号链接 DemoAlias —— 它在包里是一个真成员
        assert rec["bundle_macho_files"] == 5, rec["bundle_macho_files"]
        assert sorted(rec["bundle_privacy_manifests"]) == [
            "Analytics.bundle/PrivacyInfo.xcprivacy", "Frameworks/Net.framework/PrivacyInfo.xcprivacy",
            "PlugIns/Widget.appex/PrivacyInfo.xcprivacy", "PrivacyInfo.xcprivacy"], rec["bundle_privacy_manifests"]
        assert sorted(rec["bundle_nested_bundles"]) == [
            "Analytics.bundle", "Frameworks/Net.framework", "PlugIns/Widget.appex"], rec["bundle_nested_bundles"]
        # 该丢的都丢了，而且记了账
        dropped = {k: v["n"] for k, v in rec["bundle_dropped_by_ext"].items()}
        assert dropped.get(".car") == 2 and dropped.get(".png") == 1 and dropped.get(".nib") == 1, dropped
        assert dropped.get(".strings") == 1 and dropped.get(".ttf") == 1, dropped
        assert rec["bundle_dropped_bytes"] > 13000 and rec["bundle_full_bytes"] > rec["bundle_kept_bytes"]
        assert rec["bundle_symlinks"] == 2, rec["bundle_symlinks"]

        # 打开的方式与分析器一致：Payload/<App>.app/ 一个根，主可执行文件在
        with zipfile.ZipFile(out / "bundle.none.ipa") as zf:
            names = set(zf.namelist())
            assert "Payload/Demo.app/Demo" in names and "Payload/Demo.app/Info.plist" in names
            assert "Payload/Demo.app/Frameworks/Net.framework/Net" in names
            assert "Payload/Demo.app/PlugIns/Widget.appex/PrivacyInfo.xcprivacy" in names
            assert "Payload/Demo.app/Frameworks/libswiftCore.dylib" in names
            assert "Payload/Demo.app/embedded.mobileprovision" in names
            assert not [n for n in names if n.endswith((".car", ".png", ".nib", ".strings", ".ttf"))], names
            assert zf.read("Payload/Demo.app/Demo") == MACHO + b"main"
            assert "Payload/Demo.app/DemoAlias" in names                    # 包内符号链接跟着走
            assert not [n for n in names if n.endswith("Dangling")]         # 包外的不跟
        v = rec["bundle_verify"]
        assert v["verdict"] == "OK" and v["opens"] and v["payload_roots"] == ["Demo.app"], v
        assert v["n_privacy_manifests"] == 4 and v["executable_member"] == "Payload/Demo.app/Demo"
        assert v["executable_sha256"] == hashlib.sha256(MACHO + b"main").hexdigest()

        # 同一份构建打两次，字节相同（时间戳固定）—— 可复现性靠这个，不靠口头保证
        out2 = tmp / "out2"; out2.mkdir()
        C.pack_bundle(app, out2, "Demo", max_bytes=10 * 1024 * 1024, strip_styles=("none",))
        assert (out / "bundle.none.ipa").read_bytes() == (out2 / "bundle.none.ipa").read_bytes()

        # 没有清单的 App 与「打包失败」必须分得开
        bare = tmp / "Release2" / "Bare.app"; bare.mkdir(parents=True)
        (bare / "Bare").write_bytes(MACHO + b"bare"); (bare / "Info.plist").write_bytes(PLIST % b"Bare")
        out3 = tmp / "out3"; out3.mkdir()
        r3 = C.pack_bundle(bare, out3, "Bare", max_bytes=10 * 1024 * 1024, strip_styles=("none",))
        assert r3["bundle_verify"]["verdict"] == "OK_NO_PRIVACY_MANIFEST", r3["bundle_verify"]
        assert r3["bundle_privacy_manifests"] == []

        # 超预算照传，只是记一笔
        out4 = tmp / "out4"; out4.mkdir()
        r4 = C.pack_bundle(app, out4, "Demo", max_bytes=10, strip_styles=("none",))
        assert r4["bundle_zip"] == "bundle.none.ipa" and (out4 / "bundle.none.ipa").is_file()
        assert r4["bundle_note"].startswith("BUNDLE_OVER_BUDGET"), r4["bundle_note"]

        # 每个 strip 档位一份包：内容清单相同，Mach-O 被 strip 过，清单与 plist 一字不动
        out5 = tmp / "out5"; out5.mkdir()
        r6 = C.pack_bundle(app, out5, "Demo", max_bytes=10 * 1024 * 1024, strip_styles=("none", "all"))
        styles = [v["strip_style"] for v in r6["bundle_variants"]]
        assert styles == ["none", "all"], styles
        assert [v["file"] for v in r6["bundle_variants"]] == ["bundle.none.ipa", "bundle.all.ipa"]
        assert r6["bundle_zip"] == "bundle.none.ipa"                    # 代表变体是第一个
        with zipfile.ZipFile(out5 / "bundle.none.ipa") as a, zipfile.ZipFile(out5 / "bundle.all.ipa") as b:
            assert sorted(a.namelist()) == sorted(b.namelist())         # 成员清单一致
            for n in ("Payload/Demo.app/PrivacyInfo.xcprivacy", "Payload/Demo.app/Info.plist"):
                assert a.read(n) == b.read(n)                           # 非代码文件一字不动
        # 这台机器上没有 strip（Linux），所以记成 strip_failed 而不是假装成功 ——
        # 「没跑成」和「跑了没变化」必须分得开
        av = r6["bundle_variants"][1]
        assert av["n_stripped"] + len(av["strip_failed"]) == 5, (av["n_stripped"], av["strip_failed"])
        assert av["verify"]["verdict"] in ("OK", "EXECUTABLE_DIFFERS_FROM_COLLECTED_BINARY")

        # 名字不对时不硬说 OK
        r5 = C.verify_bundle(out / "bundle.none.ipa", "Demo.app", "NotThere")
        assert r5["verdict"] == "EXECUTABLE_MISSING_IN_ZIP", r5

        # Mach-O 的判断看magic，不看后缀
        assert C.is_macho(app / "Demo") and C.is_macho(app / "Frameworks" / "libswiftCore.dylib")
        assert not C.is_macho(app / "Assets.car") and not C.is_macho(app / "icon@3x.png")

        print(f"PASS  Payload/<App>.app 成型（{rec['bundle_kept_files']} 留 / {rec['bundle_dropped_files']} 丢，"
              f"{rec['bundle_full_bytes']:,} → {rec['bundle_zip_bytes']:,} 字节）："
              f"四层清单齐 / 资源按后缀记账丢弃 / 包内外符号链接分开 / 两次打包字节一致 / "
              f"「没有清单」与「打包失败」分得开 / 超预算照传只记账 / 每个 strip 档位一份包且非代码文件不动")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
