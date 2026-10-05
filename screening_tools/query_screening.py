"""Check IEEE Xplore RIS records against the review's search query."""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
from dataclasses import dataclass
import hashlib
import html
import io
import os
from pathlib import Path
import re
import tempfile
import unicodedata


FL_TERMS = (
    "federated learning",
    "federated machine learning",
    "federated edge learning",
    "federated optimization",
)
BLOCKCHAIN_TERMS = ("blockchain", "block chain", "distributed ledger")
SEARCH_FIELDS = ("TI", "AB", "KW")
SOURCE_NAME = "IEEE Xplore"
QUERY = (
    '("federated learning" OR "federated machine learning" OR '
    '"federated edge learning" OR "federated optimization") AND '
    '(blockchain* OR "block chain*" OR "distributed ledger*")'
)
RIS_TAG = re.compile(r"([A-Z0-9]{2}) {1,2}- ?(.*)")
RECORD_START = re.compile(rb"(?m)^TY {1,2}- ")


@dataclass
class ScreeningResult:
    """Selected records and the evidence behind each decision."""

    retained: list[bytes]
    excluded: list[bytes]
    decisions: list[dict]
    format_variants: list[dict]
    other_candidates: list[dict]
    counts: Counter[str]


def split_records(data: bytes) -> list[bytes]:
    """Split a UTF-8 RIS file without rewriting its record bytes."""
    offset = 3 if data.startswith(b"\xef\xbb\xbf") else 0
    starts = [match.start() + offset for match in RECORD_START.finditer(data[offset:])]
    if not starts or starts[0] != offset:
        raise ValueError("Expected a RIS file beginning with a TY record.")
    starts[0] = 0
    ends = starts[1:] + [len(data)]
    records = [data[start:end] for start, end in zip(starts, ends)]
    for number, record in enumerate(records, 1):
        fields = parse_fields(record)
        if len(fields["TY"]) != 1 or len(fields["ER"]) != 1:
            raise ValueError(f"Record {number} must contain one TY and one ER field.")
        if not fields["TI"]:
            raise ValueError(f"Record {number} has no TI title field.")
    return records


def parse_fields(record: bytes) -> defaultdict[str, list[str]]:
    """Read repeated RIS fields and join their continuation lines."""
    fields = defaultdict(list)
    tag = None
    for line in record.decode("utf-8-sig").splitlines():
        match = RIS_TAG.fullmatch(line)
        if match:
            tag = match[1]
            fields[tag].append(match[2].strip())
        elif tag and line.strip():
            fields[tag][-1] += " " + line.strip()
    return fields


def normalize(text: str) -> str:
    """Normalize Unicode, case, hyphens, and whitespace."""
    text = unicodedata.normalize("NFKC", html.unescape(text)).casefold()
    text = text.replace("\u00ad", "")
    text = re.sub(r"[\u2010-\u2015\u2212-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def match_terms(fields: dict, *, normalized: bool) -> list[list[dict]]:
    """Match each phrase within one field value; groups may span fields."""
    evidence = [[], []]
    for field in SEARCH_FIELDS:
        for value in fields.get(field, []):
            text = normalize(value) if normalized else value.lower()
            for group, terms in enumerate((FL_TERMS, BLOCKCHAIN_TERMS)):
                for term in terms:
                    pattern = (
                        r"\b" + re.escape(term) + (r"\b" if group == 0 else r"\w*")
                    )
                    found = re.search(pattern, text) if normalized else term in text
                    if found:
                        evidence[group].append({
                            "field": field,
                            "query_term": term + ("*" if group else ""),
                            "matched_text": found.group(0) if normalized else term,
                        })
    return evidence


def screen_records(records: list[bytes]) -> ScreeningResult:
    """Exclude only IEEE Xplore records that fail the normalized query."""
    retained, excluded, decisions, variants, other_candidates = [], [], [], [], []
    counts = Counter()
    for number, record in enumerate(records, 1):
        fields = parse_fields(record)
        literal = match_terms(fields, normalized=False)
        normalized = match_terms(fields, normalized=True)
        literal_ok, normalized_ok = all(literal), all(normalized)
        ieee = SOURCE_NAME in fields["DB"] or SOURCE_NAME in fields["DP"]
        if normalized_ok:
            reason = "符合检索式"
        elif not any(normalized):
            reason = "两组均未命中"
        elif not normalized[0]:
            reason = "未命中联邦学习组"
        else:
            reason = "未命中区块链组"
        remove = ieee and not normalized_ok
        decision = {
            "original_position": number,
            "title": fields["TI"][0],
            "doi": fields["DO"],
            "source_db": fields["DB"],
            "source_dp": fields["DP"],
            "ieee_xplore": ieee,
            "literal_match": literal_ok,
            "normalized_match": normalized_ok,
            "decision": "remove" if remove else "retain",
            "reason": reason,
            "match_evidence": normalized,
        }
        decisions.append(decision)
        counts["total_before"] += 1
        counts["ieee_before" if ieee else "other_before"] += 1
        if not literal_ok:
            counts["literal_fail_all"] += 1
            counts["literal_fail_ieee" if ieee else "literal_fail_other"] += 1
        if remove:
            excluded.append(record)
            counts["removed_ieee"] += 1
            counts[reason] += 1
        else:
            retained.append(record)
        if not literal_ok and normalized_ok:
            variants.append({
                **decision, "abstract": fields["AB"], "keywords": fields["KW"],
            })
        if not ieee and not literal_ok:
            other_candidates.append({
                **decision, "abstract": fields["AB"], "keywords": fields["KW"],
            })
    counts["total_after"] = len(retained)
    counts["ieee_after"] = counts["ieee_before"] - counts["removed_ieee"]
    counts["other_after"] = counts["other_before"]
    return ScreeningResult(
        retained, excluded, decisions, variants, other_candidates, counts,
    )


def make_summary(
    result: ScreeningResult, data: bytes, source: Path, *, apply_requested: bool,
) -> dict:
    """Build an audit summary from the supplied input and computed counts."""
    return {
        "source": str(source),
        "counts": dict(result.counts),
        "before_sha256": hashlib.sha256(data).hexdigest(),
        "after_sha256": hashlib.sha256(b"".join(result.retained)).hexdigest(),
        "apply_requested": apply_requested,
    }


def make_report(result: ScreeningResult) -> str:
    """Summarize the number of retained and excluded records."""
    counts = result.counts
    lines = [
        f"Input records: {counts['total_before']}",
        f"Retained: {counts['total_after']}",
        f"Excluded (IEEE Xplore): {counts['removed_ieee']}",
    ]
    lines.extend(
        f"  {reason}: {counts[reason]}"
        for reason in ("未命中联邦学习组", "未命中区块链组", "两组均未命中")
        if counts[reason]
    )
    return "\n".join(lines) + "\n"


def decisions_csv(result: ScreeningResult) -> bytes:
    """Write one compact row per record, including the matching fields."""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "record", "title", "doi", "source", "decision", "reason",
        "federated_learning_match", "blockchain_match",
    ])
    for row in result.decisions:
        matches = [
            "; ".join(dict.fromkeys(
                f"{item['field']}: {item['matched_text']}" for item in group
            ))
            for group in row["match_evidence"]
        ]
        reason = (
            row["reason"] if row["ieee_xplore"] else "Source outside IEEE Xplore scope"
        )
        writer.writerow([
            row["original_position"], row["title"], "; ".join(row["doi"]),
            "; ".join(row["source_db"] or row["source_dp"]),
            row["decision"], reason, *matches,
        ])
    return output.getvalue().encode("utf-8-sig")


def write_atomic(path: Path, data: bytes, *, replace: bool = False) -> None:
    """Write a complete file; refuse an existing destination by default."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".screening-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_results(
    result: ScreeningResult,
    data: bytes,
    source: Path,
    output_dir: Path,
    *,
    apply: bool = False,
) -> dict:
    """Save RIS results, a decision table, and a short summary."""
    summary = make_summary(result, data, source, apply_requested=apply)
    documents = {
        "retained.ris": b"".join(result.retained),
        "excluded.ris": b"".join(result.excluded),
        "decisions.csv": decisions_csv(result),
        "summary.txt": make_report(result).encode("utf-8"),
    }
    if apply:
        documents["input_backup.ris"] = data
    paths = [output_dir / name for name in documents]
    for path in paths:
        if path.resolve() == source.resolve():
            raise ValueError("An output path would overwrite the input RIS.")
        if path.exists():
            raise FileExistsError(f"Output already exists: {path}")
    if source.read_bytes() != data:
        raise ValueError("The input changed while it was being screened.")
    for name, content in documents.items():
        write_atomic(output_dir / name, content)
    if apply:
        if source.read_bytes() != data:
            raise ValueError("The input changed; it has not been replaced.")
        write_atomic(source, b"".join(result.retained), replace=True)
    return summary
