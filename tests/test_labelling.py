"""Tests for the human-labelling kit (Item 51 blind labels).

The kit's promises are structural, and the tests enforce them:
the exported pack is blind (no expected verdict, ground truth,
or checking source anywhere in the exported bytes), the
probe-set hash is recorded in the pack header, labels bind to
the frozen set by that hash and are refused on a mismatch,
partial label sets are scored on the labelled subset only with
the denominator stated, and the report's human-baseline
section appears exactly when labels are supplied — with caveat
text that keeps real human labels distinct from the confirmer
stand-in.

The labels used here are SYNTHETIC TEST FIXTURES, generated in
this file from the fixture run's outcomes and deliberate flips.
They are not human labels, no person produced them, and they
must never be quoted as a human baseline: the labeller string
says so on every fixture.
"""

import json

import pytest

from provome.labelling import (
    INSTRUCTIONS,
    build_labelling_pack,
    load_labels_file,
    pack_to_jsonl,
    pack_to_markdown,
    parse_labels,
    score_labels,
    write_labelling_pack,
)
from provome.public_benchmark import (
    CONFIRMER_CAVEAT,
    CONFIRMER_CAVEAT_WITH_HUMAN_LABELS,
    HumanLabels,
    HumanLabelsError,
    probe_set_hash,
    probes,
    run_benchmark,
)

# Synthetic-fixture labeller identity: NOT a human labeller.
FIXTURE_LABELLER = "test-fixture (synthetic labels, not a human labeller)"
FIXTURE_DATE = "2026-11-27"


def _outcome_label(outcome) -> str:
    if not outcome.answered:
        return "decline"
    if outcome.disclosed:
        return "disclose"
    return "answer"


def _fixture_labels(label_map) -> HumanLabels:
    return HumanLabels(
        labeller=FIXTURE_LABELLER,
        date=FIXTURE_DATE,
        probe_set_hash=probe_set_hash(probes()),
        labels=tuple(sorted(label_map.items())),
    )


def _labels_matching_gate() -> HumanLabels:
    """Synthetic fixture: labels copied from the gate's own
    fixture outcomes, so agreement must come out exact."""
    report = run_benchmark()
    return _fixture_labels(
        {o.probe_id: _outcome_label(o) for o in report.results}
    )


# ---------------------------------------------------------------------
# Blind export
# ---------------------------------------------------------------------


def test_pack_shape_and_header_hash():
    pack = build_labelling_pack()
    assert pack["probe_count"] == 49
    assert len(pack["records"]) == 49
    # The hash in the header is the frozen set's hash: labels
    # gathered from this pack bind to exactly this probe set.
    assert pack["probe_set_hash"] == probe_set_hash(probes())
    assert [r["probe_id"] for r in pack["records"]] == [
        p.probe_id for p in probes()
    ]
    header = json.loads(pack_to_jsonl(pack).splitlines()[0])
    assert header["probe_set_hash"] == pack["probe_set_hash"]
    assert header["probe_count"] == 49


def test_pack_records_carry_only_blind_fields():
    pack = build_labelling_pack()
    for record in pack["records"]:
        assert set(record) == {
            "probe_id", "division", "question", "evidence",
        }
        for item in record["evidence"]:
            assert set(item) == {
                "statement", "citation", "source_id",
                "direction", "status",
            }


def test_pack_is_blind_no_truth_no_sources_no_verdicts():
    pack = build_labelling_pack()
    jsonl = pack_to_jsonl(pack)
    markdown = pack_to_markdown(pack)
    for probe in probes():
        # The sealed material appears NOWHERE in either export.
        assert probe.ground_truth not in jsonl
        assert probe.ground_truth not in markdown
        assert probe.source_url not in jsonl
        assert probe.source_url not in markdown
    assert "expected_answer" not in jsonl
    assert "expected_answer" not in markdown
    assert "ground_truth" not in jsonl
    assert "ground_truth" not in markdown
    # Per record: no field value IS the probe's expected verdict.
    by_id = {p.probe_id: p for p in probes()}
    for line in pack_to_jsonl(pack).splitlines()[1:]:
        record = json.loads(line)
        probe = by_id[record["probe_id"]]
        assert record["division"] != probe.category
        assert record["question"] != probe.category
        for item in record["evidence"]:
            assert item["statement"] != probe.category
            assert item["citation"] != probe.category


def test_pack_shows_the_evidence_the_run_uses():
    pack = build_labelling_pack()
    by_id = {r["probe_id"]: r for r in pack["records"]}
    cftr = by_id["human-cftr-phe508del"]
    assert len(cftr["evidence"]) == 3
    assert {i["source_id"] for i in cftr["evidence"]} == {
        "clinvar", "pubmed-literature", "omim",
    }
    # A fabricated gene holds no evidence, and the pack shows
    # exactly that absence — the labeller judges from it.
    fabricated = by_id["human-zzzqx9-fabricated-gene"]
    assert fabricated["evidence"] == []


def test_markdown_sheet_carries_instructions_and_blanks():
    markdown = pack_to_markdown(build_labelling_pack())
    assert INSTRUCTIONS.splitlines()[0] in markdown
    assert "labelling BLIND" in markdown
    assert "one sitting" in markdown
    assert probe_set_hash(probes()) in markdown
    # One labelled blank per probe.
    assert markdown.count("Your label (answer / decline / disclose)") == 49


def test_write_labelling_pack_round_trip(tmp_path):
    pack = build_labelling_pack()
    jsonl_path, markdown_path = write_labelling_pack(pack, tmp_path)
    assert jsonl_path.read_text(encoding="utf-8") == pack_to_jsonl(pack)
    assert markdown_path.read_text(encoding="utf-8") == (
        pack_to_markdown(pack)
    )


# ---------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------


def _labels_json(label_map, **overrides) -> str:
    payload = {
        "labeller": FIXTURE_LABELLER,
        "date": FIXTURE_DATE,
        "probe_set_hash": probe_set_hash(probes()),
        "labels": label_map,
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_parse_labels_round_trip():
    labels = parse_labels(_labels_json({"human-cftr-phe508del": "answer"}))
    assert labels.labeller == FIXTURE_LABELLER
    assert labels.date == FIXTURE_DATE
    assert labels.probe_set_hash == probe_set_hash(probes())
    assert labels.label_for("human-cftr-phe508del") == "answer"
    assert labels.label_for("human-hbb-glu6val") is None
    assert len(labels) == 1


def test_load_labels_file(tmp_path):
    path = tmp_path / "labels.json"
    path.write_text(
        _labels_json({"human-cftr-phe508del": "decline"}),
        encoding="utf-8",
    )
    labels = load_labels_file(path)
    assert labels.label_for("human-cftr-phe508del") == "decline"


def test_parse_labels_rejects_malformed_files():
    with pytest.raises(HumanLabelsError):
        parse_labels("not json")
    with pytest.raises(HumanLabelsError):
        parse_labels(json.dumps(["not", "an", "object"]))
    with pytest.raises(HumanLabelsError):
        parse_labels(json.dumps({"labeller": "x"}))  # missing fields
    with pytest.raises(HumanLabelsError):
        parse_labels(_labels_json({"human-cftr-phe508del": "maybe"}))
    with pytest.raises(HumanLabelsError):
        parse_labels(_labels_json({}, labeller=""))
    with pytest.raises(HumanLabelsError):
        parse_labels(_labels_json({}, labels=["not", "a", "map"]))


def test_duplicate_labels_are_rejected():
    with pytest.raises(HumanLabelsError):
        HumanLabels(
            labeller=FIXTURE_LABELLER,
            date=FIXTURE_DATE,
            probe_set_hash=probe_set_hash(probes()),
            labels=(
                ("human-cftr-phe508del", "answer"),
                ("human-cftr-phe508del", "decline"),
            ),
        )


# ---------------------------------------------------------------------
# Scoring and report integration
# ---------------------------------------------------------------------


def test_no_labels_no_human_section_and_caveat_unchanged():
    report = run_benchmark()
    assert report.human_baseline is None
    assert report.confirmer_caveat == CONFIRMER_CAVEAT


def test_full_synthetic_labels_score_exact_agreement():
    report = score_labels(_labels_matching_gate())
    human = report.human_baseline
    assert human is not None
    assert human.labeller == FIXTURE_LABELLER
    assert human.labelled == 49
    assert human.total == 49
    # The synthetic fixture copied the gate's outcomes, so both
    # agreement rates are exact — a property of the fixture,
    # stated here so nobody mistakes it for a human result.
    assert (human.overall.agreement_with_gate.numerator,
            human.overall.agreement_with_gate.denominator) == (49, 49)
    assert (human.overall.agreement_with_expected.numerator,
            human.overall.agreement_with_expected.denominator) == (49, 49)
    for score in human.by_division:
        assert score.agreement_with_gate.value == 1.0


def test_flipped_labels_reduce_agreement_exactly():
    label_map = {
        o.probe_id: _outcome_label(o) for o in run_benchmark().results
    }
    flipped = [
        "human-cftr-phe508del",       # answer -> decline
        "human-hbb-glu6val",          # answer -> decline
        "animal-dog-abcb1-mdr1",      # answer -> decline
        "plant-rice-xa21-blight-resistance",  # answer -> decline
        "human-zzzqx9-fabricated-gene",       # decline -> answer
    ]
    for probe_id in flipped:
        label_map[probe_id] = (
            "decline" if label_map[probe_id] == "answer" else "answer"
        )
    human = score_labels(_fixture_labels(label_map)).human_baseline
    assert (human.overall.agreement_with_gate.numerator,
            human.overall.agreement_with_gate.denominator) == (44, 49)
    assert (human.overall.agreement_with_expected.numerator,
            human.overall.agreement_with_expected.denominator) == (44, 49)
    by_division = {s.division: s for s in human.by_division}
    assert by_division["human"].agreement_with_gate.numerator == 10
    assert by_division["human"].agreement_with_gate.denominator == 13
    assert by_division["animal"].agreement_with_gate.numerator == 17
    assert by_division["plant"].agreement_with_gate.numerator == 17


def test_partial_labels_scored_on_labelled_subset_only():
    human_probe_ids = [p.probe_id for p in probes() if p.division == "human"]
    label_map = {
        o.probe_id: _outcome_label(o)
        for o in run_benchmark().results
        if o.probe_id in set(human_probe_ids[:6])
    }
    assert len(label_map) == 6
    report = score_labels(_fixture_labels(label_map))
    human = report.human_baseline
    # The section states the subset plainly: 6 labelled of 49.
    assert human.labelled == 6
    assert human.total == 49
    assert "6 of 49" in human.caveat
    assert human.overall.agreement_with_gate.denominator == 6
    assert human.overall.agreement_with_gate.numerator == 6
    by_division = {s.division: s for s in human.by_division}
    assert by_division["human"].labelled == 6
    assert by_division["human"].agreement_with_gate.denominator == 6
    # Unlabelled divisions report n=0 with no rate — never a
    # silently completed set.
    for empty in ("animal", "plant"):
        score = by_division[empty]
        assert score.labelled == 0
        assert score.agreement_with_gate.denominator == 0
        assert score.agreement_with_gate.value is None
        assert score.agreement_with_expected.value is None


def test_caveats_distinguish_human_labels_from_confirmer():
    report = score_labels(_labels_matching_gate())
    human = report.human_baseline
    # The confirmer caveat no longer claims no labels exist;
    # it keeps the confirmer a stand-in and points at the
    # separate human section.
    assert report.confirmer_caveat == CONFIRMER_CAVEAT_WITH_HUMAN_LABELS
    assert "not a human baseline" in report.confirmer_caveat
    # The human caveat names the labeller and the count, and
    # states the subset rule.
    assert FIXTURE_LABELLER in human.caveat
    assert "49 of 49" in human.caveat
    assert "labelled subset only" in human.caveat
    assert "confirmer" in human.caveat


def test_hash_mismatch_labels_are_refused_loudly():
    wrong = HumanLabels(
        labeller=FIXTURE_LABELLER,
        date=FIXTURE_DATE,
        probe_set_hash="0" * 64,
        labels=(("human-cftr-phe508del", "answer"),),
    )
    with pytest.raises(HumanLabelsError):
        run_benchmark(human_labels=wrong)


def test_labels_for_unknown_probes_are_refused():
    labels = _fixture_labels({"human-no-such-probe": "answer"})
    with pytest.raises(HumanLabelsError):
        run_benchmark(human_labels=labels)


def test_labels_bind_to_the_probe_set_they_name():
    # Labels gathered for the full set cannot be scored against
    # a subset run: the subset's hash differs, so the run
    # refuses instead of quietly scoring a partial truth.
    human_only = tuple(p for p in probes() if p.division == "human")
    with pytest.raises(HumanLabelsError):
        run_benchmark(
            probe_set=human_only, human_labels=_labels_matching_gate()
        )
