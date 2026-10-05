"""Prepare PDF reading files with existing Poppler tools and optional pypdf."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Optional, Sequence, Union


DEFAULT_CHUNK_CHARS = 14000
RENDER_SIZE = 1600
PathLike = Union[str, Path]


def _write_chunk(path: Path, text: str, source: Path) -> None:
    """Write one UTF-8 chunk atomically, refusing existing files or the input."""
    if path.resolve() == source.resolve():
        raise ValueError("输出不能覆盖输入文件")
    descriptor, temporary = tempfile.mkstemp(prefix=".screening-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _page_count_check(source: Path, extracted_count: int) -> dict[str, Any]:
    """Compare declared, enumerated, and extracted PDF page counts if possible."""
    verdict = "UNAVAILABLE"
    reader_count: Optional[int] = None
    error: Optional[str] = None
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(source), strict=True)
        reader_count = len(reader.pages)
        enumerated_count = sum(1 for _ in reader.pages)
        declared_count = int(reader.trailer["/Root"]["/Pages"]["/Count"])
        if declared_count == enumerated_count == reader_count == extracted_count:
            verdict = "PASS"
        else:
            verdict = "FAIL"
    except Exception as exc:
        # Extraction remains usable when this optional structural check fails.
        error = f"{type(exc).__name__}: {exc}"
    return {
        "structural_check": verdict,
        "reader_pages": reader_count,
        "error": error,
    }


def prepare_pdf(
    input_path: PathLike,
    output_dir: PathLike,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
) -> dict[str, Any]:
    """Extract PDF text by page and write numbered character-length chunks.

    Chunk headings retain PDF page numbers and character offsets. The returned
    report describes extraction and structural checks, not reading completion.
    Existing chunk files are never overwritten.
    """
    source = Path(input_path).expanduser()
    with source.open("rb") as stream:
        signature = stream.read(1024)
    if source.suffix.lower() != ".pdf" or b"%PDF-" not in signature:
        raise ValueError("输入不是已下载完成的 PDF；HTML 全文请直接读原HTML及图表")

    destination = Path(output_dir).expanduser()
    destination.mkdir(parents=True, exist_ok=True)
    extractor = shutil.which("pdftotext")
    if not extractor:
        raise ValueError("缺少 pdftotext；请检查现有 Poppler 配置或使用替代读取器")
    if chunk_chars < 1000:
        raise ValueError("chunk-chars 至少 1000")
    completed = subprocess.run(
        [extractor, "-layout", str(source), "-"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    if completed.returncode:
        raise ValueError("文本提取失败：" + completed.stderr[:600])
    pages = completed.stdout.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()

    structural = _page_count_check(source, len(pages))
    empty_pages: list[int] = []
    chunks = 0
    for page_number, text in enumerate(pages, 1):
        if not text.strip():
            empty_pages.append(page_number)
        for offset in range(0, max(len(text), 1), chunk_chars):
            chunks += 1
            end = min(offset + chunk_chars, len(text))
            heading = (
                f"【PDF页 {page_number}/{len(pages)}；"
                f"字符 {offset}:{end}/{len(text)}】\n"
            )
            _write_chunk(
                destination / f"read_{chunks:04d}.txt",
                heading + text[offset:end],
                source,
            )

    return {
        "structural_check": structural["structural_check"],
        "reader_pages": structural["reader_pages"],
        "extracted_pages": len(pages),
        "chunks": chunks,
        "empty_text_pages": empty_pages,
        "error": structural["error"],
        "extractor_warning": completed.stderr.strip() or None,
        "note": "结构PASS不证明通读完成；空页、公式、图像算法与关键表格须看原页。",
    }


def render_pages(
    input_path: PathLike,
    pages: Sequence[int],
    output_dir: PathLike,
) -> list[Path]:
    """Render selected PDF pages at size 1600, refusing existing PNG outputs."""
    tool = shutil.which("pdftoppm")
    if not tool:
        raise ValueError("缺少 pdftoppm；请检查现有 Poppler 配置")
    source = Path(input_path).expanduser()
    directory = Path(output_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    selected = sorted(set(pages))
    if not selected or min(selected) < 1:
        raise ValueError("编号从 1 开始")

    outputs: list[Path] = []
    for page in selected:
        output = directory / f"page_{page:03d}.png"
        if output.resolve() == source.resolve():
            raise ValueError("输出不能覆盖输入文件")
        if output.exists():
            raise FileExistsError("选页图片已存在")
        with tempfile.TemporaryDirectory(prefix=".render-", dir=directory) as temporary:
            staging = Path(temporary) / "page"
            subprocess.run(
                [
                    tool,
                    "-f", str(page),
                    "-l", str(page),
                    "-singlefile",
                    "-scale-to", str(RENDER_SIZE),
                    "-png",
                    str(source),
                    str(staging),
                ],
                check=True,
                timeout=120,
                capture_output=True,
            )
            os.link(staging.with_suffix(".png"), output)
        outputs.append(output)
    return outputs
