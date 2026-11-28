"""Red-team round, 2026-11-28: attacks on the wired default chain.

Stage 1 gate work.  Every attack runs through the chain as it
is actually composed — :class:`provome.copilot.Copilot` over
:class:`provome.cross_source.CrossSourceProvider` (corpus +
ClinVar/gnomAD structured side + the v0.24.0 registered
sources: Open Targets, ENA, RCSB PDB) — with fixture
transports, fully offline.

Threat model (the one the 2026-11-25 hardening round used):
a fixture transport simulates the upstream service for the
source under attack.  For a *poisoned-feed* attack, that one
feed answers with fabricated or altered records while every
other endpoint answers honestly or empty.  For a *planted-
corpus* attack, the corpus directory — an input to the chain —
carries records a real ingest should never have admitted.

Every test encodes the EXPECTED behaviour: decline, do not
count the poisoned item, disclose the refutation.  A test
that fails is a successful attack, and the failure is the
finding's evidence.  No production code is modified by this
file; findings are documented in
``~/workspace/provome-hardening/RED_TEAM_2026-11-28.md``.

Attack classes:
  1. Fabricated genes and variants.
  2. Fake papers (invented PMIDs, altered claims, retracted).
  3. Source-group forgery.
  4. Refutation suppression.
  5. v0.24 routing attacks (PDB/accession hijack, ENA/RCSB
     dressing, Open Targets rewriting).
  6. Confirmation bypass.
"""

import gzip
import json
import math
import struct
import urllib.parse
import zipfile
from array import array
from collections import Counter
from datetime import date

import pytest

from provome.confirmation import (
    confirm_clinvar,
    confirm_literature,
    confirm_literature_detail,
)
from provome.copilot import Copilot, Verdict
from provome.corpus_provider import CorpusProvider
from provome.cross_source import CrossSourceProvider
from provome.evidence import VerificationStatus, evaluate_gate
from provome.live import LiveEvidenceProvider
from provome.rag import _tokenise
from provome.source_providers import (
    EnaProvider,
    OpenTargetsProvider,
    RcsbPdbProvider,
)

FIXED_DATE = date(2026, 11, 28)

VARIANT_Q = "What is known about CFTR p.Phe508del and cystic fibrosis?"
CFTR_ENSEMBL_ID = "ENSG00000001626"

# ---------------------------------------------------------------------
# Corpus records
# ---------------------------------------------------------------------

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


def _record(pmid, title, abstract, year="2023"):
    return {
        "pmid": pmid,
        "title": title,
        "abstract": abstract,
        "year": year, "journal": "Fixture Journal",
        "division": "human", "query_id": "fx",
    }


# ---------------------------------------------------------------------
# Registered-source payloads (recorded shapes, copied from
# test_cross_source_registered.py / test_source_providers.py)
# ---------------------------------------------------------------------

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

# The same associations, but every row's ONLY datasource is
# Europe PMC — the same literature index the corpus is built
# from (class 3: one underlying source dressed as two groups).
OPENTARGETS_EUROPEPMC_ONLY = {
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
                        "score": 0.91,
                        "datasourceScores": [
                            {"id": "europepmc", "score": 0.99}
                        ],
                    },
                    {
                        "disease": {
                            "id": "MONDO_0010178",
                            "name": (
                                "congenital bilateral aplasia of "
                                "vas deferens from CFTR mutation"
                            ),
                        },
                        "score": 0.82,
                        "datasourceScores": [
                            {"id": "europepmc", "score": 0.97}
                        ],
                    },
                    {
                        "disease": {
                            "id": "MONDO_0008185",
                            "name": "hereditary chronic pancreatitis",
                        },
                        "score": 0.71,
                        "datasourceScores": [
                            {"id": "europepmc", "score": 0.95}
                        ],
                    },
                ]
            },
        }
    }
}

# Associations whose own scores say there is no association
# (class 5: a record that refutes-by-content, dressed as
# support by the parser's hardcoded direction).
OPENTARGETS_ZERO_SCORE = {
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
                        "score": 0.0,
                        "datasourceScores": [],
                    },
                    {
                        "disease": {
                            "id": "MONDO_0010178",
                            "name": (
                                "congenital bilateral aplasia of "
                                "vas deferens from CFTR mutation"
                            ),
                        },
                        "score": 0.0,
                        "datasourceScores": [],
                    },
                    {
                        "disease": {
                            "id": "MONDO_0008185",
                            "name": "hereditary chronic pancreatitis",
                        },
                        "score": 0.0,
                        "datasourceScores": [],
                    },
                ]
            },
        }
    }
}

# A payload for a DIFFERENT gene, served in answer to a CFTR
# query (class 5: misattributed associations).
OPENTARGETS_WRONG_GENE = {
    "data": {
        "target": {
            "approvedSymbol": "BRCA1",
            "associatedDiseases": {
                "rows": [
                    {
                        "disease": {
                            "id": "MONDO_0011450",
                            "name": (
                                "hereditary breast and ovarian "
                                "cancer syndrome"
                            ),
                        },
                        "score": 0.95,
                        "datasourceScores": [
                            {"id": "eva", "score": 0.99}
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
# Fixture transports
# ---------------------------------------------------------------------


def _fixed_today():
    return FIXED_DATE


def _payload_transport(payload):
    def transport(url, body=None):
        return payload

    return transport


def clinvar_record(classification, title=None, protein_change="F508del"):
    """One ClinVar esummary record for CFTR p.Phe508del."""
    return {
        "title": title
        or "NM_000492.4(CFTR):c.1521_1523delCTT (p.Phe508del)",
        "protein_change": protein_change,
        "germline_classification": {
            "description": classification,
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
    }


def make_structured_transport(
    *,
    clinvar_uids=(),
    clinvar_records=None,
    fake_term_markers=(),
):
    """URL-routing fixture for the ClinVar/gnomAD side.

    ``fake_term_markers``: substrings of the (decoded, lowered)
    esearch term for which ClinVar honestly has nothing — the
    fabricated-variant searches.  Every other endpoint answers
    honestly or empty.
    """
    records = clinvar_records or {}

    def transport(url, body=None):
        if "db=pubmed" in url:
            return {"result": {"uids": []}}
        if "db=snp" in url:
            return {"esearchresult": {"idlist": []}}
        if "esearch" in url and "db=clinvar" in url:
            term = urllib.parse.unquote(url).lower()
            if any(marker in term for marker in fake_term_markers):
                return {"esearchresult": {"idlist": []}}
            return {"esearchresult": {"idlist": list(clinvar_uids)}}
        if "esummary" in url and "db=clinvar" in url:
            result = {"uids": list(records)}
            result.update(records)
            return {"result": result}
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

    return transport


#: The honest structured world for CFTR p.Phe508del: one real
#: ClinVar record, Pathogenic.
GENUINE_STRUCTURED = make_structured_transport(
    clinvar_uids=["12139"],
    clinvar_records={"12139": clinvar_record("Pathogenic")},
)

#: A structured world with nothing in it.
EMPTY_STRUCTURED = make_structured_transport()


def cftr_resolver(symbol):
    return CFTR_ENSEMBL_ID if symbol == "CFTR" else None


def make_wired(
    corpus_dir,
    *,
    structured_transport=GENUINE_STRUCTURED,
    resolver=cftr_resolver,
    ot_payload=OPENTARGETS_CFTR,
    ena_payload=ENA_SRR1039508,
    rcsb_transport=None,
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
            transport=rcsb_transport
            or _payload_transport(RCSB_4HHB),
            today=_fixed_today,
        ),
        gene_resolver=resolver,
    )


@pytest.fixture()
def genuine_corpus(tmp_path):
    _write_fixture_corpus(tmp_path / "corpus", _GENUINE_RECORDS)
    return tmp_path / "corpus"


def _corpus(tmp_path, records):
    _write_fixture_corpus(tmp_path / "corpus", records)
    return tmp_path / "corpus"


# =====================================================================
# Class 1 — fabricated genes and variants
# =====================================================================


def test_class1_fabricated_variant_of_real_gene_declines(genuine_corpus):
    # CFTR p.Ala123Val does not exist in any fixture source:
    # ClinVar honestly returns nothing for it, the corpus holds
    # only p.Phe508del papers, Open Targets knows the GENE only.
    # Expected: DECLINE — no evidence is about this variant.
    transport = make_structured_transport(
        clinvar_uids=["12139"],
        clinvar_records={"12139": clinvar_record("Pathogenic")},
        fake_term_markers=("ala123val", "a123v"),
    )
    copilot = Copilot(
        provider=make_wired(
            genuine_corpus, structured_transport=transport
        )
    )
    answer = copilot.ask(
        "What is known about CFTR p.Ala123Val and cystic fibrosis?"
    )
    assert answer.verdict is Verdict.DECLINE, (
        f"fabricated variant answered: {answer.verified_item_count} "
        f"items from {answer.independent_source_count} groups; "
        f"claims: {[c.statement for c in answer.claims]}"
    )


def test_class1_fabricated_coding_variant_of_real_gene_declines(
    genuine_corpus,
):
    # Same attack through the c. notation path.
    transport = make_structured_transport(
        clinvar_uids=["12139"],
        clinvar_records={"12139": clinvar_record("Pathogenic")},
        fake_term_markers=("9999",),
    )
    copilot = Copilot(
        provider=make_wired(
            genuine_corpus, structured_transport=transport
        )
    )
    answer = copilot.ask(
        "What is known about CFTR c.9999G>T and cystic fibrosis?"
    )
    assert answer.verdict is Verdict.DECLINE, (
        f"fabricated variant answered: {answer.verified_item_count} "
        f"items from {answer.independent_source_count} groups; "
        f"claims: {[c.statement for c in answer.claims]}"
    )


def test_class1_fabricated_gene_declines(genuine_corpus):
    # An invented gene with an invented variant, every source
    # honest.  Expected: DECLINE, and zero items counted —
    # no source holds any evidence about ZZZQX9 at all, so
    # nothing may be counted toward its gate, let alone
    # manufactured.  (The verdict assertion and the count
    # assertion are reported separately in the red-team
    # report: they do not stand or fall together.)
    copilot = Copilot(
        provider=make_wired(
            genuine_corpus, structured_transport=EMPTY_STRUCTURED
        )
    )
    answer = copilot.ask(
        "What is known about ZZZQX9 p.Ala1Val and its disease?"
    )
    assert answer.verdict is Verdict.DECLINE
    assert answer.independent_source_count == 0, (
        f"items counted for an invented gene: "
        f"{answer.verified_item_count} verified item(s) from "
        f"{answer.independent_source_count} group(s)"
    )
    assert answer.verified_item_count == 0
    items = copilot.provider.items_for_topic(answer.topic_key)
    assert "opentargets" not in {i.source_id for i in items}


# =====================================================================
# Class 2 — fake papers
# =====================================================================


def test_class2_invented_pmid_in_corpus_does_not_count(tmp_path):
    # A planted corpus record whose PMID exists in no index:
    # PubMed has never heard of 99999999.  Expected: the item
    # never counts toward the gate and is never cited.
    records = _GENUINE_RECORDS[:2] + [
        _record(
            "99999999",
            "CFTR Phe508del eliminated by compound QX in cystic "
            "fibrosis trial",
            "Compound QX eliminated Phe508del CFTR disease in a "
            "cystic fibrosis trial.",
        )
    ]
    copilot = Copilot(provider=make_wired(_corpus(tmp_path, records)))
    answer = copilot.ask(VARIANT_Q)
    citations = [
        citation
        for claim in answer.claims
        for citation in claim.citations
    ]
    assert not any("99999999" in c for c in citations), (
        f"invented PMID cited in an answer: {citations}"
    )


def test_class2_real_pmid_altered_claims_do_not_count_as_support(
    tmp_path,
):
    # The genuine record's PMID, with its claim rewritten to
    # the opposite conclusion.  Expected: the altered item is
    # not counted as supporting evidence.
    altered = _record(
        "91000001",
        "CFTR Phe508del conclusively shown benign and unrelated "
        "to cystic fibrosis",
        "Controlled data show CFTR Phe508del is benign and "
        "unrelated to cystic fibrosis.",
        year="2020",
    )
    records = [altered] + _GENUINE_RECORDS[1:]
    provider = make_wired(_corpus(tmp_path, records))
    topic = provider.topic_for_question(VARIANT_Q)
    decision = evaluate_gate(provider.items_for_topic(topic), topic)
    supporting_ids = {i.item_id for i in decision.verified_supporting}
    assert "corpus-pmid-91000001" not in supporting_ids, (
        "altered-claims item counted as verified support"
    )


def test_class2_retracted_paper_does_not_count(tmp_path):
    # PMID 91000077 is, in the fixture PubMed world, a retracted
    # publication.  The codebase's own confirmer refuses it
    # (asserted first, as the contrast); the chain is expected
    # to refuse it too.
    title = "CFTR Phe508del corrector response in cystic fibrosis organoids"

    def pubmed_transport(url, body=None):
        return {
            "result": {
                "uids": ["91000077"],
                "91000077": {
                    "title": title,
                    "pubtype": ["Journal Article", "Retracted Publication"],
                },
            }
        }

    status, _note, retracted = confirm_literature_detail(
        pubmed_transport, "91000077", title
    )
    assert status is VerificationStatus.FAILED and retracted

    records = _GENUINE_RECORDS[:2] + [
        _record(
            "91000077",
            title,
            "Corrector compounds restored Phe508del CFTR function "
            "in cystic fibrosis organoids.",
        )
    ]
    copilot = Copilot(provider=make_wired(_corpus(tmp_path, records)))
    answer = copilot.ask(VARIANT_Q)
    citations = [
        citation
        for claim in answer.claims
        for citation in claim.citations
    ]
    assert not any("91000077" in c for c in citations), (
        f"retracted paper cited in an answer: {citations}"
    )


def test_class2_nonnumeric_pmid_is_dropped(tmp_path):
    # Control: records whose "PMID" is not numeric fail the
    # corpus read-time check and are dropped, so the question
    # declines on the remaining single ClinVar item.
    records = [
        _record("PMC910001", _GENUINE_RECORDS[0]["title"],
                _GENUINE_RECORDS[0]["abstract"], year="2020"),
        _record("PMC910002", _GENUINE_RECORDS[1]["title"],
                _GENUINE_RECORDS[1]["abstract"], year="2021"),
        _record("PMC910003", _GENUINE_RECORDS[2]["title"],
                _GENUINE_RECORDS[2]["abstract"], year="2019"),
    ]
    # Open Targets is pinned off (resolver resolves nothing)
    # so the test isolates the corpus drop: with the records
    # dropped, only the single ClinVar item remains.
    provider = make_wired(
        _corpus(tmp_path, records), resolver=lambda symbol: None
    )
    copilot = Copilot(provider=provider)
    answer = copilot.ask(VARIANT_Q)
    assert answer.verdict is Verdict.DECLINE
    assert provider.corpus_provider.dropped_records >= 3


# =====================================================================
# Class 3 — source-group forgery
# =====================================================================

# Titles that restate the ClinVar record's classification
# without ever naming ClinVar; the abstracts admit the
# restatement outright.  The v0.22.0 absorption guard keys on
# the title naming the database and the variant.
_RESTATEMENT_RECORDS = [
    _record(
        "92000011",
        "CFTR Phe508del classified pathogenic in cystic fibrosis",
        "This note restates the ClinVar classification of CFTR "
        "Phe508del as pathogenic in cystic fibrosis.",
    ),
    _record(
        "92000012",
        "Pathogenic classification of CFTR Phe508del restated "
        "for cystic fibrosis care",
        "A restatement of the ClinVar classification of CFTR "
        "Phe508del, pathogenic for cystic fibrosis.",
    ),
    _record(
        "92000013",
        "CFTR Phe508del: pathogenic in cystic fibrosis, database "
        "entry summarised",
        "Summary of the ClinVar record for CFTR Phe508del and "
        "its pathogenic classification in cystic fibrosis.",
    ),
]


def test_class3_record_reports_paraphrased_still_absorbed(tmp_path):
    # The only literature in the corpus is restatements of the
    # ClinVar record itself.  Expected: they cannot manufacture
    # the second group, so the question declines on the one
    # remaining ClinVar item.
    provider = make_wired(_corpus(tmp_path, _RESTATEMENT_RECORDS))
    copilot = Copilot(provider=provider)
    answer = copilot.ask(VARIANT_Q)
    assert answer.verdict is Verdict.DECLINE, (
        f"record restatements manufactured a pass: "
        f"{answer.verified_item_count} items from "
        f"{answer.independent_source_count} groups; "
        f"absorbed={provider.absorbed_duplicate_records}"
    )


def test_class3_same_paper_under_three_pmids_counts_once(tmp_path):
    # One paper planted three times under three PMIDs.
    # Expected: one evidence line counts once, leaving
    # 1 paper + 1 ClinVar item = 2 items, below the gate.
    one_paper = _GENUINE_RECORDS[0]
    records = [
        _record("93000001", one_paper["title"], one_paper["abstract"],
                year="2020"),
        _record("93000002", one_paper["title"], one_paper["abstract"],
                year="2020"),
        _record("93000003", one_paper["title"], one_paper["abstract"],
                year="2020"),
    ]
    copilot = Copilot(provider=make_wired(_corpus(tmp_path, records)))
    answer = copilot.ask(VARIANT_Q)
    assert answer.verdict is Verdict.DECLINE, (
        f"one paper under three PMIDs counted as "
        f"{answer.verified_item_count} items"
    )


def test_class3_opentargets_europepmc_only_is_not_a_second_group(
    genuine_corpus,
):
    # Open Targets supplies the "second group", but in this
    # payload every association score derives solely from
    # Europe PMC — the same literature index the corpus items
    # come from.  Expected: one underlying source is one
    # group, so the question declines.
    copilot = Copilot(
        provider=make_wired(
            genuine_corpus,
            structured_transport=EMPTY_STRUCTURED,
            ot_payload=OPENTARGETS_EUROPEPMC_ONLY,
        )
    )
    answer = copilot.ask(VARIANT_Q)
    assert answer.verdict is Verdict.DECLINE, (
        f"Europe PMC counted twice under two group labels: "
        f"{answer.verified_item_count} items from "
        f"{answer.independent_source_count} groups "
        f"{answer.independent_source_count and ''}"
    )


def test_class3_single_group_flood_still_declines(tmp_path):
    # Control: ten genuine-style records, all one group, no
    # second source anywhere.  Flooding the count must not
    # fake the group rule.
    extra = [
        _record(
            f"910000{i:02d}",
            f"CFTR Phe508del study {i} in cystic fibrosis",
            "A study of CFTR Phe508del in cystic fibrosis "
            "patients and model systems.",
        )
        for i in range(4, 11)
    ]
    provider = make_wired(
        _corpus(tmp_path, _GENUINE_RECORDS + extra),
        structured_transport=EMPTY_STRUCTURED,
        resolver=lambda symbol: None,
    )
    copilot = Copilot(provider=provider)
    answer = copilot.ask(VARIANT_Q)
    assert answer.verdict is Verdict.DECLINE
    assert answer.verified_item_count == 10
    assert answer.independent_source_count == 1


# =====================================================================
# Class 4 — refutation suppression
# =====================================================================


def test_class4_refuting_item_is_disclosed_never_clean(genuine_corpus):
    # One verified refuting item (ClinVar: Benign) alongside
    # plentiful support.  Below the block threshold the gate
    # may pass, but the answer must carry the disclosure —
    # never a clean answer.
    transport = make_structured_transport(
        clinvar_uids=["12139", "12140"],
        clinvar_records={
            "12139": clinvar_record("Pathogenic"),
            "12140": clinvar_record("Benign"),
        },
    )
    copilot = Copilot(
        provider=make_wired(
            genuine_corpus, structured_transport=transport
        )
    )
    answer = copilot.ask(VARIANT_Q)
    assert answer.verdict is Verdict.ANSWER
    assert any(
        "refut" in warning.lower() for warning in answer.warnings
    ), f"answer issued clean despite a refuting item: {answer.warnings}"


def test_class4_refutation_at_half_blocks(genuine_corpus):
    # Two verified refuting items against four supporting
    # items reaches half the supporting count: the gate must
    # block, not answer.
    transport = make_structured_transport(
        clinvar_uids=["12139", "12140", "12141"],
        clinvar_records={
            "12139": clinvar_record("Pathogenic"),
            "12140": clinvar_record("Benign"),
            "12141": clinvar_record("Likely benign"),
        },
    )
    copilot = Copilot(
        provider=make_wired(
            genuine_corpus,
            structured_transport=transport,
            resolver=lambda symbol: None,
        )
    )
    answer = copilot.ask(VARIANT_Q)
    assert answer.verdict is Verdict.DECLINE
    assert any(
        "refut" in reason.lower()
        for reason in answer.declined_reasons
    )


def test_class4_refuting_corpus_paper_is_not_counted_as_support(
    tmp_path,
):
    # A corpus paper whose own title refutes the claim.
    # Expected: it is not counted among the supporting items.
    records = _GENUINE_RECORDS[:2] + [
        _record(
            "91000009",
            "CFTR Phe508del does not cause cystic fibrosis: a "
            "controlled reappraisal",
            "In a controlled reappraisal, CFTR Phe508del was not "
            "associated with cystic fibrosis.",
        )
    ]
    provider = make_wired(_corpus(tmp_path, records))
    topic = provider.topic_for_question(VARIANT_Q)
    decision = evaluate_gate(provider.items_for_topic(topic), topic)
    supporting_ids = {i.item_id for i in decision.verified_supporting}
    assert "corpus-pmid-91000009" not in supporting_ids, (
        "a paper refuting the claim was counted as supporting it"
    )


# =====================================================================
# Class 5 — v0.24 routing attacks
# =====================================================================


def test_class5_accession_and_pdb_tokens_do_not_hijack_variant_question(
    genuine_corpus,
):
    # Control: an accession and a PDB token planted in a
    # variant question.  The variant topic must win; no ENA or
    # RCSB items may enter the merge.
    provider = make_wired(genuine_corpus)
    copilot = Copilot(provider=provider)
    answer = copilot.ask(
        VARIANT_Q + " (run SRR1039508; see also PDB entry 4HHB structure)"
    )
    assert answer.topic_key == "CFTR:p.Phe508del"
    items = provider.items_for_topic(answer.topic_key)
    assert not {i.source_id for i in items} & {"ena", "rcsb-pdb"}
    assert answer.verdict is Verdict.ANSWER


def test_class5_accession_question_asking_for_proof_declines(
    genuine_corpus,
):
    # An accession question phrased as a proof question: the
    # archive record is NEUTRAL provenance, so the gate must
    # decline with zero supporting items.
    copilot = Copilot(provider=make_wired(genuine_corpus))
    answer = copilot.ask(
        "Does sequencing run SRR1039508 prove that CFTR causes "
        "cystic fibrosis?"
    )
    assert answer.topic_key == "ena:SRR1039508"
    assert answer.verdict is Verdict.DECLINE
    assert answer.verified_item_count == 0


def test_class5_year_token_does_not_become_a_structure_record(
    genuine_corpus,
):
    # "2024" is a PDB-shaped token and the question says
    # "structure", so the router sends it to RCSB — where no
    # such entry exists.  Expected: a decline, no record, no
    # invented answer.  (The routing quirk itself is recorded
    # in the red-team report.)
    def rcsb_by_id_transport(url, body=None):
        if url.rstrip("/").endswith("/4HHB"):
            return RCSB_4HHB
        return {}

    provider = make_wired(
        genuine_corpus, rcsb_transport=rcsb_by_id_transport
    )
    copilot = Copilot(provider=provider)
    answer = copilot.ask(
        "Summarise the 2024 structure of evidence for cystic "
        "fibrosis genetics."
    )
    assert answer.topic_key == "rcsb:2024"
    assert answer.verdict is Verdict.DECLINE
    assert answer.verified_item_count == 0


def test_class5_rcsb_mirror_returning_wrong_entry_still_declines(
    genuine_corpus,
):
    # A poisoned RCSB mirror returns the 4HHB record whatever
    # entry was asked for.  The served item is NEUTRAL and the
    # gate must still decline with zero supporting items.
    provider = make_wired(
        genuine_corpus,
        rcsb_transport=_payload_transport(RCSB_4HHB),
    )
    copilot = Copilot(provider=provider)
    answer = copilot.ask("What is the structure of PDB entry 9ZZZ?")
    assert answer.topic_key == "rcsb:9ZZZ"
    assert answer.verdict is Verdict.DECLINE
    assert answer.verified_item_count == 0


def test_class5_opentargets_zero_score_rows_do_not_support(
    genuine_corpus,
):
    # Open Targets rows whose own overall score is 0.000 —
    # the Platform reporting no association.  Expected: they
    # are not supporting evidence, so with no ClinVar the
    # question declines on the group rule.
    copilot = Copilot(
        provider=make_wired(
            genuine_corpus,
            structured_transport=EMPTY_STRUCTURED,
            ot_payload=OPENTARGETS_ZERO_SCORE,
        )
    )
    answer = copilot.ask(VARIANT_Q)
    assert answer.verdict is Verdict.DECLINE, (
        f"zero-score associations supplied a pass: "
        f"{answer.verified_item_count} items from "
        f"{answer.independent_source_count} groups"
    )


def test_class5_opentargets_wrong_gene_payload_does_not_count(
    genuine_corpus,
):
    # The Platform (or a poisoned mirror) answers a CFTR query
    # with a BRCA1 payload.  Expected: another gene's
    # associations never appear as claims in a CFTR answer.
    copilot = Copilot(
        provider=make_wired(
            genuine_corpus, ot_payload=OPENTARGETS_WRONG_GENE
        )
    )
    answer = copilot.ask(VARIANT_Q)
    statements = [claim.statement for claim in answer.claims]
    assert not any("BRCA1" in s for s in statements), (
        f"wrong-gene associations issued as claims: {statements}"
    )


# =====================================================================
# Class 6 — confirmation bypass
# =====================================================================


def test_class6_corpus_items_skip_pubmed_confirmation(tmp_path):
    # The pair (PMID 91000001, altered title) is REFUSED by the
    # codebase's own literature confirmer against an honest
    # PubMed (asserted first).  Expected: the chain refuses it
    # too — an item that cannot pass confirmation must not
    # count as verified support anywhere in the chain.
    altered_title = (
        "CFTR Phe508del: consortium announces breakthrough "
        "therapy results"
    )

    def pubmed_transport(url, body=None):
        return {
            "result": {
                "uids": ["91000001"],
                "91000001": {
                    "title": _GENUINE_RECORDS[0]["title"],
                    "pubtype": ["Journal Article"],
                },
            }
        }

    status, _note = confirm_literature(
        pubmed_transport, "91000001", altered_title
    )
    assert status is VerificationStatus.FAILED

    records = [
        _record(
            "91000001",
            altered_title,
            "A consortium announces breakthrough therapy results "
            "for CFTR Phe508del in cystic fibrosis.",
            year="2020",
        )
    ] + _GENUINE_RECORDS[1:]
    provider = make_wired(_corpus(tmp_path, records))
    topic = provider.topic_for_question(VARIANT_Q)
    decision = evaluate_gate(provider.items_for_topic(topic), topic)
    supporting_ids = {i.item_id for i in decision.verified_supporting}
    assert "corpus-pmid-91000001" not in supporting_ids, (
        "an item the PubMed confirmer refuses was counted as "
        "verified support through the corpus path"
    )


def test_class6_fabricated_clinvar_record_does_not_count(
    genuine_corpus,
):
    # A poisoned ClinVar feed invents Variation ID 999999 for
    # the real variant, classified Pathogenic.  Expected: a
    # record ClinVar does not hold never counts, so the
    # question declines on the corpus's single group.
    transport = make_structured_transport(
        clinvar_uids=["999999"],
        clinvar_records={"999999": clinvar_record("Pathogenic")},
    )
    copilot = Copilot(
        provider=make_wired(
            genuine_corpus,
            structured_transport=transport,
            resolver=lambda symbol: None,
        )
    )
    answer = copilot.ask(VARIANT_Q)
    citations = [
        citation
        for claim in answer.claims
        for citation in claim.citations
    ]
    assert answer.verdict is Verdict.DECLINE, (
        f"fabricated ClinVar record manufactured a pass: "
        f"{answer.verified_item_count} items, citations {citations}"
    )
    assert not any("999999" in c for c in citations)


def test_class6_clinvar_confirmer_would_catch_the_fabrication():
    # Control: the independent ClinVar confirmer exists in the
    # codebase and refuses the fabricated ID against an honest
    # ClinVar — the finding in the previous test is that this
    # chain never calls it.
    def honest_transport(url, body=None):
        return {"result": {"uids": []}}

    status, direction, _note = confirm_clinvar(
        honest_transport, "999999", "CFTR:p.Phe508del"
    )
    assert status is VerificationStatus.FAILED
    assert direction is None
