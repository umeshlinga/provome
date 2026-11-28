# Licence Register — Provome

> **This register is bookkeeping, not legal advice.** It records what
> the company currently believes about the terms of each model and
> data source it uses or plans to use, and where that belief came
> from. **Every entry must be verified with counsel before the
> company charges money** on any product that touches that source.
> An entry marked UNVERIFIED means exactly that: no source in the
> workspace or on the provider's own pages has been checked yet, and
> the source must be treated as unusable commercially until it is.

Status values: `free for commercial use` / `research-only` /
`licence required` / `UNVERIFIED`.

| Source | What we use it for | Commercial terms status | Where the status came from |
|---|---|---|---|
| ClinVar (NCBI) | Variant clinical assertions; evidence items and confirmation lookups via E-utilities | free for commercial use | Provome internal data licence register v1 (`~/workspace/provome-roadmap/DATA_LICENCE_REGISTER_v1.md`, 2026-11-25): "Public domain / freely available, NCBI terms" — that register itself flags re-verification at integration time |
| gnomAD (Broad Institute) | Population allele frequencies | free for commercial use | Internal register v1: "Freely available for research and commercial use per gnomAD terms; cite the release" — flagged for re-verification at integration time |
| PubMed (NLM/NCBI) | Literature evidence and independent re-confirmation (E-utilities esummary) | free for commercial use | Internal register v1: "NLM terms; PMC OA subset has per-article licences" — metadata/abstracts are the free part; PMC full text is per-article and must be checked article by article |
| Europe PMC (EMBL-EBI) | Literature search feed for the live providers | UNVERIFIED | No workspace source records Europe PMC's terms; the articles it returns carry their own licences, and the API terms have not been checked |
| AlphaMissense (DeepMind) | Precomputed missense pathogenicity scores, cited as evidence | free for commercial use | DeepMind's own AlphaMissense page, confirmed 2026-11-25: the predictions are free for commercial use. The exact licence file on the exact copy used must still be checked before bundling |
| REVEL | Ensemble missense pathogenicity scores | UNVERIFIED | Still unchecked as of 2026-11-25; no terms source has been read |
| CADD | Variant deleteriousness scores | UNVERIFIED | No workspace source verifies CADD's terms; known in the field to distinguish academic from commercial use, but that has not been confirmed from CADD's own pages |
| SpliceAI (Illumina) | Splicing predictions | UNVERIFIED | No workspace source verifies the terms; the code and the precomputed score files may carry different terms, and neither has been checked |
| AlphaGenome (DeepMind) | Regulatory/splicing effect predictions via API | research-only | The AlphaGenome API has been offered for non-commercial research (reviewed 2026-11-25); commercial terms were not available, so no paid feature may be built on it until they are |
| OMIM (Johns Hopkins) | Gene–phenotype relationships; evidence items | licence required | Internal register v1, FLAGGED: "Licence REQUIRED, esp. commercial/API use (Johns Hopkins). Do NOT integrate commercially on the public website data alone" |
| HGMD (QIAGEN) | Variant–disease database | licence required | Internal register v1, FLAGGED: "Commercial licence, paid. Public version is academic-only and stale" — licence it or exclude it |
| COSMIC (Wellcome Sanger Institute) | Somatic mutation catalogue | licence required | Internal register v1, FLAGGED: "Free for academic; commercial use needs a licence" |
| MedCPT (NCBI) | PubMed-trained retriever for literature search (planned) | UNVERIFIED | The model's licence on its distribution page has not been checked |
| GLiNER | Zero-shot entity extraction from literature text (planned) | UNVERIFIED | The model's licence on its distribution page has not been checked |
| Claude API (Anthropic) | LLM extraction/synthesis inside the Copilot, behind the gate; coding agents in the workshop | licence required | Paid commercial API: use is governed by Anthropic's commercial terms and pricing (reviewed 2026-11-25 in the workspace's Claude/Anthropic research review). Consumer subscription plans carry no API access; a Console agreement is the licence |
| Open Targets Platform | Gene–disease association evidence items (`provome/source_providers.py`, registered v0.23.0) | free for commercial use | Open Targets Platform licence page (platform-docs.opentargets.org/licence), read 2026-11-27: "Open Targets Platform is marked with CC0 1.0 … allowing downstream users to consume the data without restriction"; platform codebases are Apache 2.0. Live-verified the same day (GraphQL, no key) |
| European Nucleotide Archive — ENA (EMBL-EBI) | Sequence-archive record items (`provome/source_providers.py`, registered v0.23.0) | free for commercial use | EMBL-EBI Terms of Use (ebi.ac.uk/about/terms-of-use, revised 2026-10-14), read 2026-11-27: EMBL-EBI provides freely available data resources and "imposes no additional restriction on the use of the contributed data than those provided by the data owner"; attribution expected per good scientific practice. Live-verified the same day (Portal API, no key) |
| RCSB Protein Data Bank (wwPDB) | Structure record items (`provome/source_providers.py`, registered v0.23.0) | free for commercial use | RCSB PDB Usage Policies (rcsb.org/pages/usage-policy), read 2026-11-27: "data files contained in the PDB archive are available under the CC0 1.0 Universal (CC0 1.0) Public Domain Dedication. All data provided by RCSB PDB programmatic APIs are available under the same license." Live-verified the same day (Data API, no key) |
| KEGG (Kanehisa Laboratories) | NOT integrated — pathway/gene reference was a candidate only | licence required | KEGG Copyright and Disclaimer (kegg.jp/kegg/legal.html, updated 2024-10-01), read 2026-11-27: "Non-academic use of KEGG requires a commercial license" (via Pathway Solutions). The REST API answers live, but Provome is a commercial venture: HELD, not registered, until a licence exists |
| MyGene.info (BioThings) | NOT integrated — gene ID resolution was a candidate only | UNVERIFIED | API live-verified 2026-11-27 (v3 query API, no key), but no explicit licence statement was found on the service's own documentation pages checked that day. Separately, it is an aggregator of other databases' annotations, so it is not registrable as an independent evidence source regardless of licence |

## Standing rules

1. Nothing ships commercially on an UNVERIFIED or research-only
   entry. UNVERIFIED defaults to excluded until checked.
2. Statuses sourced from the internal register v1 inherit its own
   caveat: they are recorded knowledge, flagged there for
   re-verification at integration time, and this register does not
   upgrade them beyond that.
3. When an entry is verified against the provider's own terms page,
   update the row with the page and the date it was read.
