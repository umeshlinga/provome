"""The human-labelling kit for the public benchmark.

Item 51's adopted standard says the automated grader must
itself be graded against blind human labels.  The public
benchmark (:mod:`provome.public_benchmark`) has carried that
gap honestly since v1: its confirmer baseline is an internal
calibration stand-in, and every report says it is NOT a human
baseline.  This module is how real labels are gathered:

* **Blind export.**  :func:`build_labelling_pack` exports the
  frozen probe set as a labelling pack — one record per probe
  holding ONLY the probe id, its division, the question, and
  the evidence items a labeller may inspect (exactly the items
  the scored run's own providers serve for that probe).  The
  expected verdicts, the ground-truth notes, and the checking
  sources are SEALED: they exist in the probe set, and they
  appear nowhere in the exported pack.  The probe-set hash IS
  recorded in the pack header, so labels gathered from the
  pack bind to the exact frozen set.
* **Ingest and scoring.**  A labels file (JSON: labeller
  identity, date, the pack's probe-set hash, and a
  probe-id → label map) is parsed by :func:`parse_labels` /
  :func:`load_labels_file` and scored by passing the result
  to :func:`provome.public_benchmark.run_benchmark` as
  ``human_labels`` — or via the :func:`score_labels`
  convenience here.  A labels file whose hash does not match
  the frozen set is refused loudly.  A partial label set is
  scored on the labelled subset only, with the labelled
  denominator stated; it is never silently treated as
  complete.
* **No fabricated labels.**  This module ships no labels.  The
  first real labels will be a named person's, gathered blind
  from this pack, and the report section they produce names
  that person and the count.

The pack is written two ways: JSONL (one blind record per
line, behind a header line carrying the probe-set hash) for
tooling, and a Markdown sheet a human can work through in one
sitting — the labeller instructions are the sheet's header.

Run it to write a pack into a directory:

    python -m provome.labelling [output-dir]

Research software.  Not for clinical use.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

# The benchmark's own fixture factories: the pack must show a
# labeller exactly the evidence the scored run used, so the
# providers are the benchmark's, not a second copy of them.
from .public_benchmark import (
    BENCHMARK_VERSION,
    HumanLabels,
    HumanLabelsError,
    LABEL_VALUES,
    PublicBenchmarkReport,
    _default_factories,
    probe_set_hash,
    probes,
    run_benchmark,
)

__all__ = [
    "INSTRUCTIONS",
    "build_labelling_pack",
    "load_labels_file",
    "pack_to_jsonl",
    "pack_to_markdown",
    "parse_labels",
    "score_labels",
    "write_labelling_pack",
    "main",
]


#: The labeller instructions.  They are the header of the
#: exported Markdown sheet and the ``instructions`` field of
#: the pack, so every labeller works from the same text.
INSTRUCTIONS = """\
# Provome public benchmark — labelling sheet

## How to label (read this first)

You are labelling BLIND. For each probe below you see the
question and the evidence items a system could inspect —
nothing else. The system's answer, the expected verdict, and
the notes a checker would use are sealed: they are not in
this pack, and they are revealed only when your labels are
scored.

For each probe, choose exactly one label:

- **answer** — the evidence shown is sufficient, trustworthy,
  and on topic: a careful scientist could answer the question
  from it, citing it.
- **decline** — the evidence is missing, unverifiable,
  off-topic, for the wrong division or species, or about
  something that does not exist: the only honest response is
  to decline.
- **disclose** — the evidence supports an answer, BUT it also
  contains genuine disagreement or a qualification that must
  be surfaced with the answer, never hidden.

Judge the evidence in front of you, not what you know from
outside it: if the shown evidence would not let you cite a
claim, label **decline** even if you believe the claim is
true in the world.

There are 49 probes; most labellers finish in one sitting of
about an hour. Label every probe if you can. A partial set is
scored on the labelled probes only, with the smaller
denominator stated — it is never silently treated as
complete.

## Recording your labels

Save a JSON file in this shape:

    {
      "labeller": "<your name>",
      "date": "<YYYY-MM-DD>",
      "probe_set_hash": "<the hash in this sheet's header>",
      "labels": {"<probe_id>": "answer", "<probe_id>": "decline"}
    }

Each label is one of: answer, decline, disclose. The hash
binds your labels to this exact frozen probe set; labels
carrying a different set's hash are refused at scoring time.
"""


# ---------------------------------------------------------------------
# Blind export
# ---------------------------------------------------------------------


def build_labelling_pack(probe_set=None, provider_factories=None) -> dict:
    """Build the blind labelling pack for a probe set.

    Each record carries ONLY the probe id, the division, the
    question, and the evidence items the benchmark's own
    providers serve for that probe — the items a labeller may
    inspect.  Expected verdicts, ground truth, and source URLs
    are not read into the pack at all: what was never copied
    cannot leak.  The pack header records the probe-set hash
    so labels bind to the exact frozen set.
    """
    probe_set = tuple(probe_set) if probe_set is not None else probes()
    factories = _default_factories()
    if provider_factories:
        factories.update(provider_factories)
    records = []
    for probe in probe_set:
        provider = factories[probe.division]()
        topic = provider.topic_for_question(probe.question)
        items = (
            provider.items_for_topic(topic) if topic is not None else ()
        )
        records.append(
            {
                "probe_id": probe.probe_id,
                "division": probe.division,
                "question": probe.question,
                "evidence": [
                    {
                        "statement": item.statement,
                        "citation": item.citation,
                        "source_id": item.source_id,
                        "direction": item.direction.value,
                        "status": item.status.value,
                    }
                    for item in items
                ],
            }
        )
    return {
        "pack_type": "provome-public-benchmark-labelling-pack",
        "benchmark_version": BENCHMARK_VERSION,
        "probe_set_hash": probe_set_hash(probe_set),
        "probe_count": len(probe_set),
        "instructions": INSTRUCTIONS,
        "records": records,
    }


def pack_to_jsonl(pack: dict) -> str:
    """Serialise a pack as JSONL: a header line carrying the
    probe-set hash, then one blind record per line."""
    header = {
        "pack_type": pack["pack_type"],
        "benchmark_version": pack["benchmark_version"],
        "probe_set_hash": pack["probe_set_hash"],
        "probe_count": pack["probe_count"],
    }
    lines = [json.dumps(header, sort_keys=True)]
    for record in pack["records"]:
        lines.append(json.dumps(record, sort_keys=True))
    return "\n".join(lines) + "\n"


def pack_to_markdown(pack: dict) -> str:
    """Render a pack as the human labelling sheet: the
    instructions, the pack header (with the probe-set hash a
    labels file must carry), then one section per probe with a
    blank for the label."""
    lines = [pack["instructions"].rstrip(), ""]
    lines.append(
        f"Pack: {pack['benchmark_version']} · "
        f"probes: {pack['probe_count']} · "
        f"probe-set hash: `{pack['probe_set_hash']}`"
    )
    for record in pack["records"]:
        lines.append("")
        lines.append(f"## {record['probe_id']} — {record['division']}")
        lines.append("")
        lines.append(record["question"])
        lines.append("")
        if record["evidence"]:
            lines.append("Evidence you may inspect:")
            for index, item in enumerate(record["evidence"], start=1):
                lines.append(
                    f"{index}. [{item['status']}/{item['direction']}] "
                    f"{item['statement']}"
                )
                lines.append(
                    f"   Source: {item['citation']} ({item['source_id']})"
                )
        else:
            lines.append(
                "Evidence you may inspect: (none — no evidence "
                "items are held for this question)"
            )
        lines.append("")
        lines.append(
            "Your label (answer / decline / disclose): ____________"
        )
    return "\n".join(lines) + "\n"


def write_labelling_pack(pack: dict, out_dir) -> tuple:
    """Write a pack to ``out_dir`` as JSONL + Markdown.

    Returns ``(jsonl_path, markdown_path)``.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jsonl_path = out / "labelling_pack.jsonl"
    markdown_path = out / "labelling_sheet.md"
    jsonl_path.write_text(pack_to_jsonl(pack), encoding="utf-8")
    markdown_path.write_text(pack_to_markdown(pack), encoding="utf-8")
    return (jsonl_path, markdown_path)


# ---------------------------------------------------------------------
# Label ingest
# ---------------------------------------------------------------------


def parse_labels(text: str) -> HumanLabels:
    """Parse a labels file's JSON text into :class:`HumanLabels`.

    The file must be a JSON object with ``labeller``, ``date``,
    ``probe_set_hash``, and ``labels`` (a probe-id → label
    map).  Anything malformed raises :class:`HumanLabelsError`;
    whether the hash matches the frozen set and the named
    probes exist is checked at scoring time by
    :func:`provome.public_benchmark.run_benchmark`, which
    refuses loudly on a mismatch.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HumanLabelsError(f"labels file is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        raise HumanLabelsError("labels file must be a JSON object")
    for field_name in ("labeller", "date", "probe_set_hash", "labels"):
        if field_name not in payload:
            raise HumanLabelsError(
                f"labels file is missing the {field_name!r} field"
            )
    raw_labels = payload["labels"]
    if not isinstance(raw_labels, dict):
        raise HumanLabelsError(
            "labels must be an object mapping probe ids to labels"
        )
    pairs = []
    for probe_id, label in raw_labels.items():
        if not isinstance(label, str) or label not in LABEL_VALUES:
            raise HumanLabelsError(
                f"label for probe {probe_id!r} is {label!r}; "
                f"labels must be one of {LABEL_VALUES}"
            )
        pairs.append((str(probe_id), label))
    return HumanLabels(
        labeller=payload["labeller"],
        date=payload["date"],
        probe_set_hash=payload["probe_set_hash"],
        labels=tuple(sorted(pairs)),
    )


def load_labels_file(path) -> HumanLabels:
    """Read and parse a labels file from disk."""
    return parse_labels(Path(path).read_text(encoding="utf-8"))


def score_labels(human_labels: HumanLabels,
                 probe_set=None) -> PublicBenchmarkReport:
    """Run the benchmark with one labeller's labels and return
    the report; its ``human_baseline`` section is the score."""
    return run_benchmark(probe_set=probe_set, human_labels=human_labels)


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    """Write a labelling pack into a directory (default:
    ``labelling-pack`` under the working directory)."""
    args = list(argv) if argv is not None else []
    out_dir = args[0] if args else "labelling-pack"
    pack = build_labelling_pack()
    jsonl_path, markdown_path = write_labelling_pack(pack, out_dir)
    print(f"Labelling pack written ({pack['probe_count']} probes, blind):")
    print(f"  {jsonl_path}")
    print(f"  {markdown_path}")
    print(f"  probe-set hash: {pack['probe_set_hash']}")
    print(
        "The pack contains no expected verdicts, ground truth, "
        "or checking sources."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
