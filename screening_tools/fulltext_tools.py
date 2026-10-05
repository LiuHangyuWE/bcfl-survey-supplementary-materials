#!/usr/bin/env python3
"""Command-line utilities for RIS screening results and PDF reading files.

The commands retain the original Chinese result fields and note format.
Screening decisions are supplied as JSON; this tool does not make them.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Optional, Sequence

if __package__:
    from . import pdf_tools, ris_results
else:
    import pdf_tools
    import ris_results


def path_argument(value: str) -> Path:
    """Accept relative or absolute paths and expand a leading '~'."""
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    """Define the available commands and their arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("doctor", help="检查现有运行环境")

    sub = commands.add_parser("split", help="按输入RIS顺序拆出记录")
    sub.add_argument("input", type=path_argument)
    sub.add_argument("--ids", required=True)
    sub.add_argument("--work-dir", type=path_argument, required=True)

    sub = commands.add_parser(
        "record", help="将单篇结果写入N1备注；--result - 读标准输入"
    )
    sub.add_argument("input", type=path_argument)
    sub.add_argument("--result", required=True)
    sub.add_argument("--output", type=path_argument, required=True)

    sub = commands.add_parser("merge", help="收齐单篇*.result.ris后按编号合并")
    sub.add_argument("--work-dir", type=path_argument, required=True)
    sub.add_argument("--ids", required=True)
    sub.add_argument("--output", type=path_argument, required=True)
    sub.add_argument("--replace", action="store_true")
    sub.add_argument(
        "--base", type=path_argument, help="加载原总RIS，仅更新本次收到的编号"
    )

    sub = commands.add_parser("export", help="按筛选状态导出分类RIS")
    sub.add_argument("input", type=path_argument)
    sub.add_argument(
        "--status", required=True, choices=["保留", "排除", "存疑", "待获取"]
    )
    sub.add_argument("--output", type=path_argument, required=True)

    sub = commands.add_parser("prepare", help="提取PDF，按字符长度分段")
    sub.add_argument("input", type=path_argument)
    sub.add_argument("--work-dir", type=path_argument, required=True)
    sub.add_argument(
        "--chunk-chars", type=int, default=pdf_tools.DEFAULT_CHUNK_CHARS
    )

    sub = commands.add_parser("render", help="按需渲染关键页")
    sub.add_argument("input", type=path_argument)
    sub.add_argument("--pages", required=True)
    sub.add_argument("--work-dir", type=path_argument, required=True)
    return parser


def run_command(args: argparse.Namespace) -> None:
    """Run the selected record or PDF operation."""
    if args.command == "doctor":
        report = {
            "python": sys.executable,
            "pypdf_available": importlib.util.find_spec("pypdf") is not None,
            "tools": {
                name: shutil.which(name)
                for name in ["curl", "pdftotext", "pdftoppm", "pdfinfo"]
            },
            "note": "仅检查现有依赖；此命令不安装依赖或更改运行环境。",
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.command == "split":
        selected = ris_results.split_records(
            ris_results.read_ris(args.input), ris_results.numbers(args.ids)
        )
        paths = [args.work_dir / f"{identifier:03d}.ris" for identifier in selected]
        if any(path.exists() for path in paths):
            raise FileExistsError("工作目录已有同名记录，请使用独立临时目录")
        for path, record in zip(paths, selected.values()):
            ris_results.atomic_write(path, record, protected_paths=[args.input])
        print(f"已拆出 {len(selected)} 条记录；原始 RIS 未修改")
    elif args.command == "record":
        records = ris_results.read_ris(args.input)
        if len(records) != 1:
            raise ValueError("record 每次只处理一篇")
        if args.result == "-":
            text = sys.stdin.read()
            protected = [args.input]
        else:
            result_path = path_argument(args.result)
            text = result_path.read_text(encoding="utf-8-sig")
            protected = [args.input, result_path]
        result = json.loads(text)
        ris_results.atomic_write(
            args.output, ris_results.add_note(records[0], result),
            protected_paths=protected,
        )
        print(f"已生成单篇结果 {int(result['id']):03d}")
    elif args.command == "merge":
        base_records = ris_results.read_ris(args.base) if args.base else []
        merged = ris_results.merge_records(
            ris_results.read_result_files(args.work_dir),
            ris_results.numbers(args.ids),
            base_records,
        )
        protected = list(args.work_dir.glob("*.result.ris"))
        if args.base and not args.replace:
            protected.append(args.base)
        ris_results.atomic_write(
            args.output, "\n".join(merged), args.replace,
            protected_paths=protected,
        )
        print(f"已按编号合并 {len(merged)} 篇")
    elif args.command == "export":
        selected = ris_results.select_records(
            ris_results.read_ris(args.input), args.status
        )
        ris_results.atomic_write(
            args.output, "\n".join(selected), protected_paths=[args.input]
        )
        print(f"已导出 {len(selected)} 篇{args.status}记录")
    elif args.command == "prepare":
        report = pdf_tools.prepare_pdf(args.input, args.work_dir, args.chunk_chars)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.command == "render":
        pdf_tools.render_pages(
            args.input, ris_results.numbers(args.pages), args.work_dir
        )
        print("选页已渲染；图片属于临时阅读产物")


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Run a command, reporting expected input or external-tool failures."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run_command(args)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"失败：{exc}\n")


if __name__ == "__main__":
    main()
