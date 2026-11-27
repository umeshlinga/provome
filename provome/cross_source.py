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
2. **Record-report absorption.**  A corpus paper whose title
   names ClinVar *and* the variant (in any accepted notation)
   presents itself as a report *of* the ClinVar record.  When
   a ClinVar item for the same variant is in the merged set,
   such a paper is carried as UNVERIFIED with an explanatory
   note and counted on the provider
   (``absorbed_duplicate_records``): it stays visible for
   audit, but it cannot manufacture the second group.  The
   rule is deliberately narrow — it fires only when the title
   names both the database and the variant — and conservative
   in the safe direction: it can demote a genuinely
   independent paper whose title happens to name ClinVar and
   the variant (a comparison study, say), which costs a
   decline, never a false answer.

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

Research software.  Not for clinical use.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional, Sequence

from .confirmation import variant_short_from_topic
from .corpus_provider import CorpusProvider
from .domains import Division
from .evidence import EvidenceItem, SupportDirection, VerificationStatus
from .live import LiveEvidenceProvider, acceptable_notations, parse_variant_query

__all__ = [
    "STRUCTURED_SOURCE_IDS",
    "CrossSourceProvider",
]

#: The sources taken from the structured (live) side.  The live
#: provider's literature items are deliberately excluded; see
#: the module docstring.
STRUCTURED_SOURCE_IDS = frozenset({"clinvar", "gnomad"})


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

    Attributes:
        absorbed_duplicate_records: Cumulative count of corpus
            items the independence guard carried as UNVERIFIED
            because their titles present them as reports of
            the ClinVar record served alongside them (see the
            module docstring for exactly what the guard does
            and does not catch).
    """

    name = "cross-source"
    scoped_divisions = frozenset({Division.HUMAN})

    def __init__(
        self,
        corpus_provider: CorpusProvider,
        structured_provider: Optional[LiveEvidenceProvider] = None,
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
        self.absorbed_duplicate_records = 0
        self._questions: dict = {}
        self._cache: dict = {}

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
        corpus_items = self._absorb_record_reports(
            corpus_items, structured, topic_key
        )
        result = tuple(structured) + tuple(corpus_items)
        self._cache[topic_key] = result
        return result

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
        merged set, a verified supporting corpus item whose
        statement or citation names ClinVar and the variant (any
        accepted notation) is the structured record's own
        report, not an independent line; it is carried as
        UNVERIFIED with the reason in its notes, and counted on
        the provider.
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
            text = f"{item.statement} {item.citation}".lower()
            is_record_report = (
                item.is_verified
                and item.direction is SupportDirection.SUPPORTS
                and "clinvar" in text
                and any(form in text for form in accepted)
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
                        "this literature item's title names ClinVar "
                        "and the variant, so it presents itself as a "
                        "report of the same ClinVar record served "
                        "here as structured evidence; it is carried "
                        "as UNVERIFIED so one evidence line cannot "
                        "manufacture a second independence group. "
                        "It is not counted toward the gate."
                    ).strip(),
                )
            )
        return merged
