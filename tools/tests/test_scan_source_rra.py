#!/usr/bin/env python3
"""Fixtures are verbatim lines from real units, not hand-written.

Every source line below was copied out of wikimedia/wikipedia-ios, apple/swift-nio
(558f24a), Cache 6.0.0 or SDWebImage 4.x (vendored in DaidoujiChen/Dai-Hentai);
only the surrounding files were trimmed to the lines that exercise one rule each.
Each block records the recall gap it guards against -- all of them were found
by running the scanner on the real code and reading what it missed.
"""
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import scan_source_rra as S

def locate_rules():
    """RRA_RULES env, else the single cross_rra_analyzer_<ver>/rra_rules.yaml under ~/autodl-tmp.

    Two analyzer versions side by side is exactly the situation that produced
    inconsistent numbers before, so in that case the test refuses to guess.
    """
    env = os.environ.get("RRA_RULES")
    if env and pathlib.Path(env).is_file():
        return pathlib.Path(env)
    cands = sorted(pathlib.Path.home().glob("autodl-tmp/cross_rra_analyzer_*/rra_rules.yaml"))
    cands += sorted(pathlib.Path(__file__).resolve().parents[4].glob("repo/current/rra_rules.yaml"))
    if len(cands) == 1:
        return cands[0]
    raise SystemExit(f"rra_rules.yaml: set RRA_RULES (candidates: {[str(c) for c in cands]})")


RULES = locate_rules()

PBXPROJ = r'''// !$*UTF8*$!
{
	archiveVersion = 1;
	objectVersion = 56;
	objects = {
		F001 /* Settings.swift in Sources */ = {isa = PBXBuildFile; fileRef = A001 /* Settings.swift */; };
		F002 /* Constants.m in Sources */ = {isa = PBXBuildFile; fileRef = A002 /* Constants.m */; };
		F003 /* Store.m in Sources */ = {isa = PBXBuildFile; fileRef = A003 /* Store.m */; };
		F004 /* Widget.swift in Sources */ = {isa = PBXBuildFile; fileRef = A004 /* Widget.swift */; };
		F005 /* PrivacyInfo.xcprivacy in Resources */ = {isa = PBXBuildFile; fileRef = A005 /* PrivacyInfo.xcprivacy */; };
		F006 /* Shared.swift in Sources */ = {isa = PBXBuildFile; fileRef = A006 /* Shared.swift */; };
		F007 /* Shared.swift in Sources */ = {isa = PBXBuildFile; fileRef = A006 /* Shared.swift */; };
		A001 /* Settings.swift */ = {isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = Settings.swift; sourceTree = "<group>"; };
		A002 /* Constants.m */ = {isa = PBXFileReference; lastKnownFileType = sourcecode.c.objc; path = Constants.m; sourceTree = "<group>"; };
		A003 /* Store.m */ = {isa = PBXFileReference; lastKnownFileType = sourcecode.c.objc; name = Store.m; path = "Demo/Data/Store.m"; sourceTree = SOURCE_ROOT; };
		A004 /* Widget.swift */ = {isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = Widget.swift; sourceTree = "<group>"; };
		A005 /* PrivacyInfo.xcprivacy */ = {isa = PBXFileReference; lastKnownFileType = text.xml; path = PrivacyInfo.xcprivacy; sourceTree = "<group>"; };
		A006 /* Shared.swift */ = {isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = Shared.swift; sourceTree = "<group>"; };
		A007 /* Release.xcconfig */ = {isa = PBXFileReference; lastKnownFileType = text.xcconfig; path = Release.xcconfig; sourceTree = "<group>"; };
		G000 = {isa = PBXGroup; children = (G001 /* Demo */, G002 /* Widget */, G003 /* Config */, G004 /* Shared */); sourceTree = "<group>"; };
		G001 /* Demo */ = {isa = PBXGroup; children = (A001 /* Settings.swift */, A002 /* Constants.m */, A003 /* Store.m */, A005 /* PrivacyInfo.xcprivacy */); path = Demo; sourceTree = "<group>"; };
		G002 /* Widget */ = {isa = PBXGroup; children = (A004 /* Widget.swift */); path = "Demo/../Widget"; sourceTree = "<group>"; };
		G003 /* Config */ = {isa = PBXGroup; children = (A007 /* Release.xcconfig */); path = Config; sourceTree = "<group>"; };
		G004 /* Shared */ = {isa = PBXGroup; children = (A006 /* Shared.swift */); path = Shared; sourceTree = "<group>"; };
		T001 /* Demo */ = {
			isa = PBXNativeTarget;
			buildConfigurationList = L001 /* Build configuration list for PBXNativeTarget "Demo" */;
			buildPhases = (S001 /* Sources */, R001 /* Resources */);
			name = Demo;
			productName = Demo;
			productType = "com.apple.product-type.application";
		};
		T002 /* DemoWidget */ = {
			isa = PBXNativeTarget;
			buildConfigurationList = L002 /* Build configuration list for PBXNativeTarget "DemoWidget" */;
			buildPhases = (S002 /* Sources */);
			name = DemoWidget;
			productName = DemoWidget;
			productType = "com.apple.product-type.app-extension";
		};
		S001 /* Sources */ = {isa = PBXSourcesBuildPhase; files = (F001 /* Settings.swift in Sources */, F002 /* Constants.m in Sources */, F003 /* Store.m in Sources */, F006 /* Shared.swift in Sources */); };
		S002 /* Sources */ = {isa = PBXSourcesBuildPhase; files = (F004 /* Widget.swift in Sources */, F007 /* Shared.swift in Sources */); };
		R001 /* Resources */ = {isa = PBXResourcesBuildPhase; files = (F005 /* PrivacyInfo.xcprivacy in Resources */); };
		P000 /* Project object */ = {
			isa = PBXProject;
			buildConfigurationList = L000 /* Build configuration list for PBXProject "Demo" */;
			mainGroup = G000;
			targets = (T001 /* Demo */, T002 /* DemoWidget */);
		};
		C000 /* Debug */ = { isa = XCBuildConfiguration; buildSettings = { OTHER_SWIFT_FLAGS = "-DDEBUG"; GROUP_ID = group.com.acme.demo; }; name = Debug; };
		C001 /* Release */ = { isa = XCBuildConfiguration; baseConfigurationReference = A007 /* Release.xcconfig */; buildSettings = { OTHER_SWIFT_FLAGS = "$(inherited) -DNDEBUG"; GROUP_ID = group.com.acme.demo; GCC_PREPROCESSOR_DEFINITIONS = ("GROUP_ID=$(GROUP_ID)", "$(inherited)"); }; name = Release; };
		C002 /* Test */ = { isa = XCBuildConfiguration; buildSettings = { OTHER_SWIFT_FLAGS = "-DNDEBUG -DTEST"; GROUP_ID = group.com.acme.demo; }; name = Test; };
		C010 /* Debug */ = { isa = XCBuildConfiguration; buildSettings = { PRODUCT_BUNDLE_IDENTIFIER = com.acme.demo; CODE_SIGN_ENTITLEMENTS = Demo/Demo.entitlements; }; name = Debug; };
		C011 /* Release */ = { isa = XCBuildConfiguration; buildSettings = { PRODUCT_BUNDLE_IDENTIFIER = com.acme.demo; CODE_SIGN_ENTITLEMENTS = Demo/Demo.entitlements; SWIFT_ACTIVE_COMPILATION_CONDITIONS = "$(inherited) ACME_APP"; }; name = Release; };
		C012 /* Test */ = { isa = XCBuildConfiguration; buildSettings = { PRODUCT_BUNDLE_IDENTIFIER = com.acme.demo; }; name = Test; };
		C020 /* Debug */ = { isa = XCBuildConfiguration; buildSettings = { PRODUCT_BUNDLE_IDENTIFIER = com.acme.demo.widget; }; name = Debug; };
		C021 /* Release */ = { isa = XCBuildConfiguration; buildSettings = { PRODUCT_BUNDLE_IDENTIFIER = com.acme.demo.widget; SWIFT_ACTIVE_COMPILATION_CONDITIONS = "$(inherited) ACME_WIDGET"; }; name = Release; };
		C022 /* Test */ = { isa = XCBuildConfiguration; buildSettings = { PRODUCT_BUNDLE_IDENTIFIER = com.acme.demo.widget; }; name = Test; };
		L000 /* Build configuration list for PBXProject "Demo" */ = { isa = XCConfigurationList; buildConfigurations = (C000 /* Debug */, C001 /* Release */, C002 /* Test */); };
		L001 /* Build configuration list for PBXNativeTarget "Demo" */ = { isa = XCConfigurationList; buildConfigurations = (C010 /* Debug */, C011 /* Release */, C012 /* Test */); };
		L002 /* Build configuration list for PBXNativeTarget "DemoWidget" */ = { isa = XCConfigurationList; buildConfigurations = (C020 /* Debug */, C021 /* Release */, C022 /* Test */); };
	};
	rootObject = P000 /* Project object */;
}
'''

FILES = {
    # --- synthetic app repo shaped like wikipedia-ios's project files: two targets, a Release config with
    #     -DNDEBUG (xcconfig + pbxproj), a Test config with -DTEST, GCC_PREPROCESSOR_DEFINITIONS carrying a build
    #     setting into a C macro, a file compiled into both targets, a file in no target, an entitlements placeholder
    "repos/acme-Demo/Demo.xcodeproj/project.pbxproj": PBXPROJ,
    "repos/acme-Demo/Config/Release.xcconfig": "#include \"Base.xcconfig\"\nOTHER_SWIFT_FLAGS = $(inherited) -DFROM_XCCONFIG\n",
    "repos/acme-Demo/Config/Base.xcconfig": "OTHER_SWIFT_FLAGS = -DFROM_BASE\n",
    "repos/acme-Demo/Demo/Demo.entitlements": """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>com.apple.security.application-groups</key><array><string>group.com.acme.demo$(SIGNING_DISAMBIGUATOR)</string></array></dict></plist>
""",
    "repos/acme-Demo/Demo/PrivacyInfo.xcprivacy": """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>NSPrivacyAccessedAPITypes</key><array>
<dict><key>NSPrivacyAccessedAPIType</key><string>NSPrivacyAccessedAPICategoryUserDefaults</string>
<key>NSPrivacyAccessedAPITypeReasons</key><array><string>CA92.1</string></array></dict>
</array></dict></plist>
""",
    "repos/acme-Demo/Demo/Settings.swift": """import Foundation

let lastRunKey = "AcmeLastRun"

final class Settings {
    func record() {
#if TEST
        UserDefaults.standard.set("test", forKey: lastRunKey)
#else
        UserDefaults.standard.set(Date(), forKey: lastRunKey)
#endif
#if ACME_APP
        UserDefaults.standard.set(true, forKey: "AcmeIsApp")
#endif
#if FROM_XCCONFIG && FROM_BASE
        let seen = UserDefaults.standard.bool(forKey: "AcmeSeen")
#endif
    }
}
""",
    "repos/acme-Demo/Demo/Constants.m": """#import "Constants.h"
#import "QuoteMacros.h"

NSString *const AcmeGroupID = @QUOTE(GROUP_ID);
""",
    "repos/acme-Demo/Demo/Data/Store.m": """#import "Store.h"

@implementation Store
- (void)load {
    NSUserDefaults *ud = [[NSUserDefaults alloc] initWithSuiteName:AcmeGroupID];
    [ud boolForKey:@"AcmeSeen"];
}
@end
""",
    "repos/acme-Demo/Widget/Widget.swift": """import WidgetKit

struct Provider {
    func snapshot() -> String? {
        return UserDefaults(suiteName: "group.com.acme.demo")?.string(forKey: lastRunKey)
    }
}
""",
    "repos/acme-Demo/Shared/Shared.swift": """import Foundation

enum Shared {
    static func flag() -> Bool {
#if ACME_WIDGET
        return UserDefaults.standard.bool(forKey: "AcmeWidgetFlag")
#else
        return false
#endif
    }
}
""",
    "repos/acme-Demo/Demo/Dead.swift": """import Foundation

func deadCode() {
    UserDefaults.standard.removeObject(forKey: "AcmeDead")
}
""",
    # --- v3.6 ALT additions: Darwin CLOCK_MONOTONIC, the two approximate mach clocks, keyboard-extension primaryLanguage;
    #     the _RAW variants must keep their own api keys and the Linux branch must stay dead
    "deps/clockkit@abcdefabcdef/Sources/ClockKit/Clocks.swift": """import Foundation

enum Clocks {
    static func monotonic() -> UInt64 {
#if os(Linux)
        var ts = timespec(); clock_gettime(CLOCK_MONOTONIC, &ts); return UInt64(ts.tv_sec)
#else
        return clock_gettime_nsec_np(CLOCK_MONOTONIC)
#endif
    }
    static func raw() -> UInt64 { clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW) }
    static func approx() -> UInt64 { mach_approximate_time() }
    static func approxContinuous() -> UInt64 { mach_continuous_approximate_time() }
    static func continuous() -> UInt64 { mach_continuous_time() }
}
""",
    "deps/clockkit@abcdefabcdef/Sources/ClockKit/KeyboardLang.swift": """import UIKit

final class KeyboardViewController: UIInputViewController {
    var lang: String? { textDocumentProxy.documentInputMode?.primaryLanguage }
}
""",
    # --- callers of the wikipedia extension members (wrapper links)
    "repos/wikimedia-wikipedia-ios/Wikipedia/Code/Caller.swift": """import Foundation

final class Caller {
    func run() {
        UserDefaults.standard.shouldRestoreNavigationStackOnResume = true
        let tab = UserDefaults.standard.defaultTabType
        UserDefaults.standard.wmf_setAppResignActiveDate(Date())
        let d = UserDefaults.standard.wmf_appResignActiveDate()
        UserDefaults.standard.wmf_undefinedMember()
    }
}
""",
    # --- wikipedia-ios: `@objc public extension UserDefaults` (v2 regex missed the attribute prefix)
    "repos/wikimedia-wikipedia-ios/Wikipedia/Code/NSUserDefaults+WMFExtensions.swift": """import Foundation

let WMFAppResignActiveDateKey = "WMFAppResignActiveDateKey"

@objc public extension UserDefaults {
    @objc(WMFUserDefaultsKey) class Key: NSObject {
        @objc public static let defaultTabType = "WMFDefaultTabTypeKey"
    }

    @objc func wmf_dateForKey(_ key: String) -> Date? {
        return self.object(forKey: key) as? Date
    }

    @objc func wmf_appResignActiveDate() -> Date? {
        return self.wmf_dateForKey(WMFAppResignActiveDateKey)
    }

    @objc func wmf_setAppResignActiveDate(_ date: Date?) {
        if let date = date {
            self.set(date, forKey: WMFAppResignActiveDateKey)
        } else {
            self.removeObject(forKey: WMFAppResignActiveDateKey)
        }
    }

    @objc var shouldRestoreNavigationStackOnResume: Bool {
        get {
            return bool(forKey: "WMFShouldRestoreNavigationStackOnResume")
        }
        set {
            set(newValue, forKey: "WMFShouldRestoreNavigationStackOnResume")
        }
    }

    @objc var defaultTabType: WMFAppDefaultTabType {
        get {
            guard let defaultTabType = WMFAppDefaultTabType(rawValue: integer(forKey: UserDefaults.Key.defaultTabType)) else {
                return .explore
            }
            return defaultTabType
        }
    }
}
""",
    # --- wikipedia-ios: injected instance; domain decided by the two constructions in another file
    "repos/wikimedia-wikipedia-ios/WMFData/Package.swift": "// swift-tools-version:5.9\n",
    "repos/wikimedia-wikipedia-ios/WMFData/Sources/WMFData/Store/WMFUserDefaultsStore.swift": """import Foundation

class WMFUserDefaultsStore: WMFKeyValueStore {

    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    func synchronize() {
        defaults.synchronize()
    }

    private func save<T: Codable>(defaultsKey: String, value: T) throws {
        do {
            let data = try JSONEncoder().encode(value)
            defaults.set(data, forKey: defaultsKey)
        } catch let error {
            throw WMFUserDefaultsStoreError.failureEncodingJSON(error)
        }
    }
}
""",
    "repos/wikimedia-wikipedia-ios/WMFData/Sources/WMFData/Environment/WMFDataEnvironment.swift": """import Foundation

public final class WMFDataEnvironment: ObservableObject {
    private var _userDefaultsStore: WMFKeyValueStore? = WMFUserDefaultsStore()

    private var _crossProcessUserDefaultsStore: WMFKeyValueStore? = {
        guard let defaults = UserDefaults(suiteName: "group.org.wikimedia.wikipedia") else {
            return nil
        }
        return WMFUserDefaultsStore(defaults: defaults)
    }()
}
""",
    # --- wikipedia-ios ObjC: alias + suite constant defined in another file + dot-syntax wrapped member
    "repos/wikimedia-wikipedia-ios/Wikipedia/Code/MWKDataStore.m": """#import "MWKDataStore.h"

@implementation MWKDataStore

- (void)migrate {
    if (currentLibraryVersion < 8) {
        NSUserDefaults *ud = [[NSUserDefaults alloc] initWithSuiteName:WMFApplicationGroupIdentifier];
        [ud removeObjectForKey:@"WMFOpenArticleURLKey"];
        [ud synchronize];
    }
    NSUserDefaults *userDefaults = [NSUserDefaults standardUserDefaults];
    [userDefaults setObject:@"x" forKey:@"y"];
    NSUserDefaults.standardUserDefaults.wmf_didShowReadingListCardInFeed = YES;
    return [[NSUserDefaults standardUserDefaults] boolForKey:@"z"];
}

@end
""",
    "repos/wikimedia-wikipedia-ios/WMF Framework/WMFConstants.m": """NSString *const WMFApplicationGroupIdentifier = @"group.org.wikimedia.wikipedia";
""",
    "repos/wikimedia-wikipedia-ios/Wikipedia/Resources/PrivacyInfo.xcprivacy": """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>NSPrivacyAccessedAPITypes</key><array>
<dict><key>NSPrivacyAccessedAPIType</key><string>NSPrivacyAccessedAPICategoryUserDefaults</string>
<key>NSPrivacyAccessedAPITypeReasons</key><array><string>1C8F.1</string></array></dict>
</array></dict></plist>
""",
    # --- wikipedia-ios: func parameter with a default, called without the label elsewhere; custom flags TEST/UITEST
    "repos/wikimedia-wikipedia-ios/Wikipedia/Code/TestNetworkFixtures/TestNetworkFixtureInterceptor.swift": """import Foundation

final class TestNetworkFixtureInterceptor {
    @discardableResult
    public static func configureBasicServiceIfNeeded(userDefaults: UserDefaults = .standard, environment: WMFDataEnvironment = WMFDataEnvironment.current) -> Bool {
#if TEST || UITEST
        guard let urlSession = basicServiceURLSession(profileValue: userDefaults.string(forKey: profileKey)) else {
            return false
        }

        environment.basicService = WMFBasicService(urlSession: urlSession)
        return true
#else
        return false
#endif
    }
}
""",
    "repos/wikimedia-wikipedia-ios/Wikipedia/Code/WMFAppViewController+Extensions.swift": """extension WMFAppViewController {
    func setupDataEnvironment() {
        #if TEST || UITEST
            TestNetworkFixtureInterceptor.configureBasicServiceIfNeeded()
        #endif
    }
}
""",
    # --- wikipedia-ios: multi-line `guard …, let userDefaults = UserDefaults(suiteName:) else {` binds in the enclosing scope
    "repos/wikimedia-wikipedia-ios/Widgets/Widgets/RandomWidget.swift": """import WidgetKit

private extension WMFRandomWidgetViewModel.DisplaySet {

    static func dailyIndex(optionsCount: Int) -> Int {
        guard optionsCount > 0,
              let userDefaults = UserDefaults(suiteName: "group.org.wikimedia.wikipedia") else {
            return 0
        }
        let today = Calendar.current.startOfDay(for: Date())
        let indexKey = WMFUserDefaultsKey.randomWidgetDailyIndex.rawValue
        let dateKey = WMFUserDefaultsKey.randomWidgetDailyDate.rawValue
        let lastDate = userDefaults.object(forKey: dateKey) as? Date ?? .distantPast
        if Calendar.current.isDate(lastDate, inSameDayAs: today) {
            return userDefaults.integer(forKey: indexKey)
        }
        let newIndex = Int.random(in: 0..<optionsCount)
        userDefaults.set(newIndex, forKey: indexKey)
        userDefaults.set(today, forKey: dateKey)
        return newIndex
    }
}
""",
    # --- SaxWeather a746c4f: `let stationID = UserDefaults.standard.string(forKey:) ?? ""` is a String,
    #     so `stationID.isEmpty` is not a site (118 such false sites in the first full-corpus run)
    "repos/saxobroko-SaxWeather/SaxWeather/SaxWeather/ContentView.swift": """import SwiftUI

struct ContentView: View {
    private var shouldShowLocationHeader: Bool {
        let wuApiKey = KeychainService.shared.getApiKey(forService: "wu") ?? ""
        let stationID = UserDefaults.standard.string(forKey: "stationID") ?? ""
        let hasWeatherUnderground = !wuApiKey.isEmpty && !stationID.isEmpty
        if hasWeatherUnderground && !disableAPIKeys {
            return false
        }
        return true
    }
}
""",
    # --- RevenueCat 562c923: every DeviceCache access goes through a wrapper whose closure receives the UserDefaults
    #     (`self.userDefaults.write { $0.set(…) }`, `{ userDefaults in … }`); 126 sites over its three identities
    "deps/revenuecat@562c92396a18/Sources/Misc/Concurrency/SynchronizedUserDefaults.swift": """import Foundation

/// A `UserDefaults` wrapper to synchronize access and writes.
///
/// - SeeAlso: `Atomic`.
internal final class SynchronizedUserDefaults {

    private let atomic: Atomic<UserDefaults>

    init(userDefaults: UserDefaults) {
        self.atomic = .init(userDefaults)
    }

    func read<T>(_ action: (UserDefaults) throws -> T) rethrows -> T {
        return try self.atomic.withValue {
            return try action($0)
        }
    }

    func write(_ action: (UserDefaults) throws -> Void) rethrows {
        return try self.atomic.withValue {
            try action($0)
        }
    }
}
""",
    "deps/revenuecat@562c92396a18/Sources/Caching/DeviceCache.swift": """import Foundation

// swiftlint:disable file_length type_body_length
class DeviceCache {

    private let sandboxEnvironmentDetector: SandboxEnvironmentDetector
    private let userDefaults: SynchronizedUserDefaults
    private let offeringsCachedObject: InMemoryCachedObject<Offerings>

    private let _cachedAppUserID: Atomic<String?>
    private let _cachedLegacyAppUserID: Atomic<String?>

    init(sandboxEnvironmentDetector: SandboxEnvironmentDetector,
         userDefaults: UserDefaults,
         offeringsCachedObject: InMemoryCachedObject<Offerings> = .init()) {
        self.sandboxEnvironmentDetector = sandboxEnvironmentDetector
        self.offeringsCachedObject = offeringsCachedObject
        self.userDefaults = .init(userDefaults: userDefaults)
        self._cachedAppUserID = .init(userDefaults.string(forKey: CacheKeys.appUserDefaults))
        self._cachedLegacyAppUserID = .init(userDefaults.string(forKey: CacheKeys.legacyGeneratedAppUserDefaults))
    }

    // MARK: - generic methods

    func update<Key: DeviceCacheKeyType, Value: Codable>(
        key: Key,
        default defaultValue: Value,
        updater: @Sendable (inout Value) -> Void
    ) {
        self.userDefaults.write {
            var value: Value = $0.value(forKey: key) ?? defaultValue
            updater(&value)
            $0.set(codable: value, forKey: key)
        }
    }

    func value<Key: DeviceCacheKeyType, Value: Codable>(for key: Key) -> Value? {
        self.userDefaults.read {
            $0.value(forKey: key)
        }
    }

    func clearCaches(oldAppUserID: String, andSaveWithNewUserID newUserID: String) {
        self.userDefaults.write { userDefaults in
            userDefaults.removeObject(forKey: CacheKeys.legacyGeneratedAppUserDefaults)
            userDefaults.removeObject(
                forKey: CacheKey.customerInfo(oldAppUserID)
            )

            // Cache new appUserID.
            userDefaults.set(newUserID, forKey: CacheKeys.appUserDefaults)
            self._cachedAppUserID.value = newUserID
            self._cachedLegacyAppUserID.value = nil
        }
    }

    func cachedCustomerInfoData(appUserID: String) -> Data? {
        return self.userDefaults.read {
            $0.data(forKey: CacheKey.customerInfo(appUserID))
        }
    }

    static func deleteSyncedSubscriberAttributesForOtherUsers(
        _ userDefaults: UserDefaults
    ) {
        let allStoredAttributes: [String: [String: Any]]
        = userDefaults.dictionary(forKey: CacheKeys.subscriberAttributes)
        as? [String: [String: Any]] ?? [:]

        var filteredAttributes: [String: Any] = [:]

        userDefaults.set(filteredAttributes, forKey: CacheKeys.subscriberAttributes)
    }

    static func productEntitlementMappingLastUpdated(_ userDefaults: UserDefaults) -> Date? {
        return userDefaults.date(forKey: CacheKeys.productEntitlementMappingLastUpdated)
    }
}
""",
    # --- foqos: the construction spans three lines; the static property is used from static funcs
    "repos/awaseem-foqos/Foqos/Utils/SoftUnblockGrantStore.swift": """import Foundation

enum SoftUnblockGrantStore {
  private static let suite = UserDefaults(
    suiteName: "group.dev.ambitionsoftware.foqos"
  )!

  private static let activeSessionKey = "softUnblock.activeSession"

  static func currentSession(at date: Date) -> SoftUnblockSessionState? {
    guard let data = suite.data(forKey: activeSessionKey) else { return nil }
    guard var session = try? JSONDecoder().decode(SoftUnblockSessionState.self, from: data) else {
      return nil
    }
    return session
  }
}
""",
    # --- wBlock: `guard let defaults = UserDefaults(suiteName: g),` continues with another clause on the next line;
    #     `UserDefaults(suiteName:)?.set(…)` is a direct site (optional chaining)
    "repos/0xCUB3-wBlock/wBlockCoreService/BlockingPauseStore.swift": """import Foundation

public enum BlockingPauseStore {
    public static func consumeResumeRequest(groupIdentifier: String = GroupIdentifier.shared.value) -> Bool {
        guard let defaults = UserDefaults(suiteName: groupIdentifier),
              defaults.bool(forKey: resumeRequestKey)
        else { return false }
        defaults.set(false, forKey: resumeRequestKey)
        return true
    }

    public static func setResumeApplying(groupIdentifier: String = GroupIdentifier.shared.value) {
        UserDefaults(suiteName: groupIdentifier)?.set(ResumeStatus.applying.rawValue, forKey: resumeStatusKey)
    }
}
""",
    # --- swift-nio: whole C file under #ifdef __linux__; per-target manifest; NIODeadline wrapper member
    "deps/swift-nio@558f24a46471/Sources/CNIOLinux/shim.c": """#ifdef __linux__
f_type_t CNIOLinux_statfs_ftype(const char *path) {
    struct statfs fs;
    f_type_t f_type = 0;
    if (statfs(path, &fs) == 0) {
        f_type = fs.f_type;
    }
    return f_type;
}
#endif
""",
    "deps/swift-nio@558f24a46471/Sources/NIOCore/EventLoop.swift": """public struct NIODeadline: Equatable, Hashable, Sendable {
    @inlinable
    public var uptimeNanoseconds: UInt64 {
        return self._uptimeNanoseconds
    }

    @inlinable
    public static func now() -> NIODeadline {
        return NIODeadline.uptimeNanoseconds(NIODeadline.timeNow())
    }

    @inlinable
    static func timeNow() -> UInt64 {
        #if os(Linux)
        var ts = timespec()
        clock_gettime(CLOCK_MONOTONIC, &ts)
        return UInt64(ts.tv_sec) &* 1_000_000_000 &+ UInt64(ts.tv_nsec)
        #elseif os(WASI)
        var ts = timespec()
        CNIOWASI_gettime(&ts)
        return UInt64(ts.tv_sec) &* 1_000_000_000 &+ UInt64(ts.tv_nsec)
        #else
        return DispatchTime.now().uptimeNanoseconds
        #endif  // os(Linux)
    }

    public static func < (lhs: NIODeadline, rhs: NIODeadline) -> Bool {
        return lhs.uptimeNanoseconds < rhs.uptimeNanoseconds
    }
}
""",
    "deps/swift-nio@558f24a46471/Sources/NIOPosix/System.swift": """private let sysFstat = fstat
private let sysStat = stat
private let sysLstat = lstat

internal enum Posix {
    @inline(never)
    internal static func fstat(descriptor: CInt, outStat: UnsafeMutablePointer<stat>) throws {
        _ = try syscall(blocking: false) {
            sysFstat(descriptor, outStat)
        }
    }
}
""",
    "deps/swift-nio@558f24a46471/Sources/NIOPosix/PrivacyInfo.xcprivacy": """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>NSPrivacyAccessedAPITypes</key><array>
<dict><key>NSPrivacyAccessedAPIType</key><string>NSPrivacyAccessedAPICategoryFileTimestamp</string>
<key>NSPrivacyAccessedAPITypeReasons</key><array><string>0A2A.1</string></array></dict>
</array></dict></plist>
""",
    # --- Cache 6.0.0: bare members (WEAK) that are all real; WRITE via setAttributes
    "pods/Cache@6.0.0/Source/Shared/Storage/DiskStorage.swift": """import Foundation

final class DiskStorage<Key: Hashable, Value> {
  func entry(forKey key: Key) throws -> Entry<Value> {
    let attributes = try fileManager.attributesOfItem(atPath: filePath)
    guard let date = attributes[.modificationDate] as? Date else {
      throw StorageError.malformedFileAttributes
    }
    try fileManager.setAttributes([.modificationDate: expiry.date], ofItemAtPath: filePath)
    let resourceKeys: [URLResourceKey] = [
      .isDirectoryKey,
      .contentModificationDateKey,
      .totalFileAllocatedSizeKey
    ]
  }
}
""",
    # --- SDWebImage vendored in a repo's Pods/: project macros derived from TARGET_OS_* in a header
    "repos/DaidoujiChen-Dai-Hentai/Pods/SDWebImage/SDWebImage/SDWebImageCompat.h": """#if !TARGET_OS_IPHONE && !TARGET_OS_IOS && !TARGET_OS_TV && !TARGET_OS_WATCH
    #define SD_MAC 1
#else
    #define SD_MAC 0
#endif

#if TARGET_OS_IOS || TARGET_OS_TV
    #define SD_UIKIT 1
#else
    #define SD_UIKIT 0
#endif
""",
    "repos/DaidoujiChen-Dai-Hentai/Pods/SDWebImage/SDWebImage/SDImageCache.m": """#import "SDImageCache.h"
@implementation SDImageCache
- (void)deleteOldFilesWithCompletionBlock:(SDWebImageNoParamsBlock)completionBlock {
#if SD_UIKIT
        NSArray<NSString *> *resourceKeys = @[NSURLIsDirectoryKey, NSURLContentModificationDateKey, NSURLTotalFileAllocatedSizeKey];
#endif
#if SD_MAC
        NSDate *modificationDate = resourceValues[NSURLContentModificationDateKey];
#endif
}
@end
""",
}


def build(root):
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def load(out, name):
    return [json.loads(l) for l in io.open(out / name, encoding="utf-8")]


def find(sites, file_suffix, line=None, tag=None):
    r = [s for s in sites if s["file"].endswith(file_suffix) and (line is None or s["line"] == line) and (tag is None or s["tag"] == tag)]
    return r


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="scan_rra_"))
    try:
        src = tmp / "src"; out = tmp / "ws"
        build(src)
        buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
        try:
            rc = S.main(["--rules", str(RULES), "--src", str(src), "--out-dir", str(out)])
        finally:
            sys.stdout = old
        assert rc == 0, buf.getvalue()
        wiki = load(out, "repos__wikimedia-wikipedia-ios.jsonl")
        nio = load(out, "deps__swift-nio@558f24a46471.jsonl")
        cache = load(out, "pods__Cache@6.0.0.jsonl")
        dh = load(out, "repos__DaidoujiChen-Dai-Hentai.jsonl")
        units = json.load(io.open(out / "_units.json", encoding="utf-8"))["units"]

        # 1. @objc public extension: every implicit-self member call is a site, the declaration line is NA
        ext = find(wiki, "NSUserDefaults+WMFExtensions.swift")
        body = [s for s in ext if s["hint"] == "EXTENSION_BODY_IMPLICIT_SELF"]
        assert sorted((s["line"], s["operation_prefill"]) for s in body) == \
            [(11, "READ"), (20, "WRITE"), (22, "REMOVE"), (28, "READ"), (31, "WRITE"), (37, "READ")], sorted((s["line"], s["operation_prefill"]) for s in body)
        assert [s["hint"] for s in ext if s["line"] == 5] == ["TYPE_EXTENSION_DECLARATION"]
        assert all(s["domain_hint"] == "SELF_INSTANCE" for s in body)
        # `UserDefaults.Key.defaultTabType` is a nested type reference, not a use
        assert [s["hint"] for s in ext if s["line"] == 37 and s["tag"] == "UserDefaults"] == ["NESTED_TYPE_REFERENCE:Key"]
        # wrapped call through self (`self.wmf_dateForKey`) is not an extension-body family site
        assert not [s for s in body if s["line"] == 15]

        # 2. injected instance -> alias sites with MIXED_DOMAINS from constructions in another file
        store = find(wiki, "WMFUserDefaultsStore.swift")
        alias = [s for s in store if s["hint"] == "ALIAS_MEMBER_CALL"]
        assert sorted((s["line"], s["operation_prefill"]) for s in alias) == [(12, "SYNC"), (18, "WRITE")]
        for s in alias:
            assert s["domain_hint"] == "MIXED_DOMAINS:APP_GROUP|APP_PRIVATE", s["domain_hint"]
            doms = {(d["loc"].split("/")[-1], d["domain"]) for d in s["instance_domains"]}
            assert doms == {("WMFDataEnvironment.swift:4", "APP_PRIVATE"), ("WMFDataEnvironment.swift:10", "APP_GROUP")}, doms
        assert [s["hint"] for s in store if s["line"] == 5] == ["TYPE_ANNOTATION_OR_PROPERTY_DECL"]
        assert [(s["hint"], s["domain_hint"]) for s in store if s["line"] == 7] == [("INJECTION_PARAMETER", "APP_PRIVATE")]
        # local package unit, covered by the app-level manifest (fallback), unit kind LOCAL_PKG
        assert store[0]["unit_kind"] == "LOCAL_PKG" and store[0]["unit"] == "WMFData/WMFData", (store[0]["unit_kind"], store[0]["unit"])
        assert store[0]["manifest_scope"] == "APP_LEVEL_FALLBACK_LOCAL_PKG" and store[0]["declared_reasons"] == {"UserDefaults": ["1C8F.1"]}

        # 2b. func parameter: the site inside the body is an alias site; its domain comes from the caller
        #     (called without the label -> the default `.standard`); the custom flags TEST/UITEST are unknown, not false
        tnf = find(wiki, "TestNetworkFixtureInterceptor.swift")
        assert [(s["hint"], s["line"]) for s in tnf] == [("INJECTION_PARAMETER", 5), ("ALIAS_MEMBER_CALL", 7)], [(s["hint"], s["line"]) for s in tnf]
        site = tnf[1]
        assert site["alias_of"]["form"] == "FUNC_PARAMETER" and site["operation_prefill"] == "READ"
        assert site["domain_hint"] == "APP_PRIVATE" and site["instance_domains"][0]["loc"].endswith("WMFAppViewController+Extensions.swift:4"), site["instance_domains"]
        assert site["instance_domains"][0]["form"] == "CALL_DEFAULT"
        assert site["guard_live_on_ios"] is None and site["compile_guard"] == ["#if TEST || UITEST"]
        # 2d. guard-let alias with the `else {` on the declaration line stays in scope after the guard;
        #     the widget path is only a hint (target_hint), the unit stays APP
        rw = find(wiki, "RandomWidget.swift")
        assert sorted((s["line"], s["hint"].split(":")[0], s["operation_prefill"]) for s in rw) == \
            [(7, "ACQUIRE_CANDIDATE", "ACQUIRE"), (13, "ALIAS_MEMBER_CALL", "READ"), (15, "ALIAS_MEMBER_CALL", "READ"),
             (18, "ALIAS_MEMBER_CALL", "WRITE"), (19, "ALIAS_MEMBER_CALL", "WRITE")], sorted((s["line"], s["hint"], s["operation_prefill"]) for s in rw)
        assert all(s["domain_hint"] == "APP_GROUP" for s in rw), [s["domain_hint"] for s in rw]
        assert rw[0]["target_hint"] == "PATH_SUGGESTS_OTHER_TARGET:Widgets" and rw[0]["unit"] == "APP"
        # 2c. a value read from defaults is not an instance: no alias sites from `stationID.isEmpty`
        sax = load(out, "repos__saxobroko-SaxWeather.jsonl")
        assert [(s["line"], s["hint"]) for s in sax] == [(6, "FAMILY_MEMBER:string")], [(s["line"], s["hint"]) for s in sax]

        # 2f. multi-line construction: `UserDefaults(\n suiteName: …\n)!` is still an instance; property scope is the file
        fq = load(out, "repos__awaseem-foqos.jsonl")
        assert sorted((s["line"], s["hint"].split(":")[0], s["operation_prefill"], s["domain_hint"]) for s in fq) == \
            [(4, "ACQUIRE_CANDIDATE", "ACQUIRE", "APP_GROUP"), (11, "ALIAS_MEMBER_CALL", "READ", "APP_GROUP")], sorted((s["line"], s["hint"], s["operation_prefill"], s["domain_hint"]) for s in fq)

        # 2g. guard clause with a trailing comma keeps the alias; optional-chained construction is a direct site
        wb = load(out, "repos__0xCUB3-wBlock.jsonl")
        assert sorted((s["line"], s["hint"].split(":")[0], s["operation_prefill"]) for s in wb) == \
            [(5, "ACQUIRE_CANDIDATE", "ACQUIRE"), (6, "ALIAS_MEMBER_CALL", "READ"), (8, "ALIAS_MEMBER_CALL", "WRITE"),
             (13, "FAMILY_MEMBER", "WRITE")], sorted((s["line"], s["hint"], s["operation_prefill"]) for s in wb)

        # 2e. wrapper closures: `$0` and `{ userDefaults in` bind a UserDefaults inside the closure body only;
        #     the init parameter is in scope in the init body only; `self.userDefaults.write` itself is a wrapper call
        rc = load(out, "deps__revenuecat@562c92396a18.jsonl")
        dc = [s for s in rc if s["file"].endswith("DeviceCache.swift")]
        got = sorted((s["line"], s["tag"], s["operation_prefill"], s["alias_of"]["form"]) for s in dc if s["hint"] == "ALIAS_MEMBER_CALL")
        assert got == [(19, "userDefaults.string", "READ", "INIT_PARAMETER"), (20, "userDefaults.string", "READ", "INIT_PARAMETER"),
                       (31, "$0.value", "READ", "CLOSURE_PARAMETER"), (33, "$0.set", "WRITE", "CLOSURE_PARAMETER"),
                       (39, "$0.value", "READ", "CLOSURE_PARAMETER"),
                       (45, "userDefaults.removeObject", "REMOVE", "CLOSURE_PARAMETER"), (46, "userDefaults.removeObject", "REMOVE", "CLOSURE_PARAMETER"),
                       (51, "userDefaults.set", "WRITE", "CLOSURE_PARAMETER"),
                       (59, "$0.data", "READ", "CLOSURE_PARAMETER"),
                       (67, "userDefaults.dictionary", "READ", "FUNC_PARAMETER"), (72, "userDefaults.set", "WRITE", "FUNC_PARAMETER"),
                       (76, "userDefaults.date", "WRAPPED?", "FUNC_PARAMETER")], got
        # positional parameter (`_ userDefaults`) on its own line, no trailing comma: in scope for the body, domain unresolved
        assert [s["instance_domains"][0]["note"] for s in dc if s["line"] == 67] == ["positional parameter, argument not resolved"] or \
            "no call found" in [s["instance_domains"][0]["note"] for s in dc if s["line"] == 67][0]
        assert all(s["alias_of"]["callable"] == "SynchronizedUserDefaults.write" for s in dc if s["line"] in (31, 33, 45, 46, 51))
        assert all(s["domain_hint"] == "UNKNOWN" for s in dc if s["hint"] == "ALIAS_MEMBER_CALL" and s["line"] >= 31)
        # the wrapper's own body: the atomic property is a type annotation, `action($0)` is not a family call
        su = [s for s in rc if s["file"].endswith("SynchronizedUserDefaults.swift")]
        assert sorted((s["line"], s["hint"]) for s in su) == [(8, "TYPE_ANNOTATION_OR_PROPERTY_DECL"), (10, "INJECTION_PARAMETER"),
                                                              (14, "TYPE_ANNOTATION_OR_PROPERTY_DECL"), (20, "TYPE_ANNOTATION_OR_PROPERTY_DECL")], sorted((s["line"], s["hint"]) for s in su)
        assert units["deps/revenuecat@562c92396a18"]["wrapper_methods"] == {"read": ["SynchronizedUserDefaults"], "write": ["SynchronizedUserDefaults"]}

        # 3. ObjC alias, suite constant resolved from another file, dot-syntax wrapped member, bracket family member
        m = find(wiki, "MWKDataStore.m")
        ud = [s for s in m if s["alias_of"] and s["alias_of"]["alias"] == "ud"]
        assert sorted((s["line"], s["operation_prefill"], s["domain_hint"]) for s in ud) == [(8, "REMOVE", "APP_GROUP"), (9, "SYNC", "APP_GROUP")], sorted((s["line"], s["operation_prefill"], s["domain_hint"]) for s in ud)
        assert ud[0]["instance_domains"][0]["note"].startswith("SUITE_CONST:WMFApplicationGroupIdentifier='group.org.wikimedia.wikipedia' @ WMF Framework/WMFConstants.m:"), ud[0]["instance_domains"][0]["note"]
        assert [(s["hint"], s["operation_prefill"]) for s in m if s["line"] == 7] == [("TYPE_ANNOTATION_OR_PROPERTY_DECL", "NA"), ("ACQUIRE_CANDIDATE", "ACQUIRE")]
        assert [s["operation_prefill"] for s in m if s["line"] == 12] == ["WRITE"]              # [userDefaults setObject:]
        assert [s["hint"] for s in m if s["line"] == 13] == ["POSSIBLE_WRAPPED_MEMBER:wmf_didShowReadingListCardInFeed"]
        assert [s["hint"] for s in m if s["line"] == 14] == ["FAMILY_MEMBER:boolForKey"]

        # 4. guard chains: statfs in #ifdef __linux__ is dead; DispatchTime in the #else of os(Linux)/os(WASI) is live
        shim = find(nio, "shim.c")
        assert [(s["tag"], s["guard_live_on_ios"], s["compile_guard"], s["hint"]) for s in shim] == [("statfs", False, ["#ifdef __linux__"], None)], shim
        ev = find(nio, "EventLoop.swift")
        # v3.6: the Linux-branch clock_gettime(CLOCK_MONOTONIC) is an ALT candidate too -- dead on iOS, which the guard says
        assert [(s["site_class"], s["line"], s["api"], s["guard_live_on_ios"]) for s in ev] == \
            [("ALT", 16, "alt.clock_gettime.monotonic", False), ("ALT", 23, "alt.dispatch_time.uptime_nanoseconds", True)], \
            [(s["site_class"], s["line"], s["api"]) for s in ev]
        ev = [s for s in ev if s["line"] == 23]
        assert ev[0]["compile_guard"] == ["#if os(Linux)", "#elseif os(WASI)", "#else"] and ev[0]["alt_tier"] == "NEAR_EQUIVALENT"
        # per-target manifest: NIOPosix declares 0A2A.1, CNIOLinux and NIOCore have no covering manifest
        sysw = find(nio, "System.swift")
        # the three function-reference bindings are candidates (WEAK, tag "=stat", hint FUNCTION_REFERENCE); the
        # wrapper declaration line `static func fstat(...)` stays a candidate but is prefilled FUNCTION_DECLARATION / NA
        assert sorted((s["line"], s["tag"], s["tier"], s["hint"], s["operation_prefill"]) for s in sysw) == \
            [(1, "=fstat", "WEAK", "FUNCTION_REFERENCE", "READ"), (2, "=stat", "WEAK", "FUNCTION_REFERENCE", "READ"),
             (3, "=lstat", "WEAK", "FUNCTION_REFERENCE", "READ"), (7, "fstat", "STRONG", "FUNCTION_DECLARATION", "NA")], \
            sorted((s["line"], s["tag"], s["tier"], s["hint"], s["operation_prefill"]) for s in sysw)
        assert sysw[0]["declared_reasons"] == {"FileTimestamp": ["0A2A.1"]} and sysw[0]["manifest_scope"] == "NEAREST_ANCESTOR"
        assert "R0A2A_1" in json.dumps(sysw[0]["reason_constraints"]) or sysw[0]["reason_constraints"]["FileTimestamp"]["0A2A.1"]["constraints"]
        assert shim[0]["declared_reasons"] == {"DiskSpace": []} and shim[0]["manifest_scope"] == "NO_COVERING_MANIFEST_IN_SPM"
        assert sysw[0]["unit"] == "swift-nio/NIOPosix" and sysw[0]["unit_kind"] == "SPM"

        # 5. Cache: bare members are WEAK; WRITE prefill on setAttributes; key request site
        assert sorted((s["line"], s["tier"], s["operation_prefill"]) for s in cache) == \
            [(6, "WEAK", "READ"), (9, "WEAK", "WRITE"), (12, "STRONG", "READ")], sorted((s["line"], s["tier"], s["operation_prefill"]) for s in cache)
        assert cache[0]["manifest_scope"] == "NO_MANIFEST_IN_POD" and cache[0]["unit_kind"] == "POD"

        # 6. project macros from a header decide liveness in a vendored pod; unit is LOCAL_POD scoped to Pods/<Name>
        assert units["repos/DaidoujiChen-Dai-Hentai"]["unit_macros"] == {"SD_MAC": False, "SD_UIKIT": True}
        assert sorted((s["line"], s["guard_live_on_ios"]) for s in dh) == [(5, True), (8, False)]
        assert dh[0]["unit"] == "POD:SDWebImage" and dh[0]["unit_kind"] == "LOCAL_POD" and dh[0]["manifest_scope"] == "NO_MANIFEST_IN_POD"
        assert dh[0]["unit_role_prefill"] == "THIRD_PARTY"

        # 8. build facts (xcodeproj_facts): flags per target decide custom guards; build-setting macro resolves a constant;
        #    file -> target gives declaring_unit_prefill, the target's own manifest, entitlement app groups
        acme = load(out, "repos__acme-Demo.jsonl")
        st = find(acme, "Settings.swift")
        assert sorted((s["line"], s["guard_live_on_ios"]) for s in st) == [(8, False), (10, True), (13, True), (16, True)], sorted((s["line"], s["guard_live_on_ios"], s["compile_guard"]) for s in st)
        assert st[0]["build_flags"] == {"source": "pbxproj:Release", "swift_true": ["ACME_APP", "FROM_BASE", "FROM_XCCONFIG", "NDEBUG"], "closed": True}, st[0]["build_flags"]
        assert st[0]["target"]["name"] == "Demo" and st[0]["target"]["kind"] == "APP" and st[0]["declaring_unit_prefill"] == "Demo"
        assert st[0]["target"]["app_groups"] == ["group.com.acme.demo"] and st[0]["target"]["bundle_id"] == "com.acme.demo", st[0]["target"]
        assert st[0]["manifest_scope"] == "TARGET_RESOURCE" and st[0]["unit_manifest"] == "Demo/PrivacyInfo.xcprivacy" and st[0]["declared_reasons"] == {"UserDefaults": ["CA92.1"]}
        assert [s["key"] for s in st if s["line"] == 10] == [{"expr": "lastRunKey", "value": "AcmeLastRun", "source": "CONST:lastRunKey @ Demo/Settings.swift:3"}], [s["key"] for s in st if s["line"] == 10]
        sm = [s for s in find(acme, "Store.m") if s["alias_of"]]
        assert sm and sm[0]["domain_hint"] == "APP_GROUP" and "build macro GROUP_ID from Demo.xcodeproj:Demo:Release" in sm[0]["instance_domains"][0]["note"], sm[0]["instance_domains"]
        assert find(acme, "Store.m")[0]["target"]["name"] == "Demo"                       # SOURCE_ROOT reference, group path ignored
        wg = find(acme, "Widget.swift")
        assert wg[0]["target"]["name"] == "DemoWidget" and wg[0]["declaring_unit_prefill"] == "DemoWidget" and wg[0]["target"]["kind"] == "APP_EXTENSION"
        assert wg[0]["manifest_scope"] == "APP_LEVEL_FALLBACK_EXTENSION_TARGET", wg[0]["manifest_scope"]
        sh = find(acme, "Shared.swift")
        assert sh[0]["target"]["name"] == "Demo" and sh[0]["target"]["also_in"] == ["DemoWidget"]
        assert sh[0]["guard_live_on_ios"] is None and sh[0]["build_flags"]["closed"] is False     # ACME_WIDGET is true in one target, false in the other
        dead = find(acme, "Dead.swift")
        assert dead[0]["target"] == {"project": None, "name": None, "kind": None, "manifests": [], "note": "NOT_IN_ANY_XCODE_TARGET"}, dead[0]["target"]
        bf = units["repos/acme-Demo"]["build_facts"]
        assert bf["primary_app_target"] == ["Demo.xcodeproj", "Demo"] and [t["name"] for t in bf["projects"][0]["targets"]] == ["Demo", "DemoWidget"]

        # 9. wrapper links: WRAPPED sites point at the member definition (access, keys); body sites carry their callers
        cl = find(wiki, "Caller.swift")
        by_line = {s["line"]: s for s in cl}
        assert by_line[5]["wrapper_ref"][0]["access"] == "SET" and by_line[5]["wrapper_ref"][0]["has_setter"] is True
        assert by_line[5]["wrapper_ref"][0]["keys"][0]["value"] == "WMFShouldRestoreNavigationStackOnResume"
        assert by_line[6]["wrapper_ref"][0]["access"] == "GET" and by_line[6]["wrapper_ref"][0]["keys"][0]["value"] == "WMFDefaultTabTypeKey", by_line[6]["wrapper_ref"]
        assert by_line[7]["wrapper_ref"][0]["access"] == "CALL" and by_line[7]["wrapper_ref"][0]["kind"] == "FUNC"
        assert by_line[8]["wrapper_ref"][0]["calls"] == ["wmf_dateForKey"]
        assert by_line[9]["wrapper_ref"] is None and "wmf_undefinedMember" in by_line[9]["wrapper_note"]
        body = {s["line"]: s for s in find(wiki, "NSUserDefaults+WMFExtensions.swift") if s["hint"] == "EXTENSION_BODY_IMPLICIT_SELF"}
        assert body[11]["callers"]["member"] == "wmf_dateForKey" and body[11]["callers"]["via_members"] == ["wmf_appResignActiveDate"]
        assert body[11]["callers"]["n"] == 1 and body[11]["callers"]["by_domain"] == {"APP_PRIVATE": 1}, body[11]["callers"]
        assert body[31]["callers"]["member"] == "shouldRestoreNavigationStackOnResume" and body[31]["callers"]["sites"][0]["access"] == "SET"
        assert body[37]["callers"]["n"] == 1 and body[37]["member"]["kind"] == "VAR" and body[37]["member"]["has_setter"] is False
        assert body[11]["key"] == {"expr": "key", "value": None, "source": "UNRESOLVED_IDENTIFIER"} and body[20]["key"]["value"] == "WMFAppResignActiveDateKey"
        assert sorted(units["repos/wikimedia-wikipedia-ios"]["ud_members"]) == ["defaultTabType", "setShouldRestoreNavigationStackOnResume", "shouldRestoreNavigationStackOnResume", "wmf_appResignActiveDate", "wmf_dateForKey", "wmf_setAppResignActiveDate"], sorted(units["repos/wikimedia-wikipedia-ios"]["ud_members"])
        # ObjC dot-syntax wrapped member without a definition in the unit
        assert [s["wrapper_ref"] for s in find(wiki, "MWKDataStore.m") if s["line"] == 13] == [None]

        # 10. context: the enclosing function, numbered, signature always present
        ctx = by_line[6]["context"]
        assert ctx["function_lines"] == [4, 10] and ctx["lines"][0].startswith("    4   ") and any(l.startswith("    6>> ") for l in ctx["lines"]), ctx

        # 11. v3.6 ALT rows: api keys per form, Linux branch dead, _RAW variants untouched
        ck = load(out, "deps__clockkit@abcdefabcdef.jsonl")
        got = sorted((s["file"].split("/")[-1], s["line"], s["api"], s["guard_live_on_ios"]) for s in ck)
        assert got == [("Clocks.swift", 6, "alt.clock_gettime.monotonic", False), ("Clocks.swift", 8, "alt.clock_gettime.monotonic", True),
                       ("Clocks.swift", 11, "alt.clock_gettime.monotonic_raw", True), ("Clocks.swift", 12, "alt.mach_approximate_time", True),
                       ("Clocks.swift", 13, "alt.mach_continuous_approximate_time", True), ("Clocks.swift", 14, "alt.mach_continuous_time", True),
                       ("KeyboardLang.swift", 4, "alt.text_document_proxy.primary_language", True)], got
        assert all(s["site_class"] == "ALT" for s in ck) and {s["alt_tier"] for s in ck} == {"NEAR_EQUIVALENT", "PARTIAL_DATUM"}

        # 7. identity: site_id unique and stable
        allsites = wiki + nio + cache + dh + sax + rc + fq + wb + acme + ck
        ids = [s["site_id"] for s in allsites]
        assert len(ids) == len(set(ids))
        for s in allsites:
            assert s["site_class"] in ("RRA", "ALT") and s["category"] and s["categories"]
            marked = [l for l in s["context"]["lines"] if l.startswith(f"{s['line']:5d}>>")]
            assert len(marked) == 1 and marked[0][8:] == open(pathlib.Path(tmp) / "src" / s["unit_location"] / s["file"], encoding="utf-8").read().splitlines()[s["line"] - 1], (s["file"], s["line"], s["context"]["lines"][:3])
        print(f"PASS  {len(allsites)} 个站点：扩展体隐式 self / 注入实例 MIXED_DOMAINS / func 参数经调用方定域 / 封装闭包参数($0 / x in) / 值别名不是站点 / ObjC 别名+常量解析 / 守卫链 / 目标级清单 / WEAK 档 / 工程宏")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
