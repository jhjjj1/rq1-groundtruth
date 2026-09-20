#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The executable form of the annotation protocol's rule tables (§4.5 domains, §4.7 ALT).

Two consumers need the same rules and must never drift apart: `annotate_validate.py`
(does the annotation match the table?) and `apply_rule_fixes.py` (mechanically
correct the cases where it does not).  A rule implemented twice is a rule that will
be implemented differently, so it lives here once.

What the tables are
-------------------
§4.5 gives, for each `DefaultsDomainIs(...)` / `AllIntendedParticipants...`
constraint, the verdict that follows from the domain a site operates on.  The
table changed once: 1.11 judges a domain by **who can reach it** (App Group
membership per `com.apple.security.application-groups` entitlement), where
1.9/1.10 judged it by spelling (`.standard`? `group.` prefix?).  1.9 and 1.10
are identical on these three rows -- verified line by line against
`ANNOTATION_PRINCIPLES.md` 1.9 L346 and 1.11 L358 -- so `table="1.10"` covers both.

§4.7 gives, for an ALT site, whether the use exceeds every approved reason.

What this module refuses to do
------------------------------
`expected_*` returns ``None`` for every situation the table does not settle.
``None`` means "the table says nothing here", and no caller may turn it into a
judgement: the validator does not flag it, the fixer does not touch it.  This is
the same discipline the project applies everywhere else -- an instrument that
cannot see something reports that it cannot see it, rather than reporting zero.

Why domain evidence is combined, not taken from the first source that answers
----------------------------------------------------------------------------
Measured on the second pass: 59 wBlock sites carry `domain_hint=APP_PRIVATE`
from the scanner while the annotation judges `RCA92_C1=CONFLICT`.  Both are
defensible readings of `UserDefaults(suiteName: GroupIdentifier.shared.value)
?? .standard` -- the scanner saw the `.standard` fallback, the annotator
resolved `GroupIdentifier.shared.value` across files to an App Group.  A
first-source-wins resolution would have taken the scanner's reading and called
a correct annotation wrong.  So every source contributes a *signal*, and
contradictory signals collapse to UNKNOWN (§`resolve_domain`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

# --------------------------------------------------------------------------- #
# Domain kinds.  These describe *what the domain is*, not what any table makes
# of it -- whether a named suite counts as an App Group is a table-dependent
# question (1.10 reads the name, 1.11 reads the entitlement), so it is answered
# in `expected_domain_verdict`, never here.
# --------------------------------------------------------------------------- #
DOM_STANDARD = "STANDARD"                  #: `.standard` / `UserDefaults()` -- the app's own private domain
DOM_SUITE = "SUITE"                        #: `suiteName:` with a value we know
DOM_SUITE_UNRESOLVED = "SUITE_UNRESOLVED"  #: there is a `suiteName:`, its value is not resolvable
DOM_GLOBAL_OR_FOREIGN = "GLOBAL_OR_FOREIGN"  #: NSGlobalDomain / another app's bundle id
DOM_MIXED = "MIXED"                        #: the site operates on more than one domain
DOM_UNKNOWN = "UNKNOWN"                    #: no signal, or signals that contradict each other

#: The kinds that make a positive, mutually exclusive claim about the domain.
#: Two different ones in the same site's evidence is a contradiction.
_CONCRETE = frozenset({DOM_STANDARD, DOM_SUITE, DOM_GLOBAL_OR_FOREIGN})

VERDICTS = ("SUPPORTED", "CONFLICT", "UNKNOWN")
TABLES = ("1.10", "1.11")

#: Scanner vocabulary for `domain_hint` / `instance_domains[*].domain`
#: (`scan_source_rra.py` L584-L598, L1226, L1370, L1520).
_HINT_APP_PRIVATE = "APP_PRIVATE"
_HINT_APP_GROUP = "APP_GROUP"
_HINT_SELF_INSTANCE = "SELF_INSTANCE"
_HINT_UNKNOWN = "UNKNOWN"

#: `.standard` on its own is not enough: a one-shot test suite is routinely named
#: `test.x.cloudhosts.standard.<uuid>`, and matching the bare word inside that
#: string literal read 28 real sites as the app-private domain.  The receiver has
#: to be there.
RE_STANDARD = re.compile(
    r"(?:NS)?UserDefaults\s*\.\s*standard\b"
    r"|\bstandardUserDefaults\b"
    r"|(?:NS)?UserDefaults\s*\(\s*\)"
    r"|\[\s*\[\s*NSUserDefaults\s+alloc\s*\]\s*init\s*\]")
#: `.global` is also `DispatchQueue.global()`, which annotations quote constantly;
#: a call is never a defaults domain, so the bare form only counts when not called.
RE_GLOBAL = re.compile(r"NSGlobalDomain|\.globalDomain\b|\.global\b(?!\s*\()")
#: `suiteName: "literal"` / `let suite = "literal"` -- the value is right there.
RE_SUITE_LITERAL = re.compile(r"\b\w*suite\w*\s*[:=]\s*\"([^\"]+)\"", re.IGNORECASE)
#: `suiteName:` whose argument is *not* a string literal.  This is a positive
#: statement that a suite exists, which is what makes `?? .standard` a
#: contradiction rather than a plain `.standard` site.
RE_SUITE_OPAQUE = re.compile(r"\bsuiteName\s*:\s*(?!\s*\")")
#: A concrete App Group id written out in the annotation.  Prose can only veto, so
#: this never decides a site; it exists so that a record naming `group.skula.wBlock`
#: while the prefill says `.standard` produces "cannot tell" instead of a verdict.
#: Measured cost on the 229 sites this checker is meant to catch: zero of them
#: mention a `group.` id anywhere.
RE_GROUP_LITERAL = re.compile(r"\bgroup\.[A-Za-z0-9_.\-]{2,}")
#: A signal inside a negation is not a signal.  `域不是 .standard 而是 …` must
#: not be read as `.standard`.
RE_NEGATED = re.compile(r"(不是|不在|并非|非|没有|不用|未用|≠|not\s+)\s*[^，。；;]{0,6}$")

#: Xcode build-setting placeholders in entitlement values
#: (`group.org.wikimedia.wikipedia$(SIGNING_DISAMBIGUATOR)`).
RE_XCODE_VAR = re.compile(r"\$[({][^)}]*[)}]")


@dataclass(frozen=True)
class DomainSignal:
    kind: str
    source: str
    suite: str | None = None
    #: What the *spelling* says about group-ness when no literal is available
    #: (the scanner already applied the 1.10 prefix rule to produce APP_GROUP).
    name_says_group: bool | None = None
    detail: str = ""


@dataclass(frozen=True)
class DomainEvidence:
    kind: str
    source: str
    suite: str | None = None
    name_says_group: bool | None = None
    in_entitlements: bool | None = None
    signals: tuple[DomainSignal, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source": self.source,
            "suite": self.suite,
            "name_says_group": self.name_says_group,
            "in_entitlements": self.in_entitlements,
            "signals": [
                {"kind": s.kind, "source": s.source, "suite": s.suite, "detail": s.detail}
                for s in self.signals
            ],
        }


# --------------------------------------------------------------------------- #
# App Group membership
# --------------------------------------------------------------------------- #
def _strip_xcode_vars(name: str) -> str:
    """`group.x$(SIGNING_DISAMBIGUATOR)` -> `group.x`; also used on suite names."""
    return RE_XCODE_VAR.sub("", str(name or "")).strip()


@dataclass(frozen=True)
class GroupFacts:
    """The App Groups a target joins, and whether the batch said anything at all.

    An empty set with ``known=True`` means "this target joins no group" -- a fact.
    An empty set with ``known=False`` means the batch carried no entitlement
    facts, which is not the same claim and must never be read as non-membership.
    """

    groups: frozenset[str] = frozenset()
    known: bool = False

    def __iter__(self):
        return iter(self.groups)

    def __contains__(self, item) -> bool:
        return item in self.groups

    def __bool__(self) -> bool:
        return bool(self.groups)

    def __len__(self) -> int:
        return len(self.groups)


def group_facts_of(header: Mapping[str, Any], site: Mapping[str, Any] | None = None) -> GroupFacts:
    """`app_groups_of` plus whether the batch carried entitlement facts at all."""
    header = header or {}
    site = site or {}
    target = site.get("target")
    known = bool(
        (header.get("build_facts") or {}).get("projects")
        or (isinstance(target, Mapping) and "app_groups" in target)
        or any(((h or {}).get("app_facts") or {}).get("app_group_ids") is not None
               for h in (header.get("hosts") or ()))
    )
    return GroupFacts(frozenset(app_groups_of(header, site)), known)


#: Target kinds that own an entitlements file, and therefore App Group membership.
#: Everything else -- a framework, an SPM target, a pod, a bundle -- runs inside one
#: of these, and inherits *their* groups.  wikipedia-ios is the measured case: its
#: `MWKDataStore.m` sites are compiled into the WMF framework, whose target record
#: says `app_groups: []` because frameworks are not signed with entitlements; the
#: group the code actually reaches is the app's.
_HOST_KINDS = frozenset({"APP", "APP_EXTENSION", "APPEX"})


def _host_targets(header: Mapping[str, Any]):
    projects = ((header.get("build_facts") or {}).get("projects") or ()) if header else ()
    return [t for p in projects for t in (p.get("targets") or ()) if isinstance(t, Mapping)]


def app_groups_of(header: Mapping[str, Any], site: Mapping[str, Any] | None = None) -> set[str]:
    """The App Groups the process running this site's code is a member of.

    1.11 §4.5 says membership is decided by `com.apple.security.application-groups`,
    which the scanner lifts into the batch (`build_facts.projects[].targets[].app_groups`,
    mirrored on each site as `target.app_groups`).

    Whose entitlements?  The **host process's**.  A site in an APP / APP_EXTENSION
    target uses that target's groups (plus those of the other host targets the file
    is also compiled into).  A site in a framework, SPM target or pod has no
    entitlements of its own -- its record says `app_groups: []`, which is a fact
    about signing, not about reachability -- so it takes the union over the batch's
    host targets, the processes it can be loaded into.  A site with no target at
    all does the same.  Batches cut before `build_facts` existed only carry
    `hosts[*].app_facts.app_group_ids`; reading those is the documented fallback.

    An empty set is a fact (no host joins any group), not a failure.
    """
    header = header or {}
    site = site or {}
    target = site.get("target") if isinstance(site.get("target"), Mapping) else None
    targets = _host_targets(header)
    hosts = [t for t in targets if str(t.get("kind") or "").upper() in _HOST_KINDS]
    out: set[str] = set()
    if target is not None and str(target.get("kind") or "").upper() in _HOST_KINDS and "app_groups" in target:
        out.update(_strip_xcode_vars(g) for g in (target.get("app_groups") or ()))
        also = {str(x) for x in (target.get("also_in") or ())}
        for t in hosts:
            if str(t.get("name") or "") in also:
                out.update(_strip_xcode_vars(g) for g in (t.get("app_groups") or ()))
        return {g for g in out if g}
    if hosts:
        for t in hosts:
            out.update(_strip_xcode_vars(g) for g in (t.get("app_groups") or ()))
        return {g for g in out if g}
    if targets:
        for t in targets:
            out.update(_strip_xcode_vars(g) for g in (t.get("app_groups") or ()))
    if not out:
        for host in header.get("hosts") or ():
            for g in ((host or {}).get("app_facts") or {}).get("app_group_ids") or ():
                out.add(_strip_xcode_vars(g))
    return {g for g in out if g}


def _suite_in_groups(suite: str | None, groups: Iterable[str]) -> bool | None:
    """Is this suite one of the target's App Groups?  None when we cannot tell.

    `groups` may be a `GroupFacts`; a plain iterable is treated as known facts,
    which keeps the function usable from tests with a literal set.
    """
    if not suite:
        return None
    if isinstance(groups, GroupFacts) and not groups.known:
        return None
    s = _strip_xcode_vars(suite)
    gs = {_strip_xcode_vars(g) for g in groups}
    if not gs:
        return False
    if s in gs:
        return True
    # `group.x$(SIGNING_DISAMBIGUATOR)` strips to `group.x`; the signed suite is
    # `group.x<something>`.  Only that direction is a match -- a suite that is a
    # *prefix* of a group id (`group.org` against `group.org.demo.app`) is a
    # different domain, not a member.
    for g in gs:
        if g and s.startswith(g) and len(s) > len(g):
            return True
    return False


# --------------------------------------------------------------------------- #
# Signals
# --------------------------------------------------------------------------- #
def _signal_from_hint(hint: Any, source: str) -> DomainSignal | None:
    """One scanner hint -> at most one signal.  Unresolvable hints say so."""
    if not isinstance(hint, str) or not hint:
        return None
    if hint == _HINT_APP_PRIVATE:
        return DomainSignal(DOM_STANDARD, source, detail=hint)
    if hint == _HINT_APP_GROUP:
        return DomainSignal(DOM_SUITE, source, suite=None, name_says_group=True, detail=hint)
    if hint.startswith("SUITE:"):
        value = hint[len("SUITE:"):]
        return DomainSignal(DOM_SUITE, source, suite=value,
                            name_says_group=value.startswith("group."), detail=hint)
    if hint.startswith("MIXED_DOMAINS"):
        return DomainSignal(DOM_MIXED, source, detail=hint)
    if hint.startswith("SUITE_CONST:"):
        # The scanner found a `suiteName:` whose constant it could not pin down.
        return DomainSignal(DOM_SUITE_UNRESOLVED, source, detail=hint)
    if hint in (_HINT_SELF_INSTANCE, _HINT_UNKNOWN):
        # `SELF_INSTANCE` is an extension body on UserDefaults: the domain is
        # whatever the caller passed, so the hint itself asserts nothing.
        return None
    return None


def _text_signals(rec: Mapping[str, Any], groups: Iterable[str]) -> list[DomainSignal]:
    """Signals the annotation itself states, with negations dropped.

    Only the fields where the annotator describes *this* site are read.  Every
    signal keeps the matched text so a reviewer can audit the reading.
    """
    out: list[DomainSignal] = []
    for fieldname in ("notes", "fate_evidence", "is_api_use_reason"):
        blob = str(rec.get(fieldname) or "")
        if not blob:
            continue
        for rx, kind in ((RE_STANDARD, DOM_STANDARD), (RE_GLOBAL, DOM_GLOBAL_OR_FOREIGN)):
            for m in rx.finditer(blob):
                left = blob[max(0, m.start() - 24):m.start()]
                if RE_NEGATED.search(left):
                    continue
                out.append(DomainSignal(kind, f"text:{fieldname}", detail=m.group(0)))
        for m in RE_SUITE_LITERAL.finditer(blob):
            left = blob[max(0, m.start() - 24):m.start()]
            if RE_NEGATED.search(left):
                continue
            value = m.group(1)
            out.append(DomainSignal(DOM_SUITE, f"text:{fieldname}", suite=value,
                                    name_says_group=value.startswith("group."),
                                    detail=m.group(0)[:80]))
        for m in RE_GROUP_LITERAL.finditer(blob):
            left = blob[max(0, m.start() - 24):m.start()]
            if RE_NEGATED.search(left):
                continue
            out.append(DomainSignal(DOM_SUITE, f"text:{fieldname}", suite=m.group(0),
                                    name_says_group=True, detail=m.group(0)))
        for m in RE_SUITE_OPAQUE.finditer(blob):
            left = blob[max(0, m.start() - 24):m.start()]
            if RE_NEGATED.search(left):
                continue
            out.append(DomainSignal(DOM_SUITE_UNRESOLVED, f"text:{fieldname}",
                                    detail=blob[m.start():m.start() + 60]))
    return out


def domain_signals(site: Mapping[str, Any] | None, rec: Mapping[str, Any] | None,
                   groups: Iterable[str] = ()) -> list[DomainSignal]:
    """Every signal about this site's domain, from batch facts first, then the record."""
    out: list[DomainSignal] = []
    site = site or {}
    s = _signal_from_hint(site.get("domain_hint"), "domain_hint")
    if s is not None:
        out.append(s)
    inst = site.get("instance_domains")
    if isinstance(inst, Sequence) and not isinstance(inst, (str, bytes)):
        got = [x for x in
               (_signal_from_hint((d or {}).get("domain"), "instance_domains")
                for d in inst if isinstance(d, Mapping))
               if x is not None]
        kinds = {(g.kind, g.suite) for g in got}
        if len(kinds) > 1:
            out.append(DomainSignal(DOM_MIXED, "instance_domains",
                                    detail=",".join(sorted(str(k) for k, _ in kinds))))
        else:
            out.extend(got)
    callers = site.get("callers")
    if isinstance(callers, Mapping):
        # Extension bodies (`SELF_INSTANCE`): the domain is the caller's.  The
        # scanner groups the callers it found *inside this unit* by the domain
        # each one passes in.  The reading is only as good as every caller's own
        # classification, so one unclassified caller (UNKNOWN, an unresolved
        # SUITE_CONST) means no reading -- a caller passing a group instance
        # would otherwise hide behind the ones passing `.standard`.
        by_domain = callers.get("by_domain") or {}
        keys = [str(k) for k in by_domain if by_domain.get(k)]
        unresolved = [k for k in keys if k in (_HINT_UNKNOWN, "") or k.startswith("SUITE_CONST")]
        named = [k for k in keys if k not in unresolved]
        if unresolved or not named:
            pass
        elif len(named) == 1:
            s = _signal_from_hint(named[0], "callers.by_domain")
            if s is not None:
                out.append(s)
        else:
            out.append(DomainSignal(DOM_MIXED, "callers.by_domain", detail="|".join(sorted(named))))
    if rec:
        out.extend(_text_signals(rec, groups))
    return out


def _reading(signals: Sequence[DomainSignal], groups: Iterable[str], source_tag: str) -> DomainEvidence:
    """One reading from a set of signals that are all of the same standing."""
    signals = tuple(signals)
    if not signals:
        return DomainEvidence(DOM_UNKNOWN, "none", signals=signals)
    if any(s.kind == DOM_MIXED for s in signals):
        return DomainEvidence(DOM_MIXED, "mixed", signals=signals)
    concrete = [s for s in signals if s.kind in _CONCRETE]
    kinds = {s.kind for s in concrete}
    if len(kinds) > 1:
        return DomainEvidence(DOM_UNKNOWN, "conflict:" + "|".join(sorted(kinds)), signals=signals)
    if not kinds:
        if any(s.kind == DOM_SUITE_UNRESOLVED for s in signals):
            return DomainEvidence(DOM_SUITE_UNRESOLVED, source_tag, signals=signals)
        return DomainEvidence(DOM_UNKNOWN, "none", signals=signals)
    kind = next(iter(kinds))
    if kind == DOM_SUITE:
        suites = {s.suite for s in concrete if s.suite}
        if len(suites) > 1:
            return DomainEvidence(DOM_UNKNOWN, "conflict:suites:" + "|".join(sorted(suites)),
                                  signals=signals)
        suite = next(iter(suites)) if suites else None
        says_group = None
        for s in concrete:
            if s.name_says_group is not None:
                says_group = bool(says_group) or s.name_says_group if says_group is not None else s.name_says_group
        if suite is not None:
            says_group = suite.startswith("group.")
        return DomainEvidence(DOM_SUITE, concrete[0].source, suite=suite,
                              name_says_group=says_group,
                              in_entitlements=_suite_in_groups(suite, groups),
                              signals=signals)
    if kind == DOM_STANDARD and any(s.kind == DOM_SUITE_UNRESOLVED for s in signals):
        # "there is a suiteName here" contradicts "the domain is .standard"
        return DomainEvidence(DOM_UNKNOWN, "conflict:STANDARD|SUITE_UNRESOLVED", signals=signals)
    return DomainEvidence(kind, concrete[0].source, signals=signals)


def combine_signals(signals: Sequence[DomainSignal], groups: Iterable[str] = ()) -> DomainEvidence:
    """Collapse signals to one reading.  Batch facts decide; the annotation may veto.

    The two kinds of signal do **not** have the same standing, and treating them
    as if they did was wrong in both directions on real data:

    * Batch facts (`domain_hint`, `instance_domains`, `callers.by_domain`) are
      produced by one scanner from the source with a documented vocabulary.  They
      are the only thing allowed to *establish* a reading.
    * The annotation's prose is free text that routinely quotes **other files** --
      the definition of a helper, a key constant, a sibling site.  Read as claims
      about this site it produces both false positives (a suite literally named
      `…cloudhosts.standard.<uuid>`) and false negatives.  So prose may only
      **veto**: if it contradicts the batch reading, the result is UNKNOWN and no
      rule is applied; if the batch said nothing, prose alone establishes nothing.

    The veto is what keeps the 59 `UserDefaults(suiteName: X) ?? .standard` sites
    out of the checker's way -- the prefill says APP_PRIVATE, the prose resolves a
    suite, and the honest output is "this module cannot tell".
    """
    signals = tuple(signals)
    facts = [s for s in signals if not s.source.startswith("text:")]
    prose = [s for s in signals if s.source.startswith("text:")]
    base = _reading(facts, groups, "suite_unresolved")
    if base.kind in (DOM_UNKNOWN, DOM_MIXED, DOM_SUITE_UNRESOLVED) or not prose:
        return DomainEvidence(base.kind, base.source, base.suite, base.name_says_group,
                              base.in_entitlements, signals)
    veto = _reading(list(prose), groups, "suite_unresolved")
    if veto.kind == DOM_UNKNOWN and veto.source.startswith("conflict"):
        return DomainEvidence(DOM_UNKNOWN, "veto:" + veto.source, signals=signals)
    if veto.kind in (DOM_UNKNOWN,):
        return DomainEvidence(base.kind, base.source, base.suite, base.name_says_group,
                              base.in_entitlements, signals)
    if veto.kind == DOM_MIXED:
        return DomainEvidence(DOM_UNKNOWN, "veto:MIXED", signals=signals)
    if veto.kind == DOM_SUITE_UNRESOLVED:
        # "there is a suiteName here" contradicts STANDARD (the `?? .standard`
        # shape) and merely restates a SUITE reading -- `UserDefaults(suiteName:
        # suite)` quoted next to a batch that already named the suite.
        if base.kind == DOM_STANDARD:
            return DomainEvidence(DOM_UNKNOWN, "veto:STANDARD|SUITE_UNRESOLVED", signals=signals)
        return DomainEvidence(base.kind, base.source, base.suite, base.name_says_group,
                              base.in_entitlements, signals)
    if veto.kind == base.kind and not (base.kind == DOM_SUITE and veto.suite and base.suite
                                       and veto.suite != base.suite):
        return DomainEvidence(base.kind, base.source, base.suite or veto.suite,
                              base.name_says_group if base.name_says_group is not None else veto.name_says_group,
                              base.in_entitlements if base.in_entitlements is not None
                              else _suite_in_groups(veto.suite, groups), signals)
    return DomainEvidence(DOM_UNKNOWN, f"veto:{base.kind}|{veto.kind}", signals=signals)


def resolve_domain(site: Mapping[str, Any] | None, rec: Mapping[str, Any] | None,
                   header: Mapping[str, Any] | None = None) -> DomainEvidence:
    """The domain this site operates on, from every available signal.  See `combine_signals`."""
    groups = group_facts_of(header or {}, site) if header is not None else GroupFacts()
    return combine_signals(domain_signals(site, rec, groups), groups)


def evidence_for_domain_label(label: str, groups: Iterable[str] = ()) -> DomainEvidence:  # noqa: ARG001
    """A per-domain verdict key (`{"APP_PRIVATE": …, "APP_GROUP": …}`) as evidence.

    A MIXED_DOMAINS site splits its verdict by domain; each branch states its own
    domain in the key, so the table can be applied to each branch separately.
    """
    if label == "APP_PRIVATE":
        return DomainEvidence(DOM_STANDARD, "verdict_key")
    if label == "APP_GROUP":
        return DomainEvidence(DOM_SUITE, "verdict_key", name_says_group=True, in_entitlements=True)
    return DomainEvidence(DOM_UNKNOWN, "verdict_key:" + str(label))


# --------------------------------------------------------------------------- #
# §4.5 -- the domain rows
# --------------------------------------------------------------------------- #
def _is_app_group(ev: DomainEvidence, table: str) -> bool | None:
    """Is this suite an App Group, under the named table?  None = not decidable.

    1.9/1.10 read the name (`group.` prefix).  1.11 reads the entitlement, and
    says so twice: a name carrying `group` that is not in
    `com.apple.security.application-groups` is not an App Group, and a suite in
    the entitlement is one whatever it is called.
    """
    if table == "1.10":
        return ev.name_says_group
    if ev.in_entitlements is not None:
        return ev.in_entitlements
    return None


def expected_domain_verdict(predicate: str, ev: DomainEvidence, groups: Iterable[str] = (),
                            table: str = "1.11") -> str | None:
    """The verdict §4.5 gives for this constraint on this domain, or None if it gives none.

    `SUITE_UNRESOLVED` deliberately yields None rather than UNKNOWN.  §4.5's
    "suite 名未解析 → UNKNOWN" addresses the annotator, who has the whole unit
    open; that the *scanner* could not fold a constant says nothing about what
    the annotator resolved, and 265 correctly-resolved sites (KeePassium's
    `UserDefaults.appGroupShared`, whose suite is `AppGroup.id` in another file)
    were reported as violations while this returned UNKNOWN.
    """
    if table not in TABLES:
        raise ValueError(f"table must be one of {TABLES}: {table!r}")
    pred = str(predicate or "")
    if ev.kind in (DOM_MIXED, DOM_UNKNOWN):
        # MIXED is settled per branch by the caller; UNKNOWN has no reading to apply.
        return None
    if pred.startswith("DefaultsDomainIs(APP_PRIVATE"):
        if ev.kind == DOM_STANDARD:
            return "SUPPORTED"
        if ev.kind == DOM_GLOBAL_OR_FOREIGN:
            return "CONFLICT"
        if ev.kind == DOM_SUITE_UNRESOLVED:
            return None
        grouped = _is_app_group(ev, table)
        if grouped is None:
            return "UNKNOWN"
        if table == "1.10":
            # 1.9/1.10: anything that is not `.standard` / `UserDefaults()` conflicts --
            # a `group.` suite belongs to 1C8F, a non-group suite was read as
            # "not the app-private domain".
            return "CONFLICT"
        # 1.11: a custom suite is a plist inside this app's own container; only a
        # real App Group (entitlement) moves the site under 1C8F.
        return "CONFLICT" if grouped else "SUPPORTED"
    if pred.startswith("DefaultsDomainIs(APP_GROUP"):
        if ev.kind in (DOM_STANDARD, DOM_GLOBAL_OR_FOREIGN):
            return "CONFLICT"
        if ev.kind == DOM_SUITE_UNRESOLVED:
            return None
        grouped = _is_app_group(ev, table)
        if grouped is None:
            return "UNKNOWN"
        return "SUPPORTED" if grouped else "CONFLICT"
    if pred.startswith("AllIntendedParticipantsAreMembersOfSameAppGroup"):
        # §4.5: this row is about *participants*, not about the domain -- a domain
        # mismatch is already recorded by C1 and is not recorded twice here.  It
        # reads the entitlement in **both** tables: 1.9's row already said "名字在
        # 该 target 的 app_groups(entitlements) 里", so 1.11 changed nothing here.
        if ev.kind == DOM_STANDARD:
            return "SUPPORTED"          # the only participant is this app
        if ev.kind == DOM_GLOBAL_OR_FOREIGN:
            return "CONFLICT"
        if ev.kind == DOM_SUITE_UNRESOLVED:
            return None
        if ev.in_entitlements is True:
            return "SUPPORTED"
        if ev.in_entitlements is False:
            # Not a group this target joins: the participants cannot all be
            # members of it.  A plain custom suite (no `group.` in the name) is
            # not addressed by the table at all.
            return "CONFLICT" if ev.name_says_group else None
        return "UNKNOWN"                # suite name unresolved / no entitlement facts
    return None


DOMAIN_PREDICATE_PREFIXES = (
    "DefaultsDomainIs(APP_PRIVATE",
    "DefaultsDomainIs(APP_GROUP",
    "AllIntendedParticipantsAreMembersOfSameAppGroup",
)


def is_domain_predicate(predicate: str) -> bool:
    return str(predicate or "").startswith(DOMAIN_PREDICATE_PREFIXES)


# --------------------------------------------------------------------------- #
# §4.7 -- ALT sites
# --------------------------------------------------------------------------- #
#: Fates that keep the value on the device.  `LOGGED` is deliberately absent:
#: §4.2 treats it as a system-log primitive (on device), but §4.7's third row
#: says "value_fate 全为本地且无 escape", and reading LOGGED into 全为本地 is an
#: interpretation, not the table.  A LOGGED-only ALT site therefore gets no
#: expectation rather than a guessed one.
_LOCAL_FATES = frozenset({"LOCAL_ONLY", "PERSISTED_LOCAL", "UI_DISPLAY"})


def expected_alt_exceeds(rec: Mapping[str, Any]) -> str | None:
    """§4.7's rows for `exceeds_all_reasons`, or None where the table is silent."""
    if str(rec.get("site_class") or "") != "ALT":
        return None
    use = str(rec.get("is_api_use") or "")
    if use == "NO":
        return None          # already covered by the existing "ALT NO -> null" rule
    if use != "YES":
        return None
    fates = set(rec.get("value_fate") or ())
    esc = rec.get("escape")
    raw = isinstance(esc, Mapping) and str(esc.get("value") or "") == "RAW"
    if "DERIVED_ID" in fates:
        return "YES"
    if raw and "OFF_DEVICE" in fates:
        return "YES"
    if esc is None and fates and fates <= _LOCAL_FATES:
        return "NO"
    return None
