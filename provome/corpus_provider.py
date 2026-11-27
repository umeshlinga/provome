"""The corpus-backed Copilot provider: retrieval over the Item 53 corpus.

The Copilot's other providers fetch a handful of records per
question from live databases, or serve bundled fixtures.  This
provider is the literature-scale half (LLM track Stage A,
:mod:`provome.rag`): it answers the Copilot's two questions —
which topic is this, and what evidence do you hold — from the
verified Europe PMC corpus on local disk
(``~/workspace/provome-corpus/`` by layout, any directory with
the same artefacts by configuration):

* ``corpus.jsonl.gz`` — the deduped records, one JSON object
  per line (``pmid``, ``title``, ``abstract``, ``year``,
  ``journal``, ``division``, ``query_id``), in index row order;
* ``index/tfidf_csr.npz`` — the TF-IDF matrix in CSR form
  (``provome.rag`` mathematics: tf = count / document tokens,
  idf = log((1 + N) / (1 + df)) + 1, rows L2-normalised);
* ``index/vocab.json`` — the indexed terms, position = column;
* ``index/doc_ids.json`` — the PMID of each matrix row.

No network at query time, and no new dependency: the package
stays standard-library only, so the ``.npz`` members are
extracted to a scratch directory and memory-mapped, the
``.npy`` headers parsed directly, and search runs over the
CSR columns the query's terms touch.  The extraction needs
scratch space for the payloads (~2.4 GB at Item 53 scale);
the scratch location is configurable (``scratch_dir``) for
machines whose system temp area is small.  Query terms are
weighted uniformly — per-term document frequencies are not
stored in the index artefacts — while the document vectors
carry the corpus idf and are L2-normalised, so a score is a
cosine against a uniform-weight query.  Records are fetched
by streaming scan of the corpus file (a record's row is its
line number); at 2.2M records a scan is the honest cost of
not having an offset index, and building one is future work,
not a hidden step here.

Trust rules, stated plainly:

* **Re-verification at read time.**  Every retrieved record
  is re-checked against the corpus's own verified standard —
  the Item 53 ingest rule applied again: a real PMID present,
  a non-empty abstract present, and the record's PMID
  matching the index row's recorded PMID.  A record failing
  any check is dropped and counted on the provider
  (``dropped_records``); it never becomes an evidence item.
* **What VERIFIED means here.**  Items that pass are marked
  VERIFIED under that corpus standard — PMID + abstract
  required at ingest, re-checked at read.  That is not a
  live re-confirmation: this provider makes no network
  calls, and does not repeat the pipeline evaluation's
  per-PMID live check against PubMed.  The item's notes say
  which standard was applied.
* **One source, one group.**  Every item's source is Europe
  PMC, registered in the source registry as exactly one
  independence group.  The cite-or-decline gate is unchanged
  and is not weakened for this provider — so corpus-only
  evidence FAILS the gate's two-group rule, and the honest
  behaviour of ``Copilot(provider=CorpusProvider(...))``
  today is a DECLINE that names that reason, however much
  verified evidence retrieval found.  That is the known
  structural gap from the Item 53 evaluation, stated here
  rather than tuned around: a second independent source
  group (for example ClinVar or gnomAD cross-supply) is
  future work.
* **Division scoping.**  A provider instance is scoped to
  one division, and retrieval is filtered by the records'
  primary-division tags: a Human provider never issues
  Plant-tagged records as evidence, and the Copilot's own
  division guard declines out-of-scope questions before
  retrieval runs.

Research software.  Not for clinical use.
"""

from __future__ import annotations

import ast
import gzip
import json
import math
import mmap
import shutil
import struct
import tempfile
import zipfile
from pathlib import Path
from typing import Optional, Sequence

from .domains import Division
from .evidence import (
    EvidenceItem,
    EvidenceKind,
    SupportDirection,
    VerificationStatus,
)
from .rag import _tokenise  # the tokeniser the corpus index was built with

__all__ = [
    "EUROPEPMC_SOURCE_ID",
    "CorpusProvider",
]

#: The only source this provider emits.  Registered in
#: :func:`provome.evidence.default_source_registry` as exactly
#: one independence group.
EUROPEPMC_SOURCE_ID = "europepmc"


# ---------------------------------------------------------------------
# Reading the on-disk index: .npy members, memory-mapped.
# ---------------------------------------------------------------------

_NPY_DESCR_FORMATS = {
    "<f4": "f", "<f8": "d",
    "<i4": "i", "<i8": "q",
    "<u4": "I", "<u8": "Q",
    "|u1": "B",
}


class _NpyArray:
    """One ``.npy`` member of the index archive, memory-mapped.

    The member is extracted to a scratch file (the archive is
    compressed, so it cannot be mapped in place), the header
    parsed, and the payload exposed as a typed memoryview over
    the map — the 2.3 GB of matrix payloads are paged by the
    OS, never loaded into Python objects.
    """

    def __init__(self, npz_path: Path, member: str, scratch: Path) -> None:
        target = scratch / member
        with zipfile.ZipFile(npz_path) as archive:
            with archive.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        self._file = target.open("rb")
        self._mm = mmap.mmap(
            self._file.fileno(), 0, access=mmap.ACCESS_READ
        )
        self.data_offset, descr, shape = self._parse_header(self._mm)
        if descr not in _NPY_DESCR_FORMATS:
            raise ValueError(
                f"unsupported .npy dtype {descr!r} in {member}; "
                "the corpus index uses little-endian float/int arrays"
            )
        fmt = _NPY_DESCR_FORMATS[descr]
        count = 1
        for dim in shape:
            count *= dim
        self.shape = shape
        self.itemsize = struct.calcsize(fmt)
        payload = self._mm[
            self.data_offset:self.data_offset + count * self.itemsize
        ]
        self.values = memoryview(payload).cast(fmt)

    @staticmethod
    def _parse_header(mm) -> tuple:
        if mm[0:6] != b"\x93NUMPY":
            raise ValueError("index member is not an .npy payload")
        major = mm[6]
        if major == 1:
            header_len = struct.unpack("<H", mm[8:10])[0]
            header_start = 10
        else:
            header_len = struct.unpack("<I", mm[8:12])[0]
            header_start = 12
        header = ast.literal_eval(
            mm[header_start:header_start + header_len]
            .decode("latin1")
            .strip()
        )
        return (
            header_start + header_len,
            header["descr"],
            tuple(header["shape"]),
        )

    def find_all(self, pattern: bytes):
        """Byte offsets of ``pattern`` in the payload, ascending."""
        start = self.data_offset
        end = self.data_offset + len(self.values) * self.itemsize
        pos = self._mm.find(pattern, start, end)
        while pos != -1:
            yield pos
            pos = self._mm.find(pattern, pos + 1, end)

    def close(self) -> None:
        self.values = None
        self._mm.close()
        self._file.close()


class _CorpusIndex:
    """The on-disk TF-IDF index: vocabulary, row ids, CSR matrix."""

    def __init__(self, corpus_dir: Path,
                 scratch_dir: Optional[Path] = None) -> None:
        index_dir = corpus_dir / "index"
        vocab_terms = json.loads(
            (index_dir / "vocab.json").read_text(encoding="utf-8")
        )
        self._vocab = {
            term: column for column, term in enumerate(vocab_terms)
        }
        self.doc_ids = json.loads(
            (index_dir / "doc_ids.json").read_text(encoding="utf-8")
        )
        if scratch_dir is not None:
            Path(scratch_dir).mkdir(parents=True, exist_ok=True)
            scratch = Path(
                tempfile.mkdtemp(
                    prefix="provome-corpus-index-", dir=scratch_dir
                )
            )
        else:
            scratch = Path(
                tempfile.mkdtemp(prefix="provome-corpus-index-")
            )
        npz_path = index_dir / "tfidf_csr.npz"
        self._data = _NpyArray(npz_path, "data.npy", scratch)
        self._indices = _NpyArray(npz_path, "indices.npy", scratch)
        self._indptr = _NpyArray(npz_path, "indptr.npy", scratch)
        self.rows = len(self._indptr.values) - 1
        self._corpus_gz = corpus_dir / "corpus.jsonl.gz"

    # -- retrieval -------------------------------------------------

    def _column_hits(self, column: int):
        """Yield ``(row, value)`` for every entry in one CSR column.

        The column's positions are found by byte search over the
        indices payload (C-speed scan, alignment- and
        value-verified), and rows are resolved by walking the
        row pointer monotonically — positions ascend, so the
        pointer never rewinds.
        """
        indices = self._indices.values
        data = self._data.values
        indptr = self._indptr.values
        base = self._indices.data_offset
        pattern = struct.pack("<i", column)
        row = 0
        for pos in self._indices.find_all(pattern):
            offset = pos - base
            if offset % 4 != 0:
                continue
            position = offset // 4
            if indices[position] != column:
                continue
            while indptr[row + 1] <= position:
                row += 1
            yield row, data[position]

    def search(self, query_text: str, limit: int) -> list:
        """Top rows for a query: ``[(row, cosine_score)]``, best first.

        Only documents sharing a term with the query can score;
        a query with no in-vocabulary term retrieves nothing.
        """
        columns = sorted(
            {
                self._vocab[token]
                for token in set(_tokenise(query_text))
                if token in self._vocab
            }
        )
        if not columns:
            return []
        scores: dict = {}
        for column in columns:
            for row, value in self._column_hits(column):
                scores[row] = scores.get(row, 0.0) + value
        query_norm = math.sqrt(len(columns))
        ranked = sorted(
            scores.items(), key=lambda pair: (-pair[1], pair[0])
        )
        return [
            (row, score / query_norm)
            for row, score in ranked[:limit]
            if score > 0.0
        ]

    # -- records ---------------------------------------------------

    def fetch_records(self, rows) -> dict:
        """Stream the corpus once, returning ``{row: record}``.

        A record's row is its line number in ``corpus.jsonl.gz``
        (the alignment the corpus build guarantees and the
        provider re-checks).  Lines are parsed only for wanted
        rows; an unparseable wanted line is simply absent from
        the result, and the provider counts it as dropped.
        """
        wanted = set(rows)
        if not wanted:
            return {}
        last = max(wanted)
        found: dict = {}
        with gzip.open(self._corpus_gz, "rb") as handle:
            for line_no, line in enumerate(handle):
                if line_no in wanted:
                    try:
                        found[line_no] = json.loads(line)
                    except ValueError:
                        pass  # corrupt line: absent, counted downstream
                if line_no >= last:
                    break
        return found

    def close(self) -> None:
        self._data.close()
        self._indices.close()
        self._indptr.close()


# ---------------------------------------------------------------------
# The provider
# ---------------------------------------------------------------------


class CorpusProvider:
    """A Copilot evidence provider over the local Europe PMC corpus.

    Args:
        corpus_dir: Directory holding ``corpus.jsonl.gz`` and
            ``index/`` in the Item 53 layout.
        division: The one division this instance is scoped to
            (a :class:`~provome.domains.Division` or its value,
            for example ``"plant"``).  Retrieval is filtered to
            records whose primary-division tag matches, and the
            Copilot's division guard declines questions for any
            other division.
        max_items: Maximum evidence items served per topic.
        candidate_rows: How far down the ranked retrieval the
            provider looks for records that survive filtering
            and re-verification.
        scratch_dir: Where the index payloads are extracted
            for memory-mapping.  Defaults to the system temp
            area; the extraction needs ~2.4 GB at Item 53
            scale, so point this at a disk-backed directory
            where temp space is small.

    Attributes:
        dropped_records: Cumulative count of retrieved records
            dropped by the read-time re-verification (missing
            or malformed PMID, missing abstract, PMID not
            matching the index row, or an unreadable record
            line).
        out_of_division_records: Cumulative count of retrieved
            records skipped because their primary division is
            not this provider's division.
    """

    name = "europepmc-corpus"

    def __init__(
        self,
        corpus_dir,
        division=Division.HUMAN,
        *,
        max_items: int = 10,
        candidate_rows: int = 200,
        scratch_dir=None,
    ) -> None:
        if isinstance(division, str):
            division = Division(division.strip().lower())
        self.corpus_dir = Path(corpus_dir)
        self.division = division
        self.scoped_divisions = frozenset({division})
        if max_items < 1:
            raise ValueError("max_items must be at least 1")
        if candidate_rows < 1:
            raise ValueError("candidate_rows must be at least 1")
        self.max_items = max_items
        self.candidate_rows = candidate_rows
        self.scratch_dir = (
            Path(scratch_dir) if scratch_dir is not None else None
        )
        self.dropped_records = 0
        self.out_of_division_records = 0
        self._index: Optional[_CorpusIndex] = None
        self._items_cache: dict = {}

    # -- provider protocol ------------------------------------------

    def topic_for_question(self, question_text: str) -> Optional[str]:
        items = self._retrieve(question_text)
        if not items:
            return None
        topic_key = self._topic_key(question_text)
        self._items_cache[topic_key] = items
        return topic_key

    def items_for_topic(self, topic_key: str) -> Sequence[EvidenceItem]:
        if topic_key in self._items_cache:
            return self._items_cache[topic_key]
        if isinstance(topic_key, str) and topic_key.startswith("corpus:"):
            # The topic key carries the normalised question, and
            # retrieval is deterministic: re-running it rebuilds
            # the same items without session state.
            items = self._retrieve(topic_key[len("corpus:"):])
            self._items_cache[topic_key] = items
            return items
        return ()

    def close(self) -> None:
        """Release the memory-mapped index, if it was loaded."""
        if self._index is not None:
            self._index.close()
            self._index = None

    # -- internals ---------------------------------------------------

    @staticmethod
    def _topic_key(question_text: str) -> str:
        return "corpus:" + " ".join(question_text.lower().split())

    def _ensure_index(self) -> _CorpusIndex:
        if self._index is None:
            self._index = _CorpusIndex(self.corpus_dir, self.scratch_dir)
        return self._index

    def _retrieve(self, question_text: str) -> tuple:
        index = self._ensure_index()
        ranked = index.search(question_text, limit=self.candidate_rows)
        if not ranked:
            return ()
        records = index.fetch_records([row for row, _ in ranked])
        topic_key = self._topic_key(question_text)
        items: list = []
        for row, score in ranked:
            item = self._record_to_item(
                index, row, records.get(row), topic_key, score
            )
            if item is not None and len(items) < self.max_items:
                items.append(item)
        return tuple(items)

    def _record_to_item(self, index, row, record, topic_key, score):
        """One retrieved record → an evidence item, or a counted drop.

        The Item 53 ingest rule, applied again at read time:
        PMID present and well-formed, abstract present, and the
        record's PMID matching the index row it was retrieved
        as.  Division filtering is scoping, counted separately.
        """
        if record is None:
            self.dropped_records += 1
            return None
        if record.get("division") != self.division.value:
            self.out_of_division_records += 1
            return None
        pmid_raw = record.get("pmid")
        pmid = str(pmid_raw).strip() if pmid_raw is not None else ""
        abstract = record.get("abstract")
        if not pmid.isdigit():
            self.dropped_records += 1
            return None
        if not isinstance(abstract, str) or not abstract.strip():
            self.dropped_records += 1
            return None
        if index.doc_ids[row] != pmid:
            self.dropped_records += 1
            return None
        title = str(record.get("title") or "").strip()
        if title:
            statement = title
        else:
            statement = " ".join(abstract.split())[:240]
        citation = f"PMID {pmid}"
        if title:
            citation += f". {title.rstrip('.')}."
        journal = str(record.get("journal") or "").strip()
        year = str(record.get("year") or "").strip()
        if journal and year:
            citation += f" {journal}, {year}."
        elif journal:
            citation += f" {journal}."
        elif year:
            citation += f" {year}."
        return EvidenceItem(
            item_id=f"corpus-pmid-{pmid}",
            topic_key=topic_key,
            statement=statement,
            source_id=EUROPEPMC_SOURCE_ID,
            citation=citation,
            kind=EvidenceKind.LITERATURE_REPORT,
            direction=SupportDirection.SUPPORTS,
            status=VerificationStatus.VERIFIED,
            locator=f"https://europepmc.org/article/MED/{pmid}",
            notes=(
                f"retrieved from the local Europe PMC corpus "
                f"index (Item 53 verified corpus) with TF-IDF "
                f"score {score:.3f}; PMID, abstract, and index "
                "alignment re-checked at read time under the "
                "corpus standard — not a live re-confirmation. "
                "Europe PMC is one independence group, so this "
                "evidence alone cannot satisfy the gate's "
                "two-group rule"
            ),
        )
