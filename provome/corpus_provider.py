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
CSR columns the query's terms touch (one pass over the
indices payload for all of the query's columns together).
The extraction needs scratch space for the payloads
(~2.4 GB at Item 53 scale); the scratch location is
configurable (``scratch_dir``) for machines whose system
temp area is small, and a configured scratch directory is
also where the extraction and the record offset index are
cached for reuse across provider instances.  Query terms
are weighted uniformly — per-term document frequencies are
not stored in the index artefacts — while the document
vectors carry the corpus idf and are L2-normalised, so a
score is a cosine against a uniform-weight query.
Records are fetched through a line-offset index: the first
fetch against a persistent scratch directory decompresses
``corpus.jsonl.gz`` once into an uncompressed copy there,
recording each line's byte offset, and later fetches seek
directly to the wanted lines.  Without a persistent scratch
directory, records are fetched by the original streaming
scan of the corpus file (a record's row is its line number).
Either way the bytes parsed for a row are the corpus's own
line for that row, byte for byte.

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
  which standard was applied.  (Where this provider is
  composed into the cross-source chain, that chain *does*
  re-confirm every corpus item against PubMed before it can
  count toward a pass — see :mod:`provome.cross_source`.
  Standalone, the corpus standard is what applies.)
* **Direction is derived, not assumed.**  An item's
  direction comes from the record's own title and abstract:
  explicit negation language ("does not cause", "not
  associated with", "benign and unrelated", …) makes it
  REFUTES; anything else is SUPPORTS.  The pattern set is
  deliberately narrow, and a paper arguing against the
  claim in subtler words will still read as SUPPORTS —
  the stated limit of the check (see ``_derive_direction``).
* **One paper is one line.**  Records with identical
  normalised title and abstract are the same paper, however
  many PMIDs they arrive under; retrieval serves the first
  and counts the rest (``duplicate_content_records``), so a
  duplicated paper cannot inflate the evidence count.
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
import hashlib
import json
import math
import mmap
import os
import re
import shutil
import struct
import tempfile
import zipfile
from array import array
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
# Direction derivation (red-team round, 2026-11-28, class 4)
#
# Corpus items were originally stamped SUPPORTS unconditionally,
# so a paper whose own title says the variant "does not cause"
# the disease was counted as supporting the claim it refutes.
# The direction is now derived from the record's own text: a
# small set of explicit-negation patterns marks the item
# REFUTES; everything else stays SUPPORTS.  The patterns are
# deliberately narrow — they fire only on unambiguous negation
# language about causation, association, or classification —
# and the limit is stated plainly: a paper that argues against
# the claim in subtler words will still be read as SUPPORTS
# here.  That residual risk runs in the safe direction only
# when combined with the chain's other checks (the cross-source
# composite re-confirms every corpus item against PubMed
# before it can count toward a pass; see
# :mod:`provome.cross_source`).
# ---------------------------------------------------------------------

_REFUTATION_PATTERNS = (
    re.compile(r"\bdoes not cause\b|\bdid not cause\b|\bdo not cause\b"),
    re.compile(r"\bis not causal\b|\bnot causally\b"),
    re.compile(r"\bnot associated with\b|\bno association\b"),
    re.compile(r"\bnot linked to\b|\bno link between\b"),
    re.compile(
        r"\bbenign and unrelated\b|\bshown benign\b"
        r"|\bclassified benign\b|\bclassified as benign\b"
        r"|\bis benign\b"
    ),
    re.compile(
        r"\bnot pathogenic\b|\bnon[- ]pathogenic\b"
        r"|\bnot disease[- ]causing\b"
    ),
)


def _derive_direction(title: str, abstract: str) -> SupportDirection:
    """The direction a corpus record's own text supports.

    REFUTES only on explicit negation language (see the
    pattern list above); SUPPORTS otherwise.  The claim a
    corpus item is read against is the topic's implicit one —
    that the retrieved record supports the association its
    retrieval was about — so a record that negates causation
    or association, or classifies the variant benign, refutes
    it.
    """
    text = f"{title} {abstract}".lower()
    if any(pattern.search(text) for pattern in _REFUTATION_PATTERNS):
        return SupportDirection.REFUTES
    return SupportDirection.SUPPORTS


def _content_key(title: str, abstract: str) -> tuple:
    """Identity of a paper's content, for duplicate collapse.

    The same paper planted (or ingested) under two PMIDs is
    one evidence line, not two: the gate's deduplication keys
    on the canonical citation (the PMID), so identical content
    under different PMIDs would otherwise count once per PMID.
    Two records are the same paper here when their normalised
    title *and* abstract are identical.
    """
    return (
        " ".join((title or "").lower().split()),
        " ".join((abstract or "").lower().split()),
    )


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
            info = archive.getinfo(member)
            if not (
                target.exists()
                and target.stat().st_size == info.file_size
            ):
                # Extract to a per-process temp name and publish
                # by rename, so a concurrent or interrupted
                # extraction never leaves a partial file under
                # the member's name; a complete file already
                # extracted by an earlier instance is reused
                # untouched (its size is the member's size).
                staging = scratch / f"{member}.tmp-{os.getpid()}"
                with archive.open(member) as src, staging.open(
                    "wb"
                ) as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                os.replace(staging, target)
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


def _cache_key(*paths: Path) -> str:
    """Identity of the on-disk artefacts a cache was built from.

    Path, size, and modification time of each artefact: if any
    of them changes, the key changes and stale cache content is
    simply never found under the new key.
    """
    digest = hashlib.sha1()
    for path in paths:
        stat = path.stat()
        digest.update(str(path.resolve()).encode("utf-8"))
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
    return digest.hexdigest()[:16]


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
        self._corpus_gz = corpus_dir / "corpus.jsonl.gz"
        npz_path = index_dir / "tfidf_csr.npz"
        if scratch_dir is not None:
            # A configured scratch directory is a persistent
            # cache: the directory name is keyed on the index
            # and corpus artefacts themselves, so provider
            # instances (in this or a later process) reuse the
            # extracted payloads and the record offset index
            # built by the first instance, and a rebuilt corpus
            # or index lands under a fresh key.
            Path(scratch_dir).mkdir(parents=True, exist_ok=True)
            scratch = Path(scratch_dir) / (
                "provome-corpus-index-"
                + _cache_key(npz_path, self._corpus_gz)
            )
            scratch.mkdir(parents=True, exist_ok=True)
            self._persistent = True
        else:
            scratch = Path(
                tempfile.mkdtemp(prefix="provome-corpus-index-")
            )
            self._persistent = False
        self._scratch = scratch
        self._data = _NpyArray(npz_path, "data.npy", scratch)
        self._indices = _NpyArray(npz_path, "indices.npy", scratch)
        self._indptr = _NpyArray(npz_path, "indptr.npy", scratch)
        self.rows = len(self._indptr.values) - 1
        self._row_by_pmid: Optional[dict] = None
        self._copy_path: Optional[Path] = None
        self._copy_offsets: Optional[array] = None
        self._copy_unavailable = False

    # -- retrieval -------------------------------------------------

    def _positions_by_column(self, columns) -> dict:
        """Entry positions per column: ``{column: [position, …]}``.

        One pass over the indices payload finds every column's
        occurrences together, where the original code scanned
        the whole payload once per column (``_NpyArray.find_all``
        per column, then the same alignment and value checks
        applied here).  The pass is a single alternation search
        over the memory map; the positions collected for a
        column are exactly the positions the per-column byte
        search yielded — ascending, alignment-filtered, and
        value-verified against the payload itself:

        * A match's span is consumed by the search, so an
          occurrence starting inside a matched span is never
          reported.  An aligned occurrence can hide there only
          when the match starts unaligned, and each match span
          then contains exactly one aligned offset; that offset
          is checked explicitly (the patch-up below).
        * Occurrences outside every matched span are reached by
          the scan one byte at a time and reported directly.

        So the union of the reported matches and the patch-up
        checks is the per-column search's yield, position for
        position — the regression tests pin this against the
        per-column reference on a payload crafted so that
        occurrences overlap.
        """
        indices = self._indices
        values = indices.values
        itemsize = indices.itemsize
        base = indices.data_offset
        end = base + len(values) * itemsize
        pattern_of = {
            struct.pack("<i", column): column for column in columns
        }
        finder = re.compile(
            b"|".join(re.escape(pattern) for pattern in pattern_of)
        )
        found: dict = {column: [] for column in columns}
        unaligned_starts: list = []
        for match in finder.finditer(indices._mm, base, end):
            start = match.start()
            offset = start - base
            if offset % itemsize:
                unaligned_starts.append(start)
                continue
            column = pattern_of[match.group()]
            position = offset // itemsize
            if values[position] == column:
                found[column].append(position)
        # Patch-up: the one aligned offset inside each unaligned
        # match's span, checked the same way as a reported match.
        for start in unaligned_starts:
            aligned = start + (-(start - base) % itemsize)
            if aligned + itemsize > end:
                continue
            column = pattern_of.get(
                bytes(indices._mm[aligned:aligned + itemsize])
            )
            if column is None:
                continue
            position = (aligned - base) // itemsize
            if values[position] == column:
                found[column].append(position)
        for positions in found.values():
            positions.sort()
        return found

    def search(self, query_text: str, limit: int) -> list:
        """Top rows for a query: ``[(row, cosine_score)]``, best first.

        Only documents sharing a term with the query can score;
        a query with no in-vocabulary term retrieves nothing.
        Scores accumulate per row in ascending column order and,
        within a column, ascending entry order — the accumulation
        order the original per-column scan used, so the sums are
        the same floats, not merely close ones.
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
        positions = self._positions_by_column(columns)
        data = self._data.values
        indptr = self._indptr.values
        scores: dict = {}
        for column in columns:
            row = 0
            for position in positions[column]:
                while indptr[row + 1] <= position:
                    row += 1
                scores[row] = scores.get(row, 0.0) + data[position]
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

    def row_for_pmid(self, pmid: str) -> Optional[int]:
        """The corpus row carrying ``pmid``, or ``None``.

        The first row, if the id were ever duplicated — the
        semantics ``list.index`` gave the original linear scan
        of ``doc_ids``, kept by building the map in row order
        and never overwriting an entry.
        """
        if self._row_by_pmid is None:
            mapping: dict = {}
            for row, doc_id in enumerate(self.doc_ids):
                if doc_id not in mapping:
                    mapping[doc_id] = row
            self._row_by_pmid = mapping
        return self._row_by_pmid.get(pmid)

    def _ensure_record_copy(self) -> bool:
        """The uncompressed corpus copy + line offsets, if usable.

        Built once into the persistent scratch cache (see
        ``__init__``): the gzip is streamed a single time, each
        line written to the copy as its byte offset is recorded.
        Reuse is validated against the corpus file's size and
        modification time and the built files' sizes.  Without
        a persistent scratch directory, or if the build fails
        (for example a full disk), this returns ``False`` and
        record fetches use the streaming scan — the same
        records, at the original cost.
        """
        if self._copy_offsets is not None:
            return True
        if not self._persistent or self._copy_unavailable:
            return False
        copy_path = self._scratch / "corpus.jsonl"
        offsets_path = self._scratch / "corpus.offsets.bin"
        meta_path = self._scratch / "corpus.copy.json"
        gz_stat = self._corpus_gz.stat()
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if (
                meta["gz_size"] == gz_stat.st_size
                and meta["gz_mtime_ns"] == gz_stat.st_mtime_ns
                and copy_path.stat().st_size == meta["copy_size"]
                and offsets_path.stat().st_size == meta["lines"] * 8
            ):
                offsets = array("q")
                with offsets_path.open("rb") as handle:
                    offsets.frombytes(handle.read())
                self._copy_path = copy_path
                self._copy_offsets = offsets
                return True
        except (OSError, ValueError, KeyError, TypeError):
            pass  # no usable cache: build it below
        try:
            staging_copy = self._scratch / (
                f"corpus.jsonl.tmp-{os.getpid()}"
            )
            staging_offsets = self._scratch / (
                f"corpus.offsets.bin.tmp-{os.getpid()}"
            )
            offsets = array("q")
            offset = 0
            with gzip.open(self._corpus_gz, "rb") as src, (
                staging_copy.open("wb")
            ) as dst:
                for line in src:
                    offsets.append(offset)
                    dst.write(line)
                    offset += len(line)
            with staging_offsets.open("wb") as handle:
                offsets.tofile(handle)
            os.replace(staging_copy, copy_path)
            os.replace(staging_offsets, offsets_path)
            meta_tmp = self._scratch / (
                f"corpus.copy.json.tmp-{os.getpid()}"
            )
            meta_tmp.write_text(
                json.dumps(
                    {
                        "gz_size": gz_stat.st_size,
                        "gz_mtime_ns": gz_stat.st_mtime_ns,
                        "lines": len(offsets),
                        "copy_size": offset,
                    }
                ),
                encoding="utf-8",
            )
            os.replace(meta_tmp, meta_path)
        except OSError:
            self._copy_unavailable = True
            return False
        self._copy_path = copy_path
        self._copy_offsets = offsets
        return True

    def fetch_records(self, rows) -> dict:
        """Return ``{row: record}`` for the wanted rows.

        A record's row is its line number in ``corpus.jsonl.gz``
        (the alignment the corpus build guarantees and the
        provider re-checks).  With the offset index (see
        :meth:`_ensure_record_copy`) each wanted line is read
        directly from the uncompressed copy; otherwise the
        corpus is streamed once and lines are parsed only for
        wanted rows.  Either way an unparseable wanted line is
        simply absent from the result, and the provider counts
        it as dropped.
        """
        wanted = set(rows)
        if not wanted:
            return {}
        if self._ensure_record_copy():
            assert self._copy_path is not None
            assert self._copy_offsets is not None
            found: dict = {}
            with self._copy_path.open("rb") as handle:
                for row in sorted(wanted):
                    if row < 0 or row >= len(self._copy_offsets):
                        continue
                    handle.seek(self._copy_offsets[row])
                    line = handle.readline()
                    try:
                        found[row] = json.loads(line)
                    except ValueError:
                        pass  # corrupt line: absent, counted downstream
            return found
        last = max(wanted)
        found = {}
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
            for memory-mapping, and where the persistent cache
            lives: a directory keyed on the corpus artefacts
            holding the extracted payloads plus the record
            offset index (an uncompressed corpus copy with each
            line's byte offset), reused by later provider
            instances while the artefacts are unchanged.
            Defaults to a fresh system-temp directory per
            instance, where nothing is reused and record
            fetches stream the corpus file.  The cache needs
            ~2.4 GB for the payloads at Item 53 scale plus the
            corpus's uncompressed size for the copy, so point
            this at a disk-backed directory where temp space
            is small.

    Attributes:
        dropped_records: Cumulative count of retrieved records
            dropped by the read-time re-verification (missing
            or malformed PMID, missing abstract, PMID not
            matching the index row, or an unreadable record
            line).
        out_of_division_records: Cumulative count of retrieved
            records skipped because their primary division is
            not this provider's division.
        duplicate_content_records: Cumulative count of
            retrieved records skipped because an identical
            paper (same normalised title and abstract) was
            already served in the same retrieval under another
            PMID — one paper is one evidence line, however
            many PMIDs it is planted under.
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
        self.duplicate_content_records = 0
        self._index: Optional[_CorpusIndex] = None
        self._items_cache: dict = {}
        self._abstract_cache: dict = {}

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

    def abstract_for_pmid(self, pmid: str) -> str:
        """The abstract of the corpus record for ``pmid``.

        Used by the cross-source composite's independence
        guard, which must read the record's own text (a
        restatement of a database record often admits itself
        only in the abstract).  Returns ``""`` when the corpus
        holds no such record.  The lookup resolves the PMID to
        its row through the index's id map and fetches that one
        record (see :meth:`_CorpusIndex.fetch_records`), and is
        cached per PMID on the instance.
        """
        pmid = str(pmid).strip()
        if pmid in self._abstract_cache:
            return self._abstract_cache[pmid]
        abstract = ""
        index = self._ensure_index()
        row = index.row_for_pmid(pmid)
        if row is not None:
            record = index.fetch_records([row]).get(row)
            if record is not None and str(
                record.get("pmid") or ""
            ).strip() == pmid:
                text = record.get("abstract")
                if isinstance(text, str):
                    abstract = text
        self._abstract_cache[pmid] = abstract
        return abstract

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
        seen_content: set = set()
        for row, score in ranked:
            record = records.get(row)
            item = self._record_to_item(
                index, row, record, topic_key, score
            )
            if item is None:
                continue
            # One paper is one evidence line: identical content
            # already served in this retrieval (the same paper
            # under another PMID) is counted and skipped, so a
            # duplicated paper cannot inflate the item count.
            key = _content_key(
                str(record.get("title") or ""),
                str(record.get("abstract") or ""),
            )
            if key in seen_content:
                self.duplicate_content_records += 1
                continue
            seen_content.add(key)
            if len(items) < self.max_items:
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
        # Direction is derived from the record's own text,
        # never assumed: explicit negation language in the
        # title or abstract makes the item REFUTES (see
        # _derive_direction for the patterns and their limits).
        direction = _derive_direction(title, abstract)
        return EvidenceItem(
            item_id=f"corpus-pmid-{pmid}",
            topic_key=topic_key,
            statement=statement,
            source_id=EUROPEPMC_SOURCE_ID,
            citation=citation,
            kind=EvidenceKind.LITERATURE_REPORT,
            direction=direction,
            status=VerificationStatus.VERIFIED,
            locator=f"https://europepmc.org/article/MED/{pmid}",
            notes=(
                f"retrieved from the local Europe PMC corpus "
                f"index (Item 53 verified corpus) with TF-IDF "
                f"score {score:.3f}; PMID, abstract, and index "
                "alignment re-checked at read time under the "
                "corpus standard — not a live re-confirmation. "
                f"Direction ({direction.value}) derived from "
                "the record's own title and abstract text. "
                "Europe PMC is one independence group, so this "
                "evidence alone cannot satisfy the gate's "
                "two-group rule"
            ),
        )
