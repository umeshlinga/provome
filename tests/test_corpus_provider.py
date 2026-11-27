"""Tests for the corpus-backed Copilot provider (Item 53 corpus).

The suite never touches the real 2.2M-record corpus: each test
builds a small synthetic fixture corpus in the exact on-disk
Item 53 layout (corpus.jsonl.gz + TF-IDF CSR npz + vocab +
doc_ids) and runs the provider against it.  The fixture plants
its traps openly: a record with no abstract and a record with
no PMID, both with strong query overlap, which the read-time
re-verification must drop and count; and Plant records a Human
provider must never serve.

The headline behaviour under test is honesty at the gate:
corpus evidence is one independence group (Europe PMC), so
even with three verified items the Copilot must DECLINE, and
the decline must name the two-group rule — not a shortage of
evidence.
"""

import gzip
import json
import math
import struct
import zipfile
from array import array
from collections import Counter

import pytest

from provome.copilot import Copilot, CopilotQuestion, Verdict
from provome.corpus_provider import CorpusProvider
from provome.domains import Division
from provome.evidence import (
    VerificationStatus,
    default_source_registry,
    evaluate_gate,
)
from provome.rag import _tokenise

CFTR_Q = "CFTR Phe508del cystic fibrosis"
# No function words: every token here is content-bearing, so the
# Human records (which share no content token with it) score 0
# and the division filter is exercised on the Plant records
# alone, exactly as the test below asserts.
XA21_Q = "XA21 bacterial blight resistance rice"

_FIXTURE_RECORDS = [
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
    {
        # Planted bad record: strong query overlap, no abstract.
        # The read-time re-verification must drop and count it.
        "pmid": "91000004",
        "title": "CFTR Phe508del cystic fibrosis registry report",
        "abstract": "",
        "year": "2022", "journal": "Fixture Journal",
        "division": "human", "query_id": "fx",
    },
    {
        # Planted bad record: strong query overlap, no PMID.
        "pmid": "",
        "title": "CFTR Phe508del cystic fibrosis anonymous note",
        "abstract": (
            "CFTR Phe508del cystic fibrosis observations recorded "
            "without any identifier."
        ),
        "year": "2022", "journal": "Fixture Journal",
        "division": "human", "query_id": "fx",
    },
    {
        "pmid": "91000006",
        "title": "XA21 receptor kinase confers blight resistance in rice",
        "abstract": (
            "The rice XA21 receptor kinase recognises a bacterial "
            "blight signal and confers resistance to Xanthomonas "
            "oryzae in rice."
        ),
        "year": "2018", "journal": "Fixture Plant Journal",
        "division": "plant", "query_id": "fx",
    },
    {
        "pmid": "91000007",
        "title": "XA21 signalling pathways in rice immunity",
        "abstract": (
            "XA21 signalling in rice activates defence gene "
            "expression against bacterial blight."
        ),
        "year": "2019", "journal": "Fixture Plant Journal",
        "division": "plant", "query_id": "fx",
    },
]


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


@pytest.fixture()
def corpus_dir(tmp_path):
    _write_fixture_corpus(tmp_path / "corpus", _FIXTURE_RECORDS)
    return tmp_path / "corpus"


# ---------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------


def test_europepmc_is_registered_as_exactly_one_group():
    registry = default_source_registry()
    assert "europepmc" in registry
    assert registry.independence_group_of("europepmc") == "europepmc"
    # One index, one group: it is not the live-literature group
    # under another name.
    assert registry.independence_group_of("europepmc") != (
        registry.independence_group_of("pubmed-literature")
    )


# ---------------------------------------------------------------------
# Retrieval and re-verification
# ---------------------------------------------------------------------


def test_retrieval_finds_the_planted_records(corpus_dir):
    provider = CorpusProvider(corpus_dir)
    topic = provider.topic_for_question(CFTR_Q)
    assert topic is not None
    items = provider.items_for_topic(topic)
    assert {item.item_id for item in items} == {
        "corpus-pmid-91000001",
        "corpus-pmid-91000002",
        "corpus-pmid-91000003",
    }
    for item in items:
        assert item.source_id == "europepmc"
        assert item.status is VerificationStatus.VERIFIED
        assert "PMID" in item.citation
        assert item.locator.startswith("https://europepmc.org/article/MED/")


def test_read_time_reverification_drops_and_counts_bad_records(corpus_dir):
    provider = CorpusProvider(corpus_dir)
    topic = provider.topic_for_question(CFTR_Q)
    items = provider.items_for_topic(topic)
    served_pmids = {item.item_id for item in items}
    # The no-abstract and no-PMID records both overlap the
    # query strongly; neither is served, and both are counted.
    assert "corpus-pmid-91000004" not in served_pmids
    assert provider.dropped_records == 2


def test_no_overlap_question_recognises_no_topic(corpus_dir):
    provider = CorpusProvider(corpus_dir)
    assert provider.topic_for_question(
        "black hole thermodynamics accretion disk entropy"
    ) is None
    assert provider.items_for_topic("corpus:no such topic") == ()
    assert provider.items_for_topic("unrelated-key") == ()


# ---------------------------------------------------------------------
# Division scoping
# ---------------------------------------------------------------------


def test_human_provider_never_serves_plant_records(corpus_dir):
    provider = CorpusProvider(corpus_dir, division=Division.HUMAN)
    # The XA21 records retrieve strongly — and are all Plant.
    assert provider.topic_for_question(XA21_Q) is None
    assert provider.out_of_division_records == 2
    assert provider.dropped_records == 0


def test_plant_provider_serves_plant_records(corpus_dir):
    provider = CorpusProvider(corpus_dir, division="plant")
    topic = provider.topic_for_question(XA21_Q)
    assert topic is not None
    items = provider.items_for_topic(topic)
    assert {item.item_id for item in items} == {
        "corpus-pmid-91000006",
        "corpus-pmid-91000007",
    }


def test_copilot_division_guard_declines_out_of_scope_questions(corpus_dir):
    copilot = Copilot(provider=CorpusProvider(corpus_dir))
    answer = copilot.ask(
        CopilotQuestion(text=XA21_Q, division=Division.PLANT)
    )
    assert answer.verdict is Verdict.DECLINE
    assert any(
        "not scoped to" in reason for reason in answer.declined_reasons
    )


# ---------------------------------------------------------------------
# The gate, unchanged: corpus-only evidence declines on one group
# ---------------------------------------------------------------------


def test_gate_declines_corpus_only_evidence_on_the_group_rule(corpus_dir):
    provider = CorpusProvider(corpus_dir)
    topic = provider.topic_for_question(CFTR_Q)
    items = provider.items_for_topic(topic)
    decision = evaluate_gate(items, topic)
    assert not decision.passed
    # The evidence is sufficient in count — three verified
    # items — and fails on exactly one rule: one group.
    assert decision.verified_count == 3
    assert decision.independent_source_count == 1
    assert any(
        "1 independent source group(s)" in reason
        for reason in decision.reasons
    )
    assert not any(
        "verified supporting item(s); the gate requires" in reason
        for reason in decision.reasons
    )


def test_copilot_declines_with_the_group_reason_stated(corpus_dir):
    copilot = Copilot(provider=CorpusProvider(corpus_dir))
    answer = copilot.ask(CFTR_Q)
    assert answer.verdict is Verdict.DECLINE
    assert answer.claims == ()
    assert answer.verified_item_count == 3
    assert answer.independent_source_count == 1
    assert any(
        "1 independent source group(s); the gate requires at "
        "least 2" in reason
        for reason in answer.declined_reasons
    )
