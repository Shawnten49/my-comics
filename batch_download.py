#!/usr/bin/env python3
"""
按网页 URL 批量调用 image_crawler.py，并按页面标题去重和汇总。

albums.txt 每行一个网页地址，例如：
    https://18comic.vip/photo/601116
    https://18comic.vip/photo/601119

运行：
    python3 batch_download.py albums.txt \
      --out-root "<项目根目录>/download" \
      --cdp-url "http://127.0.0.1:9222"
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shlex
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, TextIO, Tuple
from urllib.parse import urlparse

from comic_web.runtime_settings import load_runtime_defaults


IMAGE_SUMMARY_RE = re.compile(
    r"下载结束：新增\s*(\d+)，已存在\s*(\d+)，失败\s*(\d+)"
)
PROJECT_ROOT = Path(__file__).resolve().parent


def current_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def stamped(message: str) -> str:
    return f"[{current_timestamp()}] {message}"


def emit(
    message: str,
    batch_log: Optional[TextIO] = None,
    error: bool = False,
) -> None:
    rendered = stamped(message)
    print(rendered, file=sys.stderr if error else sys.stdout, flush=True)
    if batch_log is not None:
        batch_log.write(rendered + "\n")
        batch_log.flush()


@dataclass
class PageResult:
    page_url: str
    title: str
    folder_name: str
    output_dir: str
    status: str
    exit_code: Optional[int]
    duration_seconds: float
    existing_images: int = 0
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    log_file: str = ""
    result_file: str = ""
    error: str = ""


def parse_args() -> argparse.Namespace:
    runtime_defaults = load_runtime_defaults(PROJECT_ROOT)
    parser = argparse.ArgumentParser(
        description="读取单列网页 URL，逐个调用 image_crawler.py 并汇总结果。"
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="-",
        help="URL 文本文件，每行一个网页地址；使用 - 或省略时从标准输入读取",
    )
    parser.add_argument(
        "--out-root",
        "--out",
        dest="out_root",
        type=Path,
        default=runtime_defaults.download_root,
        help="下载根目录；默认读取数据库 download.root",
    )
    parser.add_argument(
        "--crawler",
        type=Path,
        default=Path(__file__).with_name("image_crawler.py"),
        help="单任务下载脚本路径，默认使用同目录 image_crawler.py",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="运行 image_crawler.py 的 Python 解释器，默认当前解释器",
    )
    parser.add_argument(
        "--cdp-url",
        default="http://127.0.0.1:9222",
        help="已开启调试端口的 Chrome 地址，默认 http://127.0.0.1:9222",
    )
    parser.add_argument(
        "--selector",
        default=".scramble-page img",
        help="传给 image_crawler.py 的图片 CSS 选择器",
    )
    parser.add_argument(
        "--download-mode",
        choices=("auto", "direct", "browser"),
        default="auto",
        help="下载网络模式，默认 auto；CDP 模式会自动使用 Chrome 网络",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="传给单任务下载器的网络超时秒数，默认 120",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=4,
        help="传给单任务下载器的失败重试次数，默认 4",
    )
    parser.add_argument(
        "--verification-wait",
        type=int,
        default=360,
        help="Cloudflare 人工验证等待秒数，默认 360",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="某个网页失败后立即停止后续网页",
    )
    parser.add_argument(
        "--force",
        "--no-skip-existing",
        dest="force",
        action="store_true",
        help="即使标题目录已有图片，也重新检查和下载",
    )
    parser.add_argument(
        "--sleep-min",
        type=float,
        default=1.0,
        help="网页之间的最短随机等待秒数，默认 1",
    )
    parser.add_argument(
        "--sleep-max",
        type=float,
        default=10.0,
        help="网页之间的最长随机等待秒数，默认 10",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只读取 URL 并显示将要执行的命令，不访问 Chrome 或下载",
    )
    return parser.parse_args()


def read_page_urls(source: str) -> Tuple[List[str], int]:
    if source == "-":
        handle: TextIO = sys.stdin
        close_handle = False
    else:
        path = Path(source).expanduser()
        handle = path.open("r", encoding="utf-8")
        close_handle = True

    page_urls: List[str] = []
    duplicates = 0
    seen = set()
    try:
        for line_number, raw_line in enumerate(handle, start=1):
            value = raw_line.split("#", 1)[0].strip().lstrip("\ufeff")
            if not value:
                continue
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(
                    f"第 {line_number} 行不是有效的 HTTP(S) 网页地址："
                    f"{raw_line.rstrip()}"
                )
            if value in seen:
                duplicates += 1
                continue
            seen.add(value)
            page_urls.append(value)
    finally:
        if close_handle:
            handle.close()
    return page_urls, duplicates


def check_cdp(cdp_url: str) -> None:
    endpoint = cdp_url.rstrip("/") + "/json/version"
    try:
        with urllib.request.urlopen(endpoint, timeout=5) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
            info = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(
            f"无法连接 Chrome CDP：{cdp_url}\n"
            "请先按 README 启动独立 Chrome，并保持窗口运行。"
        ) from exc
    emit(f"Chrome CDP 已连接：{info.get('Browser', 'unknown')} ({cdp_url})")


def build_command(
    args: argparse.Namespace,
    page_url: str,
    result_file: Path,
) -> List[str]:
    command = [
        args.python,
        str(args.crawler),
        page_url,
        "--out-root",
        str(args.out_root),
        "--selector",
        args.selector,
        "--cdp-url",
        args.cdp_url,
        "--download-mode",
        args.download_mode,
        "--timeout",
        str(args.timeout),
        "--retries",
        str(args.retries),
        "--verification-wait",
        str(args.verification_wait),
        "--result-file",
        str(result_file),
    ]
    if args.force:
        command.append("--force")
    return command


def write_and_print(
    line: str,
    page_log: TextIO,
    batch_log: TextIO,
    prefix: str,
) -> str:
    clean_line = line.rstrip("\r\n")
    rendered = stamped(f"{prefix}{clean_line}")
    print(rendered, flush=True)
    page_log.write(rendered + "\n")
    page_log.flush()
    batch_log.write(rendered + "\n")
    batch_log.flush()
    return clean_line


def load_crawler_result(
    result_file: Path,
    page_url: str,
    exit_code: int,
    summary: Optional[Tuple[int, int, int]],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if result_file.is_file():
        try:
            payload = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}

    downloaded, skipped, failed = summary or (0, 0, 0)
    downloaded = int(payload.get("downloaded", downloaded) or 0)
    skipped = int(payload.get("skipped", skipped) or 0)
    failed = int(payload.get("failed", failed) or 0)
    status = str(payload.get("status") or "")
    if status not in {"success", "existing", "failed"}:
        status = "success" if exit_code == 0 else "failed"
    if exit_code != 0:
        status = "failed"

    return {
        "page_url": page_url,
        "title": str(payload.get("title") or ""),
        "folder_name": str(payload.get("folder_name") or ""),
        "output_dir": str(payload.get("output_dir") or ""),
        "status": status,
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
        "existing_images": int(payload.get("existing_images", 0) or 0),
        "error": str(payload.get("error") or ""),
    }


def run_page(
    args: argparse.Namespace,
    page_url: str,
    index: int,
    total: int,
    page_log_path: Path,
    result_file: Path,
    batch_log: TextIO,
) -> PageResult:
    command = build_command(args, page_url, result_file)
    prefix = f"[{index}/{total}] "
    started = time.monotonic()
    summary: Optional[Tuple[int, int, int]] = None

    emit(f"{prefix}开始页面：{page_url}", batch_log)

    with page_log_path.open("w", encoding="utf-8") as page_log:
        page_log.write(stamped("$ " + shlex.join(command)) + "\n\n")
        result_file.unlink(missing_ok=True)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        try:
            for line in process.stdout:
                clean_line = write_and_print(
                    line,
                    page_log,
                    batch_log,
                    prefix,
                )
                match = IMAGE_SUMMARY_RE.search(clean_line)
                if match:
                    summary = tuple(int(value) for value in match.groups())
            exit_code = process.wait()
        except KeyboardInterrupt:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise

    duration = time.monotonic() - started
    payload = load_crawler_result(
        result_file,
        page_url,
        exit_code,
        summary,
    )
    title = payload["title"] or payload["folder_name"] or "未知标题"
    result = PageResult(
        page_url=page_url,
        title=title,
        folder_name=payload["folder_name"],
        output_dir=payload["output_dir"],
        status=payload["status"],
        exit_code=exit_code,
        duration_seconds=round(duration, 2),
        existing_images=payload["existing_images"],
        downloaded=payload["downloaded"],
        skipped=payload["skipped"],
        failed=payload["failed"],
        log_file=str(page_log_path),
        result_file=str(result_file),
        error=payload["error"],
    )

    status_label = {
        "success": "成功",
        "existing": "已存在，跳过",
        "failed": "失败",
    }[result.status]
    emit(
        f"{prefix}{status_label}：{title}，耗时 {duration:.1f}s，"
        f"新增 {result.downloaded}，已存在 {result.skipped}，"
        f"失败 {result.failed}",
        batch_log,
    )
    return result


def unique_nonempty(values: Sequence[str]) -> List[str]:
    output: List[str] = []
    seen = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output


def write_summary(
    log_dir: Path,
    results: Sequence[PageResult],
    args: argparse.Namespace,
    duplicate_urls: int,
) -> Dict[str, Any]:
    success = [result for result in results if result.status == "success"]
    failed = [result for result in results if result.status == "failed"]
    existing = [result for result in results if result.status == "existing"]
    existing_titles = unique_nonempty(
        [result.folder_name or result.title for result in existing]
    )
    all_folder_names = [
        result.folder_name for result in results if result.folder_name
    ]
    title_counts = Counter(all_folder_names)
    duplicate_titles = sorted(
        name for name, count in title_counts.items() if count > 1
    )

    summary: Dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input": args.input,
        "output_root": str(args.out_root),
        "page_total": len(results),
        "page_success": len(success),
        "page_failed": len(failed),
        "page_existing": len(existing),
        "duplicate_urls_skipped": duplicate_urls,
        "duplicate_title_count": len(duplicate_titles),
        "duplicate_titles": duplicate_titles,
        "image_downloaded": sum(result.downloaded for result in results),
        "image_skipped": sum(result.skipped for result in results),
        "image_failed": sum(result.failed for result in results),
        "image_in_existing_titles": sum(
            result.existing_images for result in existing
        ),
        "success_titles": [result.title for result in success],
        "failed_urls": [result.page_url for result in failed],
        "failed_pages": [
            {
                "title": result.title or "未知标题",
                "url": result.page_url,
                "error": result.error,
            }
            for result in failed
        ],
        "existing_titles": existing_titles,
        "existing_pages": [
            {
                "url": result.page_url,
                "title": result.title,
                "folder_name": result.folder_name,
                "output_dir": result.output_dir,
                "images": result.existing_images,
            }
            for result in existing
        ],
        "pages": [asdict(result) for result in results],
    }

    json_path = log_dir / "batch_summary.json"
    text_path = log_dir / "batch_summary.txt"
    summary["json_file"] = str(json_path)
    summary["text_file"] = str(text_path)
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        "批量下载汇总",
        f"生成时间：{summary['generated_at']}",
        f"页面总数：{summary['page_total']}",
        f"成功下载页面：{summary['page_success']}",
        f"失败页面：{summary['page_failed']}",
        f"标题已存在跳过页面：{summary['page_existing']}",
        f"重复 URL：{duplicate_urls}",
        f"重复标题：{summary['duplicate_title_count']}",
        f"图片新增：{summary['image_downloaded']}",
        f"图片已存在：{summary['image_skipped']}",
        f"图片失败：{summary['image_failed']}",
        f"已存在标题中的图片：{summary['image_in_existing_titles']}",
        f"成功标题：{' | '.join(summary['success_titles']) or '-'}",
        f"已存在标题：{' | '.join(existing_titles) or '-'}",
    ]
    if summary["failed_pages"]:
        lines.append("失败页面：")
        lines.extend(
            f"- {item['title']} | {item['url']}"
            for item in summary["failed_pages"]
        )
    else:
        lines.append("失败页面：-")
    text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    args = parse_args()
    args.out_root = args.out_root.expanduser().resolve()
    args.crawler = args.crawler.expanduser().resolve()
    if args.sleep_min < 0:
        raise ValueError("--sleep-min 不能小于 0")
    if args.sleep_max < args.sleep_min:
        raise ValueError("--sleep-max 必须大于或等于 --sleep-min")

    page_urls, duplicate_urls = read_page_urls(args.input)
    if not page_urls:
        emit("没有读取到网页 URL。", error=True)
        return 2
    if not args.crawler.is_file():
        emit(f"找不到单任务下载器：{args.crawler}", error=True)
        return 2

    emit(
        f"读取到 {len(page_urls)} 个唯一网页 URL，重复 {duplicate_urls} 个。"
    )
    if args.sleep_max > 0:
        emit(
            f"页面间随机等待：{args.sleep_min:g}-{args.sleep_max:g} 秒。"
        )

    if args.dry_run:
        dry_log_dir = args.out_root / "logs"
        for index, page_url in enumerate(page_urls, start=1):
            result_file = dry_log_dir / f"{index:04d}.result.json"
            emit(
                f"[{index}/{len(page_urls)}] "
                + shlex.join(build_command(args, page_url, result_file))
            )
        return 0

    try:
        check_cdp(args.cdp_url)
    except RuntimeError as exc:
        emit(f"批量任务无法开始：{exc}", error=True)
        return 2

    log_dir = args.out_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    batch_log_path = log_dir / "batch_download.log"
    results: List[PageResult] = []

    with batch_log_path.open("a", encoding="utf-8") as batch_log:
        batch_log.write(
            "\n"
            + stamped(
                f"===== BATCH START count={len(page_urls)} ====="
            )
            + "\n"
        )
        batch_log.flush()
        try:
            for index, page_url in enumerate(page_urls, start=1):
                result = run_page(
                    args,
                    page_url,
                    index,
                    len(page_urls),
                    log_dir / f"{index:04d}.log",
                    log_dir / f"{index:04d}.result.json",
                    batch_log,
                )
                results.append(result)
                if result.status == "failed" and args.stop_on_error:
                    emit(
                        "已启用 --stop-on-error，停止后续页面。",
                        batch_log,
                    )
                    break
                if result.status == "existing":
                    continue
                if index < len(page_urls) and args.sleep_max > 0:
                    delay = round(
                        random.uniform(args.sleep_min, args.sleep_max),
                        2,
                    )
                    emit(
                        f"等待 {delay:.2f} 秒后开始下一个页面……",
                        batch_log,
                    )
                    time.sleep(delay)
        except KeyboardInterrupt:
            batch_log.write(stamped("BATCH INTERRUPTED BY USER") + "\n")
            batch_log.flush()
            emit("批量任务被用户中断。", error=True)

        summary = write_summary(log_dir, results, args, duplicate_urls)
        batch_log.write(
            stamped(
                f"===== BATCH END success={summary['page_success']} "
                f"failed={summary['page_failed']} "
                f"existing={summary['page_existing']} ====="
            )
            + "\n"
        )

    print()
    emit("批量下载汇总")
    emit(f"页面总数：{summary['page_total']}")
    emit(f"成功下载页面：{summary['page_success']}")
    emit(f"失败页面：{summary['page_failed']}")
    emit(f"标题已存在跳过页面：{summary['page_existing']}")
    emit(f"重复标题：{summary['duplicate_title_count']}")
    emit(f"图片新增：{summary['image_downloaded']}")
    emit(f"图片已存在：{summary['image_skipped']}")
    emit(f"图片失败：{summary['image_failed']}")
    if summary["existing_titles"]:
        emit(f"已存在标题：{' | '.join(summary['existing_titles'])}")
    if summary["failed_pages"]:
        emit("失败页面：")
        for item in summary["failed_pages"]:
            emit(f"  {item['title']} | {item['url']}")
    emit(f"汇总文件：{summary['text_file']}")
    return 0 if not summary["page_failed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(141)
    except ValueError as exc:
        emit(f"输入错误：{exc}", error=True)
        raise SystemExit(2)
