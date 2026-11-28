"""Tests for the registered-source providers (v0.23.0).

Mirrors the Europe PMC registration tests in
``test_corpus_provider.py``: registration present, independence
group correct, and retrieval/parsing exercised entirely on
recorded fixtures — no live network in the suite.

The fixtures below were recorded from the real services on
2026-11-27, during the Founder-ordered verification pass:

* Open Targets GraphQL for CFTR (ENSG00000001626): the three
  top association rows exactly as served, scores included.
* ENA Portal API read-run record for SRR1039508, exactly as
  served.
* RCSB Data API entry 4HHB, trimmed to the fields the parser
  reads (``rcsb_id``, ``struct.title``, ``exptl``,
  ``rcsb_entry_info.resolution_combined``), values as served.
"""

from datetime import date

import pytest

from provome.evidence import (
    SupportDirection,
    VerificationStatus,
    default_source_registry,
)
from provome.source_providers import (
    EnaProvider,
    OpenTargetsProvider,
    RcsbPdbProvider,
)

FIXED_DAY = date(2026, 11, 27)


def _fixed_today():
    return FIXED_DAY


# ---------------------------------------------------------------------
# Recorded fixtures
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
                            {"id": "eva", "score": 0.9472714280245705},
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


class FixtureTransport:
    """Returns a recorded payload; records the URL it was given."""

    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def __call__(self, url, body=None):
        self.urls.append(url)
        return self.payload


def _raising_transport(url, body=None):
    raise RuntimeError("network is down in the fixture world")


# ---------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------


def test_opentargets_is_registered_as_exactly_one_group():
    registry = default_source_registry()
    assert "opentargets" in registry
    assert registry.independence_group_of("opentargets") == "opentargets"


def test_ena_is_registered_as_exactly_one_group():
    registry = default_source_registry()
    assert "ena" in registry
    assert registry.independence_group_of("ena") == "ena"


def test_rcsb_pdb_is_registered_as_exactly_one_group():
    registry = default_source_registry()
    assert "rcsb-pdb" in registry
    assert registry.independence_group_of("rcsb-pdb") == "rcsb-pdb"


def test_new_groups_are_distinct_from_existing_groups():
    registry = default_source_registry()
    new_groups = {
        registry.independence_group_of(source_id)
        for source_id in ("opentargets", "ena", "rcsb-pdb")
    }
    # Three sources, three groups — none is another under a new name.
    assert len(new_groups) == 3
    existing = {
        registry.independence_group_of(source_id)
        for source_id in (
            "clinvar",
            "gnomad",
            "pubmed-literature",
            "europepmc",
            "omim",
            "uniprot",
            "chembl",
            "ensembl",
        )
    }
    assert new_groups.isdisjoint(existing)


def test_kegg_and_mygene_are_deliberately_not_registered():
    # KEGG is held on licence grounds (commercial licence
    # required for non-academic use); MyGene.info is an
    # aggregator and the registry has no non-evidence plumbing
    # category.  Neither may appear as an evidence source.
    registry = default_source_registry()
    assert "kegg" not in registry
    assert "mygene" not in registry
    assert "mygene-info" not in registry


# ---------------------------------------------------------------------
# Open Targets retrieval
# ---------------------------------------------------------------------


def test_opentargets_fetch_builds_verified_association_items():
    transport = FixtureTransport(OPENTARGETS_CFTR)
    provider = OpenTargetsProvider(
        transport=transport, today=_fixed_today
    )
    items = provider.fetch_gene_associations("ENSG00000001626", "CFTR")
    assert len(items) == 3
    first = items[0]
    assert first.source_id == "opentargets"
    assert first.status is VerificationStatus.VERIFIED
    assert first.direction is SupportDirection.SUPPORTS
    assert first.topic_key == "cftr"
    assert first.item_id == "opentargets-ENSG00000001626-MONDO_0009061"
    assert "cystic fibrosis" in first.statement
    assert "0.921" in first.statement
    assert "2026-11-27" in first.citation
    assert first.locator == (
        "https://platform.opentargets.org/disease/MONDO_0009061"
    )
    # The GraphQL endpoint was the one queried.
    assert transport.urls == [
        "https://api.platform.opentargets.org/api/v4/graphql"
    ]


def test_opentargets_empty_or_broken_payload_yields_no_items():
    provider = OpenTargetsProvider(
        transport=FixtureTransport({}), today=_fixed_today
    )
    assert provider.fetch_gene_associations("ENSG00000001626", "CFTR") == ()
    provider = OpenTargetsProvider(
        transport=_raising_transport, today=_fixed_today
    )
    assert provider.fetch_gene_associations("ENSG00000001626", "CFTR") == ()


def test_opentargets_rejects_a_non_ensembl_id():
    provider = OpenTargetsProvider(
        transport=FixtureTransport(OPENTARGETS_CFTR), today=_fixed_today
    )
    with pytest.raises(ValueError):
        provider.fetch_gene_associations("CFTR", "CFTR")


# ---------------------------------------------------------------------
# ENA retrieval
# ---------------------------------------------------------------------


def test_ena_fetch_builds_a_neutral_archive_item():
    transport = FixtureTransport(ENA_SRR1039508)
    provider = EnaProvider(transport=transport, today=_fixed_today)
    items = provider.fetch_run("SRR1039508")
    assert len(items) == 1
    (item,) = items
    assert item.source_id == "ena"
    assert item.status is VerificationStatus.VERIFIED
    # An archive record is provenance, never support for a claim.
    assert item.direction is SupportDirection.NEUTRAL
    assert "SRR1039508" in item.statement
    assert "Homo sapiens" in item.statement
    assert "PRJNA229998" in item.statement
    assert "2026-11-27" in item.citation
    assert item.locator == (
        "https://www.ebi.ac.uk/ena/browser/view/SRR1039508"
    )
    assert "portal/api/search" in transport.urls[0]


def test_ena_empty_or_broken_payload_yields_no_items():
    provider = EnaProvider(
        transport=FixtureTransport([]), today=_fixed_today
    )
    assert provider.fetch_run("SRR1039508") == ()
    provider = EnaProvider(
        transport=_raising_transport, today=_fixed_today
    )
    assert provider.fetch_run("SRR1039508") == ()


def test_ena_rejects_a_non_accession():
    provider = EnaProvider(
        transport=FixtureTransport(ENA_SRR1039508), today=_fixed_today
    )
    with pytest.raises(ValueError):
        provider.fetch_run("not an accession")


# ---------------------------------------------------------------------
# RCSB PDB retrieval
# ---------------------------------------------------------------------


def test_rcsb_fetch_builds_a_neutral_structure_item():
    transport = FixtureTransport(RCSB_4HHB)
    provider = RcsbPdbProvider(transport=transport, today=_fixed_today)
    items = provider.fetch_entry("4HHB")
    assert len(items) == 1
    (item,) = items
    assert item.source_id == "rcsb-pdb"
    assert item.status is VerificationStatus.VERIFIED
    # A structure record is context, never support for a claim.
    assert item.direction is SupportDirection.NEUTRAL
    assert "DEOXYHAEMOGLOBIN" in item.statement
    assert "1.74" in item.statement
    assert "4HHB" in item.citation
    assert "2026-11-27" in item.citation
    assert item.locator == "https://www.rcsb.org/structure/4HHB"
    assert transport.urls == [
        "https://data.rcsb.org/rest/v1/core/entry/4HHB"
    ]


def test_rcsb_empty_or_broken_payload_yields_no_items():
    provider = RcsbPdbProvider(
        transport=FixtureTransport({}), today=_fixed_today
    )
    assert provider.fetch_entry("4HHB") == ()
    provider = RcsbPdbProvider(
        transport=_raising_transport, today=_fixed_today
    )
    assert provider.fetch_entry("4HHB") == ()


def test_rcsb_rejects_a_non_pdb_id():
    provider = RcsbPdbProvider(
        transport=FixtureTransport(RCSB_4HHB), today=_fixed_today
    )
    with pytest.raises(ValueError):
        provider.fetch_entry("HHB")
