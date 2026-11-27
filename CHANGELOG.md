# Changelog

All notable changes to Provome are recorded here. Versions follow
semantic versioning.

## [0.21.0] - 2026-11-27

Corpus-backed Copilot provider (LLM track Stage A remainder,
Item 30; the Item 53 corpus put to work). New module
`provome/corpus_provider.py` (+9 tests): a Copilot
EvidenceProvider that retrieves from the LOCAL Item 53 corpus
index on disk — the TF-IDF CSR matrix, vocabulary, and row
PMIDs at `~/workspace/provome-corpus/` — with no network at
query time and no new dependency: the `.npz` members are
extracted to a configurable scratch directory (~2.4 GB at
this scale) and memory-mapped, the `.npy` headers parsed
directly, and search runs over the CSR columns the query's
terms touch. Records are fetched by streaming scan of
`corpus.jsonl.gz` (a record's row is its line number).
Every retrieved record is re-checked at answer time against
the corpus's own verified standard — the Item 53 ingest
rule applied again: real PMID present, non-empty abstract
present, and the record's PMID matching its index row. A
record failing any check is dropped and counted on the
provider (`dropped_records`); division filtering by the
records' primary-division tags is counted separately
(`out_of_division_records`). Europe PMC is registered in
the source registry as exactly one independence group, and
every corpus item carries it as its source. The
cite-or-decline gate is UNCHANGED and is not weakened for
this provider. Tests run against a small synthetic fixture
corpus built in the test file in the exact on-disk layout
(never the real corpus): retrieval finds the planted
records, the planted no-abstract and no-PMID records are
dropped and counted (2), a Human provider never serves
Plant records, and the gate declines corpus-only evidence
on the group rule with three verified items in hand.

Real smoke run (outside the test suite) against the actual
2,226,953-record corpus, three questions from the Item 53
evaluation set, one per division, through
`Copilot(provider=CorpusProvider(...))`. Output verbatim:

    [DECLINE] division: Human
    Q: CFTR Phe508del cystic fibrosis transmembrane conductance regulator
    Topic: corpus:cftr phe508del cystic fibrosis transmembrane conductance regulator
    Declined. Reasons:
    - verified supporting items come from 1 independent source group(s); the gate requires at least 2
    [provider: dropped_records=0 out_of_division_records=11 elapsed=97.2s]

    [DECLINE] division: Animal
    Q: ABCB1 MDR1 mutation dog ivermectin sensitivity collie
    Topic: corpus:abcb1 mdr1 mutation dog ivermectin sensitivity collie
    Declined. Reasons:
    - verified supporting items come from 1 independent source group(s); the gate requires at least 2
    [provider: dropped_records=0 out_of_division_records=173 elapsed=84.2s]

    [DECLINE] division: Plant
    Q: FLC Arabidopsis flowering locus vernalization repression
    Topic: corpus:flc arabidopsis flowering locus vernalization repression
    Declined. Reasons:
    - verified supporting items come from 1 independent source group(s); the gate requires at least 2
    [provider: dropped_records=0 out_of_division_records=12 elapsed=78.9s]

Honest limits, stated plainly. (1) All three questions
DECLINED, each on the single group rule and no other: in
every case at least three verified items were retrieved, so
the declines are the Item 53 structural result reproduced
through the Copilot — single-source evidence (Europe PMC
alone = 1 independent group) cannot pass a two-group gate.
The known next gap is a second independent source group
(ClinVar/gnomAD cross-supply); it is future work and was
not built here, and the gate was not weakened to hide it.
(2) VERIFIED on corpus items means the corpus standard —
PMID + abstract required at ingest, re-checked at read —
not a live re-confirmation; this provider makes no network
calls and does not repeat the pipeline evaluation's live
PubMed check per query. (3) Query terms are weighted
uniformly (per-term document frequencies are not stored in
the index artefacts); document vectors carry the corpus
idf. (4) Latency is ~80-97 s per question in pure Python
(column scans plus a streaming record fetch) — the Item 53
evaluation's 0.348 s mean was numpy machinery over the same
index, not this provider; an offset index for record lookup
is future work. Suite 510.

## [0.20.0] - 2026-11-27

Human-labelling kit for the public benchmark (Item 51
blind-human-labels standard; the Item 57 road's named
remainder). New module `provome/labelling.py` (+18 tests).
Blind export: `build_labelling_pack` exports all 49 frozen
probes as a labelling pack — per probe ONLY the id, division,
question, and the evidence items the scored run's own
providers serve for it. Expected verdicts, ground-truth
notes, and checking sources are never read into the pack, so
they cannot leak into it; a test scans both exported forms
(JSONL and the Markdown sheet) for every probe's ground
truth and source URL and asserts their absence, and asserts
each record carries only the blind fields. The probe-set
hash is recorded in the pack header, and the Markdown
sheet's header is the labeller instructions: what the three
labels (answer / decline / disclose) mean, judge the shown
evidence not outside knowledge, ~one sitting for 49 probes.
Ingest + scoring: a labels file (labeller identity, date,
probe-set hash, probe-id → label map) parses into
`HumanLabels` and is scored by `run_benchmark(...,
human_labels=...)`, which refuses loudly — before any probe
runs — when the labels' hash does not match the frozen set
or a label names a probe the set does not contain. The
report gains an OPTIONAL `human_baseline` section: absent
when no labels are supplied (the v2 confirmer caveat stands
unchanged), present when they are, carrying
human-vs-gate and human-vs-expected agreement as Rate
fractions with denominators and Wilson intervals, per
division and overall. Partial label sets are scored on the
labelled subset only, the labelled denominator stated, an
unlabelled division reporting n=0 with no rate; the section
names the labeller and the labelled count ("N of 49"), and
the confirmer caveat switches to a text that keeps real
human labels distinct from the confirmer stand-in. No
labels ship with this release and none were fabricated:
the only labels exercised anywhere are synthetic fixtures
inside the test file, marked as such in their labeller
string. The first real baseline waits for a named person
labelling blind from this pack. Suite 501.

## [0.19.0] - 2026-11-27

Public benchmark v2 (roadmap Item 57, remainder; Item 51
confirmer-calibration standard). The frozen probe set grows
from 17 to 49 probes (Human 13, Animal 18, Plant 18; 20
answer, 25 decline, 4 disclose), preregistered under a new
hash (b44d2ee6e3fafdeca42c0d401ca92f1b561162685fae2d1290
e15f389543bc13). New Human probes are grounded in variants
already used in this repo: FGFR3 p.Gly380Arg (the bundled
worked examples' achondroplasia variant; ClinVar, OMIM
#100800) as an answer probe, an HBB p.Glu6Val disagreement
probe, and fabricated-variant, fabricated-gene (FAKGENE1),
and wrong-division traps in the existing families. New
Animal and Plant probes are selected by name from those
divisions' own frozen benchmarks, so their ground truth
stays single-sourced. Every division now carries disclosure
probes: Animal (sheep PRNP / Nor98 atypical scrapie,
Benestad et al. 2003) and Plant (rice XA21 developmental
regulation, Century et al. 1999) reach theirs through a
disclosure fixture wrapper that adds the one published
refuting record to the division provider's own recorded
items — necessary because the divisions' live providers
emit supporting items only, and stated here rather than
hidden. Scores as measured: gate verdict 49/49 overall
(Human 13/13, Animal 18/18, Plant 18/18; 95% CI 93-100%),
answer 20/20, decline 25/25, disclosure 4/4 (Human 2/2,
Animal 1/1, Plant 1/1 — no division reports disclosure
0/0 anymore). New confirmer calibration baseline: an
independent re-derivation of the gate rule (its own
counting code; never calls the gate, never touches a
Copilot) scored in the same `systems` tuple — confirmer
49/49, gate/confirmer agreement 49/49 (95% CI 93-100%).
Trivial-baseline floor: always-decline 25/49, always-answer
20/49. Failure register: explicitly empty. Honest limits,
stated in the release: the confirmer implements the same
written rule as the gate, so agreement measures
implementation consistency — it is NOT a human baseline,
no human labels exist for this probe set yet, and every
report now carries that caveat verbatim alongside the v1
caveat; the probe set remains fixed and builder-chosen;
the Human fixture remains a small hand-built provider over
published facts. A test proves the confirmer can disagree
with the gate (a duplicated evidence line the gate
collapses and the confirmer counts), so the calibration
check is a check, not a rubber stamp. Nothing is
published from this run until the Founder Witness Gate.
Suite 483.

## [0.18.0] - 2026-11-27

Public benchmark v1 (roadmap Item 57; Item 38 in buildable
form, Item 51 standards). `provome/public_benchmark.py`: 17
frozen probes across the three divisions (Human 7, Animal 5,
Plant 5; 6 answer, 10 decline, 1 disclose), built from public
records only - published ClinVar variants already used in this
repo's live demos (CFTR p.Phe508del, HBB p.Glu6Val), OMIM/OMIA
entries, and the divisions' own recorded Ensembl/Europe PMC
fixtures; no patient data, no network in the scored run.
Preregistration: the probe set is hashed (canonical JSON,
SHA-256) before the run, the hash is recorded in the report,
and a run handed a mismatched preregistered hash refuses.
Every score is an x/n fraction with a Wilson 95% interval -
no bare percentages anywhere in the report structure.
Trivial-baseline floor: always-decline scores 10/17 and
always-answer 6/17 (both 0/1 on disclosure) in the same
`systems` tuple as the gate, so the gate's score cannot be
quoted without the floor beside it. Failure register: every
miss is a record with expected vs actual and a cause class
from a fixed five-class taxonomy; the gate's register this
run is explicitly empty. Scores as measured: gate verdict
17/17 overall (Human 7/7, Animal 5/5, Plant 5/5), answer
6/6, decline 10/10, disclosure 1/1 (Human only; Animal and
Plant carry no disclose probes and report 0/0, no score -
a division with zero probes is never given an invented
number). Honest limits, stated in the release: the probe set
is fixed and builder-chosen, the Human fixture is a small
hand-built provider over published facts, and the report
carries the caveat verbatim that fixture/public-probe
results are a benchmark of gate behaviour, not external
clinical validation. Nothing is published from this run
until the Founder Witness Gate. Suite 478.

## [0.17.0] - 2026-11-25

Three-doors packaging v1 (roadmap Item 31 in installable
form). `provome/doors.py`: thin wiring only - a test greps
the module to prove no gate logic lives there. Three console
scripts, `provome-human`, `provome-animal`, `provome-plant`,
each wiring only its own division's copilot, boundary, and
three benchmark-verified examples; every output framed as
its door, and cross-division questions decline through the
door layer in both directions (suite-tested, clean-install
verified). Honest limit, stated in the release: the doors
ship in one wheel by design (one engine); the per-door
extras are name markers today, not separate distributions,
and packaging a door is not a coverage claim - Animal and
Plant boundaries still say coverage is NOT claimed.
Suite 361.

## [0.16.0] - 2026-11-25

Plant benchmark v1 + two provider fixes it forced.
`provome/plant_benchmark.py`: 21 frozen probes (8 ANSWER,
13 DECLINE), same audited harness as Animal; fixture score
21/21 - a smoke benchmark, NOT external validation; coverage
wording unchanged. The set keeps its gaps visible on purpose:
rice SD1 and maize TB1 (real genes the Ensembl Plants symbol
index cannot resolve), Arabidopsis FRI (record exists,
literature window too thin), trait topics (decline by
design). FIXES in `provome/plant.py`: (1) the gene-symbol
pattern was all-uppercase-only, making real symbols like
Rht-B1, GmFT2a and tb1 unaskable - widened with a gene-shape
predicate, regression-tested both ways (live check: Rht-B1
now parses and reaches the gate, declining honestly on the
index limit); (2) the phenotype parser read the same nested
payload shape that broke Animal - corrected defensively to
the real top-level shape (Ensembl Plants serves no
associations today, so the path is unproven live for Plant
and contributes zero items either way). Every Plant answer
still sits exactly at the gate minimum - zero margin is the
division's known weakness, recorded, not hidden. Suite 339.

## [0.15.0] - 2026-11-25

Animal benchmark v1 + a production bug it caught and we
fixed the same day. `provome/animal_benchmark.py`: 21 frozen
probes with recorded ground truth (11 ANSWER cases from real
OMIA associations, 10 DECLINE traps including fabricated
genes, wrong-species genes, and one real association v1
honestly cannot reach); the harness re-audits the gate
arithmetic per probe, and a rigged always-answer provider
fails it. Fixture score 21/21 - a smoke benchmark, NOT
external validation; coverage wording unchanged. THE BUG:
Ensembl phenotype items never fired in production - the
parser read a nested payload shape that existed only in an
invented test fixture, while the real API serves the
description at the entry's top level with a plain-string
source. Every real OMIA phenotype association was silently
dropped. Fixed in `provome/animal.py`, the fixture corrected
to the recorded real shape, a verbatim-payload regression
test added, and the fix live-verified (equine PRKDC now
returns its SCID phenotype association). Suite 328.

## [0.14.0] - 2026-11-25

Plant division v1 + the flagship's gnomAD fix (Step 3b).
`provome/plant.py`: Plant provider (Ensembl Plants + species-
restricted literature) mirroring Animal, division-scoped to
Plant, agricultural RUO boundary; live-verified ANSWER on
Arabidopsis FLC and an honest DECLINE on rice SD1 (no symbol
record exists - the gate does not pad). Step 3b: the live Human
provider now resolves ref/alt alleles through dbSNP (ClinVar
xref -> NCBI refsnp GRCh38 SPDI -> left-normalisation against
the reference), so the gnomAD item fires generally - live-
verified CFTR p.Phe508del (AF 0.00788) and HBB p.Glu6Val
(AF 0.01272); ambiguous multi-allelic cases still contribute
nothing rather than guess. Also fixed: a nondeterministic
ClinVar notation search, and per-source failure isolation in
the live provider (one flaky API costs only its own items,
never the whole answer - regression-tested). Plant and Animal
remain in build and unbenchmarked; coverage is not claimed.
Suite 319. (Released as 0.14.0: the 0.9.0-0.13.0 numbers were
taken by the parallel voice-session release line.)

## [0.13.0] - 2026-11-25

Gate benchmark, internal v1 (priority item #1 under the
Founder's one-by-one order): `provome/benchmark.py` measures the
cite-or-decline gate's behaviour on 10 fixed probes - must-answer,
must-decline (too few verified, one source group, unverified
only, wrong topic, refuting only, empty), and must-disclose
(passes but surfaces verified refutation instead of hiding it).
Result at ship: 10/10 correct, zero disclosure failures. Fixture
probes test gate behaviour, not biomedical facts; the public
benchmark replays this harness on public data after Quality
Division and Founder sign-off. Suite 295.

## [0.12.1] - 2026-11-25

Fix: the Quality Division honesty test constructed WorkTask
without its required fields; corrected. No behaviour change.

## [0.12.0] - 2026-11-25

Quality Division built in code (Founder's direction: quality
covers the ENTIRE company, every department). `provome/quality.py`
registers 2,000 independent checkers across all 18 departments -
orchestra, research division, the three doors, drug discovery,
bridge, and all 11 disease corps - each with a distinct
(department, method, beat) assignment; every department is
covered by all 12 checking methods, so any important claim can be
verified by two different methods (no-identical-checks law). One
Chief Quality Copilot, a senior QA lead per department, and the
stop-the-line veto structure. A checker with no live dispatched
check passes nothing. Suite 293.

## [0.11.0] - 2026-11-25

Diabetes stands as its own corps at 20,000 researchers
(Founder's order). It began as focus areas inside the Metabolic
corps; it now has its own registry, focuses (type 1, type 2,
gestational, insulin resistance, complications, prevention,
paediatric, monitoring technology) and 200 senior leads.
Disease corps total: 70,000 across 11 corps. Suite 289.

## [0.10.0] - 2026-11-25

Disease corps (Founder's direction): `provome/diseases.py` gives
every major disease area its own corps of 5,000 researchers with
distinct (focus, function, beat) assignments. First wave deployed
and verified: 10 corps, 50,000 researchers - Human: oncology,
cardiovascular, infectious diseases (COVID-19 a named focus),
neurological, rare diseases, metabolic; Animal: infectious,
genetic; Plant: pathology, crop resistance. Cancer/oncology/
tumour form ONE Oncology corps (same field; the company's
no-duplicate law). Corps stand up per disease through the stage
gates via the generic builder. Suite 289.

## [0.9.0] - 2026-11-25

Research Division deployed (Founder's direction): `provome/research.py`
registers 5,000 scientific researcher units in the orchestra -
Human 2,000, Animal 1,000, Plant 1,000, Drug Discovery 600,
cross-division Bridge 400 - each with a unique (specialty, beat)
assignment across 50 specialties, one senior research lead per
hundred. Units are honest by construction: asked to run without a
live dispatched assignment, a unit reports exactly that and
produces no claims. Registration is the organisation standing up,
not a claim of completed research. Suite 286.

## [0.8.0] - 2026-11-25

LLM track Stage A + Animal division v1 + a division-scoping fix.
`provome/rag.py`: retrieval index with a pluggable embedder
protocol (stdlib TF-IDF baseline built in; neural embedders plug
in without caller changes) and an evidence bridge whose rule is
that similarity ranks but never verifies - only documents with a
real PMID become verified evidence. `provome/animal.py`: Animal
division provider (Ensembl REST + species-restricted Europe PMC
literature) under the unchanged cite-or-decline gate, veterinary
RUO boundary on every output; live-verified on canine ABCB1
(ANSWER, 3 items / 2 sources). Division scoping fix (CEO):
providers now declare `scoped_divisions`; the Copilot declines
out-of-scope divisions per provider instead of either leaking
human evidence into Animal answers or blocking genuinely
animal-scoped evidence. Animal remains in build and
unbenchmarked - coverage is not claimed. Suite 281.

## [0.7.0] - 2026-11-25

Step 6: freemium tiers as real product structure. Built by two
specialist workers, CEO-verified. `provome/tiers.py`: Free $0 /
Researcher $79 / Lab $499 entitlements (daily live questions,
batch size, audit-trail access, seats, priority support) with
denials that name the limit and the unlocking tier.
`provome/usage.py`: per-account daily metering with JSON
persistence and honest quota decisions (limit reached -> message
names the next-day reset; nothing silently dropped). Checkout
and billing remain Step 8 and are not claimed. 17 new tests;
suite 260.

## [0.6.1] - 2026-11-25

Copilot coverage fact sheet updated for Step 5: Drug Discovery
target evidence is live (computational only); Animal and Plant
remain honestly not covered.

## [0.6.0] - 2026-11-25

Step 5: the Drug Discovery division, v1 (`provome/drug.py`).
Target evidence under the same cite-or-decline gate: live ChEMBL
targets/mechanisms (joined to genes via reviewed UniProt
accessions), approved and phase 2-3 compounds, and title-matched
Europe PMC literature. Every answer prints the computational-only
boundary. Verified live: CFTR (ivacaftor, lumacaftor, tezacaftor)
and BCL2 (venetoclax) ANSWER; unknown gene DECLINEs. ChEMBL added
to the source registry. 4 new tests; suite 243.

## [0.5.0] - 2026-11-25

The rename release: Omevra becomes **Provome** everywhere —
package (`provome`), repository, documentation, and website —
executing the Founder's 2026-11-25 decision (name-risk finding:
Omivera biotech + OMEVRA-DM pharma brand; see the roadmap's
NAME_OPTIONS_v1.md). All 239 tests pass under the new name.

### Added
- The Provome website (this repo's index.html): how it works, a
  real live-run proof section, honestly-labelled divisions, the
  freemium pricing shape, Research Use Only footer.

## [0.4.1] - 2026-11-25

Full audit of Steps 1-4 (three senior audit workers + CEO review).
Fixes only; no new features.

- `__init__.__version__` had remained "0.2.0" through three
  releases; synced to the real version, with a new
  version-consistency test so the drift fails loudly next time.
- README "Honest limits" still claimed the package ships no live
  database access - stale since v0.3.0. Rewritten to describe both
  providers accurately; architecture list now includes live.py and
  converse.py; worker-scale note corrected (10,000-worker stress
  test, not 1,000).
- Guide SIMPLE depth now prints the key source it refers to.

## [0.4.0] - 2026-11-25

Step 4: the human Copilot (Items 21 + 23). `provome.converse.Guide`
wraps the gated Copilot in a conversational layer at three depths
(simple / standard / expert). It adds no claims: every factual
sentence comes from a finished gated answer; declines stay declines
and are explained with the exact missing piece and the question
shape that unblocks them. Tool questions ("What is Provome?", "How
do you work?", pricing, coverage, sources) are answered from a
static product fact sheet with honest coverage limits. Every guided
answer carries run transparency (provider, gate arithmetic,
confidence-is-a-heuristic note) and limits. 7 new tests; suite 237.

## [0.3.0] - 2026-11-25

Step 3: the live brain. The Copilot gains its own browser
(`provome.live.LiveEvidenceProvider`, Item 22): at question time it
fetches real records from ClinVar (NCBI E-utilities), gnomAD (Broad
GraphQL, when the ClinVar record supplies usable coordinates), and
Europe PMC literature, and answers only from what actually came
back — every citation carries its record id and fetch date.

### Added
- Intent validation: a topic is recognised only when the question
  names a gene AND an HGVS-like variant; everything else declines
  (closes the Step 1 Worker A residual finding for the live path).
- ClinVar record verification: loose search hits are filtered to
  the exact variant (title / protein-change match), multi-variant
  haplotype records are rejected, and legacy protein numbering is
  reconciled (literature HBB p.Glu6Val = ClinVar p.Glu7Val), with
  both three-letter and one-letter spellings searched.
- Live demos verified end-to-end: CFTR p.Phe508del ANSWER from the
  real ClinVar record (Pathogenic, Variation ID 4072070) plus live
  literature; HBB p.Glu6Val ANSWER (Variation ID 15333); weather
  question DECLINE.
- 6 fixture-based tests (no network in CI). Suite: 230 passed.

### Known limitation
- ClinVar's summary feed does not expose ref/alt alleles, so the
  gnomAD item fires only when coordinates are otherwise available;
  an allele-resolution source (dbSNP cross-reference) is Step 3b.

## [0.2.1] - 2026-11-25

Step 1 hardening round. Three senior test workers ran adversarial,
fuzz, and stress suites against v0.2.0 (61 gate cases, 18,706 fuzz
calls, 17 orchestra stress/scale checks incl. a 10,000-worker
registration test); the CEO reviewed every finding, fixed what was
real, and re-ran all suites.

### Fixed
- Gate integrity: unregistered (fabricated) sources no longer count
  toward the cite-or-decline gate; only registry sources can vouch.
- Cross-division leakage: human clinical evidence is never issued
  as an answer in the Animal or Plant divisions; the Copilot
  declines there until division-scoped evidence is benchmarked.
- Evidence counting: one citation restated in other words is one
  evidence line (citation-identity deduplication), and verified
  refuting evidence that matches or outweighs supporting evidence
  now blocks an answer instead of only warning.
- Topic matching uses word boundaries (an alias inside a larger
  token no longer matches).
- Validation: Claim rejects empty citation strings, EvidenceItem
  rejects whitespace ids, Copilot.ask raises a clean TypeError on
  non-string input.
- Wrong-type arguments across variant/consequence/population/
  calibration now raise the documented ValueError/VariantParseError
  instead of leaking AttributeError/TypeError (12 fuzz patterns);
  calibration_table caps n_bins at 10,000 (resource guard).

### Added
- tests/test_hardening_step1.py: 12 regression tests. Suite is now
  224 tests. Worker suites after fixes: gate 58/61 (remaining 3 are
  the documented demo-provider intent limitation, not carried into
  the live provider), fuzz 0 failures, orchestra stress 17/17.

### Known, scheduled
- A permanently-failing worker is excluded per run but re-assigned
  on the next run (no cross-run failure history). Correctness is
  unaffected; registry deprioritisation is scheduled in Item 9b.
semantic versioning.

## [0.2.0] - 2026-11-24

### Added
- One Copilot, an organisation inside: senior role registry
  (provome/roles.py) where every role is senior by construction, from
  Chief Copilot to division leads and senior specialists.
- Team registry (provome/teams.py): 14 teams including Cancer, Tumor
  Biology, Rare Disease, DNA and Variant, Animal Health, Livestock,
  Plant Crops and Plant Pathology, each with a senior lead and
  declared problem areas, plus a router with a named fallback team
  per division.
- Orchestration engine (provome/orchestra.py): the Chief Copilot
  routes each question to a team, assigns primary and cross-check
  workers, requires agreement from at least 2 independent workers
  per key claim, escalates disagreements to the team lead with a
  tiebreak worker, and excludes failed or erroring workers with the
  reason recorded.
- Six-stage QC pipeline: evidence gate, citation check, calculation
  check, consistency check, cross-worker agreement, senior review.
  The final answer is produced only when all six pass; otherwise the
  Copilot declines naming every failed stage.
- Full audit trail for every assignment, result, disagreement,
  escalation, exclusion and sign-off.
- Test suite grows to 212 tests, including a 1,000-worker scale
  test and edge cases (empty registry, worker exceptions) that must
  decline cleanly rather than crash.

## [0.1.0] - 2026-11-24

### Added
- Provome Copilot core with the cite-or-decline evidence gate: an
  answer requires at least 3 verified evidence items from at least 2
  independent source groups, otherwise the Copilot declines with
  named reasons.
- Evidence engine: EvidenceItem model, source registry with
  independence groups, fingerprint deduplication, gate evaluation.
- Three divisions on one engine (provome/domains.py): Human (live,
  benchmarked), Animal and Plant (expansion). ACMG marked
  human-clinical only and refused for Animal and Plant questions.
- ACMG/AMP classification: Richards 2015 combining rules and the
  Tavtigian Bayesian points system, with framework disagreements
  surfaced instead of hidden.
- Population genetics: Whiffin maximum credible allele frequency,
  BA1/BS1/PM2-style threshold evaluation.
- Variant and consequence science: VCF and HGVS c./p. parsing, full
  standard codon table, molecular consequence calling.
- Calibration science from scratch: AUROC, Brier score, log loss,
  ECE/MCE and calibration tables.
- Phenotype semantic similarity: information content, MICA, Resnik
  similarity on a labelled illustrative ontology subset.
- Test suite: 167 tests covering every module.
- CI: GitHub Actions runs the suite and the Copilot demo smoke test
  on Python 3.10, 3.11 and 3.12 for every push and pull request.
