"""Tests for the cross-source provider (v0.22.0).

The composite merges corpus literature (Europe PMC, one
independence group) with structured variant evidence from the
live provider machinery (ClinVar, gnomAD) so a Human variant
question can satisfy the gate's two-group rule on the gate's
own unchanged terms.

Everything runs on fixtures: a small synthetic corpus in the
exact on-disk Item 53 layout (the writer is copied from
test_corpus_provider.py, the suite's house pattern of
self-contained test files) and a fixture transport for the
live provider.  No network.

The behaviours under test, in order of importance:

* corpus + a genuinely independent ClinVar item PASSES the
  gate, and the Copilot answers citing both groups;
* the same question with the structured item removed DECLINES
  on the group rule;
* corpus items that merely report the ClinVar record are
  absorbed by the independence guard and cannot manufacture
  the second group — the decline stands;
* the guard is narrow: one absorbed duplicate among genuine
  papers does not block a legitimate pass;
* the composite is Human-only, in construction and through
  the Copilot's division guard;
* the public benchmark still reproduces exactly.
"""

import gzip
import json
import math
import struct
import zipfile
from array import array
from collections import Counter
from datetime import date

import pytest

from provome.copilot import Copilot, CopilotQuestion, Verdict
from provome.corpus_provider import CorpusProvider
from provome.cross_source import CrossSourceProvider
from provome.domains import Division
from provome.evidence import VerificationStatus, evaluate_gate
from provome.live import LiveEvidenceProvider
from provome.public_benchmark import run_benchmark
from provome.rag import _tokenise

FIXED_DATE = date(2026, 11, 27)

VARIANT_Q = "What is known about CFTR p.Phe508del and cystic fibrosis?"
CORPUS_ONLY_Q = "CFTR Phe508del cystic fibrosis"

# The three genuine corpus records: independent published
# reports about the variant.  (The same records
# test_corpus_provider.py plants.)
_GENUINE_RECORDS = [
    {
        "pmid": "91000001",
        "title": "CFTR Phe508del impairs folding in cystic fibrosis",
        "abstract": (
            "The Phe508del variant of CFTR misfolds in the "
            "endoplasmic reticulum and is degraded before reaching "
            "the cell surface, causing cystic fibrosis."
        ),
        "year": "2020", "journal": "Fixture Journal",
        "division": "human", "query_id": "fx",
    },
    {
        "pmid": "91000002",
        "title": "Corrector therapy for CFTR Phe508del cystic fibrosis",
        "abstract": (
            "Corrector compounds partially rescue the folding and "
            "trafficking of Phe508del CFTR in cystic fibrosis "
            "airway cells."
        ),
        "year": "2021", "journal": "Fixture Journal",
        "division": "human", "query_id": "fx",
    },
    {
        "pmid": "91000003",
        "title": "Genotype and phenotype for CFTR Phe508del",
        "abstract": (
            "Patients homozygous for CFTR Phe508del show classic "
            "cystic fibrosis pancreatic insufficiency and lung "
            "disease."
        ),
        "year": "2019", "journal": "Fixture Journal",
        "division": "human", "query_id": "fx",
    },
]

# Records whose titles present them as reports OF the ClinVar
# record: each names ClinVar and the variant.  The
# independence guard must absorb every one of them.
_CLINVAR_REPORT_RECORD = {
    "pmid": "92000001",
    "title": "ClinVar classification report for CFTR Phe508del",
    "abstract": (
        "This report restates the ClinVar classification of "
        "CFTR Phe508del as pathogenic in cystic fibrosis."
    ),
    "year": "2023", "journal": "Fixture Journal",
    "division": "human", "query_id": "fx",
}

_DUP_RECORDS = [
    _CLINVAR_REPORT_RECORD,
    {
        "pmid": "92000002",
        "title": "CFTR Phe508del: the ClinVar record reviewed",
        "abstract": (
            "A review of the ClinVar record for CFTR Phe508del "
            "and its pathogenic classification in cystic "
            "fibrosis."
        ),
        "year": "2023", "journal": "Fixture Journal",
        "division": "human", "query_id": "fx",
    },
    {
        "pmid": "92000003",
        "title": "ClinVar Phe508del submission summary for CFTR",
        "abstract": (
            "Summary of the ClinVar submission for CFTR "
            "Phe508del, classified pathogenic for cystic "
            "fibrosis."
        ),
        "year": "2024", "journal": "Fixture Journal",
        "division": "human", "query_id": "fx",
    },
]


# ---------------------------------------------------------------------
# Fixture corpus writer (copied from test_corpus_provider.py)
# ---------------------------------------------------------------------


def _npy_bytes(descr: str, shape: tuple, payload: bytes) -> bytes:
    header = (
        "{'descr': '%s', 'fortran_order': False, 'shape': %s, }"
        % (descr, repr(tuple(shape)))
    )
    padding = (64 - (10 + len(header) + 1) % 64) % 64
    header_bytes = (header + " " * padding + "\n").encode("latin1")
    return (
        b"\x93NUMPY\x01\x00"
        + struct.pack("<H", len(header_bytes))
        + header_bytes
        + payload
    )


def _write_fixture_corpus(root, records) -> None:
    """Write records as a miniature Item 53 corpus directory:
    the same artefacts, the same TF-IDF mathematics."""
    root.mkdir(parents=True, exist_ok=True)
    index_dir = root / "index"
    index_dir.mkdir(exist_ok=True)
    with gzip.open(
        root / "corpus.jsonl.gz", "wt", encoding="utf-8"
    ) as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    tokenised = [
        _tokenise(f"{r['title']} {r.get('abstract', '')}")
        for r in records
    ]
    doc_freq: Counter = Counter()
    for tokens in tokenised:
        doc_freq.update(set(tokens))
    vocab = sorted(doc_freq)
    column_of = {term: i for i, term in enumerate(vocab)}
    n_docs = len(records)
    data, indices, indptr = array("f"), array("i"), array("i")
    for tokens in tokenised:
        indptr.append(len(indices))
        counts = Counter(tokens)
        total = sum(counts.values())
        entries = []
        for term, count in counts.items():
            if term not in column_of:
                continue
            idf = math.log((1 + n_docs) / (1 + doc_freq[term])) + 1.0
            entries.append((column_of[term], (count / total) * idf))
        norm = math.sqrt(sum(w * w for _, w in entries))
        for column, weight in sorted(entries):
            indices.append(column)
            data.append(weight / norm if norm else 0.0)
    indptr.append(len(indices))
    with zipfile.ZipFile(
        index_dir / "tfidf_csr.npz", "w", zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr(
            "data.npy", _npy_bytes("<f4", (len(data),), data.tobytes())
        )
        archive.writestr(
            "indices.npy",
            _npy_bytes("<i4", (len(indices),), indices.tobytes()),
        )
        archive.writestr(
            "indptr.npy",
            _npy_bytes("<i4", (len(indptr),), indptr.tobytes()),
        )
        archive.writestr(
            "shape.npy",
            _npy_bytes("<i8", (2,), array("q", [n_docs, len(vocab)]).tobytes()),
        )
        archive.writestr(
            "format.npy", _npy_bytes("|S3", (), b"csr")
        )
    (index_dir / "vocab.json").write_text(
        json.dumps(vocab), encoding="utf-8"
    )
    (index_dir / "doc_ids.json").write_text(
        json.dumps([str(r.get("pmid", "")) for r in records]),
        encoding="utf-8",
    )
    (root / "MANIFEST.json").write_text(
        json.dumps({"fixture": True, "records": n_docs}),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------
# Fixture structured side: the live provider over a fixture transport
# ---------------------------------------------------------------------


def cftr_transport(url, body=None):
    """Fixture ClinVar/gnomAD world for CFTR p.Phe508del."""
    if "db=pubmed" in url:
        return {"result": {"uids": []}}
    if "esearch" in url:
        return {"esearchresult": {"idlist": ["12139"]}}
    if "esummary" in url:
        return {
            "result": {
                "uids": ["12139"],
                "12139": {
                    "title": (
                        "NM_000492.4(CFTR):c.1521_1523delCTT "
                        "(p.Phe508del)"
                    ),
                    "protein_change": "F508del",
                    "germline_classification": {
                        "description": "Pathogenic",
                        "review_status": "reviewed by expert panel",
                    },
                    "variation_set": [
                        {
                            "variation_loc": [
                                {
                                    "assembly_name": "GRCh38",
                                    "chr": "7",
                                    "start": "117559590",
                                    "ref": "CTT",
                                    "alt": "C",
                                }
                            ]
                        }
                    ],
                },
            }
        }
    if "gnomad" in url:
        return {
            "data": {
                "variant": {
                    "variant_id": "7-117559590-CTT-C",
                    "genome": {"ac": 100, "an": 10000, "af": 0.01},
                    "exome": None,
                }
            }
        }
    if "europepmc" in url:
        return {"resultList": {"result": []}}
    raise AssertionError(f"unexpected URL: {url}")


def empty_transport(url, body=None):
    """A structured world with nothing in it."""
    if "esearch" in url:
        return {"esearchresult": {"idlist": []}}
    if "europepmc" in url:
        return {"resultList": {"result": []}}
    raise AssertionError(f"unexpected URL: {url}")


def make_composite(corpus_dir, transport=cftr_transport):
    corpus = CorpusProvider(corpus_dir)
    structured = LiveEvidenceProvider(
        transport=transport, today=lambda: FIXED_DATE
    )
    return CrossSourceProvider(corpus, structured)


@pytest.fixture()
def genuine_corpus(tmp_path):
    _write_fixture_corpus(tmp_path / "corpus", _GENUINE_RECORDS)
    return tmp_path / "corpus"


@pytest.fixture()
def dup_corpus(tmp_path):
    _write_fixture_corpus(tmp_path / "corpus", _DUP_RECORDS)
    return tmp_path / "corpus"


@pytest.fixture()
def mixed_corpus(tmp_path):
    _write_fixture_corpus(
        tmp_path / "corpus", _GENUINE_RECORDS + [_CLINVAR_REPORT_RECORD]
    )
    return tmp_path / "corpus"


# ---------------------------------------------------------------------
# (a) Corpus + independent structured evidence passes the gate
# ---------------------------------------------------------------------


def test_merged_items_carry_both_groups(genuine_corpus):
    provider = make_composite(genuine_corpus)
    topic = provider.topic_for_question(VARIANT_Q)
    assert topic == "CFTR:p.Phe508del"
    items = provider.items_for_topic(topic)
    sources = {item.source_id for item in items}
    assert sources == {"clinvar", "gnomad", "europepmc"}
    # Every merged item is retagged onto the one topic key, so
    # the gate sees a single topic.
    assert {item.topic_key for item in items} == {"CFTR:p.Phe508del"}
    # The live provider's literature items are never merged.
    assert "pubmed-literature" not in sources


def test_gate_passes_with_corpus_plus_clinvar(genuine_corpus):
    provider = make_composite(genuine_corpus)
    topic = provider.topic_for_question(VARIANT_Q)
    items = provider.items_for_topic(topic)
    decision = evaluate_gate(items, topic)
    assert decision.passed
    assert decision.verified_count == 4  # 3 corpus + 1 ClinVar
    assert decision.independent_source_count == 2
    assert set(decision.independent_groups) == {
        "europepmc",
        "ncbi-clinvar",
    }


def test_copilot_answers_citing_both_groups(genuine_corpus):
    copilot = Copilot(provider=make_composite(genuine_corpus))
    answer = copilot.ask(VARIANT_Q)
    assert answer.verdict is Verdict.ANSWER
    assert answer.verified_item_count == 4
    assert answer.independent_source_count == 2
    citations = [
        citation
        for claim in answer.claims
        for citation in claim.citations
    ]
    assert any("ClinVar Variation ID 12139" in c for c in citations)
    assert any(c.startswith("PMID ") for c in citations)


# ---------------------------------------------------------------------
# (b) Without the structured item, the group rule still declines
# ---------------------------------------------------------------------


def test_without_structured_evidence_the_decline_stands(genuine_corpus):
    provider = make_composite(genuine_corpus, transport=empty_transport)
    copilot = Copilot(provider=provider)
    answer = copilot.ask(VARIANT_Q)
    assert answer.verdict is Verdict.DECLINE
    assert answer.verified_item_count == 3
    assert answer.independent_source_count == 1
    assert any(
        "1 independent source group(s); the gate requires at "
        "least 2" in reason
        for reason in answer.declined_reasons
    )


# ---------------------------------------------------------------------
# (c) The independence guard: record reports cannot fake a group
# ---------------------------------------------------------------------


def test_clinvar_report_duplicates_are_absorbed(dup_corpus):
    provider = make_composite(dup_corpus)
    topic = provider.topic_for_question(VARIANT_Q)
    items = provider.items_for_topic(topic)
    corpus_items = [
        item for item in items if item.source_id == "europepmc"
    ]
    assert len(corpus_items) == 3
    # All three are carried for audit, none counts as verified.
    assert all(
        item.status is VerificationStatus.UNVERIFIED
        for item in corpus_items
    )
    assert all(
        "Independence guard" in item.notes for item in corpus_items
    )
    assert provider.absorbed_duplicate_records == 3


def test_absorbed_duplicates_do_not_create_a_second_group(dup_corpus):
    provider = make_composite(dup_corpus)
    copilot = Copilot(provider=provider)
    answer = copilot.ask(VARIANT_Q)
    assert answer.verdict is Verdict.DECLINE
    # Only the ClinVar item itself counts: one item, one group.
    assert answer.verified_item_count == 1
    assert answer.independent_source_count == 1


def test_guard_is_narrow_genuine_papers_still_pass(mixed_corpus):
    provider = make_composite(mixed_corpus)
    copilot = Copilot(provider=provider)
    answer = copilot.ask(VARIANT_Q)
    # Exactly one item is absorbed; the three genuine papers
    # plus ClinVar still satisfy the gate.
    assert provider.absorbed_duplicate_records == 1
    assert answer.verdict is Verdict.ANSWER
    assert answer.verified_item_count == 4
    assert answer.independent_source_count == 2


# ---------------------------------------------------------------------
# (d) Division scoping: Human only, never faked for other divisions
# ---------------------------------------------------------------------


def test_composite_is_scoped_to_human_only(genuine_corpus):
    provider = make_composite(genuine_corpus)
    assert provider.scoped_divisions == frozenset({Division.HUMAN})


def test_composite_refuses_a_non_human_corpus(tmp_path):
    _write_fixture_corpus(tmp_path / "corpus", _GENUINE_RECORDS)
    plant_corpus = CorpusProvider(tmp_path / "corpus", division="plant")
    with pytest.raises(ValueError):
        CrossSourceProvider(plant_corpus)


def test_copilot_division_guard_blocks_animal_questions(genuine_corpus):
    copilot = Copilot(provider=make_composite(genuine_corpus))
    answer = copilot.ask(
        CopilotQuestion(text=VARIANT_Q, division=Division.ANIMAL)
    )
    assert answer.verdict is Verdict.DECLINE
    assert any(
        "not scoped to" in reason for reason in answer.declined_reasons
    )


def test_non_variant_question_gets_corpus_only_behaviour(genuine_corpus):
    # No HGVS notation: the structured side has no target, the
    # composite serves corpus evidence alone, and the gate
    # declines on the group rule — the v0.21.0 behaviour,
    # unchanged.
    provider = make_composite(genuine_corpus)
    copilot = Copilot(provider=provider)
    answer = copilot.ask(CORPUS_ONLY_Q)
    assert answer.verdict is Verdict.DECLINE
    assert answer.independent_source_count == 1
    items = provider.items_for_topic(answer.topic_key)
    assert {item.source_id for item in items} == {"europepmc"}


# ---------------------------------------------------------------------
# (e) The public benchmark still reproduces exactly
# ---------------------------------------------------------------------


def test_public_benchmark_still_reproduces_exactly():
    report = run_benchmark()
    assert (
        report.probe_set_hash
        == "b44d2ee6e3fafdeca42c0d401ca92f1b561162685fae2d1290e15f389543bc13"
    )
    gate = next(s for s in report.systems if s.name == "gate")
    assert (
        gate.overall.verdict.numerator,
        gate.overall.verdict.denominator,
    ) == (49, 49)
