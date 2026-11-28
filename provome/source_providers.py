"""Registered-source providers: Open Targets, ENA, RCSB PDB.

Source expansion ordered by the Founder on 2026-11-27 ("Verify
and if useful make it").  Each candidate from the public-apis
catalog was verified live, with a real query and its licence read
on the provider's own pages, before anything was registered:

* **Open Targets Platform** — VERIFIED LIVE 2026-11-27
  (GraphQL: CFTR / ENSG00000001626 returned 1,889 associated
  diseases; cystic fibrosis MONDO_0009061, overall score 0.921).
  Data licence: CC0 1.0 — "Open Targets Platform is marked with
  CC0 1.0 ... allowing downstream users to consume the data
  without restriction" (Platform licence page).  No API key.
  The GraphQL documentation publishes no numeric rate limit;
  it directs systematic/bulk users to the dataset downloads
  and BigQuery instead of repeated API querying.
* **European Nucleotide Archive (ENA)** — VERIFIED LIVE
  2026-11-27 (Portal API taxon query tax_eq(9606) returned
  Homo sapiens, and the read-run record for SRR1039508 came
  back in full: study PRJNA229998, ILLUMINA paired-end,
  22,935,521 reads).  Terms:
  EMBL-EBI Terms of Use (revised 2026-10-14) — EMBL-EBI
  "imposes no additional restriction on the use of the
  contributed data than those provided by the data owner"
  and expects attribution.  No API key; no numeric rate
  limit is published on the Portal API documentation.
* **RCSB Protein Data Bank** — VERIFIED LIVE 2026-11-27
  (Data API returned entry 4HHB, human deoxyhemoglobin).
  Licence: CC0 1.0 — "data files contained in the PDB archive
  are available under the CC0 1.0 Universal Public Domain
  Dedication.  All data provided by RCSB PDB programmatic
  APIs are available under the same license" (RCSB PDB Usage
  Policies).  No API key; no numeric rate limit is published
  in the Data API documentation.

Deliberate exclusions, with reasons:

* **KEGG — HELD, not registered.**  The REST API answers live,
  but KEGG's own Copyright and Disclaimer page (updated
  2024-10-01) states: "Non-academic use of KEGG requires a
  commercial license."  Provome is a commercial venture, so
  KEGG is unusable until a licence exists.  It is recorded in
  ``docs/LICENSE_REGISTER.md`` as licence-required.
* **MyGene.info — verified live, NOT registered.**  The v3 API
  answers with no key (CFTR resolved to Entrez 1080 /
  ENSG00000001626).  But MyGene.info is an aggregator of other
  databases' annotations: registering it as an evidence source
  would let one fetch pose as an independent line when its
  content is derived from sources Provome already counts.
  The source registry describes provenance for evidence the
  gate counts and has no non-evidence "plumbing" category,
  so there is no honest registration for it today.  It is
  recorded in the licence register as UNVERIFIED (no explicit
  licence statement was found on the service's own
  documentation pages checked 2026-11-27).

What this module is, and is not:

* It is the retrieval/parsing layer for the three newly
  registered sources: fetch a record, check it as it is
  fetched, and hand back :class:`~provome.evidence.EvidenceItem`
  objects under the registered ``source_id``s, with the fetch
  date in every citation — the same rules as
  :mod:`provome.live`.
* Verification here is verification at fetch, as in the live
  provider: an item is VERIFIED only when the record actually
  came back in this fetch and the fields used were present.
  A source that errors, times out, or returns nothing
  contributes no items.  Per CLAUDE.md red line 2, there is
  no independent confirmer for non-literature items today;
  these items are trusted at the provider's word at the
  item/gate layer, exactly like ClinVar and gnomAD items,
  and this module does not describe that gap as closed.
* Directions are derived from the served record, never
  assumed: an Open Targets item SUPPORTS the gene-disease
  association it reports (that association is the item's
  claim); ENA and RCSB PDB items are NEUTRAL — an archive
  record and a structure record are provenance and context,
  and neither supports nor refutes a disease claim.
* These providers are NOT wired into the Copilot's default
  provider chain.  Registration plus a tested retrieval layer
  is what shipped; whether and how the Copilot consults them
  per question is a separate decision, to be made the way
  :mod:`provome.cross_source` was: explicitly, with its
  independence argument written down.
* Gate arithmetic is untouched: nothing here changes
  :func:`provome.evidence.evaluate_gate`, its thresholds, or
  its counting.  The only change to ``provome/evidence.py``
  is three new entries in the production source registry,
  made under the Founder's explicit order for this work
  (stated in the commit message, per the pre-commit guard's
  override rule).

No silent network in tests: the HTTP transport is injectable
and the test-suite runs entirely on recorded fixtures.

Research software.  Not for clinical use.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Optional, Sequence

from .evidence import (
    EvidenceItem,
    EvidenceKind,
    SupportDirection,
    VerificationStatus,
)

OPEN_TARGETS_API = "https://api.platform.opentargets.org/api/v4/graphql"
ENA_PORTAL_API = "https://www.ebi.ac.uk/ena/portal/api/search"
RCSB_DATA_API = "https://data.rcsb.org/rest/v1/core/entry"
USER_AGENT = "Provome/0.23 (research software; registered-source fetch)"

OPENTARGETS_SOURCE_ID = "opentargets"
ENA_SOURCE_ID = "ena"
RCSB_PDB_SOURCE_ID = "rcsb-pdb"

# A transport takes (url, optional POST body) and returns the
# parsed JSON payload (object or array).
Transport = Callable[[str, Optional[dict]], Any]


def urllib_transport(url: str, body: Optional[dict] = None) -> Any:
    """Default transport: stdlib urllib, JSON in and out."""
    data = None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


# ---------------------------------------------------------------------
# Open Targets Platform: gene-disease association evidence
# ---------------------------------------------------------------------

_ENSEMBL_GENE_RE = re.compile(r"ENS[A-Z]*G\d+")

_ASSOCIATION_QUERY = """
query ($ensemblId: String!, $size: Int!) {
  target(ensemblId: $ensemblId) {
    approvedSymbol
    associatedDiseases(page: {index: 0, size: $size}) {
      rows {
        disease { id name }
        score
        datasourceScores { id score }
      }
    }
  }
}
"""


def parse_opentargets_associations(
    payload: Any,
    *,
    ensembl_id: str,
    symbol: str,
    fetched_on: date,
) -> tuple[EvidenceItem, ...]:
    """Turn one GraphQL response into association items.

    Only rows carrying a disease id, a disease name and a
    numeric overall score become items; anything else in the
    payload is ignored rather than dressed up as evidence.
    """
    stamp = fetched_on.isoformat()
    try:
        target = payload["data"]["target"]
        rows = target["associatedDiseases"]["rows"]
    except (KeyError, TypeError):
        return ()
    served_symbol = target.get("approvedSymbol") or symbol
    items: list[EvidenceItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        disease = row.get("disease") or {}
        disease_id = disease.get("id")
        disease_name = disease.get("name")
        score = row.get("score")
        if not disease_id or not disease_name or not isinstance(
            score, (int, float)
        ):
            continue
        datasources = row.get("datasourceScores") or []
        named = [
            f"{entry['id']} {entry['score']:.3f}"
            for entry in datasources
            if isinstance(entry, dict)
            and entry.get("id")
            and isinstance(entry.get("score"), (int, float))
        ]
        notes = (
            "Datasource scores served with this association: "
            + ", ".join(named)
            + "."
            if named
            else ""
        )
        items.append(
            EvidenceItem(
                item_id=f"opentargets-{ensembl_id}-{disease_id}",
                topic_key=served_symbol.lower(),
                statement=(
                    f"Open Targets Platform reports an association "
                    f"between {served_symbol} and {disease_name} "
                    f"({disease_id}) with an overall association "
                    f"score of {score:.3f}."
                ),
                source_id=OPENTARGETS_SOURCE_ID,
                citation=(
                    f"Open Targets Platform target-disease "
                    f"association: {served_symbol} ({ensembl_id}) "
                    f"- {disease_name} ({disease_id}) "
                    f"(fetched {stamp})"
                ),
                kind=EvidenceKind.CURATED_CLASSIFICATION,
                direction=SupportDirection.SUPPORTS,
                status=VerificationStatus.VERIFIED,
                locator=(
                    f"https://platform.opentargets.org/"
                    f"disease/{disease_id}"
                ),
                notes=notes,
            )
        )
    return tuple(items)


@dataclass
class OpenTargetsProvider:
    """Fetches gene-disease associations from Open Targets."""

    transport: Transport = urllib_transport
    today: Callable[[], date] = field(default=date.today)

    name = "opentargets"
    source_id = OPENTARGETS_SOURCE_ID

    def fetch_gene_associations(
        self, ensembl_id: str, symbol: str, *, limit: int = 10
    ) -> tuple[EvidenceItem, ...]:
        """Association items for one gene; ``()`` on any failure."""
        if not _ENSEMBL_GENE_RE.fullmatch(ensembl_id or ""):
            raise ValueError(
                f"not an Ensembl gene id: {ensembl_id!r}"
            )
        body = {
            "query": _ASSOCIATION_QUERY,
            "variables": {"ensemblId": ensembl_id, "size": limit},
        }
        try:
            payload = self.transport(OPEN_TARGETS_API, body)
        except Exception:
            return ()
        return parse_opentargets_associations(
            payload,
            ensembl_id=ensembl_id,
            symbol=symbol,
            fetched_on=self.today(),
        )


# ---------------------------------------------------------------------
# European Nucleotide Archive: sequence-archive records
# ---------------------------------------------------------------------

_ACCESSION_RE = re.compile(r"[A-Za-z]{1,3}\d{5,}")


def parse_ena_runs(
    payload: Any, *, fetched_on: date
) -> tuple[EvidenceItem, ...]:
    """Turn one ENA Portal API response into archive items.

    The Portal API returns a JSON array of record objects.
    A record becomes an item only when it carries a run
    accession and a scientific name: the two fields the
    statement is built from.
    """
    stamp = fetched_on.isoformat()
    if not isinstance(payload, list):
        return ()
    items: list[EvidenceItem] = []
    for record in payload:
        if not isinstance(record, dict):
            continue
        run = record.get("run_accession")
        scientific_name = record.get("scientific_name")
        if not run or not scientific_name:
            continue
        study = record.get("study_accession") or "an unnamed study"
        tax_id = record.get("tax_id") or "unknown"
        platform = record.get("instrument_platform") or ""
        layout = record.get("library_layout") or ""
        read_count = record.get("read_count") or ""
        detail = " ".join(
            part for part in (platform, layout) if part
        ).strip()
        statement = (
            f"ENA holds sequencing run {run} (study {study}): "
            f"{scientific_name} (tax ID {tax_id})"
            + (f", {detail} data" if detail else "")
            + (f", {read_count} reads" if read_count else "")
            + "."
        )
        items.append(
            EvidenceItem(
                item_id=f"ena-{run}",
                topic_key=run.lower(),
                statement=statement,
                source_id=ENA_SOURCE_ID,
                citation=(
                    f"European Nucleotide Archive run {run} "
                    f"(fetched {stamp} via the ENA Portal API)"
                ),
                kind=EvidenceKind.OTHER,
                direction=SupportDirection.NEUTRAL,
                status=VerificationStatus.VERIFIED,
                locator=(
                    f"https://www.ebi.ac.uk/ena/browser/view/{run}"
                ),
            )
        )
    return tuple(items)


@dataclass
class EnaProvider:
    """Fetches read-run records from the ENA Portal API."""

    transport: Transport = urllib_transport
    today: Callable[[], date] = field(default=date.today)

    name = "ena"
    source_id = ENA_SOURCE_ID

    def fetch_run(self, accession: str) -> tuple[EvidenceItem, ...]:
        """Archive item(s) for one run accession; ``()`` on failure."""
        if not _ACCESSION_RE.fullmatch(accession or ""):
            raise ValueError(f"not a run accession: {accession!r}")
        query = urllib.parse.urlencode(
            {
                "result": "read_run",
                "query": f'run_accession="{accession}"',
                "fields": (
                    "run_accession,study_accession,scientific_name,"
                    "tax_id,instrument_platform,library_layout,"
                    "read_count"
                ),
                "format": "json",
            }
        )
        try:
            payload = self.transport(f"{ENA_PORTAL_API}?{query}", None)
        except Exception:
            return ()
        return parse_ena_runs(payload, fetched_on=self.today())


# ---------------------------------------------------------------------
# RCSB Protein Data Bank: structure records
# ---------------------------------------------------------------------

_PDB_ID_RE = re.compile(r"[0-9][A-Za-z0-9]{3}")


def parse_rcsb_entry(
    payload: Any, *, pdb_id: str, fetched_on: date
) -> tuple[EvidenceItem, ...]:
    """Turn one RCSB Data API entry response into a structure item.

    The entry becomes an item only when the payload names the
    entry (``rcsb_id``) and carries a structure title: the two
    fields the statement is built from.
    """
    stamp = fetched_on.isoformat()
    if not isinstance(payload, dict):
        return ()
    served_id = payload.get("rcsb_id")
    struct = payload.get("struct") or {}
    title = struct.get("title")
    if not served_id or not title:
        return ()
    methods = [
        entry.get("method")
        for entry in (payload.get("exptl") or [])
        if isinstance(entry, dict) and entry.get("method")
    ]
    method_text = methods[0] if methods else "an experimental method"
    resolutions = (payload.get("rcsb_entry_info") or {}).get(
        "resolution_combined"
    ) or []
    resolution_text = (
        f", resolution {resolutions[0]:.2f} Å"
        if resolutions and isinstance(resolutions[0], (int, float))
        else ""
    )
    entry_id = str(served_id).upper()
    item = EvidenceItem(
        item_id=f"rcsb-pdb-{entry_id}",
        topic_key=entry_id.lower(),
        statement=(
            f"RCSB PDB entry {entry_id} is an experimentally "
            f"determined structure: \"{title}\" "
            f"({method_text}{resolution_text})."
        ),
        source_id=RCSB_PDB_SOURCE_ID,
        citation=(
            f"RCSB Protein Data Bank entry {entry_id}: {title} "
            f"(fetched {stamp})"
        ),
        kind=EvidenceKind.OTHER,
        direction=SupportDirection.NEUTRAL,
        status=VerificationStatus.VERIFIED,
        locator=f"https://www.rcsb.org/structure/{entry_id}",
    )
    return (item,)


@dataclass
class RcsbPdbProvider:
    """Fetches structure entries from the RCSB Data API."""

    transport: Transport = urllib_transport
    today: Callable[[], date] = field(default=date.today)

    name = "rcsb-pdb"
    source_id = RCSB_PDB_SOURCE_ID

    def fetch_entry(self, pdb_id: str) -> tuple[EvidenceItem, ...]:
        """Structure item for one PDB id; ``()`` on any failure."""
        if not _PDB_ID_RE.fullmatch(pdb_id or ""):
            raise ValueError(f"not a PDB id: {pdb_id!r}")
        entry_id = pdb_id.upper()
        try:
            payload = self.transport(
                f"{RCSB_DATA_API}/{entry_id}", None
            )
        except Exception:
            return ()
        return parse_rcsb_entry(
            payload, pdb_id=entry_id, fetched_on=self.today()
        )


__all__ = [
    "ENA_PORTAL_API",
    "ENA_SOURCE_ID",
    "OPEN_TARGETS_API",
    "OPENTARGETS_SOURCE_ID",
    "RCSB_DATA_API",
    "RCSB_PDB_SOURCE_ID",
    "EnaProvider",
    "OpenTargetsProvider",
    "RcsbPdbProvider",
    "parse_ena_runs",
    "parse_opentargets_associations",
    "parse_rcsb_entry",
    "urllib_transport",
]
