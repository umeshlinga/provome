"""Tests for the registered-source wiring (v0.24.0).

v0.23.0 registered Open Targets, ENA and RCSB PDB with a
tested retrieval layer but deliberately no wiring.  v0.24.0
wires them into the cross-source composite — the Copilot's
default multi-source chain — each only where it genuinely
serves the question:

* Open Targets associations join the merge for variant
  topics (the gene is resolved to its Ensembl ID by an
  injected resolver; the lookup is addressing, never
  evidence);
* a question naming a run accession routes to an ``ena:``
  topic and serves the archive record — NEUTRAL, so the
  Copilot declines with the record shown;
* a question naming a PDB entry in a structure context
  routes to an ``rcsb:`` topic the same way.

Everything runs on fixtures: the corpus writer and the
structured-side transports are copied from
``test_cross_source.py`` (the suite's house pattern of
self-contained test files), and the registered-source
payloads are copied from ``test_source_providers.py``,
where they were recorded from the real services.  No
network.

The behaviours under test, in order of importance:

* the wired providers actually participate: Open Targets
  items appear in the merge, are counted under their own
  registry group, and can supply the gate's second group;
* gate arithmetic is unchanged: evidence from a single
  group still declines, exactly as in v0.22.0, and NEUTRAL
  archive/structure records never count toward a pass;
* fabricated targets still decline through the wired
  chain — a made-up gene gets no second group, and a
  made-up accession gets no record;
* the routing guards hold: a PDB-shaped token without a
  structure context does not hijack a question, and an
  unresolved gene leaves Open Targets unconsulted.
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
from provome.cross_source import (
    REGISTERED_SOURCE_IDS,
    CrossSourceProvider,
    resolve_ensembl_gene_id,
)
from provome.domains import Division
from provome.evidence import (
    SupportDirection,
    VerificationStatus,
    evaluate_gate,
)
from provome.live import LiveEvidenceProvider
from provome.rag import _tokenise
from provome.source_providers import (
    EnaProvider,
    OpenTargetsProvider,
    RcsbPdbProvider,
)

FIXED_DATE = date(2026, 11, 27)

VARIANT_Q = "What is known about CFTR p.Phe508del and cystic fibrosis?"
ENA_Q = "What is known about sequencing run SRR1039508?"
RCSB_Q = "What is the structure of PDB entry 4HHB?"
FRAUD_GENE_Q = "What is known about ZZZQX9 p.Ala1Val and its disease?"
FRAUD_ACCESSION_Q = "What is known about sequencing run SRR9999999?"

CFTR_ENSEMBL_ID = "ENSG00000001626"

# The three genuine corpus records (copied from
# test_cross_source.py): independent published reports
# about the variant.
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

# Registered-source payloads (copied from
# test_source_providers.py; recorded from the real services
# on 2026-11-27).
OPENTARGETS_CFTR = {
    "data": {
        "target": {
            "approvedSymbol": "CFTR",
            "associatedDiseases": {
                "rows": [
                    {
                        "disease": {
                            "id": "MONDO_0009061",
                            "name": "cystic fibrosis",
                        },
                        "score": 0.9211437412666207,
                        "datasourceScores": [
                            {"id": "eva", "score": 0.9989063993262915},
                            {
                                "id": "uniprot_variants",
                                "score": 0.996170566463281,
                            },
                            {
                                "id": "clinical_precedence",
                                "score": 0.993501369004063,
                            },
                            {"id": "europepmc", "score": 0.9979488892388516},
                        ],
                    },
                    {
                        "disease": {
                            "id": "MONDO_0010178",
                            "name": (
                                "congenital bilateral aplasia of vas "
                                "deferens from CFTR mutation"
                            ),
                        },
                        "score": 0.8242369387799563,
                        "datasourceScores": [
                            {
                                "id": "uniprot_variants",
                                "score": 0.9869328024670657,
                            },
                            {"id": "eva", "score": 0.9485295576865702},
                        ],
                    },
                    {
                        "disease": {
                            "id": "MONDO_0008185",
                            "name": "hereditary chronic pancreatitis",
                        },
                        "score": 0.7176978195828634,
                        "datasourceScores": [
                            {
                                "id": "eva",
                                "score": 0.9472714280245705,
                            },
                        ],
                    },
                ]
            },
        }
    }
}

ENA_SRR1039508 = [
    {
        "run_accession": "SRR1039508",
        "study_accession": "PRJNA229998",
        "scientific_name": "Homo sapiens",
        "tax_id": "9606",
        "instrument_platform": "ILLUMINA",
        "library_layout": "PAIRED",
        "read_count": "22935521",
    }
]

RCSB_4HHB = {
    "rcsb_id": "4HHB",
    "struct": {
        "title": (
            "THE CRYSTAL STRUCTURE OF HUMAN DEOXYHAEMOGLOBIN "
            "AT 1.74 ANGSTROMS RESOLUTION"
        )
    },
    "exptl": [{"method": "X-RAY DIFFRACTION"}],
    "rcsb_entry_info": {"resolution_combined": [1.74]},
}


# ---------------------------------------------------------------------
# Fixture corpus writer (copied from test_cross_source.py)
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
# Fixture transports (structured side copied from
# test_cross_source.py; registered sides are payload stubs)
# ---------------------------------------------------------------------


# PubMed's holdings in the fixture world: the three genuine
# papers exist there under their real titles (the same records
# the corpus serves), and nothing else does.  Since v0.25.0
# the cross-source chain re-confirms every corpus item
# against PubMed, so the fixture models PubMed honestly —
# the house pattern of test_live.py's pubmed fixture —
# instead of modelling a PubMed that holds nothing, which
# was the unconfirmed world the pre-v0.25.0 chain lived in.
_PUBMED_TITLES = {
    record["pmid"]: record["title"] for record in _GENUINE_RECORDS
}


def _pubmed_esummary(url):
    pmid = url.split("id=")[-1].split("&")[0]
    title = _PUBMED_TITLES.get(pmid)
    if title is None:
        return {"result": {"uids": []}}
    return {
        "result": {
            "uids": [pmid],
            pmid: {"title": title, "pubtype": ["Journal Article"]},
        }
    }


def cftr_transport(url, body=None):
    """Fixture ClinVar/gnomAD world for CFTR p.Phe508del."""
    if "db=pubmed" in url:
        return _pubmed_esummary(url)
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
    """A structured world with nothing in it.

    PubMed is a different service from the structured
    (ClinVar/gnomAD) world: the genuine papers exist there
    even when ClinVar and gnomAD hold nothing, and the
    chain's re-confirmation (v0.25.0) consults it.
    """
    if "db=pubmed" in url:
        return _pubmed_esummary(url)
    if "esearch" in url:
        return {"esearchresult": {"idlist": []}}
    if "europepmc" in url:
        return {"resultList": {"result": []}}
    raise AssertionError(f"unexpected URL: {url}")


def _payload_transport(payload):
    def transport(url, body=None):
        return payload

    return transport


def _fixed_today():
    return FIXED_DATE


def cftr_resolver(symbol):
    """Fixture gene resolver: CFTR resolves, nothing else does."""
    return CFTR_ENSEMBL_ID if symbol == "CFTR" else None


def make_wired(
    corpus_dir,
    structured_transport=cftr_transport,
    resolver=cftr_resolver,
    ot_payload=OPENTARGETS_CFTR,
    ena_payload=ENA_SRR1039508,
    rcsb_payload=RCSB_4HHB,
):
    corpus = CorpusProvider(corpus_dir)
    structured = LiveEvidenceProvider(
        transport=structured_transport, today=_fixed_today
    )
    return CrossSourceProvider(
        corpus,
        structured,
        opentargets_provider=OpenTargetsProvider(
            transport=_payload_transport(ot_payload), today=_fixed_today
        ),
        ena_provider=EnaProvider(
            transport=_payload_transport(ena_payload), today=_fixed_today
        ),
        rcsb_provider=RcsbPdbProvider(
            transport=_payload_transport(rcsb_payload), today=_fixed_today
        ),
        gene_resolver=resolver,
    )


@pytest.fixture()
def genuine_corpus(tmp_path):
    _write_fixture_corpus(tmp_path / "corpus", _GENUINE_RECORDS)
    return tmp_path / "corpus"


# ---------------------------------------------------------------------
# (a) Open Targets participates in the default chain
# ---------------------------------------------------------------------


def test_registered_source_ids_are_the_three_new_sources():
    assert REGISTERED_SOURCE_IDS == frozenset(
        {"opentargets", "ena", "rcsb-pdb"}
    )


def test_opentargets_items_join_the_variant_merge(genuine_corpus):
    provider = make_wired(genuine_corpus)
    topic = provider.topic_for_question(VARIANT_Q)
    assert topic == "CFTR:p.Phe508del"
    items = provider.items_for_topic(topic)
    sources = {item.source_id for item in items}
    assert sources == {"clinvar", "gnomad", "europepmc", "opentargets"}
    ot_items = [i for i in items if i.source_id == "opentargets"]
    assert len(ot_items) == 3
    # Retagged onto the one variant topic, like the corpus
    # items, with their gene-level statements unrewritten.
    assert {i.topic_key for i in items} == {"CFTR:p.Phe508del"}
    assert all(
        "association between CFTR and" in i.statement for i in ot_items
    )
    assert all(
        i.status is VerificationStatus.VERIFIED for i in ot_items
    )
    assert all(
        i.direction is SupportDirection.SUPPORTS for i in ot_items
    )


def test_gate_counts_opentargets_under_its_own_group(genuine_corpus):
    provider = make_wired(genuine_corpus)
    topic = provider.topic_for_question(VARIANT_Q)
    decision = evaluate_gate(provider.items_for_topic(topic), topic)
    assert decision.passed
    # 3 corpus + 1 ClinVar + 3 Open Targets supporting items.
    assert decision.verified_count == 7
    assert decision.independent_source_count == 3
    assert set(decision.independent_groups) == {
        "europepmc",
        "ncbi-clinvar",
        "opentargets",
    }


def test_copilot_answer_cites_opentargets(genuine_corpus):
    copilot = Copilot(provider=make_wired(genuine_corpus))
    answer = copilot.ask(VARIANT_Q)
    assert answer.verdict is Verdict.ANSWER
    assert answer.independent_source_count == 3
    citations = [
        citation
        for claim in answer.claims
        for citation in claim.citations
    ]
    assert any(c.startswith("Open Targets Platform") for c in citations)


def test_opentargets_can_supply_the_second_group(genuine_corpus):
    # No ClinVar in the structured world: corpus alone would
    # decline on the group rule (the v0.22.0 behaviour).  With
    # the wiring, the Platform's association is the second
    # group — its own registry group, counted once.
    provider = make_wired(
        genuine_corpus, structured_transport=empty_transport
    )
    copilot = Copilot(provider=provider)
    answer = copilot.ask(VARIANT_Q)
    assert answer.verdict is Verdict.ANSWER
    assert answer.verified_item_count == 6  # 3 corpus + 3 OT
    assert answer.independent_source_count == 2


def test_unresolved_gene_leaves_opentargets_unconsulted(genuine_corpus):
    provider = make_wired(genuine_corpus, resolver=lambda symbol: None)
    topic = provider.topic_for_question(VARIANT_Q)
    items = provider.items_for_topic(topic)
    # Exactly the v0.22.0 merge: the wiring adds nothing when
    # the gene cannot be resolved, and invents no ID.
    assert {i.source_id for i in items} == {
        "clinvar",
        "gnomad",
        "europepmc",
    }


# ---------------------------------------------------------------------
# (b) Gate arithmetic is unchanged
# ---------------------------------------------------------------------


def test_single_group_still_declines_in_the_wired_chain(genuine_corpus):
    # Wiring present but nothing resolves and the structured
    # world is empty: corpus evidence alone, one group, the
    # decline stands verbatim.
    provider = make_wired(
        genuine_corpus,
        structured_transport=empty_transport,
        resolver=lambda symbol: None,
    )
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


def test_opentargets_items_alone_still_decline_on_the_group_rule():
    # Three verified supporting items from ONE group never
    # pass, however many associations one query returns.
    provider = OpenTargetsProvider(
        transport=_payload_transport(OPENTARGETS_CFTR),
        today=_fixed_today,
    )
    items = provider.fetch_gene_associations(CFTR_ENSEMBL_ID, "CFTR")
    assert len(items) == 3
    decision = evaluate_gate(items, "cftr")
    assert not decision.passed
    assert decision.verified_count == 3
    assert decision.independent_source_count == 1


# ---------------------------------------------------------------------
# (c) ENA and RCSB PDB participate — as NEUTRAL records that
#     can never manufacture a pass
# ---------------------------------------------------------------------


def test_ena_accession_question_serves_the_record(genuine_corpus):
    provider = make_wired(genuine_corpus)
    topic = provider.topic_for_question(ENA_Q)
    assert topic == "ena:SRR1039508"
    items = provider.items_for_topic(topic)
    assert len(items) == 1
    (item,) = items
    assert item.source_id == "ena"
    assert item.status is VerificationStatus.VERIFIED
    assert item.direction is SupportDirection.NEUTRAL
    assert item.topic_key == "ena:SRR1039508"
    assert "PRJNA229998" in item.statement


def test_ena_record_does_not_answer_the_question(genuine_corpus):
    copilot = Copilot(provider=make_wired(genuine_corpus))
    answer = copilot.ask(ENA_Q)
    # The chain found and shows the verified record, and the
    # gate still declines: provenance is not support.
    assert answer.verdict is Verdict.DECLINE
    assert answer.topic_key == "ena:SRR1039508"
    assert answer.verified_item_count == 0
    assert answer.independent_source_count == 0


def test_rcsb_structure_question_serves_the_record(genuine_corpus):
    provider = make_wired(genuine_corpus)
    topic = provider.topic_for_question(RCSB_Q)
    assert topic == "rcsb:4HHB"
    items = provider.items_for_topic(topic)
    assert len(items) == 1
    (item,) = items
    assert item.source_id == "rcsb-pdb"
    assert item.status is VerificationStatus.VERIFIED
    assert item.direction is SupportDirection.NEUTRAL
    assert "DEOXYHAEMOGLOBIN" in item.statement


def test_rcsb_record_does_not_answer_the_question(genuine_corpus):
    copilot = Copilot(provider=make_wired(genuine_corpus))
    answer = copilot.ask(RCSB_Q)
    assert answer.verdict is Verdict.DECLINE
    assert answer.topic_key == "rcsb:4HHB"
    assert answer.verified_item_count == 0
    assert answer.independent_source_count == 0


def test_pdb_token_without_structure_context_does_not_hijack(
    genuine_corpus,
):
    provider = make_wired(genuine_corpus)
    topic = provider.topic_for_question("Tell me about 4HHB today please")
    # No "PDB"/"structure" context: the question is NOT
    # routed to the structure bank (it falls through to the
    # corpus path, which finds nothing for it here).
    assert topic is None


# ---------------------------------------------------------------------
# (d) Fraud through the wired chain still declines
# ---------------------------------------------------------------------


def test_fraud_fabricated_gene_gets_no_second_group(genuine_corpus):
    provider = make_wired(
        genuine_corpus, structured_transport=empty_transport
    )
    copilot = Copilot(provider=provider)
    answer = copilot.ask(FRAUD_GENE_Q)
    assert answer.verdict is Verdict.DECLINE
    # The resolver does not resolve an invented gene, so Open
    # Targets is never consulted for it; nothing in the wired
    # chain manufactures a second group for a fabrication.
    items = provider.items_for_topic(answer.topic_key)
    assert "opentargets" not in {i.source_id for i in items}
    assert answer.independent_source_count <= 1


def test_fraud_fabricated_accession_gets_no_record(genuine_corpus):
    provider = make_wired(genuine_corpus, ena_payload=[])
    topic = provider.topic_for_question(FRAUD_ACCESSION_Q)
    assert topic == "ena:SRR9999999"
    assert provider.items_for_topic(topic) == ()
    copilot = Copilot(provider=provider)
    answer = copilot.ask(FRAUD_ACCESSION_Q)
    assert answer.verdict is Verdict.DECLINE
    assert answer.verified_item_count == 0


def test_wired_chain_stays_human_scoped(genuine_corpus):
    copilot = Copilot(provider=make_wired(genuine_corpus))
    answer = copilot.ask(
        CopilotQuestion(text=VARIANT_Q, division=Division.ANIMAL)
    )
    assert answer.verdict is Verdict.DECLINE
    assert any(
        "not scoped to" in reason for reason in answer.declined_reasons
    )


# ---------------------------------------------------------------------
# (e) The gene resolver: addressing only, failure-safe
# ---------------------------------------------------------------------


def test_resolve_ensembl_gene_id_uses_the_symbol_lookup():
    seen = []

    def transport(url, body=None):
        seen.append(url)
        return {"id": CFTR_ENSEMBL_ID, "display_name": "CFTR"}

    assert resolve_ensembl_gene_id("CFTR", transport) == CFTR_ENSEMBL_ID
    assert seen == [
        "https://rest.ensembl.org/lookup/symbol/homo_sapiens/CFTR"
        "?content-type=application/json"
    ]


def test_resolve_ensembl_gene_id_fails_closed():
    def raising(url, body=None):
        raise RuntimeError("network is down in the fixture world")

    assert resolve_ensembl_gene_id("CFTR", raising) is None
    assert (
        resolve_ensembl_gene_id(
            "CFTR", lambda url, body=None: {"id": "not-an-id"}
        )
        is None
    )
    assert (
        resolve_ensembl_gene_id("CFTR", lambda url, body=None: {})
        is None
    )
    assert resolve_ensembl_gene_id("", raising) is None
