"""Tests for the public benchmark harness (Item 57, v2).

Fixture mode only: the suite never touches the network. The
point of these tests is the reporting discipline itself —
preregistration, denominators with intervals, the trivial-
baseline floor, the confirmer calibration baseline and its
agreement rate, per-division separation, the failure register,
and the honesty guards. A provider rigged to answer everything
must score badly on the decline probes, and a division with no
probes must report n=0, never an invented score.
"""

import dataclasses

import pytest

from provome.evidence import (
    EvidenceItem,
    SupportDirection,
    VerificationStatus,
)
from provome.public_benchmark import (
    BENCHMARK_VERSION,
    CAVEAT,
    CAUSE_CLASSES,
    CONFIRMER_CAVEAT,
    PreregistrationError,
    PublicHumanProvider,
    Rate,
    probe_set_hash,
    probes,
    run_benchmark,
    wilson_interval,
)

_V = VerificationStatus.VERIFIED
_S = SupportDirection.SUPPORTS


def test_fixture_benchmark_all_probes_correct():
    report = run_benchmark()
    assert report.version == BENCHMARK_VERSION
    assert report.total == 49
    assert report.passed
    assert report.failures == ()
    assert all(r.correct for r in report.results)
    gate = report.gate
    assert gate.overall.verdict.numerator == 49
    assert gate.overall.verdict.denominator == 49


def test_probe_set_shape_and_ground_truth():
    all_probes = probes()
    assert len(all_probes) == 49
    assert len(all_probes) >= 45
    assert {p.division for p in all_probes} == {
        "human", "animal", "plant",
    }
    counts = {}
    for p in all_probes:
        counts[p.division] = counts.get(p.division, 0) + 1
    assert counts == {"human": 13, "animal": 18, "plant": 18}
    assert all(count >= 13 for count in counts.values())
    assert {p.category for p in all_probes} == {
        "answer", "decline", "disclose",
    }
    # Every division carries at least one disclose probe, so no
    # division reports disclosure 0/0 in the full run.
    disclose_counts = {}
    for p in all_probes:
        if p.category == "disclose":
            disclose_counts[p.division] = (
                disclose_counts.get(p.division, 0) + 1
            )
    assert disclose_counts == {"human": 2, "animal": 1, "plant": 1}
    assert {p.expected_answer for p in all_probes} == {True, False}
    ids = [p.probe_id for p in all_probes]
    assert len(ids) == len(set(ids))  # probe ids are unique
    for probe in all_probes:
        assert probe.ground_truth.strip()
        assert probe.source_url.startswith("https://")
        assert probe.question.strip()


def test_report_carries_the_literal_caveat():
    report = run_benchmark()
    assert report.caveat == (
        "Fixture/public-probe results are a benchmark of gate "
        "behaviour, not external clinical validation."
    )
    assert report.caveat == CAVEAT


def test_report_carries_the_literal_confirmer_caveat():
    report = run_benchmark()
    assert report.confirmer_caveat == (
        "The confirmer baseline is an internal calibration "
        "stand-in, not a human baseline: it is an independent "
        "re-implementation of the gate rule, and no human "
        "labels exist for this probe set yet."
    )
    assert report.confirmer_caveat == CONFIRMER_CAVEAT


def test_fixture_mode_is_deterministic():
    first = run_benchmark()
    second = run_benchmark()
    assert first.probe_set_hash == second.probe_set_hash
    assert first.failures == second.failures
    assert [r.answered for r in first.results] == [
        r.answered for r in second.results
    ]
    assert first.gate.overall.verdict == second.gate.overall.verdict


# ---------------------------------------------------------------------
# Preregistration
# ---------------------------------------------------------------------


def test_probe_set_hash_is_stable_and_recorded():
    report = run_benchmark()
    assert report.probe_set_hash == probe_set_hash(probes())
    assert len(report.probe_set_hash) == 64


def test_changing_any_probe_changes_the_hash():
    original = probes()
    baseline = probe_set_hash(original)
    changed_question = dataclasses.replace(
        original[0], question=original[0].question + " Really?"
    )
    assert probe_set_hash((changed_question,) + original[1:]) != baseline
    flipped_verdict = dataclasses.replace(
        original[0], expected_answer=not original[0].expected_answer
    )
    assert probe_set_hash((flipped_verdict,) + original[1:]) != baseline
    edited_truth = dataclasses.replace(
        original[0], ground_truth=original[0].ground_truth + " Edited."
    )
    assert probe_set_hash((edited_truth,) + original[1:]) != baseline


def test_preregistered_run_matches():
    digest = probe_set_hash(probes())
    report = run_benchmark(preregistered_hash=digest)
    assert report.preregistration_matched is True
    assert report.preregistered_hash == digest


def test_preregistration_mismatch_refuses_the_run():
    with pytest.raises(PreregistrationError):
        run_benchmark(preregistered_hash="0" * 64)


def test_no_preregistration_is_recorded_as_none():
    report = run_benchmark()
    assert report.preregistered_hash is None
    assert report.preregistration_matched is None


# ---------------------------------------------------------------------
# Denominators and intervals
# ---------------------------------------------------------------------


def test_wilson_interval_known_values():
    low, high = wilson_interval(5, 10)
    assert low == pytest.approx(0.2366, abs=1e-3)
    assert high == pytest.approx(0.7634, abs=1e-3)
    assert wilson_interval(0, 0) == (None, None)
    with pytest.raises(ValueError):
        wilson_interval(11, 10)


def test_every_score_has_a_denominator():
    report = run_benchmark()
    overall = report.gate.overall
    assert (overall.verdict.numerator, overall.verdict.denominator) == (49, 49)
    assert (overall.answer_rate.numerator,
            overall.answer_rate.denominator) == (20, 20)
    assert (overall.decline_correctness.numerator,
            overall.decline_correctness.denominator) == (25, 25)
    assert (overall.disclosure.numerator,
            overall.disclosure.denominator) == (4, 4)
    for system in report.systems:
        for score in system.by_division + (system.overall,):
            for rate in (
                score.verdict, score.answer_rate,
                score.decline_correctness, score.disclosure,
            ):
                if rate.denominator:
                    assert 0.0 <= rate.low <= rate.value <= rate.high <= 1.0
                else:
                    assert rate.value is None
                    assert rate.low is None and rate.high is None


def test_no_division_reports_disclosure_zero_of_zero():
    report = run_benchmark()
    for system in report.systems:
        for score in system.by_division:
            assert score.disclosure.denominator >= 1


def test_zero_denominator_rate_has_no_value():
    rate = Rate.of(0, 0)
    assert rate.denominator == 0
    assert rate.value is None


# ---------------------------------------------------------------------
# Trivial-baseline floor
# ---------------------------------------------------------------------


def test_baselines_are_scored_beside_the_gate():
    report = run_benchmark()
    assert [s.name for s in report.systems] == [
        "gate", "confirmer", "always-decline", "always-answer",
    ]
    always_decline = report.systems[2]
    always_answer = report.systems[3]
    # The floor, exactly: declining everything gets only the
    # decline probes right; answering everything gets only the
    # answer probes right and still fails disclosure.
    assert (always_decline.overall.verdict.numerator,
            always_decline.overall.verdict.denominator) == (25, 49)
    assert (always_answer.overall.verdict.numerator,
            always_answer.overall.verdict.denominator) == (20, 49)
    assert always_answer.overall.disclosure.numerator == 0
    assert always_decline.overall.disclosure.numerator == 0
    gate = report.gate.overall.verdict
    assert gate.numerator > always_decline.overall.verdict.numerator
    assert gate.numerator > always_answer.overall.verdict.numerator


# ---------------------------------------------------------------------
# Confirmer calibration baseline
# ---------------------------------------------------------------------


def test_confirmer_is_scored_on_the_same_probes():
    report = run_benchmark()
    confirmer = report.systems[1]
    assert confirmer.name == "confirmer"
    # Same denominators as the gate, division by division: the
    # confirmer saw exactly the probes the gate saw.
    for conf_score, gate_score in zip(
        confirmer.by_division, report.gate.by_division
    ):
        assert conf_score.verdict.denominator == (
            gate_score.verdict.denominator
        )
        assert conf_score.disclosure.denominator == (
            gate_score.disclosure.denominator
        )
    assert (confirmer.overall.verdict.numerator,
            confirmer.overall.verdict.denominator) == (49, 49)


def test_confirmer_agreement_is_a_rate_with_a_denominator():
    report = run_benchmark()
    agreement = report.confirmer_agreement
    assert agreement.denominator == report.total == 49
    # On the frozen fixtures the independent re-derivation
    # agrees with the gate on every probe — and the rate still
    # carries its interval, never a bare percentage.
    assert agreement.numerator == 49
    assert agreement.low is not None and agreement.high == 1.0
    assert 0.0 <= agreement.low <= agreement.value <= 1.0


class _DuplicatingHumanProvider(PublicHumanProvider):
    """Serves the ClinVar CFTR record twice under two item ids,
    plus the OMIM item: three verified items by id, but only two
    evidence lines by content.  The gate collapses the duplicate
    line and declines; the confirmer, deduplicating by item id,
    counts three and answers.  The two paths MUST disagree here —
    if they could not, the confirmer would be a rubber stamp,
    not a check."""

    def topic_for_question(self, question_text):
        return "cftr:p.phe508del"

    def items_for_topic(self, topic_key):
        items = super().items_for_topic("cftr:p.phe508del")
        clinvar_copy = dataclasses.replace(
            items[0], item_id="pub-cftr-1-copy"
        )
        return (items[0], clinvar_copy, items[2])


def test_confirmer_can_disagree_with_the_gate():
    cftr_probe = next(
        p for p in probes() if p.probe_id == "human-cftr-phe508del"
    )
    report = run_benchmark(
        probe_set=(cftr_probe,),
        provider_factories={"human": _DuplicatingHumanProvider},
    )
    assert report.gate.overall.verdict.numerator == 0
    confirmer = report.systems[1]
    assert confirmer.overall.verdict.numerator == 1
    assert report.confirmer_agreement.numerator == 0
    assert report.confirmer_agreement.denominator == 1


# ---------------------------------------------------------------------
# Per-division separation
# ---------------------------------------------------------------------


def test_division_denominators_sum_to_overall():
    report = run_benchmark()
    for system in report.systems:
        per_division = sum(
            s.verdict.denominator for s in system.by_division
        )
        assert per_division == system.overall.verdict.denominator
        assert [s.division for s in system.by_division] == [
            "human", "animal", "plant",
        ]


def test_division_with_zero_probes_reports_n_zero_not_a_score():
    human_only = tuple(p for p in probes() if p.division == "human")
    report = run_benchmark(probe_set=human_only)
    assert report.total == 13
    assert report.gate.overall.verdict.denominator == 13
    by_division = {
        s.division: s for s in report.gate.by_division
    }
    for empty in ("animal", "plant"):
        score = by_division[empty]
        assert score.probes == 0
        assert score.verdict.denominator == 0
        assert score.verdict.value is None
        assert score.answer_rate.value is None
        assert score.decline_correctness.value is None
        assert score.disclosure.value is None
    assert by_division["human"].verdict.numerator == 13


# ---------------------------------------------------------------------
# Failure register and the rigged-provider guards
# ---------------------------------------------------------------------


def test_empty_failure_register_is_explicit():
    report = run_benchmark()
    assert isinstance(report.failures, tuple)
    assert report.failures == ()


class _RiggedHumanProvider(PublicHumanProvider):
    """Answers everything: a topic for every question and three
    verified items from two groups for every topic, including
    fabricated genes and unverified claims. Scored against ground
    truth, it must fail every Human decline probe."""

    def topic_for_question(self, question_text):
        return "rigged:topic"

    def items_for_topic(self, topic_key):
        def item(item_id, source):
            return EvidenceItem(
                item_id=item_id,
                topic_key=topic_key,
                statement=f"Rigged fixture statement {item_id}.",
                source_id=source,
                citation=f"Rigged fixture citation {item_id}.",
                direction=_S,
                status=_V,
            )

        return (
            item("r1", "clinvar"),
            item("r2", "clinvar"),
            item("r3", "pubmed-literature"),
        )


def test_rigged_always_answer_provider_scores_badly_on_declines():
    report = run_benchmark(
        provider_factories={"human": _RiggedHumanProvider}
    )
    assert not report.passed
    human = {
        s.division: s for s in report.gate.by_division
    }["human"]
    assert human.decline_correctness.numerator == 0
    assert human.decline_correctness.denominator == 8
    failed_ids = {f.probe_id for f in report.failures}
    assert {
        "human-brca1-unverified-only",
        "human-cftr-fabricated-variant",
        "human-zzzqx9-fabricated-gene",
        "human-dog-abcb1-wrong-division",
        "human-hbb-fabricated-variant",
        "human-fakgene1-fabricated-gene",
        "human-flc-plant-gene-wrong-division",
        "human-prnp-sheep-wrong-division",
    } <= failed_ids
    # It also flips the disclose probes: it answers, but with no
    # refuting item in its rigged evidence there is nothing to
    # disclose, so the probes are scored wrong as undisclosed.
    assert "human-cftr-disagreement-disclose" in failed_ids
    assert "human-hbb-disagreement-disclose" in failed_ids
    disclose_ids = {
        "human-cftr-disagreement-disclose",
        "human-hbb-disagreement-disclose",
    }
    for failure in report.failures:
        assert failure.cause_class in CAUSE_CLASSES
        if failure.probe_id in disclose_ids:
            assert failure.expected == "answer+disclose"
            assert failure.actual == "answer (undisclosed)"
        else:
            assert failure.expected == "decline"
            assert failure.actual == "answer"
    causes = {f.probe_id: f.cause_class for f in report.failures}
    assert causes["human-cftr-fabricated-variant"] == "provider_gap"


class _SilentHumanProvider(PublicHumanProvider):
    """Recognises topics but never holds any evidence."""

    def items_for_topic(self, topic_key):
        return ()


def test_silent_provider_failures_are_registered_with_causes():
    report = run_benchmark(
        provider_factories={"human": _SilentHumanProvider}
    )
    assert not report.passed
    by_id = {f.probe_id: f for f in report.failures}
    assert by_id["human-cftr-phe508del"].expected == "answer"
    assert by_id["human-cftr-phe508del"].actual == "decline"
    assert by_id["human-cftr-phe508del"].cause_class == "fixture_gap"
    assert by_id["human-hbb-glu6val"].cause_class == "fixture_gap"
    # The decline probes are the only Human probes it gets right.
    human = {
        s.division: s for s in report.gate.by_division
    }["human"]
    assert human.verdict.numerator == 8
    assert human.decline_correctness.numerator == 8


class _HidingHumanProvider(PublicHumanProvider):
    """Drops the refuting item, so the disagreement disappears."""

    def items_for_topic(self, topic_key):
        return tuple(
            item for item in super().items_for_topic(topic_key)
            if item.direction is not SupportDirection.REFUTES
        )


def test_hidden_disagreement_is_a_registered_failure():
    report = run_benchmark(
        provider_factories={"human": _HidingHumanProvider}
    )
    assert not report.passed
    by_id = {f.probe_id: f for f in report.failures}
    failure = by_id["human-cftr-disagreement-disclose"]
    assert failure.expected == "answer+disclose"
    assert failure.actual == "answer (undisclosed)"
    assert failure.cause_class == "gate_arithmetic"
    assert "human-hbb-disagreement-disclose" in by_id
    # Both Human disclose probes fail undisclosed; the Animal
    # and Plant disclose probes still disclose, so the overall
    # disclosure line reads 2/4, not a fabricated clean sweep.
    human = {
        s.division: s for s in report.gate.by_division
    }["human"]
    assert human.disclosure.numerator == 0
    assert human.disclosure.denominator == 2
    gate = report.gate.overall
    assert gate.disclosure.numerator == 2
    assert gate.disclosure.denominator == 4


def test_answer_probes_genuinely_pass_the_gate():
    """Every ANSWER verdict is backed by gate arithmetic re-checked
    on the provider's own items — not by the surface's say-so."""
    report = run_benchmark()
    for result in report.results:
        if result.expected_answer:
            assert result.gate_passed is True
            assert result.verified_items >= 3
            assert result.source_groups >= 2
