"""Identity regression tests for the v0.26.0 corpus latency work.

The optimisation (single-pass column scan, extraction reuse,
offset-index record fetch, PMID row map) must not change ANY
retrieval result.  Three independent pins:

* GOLDEN: the exact outputs captured from the pre-fix code
  (v0.25.0) on the shared fixture corpus — ranked rows with
  full-precision scores, served items field for field, provider
  counters, and the Copilot's verdict — reproduced by both the
  streaming path (no ``scratch_dir``) and the persistent-cache
  path (``scratch_dir`` given, cold and warm).
* REFERENCE: a slow, obviously-correct dense computation of
  the fixture search, written from the CSR arrays directly,
  compared float-for-float with ``_CorpusIndex.search``.
* OVERLAP: a hand-crafted indices payload whose byte stream
  hides an aligned occurrence inside an unaligned match's
  span — the case the single-pass scan's patch-up exists for.
  Without the patch-up this test's expected hit is missed.
"""

import ast
import gzip
import json
import math
import struct
import zipfile
from array import array

import pytest

from test_corpus_provider import _FIXTURE_RECORDS, _npy_bytes, _write_fixture_corpus

from provome.copilot import Copilot, CopilotQuestion
from provome.corpus_provider import CorpusProvider, _CorpusIndex
from provome.domains import Division

GOLDEN = json.loads(r'''{
 "abstract_91000006": "The rice XA21 receptor kinase recognises a bacterial blight signal and confers resistance to Xanthomonas oryzae in rice.",
 "abstract_missing": "",
 "human|CFTR Phe508del corrector folding trafficking cystic fibrosis": {
  "claims": [],
  "counters": [
   2,
   0,
   0
  ],
  "declined_reasons": [
   "verified supporting items come from 1 independent source group(s); the gate requires at least 2"
  ],
  "independent_source_count": 1,
  "items": [
   [
    "corpus-pmid-91000002",
    "Corrector therapy for CFTR Phe508del cystic fibrosis",
    "PMID 91000002. Corrector therapy for CFTR Phe508del cystic fibrosis. Fixture Journal, 2021.",
    "supports",
    "verified",
    "https://europepmc.org/article/MED/91000002",
    "retrieved from the local Europe PMC corpus index (Item 53 verified corpus) with TF-IDF score 0.707; PMID, abstract, and index alignment re-checked at read time under the corpus standard \u2014 not a live re-confirmation. Direction (supports) derived from the record's own title and abstract text. Europe PMC is one independence group, so this evidence alone cannot satisfy the gate's two-group rule"
   ],
   [
    "corpus-pmid-91000001",
    "CFTR Phe508del impairs folding in cystic fibrosis",
    "PMID 91000001. CFTR Phe508del impairs folding in cystic fibrosis. Fixture Journal, 2020.",
    "supports",
    "verified",
    "https://europepmc.org/article/MED/91000001",
    "retrieved from the local Europe PMC corpus index (Item 53 verified corpus) with TF-IDF score 0.393; PMID, abstract, and index alignment re-checked at read time under the corpus standard \u2014 not a live re-confirmation. Direction (supports) derived from the record's own title and abstract text. Europe PMC is one independence group, so this evidence alone cannot satisfy the gate's two-group rule"
   ],
   [
    "corpus-pmid-91000003",
    "Genotype and phenotype for CFTR Phe508del",
    "PMID 91000003. Genotype and phenotype for CFTR Phe508del. Fixture Journal, 2019.",
    "supports",
    "verified",
    "https://europepmc.org/article/MED/91000003",
    "retrieved from the local Europe PMC corpus index (Item 53 verified corpus) with TF-IDF score 0.295; PMID, abstract, and index alignment re-checked at read time under the corpus standard \u2014 not a live re-confirmation. Direction (supports) derived from the record's own title and abstract text. Europe PMC is one independence group, so this evidence alone cannot satisfy the gate's two-group rule"
   ]
  ],
  "ranked": [
   [
    1,
    "0.7066497560445902"
   ],
   [
    4,
    "0.47785444135077226"
   ],
   [
    3,
    "0.45859208604878166"
   ],
   [
    0,
    "0.39330596203901386"
   ],
   [
    2,
    "0.2951890324689724"
   ]
  ],
  "topic": "corpus:cftr phe508del corrector folding trafficking cystic fibrosis",
  "verdict": "decline",
  "verified_item_count": 3
 },
 "human|CFTR Phe508del cystic fibrosis": {
  "claims": [],
  "counters": [
   2,
   0,
   0
  ],
  "declined_reasons": [
   "verified supporting items come from 1 independent source group(s); the gate requires at least 2"
  ],
  "independent_source_count": 1,
  "items": [
   [
    "corpus-pmid-91000002",
    "Corrector therapy for CFTR Phe508del cystic fibrosis",
    "PMID 91000002. Corrector therapy for CFTR Phe508del cystic fibrosis. Fixture Journal, 2021.",
    "supports",
    "verified",
    "https://europepmc.org/article/MED/91000002",
    "retrieved from the local Europe PMC corpus index (Item 53 verified corpus) with TF-IDF score 0.495; PMID, abstract, and index alignment re-checked at read time under the corpus standard \u2014 not a live re-confirmation. Direction (supports) derived from the record's own title and abstract text. Europe PMC is one independence group, so this evidence alone cannot satisfy the gate's two-group rule"
   ],
   [
    "corpus-pmid-91000001",
    "CFTR Phe508del impairs folding in cystic fibrosis",
    "PMID 91000001. CFTR Phe508del impairs folding in cystic fibrosis. Fixture Journal, 2020.",
    "supports",
    "verified",
    "https://europepmc.org/article/MED/91000001",
    "retrieved from the local Europe PMC corpus index (Item 53 verified corpus) with TF-IDF score 0.436; PMID, abstract, and index alignment re-checked at read time under the corpus standard \u2014 not a live re-confirmation. Direction (supports) derived from the record's own title and abstract text. Europe PMC is one independence group, so this evidence alone cannot satisfy the gate's two-group rule"
   ],
   [
    "corpus-pmid-91000003",
    "Genotype and phenotype for CFTR Phe508del",
    "PMID 91000003. Genotype and phenotype for CFTR Phe508del. Fixture Journal, 2019.",
    "supports",
    "verified",
    "https://europepmc.org/article/MED/91000003",
    "retrieved from the local Europe PMC corpus index (Item 53 verified corpus) with TF-IDF score 0.390; PMID, abstract, and index alignment re-checked at read time under the corpus standard \u2014 not a live re-confirmation. Direction (supports) derived from the record's own title and abstract text. Europe PMC is one independence group, so this evidence alone cannot satisfy the gate's two-group rule"
   ]
  ],
  "ranked": [
   [
    4,
    "0.6321420073509216"
   ],
   [
    3,
    "0.6066603064537048"
   ],
   [
    1,
    "0.49533554911613464"
   ],
   [
    0,
    "0.4363841712474823"
   ],
   [
    2,
    "0.3904983848333359"
   ]
  ],
  "topic": "corpus:cftr phe508del cystic fibrosis",
  "verdict": "decline",
  "verified_item_count": 3
 },
 "human|XA21 bacterial blight resistance rice": {
  "claims": [],
  "counters": [
   0,
   2,
   0
  ],
  "declined_reasons": [
   "no recognised variant, gene, or disease topic matched the question in the evidence provider, so no evidence could be gathered; the Copilot declines rather than guessing a topic"
  ],
  "independent_source_count": 0,
  "items": [],
  "ranked": [
   [
    5,
    "0.6369687631272634"
   ],
   [
    6,
    "0.5055859869604736"
   ]
  ],
  "topic": null,
  "verdict": "decline",
  "verified_item_count": 0
 },
 "human|black hole thermodynamics accretion disk entropy": {
  "claims": [],
  "counters": [
   0,
   0,
   0
  ],
  "declined_reasons": [
   "no recognised variant, gene, or disease topic matched the question in the evidence provider, so no evidence could be gathered; the Copilot declines rather than guessing a topic"
  ],
  "independent_source_count": 0,
  "items": [],
  "ranked": [],
  "topic": null,
  "verdict": "decline",
  "verified_item_count": 0
 },
 "plant|XA21 bacterial blight resistance rice": {
  "claims": [],
  "counters": [
   0,
   0,
   0
  ],
  "declined_reasons": [
   "only 2 verified supporting item(s); the gate requires at least 3",
   "verified supporting items come from 1 independent source group(s); the gate requires at least 2"
  ],
  "independent_source_count": 1,
  "items": [
   [
    "corpus-pmid-91000006",
    "XA21 receptor kinase confers blight resistance in rice",
    "PMID 91000006. XA21 receptor kinase confers blight resistance in rice. Fixture Plant Journal, 2018.",
    "supports",
    "verified",
    "https://europepmc.org/article/MED/91000006",
    "retrieved from the local Europe PMC corpus index (Item 53 verified corpus) with TF-IDF score 0.637; PMID, abstract, and index alignment re-checked at read time under the corpus standard \u2014 not a live re-confirmation. Direction (supports) derived from the record's own title and abstract text. Europe PMC is one independence group, so this evidence alone cannot satisfy the gate's two-group rule"
   ],
   [
    "corpus-pmid-91000007",
    "XA21 signalling pathways in rice immunity",
    "PMID 91000007. XA21 signalling pathways in rice immunity. Fixture Plant Journal, 2019.",
    "supports",
    "verified",
    "https://europepmc.org/article/MED/91000007",
    "retrieved from the local Europe PMC corpus index (Item 53 verified corpus) with TF-IDF score 0.506; PMID, abstract, and index alignment re-checked at read time under the corpus standard \u2014 not a live re-confirmation. Direction (supports) derived from the record's own title and abstract text. Europe PMC is one independence group, so this evidence alone cannot satisfy the gate's two-group rule"
   ]
  ],
  "ranked": [
   [
    5,
    "0.6369687631272634"
   ],
   [
    6,
    "0.5055859869604736"
   ]
  ],
  "topic": "corpus:xa21 bacterial blight resistance rice",
  "verdict": "decline",
  "verified_item_count": 2
 }
}''')

QUESTIONS = [
    ("human", "CFTR Phe508del cystic fibrosis"),
    ("human", "CFTR Phe508del corrector folding trafficking cystic fibrosis"),
    ("human", "XA21 bacterial blight resistance rice"),
    ("plant", "XA21 bacterial blight resistance rice"),
    ("human", "black hole thermodynamics accretion disk entropy"),
]


@pytest.fixture()
def corpus_dir(tmp_path):
    _write_fixture_corpus(tmp_path / "corpus", _FIXTURE_RECORDS)
    return tmp_path / "corpus"


def _item_tuple(item):
    return [
        item.item_id, item.statement, item.citation,
        item.direction.value, item.status.value, item.locator,
        item.notes,
    ]


def _run_question(corpus_dir, division, question, scratch_dir=None):
    provider = CorpusProvider(
        corpus_dir, division=division, scratch_dir=scratch_dir
    )
    index = provider._ensure_index()
    ranked = index.search(question, limit=provider.candidate_rows)
    topic = provider.topic_for_question(question)
    items = provider.items_for_topic(topic) if topic else ()
    copilot = Copilot(
        provider=CorpusProvider(
            corpus_dir, division=division, scratch_dir=scratch_dir
        )
    )
    answer = copilot.ask(
        CopilotQuestion(text=question, division=Division(division))
    )
    return {
        "ranked": [[row, repr(score)] for row, score in ranked],
        "topic": topic,
        "items": [_item_tuple(item) for item in items],
        "counters": [
            provider.dropped_records,
            provider.out_of_division_records,
            provider.duplicate_content_records,
        ],
        "verdict": answer.verdict.value,
        "declined_reasons": list(answer.declined_reasons),
        "verified_item_count": answer.verified_item_count,
        "independent_source_count": answer.independent_source_count,
    }


def _assert_matches_golden(result, golden):
    assert result["ranked"] == golden["ranked"]
    assert result["topic"] == golden["topic"]
    assert result["items"] == golden["items"]
    assert result["counters"] == golden["counters"]
    assert result["verdict"] == golden["verdict"]
    assert result["declined_reasons"] == golden["declined_reasons"]
    assert result["verified_item_count"] == golden["verified_item_count"]
    assert (
        result["independent_source_count"]
        == golden["independent_source_count"]
    )


@pytest.mark.parametrize("division,question", QUESTIONS)
def test_golden_streaming_path(corpus_dir, division, question):
    result = _run_question(corpus_dir, division, question)
    _assert_matches_golden(result, GOLDEN[f"{division}|{question}"])


@pytest.mark.parametrize("division,question", QUESTIONS)
def test_golden_persistent_cache_path(
    corpus_dir, tmp_path, division, question
):
    scratch = tmp_path / "scratch"
    cold = _run_question(
        corpus_dir, division, question, scratch_dir=scratch
    )
    _assert_matches_golden(cold, GOLDEN[f"{division}|{question}"])
    warm = _run_question(
        corpus_dir, division, question, scratch_dir=scratch
    )
    _assert_matches_golden(warm, GOLDEN[f"{division}|{question}"])


def test_golden_abstract_lookup(corpus_dir, tmp_path):
    for scratch in (None, tmp_path / "scratch"):
        provider = CorpusProvider(
            corpus_dir, division="human", scratch_dir=scratch
        )
        assert provider.abstract_for_pmid("91000006") == (
            GOLDEN["abstract_91000006"]
        )
        assert provider.abstract_for_pmid("00000000") == (
            GOLDEN["abstract_missing"]
        )


# ---------------------------------------------------------------------
# The persistent cache: built once, reused, and fetch-identical
# ---------------------------------------------------------------------


def test_record_copy_fetch_matches_streaming_fetch(corpus_dir, tmp_path):
    streaming = _CorpusIndex(corpus_dir)
    assert streaming._ensure_record_copy() is False
    cached = _CorpusIndex(corpus_dir, scratch_dir=tmp_path / "scratch")
    assert cached._ensure_record_copy() is True
    rows = list(range(len(_FIXTURE_RECORDS)))
    assert cached.fetch_records(rows) == streaming.fetch_records(rows)
    # A subset, and rows asked in reverse: same records.
    assert cached.fetch_records([6, 0]) == streaming.fetch_records([6, 0])
    assert cached.fetch_records([]) == {}


def test_persistent_cache_is_reused_not_reextracted(corpus_dir, tmp_path):
    scratch = tmp_path / "scratch"
    first = CorpusProvider(
        corpus_dir, division="human", scratch_dir=scratch
    )
    first.topic_for_question("CFTR Phe508del cystic fibrosis")
    cache_dirs = list(scratch.iterdir())
    assert len(cache_dirs) == 1
    cache = cache_dirs[0]
    assert (cache / "corpus.jsonl").exists()
    assert (cache / "corpus.offsets.bin").exists()
    payload_mtimes = {
        name: (cache / name).stat().st_mtime_ns
        for name in ("data.npy", "indices.npy", "indptr.npy")
    }
    first.close()
    second = CorpusProvider(
        corpus_dir, division="human", scratch_dir=scratch
    )
    second.topic_for_question("CFTR Phe508del cystic fibrosis")
    assert {
        name: (cache / name).stat().st_mtime_ns
        for name in payload_mtimes
    } == payload_mtimes
    second.close()


# ---------------------------------------------------------------------
# Reference: dense computation over the fixture CSR arrays
# ---------------------------------------------------------------------


def _read_npy_payloads(corpus_dir):
    archive_path = corpus_dir / "index" / "tfidf_csr.npz"
    payloads = {}
    with zipfile.ZipFile(archive_path) as archive:
        for member in ("data.npy", "indices.npy", "indptr.npy"):
            raw = archive.read(member)
            header_len = struct.unpack("<H", raw[8:10])[0]
            header = ast.literal_eval(
                raw[10:10 + header_len].decode("latin1")
            )
            fmt = {"<f4": "f", "<i4": "i"}[header["descr"]]
            values = array(fmt)
            values.frombytes(raw[10 + header_len:])
            payloads[member] = values
    return payloads["data.npy"], payloads["indices.npy"], payloads["indptr.npy"]


def _reference_search(corpus_dir, query_text, limit):
    from provome.rag import _tokenise

    vocab = json.loads(
        (corpus_dir / "index" / "vocab.json").read_text(encoding="utf-8")
    )
    column_of = {term: i for i, term in enumerate(vocab)}
    columns = sorted(
        {column_of[t] for t in set(_tokenise(query_text)) if t in column_of}
    )
    if not columns:
        return []
    data, indices, indptr = _read_npy_payloads(corpus_dir)
    scores = {}
    for row in range(len(indptr) - 1):
        entries = {
            indices[p]: data[p]
            for p in range(indptr[row], indptr[row + 1])
        }
        total = 0.0
        for column in columns:
            if column in entries:
                total += entries[column]
        if total > 0.0:
            scores[row] = total
    query_norm = math.sqrt(len(columns))
    ranked = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
    return [
        (row, score / query_norm)
        for row, score in ranked[:limit]
        if score > 0.0
    ]


@pytest.mark.parametrize("division,question", QUESTIONS)
def test_search_matches_dense_reference(corpus_dir, division, question):
    index = _CorpusIndex(corpus_dir)
    expected = _reference_search(
        corpus_dir, question, limit=200
    )
    assert index.search(question, limit=200) == expected


# ---------------------------------------------------------------------
# Overlap: an aligned occurrence hidden inside a match's span
# ---------------------------------------------------------------------


def _write_overlap_corpus(root):
    root.mkdir(parents=True, exist_ok=True)
    index_dir = root / "index"
    index_dir.mkdir(exist_ok=True)
    with gzip.open(root / "corpus.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"pmid": "1", "abstract": "x"}) + "\n")
    # One row, two entries.  The indices byte stream is
    # 00 00 00 01 | 00 00 00 00: the column-1 pattern
    # (01 00 00 00) matches at byte 3 — unaligned — and its
    # span covers byte 4, where the column-0 pattern
    # (00 00 00 00) occurs aligned at entry position 1.
    data = array("f", [0.25, 0.5])
    indices = array("i", [0x01000000, 0])
    indptr = array("i", [0, 2])
    with zipfile.ZipFile(
        index_dir / "tfidf_csr.npz", "w", zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr(
            "data.npy", _npy_bytes("<f4", (2,), data.tobytes())
        )
        archive.writestr(
            "indices.npy", _npy_bytes("<i4", (2,), indices.tobytes())
        )
        archive.writestr(
            "indptr.npy", _npy_bytes("<i4", (2,), indptr.tobytes())
        )
    (index_dir / "vocab.json").write_text(
        json.dumps(["alpha", "beta"]), encoding="utf-8"
    )
    (index_dir / "doc_ids.json").write_text(
        json.dumps(["1"]), encoding="utf-8"
    )


def test_positions_patch_up_recovers_hidden_occurrence(tmp_path):
    root = tmp_path / "overlap"
    _write_overlap_corpus(root)
    index = _CorpusIndex(root)
    positions = index._positions_by_column([0, 1])
    assert positions[0] == [1]
    assert positions[1] == []
    ranked = index.search("alpha beta", limit=10)
    assert ranked == [(0, 0.5 / math.sqrt(2))]
