#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Every row of the §4.5 domain table and the §4.7 ALT table, plus the three
shapes that made "first source that answers wins" the wrong resolution:
a `?? .standard` fallback, a negated mention, and an extension body whose
domain only its callers know."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import rule_table as T  # noqa: E402

GROUPS = {"group.org.demo.app", "group.org.demo.app.beta"}
P_PRIVATE = "DefaultsDomainIs(APP_PRIVATE)"
P_GROUP = "DefaultsDomainIs(APP_GROUP)"
P_PART = "AllIntendedParticipantsAreMembersOfSameAppGroup()"


def ev(kind, **kw):
    return T.DomainEvidence(kind, "test", **kw)


def site(**kw):
    base = {"site_id": "s", "site_class": "RRA", "domain_hint": None, "instance_domains": None}
    base.update(kw)
    return base


def rec(**kw):
    base = {"site_id": "s", "site_class": "RRA", "is_api_use": "YES", "notes": "",
            "fate_evidence": "", "is_api_use_reason": "", "value_fate": [], "escape": None}
    base.update(kw)
    return base


def header(app_groups=GROUPS, legacy=False):
    if legacy:
        return {"hosts": [{"app_facts": {"app_group_ids": sorted(app_groups)}}]}
    return {"build_facts": {"projects": [{"targets": [
        {"name": "Demo", "app_groups": sorted(app_groups)},
    ]}]}}


def main():
    # ---------------------------------------------------------------- §4.5 table
    std = ev(T.DOM_STANDARD)
    nongroup = ev(T.DOM_SUITE, suite="test.demo.x", name_says_group=False, in_entitlements=False)
    named_in = ev(T.DOM_SUITE, suite="group.org.demo.app", name_says_group=True, in_entitlements=True)
    named_out = ev(T.DOM_SUITE, suite="group.other.thing", name_says_group=True, in_entitlements=False)
    glob = ev(T.DOM_GLOBAL_OR_FOREIGN)
    unres = ev(T.DOM_SUITE_UNRESOLVED)
    unk = ev(T.DOM_UNKNOWN)
    mixed = ev(T.DOM_MIXED)

    rows = [
        # (predicate, evidence,      1.10,        1.11)
        (P_PRIVATE, std,             "SUPPORTED", "SUPPORTED"),
        (P_PRIVATE, nongroup,        "CONFLICT",  "SUPPORTED"),   # the 1.11 rewrite
        (P_PRIVATE, named_in,        "CONFLICT",  "CONFLICT"),
        (P_PRIVATE, named_out,       "CONFLICT",  "SUPPORTED"),   # name says group, entitlement does not
        (P_PRIVATE, glob,            "CONFLICT",  "CONFLICT"),
        (P_PRIVATE, unres,           None,        None),      # scanner could not fold it; annotator may have
        (P_PRIVATE, unk,             None,        None),
        (P_PRIVATE, mixed,           None,        None),
        (P_GROUP,   std,             "CONFLICT",  "CONFLICT"),
        (P_GROUP,   nongroup,        "CONFLICT",  "CONFLICT"),
        (P_GROUP,   named_in,        "SUPPORTED", "SUPPORTED"),
        (P_GROUP,   named_out,       "SUPPORTED", "CONFLICT"),    # 1.10 reads the name, 1.11 the entitlement
        (P_GROUP,   glob,            "CONFLICT",  "CONFLICT"),
        (P_GROUP,   unres,           None,        None),
        (P_PART,    std,             "SUPPORTED", "SUPPORTED"),   # the 337-pair row
        (P_PART,    named_in,        "SUPPORTED", "SUPPORTED"),
        (P_PART,    named_out,       "CONFLICT",  "CONFLICT"),
        (P_PART,    nongroup,        None,        None),          # table is silent
        (P_PART,    glob,            "CONFLICT",  "CONFLICT"),
        (P_PART,    unres,           None,        None),
    ]
    for pred, e, want10, want11 in rows:
        got10 = T.expected_domain_verdict(pred, e, GROUPS, "1.10")
        got11 = T.expected_domain_verdict(pred, e, GROUPS, "1.11")
        assert got10 == want10, (pred, e.kind, e.suite, "1.10", got10, want10)
        assert got11 == want11, (pred, e.kind, e.suite, "1.11", got11, want11)
    # a predicate that is not a domain row gets nothing at all
    assert T.expected_domain_verdict("NoReadFrom(OTHER_APP | SYSTEM)", std, GROUPS, "1.11") is None
    assert not T.is_domain_predicate("NoReadFrom(OTHER_APP | SYSTEM)")
    assert all(T.is_domain_predicate(p) for p in (P_PRIVATE, P_GROUP, P_PART))

    # ------------------------------------------------------------ app groups
    h = {"build_facts": {"projects": [{"targets": [
        {"name": "Demo", "app_groups": ["group.org.demo.app$(SIGNING_DISAMBIGUATOR)"]},
        {"name": "Widget", "app_groups": ["group.org.demo.widget"]},
    ]}]}}
    s = site(target={"name": "Demo", "app_groups": ["group.org.demo.app$(SIGNING_DISAMBIGUATOR)"],
                     "also_in": ["Widget"]})
    got = T.app_groups_of(h, s)
    assert got == {"group.org.demo.app", "group.org.demo.widget"}, got     # placeholder stripped, also_in joined
    assert T.app_groups_of(h, site()) == {"group.org.demo.app", "group.org.demo.widget"}  # no target -> union
    assert T.app_groups_of(header(legacy=True), site()) == GROUPS          # legacy fallback
    assert T.app_groups_of({}, site()) == set()
    # an APP target that lists no group joins no group -- not "unknown, take the union"
    assert T.app_groups_of(h, site(target={"name": "Other", "kind": "APP", "app_groups": [],
                                           "entitlements": "o.entitlements"})) == set()
    # ...but a framework's `app_groups: []` is a fact about signing, not reachability:
    # its code runs inside the host app, and wikipedia-ios' MWKDataStore.m sites
    # (WMF framework, group suite `group.org.wikimedia.wikipedia`) were flagged as
    # CONFLICT before this was told apart
    hh = {"build_facts": {"projects": [{"targets": [
        {"name": "Demo", "kind": "APP", "app_groups": ["group.org.demo.app$(SIGNING_DISAMBIGUATOR)"],
         "entitlements": "d.entitlements"},
        {"name": "DemoKit", "kind": "FRAMEWORK", "app_groups": []},
        {"name": "Widget", "kind": "APP_EXTENSION", "app_groups": ["group.org.demo.widget"],
         "entitlements": "w.entitlements"},
    ]}]}}
    fw = site(domain_hint="SUITE:group.org.demo.app",
              target={"name": "DemoKit", "kind": "FRAMEWORK", "app_groups": []})
    assert T.app_groups_of(hh, fw) == {"group.org.demo.app", "group.org.demo.widget"}
    e = T.resolve_domain(fw, rec(), hh)
    assert (e.kind, e.in_entitlements) == (T.DOM_SUITE, True), e
    assert T.expected_domain_verdict(P_GROUP, e, T.group_facts_of(hh, fw), "1.11") == "SUPPORTED"
    assert T.expected_domain_verdict(P_PART, e, T.group_facts_of(hh, fw), "1.11") == "SUPPORTED"
    # an SPM target / pod (no `app_groups` key at all) resolves the same way
    assert T.app_groups_of(hh, site(target={"name": "Lib", "kind": "SPM_TARGET"})) == \
        {"group.org.demo.app", "group.org.demo.widget"}
    # a suite that is a prefix of a group id is a different domain, not a member
    facts = T.GroupFacts(frozenset({"group.org.demo.app"}), True)
    assert T._suite_in_groups("group.org", facts) is False
    assert T._suite_in_groups("group.org.demo.app", facts) is True
    assert T._suite_in_groups("group.org.demo.appABC12", facts) is True     # placeholder suffix
    # "joins no group" and "the batch never said" are different claims
    gf = T.group_facts_of({"build_facts": {"projects": [{"targets": [{"name": "Demo"}]}]}}, site())
    assert (gf.known, set(gf)) == (True, set()), gf
    assert T.group_facts_of({}, site()).known is False
    #   ...and the second one must not resolve to "not a member"
    e = T.resolve_domain(site(domain_hint="SUITE:group.org.demo.app"), rec(), {})
    assert e.in_entitlements is None, e
    assert T.expected_domain_verdict(P_GROUP, e, (), "1.11") == "UNKNOWN", e
    assert T.expected_domain_verdict(P_GROUP, e, (), "1.10") == "SUPPORTED"   # 1.10 reads the name
    assert T.expected_domain_verdict(P_PART, e, (), "1.10") == "UNKNOWN"      # C2 reads the entitlement in both

    # ------------------------------------------------------------- resolution
    # 1. scanner hint alone
    e = T.resolve_domain(site(domain_hint="APP_PRIVATE"), rec(), header())
    assert e.kind == T.DOM_STANDARD, e
    e = T.resolve_domain(site(domain_hint="SUITE:group.org.demo.app"), rec(), header())
    assert (e.kind, e.in_entitlements) == (T.DOM_SUITE, True), e
    e = T.resolve_domain(site(domain_hint="SUITE:test.demo.x"), rec(), header())
    assert (e.kind, e.name_says_group, e.in_entitlements) == (T.DOM_SUITE, False, False), e
    e = T.resolve_domain(site(domain_hint="MIXED_DOMAINS:APP_GROUP|APP_PRIVATE"), rec(), header())
    assert e.kind == T.DOM_MIXED, e
    # 2. instance_domains when the hint abstains
    e = T.resolve_domain(site(domain_hint="SELF_INSTANCE",
                              instance_domains=[{"domain": "APP_GROUP"}]), rec(), header())
    assert e.kind == T.DOM_SUITE and e.name_says_group is True, e
    e = T.resolve_domain(site(domain_hint="SELF_INSTANCE",
                              instance_domains=[{"domain": "APP_GROUP"}, {"domain": "APP_PRIVATE"}]),
                         rec(), header())
    assert e.kind == T.DOM_MIXED, e
    # 3. callers of an extension body
    e = T.resolve_domain(site(domain_hint="SELF_INSTANCE",
                              callers={"by_domain": {"APP_PRIVATE": 4}}), rec(), header())
    assert e.kind == T.DOM_STANDARD and e.source == "callers.by_domain", e
    # one caller the scanner could not classify is one caller that might pass a
    # group instance: no reading
    e = T.resolve_domain(site(domain_hint="SELF_INSTANCE",
                              callers={"by_domain": {"APP_PRIVATE": 4, "UNKNOWN": 1}}), rec(), header())
    assert e.kind == T.DOM_UNKNOWN, e
    e = T.resolve_domain(site(domain_hint="SELF_INSTANCE",
                              callers={"by_domain": {"APP_PRIVATE": 2, "SUITE_CONST:suite unresolved": 1}}),
                         rec(), header())
    assert e.kind == T.DOM_UNKNOWN, e
    e = T.resolve_domain(site(domain_hint="SELF_INSTANCE", callers={"n": 0, "by_domain": {}}), rec(), header())
    assert e.kind == T.DOM_UNKNOWN, e
    e = T.resolve_domain(site(domain_hint="SELF_INSTANCE",
                              callers={"by_domain": {"APP_PRIVATE": 4, "APP_GROUP": 2}}), rec(), header())
    assert e.kind == T.DOM_MIXED, e
    # 4. the annotation's own words, when nothing upstream answered
    e = T.resolve_domain(site(domain_hint="SELF_INSTANCE"),
                         rec(notes="域是 UserDefaults.standard，即本 bundle 的 App 私有域"), header())
    assert e.kind == T.DOM_UNKNOWN, e      # prose votes for nothing
    #    ...but it corroborates a batch fact, and the reading survives
    e = T.resolve_domain(site(domain_hint="APP_PRIVATE"),
                         rec(notes="域是 UserDefaults.standard，即本 bundle 的 App 私有域"), header())
    assert e.kind == T.DOM_STANDARD, e
    # 4'. prose alone never establishes a reading -- it may only veto one.  The
    #     scanner said "there is a suite I cannot fold"; the annotator naming it in
    #     notes does not let this module judge, because notes quote other files too.
    e = T.resolve_domain(site(domain_hint="SUITE_CONST:suite unresolved"),
                         rec(notes='suite 来自 L6: let suite = "test.demo.popup.x"'), header())
    assert e.kind == T.DOM_SUITE_UNRESOLVED, e
    assert T.expected_domain_verdict(P_PRIVATE, e, GROUPS, "1.11") is None
    #     and a suite named in the *batch* is judged, entitlement and all
    e = T.resolve_domain(site(domain_hint="SUITE:test.demo.popup.x"),
                         rec(notes='suite 来自 L6: let suite = "test.demo.popup.x"'), header())
    assert (e.kind, e.suite, e.in_entitlements) == (T.DOM_SUITE, "test.demo.popup.x", False), e
    #     a one-shot suite whose *name* ends in `.standard` is not the standard domain
    e = T.resolve_domain(site(domain_hint="SUITE_CONST:standardSuite unresolved"),
                         rec(notes='L158: let standardSuite = "test.demo.cloudhosts.standard.abc"'), header())
    assert e.kind == T.DOM_SUITE_UNRESOLVED, e
    assert not [s for s in e.signals if s.kind == T.DOM_STANDARD], e.signals

    # ------------------------------------------------- contradictions fail closed
    # `UserDefaults(suiteName: GroupIdentifier.shared.value) ?? .standard`: the scanner
    # saw the fallback, the annotation resolved the suite.  59 real sites look like this.
    e = T.resolve_domain(
        site(domain_hint="APP_PRIVATE"),
        rec(notes="C1：域是 L205: let defaults = UserDefaults(suiteName: GroupIdentifier.shared.value) ?? .standard"),
        header())
    assert e.kind == T.DOM_UNKNOWN and e.source.startswith("veto"), e
    assert T.expected_domain_verdict(P_PRIVATE, e, GROUPS, "1.11") is None
    # two concrete readings
    e = T.resolve_domain(site(domain_hint="APP_PRIVATE"),
                         rec(notes='写进 suiteName: "group.org.demo.app" 这个域'), header())
    assert e.kind == T.DOM_UNKNOWN and e.source.startswith("veto"), e
    # a negated mention is not a veto either
    e = T.resolve_domain(site(domain_hint="APP_PRIVATE"),
                         rec(notes="域不是 UserDefaults() 以外的东西，本行只取实例"), header())
    assert e.kind == T.DOM_STANDARD, e
    # a concrete App Group id in the prose vetoes a `.standard` prefill: 15 wBlock
    # sites read "域见 L75 的 group suite" against a APP_PRIVATE prefill, and the
    # annotation is the one that had the whole file open
    e = T.resolve_domain(site(domain_hint="APP_PRIVATE"),
                         rec(notes="C1：域见 L75 的 group suite → CONFLICT。App Group 是 group.org.demo.app"),
                         header())
    assert e.kind == T.DOM_UNKNOWN and e.source.startswith("veto"), e
    # an opaque `suiteName:` quoted in prose restates a batch SUITE reading; it
    # only ever contradicts STANDARD
    e = T.resolve_domain(site(domain_hint="SUITE:test.demo.x"),
                         rec(notes="L7: guard let defaults = UserDefaults(suiteName: suite) else {"),
                         header())
    assert (e.kind, e.suite) == (T.DOM_SUITE, "test.demo.x"), e
    # prose naming a *different* suite than the batch vetoes the batch reading
    e = T.resolve_domain(site(domain_hint="SUITE:group.org.demo.app"),
                         rec(notes='实际写进 suiteName: "test.other.suite"'), header())
    assert e.kind == T.DOM_UNKNOWN and e.source.startswith("veto"), e

    # ------------------------------------------------- per-domain verdict keys
    e = T.evidence_for_domain_label("APP_PRIVATE")
    assert T.expected_domain_verdict(P_PRIVATE, e, GROUPS, "1.11") == "SUPPORTED"
    assert T.expected_domain_verdict(P_GROUP, e, GROUPS, "1.11") == "CONFLICT"
    e = T.evidence_for_domain_label("APP_GROUP")
    assert T.expected_domain_verdict(P_GROUP, e, GROUPS, "1.11") == "SUPPORTED"
    assert T.expected_domain_verdict(P_PRIVATE, T.evidence_for_domain_label("WAT"), GROUPS, "1.11") is None

    # --------------------------------------------------------------- §4.7 ALT
    alt = dict(site_class="ALT", is_api_use="YES")
    # the first pass wrote YES on 131 sites shaped exactly like this one
    assert T.expected_alt_exceeds({**alt, "value_fate": ["LOCAL_ONLY"], "escape": None}) == "NO"
    assert T.expected_alt_exceeds({**alt, "value_fate": ["PERSISTED_LOCAL", "UI_DISPLAY"],
                                   "escape": None}) == "NO"
    assert T.expected_alt_exceeds({**alt, "value_fate": ["DERIVED_ID", "STORED"],
                                   "escape": None}) == "YES"
    assert T.expected_alt_exceeds({**alt, "value_fate": ["OFF_DEVICE", "RETURNED"],
                                   "escape": {"kind": "RETURNED", "value": "RAW"}}) == "YES"
    # raw escape that has not been traced to the end: the table defers, so do we
    assert T.expected_alt_exceeds({**alt, "value_fate": ["RETURNED"],
                                   "escape": {"kind": "RETURNED", "value": "RAW"}}) is None
    # only a derived value leaves: §4.7 row 2 wants NO, but "only derived" is not
    # readable off these fields alone, so no expectation
    assert T.expected_alt_exceeds({**alt, "value_fate": ["OFF_DEVICE"],
                                   "escape": {"kind": "PASSED_OUT", "value": "DERIVED"}}) is None
    assert T.expected_alt_exceeds({**alt, "value_fate": ["LOGGED"], "escape": None}) is None
    assert T.expected_alt_exceeds({"site_class": "ALT", "is_api_use": "NO",
                                   "value_fate": [], "escape": None}) is None
    assert T.expected_alt_exceeds({"site_class": "RRA", "is_api_use": "YES",
                                   "value_fate": ["LOCAL_ONLY"], "escape": None}) is None

    print("PASS  §4.5 两张表逐行（含 1.11 的三处改写）+ App Group 按 entitlement 认定（占位符/also_in/旧格式回退）"
          " + 域证据四级并联与三种矛盾（?? .standard / 两个具体读数 / 否定式）+ MIXED 逐域 + §4.7 四行")


if __name__ == "__main__":
    main()
