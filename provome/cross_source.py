"""Cross-source provider: the corpus, plus a genuine second group.

The corpus provider (:mod:`provome.corpus_provider`) retrieves
verified literature at scale, but every item it serves is Europe
PMC — one independence group — so on its own it can never satisfy
the cite-or-decline gate's two-group rule, and the Copilot
declines every corpus-only question, however much verified
evidence retrieval found.  That was the Item 53 structural
result, reproduced verbatim in the 0.21.0 smoke run.

This module is the cross-supply that closes the gap *on the
gate's own unchanged terms*.  A :class:`CrossSourceProvider`
merges, for one Human-division variant question:

* literature evidence from the local Europe PMC corpus, via
  :class:`~provome.corpus_provider.CorpusProvider`
  (independence group ``europepmc``), and
* structured variant evidence from the live provider machinery
  (:class:`provome.live.LiveEvidenceProvider`): ClinVar's
  curated classification (group ``ncbi-clinvar``) and gnomAD's
  population frequency (group ``gnomad``).

The gate is not touched.  Nothing here changes
:func:`provome.evidence.evaluate_gate`, its thresholds, or its
counting: the merged items go through the same deduplication
and group counting as any other provider's items, and the
composite passes exactly when the gate's own arithmetic says
the evidence is sufficient — and declines, with the gate's own
reasons, when it is not.

Why these sources are independent groups (the argument a
critic is invited to test):

* **Different operators.**  ClinVar is NCBI's archive;
  gnomAD is the Broad Institute's aggregate; Europe PMC is
  EMBL-EBI's literature index.  No shared curation pipeline
  feeds two of them.
* **Different underlying data.**  A ClinVar item is a
  submitted clinical interpretation of the variant; a gnomAD
  item is a counted allele frequency across population
  sequencing; a corpus item is the text of a published report.
  A fabrication or error in one does not appear in another by
  construction — each must be wrong separately.
* **Independence is counted only by the registry.**  Groups
  come from :func:`provome.evidence.default_source_registry`,
  never from labels this module invents; one source is never
  relabelled as two.

The independence guard (what stops the same evidence line
from arriving twice and posing as two groups):

1. **Canonical-record collapse.**  The gate's own
   deduplication merges items that cite the same canonical
   record (same PMID, same ClinVar Variation ID) under one
   topic and direction, whichever provider served them.  The
   composite relies on that machinery and does not bypass it.
2. **Record-report absorption.**  A corpus paper that
   presents itself as a report *of* the ClinVar record is
   the record's own echo, not an independent line.  When a
   ClinVar item for the same variant is in the merged set,
   such a paper is carried as UNVERIFIED with an explanatory
   note and counted on the provider
   (``absorbed_duplicate_records``): it stays visible for
   audit, but it cannot manufacture the second group.  An
   item presents itself that way when its title names
   ClinVar *and* the variant (in any accepted notation), or
   when its abstract names both *and* describes the paper
   as derived from the record — a restatement, a summary, a
   report of the record.  The abstract rule (added in
   v0.25.0, after the 2026-11-28 red-team round showed a
   paraphrased title laundering a restated abstract) is
   what stops the same restatement from evading the guard
   by rewording its title.  Both rules are conservative in
   the safe direction: they can demote a genuinely
   independent paper whose text happens to match (a
   comparison study, say), which costs a decline, never a
   false answer.

Two further checks the red-team round of 2026-11-28 forced
(v0.25.0), stated with the same plainness:

3. **Variant relevance.**  Corpus retrieval keys on the
   whole question — gene, variant, disease — and its items
   are retagged onto the parsed variant's topic.  Before
   v0.25.0 nothing checked that a retagged paper was about
   *that* variant: papers about CFTR Phe508del, retagged,
   manufactured full answers for invented variants of the
   same gene.  Now a verified corpus item counts for a
   variant topic only when its own text names both the
   topic's gene and the topic's variant (any accepted
   notation for a protein topic; the ``c.`` notation itself
   for a coding topic); anything else is carried as
   UNVERIFIED and counted on the provider
   (``off_topic_records``).  Gene-level Open Targets items
   are the stated exception, and a bounded one: their
   statements are gene–disease associations and say so,
   they form a single group, and the gate's group rule
   means they can never satisfy a variant question alone —
   after this fix, every pass this chain can issue includes
   variant-level evidence (a confirmed ClinVar item or a
   relevance-checked corpus item), because no other stream
   can supply the remaining groups.
4. **Chain re-confirmation.**  The confirmers in
   :mod:`provome.confirmation` existed but were run only by
   the orchestra's verification agent; this chain never
   called them, so corpus items counted on the corpus
   standard alone and ClinVar items on the feed's own word.
   The merged set is now re-confirmed before it is served:
   corpus items against PubMed (PMID exists, title matches,
   not retracted), ClinVar items against the served ClinVar
   record with direction derived from it.  An item that
   fails — an invented PMID, an altered title riding a real
   PMID, a fabricated Variation ID the served record does
   not support — is carried with its new status and the
   confirmer's note, and never counts
   (``confirmation_demotions``).

What the guard does **not** catch — the residual risk, stated
plainly: a ClinVar submission often rests on a published
paper, and that paper is in the corpus.  If the paper's title
names neither ClinVar nor the record, and the served ClinVar
esummary record does not expose its publication links (it
does not), the composite cannot tell that the literature item
and the classification share an underlying study.  Provenance
linkage at that depth — resolving ClinVar's publication
cross-references per record — is future work.  Until then the
two-group pass this provider enables means "a curated
classification and at least three verified literature items,
with no detected record-level duplication", not "proof that
no underlying study is shared".

Deliberate exclusions, also stated plainly:

* **The live provider's literature items are not merged.**
  They are literature reached through a second index
  (``pubmed-literature``), not structured evidence; the same
  paper can arrive through both indexes, and
  literature-from-two-indexes is not the independence this
  module exists to add.  Only the ``clinvar`` and ``gnomad``
  items are taken from the structured side.
* **gnomAD never supplies the passing group by itself.**
  Its items are NEUTRAL (a frequency neither supports nor
  refutes pathogenicity), and the gate counts groups among
  *supporting* items only.  In practice the second supporting
  group is ClinVar's classification; a variant ClinVar calls
  uncertain or conflicting yields a NEUTRAL ClinVar item too,
  and the composite declines — correctly.
* **Human division only.**  ClinVar and gnomAD are
  human-variant resources.  The composite is scoped to the
  Human division, the Copilot's division guard declines
  Animal and Plant questions before retrieval, and no Animal
  or Plant second group is claimed or faked here; those are
  future work.

Questions that do not name a gene and an HGVS variant are not
variant questions: the composite then serves corpus evidence
alone, under the corpus topic key, and the gate declines on
the group rule exactly as it does for the corpus provider by
itself.

The registered sources wired in v0.24.0
---------------------------------------

v0.23.0 registered three further sources (Open Targets, ENA,
RCSB PDB) with a tested retrieval layer in
:mod:`provome.source_providers`, deliberately unwired.  This
release wires them into this composite — the Copilot's default
multi-source chain — each only where it genuinely serves the
question the chain can actually pose:

* **Open Targets** (group ``opentargets``) joins the merge
  for *variant* topics.  A variant topic names its gene, so
  the composite resolves that gene to its Ensembl gene ID —
  addressing only, via the Ensembl REST symbol lookup the
  Animal and Plant providers already use; the lookup result
  is never itself evidence — and fetches the Platform's
  gene–disease associations for it.  The items are retagged
  onto the variant topic exactly as corpus items are.  What
  they support is what their statements say: the *gene's*
  association with the disease, which is part of what a
  variant question asks.  They make no claim about the
  variant itself, and no statement is rewritten to pretend
  otherwise.
* **ENA** (group ``ena``) is consulted when a question names
  a sequencing run accession (``SRR``/``ERR``/``DRR`` …):
  the topic becomes ``ena:<ACCESSION>`` and the composite
  serves the archive record for that run, retagged onto the
  topic.  ENA items are NEUTRAL provenance, so an
  accession question still declines — the chain now *shows*
  the verified record it found while declining, instead of
  declining blind.  An archive record can never manufacture
  a pass, alone or as a "second group": the gate counts
  groups among supporting items only, and these never
  support.
* **RCSB PDB** (group ``rcsb-pdb``) is consulted when a
  question names a PDB entry in a structure context (a
  PDB-shaped token together with the word "PDB" or
  "structure" — the context requirement is what keeps a
  stray four-character token from hijacking a question):
  the topic becomes ``rcsb:<ID>`` and the structure record
  is served the same way, NEUTRAL, with the same
  decline-with-the-record-shown behaviour.

Independence, and its stated limit.  Each wired source is
counted under exactly the independence group the registry
assigns it — ``opentargets``, ``ena``, ``rcsb-pdb`` — never
relabeled, never split, never merged with another.  Open
Targets needs the plain caveat its registry entry already
carries: the Platform *integrates* upstream records,
including ClinVar-derived (EVA) and Europe PMC literature
datasources, so its scored association is not built from
data wholly separate from the chain's other sources.  The
registry nevertheless registers it as one group — one
integration pipeline, one scored association, counted once —
and the gate counts registry groups; this wiring follows
the registry rather than inventing a second, provider-local
counting rule.  Stated as a limit, then: a pass in which
Open Targets supplies the second group means "a scored
platform association stands alongside the other verified
evidence", not "two wholly disjoint data lineages agree".
One provenance case *is* closed, since v0.25.0: an
association whose datasources are *solely* Europe PMC is
not an integration of anything — it is the literature
index restated — and the parse carries it UNVERIFIED, so
it can never be the "second group" beside corpus
literature (see
:func:`provome.source_providers.parse_opentargets_associations`).
The absorption guard above is *not* extended to Open
Targets items: it targets items that present themselves as
reports of the ClinVar record, and an Open Targets
association does not.

Division scoping, honestly.  Open Targets is a human
gene–disease resource and is wired as Human evidence only,
which is all this composite can issue: the composite is
Human-scoped and the Copilot's division guard declines
Animal and Plant questions before retrieval.  ENA and RCSB
PDB records are species-agnostic — the archive and the
structure bank hold records for animal and plant species
too — but in this release they are wired into this
Human-scoped chain only.  The Animal and Plant doors run
their own division providers, whose composition this
release does not change, so no Animal or Plant coverage is
claimed for any of the three sources.

What is still not wired, plainly: gene-only questions (a
gene named with no variant) do not consult Open Targets.
The only gene recognition this chain has is
:func:`provome.live.parse_variant_query`, which requires an
HGVS variant; inventing a bare-gene recogniser to widen the
wiring would risk misreading questions, and the corpus path
already serves those questions as it did before.

Research software.  Not for clinical use.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Callable, Optional, Sequence

from .confirmation import (
    clinvar_variation_id,
    confirm_clinvar,
    confirm_literature_detail,
    variant_short_from_topic,
)
from .corpus_provider import EUROPEPMC_SOURCE_ID, CorpusProvider
from .domains import Division
from .evidence import EvidenceItem, SupportDirection, VerificationStatus
from .live import (
    LiveEvidenceProvider,
    acceptable_notations,
    parse_variant_query,
    urllib_transport,
)
from .source_providers import (
    EnaProvider,
    OpenTargetsProvider,
    RcsbPdbProvider,
)

__all__ = [
    "STRUCTURED_SOURCE_IDS",
    "REGISTERED_SOURCE_IDS",
    "CrossSourceProvider",
    "resolve_ensembl_gene_id",
]

#: The sources taken from the structured (live) side.  The live
#: provider's literature items are deliberately excluded; see
#: the module docstring.
STRUCTURED_SOURCE_IDS = frozenset({"clinvar", "gnomad"})

#: The registered sources wired in v0.24.0 (see the module
#: docstring).  Each counts under exactly its own registry
#: independence group.
REGISTERED_SOURCE_IDS = frozenset({"opentargets", "ena", "rcsb-pdb"})

#: Ensembl REST symbol lookup — the same endpoint family the
#: Animal and Plant providers use to resolve a gene symbol to
#: a gene record.  Here it is addressing only: it locates the
#: Ensembl gene ID an Open Targets query needs.  Its result
#: is never emitted as evidence.
ENSEMBL_SYMBOL_LOOKUP = "https://rest.ensembl.org/lookup/symbol"

_ENSEMBL_GENE_ID_RE = re.compile(r"ENS[A-Z]*G\d+")
_RUN_ACCESSION_RE = re.compile(r"\b[SED]RR\d{6,}\b")
_PDB_ID_RE = re.compile(r"\b[0-9][A-Za-z0-9]{3}\b")
_CORPUS_ITEM_ID_RE = re.compile(r"^corpus-pmid-(\d+)$")

#: Words that mark a text as *derived from* a database record
#: rather than an independent report: the independence guard's
#: abstract rule fires only when a corpus item names ClinVar
#: and the variant *and* describes itself with one of these
#: (a restatement, a summary, a report of the record, …).
_DERIVATION_MARKERS = (
    "restat",
    "summar",
    "report",
    "record",
    "classification",
    "submission",
    "entry",
    "derived",
)


def resolve_ensembl_gene_id(
    symbol: str, transport: Callable = urllib_transport
) -> Optional[str]:
    """Resolve a human gene symbol to its Ensembl gene ID.

    Returns ``None`` on any failure or on a response that does
    not carry a well-formed Ensembl gene ID: an unresolved
    gene simply means Open Targets is not consulted, never an
    error and never a guessed ID.
    """
    if not symbol or not symbol.strip():
        return None
    try:
        record = transport(
            f"{ENSEMBL_SYMBOL_LOOKUP}/homo_sapiens/{symbol.strip()}"
            "?content-type=application/json",
            None,
        )
    except Exception:
        return None
    if not isinstance(record, dict):
        return None
    gene_id = record.get("id")
    if isinstance(gene_id, str) and _ENSEMBL_GENE_ID_RE.fullmatch(gene_id):
        return gene_id
    return None


class CrossSourceProvider:
    """Corpus literature + structured variant evidence, merged.

    Args:
        corpus_provider: A :class:`CorpusProvider` scoped to
            the Human division.  Any other scoping is refused:
            merging a non-Human corpus with Human structured
            resources would be incoherent.
        structured_provider: The structured side.  Defaults to
            a :class:`LiveEvidenceProvider` with its default
            (live) transport; tests inject one with a fixture
            transport.  Only its ``clinvar`` and ``gnomad``
            items are merged.
        opentargets_provider: The Open Targets side (v0.24.0).
            Defaults to a live :class:`OpenTargetsProvider`;
            tests inject one with a fixture transport.  Its
            association items join the merge for variant
            topics, under its own registry group.
        ena_provider: The ENA side (v0.24.0).  Defaults live;
            consulted only for ``ena:`` topics (a question
            naming a run accession).
        rcsb_provider: The RCSB PDB side (v0.24.0).  Defaults
            live; consulted only for ``rcsb:`` topics (a
            question naming a PDB entry in a structure
            context).
        gene_resolver: Gene symbol → Ensembl gene ID, used to
            address Open Targets queries.  Defaults to
            :func:`resolve_ensembl_gene_id` over the live
            transport; tests inject a fixture resolver.  A
            resolver that returns ``None`` simply leaves Open
            Targets unconsulted for that gene.

    Attributes:
        absorbed_duplicate_records: Cumulative count of corpus
            items the independence guard carried as UNVERIFIED
            because they present themselves as reports of the
            ClinVar record served alongside them (see the
            module docstring for exactly what the guard does
            and does not catch).
        off_topic_records: Cumulative count of corpus items
            carried as UNVERIFIED because, although retrieval
            matched them to the question, their own text is
            not about the variant the topic names (evidence
            about one variant of a gene is not evidence about
            another variant of that gene).
        confirmation_demotions: Cumulative count of merged
            items that lost VERIFIED status when the chain
            re-confirmed them against their claimed source
            (PubMed for corpus literature, ClinVar for ClinVar
            items) — the red-team round of 2026-11-28 showed
            the chain previously never ran the confirmers the
            codebase already had.
    """

    name = "cross-source"
    scoped_divisions = frozenset({Division.HUMAN})

    def __init__(
        self,
        corpus_provider: CorpusProvider,
        structured_provider: Optional[LiveEvidenceProvider] = None,
        opentargets_provider: Optional[OpenTargetsProvider] = None,
        ena_provider: Optional[EnaProvider] = None,
        rcsb_provider: Optional[RcsbPdbProvider] = None,
        gene_resolver: Optional[Callable[[str], Optional[str]]] = None,
    ) -> None:
        if corpus_provider.division is not Division.HUMAN:
            raise ValueError(
                "the cross-source provider merges a Human corpus "
                "with Human structured resources (ClinVar, "
                "gnomAD); a corpus provider scoped to "
                f"{corpus_provider.division.value!r} cannot be "
                "part of it"
            )
        self.corpus_provider = corpus_provider
        self.structured_provider = (
            structured_provider
            if structured_provider is not None
            else LiveEvidenceProvider()
        )
        self.opentargets_provider = (
            opentargets_provider
            if opentargets_provider is not None
            else OpenTargetsProvider()
        )
        self.ena_provider = (
            ena_provider if ena_provider is not None else EnaProvider()
        )
        self.rcsb_provider = (
            rcsb_provider if rcsb_provider is not None else RcsbPdbProvider()
        )
        self.gene_resolver = (
            gene_resolver
            if gene_resolver is not None
            else resolve_ensembl_gene_id
        )
        self.absorbed_duplicate_records = 0
        self.off_topic_records = 0
        self.confirmation_demotions = 0
        self._questions: dict = {}
        self._cache: dict = {}
        self._gene_ids: dict = {}

    # -- provider protocol ------------------------------------------

    def topic_for_question(self, question_text: str) -> Optional[str]:
        # Prime the corpus side either way: retrieval is cached
        # by the corpus provider under its own topic key, so the
        # merge in items_for_topic reuses it.
        corpus_topic = self.corpus_provider.topic_for_question(
            question_text
        )
        query = parse_variant_query(question_text)
        if query is not None:
            self._questions[query.topic_key] = question_text
            return query.topic_key
        accession = _RUN_ACCESSION_RE.search(question_text)
        if accession is not None:
            return f"ena:{accession.group(0).upper()}"
        lowered = question_text.lower()
        if "pdb" in lowered or "structure" in lowered:
            pdb_match = _PDB_ID_RE.search(question_text)
            if pdb_match is not None:
                return f"rcsb:{pdb_match.group(0).upper()}"
        return corpus_topic

    def items_for_topic(self, topic_key: str) -> Sequence[EvidenceItem]:
        if topic_key in self._cache:
            return self._cache[topic_key]
        if topic_key.startswith("corpus:"):
            # Not a variant question: corpus evidence alone,
            # exactly as the corpus provider serves it.
            result = tuple(self.corpus_provider.items_for_topic(topic_key))
            self._cache[topic_key] = result
            return result
        if topic_key.startswith("ena:"):
            # A run-accession question: the ENA archive record
            # for that run, retagged onto the topic.  NEUTRAL
            # provenance — the gate declines, with the record
            # shown (see the module docstring).
            result = self._registered_items(
                self.ena_provider.fetch_run, topic_key[len("ena:"):], topic_key
            )
            self._cache[topic_key] = result
            return result
        if topic_key.startswith("rcsb:"):
            # A structure question: the RCSB PDB entry record,
            # retagged onto the topic.  NEUTRAL context, same
            # decline-with-the-record-shown behaviour.
            result = self._registered_items(
                self.rcsb_provider.fetch_entry,
                topic_key[len("rcsb:"):],
                topic_key,
            )
            self._cache[topic_key] = result
            return result
        structured = [
            item
            for item in self.structured_provider.items_for_topic(topic_key)
            if item.source_id in STRUCTURED_SOURCE_IDS
        ]
        question_text = self._questions.get(topic_key)
        if question_text is None:
            # items_for_topic called without topic_for_question
            # (the live provider's own call pattern): synthesise
            # a retrieval query from the topic key itself —
            # "CFTR:p.Phe508del" tokenises to the gene and the
            # variant, which is what corpus retrieval keys on.
            question_text = topic_key.replace(":", " ")
        corpus_key = CorpusProvider._topic_key(question_text)
        corpus_items = [
            replace(item, topic_key=topic_key)
            for item in self.corpus_provider.items_for_topic(corpus_key)
        ]
        # Retagging is not relevance: retrieval matched these
        # papers to the *question*, which names the gene and
        # the disease as well as the variant, so a paper about
        # a different variant of the same gene can arrive here.
        # It is demoted before anything counts it (see
        # _demote_off_topic_records).
        corpus_items = self._demote_off_topic_records(
            corpus_items, topic_key
        )
        corpus_items = self._absorb_record_reports(
            corpus_items, structured, topic_key
        )
        opentargets_items = self._opentargets_items(topic_key)
        merged = (
            tuple(structured)
            + tuple(corpus_items)
            + tuple(opentargets_items)
        )
        # Nothing counts in this chain on a provider's word
        # alone where an independent confirmer exists: corpus
        # literature is re-confirmed against PubMed and ClinVar
        # items against the served ClinVar record, through the
        # structured side's transport (see
        # _confirm_merged_items).
        result = self._confirm_merged_items(merged, topic_key)
        self._cache[topic_key] = result
        return result

    # -- the registered sources (v0.24.0) -----------------------------

    @staticmethod
    def _registered_items(fetch, identifier: str, topic_key: str) -> tuple:
        """One registered provider's items, retagged onto the topic.

        Per-source failure isolation, the live provider's
        discipline: a provider that raises (including the
        ``ValueError`` for a malformed identifier) contributes
        no items, never an error.
        """
        try:
            items = fetch(identifier)
        except Exception:
            return ()
        return tuple(
            replace(item, topic_key=topic_key) for item in items
        )

    def _opentargets_items(self, topic_key: str) -> list:
        """Open Targets associations for the variant topic's gene.

        The gene comes from the topic key itself
        (``GENE:hgvs``); its Ensembl gene ID comes from the
        injected resolver, cached per gene.  An unresolved
        gene, a failed fetch, or a malformed ID all contribute
        no items.  The items are retagged onto the variant
        topic exactly as corpus items are; their statements
        remain the gene-level associations they are (see the
        module docstring for the independence caveat).
        """
        gene = topic_key.partition(":")[0]
        if not gene:
            return []
        if gene not in self._gene_ids:
            try:
                self._gene_ids[gene] = self.gene_resolver(gene)
            except Exception:
                self._gene_ids[gene] = None
        ensembl_id = self._gene_ids[gene]
        if not ensembl_id:
            return []
        try:
            items = self.opentargets_provider.fetch_gene_associations(
                ensembl_id, gene
            )
        except Exception:
            return []
        return [
            replace(item, topic_key=topic_key)
            for item in items
            if item.source_id in REGISTERED_SOURCE_IDS
        ]

    # -- corpus item text ----------------------------------------------

    @staticmethod
    def _corpus_pmid(item: EvidenceItem) -> Optional[str]:
        """The PMID a corpus item was built from, if recoverable."""
        match = _CORPUS_ITEM_ID_RE.match(item.item_id or "")
        return match.group(1) if match else None

    def _abstract_of(self, item: EvidenceItem) -> str:
        """The corpus record's abstract behind an item ("" if none).

        The guard and the relevance check must read the
        record's own text: a restatement of the ClinVar record
        often admits itself only in the abstract, and a paper's
        title alone does not always say which variant it is
        about.  The corpus provider caches per PMID.
        """
        pmid = self._corpus_pmid(item)
        if pmid is None:
            return ""
        return self.corpus_provider.abstract_for_pmid(pmid)

    # -- variant relevance ----------------------------------------------

    def _demote_off_topic_records(
        self,
        corpus_items: Sequence[EvidenceItem],
        topic_key: str,
    ) -> list:
        """Demote corpus items that are not about the topic's variant.

        Retrieval keys on the whole question — gene, variant,
        disease — so the papers it returns for a variant topic
        can be about a *different* variant of the same gene
        (the red-team round of 2026-11-28 manufactured passes
        for invented variants out of exactly that retagging).
        Evidence about variant A is not evidence about variant
        B: a verified corpus item counts for this topic only
        when its own text (title, citation, abstract) names
        both the topic's gene and the topic's variant — for a
        protein topic, in any accepted notation (the live
        provider's numbering-reconciliation rules); for a
        coding (``c.``) topic, the ``c.`` notation itself.
        Anything else is carried as UNVERIFIED with the reason
        in its notes, and counted on the provider.

        Stated limit: a paper that discusses the variant only
        under a notation outside the accepted set (for a
        ``c.`` topic, only under its protein name) is demoted
        too.  That costs recall and can cost an answer; it
        never manufactures one.
        """
        gene, _sep, hgvs = topic_key.partition(":")
        short = variant_short_from_topic(topic_key)
        if short is not None:
            variant_forms = acceptable_notations(short)
        else:
            lowered = hgvs.strip().lower()
            variant_forms = {lowered}
            if lowered.startswith("c."):
                variant_forms.add(lowered[2:])
        gene_pattern = (
            re.compile(rf"\b{re.escape(gene.strip().lower())}\b")
            if gene.strip()
            else None
        )
        merged: list = []
        for item in corpus_items:
            if not item.is_verified:
                merged.append(item)
                continue
            text = (
                f"{item.statement} {item.citation} "
                f"{self._abstract_of(item)}"
            ).lower()
            about_variant = any(form in text for form in variant_forms)
            about_gene = (
                gene_pattern is not None
                and gene_pattern.search(text) is not None
            )
            if about_variant and about_gene:
                merged.append(item)
                continue
            self.off_topic_records += 1
            merged.append(
                replace(
                    item,
                    status=VerificationStatus.UNVERIFIED,
                    notes=(
                        item.notes
                        + " Variant relevance (provome.cross_source): "
                        "this literature item's own text does not "
                        f"name both the gene ({gene}) and the "
                        f"variant ({hgvs}) this topic asks about, "
                        "so it is not evidence about this variant, "
                        "however strongly retrieval matched it to "
                        "the question; it is carried as UNVERIFIED "
                        "and is not counted toward the gate."
                    ).strip(),
                )
            )
        return merged

    # -- the independence guard ---------------------------------------

    def _absorb_record_reports(
        self,
        corpus_items: Sequence[EvidenceItem],
        structured_items: Sequence[EvidenceItem],
        topic_key: str,
    ) -> list:
        """Demote corpus items that merely report the ClinVar record.

        See the module docstring for the full specification and
        its limits.  In short: when a ClinVar item is in the
        merged set, a verified supporting corpus item that
        presents itself as a report *of* that record is the
        structured record's own report, not an independent
        line; it is carried as UNVERIFIED with the reason in
        its notes, and counted on the provider.  An item
        presents itself that way when its title names ClinVar
        and the variant (any accepted notation), or — the
        paraphrase case the 2026-11-28 red-team round added —
        when its abstract names ClinVar and the variant *and*
        describes itself as derived from the record (a
        restatement, a summary, a report of the record, …):
        a paraphrased title cannot launder a restated abstract.
        """
        if not any(
            item.source_id == "clinvar" for item in structured_items
        ):
            return list(corpus_items)
        short = variant_short_from_topic(topic_key)
        if short is None:
            return list(corpus_items)
        accepted = acceptable_notations(short)
        merged: list = []
        for item in corpus_items:
            if not (
                item.is_verified
                and item.direction is SupportDirection.SUPPORTS
            ):
                merged.append(item)
                continue
            title_text = f"{item.statement} {item.citation}".lower()
            abstract_text = self._abstract_of(item).lower()
            is_record_report = (
                "clinvar" in title_text
                and any(form in title_text for form in accepted)
            ) or (
                "clinvar" in abstract_text
                and any(form in abstract_text for form in accepted)
                and any(
                    marker in abstract_text
                    for marker in _DERIVATION_MARKERS
                )
            )
            if not is_record_report:
                merged.append(item)
                continue
            self.absorbed_duplicate_records += 1
            merged.append(
                replace(
                    item,
                    status=VerificationStatus.UNVERIFIED,
                    notes=(
                        item.notes
                        + " Independence guard (provome.cross_source): "
                        "this literature item presents itself as a "
                        "report of the same ClinVar record served "
                        "here as structured evidence — its title "
                        "names ClinVar and the variant, or its "
                        "abstract describes it as a restatement, "
                        "summary, or report of the ClinVar record; "
                        "it is carried as UNVERIFIED so one "
                        "evidence line cannot manufacture a second "
                        "independence group. It is not counted "
                        "toward the gate."
                    ).strip(),
                )
            )
        return merged

    # -- chain re-confirmation ------------------------------------------

    def _confirm_merged_items(
        self,
        items: Sequence[EvidenceItem],
        topic_key: str,
    ) -> tuple:
        """Re-confirm every merged item that has a confirmer.

        The codebase's confirmers (:mod:`provome.confirmation`)
        were, before v0.25.0, run only by the orchestra's
        verification agent — the Copilot chain over this
        composite never called them, so a corpus item counted
        on the corpus standard alone and a ClinVar item on the
        feed's own word (red-team round, 2026-11-28, classes 2
        and 6).  The chain now holds its merged items to the
        same standard the live literature path already meets:

        * corpus (Europe PMC) items are re-confirmed against
          PubMed via :func:`confirm_literature_detail` — the
          PMID must exist there, its title must plausibly
          match, and a retracted record fails;
        * ClinVar items are re-confirmed against the served
          ClinVar record via :func:`confirm_clinvar`, with the
          direction *derived* from the served classification,
          exactly as the verification agent does it.

        Both run over the structured provider's transport —
        the same injectable channel every fetch in this chain
        uses, so fixtures stay deterministic and offline.  An
        item that fails confirmation, or whose check cannot
        be completed, keeps its place in the merged set with
        its new status and the confirmer's note; it simply
        never counts.  Sources with no confirmer are passed
        through unchanged, and that gap is not hidden:
        gnomAD items are NEUTRAL and never count toward a
        pass; Open Targets items are hygiene-checked at parse
        time instead (see
        :func:`provome.source_providers.parse_opentargets_associations`).
        """
        transport = self.structured_provider.transport
        return tuple(
            self._confirm_item(item, transport, topic_key)
            for item in items
        )

    def _confirm_item(
        self, item: EvidenceItem, transport, topic_key: str
    ) -> EvidenceItem:
        if item.source_id == EUROPEPMC_SOURCE_ID:
            return self._confirm_corpus_item(item, transport)
        if item.source_id == "clinvar":
            return self._confirm_clinvar_item(item, transport)
        return item

    def _confirm_corpus_item(
        self, item: EvidenceItem, transport
    ) -> EvidenceItem:
        """Re-confirm one corpus item against PubMed."""
        if item.is_failed or not item.is_verified:
            # FAILED is sticky; anything not VERIFIED has
            # already been demoted by an earlier stage and is
            # not re-checked.
            return item
        pmid = self._corpus_pmid(item)
        if pmid is None:
            self.confirmation_demotions += 1
            return replace(
                item,
                status=VerificationStatus.UNVERIFIED,
                notes=(
                    item.notes
                    + " Chain re-confirmation "
                    "(provome.cross_source): no PMID could be "
                    "recovered from this corpus item, so it "
                    "cannot be re-confirmed against PubMed; it "
                    "is carried as UNVERIFIED and is not counted "
                    "toward the gate."
                ).strip(),
            )
        status, note, _retracted = confirm_literature_detail(
            transport, pmid, item.statement
        )
        if status is item.status:
            return item
        self.confirmation_demotions += 1
        return replace(
            item,
            status=status,
            notes=(
                item.notes
                + " Chain re-confirmation "
                f"(provome.cross_source): {note}"
            ).strip(),
        )

    def _confirm_clinvar_item(
        self, item: EvidenceItem, transport
    ) -> EvidenceItem:
        """Re-confirm one ClinVar item against the served record.

        Mirrors the verification agent's discipline: FAILED is
        sticky; an item with no recoverable Variation ID, or
        on a topic naming no concrete protein change, is
        outside the confirmer's remit and passes through; the
        direction is the served record's, never the item's
        own claim.
        """
        if item.is_failed:
            return item
        variation_id = clinvar_variation_id(
            item.citation, item.locator or ""
        )
        if variation_id is None or (
            variant_short_from_topic(item.topic_key) is None
        ):
            return item
        status, direction, note = confirm_clinvar(
            transport, variation_id, item.topic_key
        )
        new_direction = (
            direction if direction is not None else item.direction
        )
        if status is item.status and new_direction is item.direction:
            return item
        if item.is_verified and status is not VerificationStatus.VERIFIED:
            self.confirmation_demotions += 1
        return replace(
            item,
            status=status,
            direction=new_direction,
            notes=(
                item.notes
                + " Chain re-confirmation "
                f"(provome.cross_source): {note}"
            ).strip(),
        )
