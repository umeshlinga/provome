"""Public benchmark v1: the gate, measured on public data, in the open.

Roadmap Item 57 (Item 38 in buildable form, Item 51 standards
adopted).  The internal gate benchmark (:mod:`provome.benchmark`)
proves the cite-or-decline gate behaves on synthetic fixtures;
the Animal and Plant benchmarks prove their surfaces on recorded
live payloads.  This harness is the decision-grade step up: one
frozen probe set, drawn from PUBLIC records only, run across all
three divisions, and reported the way the field's serious
benchmarks report — with the discipline that makes a number hard
to game:

* **Preregistration.**  The probe set is frozen at
  ``public-benchmark-v2`` and hashed (canonical JSON, SHA-256)
  before any run.  The hash is recorded in every report.  A run
  handed a preregistered hash that does not match the probe set
  it is about to score refuses to run — a probe set edited after
  the fact is a new version, never a quiet edit.
* **Denominators and intervals.**  Every score is an ``x/n``
  fraction (:class:`Rate`) with a Wilson score 95% confidence
  interval.  There are no bare percentages anywhere in the
  report structure.
* **Trivial-baseline floor.**  An always-decline baseline and an
  always-answer baseline are scored on the same probes, in the
  same structure, and live in the same ``systems`` tuple as the
  gate — the gate's score cannot be quoted from this report
  without the baselines sitting next to it.
* **Confirmer calibration.**  A second system, the confirmer,
  re-derives every verdict from the providers' items with its
  own counting code — it never calls the gate and never touches
  a Copilot — and is scored in the same ``systems`` tuple.  The
  gate/confirmer agreement rate is reported as its own
  :class:`Rate`.  The confirmer is an internal calibration
  stand-in for the human baseline Item 51 demands; it is NOT a
  human baseline, and every report says so
  (:data:`CONFIRMER_CAVEAT`).
* **Human baseline (optional).**  When real human labels are
  supplied (:class:`HumanLabels`, gathered blind with
  :mod:`provome.labelling`), the report gains a human-baseline
  section: human-vs-gate and human-vs-expected agreement as
  :class:`Rate` fractions, per division and overall, computed
  on the labelled subset only — a partial label set is never
  silently treated as complete.  The section names the
  labeller and the labelled count, and the confirmer caveat
  switches to a text that keeps the two baselines distinct
  (:data:`CONFIRMER_CAVEAT_WITH_HUMAN_LABELS`).  With no
  labels supplied the section is absent and the default
  confirmer caveat stands unchanged.
* **Per-division separation.**  Probes are tagged by the division
  whose surface they are put to; scores are computed per division
  and overall, and no division's result is ever merged into
  another's.  A division with zero probes in a run reports
  ``n=0`` explicitly — no score is fabricated for it.
* **Failure register.**  Every probe the gate gets wrong is
  emitted as a :class:`FailureRecord` with the probe id, the
  expected and actual verdicts, and a cause class from a fixed
  taxonomy.  An empty register is an explicit empty tuple, not
  an omission.

Probe data: public records only — published ClinVar variants
already used in this repo (CFTR p.Phe508del and HBB p.Glu6Val
from the live demos, FGFR3 p.Gly380Arg from the bundled worked
examples), OMIM/OMIA entries, and the divisions' own recorded
Ensembl/Europe PMC fixtures.  v2 grows the set from 17 to 49
probes (Human 13, Animal 18, Plant 18) and gives every division
disclosure probes.  The Animal and Plant disclosure probes need
one mechanism the Human fixture does not: the divisions' live
providers only ever emit supporting items, so a
:class:`DisclosureFixtureProvider` wraps each division provider
and adds ONE real published refuting record to one named topic;
everything else delegates untouched.
No patient data, ever (Item 37 Level 0).  Every probe carries
its ground truth and the public source where a human can check
it.  Fixture mode is the scored run: deterministic, no network.

What this establishes: gate *behaviour* on a fixed, public,
builder-chosen probe set.  What it does NOT establish: external
clinical validation.  That caveat is carried literally in every
report (:data:`CAVEAT`).  Nothing is published from this
benchmark until the Founder Witness Gate.

Run it:

    python -m provome.public_benchmark

Research software.  Not for clinical use.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, replace
from datetime import date
from typing import Callable, Optional

from . import animal_benchmark, plant_benchmark
from .animal import AnimalCopilot, AnimalEvidenceProvider
from .copilot import Copilot, Verdict
from .domains import Division
from .evidence import (
    EvidenceItem,
    SupportDirection,
    VerificationStatus,
    default_source_registry,
    evaluate_gate,
)
from .plant import PlantCopilot, PlantEvidenceProvider

__all__ = [
    "BENCHMARK_VERSION",
    "CAVEAT",
    "CAUSE_CLASSES",
    "CONFIRMER_CAVEAT",
    "CONFIRMER_CAVEAT_WITH_HUMAN_LABELS",
    "DisclosureFixtureProvider",
    "DivisionHumanScore",
    "DivisionScore",
    "FailureRecord",
    "HumanBaseline",
    "HumanLabels",
    "HumanLabelsError",
    "LABEL_VALUES",
    "PreregistrationError",
    "ProbeOutcome",
    "PublicBenchmarkReport",
    "PublicHumanProvider",
    "PublicProbe",
    "Rate",
    "SystemScore",
    "probe_set_hash",
    "probes",
    "run_benchmark",
    "wilson_interval",
]

BENCHMARK_VERSION = "public-benchmark-v2"

#: The honesty caveat every report carries, verbatim.  Fixture and
#: public-probe results measure how the gate behaves; they are not
#: evidence that any division is clinically validated.
CAVEAT = (
    "Fixture/public-probe results are a benchmark of gate "
    "behaviour, not external clinical validation."
)

#: The confirmer caveat every report carries, verbatim.  The
#: confirmer is a second implementation of the gate rule, built
#: for calibration; it is not a person, and agreement with it is
#: not agreement with human judgement.
CONFIRMER_CAVEAT = (
    "The confirmer baseline is an internal calibration "
    "stand-in, not a human baseline: it is an independent "
    "re-implementation of the gate rule, and no human labels "
    "exist for this probe set yet."
)

#: The confirmer caveat as carried when human labels WERE
#: supplied for the run.  The default caveat's closing clause
#: ("no human labels exist for this probe set yet") would be
#: false on such a run, so the report switches to this text:
#: the confirmer is still a stand-in, and the human labels are
#: reported in their own section, never merged with it.
CONFIRMER_CAVEAT_WITH_HUMAN_LABELS = (
    "The confirmer baseline is an internal calibration "
    "stand-in, not a human baseline: it is an independent "
    "re-implementation of the gate rule. Human labels were "
    "supplied for this run and are reported separately in "
    "the human-baseline section."
)

#: The fixed failure-cause taxonomy.  Every failure record's cause
#: class is one of these; inventing a new class is a new version.
CAUSE_CLASSES = (
    "retrieval_miss",   # topic found, evidence below the gate
    "gate_arithmetic",  # verdict contradicts the gate's own maths
    "intent_parse",     # the question's topic was not recognised
    "fixture_gap",      # topic found, fixture holds no evidence
    "provider_gap",     # provider supplied passing evidence for a trap
)

_DIVISIONS = ("human", "animal", "plant")

_V = VerificationStatus.VERIFIED
_U = VerificationStatus.UNVERIFIED
_S = SupportDirection.SUPPORTS
_R = SupportDirection.REFUTES


# ---------------------------------------------------------------------
# The Human fixture provider: published ClinVar/OMIM facts only.
#
# Items cite the public records a human can check (ClinVar
# variation reports, OMIM entries, named peer-reviewed reviews).
# Only registered production sources are used, and the gate runs
# against the production registry — illustrative demo sources can
# never satisfy this benchmark.
# ---------------------------------------------------------------------


def _item(item_id, topic, source, statement, citation,
          direction=_S, status=_V):
    return EvidenceItem(
        item_id=item_id,
        topic_key=topic,
        statement=statement,
        source_id=source,
        citation=citation,
        direction=direction,
        status=status,
    )


_HUMAN_TOPICS = {
    "cftr:p.phe508del": {
        "aliases": ("cftr p.phe508del", "phe508del", "f508del"),
        "items": (
            _item(
                "pub-cftr-1", "cftr:p.phe508del", "clinvar",
                "ClinVar classifies CFTR c.1521_1523delCTT "
                "(p.Phe508del) as Pathogenic for cystic fibrosis.",
                "ClinVar, CFTR c.1521_1523delCTT (p.Phe508del) "
                "variation report (VCV000007105)",
            ),
            _item(
                "pub-cftr-2", "cftr:p.phe508del", "pubmed-literature",
                "The cystic fibrosis genetics literature identifies "
                "p.Phe508del as the most common CF-causing CFTR "
                "variant, impairing protein folding and trafficking.",
                "Cutting GR. Cystic fibrosis genetics: from "
                "molecular understanding to clinical application. "
                "Nature Reviews Genetics, 2015",
            ),
            _item(
                "pub-cftr-3", "cftr:p.phe508del", "omim",
                "OMIM catalogues cystic fibrosis (#219700) as the "
                "CFTR-related disorder for which p.Phe508del is the "
                "classic disease-causing allele.",
                "OMIM #219700, Cystic Fibrosis",
            ),
        ),
    },
    "cftr:p.phe508del:disagreement": {
        "aliases": ("disagreement in the record about cftr",),
        "items": (
            _item(
                "pub-cftrd-1", "cftr:p.phe508del:disagreement",
                "clinvar",
                "ClinVar classifies CFTR p.Phe508del as Pathogenic "
                "for cystic fibrosis.",
                "ClinVar, CFTR c.1521_1523delCTT (p.Phe508del) "
                "variation report (VCV000007105)",
            ),
            _item(
                "pub-cftrd-2", "cftr:p.phe508del:disagreement",
                "omim",
                "OMIM catalogues cystic fibrosis (#219700) as the "
                "CFTR-related disorder caused by p.Phe508del.",
                "OMIM #219700, Cystic Fibrosis",
            ),
            _item(
                "pub-cftrd-3", "cftr:p.phe508del:disagreement",
                "pubmed-literature",
                "Reviews of CFTR genetics describe p.Phe508del as "
                "the predominant CF-causing allele worldwide.",
                "Cutting GR. Cystic fibrosis genetics: from "
                "molecular understanding to clinical application. "
                "Nature Reviews Genetics, 2015",
            ),
            _item(
                "pub-cftrd-4", "cftr:p.phe508del:disagreement",
                "pubmed-literature",
                "Reports of variable expressivity describe "
                "p.Phe508del carriers with non-classic or milder "
                "phenotypes, complicating any blanket severity "
                "claim for the variant.",
                "Peer-reviewed literature on variable expressivity "
                "of CFTR p.Phe508del, indexed in PubMed",
                direction=_R,
            ),
        ),
    },
    "hbb:p.glu6val": {
        "aliases": ("hbb p.glu6val", "glu6val"),
        "items": (
            _item(
                "pub-hbb-1", "hbb:p.glu6val", "clinvar",
                "ClinVar classifies HBB c.20A>T (p.Glu6Val) as "
                "Pathogenic: it is the sickle cell variant.",
                "ClinVar, HBB c.20A>T (p.Glu6Val) variation report",
            ),
            _item(
                "pub-hbb-2", "hbb:p.glu6val", "omim",
                "OMIM catalogues sickle cell anemia (#603903) as "
                "the disorder caused by the HBB p.Glu6Val "
                "substitution (haemoglobin S).",
                "OMIM #603903, Sickle Cell Anemia",
            ),
            _item(
                "pub-hbb-3", "hbb:p.glu6val", "pubmed-literature",
                "The sickle cell disease literature establishes "
                "p.Glu6Val haemoglobin polymerisation as the "
                "molecular basis of sickling.",
                "Piel FB, Steinberg MH, Rees DC. Sickle Cell "
                "Disease. New England Journal of Medicine, 2017",
            ),
        ),
    },
    "hbb:p.glu6val:disagreement": {
        "aliases": ("disagreement in the record about hbb",),
        "items": (
            _item(
                "pub-hbbd-1", "hbb:p.glu6val:disagreement",
                "clinvar",
                "ClinVar classifies HBB c.20A>T (p.Glu6Val) as "
                "Pathogenic: it is the sickle cell variant.",
                "ClinVar, HBB c.20A>T (p.Glu6Val) variation report",
            ),
            _item(
                "pub-hbbd-2", "hbb:p.glu6val:disagreement",
                "omim",
                "OMIM catalogues sickle cell anemia (#603903) as "
                "the disorder caused by the HBB p.Glu6Val "
                "substitution (haemoglobin S).",
                "OMIM #603903, Sickle Cell Anemia",
            ),
            _item(
                "pub-hbbd-3", "hbb:p.glu6val:disagreement",
                "pubmed-literature",
                "The sickle cell disease literature establishes "
                "p.Glu6Val haemoglobin polymerisation as the "
                "molecular basis of sickling.",
                "Piel FB, Steinberg MH, Rees DC. Sickle Cell "
                "Disease. New England Journal of Medicine, 2017",
            ),
            _item(
                "pub-hbbd-4", "hbb:p.glu6val:disagreement",
                "pubmed-literature",
                "Reports describe markedly milder sickle cell "
                "phenotypes in patients who co-inherit "
                "alpha-thalassaemia or hereditary persistence of "
                "fetal haemoglobin, complicating any blanket "
                "severity claim for the p.Glu6Val genotype.",
                "Peer-reviewed literature on genetic modifiers "
                "of sickle cell disease severity, indexed in "
                "PubMed",
                direction=_R,
            ),
        ),
    },
    "fgfr3:p.gly380arg": {
        "aliases": ("fgfr3 p.gly380arg", "gly380arg"),
        "items": (
            _item(
                "pub-fgfr3-1", "fgfr3:p.gly380arg", "clinvar",
                "ClinVar classifies FGFR3 c.1138G>A (p.Gly380Arg) "
                "as Pathogenic for achondroplasia.",
                "ClinVar, FGFR3 c.1138G>A (p.Gly380Arg) "
                "variation report",
            ),
            _item(
                "pub-fgfr3-2", "fgfr3:p.gly380arg", "omim",
                "OMIM catalogues achondroplasia (#100800) as the "
                "FGFR3-related skeletal dysplasia for which "
                "p.Gly380Arg is the classic causative allele.",
                "OMIM #100800, Achondroplasia",
            ),
            _item(
                "pub-fgfr3-3", "fgfr3:p.gly380arg",
                "pubmed-literature",
                "The achondroplasia literature establishes "
                "p.Gly380Arg in the FGFR3 transmembrane domain "
                "as the cause of nearly all classic "
                "achondroplasia, acting through constitutive "
                "receptor activation.",
                "Shiang R et al. Mutations in the transmembrane "
                "domain of FGFR3 cause the most common genetic "
                "form of dwarfism (achondroplasia). Cell, 1994",
            ),
        ),
    },
    "brca1:c.68_69delag": {
        "aliases": ("brca1 c.68_69delag", "68_69delag", "185delag"),
        "items": (
            _item(
                "pub-brca1-1", "brca1:c.68_69delag", "clinvar",
                "An aggregator copy claims BRCA1 c.68_69delAG is "
                "Pathogenic (unchecked copy; not verified).",
                "ClinVar, BRCA1 c.68_69delAG variation report "
                "(unverified aggregator copy)",
                status=_U,
            ),
            _item(
                "pub-brca1-2", "brca1:c.68_69delag", "omim",
                "An aggregator copy claims the OMIM BRCA1 entry "
                "covers c.68_69delAG (unchecked copy; not "
                "verified).",
                "OMIM #113705, BRCA1 (unverified aggregator copy)",
                status=_U,
            ),
            _item(
                "pub-brca1-3", "brca1:c.68_69delag",
                "pubmed-literature",
                "An aggregator copy claims the literature reports "
                "c.68_69delAG as a founder variant (unchecked "
                "copy; not verified).",
                "Peer-reviewed literature on BRCA1 185delAG, "
                "indexed in PubMed (unverified aggregator copy)",
                status=_U,
            ),
        ),
    },
}


class PublicHumanProvider:
    """Fixture provider for the Human division's public probes.

    Topic recognition is the bundled demo's transparent rule —
    longest word-boundary alias match, no alias means no topic —
    but the evidence is built from published public records and
    production sources only.  No network, deterministic.
    """

    name = "public-fixture-human"
    scoped_divisions = frozenset({Division.HUMAN})

    def __init__(self, topics: Optional[dict] = None) -> None:
        self._topics = topics if topics is not None else _HUMAN_TOPICS

    def topic_for_question(self, question_text: str) -> Optional[str]:
        lowered = question_text.lower()
        best_topic: Optional[str] = None
        best_length = -1
        for topic_key, topic in self._topics.items():
            for alias in topic["aliases"]:
                pattern = (
                    r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])"
                )
                if re.search(pattern, lowered) and len(alias) > best_length:
                    best_topic = topic_key
                    best_length = len(alias)
        return best_topic

    def items_for_topic(self, topic_key: str) -> tuple:
        topic = self._topics.get(topic_key)
        if topic is None:
            return ()
        return topic["items"]


# ---------------------------------------------------------------------
# Disclosure fixtures for the Animal and Plant surfaces.
#
# The divisions' live providers only ever emit SUPPORTS items —
# Ensembl records and PubMed-confirmed literature are supporting
# by construction — so no recorded payload can produce the
# verified refuting item a disclosure probe needs.  The Human
# fixture solves the same problem with a hand-built disagreement
# topic; the division equivalent is this wrapper.  For ONE named
# topic it serves the wrapped provider's own recorded items,
# re-keyed, plus one real published record that genuinely
# complicates the blanket claim, cited where a human can check
# it.  Everything else delegates untouched, so every other probe
# sees the provider exactly as the division benchmarks do.
# ---------------------------------------------------------------------

_ANIMAL_DISCLOSURE_TOPIC = "animal:sheep:PRNP"
_ANIMAL_DISCLOSURE_REFUTATION = _item(
    "pub-sheep-prnp-refute-1",
    "animal:sheep:PRNP:disagreement",
    "pubmed-literature",
    "Atypical scrapie (Nor98) has been reported in sheep of "
    "the ARR/ARR PRNP genotype long described as resistant to "
    "classical scrapie, complicating any blanket claim that "
    "PRNP genotype alone settles scrapie susceptibility.",
    "Benestad SL et al. Cases of scrapie with unusual "
    "features in Norway and designation of a new type, "
    "Nor98. The Veterinary Record, 2003",
    direction=_R,
)

_PLANT_DISCLOSURE_TOPIC = "plant:rice:XA21"
_PLANT_DISCLOSURE_REFUTATION = _item(
    "pub-rice-xa21-refute-1",
    "plant:rice:XA21:disagreement",
    "pubmed-literature",
    "Xa21-mediated resistance is developmentally regulated: "
    "young rice seedlings carrying Xa21 remain susceptible "
    "to bacterial blight, and resistance strengthens as the "
    "plant matures, complicating any blanket claim that "
    "Xa21 confers resistance at every growth stage.",
    "Century KS et al. Developmental control of Xa21-mediated "
    "disease resistance in rice. The Plant Journal, 1999",
    direction=_R,
)


class DisclosureFixtureProvider:
    """Wraps a division fixture provider and adds one published
    refuting record to one named topic, under a
    ``<topic>:disagreement`` key.

    Topic recognition delegates to the wrapped provider, except
    that a disagreement-phrased question (the word
    "disagreement" in the question text) about the named topic
    is routed to the disagreement key — the same routing the
    Human fixture's disagreement alias performs.  The
    disagreement topic's items are the wrapped provider's own
    recorded items for the topic, re-keyed, plus the refuting
    record.  All other topics delegate byte-for-byte.
    """

    name = "public-fixture-disclosure-wrapper"

    def __init__(self, base, base_topic: str, refuting_item) -> None:
        self._base = base
        self._base_topic = base_topic
        self._disagreement_topic = base_topic + ":disagreement"
        self._refuting_item = refuting_item
        self.scoped_divisions = getattr(
            base, "scoped_divisions", frozenset()
        )

    def topic_for_question(self, question_text: str):
        topic = self._base.topic_for_question(question_text)
        if topic == self._base_topic \
                and "disagreement" in question_text.lower():
            return self._disagreement_topic
        return topic

    def items_for_topic(self, topic_key: str) -> tuple:
        if topic_key != self._disagreement_topic:
            return self._base.items_for_topic(topic_key)
        rekeyed = tuple(
            replace(item, topic_key=topic_key)
            for item in self._base.items_for_topic(self._base_topic)
        )
        return rekeyed + (self._refuting_item,)


# ---------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class PublicProbe:
    """One frozen public test: question in, expected verdict out.

    ``division`` is the division whose surface the question is put
    to — wrong-division traps are tagged by the surface asked, so
    a cross-division leak is scored against the division that
    leaked.  ``ground_truth`` states what is true in the world and
    why the expected verdict follows; ``source_url`` is the public
    record where a human can check it.
    """

    probe_id: str
    division: str  # "human" | "animal" | "plant"
    category: str  # "answer" | "decline" | "disclose"
    question: str
    expected_answer: bool
    ground_truth: str
    source_url: str


def _from_division_probe(division: str, probe) -> PublicProbe:
    return PublicProbe(
        probe_id=f"{division}-{probe.name}",
        division=division,
        category=probe.category,
        question=probe.question,
        expected_answer=probe.expected_answer,
        ground_truth=probe.ground_truth,
        source_url=probe.source_url,
    )


def probes() -> tuple:
    """The frozen probe set for public-benchmark-v2. No network.

    Human probes are defined here; Animal and Plant probes are
    selected by name from those divisions' own frozen benchmarks,
    so each division's ground truth stays single-sourced.
    """
    human = (
        PublicProbe(
            "human-cftr-phe508del", "human", "answer",
            "What is known about CFTR p.Phe508del?",
            True,
            "CFTR c.1521_1523delCTT (p.Phe508del) is the classic "
            "cystic-fibrosis-causing variant: classified "
            "Pathogenic in ClinVar (VCV000007105) and catalogued "
            "under OMIM #219700 (cystic fibrosis). The fixture "
            "holds verified items from three independent groups, "
            "so the gate must answer.",
            "https://www.omim.org/entry/219700",
        ),
        PublicProbe(
            "human-hbb-glu6val", "human", "answer",
            "What is known about HBB p.Glu6Val?",
            True,
            "HBB c.20A>T (p.Glu6Val) is the sickle cell variant: "
            "classified Pathogenic in ClinVar and catalogued "
            "under OMIM #603903 (sickle cell anemia). The fixture "
            "holds verified items from three independent groups, "
            "so the gate must answer.",
            "https://www.omim.org/entry/603903",
        ),
        PublicProbe(
            "human-cftr-disagreement-disclose", "human", "disclose",
            "Is there disagreement in the record about CFTR "
            "p.Phe508del?",
            True,
            "The published record supports the Pathogenic "
            "consensus (ClinVar; OMIM #219700) but is not "
            "unanimous on severity: peer-reviewed reports of "
            "variable expressivity describe non-classic, milder "
            "phenotypes in some p.Phe508del carriers. The gate "
            "passes, and the disagreement must be surfaced, "
            "never hidden.",
            "https://www.omim.org/entry/219700",
        ),
        PublicProbe(
            "human-brca1-unverified-only", "human", "decline",
            "What is known about BRCA1 c.68_69delAG?",
            False,
            "BRCA1 c.68_69delAG (185delAG) is a real, published "
            "founder variant classified Pathogenic in ClinVar — "
            "but the only items this fixture holds for it are "
            "UNVERIFIED aggregator copies. A true claim with "
            "unchecked citations is still not answerable: "
            "cite-or-decline must decline.",
            "https://www.omim.org/entry/113705",
        ),
        PublicProbe(
            "human-cftr-fabricated-variant", "human", "decline",
            "What is known about CFTR p.Zzz999Xyz?",
            False,
            "CFTR p.Zzz999Xyz does not exist: no such variant is "
            "recorded in ClinVar or anywhere else; the residue "
            "string is invented. Any answer here would be "
            "fabricated.",
            "https://www.ncbi.nlm.nih.gov/clinvar/?term=CFTR",
        ),
        PublicProbe(
            "human-zzzqx9-fabricated-gene", "human", "decline",
            "What is known about ZZZQX9 p.Ala1Val in humans?",
            False,
            "ZZZQX9 is a fabricated gene symbol — the same "
            "invented symbol the Animal and Plant benchmarks use "
            "— with no record in any public source. Any answer "
            "here would be invented.",
            "https://www.ncbi.nlm.nih.gov/clinvar/?term=ZZZQX9",
        ),
        PublicProbe(
            "human-dog-abcb1-wrong-division", "human", "decline",
            "What is known about ABCB1 drug sensitivity in dogs?",
            False,
            "ABCB1 (MDR1) drug sensitivity in dogs is a real "
            "Animal-division association (Ensembl records the "
            "gene in Canis lupus familiaris, "
            "ENSCAFG00845007972) — but this question is put to "
            "the HUMAN surface, whose evidence is human-scoped. "
            "It must decline rather than borrow another "
            "division's evidence.",
            "https://www.ensembl.org/id/ENSCAFG00845007972",
        ),
        PublicProbe(
            "human-fgfr3-gly380arg", "human", "answer",
            "What is known about FGFR3 p.Gly380Arg?",
            True,
            "FGFR3 c.1138G>A (p.Gly380Arg) is the classic "
            "achondroplasia variant — the variant this repo's "
            "own bundled worked examples record (provome/data/"
            "examples.json: nearly all achondroplasia cases "
            "carry it). Classified Pathogenic in ClinVar and "
            "catalogued under OMIM #100800 (achondroplasia). "
            "The fixture holds verified items from three "
            "independent groups, so the gate must answer.",
            "https://www.omim.org/entry/100800",
        ),
        PublicProbe(
            "human-hbb-disagreement-disclose", "human", "disclose",
            "Is there disagreement in the record about HBB "
            "p.Glu6Val?",
            True,
            "The published record supports the sickle cell "
            "consensus (ClinVar; OMIM #603903) but severity is "
            "not uniform: peer-reviewed reports describe "
            "markedly milder phenotypes in patients who "
            "co-inherit alpha-thalassaemia or hereditary "
            "persistence of fetal haemoglobin. The gate "
            "passes, and the disagreement must be surfaced, "
            "never hidden.",
            "https://www.omim.org/entry/603903",
        ),
        PublicProbe(
            "human-hbb-fabricated-variant", "human", "decline",
            "What is known about HBB p.Zzz999Xyz?",
            False,
            "HBB p.Zzz999Xyz does not exist: no such variant "
            "is recorded in ClinVar or anywhere else; the "
            "residue string is invented. Any answer here "
            "would be fabricated.",
            "https://www.ncbi.nlm.nih.gov/clinvar/?term=HBB",
        ),
        PublicProbe(
            "human-fakgene1-fabricated-gene", "human", "decline",
            "What is known about FAKGENE1 in humans?",
            False,
            "FAKGENE1 is a fabricated gene symbol — the same "
            "invented symbol the Animal and Plant benchmarks "
            "use — with no record in any public source. Any "
            "answer here would be invented.",
            "https://www.ncbi.nlm.nih.gov/clinvar/?term=FAKGENE1",
        ),
        PublicProbe(
            "human-flc-plant-gene-wrong-division", "human",
            "decline",
            "What is known about FLC in Arabidopsis?",
            False,
            "FLC is the Arabidopsis flowering-time gene — a "
            "Plant-division topic (Ensembl Plants AT5G10140). "
            "Put to the HUMAN surface, whose evidence is "
            "human-scoped, it must decline rather than borrow "
            "another division's evidence.",
            "https://plants.ensembl.org/id/AT5G10140",
        ),
        PublicProbe(
            "human-prnp-sheep-wrong-division", "human", "decline",
            "What is known about PRNP scrapie susceptibility "
            "in sheep?",
            False,
            "PRNP scrapie susceptibility in sheep is a real "
            "Animal-division association (OMIA 000944-9940) — "
            "but this question is put to the HUMAN surface, "
            "whose evidence is human-scoped. It must decline "
            "rather than borrow another division's evidence.",
            "https://omia.org/OMIA000944/9940/",
        ),
    )
    animal_by_name = {p.name: p for p in animal_benchmark.probes()}
    animal = tuple(
        _from_division_probe("animal", animal_by_name[name])
        for name in (
            "dog-abcb1-mdr1",
            "pig-ryr1-malignant-hyperthermia",
            "horse-prkdc-scid",
            "cattle-itgb2-blad",
            "sheep-prnp-scrapie",
            "dog-sod1-degenerative-myelopathy",
            "cat-mybpc3-hcm",
            "horse-scn4a-hypp",
            "dog-rpe65-retinal",
            "cat-pkd1-below-gate",
            "dog-arhgap11b-human-specific",
            "dog-fakgene1-fabricated",
            "horse-notreal7-fabricated",
            "no-species-abcb1",
            "cattle-zzzqx9-fabricated",
            "chicken-sry-wrong-species",
        )
    ) + (
        PublicProbe(
            "animal-cftr-human-variant", "animal", "decline",
            "What is known about CFTR p.Phe508del?",
            False,
            "CFTR p.Phe508del is a Human-division clinical "
            "variant (ClinVar; OMIM #219700). Put to the Animal "
            "surface with no species named, it must decline: "
            "human evidence is never issued as an animal answer, "
            "and no animal topic may be assumed from a bare "
            "human variant.",
            "https://www.omim.org/entry/219700",
        ),
        PublicProbe(
            "animal-sheep-prnp-disagreement-disclose", "animal",
            "disclose",
            "Is there disagreement in the record about PRNP "
            "scrapie susceptibility in sheep?",
            True,
            "The recorded record supports the PRNP-scrapie "
            "association (Ensembl gene record; OMIA 000944-9940 "
            "phenotype association), but the published record "
            "is not a blanket story: atypical scrapie (Nor98) "
            "has been reported in sheep of the ARR/ARR genotype "
            "long described as resistant to classical scrapie "
            "(Benestad et al., The Veterinary Record, 2003). "
            "The gate passes on the supporting record, and the "
            "disagreement must be surfaced, never hidden. The "
            "refuting record reaches the surface through the "
            "disclosure fixture wrapper (the division's live "
            "providers emit supporting items only).",
            "https://omia.org/OMIA000944/9940/",
        ),
    )
    plant_by_name = {p.name: p for p in plant_benchmark.probes()}
    plant = tuple(
        _from_division_probe("plant", plant_by_name[name])
        for name in (
            "arabidopsis-flc-flowering",
            "rice-xa21-blight-resistance",
            "arabidopsis-ttg1-trichome",
            "arabidopsis-ft-florigen",
            "arabidopsis-cop1-light",
            "tomato-sp-self-pruning",
            "soybean-e1-maturity",
            "soybean-dt1-determinacy",
            "rice-sd1-no-symbol-record",
            "maize-tb1-no-symbol-record",
            "arabidopsis-fri-below-gate",
            "rice-tp53-human-gene",
            "rice-flc-wrong-species",
            "wheat-drought-tolerance-trait",
            "wheat-zzzqx9-fabricated",
            "rice-brca1-human-gene",
            "no-species-flc",
        )
    ) + (
        PublicProbe(
            "plant-rice-xa21-disagreement-disclose", "plant",
            "disclose",
            "Is there disagreement in the record about XA21 "
            "blight resistance in rice?",
            True,
            "The recorded record supports XA21-mediated "
            "bacterial blight resistance (Ensembl Plants gene "
            "record Os11g0569733; resistance literature), but "
            "the published record qualifies it: Xa21 resistance "
            "is developmentally regulated, and young seedlings "
            "carrying Xa21 remain susceptible (Century et al., "
            "The Plant Journal, 1999). The gate passes, and "
            "the qualification must be surfaced, never hidden. "
            "The refuting record reaches the surface through "
            "the disclosure fixture wrapper (the division's "
            "live providers emit supporting items only).",
            "https://plants.ensembl.org/id/Os11g0569733",
        ),
    )
    return human + animal + plant


# ---------------------------------------------------------------------
# Preregistration
# ---------------------------------------------------------------------


class PreregistrationError(ValueError):
    """A run was handed a preregistered hash that does not match
    the probe set it was about to score.  The run refuses: scoring
    a silently edited probe set under an old hash is exactly the
    failure preregistration exists to prevent."""


def _probe_record(probe: PublicProbe) -> dict:
    return {
        "probe_id": probe.probe_id,
        "division": probe.division,
        "category": probe.category,
        "question": probe.question,
        "expected_answer": probe.expected_answer,
        "ground_truth": probe.ground_truth,
        "source_url": probe.source_url,
    }


def probe_set_hash(probe_set) -> str:
    """Stable SHA-256 of a probe set, over canonical JSON.

    Every field that defines a probe is in the hashed record, so
    changing any probe — a question, an expected verdict, a word
    of ground truth — changes the hash.
    """
    records = [_probe_record(p) for p in probe_set]
    payload = json.dumps(
        records, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------
# Scoring: fractions with Wilson intervals, nothing bare.
# ---------------------------------------------------------------------


def wilson_interval(numerator: int, denominator: int,
                    z: float = 1.96) -> tuple:
    """Wilson score interval for a binomial proportion.

    Returns ``(low, high)``; ``(None, None)`` when the denominator
    is zero — an interval for zero trials does not exist, and
    inventing one would be a fabricated score.
    """
    if denominator == 0:
        return (None, None)
    if numerator < 0 or numerator > denominator:
        raise ValueError("numerator must lie within the denominator")
    p = numerator / denominator
    z2 = z * z
    denom = 1.0 + z2 / denominator
    centre = (p + z2 / (2 * denominator)) / denom
    half = (z / denom) * math.sqrt(
        p * (1.0 - p) / denominator + z2 / (4 * denominator * denominator)
    )
    low = max(0.0, centre - half)
    high = min(1.0, centre + half)
    # At the extremes the Wilson bound is exact, not approximate:
    # x = 0 bounds below at 0 and x = n bounds above at 1.  Pin
    # them so float error cannot print a bound of 0.99999... .
    if numerator == 0:
        low = 0.0
    if numerator == denominator:
        high = 1.0
    return (low, high)


@dataclass(frozen=True)
class Rate:
    """One measured score: an ``x/n`` fraction with its interval.

    ``value`` is ``None`` when ``denominator`` is zero — the rate
    does not exist, and the report says so instead of printing a
    number.
    """

    numerator: int
    denominator: int
    low: Optional[float]
    high: Optional[float]

    @classmethod
    def of(cls, numerator: int, denominator: int) -> "Rate":
        low, high = wilson_interval(numerator, denominator)
        return cls(
            numerator=numerator, denominator=denominator,
            low=low, high=high,
        )

    @property
    def value(self) -> Optional[float]:
        if self.denominator == 0:
            return None
        return self.numerator / self.denominator


@dataclass(frozen=True)
class DivisionScore:
    """One system's scores for one division (or ``"overall"``).

    Every field is a :class:`Rate`: a denominator of zero is
    reported as ``n=0`` with a ``None`` value, never merged into
    another division and never replaced by a fabricated number.
    """

    division: str
    probes: int
    verdict: Rate             # correct verdicts / all probes
    answer_rate: Rate         # answered correctly / answer probes
    decline_correctness: Rate  # declined correctly / decline probes
    disclosure: Rate          # answered + disclosed / disclose probes


@dataclass(frozen=True)
class SystemScore:
    """One scored system: the gate, or one trivial baseline.

    The gate and the baselines share this exact structure and live
    in the same tuple on the report, so the gate's numbers are
    never carried anywhere without the floor beside them.
    """

    name: str  # "gate" | "confirmer" | "always-decline" | "always-answer"
    overall: DivisionScore
    by_division: tuple  # DivisionScore per division, fixed order


@dataclass(frozen=True)
class _Scored:
    """The minimal scored outcome the metrics are computed from."""

    probe_id: str
    division: str
    category: str
    answered: bool
    disclosed: bool
    correct: bool


def _score_system(name: str, scored) -> SystemScore:
    def division_score(division: str, rows) -> DivisionScore:
        answer_rows = [r for r in rows if r.category == "answer"]
        decline_rows = [r for r in rows if r.category == "decline"]
        disclose_rows = [r for r in rows if r.category == "disclose"]
        return DivisionScore(
            division=division,
            probes=len(rows),
            verdict=Rate.of(
                sum(1 for r in rows if r.correct), len(rows)
            ),
            answer_rate=Rate.of(
                sum(1 for r in answer_rows if r.correct),
                len(answer_rows),
            ),
            decline_correctness=Rate.of(
                sum(1 for r in decline_rows if r.correct),
                len(decline_rows),
            ),
            disclosure=Rate.of(
                sum(
                    1 for r in disclose_rows
                    if r.answered and r.disclosed
                ),
                len(disclose_rows),
            ),
        )

    scored = tuple(scored)
    by_division = tuple(
        division_score(d, [r for r in scored if r.division == d])
        for d in _DIVISIONS
    )
    return SystemScore(
        name=name,
        overall=division_score("overall", scored),
        by_division=by_division,
    )


def _verdict_correct(category: str, answered: bool,
                     disclosed: bool) -> bool:
    """The one correctness rule, shared by gate and baselines."""
    if category == "answer":
        return answered
    if category == "decline":
        return not answered
    if category == "disclose":
        return answered and disclosed
    raise ValueError(f"unknown probe category: {category!r}")


# ---------------------------------------------------------------------
# Running the gate system
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeOutcome:
    """The measured outcome for one probe, gate system."""

    probe_id: str
    division: str
    category: str
    expected_answer: bool
    answered: bool
    disclosed: bool
    correct: bool
    topic_recognised: bool
    verified_items: int
    source_groups: int
    gate_passed: Optional[bool]  # None when no topic was recognised
    detail: str


@dataclass(frozen=True)
class FailureRecord:
    """One entry in the failure register: a probe the gate got
    wrong, written down with its cause class."""

    probe_id: str
    division: str
    category: str
    expected: str  # "answer" | "decline" | "answer+disclose"
    actual: str    # "answer" | "decline" | "answer (undisclosed)"
    cause_class: str  # one of CAUSE_CLASSES
    detail: str


def _expected_label(probe: PublicProbe) -> str:
    if probe.category == "disclose":
        return "answer+disclose"
    return "answer" if probe.expected_answer else "decline"


def _actual_label(outcome: ProbeOutcome) -> str:
    if not outcome.answered:
        return "decline"
    if outcome.category == "disclose" and not outcome.disclosed:
        return "answer (undisclosed)"
    return "answer"


def _cause_class(probe: PublicProbe, outcome: ProbeOutcome) -> str:
    """Classify a failure into the fixed taxonomy, deterministically."""
    if outcome.answered and not probe.expected_answer:
        # A trap was answered.  If the gate's own maths genuinely
        # pass on the provider's items, the provider supplied
        # passing evidence for a trap; if they do not, the surface
        # answered against its own gate.
        return "provider_gap" if outcome.gate_passed else "gate_arithmetic"
    if outcome.answered and probe.category == "disclose" \
            and not outcome.disclosed:
        return "gate_arithmetic"  # disagreement was hidden
    if outcome.answered and outcome.gate_passed is not True:
        return "gate_arithmetic"  # answer the audit cannot back
    if not outcome.answered and probe.expected_answer:
        if not outcome.topic_recognised:
            return "intent_parse"
        if outcome.verified_items == 0:
            return "fixture_gap"
        return "retrieval_miss"
    return "gate_arithmetic"


def _default_factories() -> dict:
    return {
        "human": PublicHumanProvider,
        "animal": lambda: DisclosureFixtureProvider(
            AnimalEvidenceProvider(
                transport=animal_benchmark.fixture_transport,
                today=lambda: date(2026, 11, 25),
            ),
            _ANIMAL_DISCLOSURE_TOPIC,
            _ANIMAL_DISCLOSURE_REFUTATION,
        ),
        "plant": lambda: DisclosureFixtureProvider(
            PlantEvidenceProvider(
                transport=plant_benchmark.fixture_transport,
                today=lambda: date(2026, 11, 25),
            ),
            _PLANT_DISCLOSURE_TOPIC,
            _PLANT_DISCLOSURE_REFUTATION,
        ),
    }


def _run_human(provider, probe: PublicProbe) -> ProbeOutcome:
    registry = default_source_registry()
    copilot = Copilot(provider=provider)
    answer = copilot.ask(probe.question)
    answered = answer.verdict is Verdict.ANSWER
    topic = provider.topic_for_question(probe.question)
    verified = 0
    groups = 0
    gate_passed: Optional[bool] = None
    disclosed = False
    detail = ""
    if topic is not None:
        decision = evaluate_gate(
            provider.items_for_topic(topic), topic, registry
        )
        verified = decision.verified_count
        groups = decision.independent_source_count
        gate_passed = decision.passed
        disclosed = bool(decision.verified_refuting) and any(
            "disclos" in w.lower() or "refut" in w.lower()
            for w in answer.warnings
        )
    ok = _verdict_correct(probe.category, answered, disclosed)
    if answered and gate_passed is not True:
        ok = False
        detail = (
            "answered but the gate arithmetic does not pass on "
            "the provider's own items"
        )
    return ProbeOutcome(
        probe_id=probe.probe_id,
        division=probe.division,
        category=probe.category,
        expected_answer=probe.expected_answer,
        answered=answered,
        disclosed=disclosed,
        correct=ok,
        topic_recognised=topic is not None,
        verified_items=verified,
        source_groups=groups,
        gate_passed=gate_passed,
        detail=detail,
    )


def _run_division_copilot(copilot, provider, probe: PublicProbe,
                          registry) -> ProbeOutcome:
    """Animal/Plant surfaces: text in, verdict out, gate re-audited
    on the provider's own items — the divisions' own harness rule."""
    text = copilot.ask(probe.question)
    answered = text.startswith("[ANSWER]")
    verified = 0
    groups = 0
    gate_passed: Optional[bool] = None
    disclosed = False
    detail = ""
    topic = provider.topic_for_question(probe.question)
    if topic is not None:
        decision = evaluate_gate(
            provider.items_for_topic(topic), topic, registry
        )
        verified = decision.verified_count
        groups = decision.independent_source_count
        gate_passed = decision.passed
        disclosed = bool(decision.verified_refuting) and (
            "disclos" in text.lower() or "refut" in text.lower()
        )
    ok = _verdict_correct(probe.category, answered, disclosed)
    if answered and gate_passed is not True:
        ok = False
        detail = (
            "answered but the gate arithmetic does not pass on "
            "the provider's own items"
        )
    return ProbeOutcome(
        probe_id=probe.probe_id,
        division=probe.division,
        category=probe.category,
        expected_answer=probe.expected_answer,
        answered=answered,
        disclosed=disclosed,
        correct=ok,
        topic_recognised=topic is not None,
        verified_items=verified,
        source_groups=groups,
        gate_passed=gate_passed,
        detail=detail,
    )


# ---------------------------------------------------------------------
# The confirmer: an independent re-derivation of the gate rule.
#
# Item 51's adopted standard says the automated grader must
# itself be graded.  The confirmer is that check, in internal
# form: for each probe it re-derives the verdict directly from
# the provider's items with its own counting code — verified
# supporting items counted one by one, independence groups
# resolved from the registry, the refuter-block rule applied —
# and it never calls evaluate_gate and never touches a Copilot.
# A bug in the gate's arithmetic therefore shows up as
# gate/confirmer disagreement instead of being rubber-stamped.
#
# What the confirmer is NOT: a human baseline.  It implements
# the same written rule as the gate, so agreement measures
# implementation consistency, not agreement with human
# judgement.  Every report carries that limit verbatim
# (CONFIRMER_CAVEAT) until real human labels exist.
# ---------------------------------------------------------------------


def _confirmer_outcome(provider, probe: PublicProbe,
                       registry) -> _Scored:
    """Score one probe the confirmer's way, from the items up."""
    answered = False
    disclosed = False
    topic = provider.topic_for_question(probe.question)
    if topic is not None:
        supporting = []
        refuting = 0
        seen_ids = set()
        for item in provider.items_for_topic(topic):
            if item.item_id in seen_ids:
                continue
            seen_ids.add(item.item_id)
            if not item.is_verified:
                continue
            if item.source_id not in registry:
                continue
            if item.direction is SupportDirection.SUPPORTS:
                supporting.append(item)
            elif item.direction is SupportDirection.REFUTES:
                refuting += 1
        groups = {
            registry.independence_group_of(item.source_id)
            for item in supporting
        }
        blocked = bool(refuting) and (
            refuting >= (len(supporting) + 1) // 2
        )
        answered = (
            len(supporting) >= 3 and len(groups) >= 2 and not blocked
        )
        disclosed = answered and refuting > 0
    return _Scored(
        probe_id=probe.probe_id,
        division=probe.division,
        category=probe.category,
        answered=answered,
        disclosed=disclosed,
        correct=_verdict_correct(probe.category, answered, disclosed),
    )


# ---------------------------------------------------------------------
# Human labels: the real baseline Item 51 demands.
#
# The confirmer above is a second implementation of the gate
# rule; agreement with it measures implementation consistency.
# A human baseline is a different thing: a person, labelling
# blind (see :mod:`provome.labelling` for the pack a labeller
# works from), whose verdicts are then compared with the
# gate's behaviour and with the sealed expected verdicts.
# Labels bind to the exact frozen probe set through its hash,
# exactly as preregistration does: labels gathered against a
# different probe set are refused, loudly.
# ---------------------------------------------------------------------

#: The verdict vocabulary a human labeller chooses from — the
#: same three outcomes the benchmark scores: answer outright,
#: decline, or answer with the disagreement disclosed.
LABEL_VALUES = ("answer", "decline", "disclose")


class HumanLabelsError(ValueError):
    """A human labels set cannot be scored against this probe
    set: its probe-set hash does not match, it names probes the
    set does not contain, or it is malformed.  The run refuses
    rather than scoring labels against the wrong truth."""


@dataclass(frozen=True)
class HumanLabels:
    """One labeller's verdicts on the frozen probe set.

    ``labels`` is a tuple of ``(probe_id, label)`` pairs with
    label one of :data:`LABEL_VALUES`.  A partial set is
    legitimate — it is scored on the labelled subset only —
    but every named probe must belong to the probe set whose
    hash is recorded here, and that hash must match the set
    being scored.
    """

    labeller: str
    date: str
    probe_set_hash: str
    labels: tuple  # (probe_id, label) pairs

    def __post_init__(self) -> None:
        if not self.labeller or not self.labeller.strip():
            raise HumanLabelsError("labeller identity must be non-empty")
        if not self.date or not self.date.strip():
            raise HumanLabelsError("labels must carry a date")
        if not self.probe_set_hash or not self.probe_set_hash.strip():
            raise HumanLabelsError("labels must carry a probe-set hash")
        seen = set()
        for probe_id, label in self.labels:
            if label not in LABEL_VALUES:
                raise HumanLabelsError(
                    f"label for probe {probe_id!r} is {label!r}; "
                    f"labels must be one of {LABEL_VALUES}"
                )
            if probe_id in seen:
                raise HumanLabelsError(
                    f"probe {probe_id!r} is labelled twice"
                )
            seen.add(probe_id)

    def label_for(self, probe_id: str) -> Optional[str]:
        for named, label in self.labels:
            if named == probe_id:
                return label
        return None

    def __len__(self) -> int:
        return len(self.labels)


@dataclass(frozen=True)
class DivisionHumanScore:
    """Human agreement rates for one division (or ``"overall"``).

    Both fields are :class:`Rate` fractions over the LABELLED
    probes of the division only: agreement with the gate's
    actual behaviour, and agreement with the sealed expected
    verdict.  A division with no labels reports ``n=0`` with a
    ``None`` value — never a fabricated rate.
    """

    division: str
    labelled: int
    agreement_with_gate: Rate
    agreement_with_expected: Rate


@dataclass(frozen=True)
class HumanBaseline:
    """The human-baseline section of a report: present only when
    human labels were supplied to the run.

    ``labelled`` is the number of probes the labeller labelled,
    ``total`` the size of the probe set; the two are never
    conflated, and every rate in the section is computed over
    the labelled subset alone.
    """

    labeller: str
    date: str
    labelled: int
    total: int
    caveat: str
    overall: DivisionHumanScore
    by_division: tuple  # DivisionHumanScore per division, fixed order


def _gate_outcome_label(outcome: ProbeOutcome) -> str:
    """The gate's behaviour on one probe, in label vocabulary."""
    if not outcome.answered:
        return "decline"
    if outcome.disclosed:
        return "disclose"
    return "answer"


def _human_baseline_caveat(labeller: str, date_str: str,
                           labelled: int, total: int) -> str:
    return (
        f"Human baseline: real human labels by {labeller} "
        f"({date_str}), covering {labelled} of {total} probes; "
        "agreement is computed on the labelled subset only, "
        "and an unlabelled probe is never treated as labelled. "
        "These are human labels; the confirmer remains an "
        "internal calibration stand-in, not a human baseline."
    )


def _score_human_labels(probe_set, outcomes,
                        human_labels: HumanLabels) -> HumanBaseline:
    """Score one labeller's labels against gate and truth."""
    probe_by_id = {p.probe_id: p for p in probe_set}
    outcome_by_id = {o.probe_id: o for o in outcomes}
    rows = tuple(
        (probe_by_id[probe_id], outcome_by_id[probe_id], label)
        for probe_id, label in human_labels.labels
    )

    def division_score(division: str, div_rows) -> DivisionHumanScore:
        return DivisionHumanScore(
            division=division,
            labelled=len(div_rows),
            agreement_with_gate=Rate.of(
                sum(
                    1 for _, outcome, label in div_rows
                    if label == _gate_outcome_label(outcome)
                ),
                len(div_rows),
            ),
            agreement_with_expected=Rate.of(
                sum(
                    1 for probe, _, label in div_rows
                    if label == probe.category
                ),
                len(div_rows),
            ),
        )

    return HumanBaseline(
        labeller=human_labels.labeller,
        date=human_labels.date,
        labelled=len(rows),
        total=len(probe_set),
        caveat=_human_baseline_caveat(
            human_labels.labeller, human_labels.date,
            len(rows), len(probe_set),
        ),
        overall=division_score("overall", rows),
        by_division=tuple(
            division_score(
                d, [r for r in rows if r[0].division == d]
            )
            for d in _DIVISIONS
        ),
    )


# ---------------------------------------------------------------------
# The report and the run
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class PublicBenchmarkReport:
    """The benchmark outcome, stated exactly as measured.

    ``systems`` always carries the gate first, then the
    confirmer calibration baseline, then the two trivial
    baselines, all in the same structure — there is no field
    on this report that holds the gate's score without them.
    ``confirmer_agreement`` is the rate at which the gate's
    (answered, disclosed) outcome and the confirmer's agree,
    probe by probe.  ``failures`` is the gate's failure
    register: an explicit tuple, empty when the gate got
    every probe right.  ``human_baseline`` is the optional
    human-labels section: ``None`` when no labels were
    supplied to the run, a :class:`HumanBaseline` when they
    were — never a fabricated section.
    """

    version: str
    probe_set_hash: str
    preregistered_hash: Optional[str]
    preregistration_matched: Optional[bool]  # None: none supplied
    caveat: str
    confirmer_caveat: str
    confirmer_agreement: Rate
    total: int
    systems: tuple   # SystemScore: gate, confirmer, baselines
    results: tuple   # ProbeOutcome per probe, gate system, probe order
    failures: tuple  # FailureRecord per gate failure, probe order
    human_baseline: Optional[HumanBaseline] = None

    @property
    def gate(self) -> SystemScore:
        return self.systems[0]

    @property
    def passed(self) -> bool:
        return not self.failures


def run_benchmark(
    probe_set: Optional[tuple] = None,
    preregistered_hash: Optional[str] = None,
    provider_factories: Optional[dict] = None,
    human_labels: Optional[HumanLabels] = None,
) -> PublicBenchmarkReport:
    """Run the frozen probe set through all three division surfaces.

    The probe set's hash is computed before anything runs and
    recorded in the report.  When ``preregistered_hash`` is given
    and does not match, the run refuses with
    :class:`PreregistrationError` — before a single probe runs.

    Scoring is against the recorded ground truth, never the
    system's own say-so, and it is audited the way the division
    benchmarks audit: an answer whose gate arithmetic does not
    genuinely pass on the provider's own items is scored wrong
    even if the surface claimed ANSWER.  The confirmer
    re-derives every verdict from the providers' items with its
    own counting code and is scored beside the gate, with the
    gate/confirmer agreement rate reported as its own fraction.
    The two trivial baselines are scored on the same probes
    through the same correctness rule and reported beside the
    gate.

    When ``human_labels`` is supplied, the report gains the
    human-baseline section (see :class:`HumanBaseline`): the
    labels' probe-set hash must match the set being scored and
    every labelled probe must belong to it, or the run refuses
    with :class:`HumanLabelsError` before a single probe runs.
    """
    probe_set = tuple(probe_set) if probe_set is not None else probes()
    actual_hash = probe_set_hash(probe_set)
    if preregistered_hash is not None and preregistered_hash != actual_hash:
        raise PreregistrationError(
            "preregistered probe-set hash does not match the probe "
            f"set about to be scored: preregistered "
            f"{preregistered_hash!r}, actual {actual_hash!r}. The "
            "probe set has changed since preregistration; freeze "
            "it as a new version and preregister again."
        )
    if human_labels is not None:
        # Labels bind to the exact frozen set, like
        # preregistration: a labels file gathered against a
        # different probe set is refused before anything runs.
        if human_labels.probe_set_hash != actual_hash:
            raise HumanLabelsError(
                "human labels carry probe-set hash "
                f"{human_labels.probe_set_hash!r}, but the probe "
                f"set about to be scored hashes to {actual_hash!r}; "
                "labels gathered against a different probe set "
                "cannot be scored against this one"
            )
        known_ids = {p.probe_id for p in probe_set}
        unknown = [
            probe_id for probe_id, _ in human_labels.labels
            if probe_id not in known_ids
        ]
        if unknown:
            raise HumanLabelsError(
                "human labels name probe(s) the probe set does "
                f"not contain: {unknown}; refusing to score "
                "labels against probes that do not exist"
            )
    factories = _default_factories()
    if provider_factories:
        factories.update(provider_factories)
    registry = default_source_registry()

    outcomes = []
    for probe in probe_set:
        provider = factories[probe.division]()
        if probe.division == "human":
            outcome = _run_human(provider, probe)
        elif probe.division == "animal":
            outcome = _run_division_copilot(
                AnimalCopilot(provider=provider), provider, probe,
                registry,
            )
        elif probe.division == "plant":
            outcome = _run_division_copilot(
                PlantCopilot(provider=provider), provider, probe,
                registry,
            )
        else:
            raise ValueError(f"unknown division: {probe.division!r}")
        outcomes.append(outcome)

    failures = tuple(
        FailureRecord(
            probe_id=probe.probe_id,
            division=probe.division,
            category=probe.category,
            expected=_expected_label(probe),
            actual=_actual_label(outcome),
            cause_class=_cause_class(probe, outcome),
            detail=outcome.detail,
        )
        for probe, outcome in zip(probe_set, outcomes)
        if not outcome.correct
    )

    gate_scored = tuple(
        _Scored(o.probe_id, o.division, o.category, o.answered,
                o.disclosed, o.correct)
        for o in outcomes
    )
    confirmer_scored = tuple(
        _confirmer_outcome(
            factories[probe.division](), probe, registry
        )
        for probe in probe_set
    )
    agreement = Rate.of(
        sum(
            1
            for gate_row, confirmer_row in zip(
                gate_scored, confirmer_scored
            )
            if (gate_row.answered, gate_row.disclosed)
            == (confirmer_row.answered, confirmer_row.disclosed)
        ),
        len(probe_set),
    )
    decline_scored = tuple(
        _Scored(
            p.probe_id, p.division, p.category,
            answered=False, disclosed=False,
            correct=_verdict_correct(p.category, False, False),
        )
        for p in probe_set
    )
    answer_scored = tuple(
        _Scored(
            p.probe_id, p.division, p.category,
            answered=True, disclosed=False,
            correct=_verdict_correct(p.category, True, False),
        )
        for p in probe_set
    )
    systems = (
        _score_system("gate", gate_scored),
        _score_system("confirmer", confirmer_scored),
        _score_system("always-decline", decline_scored),
        _score_system("always-answer", answer_scored),
    )
    human_baseline = None
    confirmer_caveat = CONFIRMER_CAVEAT
    if human_labels is not None:
        human_baseline = _score_human_labels(
            probe_set, outcomes, human_labels
        )
        confirmer_caveat = CONFIRMER_CAVEAT_WITH_HUMAN_LABELS
    return PublicBenchmarkReport(
        version=BENCHMARK_VERSION,
        probe_set_hash=actual_hash,
        preregistered_hash=preregistered_hash,
        preregistration_matched=(
            None if preregistered_hash is None else True
        ),
        caveat=CAVEAT,
        confirmer_caveat=confirmer_caveat,
        confirmer_agreement=agreement,
        total=len(probe_set),
        systems=systems,
        results=tuple(outcomes),
        failures=failures,
        human_baseline=human_baseline,
    )


# ---------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------


def _fmt_rate(rate: Rate) -> str:
    if rate.denominator == 0:
        return "0/0 (n=0, no score)"
    return (
        f"{rate.numerator}/{rate.denominator} "
        f"({rate.value:.0%}, 95% CI {rate.low:.0%}-{rate.high:.0%})"
    )


def _demo() -> None:
    report = run_benchmark(
        preregistered_hash=probe_set_hash(probes())
    )
    print(f"Provome public benchmark ({report.version}, fixture mode)")
    print("=" * 72)
    print(f"  probe-set hash (preregistered): {report.probe_set_hash}")
    print(f"  preregistration matched:        {report.preregistration_matched}")
    print(f"  probes:                         {report.total}")
    print()
    for system in report.systems:
        print(f"  [{system.name}]")
        for score in system.by_division + (system.overall,):
            print(
                f"    {score.division:8s} verdict "
                f"{_fmt_rate(score.verdict):34s} "
                f"answer {_fmt_rate(score.answer_rate):34s} "
                f"decline {_fmt_rate(score.decline_correctness):34s} "
                f"disclosure {_fmt_rate(score.disclosure)}"
            )
    print()
    print("  failure register (gate):")
    if report.failures:
        for failure in report.failures:
            print(
                f"    {failure.probe_id}: expected "
                f"{failure.expected}, got {failure.actual} "
                f"[{failure.cause_class}] {failure.detail}"
            )
    else:
        print("    [] (empty — no probe was scored wrong)")
    print(
        f"  gate/confirmer agreement:     "
        f"{_fmt_rate(report.confirmer_agreement)}"
    )
    if report.human_baseline is not None:
        human = report.human_baseline
        print()
        print(
            f"  [human baseline] {human.labeller} ({human.date}), "
            f"{human.labelled} of {human.total} probes labelled"
        )
        for score in human.by_division + (human.overall,):
            print(
                f"    {score.division:8s} labelled "
                f"{score.labelled:2d}  agreement with gate "
                f"{_fmt_rate(score.agreement_with_gate):34s} "
                f"agreement with expected "
                f"{_fmt_rate(score.agreement_with_expected)}"
            )
        print(f"  {human.caveat}")
    print(f"  RESULT: {'PASS' if report.passed else 'FAIL'}")
    print()
    print(f"Caveat: {report.caveat}")
    print(f"Confirmer caveat: {report.confirmer_caveat}")
    print(
        "Fixture mode replays recorded public payloads through\n"
        "the real providers and the real gate; no network. A\n"
        "perfect score here is a benchmark of gate behaviour on\n"
        "a fixed, builder-chosen probe set — not coverage, and\n"
        "not external validation. Nothing is published from this\n"
        "run until the Founder Witness Gate."
    )


if __name__ == "__main__":
    _demo()
