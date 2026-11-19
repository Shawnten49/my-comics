#!/usr/bin/env python3
"""
递归把 *_rendered.png 转换为 Canvas 原生尺寸的 WebP 100。

示例：
    # 预览
    python3 convert_rendered_webp.py \
      "<项目根目录>/download" \
      --dry-run

    # 转换并保留 PNG
    python3 convert_rendered_webp.py \
      "<项目根目录>/download"

    # 转换成功并验证后删除 PNG
    python3 convert_rendered_webp.py \
      "<项目根目录>/download" \
      --remove-source
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageStat, features

from comic_web.runtime_settings import load_runtime_defaults


DEFAULT_ROOT = Path(__file__).resolve().parent / "download"


@dataclass
class ConversionResult:
    source: str
    target: str
    status: str
    source_bytes: int = 0
    target_bytes: int = 0
    source_size: str = ""
    target_size: str = ""
    elapsed_seconds: float = 0.0
    error: str = ""


def parse_args() -> argparse.Namespace:
    runtime_defaults = load_runtime_defaults(PROJECT_ROOT)
    parser = argparse.ArgumentParser(
        description="把 *_rendered.png 转为 Canvas 原生尺寸的 WebP 100。"
    )
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=runtime_defaults.download_root,
        help="要递归处理的根目录，默认读取数据库 download.root",
    )
    parser.add_argument(
        "--device-scale",
        type=float,
        default=2.0,
        help="现有 PNG 截图对应的设备像素比，默认 2",
    )
    parser.add_argument(
        "--webp-quality",
        type=int,
        default=100,
        help="WebP 质量，默认 100",
    )
    parser.add_argument(
        "--lossless",
        action="store_true",
        help="使用 WebP 无损编码；默认关闭",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=2,
        help="单个目录内的并发编码数，默认 2",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖已经存在的 *_rendered.webp",
    )
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--keep-source",
        action="store_true",
        help="保留源 PNG（默认）",
    )
    source_group.add_argument(
        "--remove-source",
        action="store_true",
        help="WebP 验证成功后删除源 PNG",
    )
    parser.add_argument(
        "--summary-dir",
        type=Path,
        default=None,
        help="汇总文件输出目录，默认使用根目录",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只扫描和校验，不转换图片或删除源文件，但仍输出汇总",
    )
    return parser.parse_args()


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    print(f"[{timestamp()}] {message}", flush=True)


def discover_pngs(root: Path) -> List[Path]:
    files: List[Path] = []
    for path in root.rglob("*_rendered.png"):
        if not path.is_file():
            continue
        if path.name.endswith("_rendered.capture.png"):
            continue
        files.append(path)
    return sorted(files)


def target_size_for(
    width: int,
    height: int,
    device_scale: float,
) -> Tuple[int, int]:
    if device_scale <= 0:
        raise ValueError("--device-scale 必须大于 0")
    return (
        max(1, round(width / device_scale)),
        max(1, round(height / device_scale)),
    )


def convert_one(
    source: Path,
    device_scale: float,
    quality: int,
    lossless: bool,
    overwrite: bool,
    remove_source: bool,
    dry_run: bool,
) -> ConversionResult:
    started = time.perf_counter()
    target = source.with_suffix(".webp")
    source_bytes = source.stat().st_size

    if target.exists() and not overwrite:
        return ConversionResult(
            source=str(source),
            target=str(target),
            status="skipped_existing",
            source_bytes=source_bytes,
            target_bytes=target.stat().st_size,
            elapsed_seconds=time.perf_counter() - started,
        )

    try:
        with Image.open(source) as image:
            frame = image.convert("RGB")
            source_size = frame.size
            native_size = target_size_for(
                frame.width,
                frame.height,
                device_scale,
            )
            if min(native_size) < 16:
                raise ValueError(f"目标尺寸异常：{native_size[0]}×{native_size[1]}")
            if max(ImageStat.Stat(frame).stddev) < 1.0:
                return ConversionResult(
                    source=str(source),
                    target=str(target),
                    status="skipped_blank",
                    source_bytes=source_bytes,
                    source_size=f"{frame.width}×{frame.height}",
                    elapsed_seconds=time.perf_counter() - started,
                )

            if dry_run:
                return ConversionResult(
                    source=str(source),
                    target=str(target),
                    status="planned",
                    source_bytes=source_bytes,
                    source_size=f"{frame.width}×{frame.height}",
                    target_size=f"{native_size[0]}×{native_size[1]}",
                    elapsed_seconds=time.perf_counter() - started,
                )

            if frame.size != native_size:
                frame = frame.resize(native_size, Image.Resampling.LANCZOS)

            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".part")
            try:
                frame.save(
                    partial,
                    format="WEBP",
                    quality=max(0, min(100, quality)),
                    method=6,
                    lossless=lossless,
                )
                with Image.open(partial) as check:
                    if check.size != native_size:
                        raise ValueError(
                            f"WebP 尺寸不匹配：{check.size} != {native_size}"
                        )
                    check.load()
                target_bytes = partial.stat().st_size
                if target_bytes <= 0:
                    raise ValueError("WebP 文件为空")
                os.replace(partial, target)
            finally:
                try:
                    partial.unlink(missing_ok=True)
                except OSError:
                    pass

        if remove_source:
            try:
                source.unlink()
            except OSError as exc:
                return ConversionResult(
                    source=str(source),
                    target=str(target),
                    status="failed",
                    source_bytes=source_bytes,
                    target_bytes=target_bytes,
                    source_size=f"{source_size[0]}×{source_size[1]}",
                    target_size=f"{native_size[0]}×{native_size[1]}",
                    elapsed_seconds=time.perf_counter() - started,
                    error=f"删除源 PNG 失败：{type(exc).__name__}: {exc}",
                )

        return ConversionResult(
            source=str(source),
            target=str(target),
            status="converted",
            source_bytes=source_bytes,
            target_bytes=target_bytes,
            source_size=f"{source_size[0]}×{source_size[1]}",
            target_size=f"{native_size[0]}×{native_size[1]}",
            elapsed_seconds=time.perf_counter() - started,
        )
    except Exception as exc:
        return ConversionResult(
            source=str(source),
            target=str(target),
            status="failed",
            source_bytes=source_bytes,
            elapsed_seconds=time.perf_counter() - started,
            error=f"{type(exc).__name__}: {exc}",
        )


def write_summary(
    summary_dir: Path,
    root: Path,
    results: Sequence[ConversionResult],
    args: argparse.Namespace,
) -> Tuple[Path, Path]:
    now = datetime.now()
    converted = [r for r in results if r.status == "converted"]
    failed = [r for r in results if r.status == "failed"]
    existing = [r for r in results if r.status == "skipped_existing"]
    blank = [r for r in results if r.status == "skipped_blank"]
    planned = [r for r in results if r.status == "planned"]
    saved_bytes = sum(
        max(0, r.source_bytes - r.target_bytes)
        for r in converted
    )

    payload = {
        "generated_at": now.isoformat(timespec="seconds"),
        "root": str(root),
        "dry_run": args.dry_run,
        "device_scale": args.device_scale,
        "webp_quality": args.webp_quality,
        "lossless": args.lossless,
        "remove_source": args.remove_source,
        "png_found": len(results),
        "converted": len(converted),
        "skipped_existing": len(existing),
        "skipped_blank": len(blank),
        "planned": len(planned),
        "failed": len(failed),
        "source_bytes": sum(r.source_bytes for r in results),
        "target_bytes": sum(r.target_bytes for r in converted),
        "saved_bytes": saved_bytes,
        "failed_files": [
            {"source": r.source, "error": r.error}
            for r in failed
        ],
        "results": [asdict(r) for r in results],
    }

    summary_dir.mkdir(parents=True, exist_ok=True)
    json_path = summary_dir / "render_webp_conversion.json"
    text_path = summary_dir / "render_webp_conversion.txt"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        "Rendered PNG 转 WebP 汇总",
        f"生成时间：{payload['generated_at']}",
        f"扫描根目录：{root}",
        f"dry-run：{'是' if args.dry_run else '否'}",
        f"设备缩放：{args.device_scale:g}",
        f"WebP 质量：{args.webp_quality}",
        f"无损模式：{'是' if args.lossless else '否'}",
        f"发现 PNG：{len(results)}",
        f"成功转换：{len(converted)}",
        f"计划转换：{len(planned)}",
        f"已存在跳过：{len(existing)}",
        f"空白跳过：{len(blank)}",
        f"失败：{len(failed)}",
        f"节省空间：{saved_bytes / 1024 / 1024:.1f} MB",
    ]
    if failed:
        lines.append("失败文件：")
        lines.extend(f"- {r.source} | {r.error}" for r in failed)
    text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return text_path, json_path


def main() -> int:
    args = parse_args()
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        log(f"目录不存在：{root}")
        return 2
    if args.workers < 1:
        log("--workers 必须大于等于 1")
        return 2
    if args.device_scale <= 0:
        log("--device-scale 必须大于 0")
        return 2
    if not features.check("webp"):
        log("当前 Pillow 不支持 WebP，请升级 Pillow。")
        return 2

    png_files = discover_pngs(root)
    if not png_files:
        log(f"没有找到 *_rendered.png：{root}")
        return 0

    grouped = {}
    for path in png_files:
        grouped.setdefault(path.parent, []).append(path)

    log(
        f"发现 {len(png_files)} 张 PNG，分布在 {len(grouped)} 个目录。"
    )
    if args.dry_run:
        log("dry-run 已开启：不会转换图片或删除源 PNG。")

    all_results: List[ConversionResult] = []
    for directory_index, directory in enumerate(sorted(grouped), start=1):
        paths = grouped[directory]
        log(
            f"[{directory_index}/{len(grouped)}] 处理目录：{directory} "
            f"({len(paths)} 张)"
        )
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(
                    convert_one,
                    path,
                    args.device_scale,
                    args.webp_quality,
                    args.lossless,
                    args.overwrite,
                    args.remove_source,
                    args.dry_run,
                ): path
                for path in paths
            }
            for future in as_completed(futures):
                result = future.result()
                all_results.append(result)
                relative = Path(result.source).relative_to(root)
                if result.status == "converted":
                    log(
                        f"完成：{relative} "
                        f"{result.source_size} → {result.target_size}，"
                        f"{result.source_bytes / 1024 / 1024:.2f} MB → "
                        f"{result.target_bytes / 1024:.1f} KB"
                    )
                elif result.status == "planned":
                    log(
                        f"计划：{relative} "
                        f"{result.source_size} → {result.target_size}"
                    )
                elif result.status == "failed":
                    log(f"失败：{relative} | {result.error}")
                else:
                    log(f"{result.status}：{relative}")

    summary_dir = (
        args.summary_dir.expanduser().resolve()
        if args.summary_dir is not None
        else root
    )
    text_path, json_path = write_summary(
        summary_dir,
        root,
        all_results,
        args,
    )

    converted = sum(r.status == "converted" for r in all_results)
    failed = sum(r.status == "failed" for r in all_results)
    existing = sum(r.status == "skipped_existing" for r in all_results)
    blank = sum(r.status == "skipped_blank" for r in all_results)
    planned = sum(r.status == "planned" for r in all_results)
    saved = sum(
        max(0, r.source_bytes - r.target_bytes)
        for r in all_results
        if r.status == "converted"
    )

    log(
        "汇总："
        f"成功 {converted}，计划 {planned}，已存在 {existing}，"
        f"空白 {blank}，失败 {failed}，节省 {saved / 1024 / 1024:.1f} MB。"
    )
    log(f"文本汇总：{text_path}")
    log(f"JSON 汇总：{json_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n处理已取消。", file=sys.stderr)
        raise SystemExit(130)
