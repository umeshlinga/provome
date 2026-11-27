"""Provome: an evidence engine for biology, in three divisions.

One engine - sequence consequence, population frequency mathematics,
calibration, phenotype similarity, and a cite-or-decline evidence
gate - pointed at Human, Animal, and Plant problems.  The Human
division is live and benchmarked today; Animal and Plant are
expansion divisions on the same engine.

Modules:
    evidence:    evidence items, sources, and the cite-or-decline gate.
    copilot:     the Provome Copilot, the single question-answering
                 surface of the engine.
    roles:       the senior role registry of the Copilot
                 organisation (Chief Copilot, division leads, senior
                 scientists, researchers, analysts, engineers).
    teams:       the team registry and question router: separate
                 senior teams per disease and species area, plus
                 shared verification, calibration, and QC services.
    orchestra:   the Chief Copilot engine: worker registry,
                 assignment, cross-checking with escalation and
                 tiebreaks, the six-stage QC pipeline, and the audit
                 trail.  One Copilot outside, an organisation inside.
    domains:     the Human / Animal / Plant division registry.
    variant:     VCF records and HGVS parsing and validation.
    consequence: molecular consequence from real sequence.
    acmg:        ACMG/AMP Richards rules and Tavtigian points
                 (Human division, clinical context only).
    population:  maximum credible allele frequency (Whiffin et al.).
    calibration: AUROC, Brier, ECE, and calibration tables.
    phenotype:   ontology information content and Resnik similarity.

Research software.  Not for clinical use.
"""

from . import (
    acmg,
    agents,
    calibration,
    consequence,
    copilot,
    domains,
    evidence,
    orchestra,
    phenotype,
    population,
    roles,
    teams,
    variant,
)
from .copilot import (
    Copilot,
    CopilotAnswer,
    CopilotQuestion,
    ProvomeCopilot,
    OrchestratedCopilot,
)
from .domains import Division, get_division
from .evidence import EvidenceItem, evaluate_gate

__version__ = "0.22.0"

__all__ = [
    "acmg",
    "calibration",
    "consequence",
    "copilot",
    "domains",
    "evidence",
    "orchestra",
    "phenotype",
    "population",
    "roles",
    "teams",
    "variant",
    "Copilot",
    "ProvomeCopilot",
    "OrchestratedCopilot",
    "CopilotAnswer",
    "CopilotQuestion",
    "Division",
    "get_division",
    "EvidenceItem",
    "evaluate_gate",
    "__version__",
]
