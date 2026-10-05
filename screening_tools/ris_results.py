"""RIS parsing, screening-result validation, and result-note operations."""

from __future__ import annotations

import html
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Optional, Sequence, Union


TAG = re.compile(r"^([A-Z0-9]{2}) {1,2}-\s?(.*)$")
MARKER = "全文筛选编号："

PathLike = Union[str, Path]
ScreeningResult = dict[str, Any]
ScreeningMetadata = tuple[int, str, str]


def parse_ris(text: str) -> list[str]:
    """Parse complete RIS records, preserving their lines and original order."""
    records: list[str] = []
    current: Optional[list[str]] = None

    for line in text.splitlines():
        match = TAG.match(line)
        tag = match[1] if match else None
        if tag == "TY":
            if current is not None:
                raise ValueError("RIS 上一记录缺少 ER")
            current = [line]
        elif current is not None:
            current.append(line)
            if tag == "ER":
                records.append("\n".join(current) + "\n")
                current = None
        elif line.strip():
            raise ValueError("RIS 存在记录外文本")

    if current is not None:
        raise ValueError("RIS 末条记录缺少 ER")
    if not records:
        raise ValueError("RIS 没有记录")
    return records


def fields(record: str) -> dict[str, list[str]]:
    """Collect RIS field values, retaining repeated and continuation lines."""
    values: dict[str, list[str]] = {}
    tag: Optional[str] = None

    for line in record.splitlines():
        match = TAG.match(line)
        if match:
            tag, value = match.groups()
            values.setdefault(tag, []).append(value)
        elif tag:
            values[tag][-1] += "\n" + line
    return values


def read_ris(path: PathLike) -> list[str]:
    """Read UTF-8 RIS text, accepting an optional byte-order mark."""
    return parse_ris(Path(path).expanduser().read_text(encoding="utf-8-sig"))


def atomic_write(
    path: PathLike,
    text: str,
    overwrite: bool = False,
    *,
    protected_paths: Sequence[PathLike] = (),
) -> None:
    """Write UTF-8 text atomically, protecting inputs and existing outputs."""
    destination = Path(path).expanduser()
    if any(
        destination.resolve() == Path(item).expanduser().resolve()
        for item in protected_paths
    ):
        raise ValueError("输出不能覆盖输入文件")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".screening-", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, destination)
        else:
            # Claim the output name atomically without overwriting it.
            os.link(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def numbers(value: str) -> list[int]:
    """Parse positive identifiers or page numbers such as '1,3,5-8'."""
    selected: set[int] = set()
    for part in value.split(","):
        bounds = part.strip().split("-")
        if len(bounds) == 1:
            selected.add(int(bounds[0]))
        elif len(bounds) == 2:
            start, end = map(int, bounds)
            if end < start:
                raise ValueError("编号区间倒序")
            selected.update(range(start, end + 1))
        else:
            raise ValueError("编号格式应为 1,3,5-8")

    if not selected or min(selected) < 1:
        raise ValueError("编号从 1 开始")
    return sorted(selected)


def _reading_version(result: ScreeningResult) -> dict[str, Any]:
    """Read optional version metadata, rejecting malformed nonempty values."""
    version = result.get("reading_version") or {}
    if not isinstance(version, dict):
        raise ValueError("reading_version 应为 JSON 对象")
    return version


def _evidence_items(result: ScreeningResult) -> list[dict[str, Any]]:
    """Read optional evidence entries, with clear errors for invalid types."""
    evidence = result.get("evidence") or []
    if not isinstance(evidence, list) or any(
        not isinstance(item, dict) for item in evidence
    ):
        raise ValueError("evidence 应为 JSON 对象列表")
    return evidence


def validate_result(result: ScreeningResult) -> None:
    """Validate the recorded status, decision, and supporting metadata.

    These checks verify the result structure. They do not verify that the
    reported reading occurred or that a screening decision is correct.
    """
    if not isinstance(result, dict):
        raise ValueError("筛选结果应为 JSON 对象")
    required_fields = ["id", "status", "decision", "code", "reason", "admission"]
    for key in required_fields:
        if not str(result.get(key) or "").strip():
            raise ValueError(f"缺少结果字段：{key}")

    try:
        identifier = int(result["id"])
    except (TypeError, ValueError) as exc:
        raise ValueError("id 应为可转换为整数的编号") from exc
    if identifier < 1:
        raise ValueError("id 从 1 开始")

    status = result["status"]
    if status not in ["completed", "pending_fulltext"]:
        raise ValueError("status 应为 completed 或 pending_fulltext")
    if result["decision"] not in ["保留", "排除", "存疑"]:
        raise ValueError("判定不合法")
    if result["code"] == "E2":
        raise ValueError("E2 不用于该结果格式；全文缺失请标为 pending_fulltext")

    if status == "pending_fulltext":
        if (result["decision"], result["code"]) != ("存疑", "全文缺失"):
            raise ValueError("未取得完整全文应为存疑／全文缺失")
        if result.get("fulltext_read_complete") is not False:
            raise ValueError("待全文记录应明确 fulltext_read_complete=false")
        return

    if result["code"] == "全文缺失":
        raise ValueError("全文缺失不能计为已完成筛选")
    if result.get("fulltext_read_complete") is not True:
        raise ValueError("已完成记录须明确全文读完")
    version = _reading_version(result)
    if not version.get("source_url") or not version.get("version"):
        raise ValueError("已完成记录缺少全文来源或阅读版本")
    if not result.get("coverage_summary"):
        raise ValueError("已完成记录缺少阅读覆盖说明")

    evidence = _evidence_items(result)
    if not evidence or any(
        not item.get("quote") or not item.get("locator")
        for item in evidence
    ):
        raise ValueError("已完成记录须有原文短摘及定位")
    if result["decision"] == "保留" and result["code"] != "无":
        raise ValueError("保留记录代码应为无")
    if result["decision"] == "排除":
        code = result["code"]
        if not isinstance(code, str) or not re.fullmatch(r"E4|S[1-4]|C[1-3]", code):
            raise ValueError("排除代码不合法")


def plain(value: Any) -> str:
    """Convert nested result metadata to the original Chinese note format."""
    if value is None:
        return "未知／未记录"
    if value is True:
        return "是"
    if value is False:
        return "否"
    if isinstance(value, dict):
        return "；".join(f"{key}：{plain(item)}" for key, item in value.items())
    if isinstance(value, list):
        return "；".join(plain(item) for item in value)
    return str(value)


def result_note(result: ScreeningResult) -> str:
    """Write the decision, source, and evidence as a concise HTML note."""
    validate_result(result)
    lines = [
        f"{MARKER}{int(result['id']):03d}",
        f"全文筛选状态：{result['status']}",
        f"判定：{result['decision']}",
        f"代码：{result['code']}",
        f"依据：{result['reason']}",
        f"准入条件：{result['admission']}",
        f"全文通读完成：{plain(result.get('fulltext_read_complete'))}",
    ]
    version = _reading_version(result)
    labels = {
        "source_url": "全文来源",
        "version": "阅读版本",
        "date": "版本日期",
        "local_fulltext": "保存全文",
        "local_pdf": "补读PDF",
    }
    for key, label in labels.items():
        if key in version and version[key] is not None:
            lines.append(f"{label}：{plain(version[key])}")
    if version and not version.get("date"):
        lines.append("版本日期：未知／未取得阅读版")

    for index, evidence in enumerate(_evidence_items(result), 1):
        lines.append(
            f"证据{index}：{evidence['quote']}\n定位：{evidence['locator']}"
        )

    optional_sections = [
        ("review_notes", "复核备注"),
        ("limitations", "局限"),
        ("follow_up", "全文补查与后续"),
    ]
    for key, label in optional_sections:
        if result.get(key):
            lines.append(f"{label}：\n{plain(result[key])}")

    paragraphs = [
        "<p>" + html.escape(line).replace("\n", "<br/>") + "</p>"
        for line in lines
    ]
    return "<div>" + "".join(paragraphs) + "</div>"


def add_note(record: str, result: ScreeningResult) -> str:
    """Append one screening note immediately before the RIS end marker."""
    note = result_note(result)
    lines = record.rstrip("\n").splitlines()
    final_tag = TAG.match(lines[-1]) if lines else None
    if final_tag is None or final_tag[1] != "ER":
        raise ValueError("记录末尾缺少 ER")
    if screening_metadata(record) is not None:
        raise ValueError("记录已有全文筛选备注；复筛请从原始记录重新生成")
    return "\n".join(lines[:-1] + ["N1  - " + note, lines[-1]]) + "\n"


def screening_metadata(record: str) -> Optional[ScreeningMetadata]:
    """Extract the identifier, status, and decision from one screening note."""
    matches: list[ScreeningMetadata] = []
    for note in fields(record).get("N1", []):
        if not re.search(MARKER + r"\d+", note):
            continue
        text = html.unescape(re.sub(r"<[^>]*>", "\n", note))
        identifier = re.search(MARKER + r"(\d+)", text)
        status = re.search(r"全文筛选状态：(completed|pending_fulltext)", text)
        decision = re.search(r"(?:^|\n)判定：(保留|排除|存疑)", text)
        if not identifier or not status or not decision:
            raise ValueError("全文筛选备注不完整")
        matches.append((int(identifier[1]), status[1], decision[1]))

    if len(matches) > 1:
        raise ValueError("同一记录含多份全文筛选结果")
    return matches[0] if matches else None


def original_fields(entry: str) -> dict[str, list[str]]:
    """Return bibliographic fields with screening notes removed for comparison."""
    data = fields(entry)
    data["N1"] = [note for note in data.get("N1", []) if MARKER not in note]
    return data


def split_records(records: Sequence[str], identifiers: Sequence[int]) -> dict[int, str]:
    """Select one-based record identifiers without changing the record text."""
    selected = sorted(set(identifiers))
    if not selected or min(selected) < 1:
        raise ValueError("编号从 1 开始")
    if max(selected) > len(records):
        raise ValueError(f"仅有 {len(records)} 条 RIS 记录")
    return {identifier: records[identifier - 1] for identifier in selected}


def read_result_files(work_dir: PathLike) -> list[str]:
    """Read one record from each *.result.ris file in filename order."""
    incoming: list[str] = []
    for path in sorted(Path(work_dir).expanduser().glob("*.result.ris")):
        records = read_ris(path)
        if len(records) != 1:
            raise ValueError(f"单篇结果条数不符：{path.name}")
        if screening_metadata(records[0]) is None:
            raise ValueError(f"缺少全文筛选备注：{path.name}")
        incoming.append(records[0])
    return incoming


def merge_records(
    incoming_records: Sequence[str],
    identifiers: Sequence[int],
    base_records: Sequence[str] = (),
) -> list[str]:
    """Merge results by identifier while protecting original bibliographic data.

    Incoming records replace matching base results only when their original
    bibliographic fields agree. The returned records follow identifier order.
    """
    indexed: dict[int, str] = {}
    for entry in base_records:
        metadata = screening_metadata(entry)
        if metadata is None or metadata[0] in indexed:
            raise ValueError("原总RIS的筛选编号缺失或重复")
        indexed[metadata[0]] = entry

    incoming: set[int] = set()
    for entry in incoming_records:
        metadata = screening_metadata(entry)
        if metadata is None:
            raise ValueError("缺少全文筛选备注")
        identifier = metadata[0]
        if identifier in incoming:
            raise ValueError(f"编号重复：{identifier}")
        if identifier in indexed:
            if original_fields(indexed[identifier]) != original_fields(entry):
                raise ValueError(f"复核记录 {identifier} 的原书目字段发生变化")
        incoming.add(identifier)
        indexed[identifier] = entry

    if not incoming:
        raise ValueError("没有单篇结果可供合并")
    expected = sorted(set(identifiers))
    if sorted(indexed) != expected:
        raise ValueError(f"结果编号与预期不一致；已有 {sorted(indexed)}")
    return [indexed[identifier] for identifier in expected]


def select_records(records: Sequence[str], status: str) -> list[str]:
    """Select one decision category, treating pending full texts as 待获取."""
    selected: list[str] = []
    for entry in records:
        metadata = screening_metadata(entry)
        if metadata is None:
            raise ValueError("输入包含没有全文筛选备注的记录")
        actual = "待获取" if metadata[1] == "pending_fulltext" else metadata[2]
        if actual == status:
            selected.append(entry)
    return selected
