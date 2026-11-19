#!/usr/bin/env python3
"""
在 Chrome 中打开网页，滚动触发懒加载，然后按页面顺序下载图片。

主要能力：
1. 使用 Playwright 控制本机 Chrome。
2. 逐屏滚动到底部，连续多轮高度不变才判定结束。
3. 从 src、data-*、srcset 中提取图片并去重。
4. 保留浏览器 Cookie，支持通过浏览器网络栈下载。
5. 自动识别 Canvas 还原和 blob: 解密图片，并导出页面实际像素。
6. 已完成文件自动跳过，失败重试，输出 manifest.json。

依赖：
    python3 -m pip install -r requirements.txt

示例：
    python3 image_crawler.py \
      "https://18comic.vip/photo/601116" \
      --out-root "<项目根目录>/download" \
      --selector ".scramble-page img"

下载文件夹会自动使用页面标题。若同名文件夹中已有有效图片，则跳过该页面。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import posixpath
import re
import socket
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import unquote, urljoin, urlparse

import requests
from PIL import Image, ImageStat
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from comic_web.chrome_cdp import ChromeCDPError, ensure_chrome_cdp
    from comic_web.runtime_settings import load_runtime_defaults
except ImportError:
    ChromeCDPError = RuntimeError
    ensure_chrome_cdp = None
    load_runtime_defaults = None


DEFAULT_EXCLUDE_PATTERN = (
    r"(?:^|[/_.-])(?:blank|logo|icon|favicon|avatar|placeholder|loading|"
    r"spinner|spacer|sprite|captcha|qrcode)(?:[/_.-]|$)"
)
CHUNK_SIZE = 128 * 1024
PROJECT_ROOT = Path(__file__).resolve().parent
TITLE_CATEGORY_RE = re.compile(
    r"[\[［]([^\]］]+)[\]］]|[\(（]([^\)）]+)[\)）]"
)


@dataclass(frozen=True)
class ImageItem:
    order: int
    url: str
    page_element_width: int
    page_element_height: int
    natural_width: int
    natural_height: int


@dataclass(frozen=True)
class DownloadTask:
    order: int
    url: str
    path: Path


@dataclass
class DownloadResult:
    order: int
    url: str
    path: Path
    status: str
    size: int = 0
    sha256: str = ""
    error: str = ""


@dataclass
class BrowserSession:
    context: BrowserContext
    browser: Optional[Browser]
    process: Optional[subprocess.Popen] = None
    owns_browser: bool = False


class DownloadContext:
    """线程局部的 requests 会话，复用连接并带上浏览器 Cookie。"""

    def __init__(
        self,
        cookies: Sequence[Dict[str, Any]],
        user_agent: str,
        referer: str,
        retries: int,
        timeout: int,
    ) -> None:
        self.cookies = cookies
        self.user_agent = user_agent
        self.referer = referer
        self.retries = retries
        self.timeout = timeout
        self.local = threading.local()

    def session(self) -> requests.Session:
        if not hasattr(self.local, "session"):
            session = requests.Session()
            retry = Retry(
                total=self.retries,
                connect=self.retries,
                read=self.retries,
                status=self.retries,
                backoff_factor=0.8,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=frozenset({"GET"}),
                respect_retry_after_header=True,
            )
            adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            session.headers.update(self.headers())
            for cookie in self.cookies:
                try:
                    session.cookies.set(
                        cookie["name"],
                        cookie["value"],
                        domain=cookie.get("domain"),
                        path=cookie.get("path", "/"),
                    )
                except (KeyError, ValueError):
                    continue
            self.local.session = session
        return self.local.session

    def headers(self) -> Dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Referer": self.referer,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }


def parse_args() -> argparse.Namespace:
    runtime_defaults = (
        load_runtime_defaults(PROJECT_ROOT)
        if load_runtime_defaults is not None
        else None
    )
    parser = argparse.ArgumentParser(
        description="滚动加载网页图片，并使用 Chrome 会话下载到本地。"
    )
    parser.add_argument("url", help="要打开的网页 URL")
    parser.add_argument(
        "--out-root",
        "--out",
        dest="out_root",
        type=Path,
        default=(
            runtime_defaults.download_root
            if runtime_defaults is not None
            else PROJECT_ROOT / "download"
        ),
        help=(
            "下载根目录，页面标题会作为其下的文件夹名；"
            "默认读取数据库 download.root"
        ),
    )
    parser.add_argument(
        "--selector",
        default="img",
        help="图片 CSS 选择器，默认 img",
    )
    parser.add_argument(
        "--path-prefix",
        action="append",
        default=[],
        help="只保留 URL 路径以此值开头的结果，可重复传入",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="显式指定漫画输出目录；用于下载任务断点续传",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="按已有 manifest 和图片文件继续下载，不跳过整本",
    )
    parser.add_argument(
        "--download-task-id",
        type=int,
        default=0,
        help="下载管理任务 ID，用于写入 .download-task.json 状态标记",
    )
    parser.add_argument(
        "--url-pattern",
        default="",
        help="只保留完整 URL 匹配此正则的结果",
    )
    parser.add_argument(
        "--exclude-pattern",
        default=DEFAULT_EXCLUDE_PATTERN,
        help="排除 URL 的正则；传空字符串可关闭默认过滤",
    )
    parser.add_argument(
        "--prefer-attr",
        default="data-original,data-src,data-lazy-src,data-original-src,data-url,currentSrc,src",
        help="图片地址优先级，以逗号分隔",
    )
    parser.add_argument(
        "--min-width",
        type=int,
        default=300,
        help="已加载图片的最小自然宽度，默认 300；使用 path-prefix 时不做尺寸过滤",
    )
    parser.add_argument(
        "--min-height",
        type=int,
        default=100,
        help="已加载图片的最小自然高度，默认 100；使用 path-prefix 时不做尺寸过滤",
    )
    parser.add_argument(
        "--scroll-ratio",
        type=float,
        default=0.55,
        help="每次滚动的视口比例，默认 0.55",
    )
    parser.add_argument(
        "--scroll-delay",
        type=float,
        default=0.25,
        help="每次滚动后的等待秒数，默认 0.25",
    )
    parser.add_argument(
        "--stable-rounds",
        type=int,
        default=3,
        help="滚动到底且页面高度连续不变的轮数，默认 3",
    )
    parser.add_argument(
        "--lazy-load-timeout",
        type=float,
        default=12.0,
        help=(
            "到达底部后等待站内图片队列的最长秒数，默认 12；"
            "超时后尝试使用页面提供的原始图片清单"
        ),
    )
    parser.add_argument(
        "--max-scrolls",
        type=int,
        default=5000,
        help="防止死循环的最大滚动次数，默认 5000",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=6,
        help="直接 HTTP 下载的并发数，默认 6",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=4,
        help="单次下载的重试次数，默认 4",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="网络超时秒数，默认 60",
    )
    parser.add_argument(
        "--download-mode",
        choices=("auto", "direct", "browser"),
        default="auto",
        help=(
            "下载网络模式：auto 直连失败后切换 Chrome，"
            "direct 只使用 Python 直连，browser 只使用 Chrome 页面下载"
        ),
    )
    parser.add_argument(
        "--render-mode",
        choices=("auto", "original", "screenshot"),
        default="auto",
        help=(
            "图片保存方式：auto 检测到页面 Canvas 还原时保存渲染截图，"
            "original 保存 CDN 原图，screenshot 强制保存页面可见结果"
        ),
    )
    parser.add_argument(
        "--render-wait",
        type=float,
        default=5.0,
        help="等待页面完成 Canvas 还原的最长秒数，默认 5",
    )
    parser.add_argument(
        "--render-format",
        choices=("webp", "png"),
        default="webp",
        help="渲染截图的保存格式，默认 webp",
    )
    parser.add_argument(
        "--render-quality",
        type=int,
        default=100,
        help="WebP 有损压缩质量，默认 100",
    )
    parser.add_argument(
        "--via-browser",
        action="store_true",
        help="兼容旧参数，等同于 --download-mode browser",
    )
    parser.add_argument(
        "--no-browser-fallback",
        action="store_true",
        help="直接 HTTP 下载失败后，不自动改走浏览器网络栈",
    )
    parser.add_argument(
        "--cdp-url",
        default="",
        help="连接已开启调试端口的 Chrome，例如 http://127.0.0.1:9222",
    )
    parser.add_argument(
        "--chrome-executable",
        type=Path,
        default=Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        help="Chrome 可执行文件路径；默认使用 macOS 的系统 Chrome",
    )
    parser.add_argument(
        "--playwright-launch",
        action="store_true",
        help="使用旧版 Playwright 自动启动模式；Cloudflare 可能识别自动化标记",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=Path(
            os.environ.get(
                "COMIC_CHROME_PROFILE_DIR",
                str(Path.home() / ".comic-chrome-profile"),
            )
        ).expanduser(),
        help="登录状态和 Cookie 使用的独立 Chrome 用户目录",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="无头运行；需要登录或验证码时不要使用",
    )
    parser.add_argument(
        "--verification-wait",
        type=int,
        default=360,
        help="遇到 Cloudflare 或站点验证页时，等待人工完成的秒数，默认 360",
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="遇到验证页时不等待人工操作，立即报错退出",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只提取并保存图片 URL，不下载文件",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="即使标题目录或渲染截图已存在，也重新检查并覆盖",
    )
    parser.add_argument(
        "--result-file",
        type=Path,
        default=None,
        help="将标题、目录、状态和图片数量写入指定 JSON 文件",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="结束后暂停，按 Enter 再关闭页面（主要用于人工检查）",
    )
    return parser.parse_args()


def folder_name_from_title(title: str, max_length: int = 180) -> str:
    """把页面标题转换为安全、稳定的文件夹名称。"""
    normalized = unicodedata.normalize("NFKC", title or "")
    normalized = normalized.strip()

    # 1. 先移除完整的站点尾部标记。
    normalized = re.sub(
        r"\s*\|H漫內頁瀏覽\s*Comics\s*-\s*禁漫天堂\s*$",
        "",
        normalized,
    ).strip()
    # 兼容只出现其中一段的旧标题格式。
    title_without_site_suffix = re.sub(
        r"\s*Comics\s*-\s*禁漫天堂\s*$",
        "",
        normalized,
    ).strip()
    if title_without_site_suffix:
        normalized = title_without_site_suffix
    title_without_browse_suffix = re.sub(
        r"\s*[|_]\s*H漫內頁瀏覽\s*$",
        "",
        normalized,
    ).strip()
    if title_without_browse_suffix:
        normalized = title_without_browse_suffix

    # 2. 移除开头连续的 [] 分组；如果正文为空，保留最后一个分组。
    leading_match = re.match(r"^(?:\s*\[[^\]]*\]\s*)+", normalized)
    if leading_match:
        groups = re.findall(r"\[([^\]]*)\]", leading_match.group(0))
        remaining = normalized[leading_match.end():].strip()
        if remaining:
            normalized = remaining
        else:
            normalized = next(
                (group.strip() for group in reversed(groups) if group.strip()),
                "",
            )

    normalized = re.sub(
        r"\s*\[(?:中国翻訳|DL版)\]\s*",
        " ",
        normalized,
    ).strip()
    normalized = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" .")
    if not normalized:
        return "未命名页面"
    if len(normalized) > max_length:
        normalized = normalized[:max_length].rstrip(" .")
    if normalized in {".", ".."}:
        return "未命名页面"
    return normalized


def categories_from_raw_title(title: str) -> List[str]:
    categories: List[str] = []
    seen = set()
    for match in TITLE_CATEGORY_RE.finditer(title or ""):
        value = unicodedata.normalize(
            "NFKC",
            match.group(1) or match.group(2) or "",
        )
        value = re.sub(r"\s+", " ", value).strip()
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        categories.append(value)
    return categories


def page_key_from_url(page_url: str) -> str:
    """提取 18comic /photo/<数字> URL 的稳定页面编号。"""
    parsed = urlparse(page_url)
    hostname = (parsed.hostname or "").lower()
    if hostname == "18comic.vip" or hostname.endswith(".18comic.vip"):
        match = re.fullmatch(r"/photo/(\d+)/?", parsed.path)
        if match:
            return match.group(1)
    return ""


def folder_name_from_page(title: str, page_url: str) -> str:
    """生成以页面编号开头、后接清理后标题的文件夹名称。"""
    cleaned_title = folder_name_from_title(title)
    page_key = page_key_from_url(page_url)
    return f"{page_key}_{cleaned_title}" if page_key else cleaned_title


def write_result_file(path: Optional[Path], payload: Dict[str, Any]) -> None:
    if path is None:
        return
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def write_album_metadata(
    output_dir: Path,
    source_url: str,
    page_id: Optional[str],
    raw_title: str,
    display_title: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_url": source_url,
        "page_id": page_id or "",
        "raw_title": raw_title,
        "title": display_title,
        "folder_name": output_dir.name,
        "categories": categories_from_raw_title(raw_title),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    metadata_path = output_dir / "metadata.json"
    temporary = metadata_path.with_name("metadata.json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, metadata_path)


def page_access_state(page: Page) -> Dict[str, Any]:
    """返回页面标题、URL 和是否处于验证/拦截状态。"""
    try:
        state = page.evaluate(
            """() => ({
                title: document.title || "",
                url: location.href,
                text: document.body ? document.body.innerText.slice(0, 3000) : "",
                html: document.documentElement
                    ? document.documentElement.outerHTML.slice(0, 12000)
                    : ""
            })"""
        )
    except Exception as exc:
        return {"title": "", "url": page.url, "blocked": False, "error": str(exc)}

    title = (state.get("title") or "").strip()
    url = (state.get("url") or "").strip()
    text = (state.get("text") or "").strip()
    html = (state.get("html") or "").strip()
    title_lower = title.lower()
    url_lower = url.lower()
    text_lower = text.lower()
    html_lower = html.lower()

    title_markers = (
        "just a moment",
        "attention required",
        "verify you are human",
        "安全检查",
        "安全验证",
        "人机验证",
    )
    content_markers = (
        "challenges.cloudflare.com",
        "cf-browser-verification",
        "id=\"challenge-form\"",
        "verify you are human",
        "checking your browser",
    )
    blocked = (
        "__cf_chl" in url_lower
        or any(marker in title_lower for marker in title_markers)
        or any(marker in html_lower for marker in content_markers)
        or (
            "请稍候" in title_lower
            and ("cloudflare" in html_lower or "安全验证" in text_lower)
        )
        or (
            "请稍候" in text_lower
            and ("cloudflare" in text_lower or "验证" in text_lower)
        )
    )
    return {
        "title": title,
        "url": url,
        "blocked": blocked,
        "text": text,
    }


def wait_for_manual_verification(
    page: Page,
    verification_wait: int,
    interactive: bool,
) -> None:
    """发现验证页后等待用户在 Chrome 中手动完成，不尝试绕过验证。"""
    state = page_access_state(page)
    if not state["blocked"]:
        # Cloudflare 的第一阶段占位页可能延迟替换。
        page.wait_for_timeout(700)
        state = page_access_state(page)
    if not state["blocked"]:
        return

    summary = (
        f"页面被验证码或安全验证拦截：{state['title'] or '无标题'}\n"
        f"当前地址：{state['url']}"
    )
    if not interactive:
        raise RuntimeError(
            summary
            + "\n请去掉 --no-interactive，或使用 --cdp-url 接入已通过验证的 Chrome。"
        )
    if verification_wait <= 0:
        raise RuntimeError(
            summary
            + "\n请在 Chrome 窗口中手动完成验证后，将 --verification-wait 设为正数再运行。"
        )

    print(
        "\n检测到 Cloudflare/站点安全验证。\n"
        "请切换到 Chrome 窗口，手动完成验证；"
        "脚本会自动等待并继续。\n"
        f"最长等待 {verification_wait} 秒。",
        flush=True,
    )
    deadline = time.monotonic() + verification_wait
    while time.monotonic() < deadline:
        page.wait_for_timeout(2000)
        state = page_access_state(page)
        if not state["blocked"]:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(1000)
            final_state = page_access_state(page)
            if not final_state["blocked"]:
                print("验证已通过，继续加载页面。", flush=True)
                return

    raise RuntimeError(
        summary
        + f"\n已等待 {verification_wait} 秒，验证仍未完成。"
        + "\n可以在 Chrome 中完成验证后重试，或增加 --verification-wait。"
    )


def scroll_until_stable(page: Page, args: argparse.Namespace) -> Dict[str, Any]:
    """逐屏滚动，触发懒加载；仅在底部多次稳定后才能结束。"""
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(int(args.scroll_delay * 1000))

    stable_rounds = 0
    previous_height = 0
    previous_loaded = -1
    scroll_count = 0

    while scroll_count < args.max_scrolls:
        metrics = page.evaluate(
            """(selector) => {
                const images = Array.from(document.querySelectorAll(selector));
                const loaded = images.filter((image) => {
                    const source = image.currentSrc || image.src || "";
                    return (
                        source &&
                        image.naturalWidth > 1 &&
                        image.naturalHeight > 1
                    );
                }).length;
                return {
                    scrollY: window.scrollY,
                    innerHeight: window.innerHeight,
                    height: Math.max(
                        document.body ? document.body.scrollHeight : 0,
                        document.documentElement.scrollHeight
                    ),
                    targetImages: images.length,
                    loadedImages: loaded
                };
            }""",
            args.selector,
        )
        current_y = int(metrics["scrollY"])
        viewport_height = max(1, int(metrics["innerHeight"]))
        height = int(metrics["height"])
        target_images = int(metrics["targetImages"])
        loaded_images = int(metrics["loadedImages"])
        at_bottom = current_y + viewport_height >= height - 4

        if at_bottom and height <= previous_height + 1:
            stable_rounds += 1
            if loaded_images != previous_loaded:
                stable_rounds = 0
            if stable_rounds >= args.stable_rounds:
                break
            page.wait_for_timeout(max(1000, int(args.scroll_delay * 4000)))
        else:
            stable_rounds = 0
            step = max(200, int(viewport_height * args.scroll_ratio))
            target_y = min(height, current_y + step)
            if at_bottom:
                target_y = height
            page.evaluate("(y) => window.scrollTo(0, y)", target_y)
            page.wait_for_timeout(int(args.scroll_delay * 1000))

        previous_height = height
        previous_loaded = loaded_images
        scroll_count += 1
    else:
        print(
            f"警告：达到最大滚动次数 {args.max_scrolls}，仍按当前 DOM 继续。",
            file=sys.stderr,
        )

    final_metrics = page.evaluate(
        """(selector) => ({
            scrollY: window.scrollY,
            innerHeight: window.innerHeight,
            height: Math.max(
                document.body ? document.body.scrollHeight : 0,
                document.documentElement.scrollHeight
            ),
            imageCount: document.images.length,
            loadedTargetImages: [
                ...document.querySelectorAll(selector)
            ].filter((image) => {
                const source = image.currentSrc || image.src || "";
                return (
                    source &&
                    image.naturalWidth > 1 &&
                    image.naturalHeight > 1
                );
            }).length
        })""",
        args.selector,
    )
    final_metrics["scrollCount"] = scroll_count
    return final_metrics


def target_image_load_state(
    page: Page,
    selector: str,
) -> Dict[str, int]:
    """Report load state for page images managed by the site's loader."""
    return page.evaluate(
        """(selector) => {
            const images = [...document.querySelectorAll(selector)].filter(
                (image) => image.closest(".chapter-image")
            );
            const loaded = images.filter((image) => {
                const source = image.currentSrc || image.src || "";
                return (
                    source &&
                    image.naturalWidth > 1 &&
                    image.naturalHeight > 1
                );
            }).length;
            const retryable = images.filter((image) => {
                const container = image.closest(".chapter-image");
                return container && (
                    container.classList.contains("is-retry") ||
                    container.querySelector(".chapter-error")
                );
            }).length;
            return {
                total: images.length,
                loaded,
                retryable,
                unresolved: Math.max(0, images.length - loaded)
            };
        }""",
        selector,
    )


def wait_for_managed_images(
    page: Page,
    args: argparse.Namespace,
) -> Dict[str, int]:
    """Wait for the site's XHR queue and retry transient image failures."""
    state = target_image_load_state(page, args.selector)
    if not state["total"]:
        return state

    print(
        "等待站内图片队列完成："
        f"{state['loaded']}/{state['total']}，"
        f"最长 {args.lazy_load_timeout:g} 秒。",
        flush=True,
    )
    deadline = time.monotonic() + max(1.0, args.lazy_load_timeout)
    last_loaded = -1
    stable_since = time.monotonic()
    retry_count = 0

    while time.monotonic() < deadline:
        state = target_image_load_state(page, args.selector)
        if state["loaded"] >= state["total"]:
            print(
                f"站内图片队列完成：{state['loaded']}/{state['total']}。",
                flush=True,
            )
            return state

        now = time.monotonic()
        if state["loaded"] != last_loaded:
            last_loaded = state["loaded"]
            stable_since = now

        if state["retryable"] and retry_count < 3:
            clicked = page.evaluate(
                """(selector) => {
                    let count = 0;
                    for (const image of document.querySelectorAll(selector)) {
                        const container = image.closest(".chapter-image");
                        if (
                            container &&
                            (
                                container.classList.contains("is-retry") ||
                                container.querySelector(".chapter-error")
                            )
                        ) {
                            container.click();
                            count += 1;
                        }
                    }
                    return count;
                }""",
                args.selector,
            )
            retry_count += 1
            print(
                f"检测到 {clicked} 张图片加载失败，已触发第 "
                f"{retry_count} 次页面重试。",
                flush=True,
            )
            stable_since = now
            page.wait_for_timeout(1500)
            continue

        if now - stable_since >= 4:
            # 站内加载器只排队当前附近的图片。滚动一次可重新触发队列，
            # 避免快速跳到底部后遗漏中间图片。
            page.evaluate(
                """() => {
                    const step = Math.max(300, Math.round(innerHeight * 0.4));
                    scrollBy(0, -step);
                    requestAnimationFrame(() => {
                        scrollTo(0, document.documentElement.scrollHeight);
                    });
                }"""
            )
            stable_since = now

        page.wait_for_timeout(1000)

    state = target_image_load_state(page, args.selector)
    print(
        "警告：站内图片队列未在时限内完成，"
        f"当前 {state['loaded']}/{state['total']}，"
        f"待处理 {state['unresolved']} 张。",
        file=sys.stderr,
    )
    return state


def extract_image_items(
    page: Page,
    args: argparse.Namespace,
) -> List[ImageItem]:
    """提取每张目标图片的候选地址，并按优先级和过滤条件选一个。"""
    raw_items = page.evaluate(
        """(selector) => {
            const absolute = (value) => {
                if (!value) return null;
                try {
                    const raw = value.trim();
                    if (raw.startsWith("blob:")) return raw;
                    const url = new URL(raw, document.baseURI);
                    if (!["http:", "https:"].includes(url.protocol)) return null;
                    return url.href;
                } catch (_) {
                    return null;
                }
            };
            const parseSrcset = (value) => {
                if (!value) return [];
                return value.split(",").map((part) => {
                    const tokens = part.trim().split(/\\s+/);
                    const url = absolute(tokens[0]);
                    const descriptor = tokens.slice(1).join(" ");
                    let rank = 0;
                    const match = descriptor.match(/^([0-9.]+)(w|x)$/);
                    if (match) rank = Number(match[1]) * (match[2] === "x" ? 1000 : 1);
                    return url ? { url, rank } : null;
                }).filter(Boolean);
            };

            return Array.from(document.querySelectorAll(selector)).map((element, order) => {
                const attrs = {};
                for (const name of [
                    "data-original", "data-src", "data-lazy-src",
                    "data-original-src", "data-url", "src"
                ]) {
                    attrs[name] = absolute(element.getAttribute(name));
                }
                const rect = element.getBoundingClientRect();
                return {
                    order,
                    attrs,
                    currentSrc: absolute(element.currentSrc),
                    srcset: parseSrcset(element.getAttribute("srcset")),
                    naturalWidth: Number(element.naturalWidth || 0),
                    naturalHeight: Number(element.naturalHeight || 0),
                    elementWidth: Math.round(rect.width || Number(element.offsetWidth) || 0),
                    elementHeight: Math.round(rect.height || Number(element.offsetHeight) || 0)
                };
            });
        }""",
        args.selector,
    )

    preferred = [
        item.strip()
        for item in args.prefer_attr.split(",")
        if item.strip()
    ]
    include_re = re.compile(args.url_pattern) if args.url_pattern else None
    exclude_re = re.compile(args.exclude_pattern, re.IGNORECASE) if args.exclude_pattern else None
    prefixes = tuple(item.rstrip("/") for item in args.path_prefix)

    resolved_items: List[Tuple[Dict[str, Any], str]] = []
    for raw in raw_items:
        candidates: List[Tuple[int, str]] = []
        for priority, attr in enumerate(preferred):
            url = raw["attrs"].get(attr)
            if not url and attr == "currentSrc":
                url = raw.get("currentSrc")
            if url:
                candidates.append(((len(preferred) - priority) * 100, url))

        for srcset_item in raw.get("srcset", []):
            candidates.append((80 + int(srcset_item.get("rank", 0)), srcset_item["url"]))

        # currentSrc 往往是浏览器实际选中的最高质量资源，排在普通 src 前。
        if raw.get("currentSrc") and "currentSrc" not in preferred:
            candidates.append((70, raw["currentSrc"]))
        if not candidates:
            continue

        candidates.sort(key=lambda item: item[0], reverse=True)
        url = candidates[0][1]
        resolved_items.append((raw, url))

    effective_prefixes = prefixes
    has_blob_urls = any(
        url.startswith("blob:")
        for _, url in resolved_items
    )
    if (
        not effective_prefixes
        and not has_blob_urls
        and len(resolved_items) >= 3
    ):
        directory_counts: Dict[str, int] = {}
        for _, candidate_url in resolved_items:
            directory = posixpath.dirname(urlparse(candidate_url).path).rstrip("/") + "/"
            directory_counts[directory] = directory_counts.get(directory, 0) + 1
        dominant_directory, dominant_count = max(
            directory_counts.items(),
            key=lambda item: item[1],
        )
        if dominant_count >= 3 and dominant_count / len(resolved_items) >= 0.85:
            effective_prefixes = (dominant_directory,)
            print(
                f"自动识别图片路径前缀：{dominant_directory}",
                flush=True,
            )

    selected: List[ImageItem] = []
    seen_urls = set()
    for raw, url in resolved_items:
        parsed = urlparse(url)
        searchable = f"{parsed.netloc}{parsed.path}?{parsed.query}"

        if effective_prefixes and not any(
            parsed.path.startswith(prefix) for prefix in effective_prefixes
        ):
            continue
        if include_re and not include_re.search(url):
            continue
        if exclude_re and exclude_re.search(searchable):
            continue

        natural_width = int(raw.get("naturalWidth", 0))
        natural_height = int(raw.get("naturalHeight", 0))
        element_width = int(raw.get("elementWidth", 0))
        element_height = int(raw.get("elementHeight", 0))
        is_loaded = natural_width > 1 and natural_height > 1

        # 精确 path-prefix 已经限定相册时，不再用尺寸淘汰短图。
        if not effective_prefixes and is_loaded:
            if natural_width < args.min_width or natural_height < args.min_height:
                continue
        elif not effective_prefixes and not is_loaded:
            if element_width and element_width < args.min_width:
                continue
            if element_height and element_height < args.min_height:
                continue

        if url in seen_urls:
            continue
        seen_urls.add(url)
        selected.append(
            ImageItem(
                order=len(selected) + 1,
                url=url,
                page_element_width=element_width,
                page_element_height=element_height,
                natural_width=natural_width,
                natural_height=natural_height,
            )
        )

    return selected


def extract_declared_managed_image_items(
    page: Page,
    args: argparse.Namespace,
) -> List[ImageItem]:
    """Read the decrypted chapter image list exposed by managed readers."""
    try:
        raw_items = page.evaluate(
            """(selector) => {
                if (
                    !window.params ||
                    !Array.isArray(window.params.chapter_images)
                ) {
                    return [];
                }
                const managed = [...document.querySelectorAll(selector)]
                    .filter((image) => image.closest(".chapter-image"));
                const images = window.params.chapter_images;
                if (!images.length || managed.length !== images.length) {
                    return [];
                }
                const host = (
                    window.params.images_domain ||
                    (window.params.images_hosts || [])[0] ||
                    window.params.cdnurl ||
                    ""
                );
                const resolve = (value) => {
                    if (!value) return "";
                    if (/^(?:[a-z][a-z0-9+.-]*:|\\/\\/)/i.test(value)) {
                        return value;
                    }
                    if (!host) return value;
                    return (
                        String(host).replace(/\\/+$/, "") +
                        "/" +
                        String(value).replace(/^\\/+/, "")
                    );
                };
                const local = Array.isArray(
                    window.params.chapter_local_images
                ) ? window.params.chapter_local_images : [];
                return images.map((value, index) => ({
                    url: resolve(value),
                    localUrl: resolve(local[index] || "")
                }));
            }""",
            args.selector,
        )
    except Exception:
        return []

    items: List[ImageItem] = []
    seen = set()
    for raw in raw_items:
        url = str(raw.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        items.append(
            ImageItem(
                order=len(items) + 1,
                url=url,
                page_element_width=0,
                page_element_height=0,
                natural_width=0,
                natural_height=0,
            )
        )
    return items


def safe_filename(url: str, fallback_order: int, existing: set) -> str:
    parsed = urlparse(url)
    if parsed.scheme == "blob":
        basename = (
            f"blob_{hashlib.sha1(url.encode('utf-8')).hexdigest()[:12]}.webp"
        )
    else:
        basename = unquote(Path(parsed.path).name)
    basename = re.sub(r"[^0-9A-Za-z._-]+", "_", basename).strip("._")
    if not basename:
        basename = f"{fallback_order:05d}"

    # 无扩展名时预留位置，下载成功后根据真实格式补全。
    stem = Path(basename).stem or f"{fallback_order:05d}"
    suffix = Path(basename).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp", ".svg"}:
        stem = basename
        suffix = ""

    name = f"{fallback_order:05d}_{stem}{suffix}" if not stem.startswith(f"{fallback_order:05d}") else f"{stem}{suffix}"
    if name in existing:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
        name = f"{Path(name).stem}_{digest}{Path(name).suffix}"
    existing.add(name)
    return name


def sniff_image_type(blob: bytes) -> Optional[str]:
    if blob.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if blob.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(blob) >= 16 and blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return ".webp"
    if len(blob) >= 12 and blob[4:8] == b"ftyp" and blob[8:12] in {
        b"avif", b"avis", b"heic", b"heix", b"mif1", b"msf1"
    }:
        return ".avif"
    if blob.startswith(b"BM"):
        return ".bmp"
    stripped = blob.lstrip().lower()
    if stripped.startswith(b"<svg") or (
        stripped.startswith(b"<?xml") and b"<svg" in stripped[:512]
    ):
        return ".svg"
    return None


def existing_file_is_image(path: Path) -> bool:
    try:
        if path.stat().st_size < 12:
            return False
        with path.open("rb") as handle:
            return sniff_image_type(handle.read(512)) is not None
    except OSError:
        return False


def find_existing_images(album_dir: Path) -> List[Path]:
    if not album_dir.is_dir():
        return []
    images: List[Path] = []
    for path in album_dir.rglob("*"):
        if not path.is_file() or path.name.endswith(".part"):
            continue
        if existing_file_is_image(path):
            images.append(path)
    return sorted(images)


def result_from_existing(task: DownloadTask) -> DownloadResult:
    size = task.path.stat().st_size
    digest = hashlib.sha256()
    with task.path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return DownloadResult(
        order=task.order,
        url=task.url,
        path=task.path,
        status="skipped",
        size=size,
        sha256=digest.hexdigest(),
    )


def download_with_requests(
    task: DownloadTask,
    context: DownloadContext,
) -> DownloadResult:
    if task.path.exists() and existing_file_is_image(task.path):
        return result_from_existing(task)

    task.path.parent.mkdir(parents=True, exist_ok=True)
    partial = task.path.with_name(task.path.name + ".part")
    last_error = ""

    for attempt in range(1, context.retries + 2):
        try:
            response = context.session().get(
                task.url,
                headers=context.headers(),
                stream=True,
                timeout=(10, context.timeout),
            )
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if content_type and not (
                content_type.startswith("image/")
                or content_type.startswith("application/octet-stream")
            ):
                raise ValueError(f"响应不是图片: {content_type}")

            digest = hashlib.sha256()
            size = 0
            first_chunk = b""
            with partial.open("wb") as handle:
                for chunk in response.iter_content(CHUNK_SIZE):
                    if not chunk:
                        continue
                    if not first_chunk:
                        first_chunk = chunk[:512]
                    handle.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)

            image_type = sniff_image_type(first_chunk)
            if not image_type:
                raise ValueError("响应内容不是可识别的图片")

            expected = response.headers.get("Content-Length")
            if expected and expected.isdigit() and int(expected) != size:
                raise ValueError(f"文件不完整：期望 {expected} 字节，实际 {size} 字节")

            final_path = task.path
            detected_suffix = image_type.lower()
            current_suffix = final_path.suffix.lower()
            if current_suffix in {"", ".jpeg"} and detected_suffix:
                final_path = final_path.with_suffix(detected_suffix)

            os.replace(partial, final_path)
            return DownloadResult(
                order=task.order,
                url=task.url,
                path=final_path,
                status="downloaded",
                size=size,
                sha256=digest.hexdigest(),
            )
        except Exception as exc:  # requests 与文件系统异常在重试后统一返回
            last_error = f"{type(exc).__name__}: {exc}"
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass
            if attempt <= context.retries:
                time.sleep(min(8.0, 0.7 * (2 ** (attempt - 1))))

    return DownloadResult(
        order=task.order,
        url=task.url,
        path=task.path,
        status="failed",
        error=last_error,
    )


def download_with_browser(
    task: DownloadTask,
    capture_page: Page,
    timeout_seconds: int,
    retries: int,
    referer: str = "",
) -> DownloadResult:
    """
    用专用 Chrome 标签页直接导航到图片 URL，并读取浏览器响应。

    与 BrowserContext.request 不同，这种方式会实际经过 Chrome 的代理、
    DNS、证书和网络扩展环境，适合浏览器能打开但 Python requests 无法连接的 CDN。
    """
    if task.path.exists() and existing_file_is_image(task.path):
        return result_from_existing(task)

    task.path.parent.mkdir(parents=True, exist_ok=True)
    partial = task.path.with_name(task.path.name + ".part")
    last_error = ""

    for attempt in range(1, retries + 2):
        try:
            response = capture_page.context.request.get(
                task.url,
                headers={"Referer": referer} if referer else {},
                timeout=timeout_seconds * 1000,
            )
            if response.status >= 400:
                raise ValueError(f"HTTP {response.status}")

            content_type = response.headers.get("content-type", "").lower()
            if content_type and not (
                content_type.startswith("image/")
                or content_type.startswith("application/octet-stream")
            ):
                raise ValueError(f"响应不是图片: {content_type}")

            body = response.body()
            image_type = sniff_image_type(body[:512])
            if not image_type:
                raise ValueError("响应内容不是可识别的图片")

            partial.write_bytes(body)
            final_path = task.path
            if not final_path.suffix and image_type:
                final_path = final_path.with_suffix(image_type)
            os.replace(partial, final_path)
            return DownloadResult(
                order=task.order,
                url=task.url,
                path=final_path,
                status="downloaded",
                size=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
            )
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass
            if attempt <= retries:
                capture_page.wait_for_timeout(
                    min(8000, int(700 * (2 ** (attempt - 1))))
                )

    return DownloadResult(
        order=task.order,
        url=task.url,
        path=task.path,
        status="failed",
        error=last_error,
    )


def browser_cookies(context: BrowserContext) -> List[Dict[str, Any]]:
    cookies = []
    for cookie in context.cookies():
        if cookie.get("name") and cookie.get("value") is not None:
            cookies.append(cookie)
    return cookies


def run_downloads_direct(
    tasks: Sequence[DownloadTask],
    context: DownloadContext,
    workers: int,
    offset: int = 0,
) -> List[DownloadResult]:
    results: List[DownloadResult] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(download_with_requests, task, context): task
            for task in tasks
        }
        total = offset + len(futures)
        for completed, future in enumerate(
            as_completed(futures), start=offset + 1
        ):
            result = future.result()
            results.append(result)
            marker = "跳过" if result.status == "skipped" else (
                "完成" if result.status == "downloaded" else "失败"
            )
            print(
                f"[{completed}/{total}] {marker}: {result.path.name}"
                + (f" ({result.error})" if result.error else "")
            )
    return results


def run_downloads_browser(
    tasks: Sequence[DownloadTask],
    capture_page: Page,
    timeout_seconds: int,
    retries: int,
    offset: int = 0,
    referer: str = "",
) -> List[DownloadResult]:
    results: List[DownloadResult] = []
    total = offset + len(tasks)
    for completed, task in enumerate(tasks, start=offset + 1):
        result = download_with_browser(
            task,
            capture_page,
            timeout_seconds=timeout_seconds,
            retries=retries,
            referer=referer,
        )
        results.append(result)
        marker = "跳过" if result.status == "skipped" else (
            "完成" if result.status == "downloaded" else "失败"
        )
        print(
            f"[{completed}/{total}] {marker}: {result.path.name}"
            + (f" ({result.error})" if result.error else ""),
            flush=True,
        )
    return results


def page_uses_canvas_rendering(page: Page) -> bool:
    try:
        return bool(
            page.evaluate(
                """() => {
                    if (
                        document.querySelectorAll('.scramble-page canvas').length > 0
                        || document.querySelectorAll('img + canvas').length > 0
                    ) {
                        return true;
                    }
                    const aid = parseInt(window.aid, 10);
                    const scrambleId = parseInt(window.scramble_id, 10);
                    return (
                        Number.isFinite(aid)
                        && Number.isFinite(scrambleId)
                        && aid >= scrambleId
                        && document.querySelector('.scramble-page img') !== null
                    );
                }"""
            )
        )
    except Exception:
        return False


def page_uses_blob_images(page: Page, selector: str) -> bool:
    """Detect images decrypted by the site into in-memory blob URLs."""
    try:
        return bool(
            page.evaluate(
                """(selector) => [...document.querySelectorAll(selector)]
                    .some((image) => {
                        const src = image.currentSrc || image.src || "";
                        return src.startsWith("blob:");
                    })""",
                selector,
            )
        )
    except Exception:
        return False


def canvas_image_data_url(
    page: Page,
    image_url: str,
    render_format: str,
    render_quality: int,
    wait_seconds: float,
) -> Tuple[Optional[str], str, int, int]:
    """Export the decoded blob image from the browser canvas."""
    result = page.evaluate(
        """async ({url, format, quality, waitSeconds}) => {
            const image = [...document.images].find((item) => {
                return item.currentSrc === url || item.src === url;
            });
            if (!image) {
                return {ok: false, error: "页面上找不到目标 Blob 图片"};
            }
            image.scrollIntoView({block: "center", inline: "center"});
            const deadline = performance.now() + waitSeconds * 1000;
            while (
                !image.complete
                || image.naturalWidth <= 1
                || image.naturalHeight <= 1
            ) {
                if (performance.now() >= deadline) {
                    return {ok: false, error: "Blob 图片解码超时"};
                }
                await new Promise((resolve) => setTimeout(resolve, 100));
            }
            try {
                if (typeof image.decode === "function") {
                    await image.decode();
                }
            } catch (_) {
                if (image.naturalWidth <= 1) {
                    return {ok: false, error: "Blob 图片无法解码"};
                }
            }
            const canvas = document.createElement("canvas");
            canvas.width = image.naturalWidth;
            canvas.height = image.naturalHeight;
            const context = canvas.getContext("2d");
            if (!context) {
                return {ok: false, error: "无法创建 Canvas 上下文"};
            }
            try {
                context.drawImage(image, 0, 0);
                const mime = format === "png" ? "image/png" : "image/webp";
                const dataUrl = canvas.toDataURL(
                    mime,
                    format === "png" ? undefined : quality / 100
                );
                return {
                    ok: dataUrl.startsWith(`data:${mime};base64,`),
                    dataUrl,
                    width: image.naturalWidth,
                    height: image.naturalHeight,
                    error: ""
                };
            } catch (error) {
                return {
                    ok: false,
                    width: image.naturalWidth,
                    height: image.naturalHeight,
                    error: String(error)
                };
            }
        }""",
        {
            "url": image_url,
            "format": render_format,
            "quality": max(0, min(100, render_quality)),
            "waitSeconds": max(0.0, wait_seconds),
        },
    )
    return (
        str(result.get("dataUrl") or "") if result.get("ok") else None,
        str(result.get("error") or ""),
        int(result.get("width") or 0),
        int(result.get("height") or 0),
    )


def download_blob_canvas_image(
    task: DownloadTask,
    page: Page,
    wait_seconds: float,
    render_format: str,
    render_quality: int,
) -> DownloadResult:
    if task.path.exists() and existing_file_is_image(task.path):
        return result_from_existing(task)

    task.path.parent.mkdir(parents=True, exist_ok=True)
    partial = task.path.with_name(task.path.name + ".part")
    last_error = ""
    for attempt in range(1, 4):
        try:
            data_url, error, width, height = canvas_image_data_url(
                page,
                task.url,
                render_format,
                render_quality,
                wait_seconds,
            )
            if not data_url:
                raise ValueError(error or "Canvas 未返回图片数据")
            if width < 64 or height < 64:
                raise ValueError(f"图片尺寸异常：{width}×{height}")
            header, separator, payload = data_url.partition(",")
            if not separator or ";base64" not in header:
                raise ValueError("Canvas 返回的数据格式不正确")
            body = base64.b64decode(payload, validate=True)
            image_type = sniff_image_type(body[:512])
            expected = ".webp" if render_format == "webp" else ".png"
            if image_type != expected:
                raise ValueError(
                    f"图片编码异常：期望 {expected}，实际 {image_type or 'unknown'}"
                )
            partial.write_bytes(body)
            os.replace(partial, task.path)
            return DownloadResult(
                order=task.order,
                url=task.url,
                path=task.path,
                status="downloaded",
                size=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
            )
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass
            if attempt < 3:
                page.wait_for_timeout(400)
    return DownloadResult(
        order=task.order,
        url=task.url,
        path=task.path,
        status="failed",
        error=last_error,
    )


def run_blob_canvas_downloads(
    tasks: Sequence[DownloadTask],
    page: Page,
    wait_seconds: float,
    render_format: str,
    render_quality: int,
) -> List[DownloadResult]:
    results: List[DownloadResult] = []
    total = len(tasks)
    for completed, task in enumerate(tasks, start=1):
        result = download_blob_canvas_image(
            task,
            page,
            wait_seconds=wait_seconds,
            render_format=render_format,
            render_quality=render_quality,
        )
        results.append(result)
        marker = "跳过" if result.status == "skipped" else (
            "完成" if result.status == "downloaded" else "失败"
        )
        print(
            f"[{completed}/{total}] {marker}: {result.path.name}"
            + (f" ({result.error})" if result.error else ""),
            flush=True,
        )
    return results


def find_visible_image_element(
    page: Page,
    image_url: str,
    wait_seconds: float,
    require_canvas: bool,
) -> Optional[Any]:
    """定位页面最终显示的 Canvas 或图片元素，并等待懒加载完成。"""
    scroll_script = """(url) => {
        const pathOf = (value) => {
            if (!value) return '';
            try { return new URL(value, document.baseURI).pathname; } catch (_) { return ''; }
        };
        const targetPath = pathOf(url);
        const img = [...document.images].find((item) => {
            return [
                item.currentSrc,
                item.src,
                item.getAttribute('data-original'),
                item.getAttribute('data-src')
            ].some((value) => pathOf(value) === targetPath);
        });
        if (!img) return false;
        const container = img.closest('.scramble-page') || img.parentElement;
        if (container) {
            container.scrollIntoView({ block: 'center', inline: 'center' });
        } else {
            img.scrollIntoView({ block: 'center', inline: 'center' });
        }
        if (img.naturalWidth <= 1) {
            const original = img.getAttribute('data-original') || img.getAttribute('data-src');
            if (original) img.src = original;
        }
        window.dispatchEvent(new Event('scroll'));
        return true;
    }"""
    element_script = """(url) => {
        const pathOf = (value) => {
            if (!value) return '';
            try { return new URL(value, document.baseURI).pathname; } catch (_) { return ''; }
        };
        const targetPath = pathOf(url);
        const img = [...document.images].find((item) => {
            return [
                item.currentSrc,
                item.src,
                item.getAttribute('data-original'),
                item.getAttribute('data-src')
            ].some((value) => pathOf(value) === targetPath);
        });
        if (!img) return null;
        const container = img.closest('.scramble-page') || img.parentElement;
        const canvas = container?.querySelector(':scope > canvas')
            || container?.querySelector('canvas');
        if (canvas && canvas.width >= 64 && canvas.height >= 64) return canvas;
        if (%s) return null;
        return img.complete && img.naturalWidth > 1 ? img : null;
    }"""
    element_script = element_script % ("true" if require_canvas else "false")
    trigger_restore_script = """(url) => {
        const pathOf = (value) => {
            if (!value) return '';
            try { return new URL(value, document.baseURI).pathname; } catch (_) { return ''; }
        };
        const targetPath = pathOf(url);
        const img = [...document.images].find((item) => {
            return [
                item.currentSrc,
                item.src,
                item.getAttribute('data-original'),
                item.getAttribute('data-src')
            ].some((value) => pathOf(value) === targetPath);
        });
        if (!img || img.naturalWidth <= 1) return false;
        const container = img.closest('.scramble-page') || img.parentElement;
        const canvas = container?.querySelector('canvas');
        if (canvas && canvas.width >= 64 && canvas.height >= 64) return true;
        if (canvas) canvas.remove();
        if (typeof onImageLoadedForNew === 'function') {
            img.dataset.scrambleProcessed = 'false';
            onImageLoadedForNew(img);
            return true;
        }
        return false;
    }"""

    if not page.evaluate(scroll_script, image_url):
        return None

    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        if require_canvas:
            page.evaluate(trigger_restore_script, image_url)
        handle = page.evaluate_handle(element_script, image_url)
        element = handle.as_element()
        if element is not None:
            try:
                box = element.bounding_box()
            except Exception:
                box = None
            if box and box["width"] >= 64 and box["height"] >= 64:
                return element
        if time.monotonic() >= deadline:
            return None
        page.wait_for_timeout(200)


def hide_overlapping_overlays(page: Page, image_url: str) -> int:
    """截图前隐藏覆盖在目标图片上的固定导航或粘滞浮层。"""
    return int(
        page.evaluate(
            """(url) => {
                const pathOf = (value) => {
                    if (!value) return '';
                    try {
                        return new URL(value, document.baseURI).pathname;
                    } catch (_) {
                        return '';
                    }
                };
                const targetPath = pathOf(url);
                const img = [...document.images].find((item) => {
                    return [
                        item.currentSrc,
                        item.src,
                        item.getAttribute('data-original'),
                        item.getAttribute('data-src')
                    ].some((value) => pathOf(value) === targetPath);
                });
                if (!img) return 0;
                const container = img.closest('.scramble-page') || img.parentElement;
                const visual = container?.querySelector('canvas') || img;
                if (!visual) return 0;
                const targetRect = visual.getBoundingClientRect();
                let hidden = 0;
                for (const element of document.querySelectorAll('body *')) {
                    if (
                        element === visual
                        || element.contains(visual)
                        || visual.contains(element)
                    ) {
                        continue;
                    }
                    const style = getComputedStyle(element);
                    if (!['fixed', 'sticky'].includes(style.position)) continue;
                    const rect = element.getBoundingClientRect();
                    if (rect.width < 1 || rect.height < 1) continue;
                    const overlaps = (
                        rect.left < targetRect.right
                        && rect.right > targetRect.left
                        && rect.top < targetRect.bottom
                        && rect.bottom > targetRect.top
                    );
                    if (!overlaps) continue;
                    element.dataset.__codexOverlayHidden = '1';
                    element.dataset.__codexVisibility = element.style.visibility;
                    element.dataset.__codexVisibilityPriority =
                        element.style.getPropertyPriority('visibility');
                    element.style.setProperty('visibility', 'hidden', 'important');
                    hidden += 1;
                }
                return hidden;
            }""",
            image_url,
        )
    )


def restore_overlapping_overlays(page: Page) -> None:
    page.evaluate(
        """() => {
            for (const element of document.querySelectorAll(
                '[data-codex-overlay-hidden="1"]'
            )) {
                const visibility = element.dataset.__codexVisibility || '';
                const priority = element.dataset.__codexVisibilityPriority || '';
                if (visibility) {
                    element.style.setProperty('visibility', visibility, priority);
                } else {
                    element.style.removeProperty('visibility');
                }
                delete element.dataset.__codexOverlayHidden;
                delete element.dataset.__codexVisibility;
                delete element.dataset.__codexVisibilityPriority;
            }
        }"""
    )


def get_rendered_native_size(element: Any) -> Optional[Tuple[int, int]]:
    try:
        dimensions = element.evaluate(
            """(element) => {
                if (element.tagName === 'CANVAS') {
                    return {
                        width: Number(element.width),
                        height: Number(element.height)
                    };
                }
                return {
                    width: Number(element.naturalWidth),
                    height: Number(element.naturalHeight)
                };
            }"""
        )
        width = int(dimensions["width"])
        height = int(dimensions["height"])
        if width >= 64 and height >= 64:
            return width, height
    except Exception:
        pass
    return None


def encode_rendered_capture(
    capture_path: Path,
    output_path: Path,
    native_size: Optional[Tuple[int, int]],
    render_format: str,
    render_quality: int,
    allow_blank: bool = False,
) -> bytes:
    """缩放回 Canvas 原生尺寸，并编码为 WebP 或优化后的 PNG。"""
    with Image.open(capture_path) as image:
        frame = image.convert("RGB")
        if native_size is not None and frame.size != native_size:
            frame = frame.resize(native_size, Image.Resampling.LANCZOS)

        if frame.width < 64 or frame.height < 64:
            raise ValueError(f"渲染尺寸异常：{frame.width}×{frame.height}")
        deviation = max(ImageStat.Stat(frame).stddev)
        if deviation < 1.0 and not allow_blank:
            raise ValueError("渲染截图接近纯色空白")

        partial = output_path.with_name(output_path.name + ".part")
        if render_format == "webp":
            frame.save(
                partial,
                format="WEBP",
                quality=max(0, min(100, render_quality)),
                method=6,
                lossless=False,
            )
        else:
            frame.save(
                partial,
                format="PNG",
                optimize=True,
                compress_level=9,
            )

    body = partial.read_bytes()
    image_type = sniff_image_type(body[:512])
    expected = ".webp" if render_format == "webp" else ".png"
    if image_type != expected:
        partial.unlink(missing_ok=True)
        raise ValueError(
            f"编码结果格式异常：期望 {expected}，实际 {image_type or 'unknown'}"
        )
    os.replace(partial, output_path)
    return body


def rendered_capture_blank_signature(
    capture_path: Path,
    native_size: Optional[Tuple[int, int]],
) -> Optional[Tuple[Tuple[float, ...], float]]:
    with Image.open(capture_path) as image:
        frame = image.convert("RGB")
        if native_size is not None and frame.size != native_size:
            frame = frame.resize(native_size, Image.Resampling.LANCZOS)
        stats = ImageStat.Stat(frame)
        deviation = max(stats.stddev)
        if deviation >= 1.0:
            return None
        return tuple(round(value, 2) for value in stats.mean), round(deviation, 2)


def download_rendered_image(
    task: DownloadTask,
    page: Page,
    wait_seconds: float,
    timeout_seconds: int,
    require_canvas: bool,
    render_format: str,
    render_quality: int,
    force: bool = False,
) -> DownloadResult:
    if not force and task.path.exists() and existing_file_is_image(task.path):
        return result_from_existing(task)

    task.path.parent.mkdir(parents=True, exist_ok=True)
    capture = task.path.with_name(task.path.stem + ".capture.png")
    try:
        capture.unlink(missing_ok=True)
    except OSError:
        pass
    last_error = ""
    blank_signatures: List[Tuple[Tuple[float, ...], float]] = []
    for attempt in range(1, 4):
        blank_signature = None
        try:
            element = find_visible_image_element(
                page,
                task.url,
                wait_seconds,
                require_canvas=require_canvas,
            )
            if element is None:
                raise ValueError("页面上找不到尺寸有效的 Canvas 或图片元素")
            native_size = get_rendered_native_size(element)
            hidden_overlays = hide_overlapping_overlays(page, task.url)
            try:
                element.screenshot(
                    path=str(capture),
                    type="png",
                    timeout=timeout_seconds * 1000,
                )
            finally:
                restore_overlapping_overlays(page)
            if hidden_overlays:
                print(
                    f"已隐藏 {hidden_overlays} 个遮挡导航/浮层后截图："
                    f"{task.path.name}",
                    flush=True,
                )
            blank_signature = rendered_capture_blank_signature(
                capture,
                native_size,
            )
            body = encode_rendered_capture(
                capture,
                task.path,
                native_size=native_size,
                render_format=render_format,
                render_quality=render_quality,
            )
            try:
                capture.unlink(missing_ok=True)
            except OSError:
                pass
            if not body.startswith(b"\x89PNG\r\n\x1a\n"):
                if render_format == "png":
                    raise ValueError("页面截图不是有效 PNG")
            return DownloadResult(
                order=task.order,
                url=task.url,
                path=task.path,
                status="downloaded",
                size=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
            )
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if blank_signature is not None:
                blank_signatures.append(blank_signature)
            else:
                blank_signatures.clear()
            temporary_paths = [task.path.with_name(task.path.name + ".part")]
            if blank_signature is None:
                temporary_paths.append(capture)
            for temporary_path in temporary_paths:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            if attempt < 3:
                page.wait_for_timeout(800)

    if len(blank_signatures) >= 3 and capture.exists():
        means = [signature[0] for signature in blank_signatures[-3:]]
        deviations = [signature[1] for signature in blank_signatures[-3:]]
        channel_spread = max(
            max(values[index] for values in means)
            - min(values[index] for values in means)
            for index in range(3)
        )
        if channel_spread <= 2.0 and max(deviations) <= 2.0:
            body = encode_rendered_capture(
                capture,
                task.path,
                native_size=native_size,
                render_format=render_format,
                render_quality=render_quality,
                allow_blank=True,
            )
            capture.unlink(missing_ok=True)
            print(
                f"检测到稳定空白页，按合法页面保存：{task.path.name}",
                flush=True,
            )
            return DownloadResult(
                order=task.order,
                url=task.url,
                path=task.path,
                status="downloaded",
                size=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
            )

    return DownloadResult(
        order=task.order,
        url=task.url,
        path=task.path,
        status="failed",
        error=last_error,
    )


def run_rendered_downloads(
    tasks: Sequence[DownloadTask],
    page: Page,
    wait_seconds: float,
    timeout_seconds: int,
    require_canvas: bool,
    render_format: str,
    render_quality: int,
    force: bool = False,
) -> List[DownloadResult]:
    results: List[DownloadResult] = []
    total = len(tasks)
    for completed, task in enumerate(tasks, start=1):
        result = download_rendered_image(
            task,
            page,
            wait_seconds=wait_seconds,
            timeout_seconds=timeout_seconds,
            require_canvas=require_canvas,
            render_format=render_format,
            render_quality=render_quality,
            force=force,
        )
        results.append(result)
        marker = "跳过" if result.status == "skipped" else (
            "完成" if result.status == "downloaded" else "失败"
        )
        print(
            f"[{completed}/{total}] {marker}: {result.path.name}"
            + (f" ({result.error})" if result.error else ""),
            flush=True,
        )
    return results


def write_manifest(
    out_dir: Path,
    source_url: str,
    page_title: str,
    items: Sequence[ImageItem],
    results: Sequence[DownloadResult],
    render_mode: str,
    render_format: str,
    render_quality: Optional[int],
) -> None:
    result_by_order = {result.order: result for result in results}
    manifest = {
        "source_url": source_url,
        "title": page_title,
        "categories": categories_from_raw_title(page_title),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "render_mode": render_mode,
        "render_format": render_format,
        "render_quality": render_quality,
        "image_count": len(items),
        "downloaded": sum(r.status == "downloaded" for r in results),
        "skipped": sum(r.status == "skipped" for r in results),
        "failed": sum(r.status == "failed" for r in results),
        "items": [
            {
                "order": item.order,
                "source": item.url,
                "file": (
                    str(result_by_order[item.order].path.relative_to(out_dir))
                    if item.order in result_by_order
                    else ""
                ),
                "status": (
                    result_by_order[item.order].status
                    if item.order in result_by_order
                    else "not_downloaded"
                ),
                "size": (
                    result_by_order[item.order].size
                    if item.order in result_by_order
                    else 0
                ),
                "sha256": (
                    result_by_order[item.order].sha256
                    if item.order in result_by_order
                    else ""
                ),
                "error": (
                    result_by_order[item.order].error
                    if item.order in result_by_order
                    else ""
                ),
            }
            for item in items
        ],
    }
    temporary = out_dir / "manifest.json.tmp"
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, out_dir / "manifest.json")


def rendered_manifest_matches(
    out_dir: Path,
    render_format: str,
    render_quality: int,
) -> bool:
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        manifest.get("render_mode") in {"rendered", "blob-canvas"}
        and manifest.get("render_format") == render_format
        and manifest.get("render_quality") == render_quality
    )


def load_manifest(out_dir: Path) -> Dict[str, Any]:
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def resume_file_for_order(
    out_dir: Path,
    manifest: Dict[str, Any],
    order: int,
    *,
    use_rendered: bool,
    render_format: str,
) -> Optional[Path]:
    items = manifest.get("items")
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            item_order = int(item.get("order"))
        except (TypeError, ValueError):
            continue
        if item_order != order or item.get("status") not in {
            "downloaded",
            "skipped",
        }:
            continue
        relative = str(item.get("file") or "")
        if not relative:
            return None
        candidate = (out_dir / relative).resolve(strict=False)
        try:
            candidate.relative_to(out_dir.resolve())
        except ValueError:
            return None
        rendered = "_rendered." in candidate.name
        if use_rendered:
            if (
                not rendered
                or candidate.suffix.lower() != f".{render_format.lower()}"
            ):
                return None
        elif rendered:
            return None
        if candidate.is_file() and existing_file_is_image(candidate):
            return candidate
        return None
    return None


def write_download_marker(
    output_dir: Path,
    task_id: int,
    status: str,
) -> None:
    if task_id <= 0:
        return
    marker = output_dir / ".download-task.json"
    payload = {
        "task_id": task_id,
        "status": status,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    temporary = marker.with_name(marker.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, marker)


def read_download_marker(output_dir: Path) -> Dict[str, Any]:
    marker = output_dir / ".download-task.json"
    if not marker.is_file():
        return {}
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def find_chrome_executable(configured: Path) -> Path:
    candidates = [
        configured,
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary"),
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/google-chrome-stable"),
        Path("/usr/bin/chromium"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        "找不到 Chrome。请使用 --chrome-executable 指定 Chrome 可执行文件路径。"
    )


def reserve_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_cdp_endpoint(
    process: subprocess.Popen,
    endpoint: str,
    timeout_seconds: int = 20,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                "Chrome 进程提前退出，调试端口没有启动。"
                "请确认 --profile-dir 没有被另一个 Chrome 实例占用。"
            )
        try:
            with urllib.request.urlopen(endpoint + "/json/version", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError(
        "Chrome 调试端口启动超时。"
        "请关闭占用同一 --profile-dir 的 Chrome 后重试。"
    )


def launch_normal_chrome_with_cdp(
    playwright: Playwright,
    args: argparse.Namespace,
) -> BrowserSession:
    """
    先用普通 Chrome 进程打开专用用户目录，再通过 CDP 接入。

    这样不会向 Chrome 传入 Playwright 的 --enable-automation 等启动参数，
    navigator.webdriver 通常保持 false，人工完成 Cloudflare 验证后不容易循环。
    """
    if args.headless and not args.playwright_launch:
        raise RuntimeError(
            "--headless 无法人工完成 Cloudflare 验证。"
            "请去掉 --headless；自动化环境可加 --playwright-launch。"
        )

    chrome_path = find_chrome_executable(args.chrome_executable.expanduser())
    args.profile_dir.mkdir(parents=True, exist_ok=True)
    port = reserve_local_port()
    endpoint = f"http://127.0.0.1:{port}"
    command = [
        str(chrome_path),
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={args.profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--hide-crash-restore-bubble",
        "about:blank",
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        wait_for_cdp_endpoint(process, endpoint)
        browser = playwright.chromium.connect_over_cdp(endpoint)
        if not browser.contexts:
            raise RuntimeError("Chrome 已启动，但没有可用的浏览器上下文")
        print(
            "Chrome 模式：普通进程 + CDP 接入（不使用 Playwright 自动化启动参数）",
            flush=True,
        )
        return BrowserSession(
            context=browser.contexts[0],
            browser=browser,
            process=process,
            owns_browser=True,
        )
    except Exception:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        raise


def open_context(
    playwright: Playwright,
    args: argparse.Namespace,
) -> BrowserSession:
    if args.cdp_url:
        if ensure_chrome_cdp is not None:
            try:
                ensure_chrome_cdp(
                    args.cdp_url,
                    profile_dir=args.profile_dir,
                )
            except ChromeCDPError as exc:
                raise RuntimeError(str(exc)) from exc
        browser = playwright.chromium.connect_over_cdp(args.cdp_url)
        if not browser.contexts:
            raise RuntimeError("已连接到 Chrome，但没有可用的浏览器上下文")
        print("Chrome 模式：接入已有 CDP 浏览器", flush=True)
        return BrowserSession(
            context=browser.contexts[0],
            browser=browser,
            owns_browser=False,
        )

    if not args.playwright_launch:
        return launch_normal_chrome_with_cdp(playwright, args)

    args.profile_dir.mkdir(parents=True, exist_ok=True)
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(args.profile_dir),
        channel="chrome",
        headless=args.headless,
        accept_downloads=True,
        viewport={"width": 1440, "height": 900},
    )
    print(
        "Chrome 模式：Playwright 自动启动（Cloudflare 开启时可能被识别）",
        flush=True,
    )
    return BrowserSession(context=context, browser=None, owns_browser=False)


def close_browser_session(session: BrowserSession) -> None:
    if session.owns_browser and session.browser is not None:
        try:
            session.browser.close()
        except Exception:
            pass
    elif session.browser is None:
        session.context.close()

    if session.process is not None:
        try:
            session.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            session.process.terminate()
            try:
                session.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                session.process.kill()


def main() -> int:
    args = parse_args()
    args.out_root = args.out_root.expanduser().resolve()
    args.profile_dir = args.profile_dir.expanduser().resolve()
    if args.output_dir is not None:
        args.output_dir = args.output_dir.expanduser().resolve()
    if args.result_file is not None:
        args.result_file = args.result_file.expanduser().resolve()
    args.out_root.mkdir(parents=True, exist_ok=True)
    page_id = page_key_from_url(args.url)

    with sync_playwright() as playwright:
        browser_session = open_context(playwright, args)
        context = browser_session.context
        page = context.new_page()
        page.bring_to_front()
        page.set_default_timeout(args.timeout * 1000)
        capture_page: Optional[Page] = None
        output_dir: Optional[Path] = None

        try:
            print(f"打开页面：{args.url}", flush=True)
            page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout * 1000)
            try:
                page.wait_for_load_state("load", timeout=args.timeout * 1000)
            except PlaywrightTimeoutError:
                print("页面 load 事件超时，继续按当前 DOM 处理。", file=sys.stderr)
            page.wait_for_timeout(800)
            webdriver_flag = page.evaluate("navigator.webdriver")
            print(f"navigator.webdriver = {str(webdriver_flag).lower()}", flush=True)
            wait_for_manual_verification(
                page,
                verification_wait=args.verification_wait,
                interactive=not args.no_interactive,
            )

            page_title = (page.title() or "").strip()
            if args.output_dir is not None:
                output_dir = args.output_dir
                try:
                    output_dir.relative_to(args.out_root)
                except ValueError as exc:
                    raise RuntimeError(
                        "断点目录必须位于下载根目录内。"
                    ) from exc
                folder_name = output_dir.name
            else:
                folder_name = folder_name_from_page(page_title, args.url)
                output_dir = args.out_root / folder_name
            output_dir.mkdir(parents=True, exist_ok=True)
            write_download_marker(
                output_dir,
                args.download_task_id,
                "running",
            )
            print(f"页面标题：{page_title or '无标题'}", flush=True)
            print(f"保存目录：{output_dir}", flush=True)

            has_canvas_rendering = page_uses_canvas_rendering(page)
            use_rendered = args.render_mode == "screenshot" or (
                args.render_mode == "auto" and has_canvas_rendering
            )
            use_blob_canvas = (
                not use_rendered
                and page_uses_blob_images(page, args.selector)
            )
            print(
                "页面图片模式："
                + (
                    "Canvas 还原截图"
                    if use_rendered
                    else "Blob 内存图片 Canvas 导出"
                    if use_blob_canvas
                    else "CDN 原始图片"
                ),
                flush=True,
            )

            result_payload: Dict[str, Any] = {
                "url": args.url,
                "page_id": page_id,
                "title": page_title,
                "folder_name": folder_name,
                "output_dir": str(output_dir),
                "status": "failed",
                "downloaded": 0,
                "skipped": 0,
                "failed": 0,
                "existing_images": 0,
                "error": "",
            }

            if not args.force and not args.resume:
                existing_images = find_existing_images(output_dir)
                if existing_images:
                    rendered_existing = [
                        path for path in existing_images
                        if re.search(r"_rendered\.(?:png|webp)$", path.name, re.I)
                    ]
                    current_rendered_existing = [
                        path for path in rendered_existing
                        if path.suffix.lower() == f".{args.render_format}"
                    ]
                    original_existing = [
                        path for path in existing_images
                        if path not in rendered_existing
                    ]
                    if use_rendered:
                        compatible = (
                            bool(current_rendered_existing)
                            and rendered_manifest_matches(
                                output_dir,
                                render_format=args.render_format,
                                render_quality=args.render_quality,
                            )
                        )
                    elif use_blob_canvas:
                        compatible = (
                            bool(original_existing)
                            and rendered_manifest_matches(
                                output_dir,
                                render_format=args.render_format,
                                render_quality=args.render_quality,
                            )
                        )
                    else:
                        compatible = bool(original_existing)
                    if compatible:
                        print(
                            f"标题同名目录已有 {len(existing_images)} 张有效图片，"
                            "跳过下载。",
                            flush=True,
                        )
                        print(
                            f"下载结束：新增 0，已存在 {len(existing_images)}，失败 0。",
                            flush=True,
                        )
                        result_payload.update(
                            status="existing",
                            skipped=len(existing_images),
                            existing_images=len(existing_images),
                            render_mode=(
                                "rendered"
                                if use_rendered
                                else "blob-canvas"
                                if use_blob_canvas
                                else "original"
                            ),
                            render_format=(
                                args.render_format
                                if use_rendered or use_blob_canvas
                                else "original"
                            ),
                        )
                        if not args.dry_run:
                            write_album_metadata(
                                output_dir,
                                args.url,
                                page_id,
                                page_title,
                                folder_name_from_title(page_title),
                            )
                        write_download_marker(
                            output_dir,
                            args.download_task_id,
                            "success",
                        )
                        write_result_file(args.result_file, result_payload)
                        return 0
                    print(
                        "已有图片格式与当前页面模式不一致，"
                        "本次不会跳过下载。",
                        flush=True,
                    )

            items: List[ImageItem] = []
            prefer_direct_downloads = False
            declared_items = (
                []
                if use_rendered
                else extract_declared_managed_image_items(
                    page,
                    args,
                )
            )
            if declared_items:
                items = declared_items
                use_blob_canvas = False
                prefer_direct_downloads = True
                print(
                    "页面已提供完整的解密图片清单，"
                    f"直接使用 {len(items)} 个原始地址；"
                    "无需等待 DOM 懒加载队列。",
                    flush=True,
                )
            else:
                print("开始逐屏滚动并等待懒加载……", flush=True)
                scroll_metrics = scroll_until_stable(page, args)
                print(
                    "滚动结束："
                    f"{scroll_metrics['scrollCount']} 次，"
                    f"页面高度 {scroll_metrics['height']}px，"
                    f"DOM 图片 {scroll_metrics['imageCount']} 张，"
                    "目标图片已载入 "
                    f"{scroll_metrics['loadedTargetImages']} 张。",
                    flush=True,
                )

                managed_state = wait_for_managed_images(page, args)
                if (
                    managed_state["total"] > 0
                    and managed_state["loaded"] < managed_state["total"]
                ):
                    declared_items = extract_declared_managed_image_items(
                        page,
                        args,
                    )
                    if (
                        not use_rendered
                        and len(declared_items) == managed_state["total"]
                    ):
                        items = declared_items
                        use_blob_canvas = False
                        prefer_direct_downloads = True
                        print(
                            "站内懒加载队列未完成，已改用页面解密后的 "
                            f"{len(items)} 个原始图片地址，通过同一 Chrome "
                            "网络上下文下载。",
                            flush=True,
                        )
                    else:
                        message = (
                            "目标图片未完整加载："
                            f"{managed_state['loaded']}/"
                            f"{managed_state['total']}，"
                            f"待处理 {managed_state['unresolved']} 张"
                        )
                        print(message, file=sys.stderr, flush=True)
                        result_payload["error"] = message
                        write_result_file(args.result_file, result_payload)
                        write_download_marker(
                            output_dir,
                            args.download_task_id,
                            "failed",
                        )
                        return 3
                else:
                    items = extract_image_items(page, args)
            if not items:
                current_state = page_access_state(page)
                print(
                    "没有匹配到图片。"
                    f"\n当前标题：{current_state['title'] or '无标题'}"
                    f"\n当前地址：{current_state['url']}"
                    "\n请检查 --selector、--path-prefix 和过滤参数；"
                    "若页面需要登录，请先在 Chrome 中完成登录。",
                    file=sys.stderr,
                )
                result_payload["error"] = "没有匹配到图片"
                write_result_file(args.result_file, result_payload)
                write_download_marker(
                    output_dir,
                    args.download_task_id,
                    "failed",
                )
                return 2

            if (
                not use_rendered
                and not use_blob_canvas
                and any(item.url.startswith("blob:") for item in items)
            ):
                use_blob_canvas = True
                print(
                    "检测到懒加载后生成的 Blob 图片，"
                    "切换为 Canvas 原尺寸导出。",
                    flush=True,
                )

            urls_path = output_dir / "image_urls.txt"
            urls_path.write_text(
                "\n".join(f"{item.order:05d}\t{item.url}" for item in items) + "\n",
                encoding="utf-8",
            )
            print(f"提取到 {len(items)} 张图片，URL 清单：{urls_path}")

            if args.dry_run:
                print("dry-run 已开启，不执行下载。")
                result_payload.update(status="success", dry_run=True)
                write_result_file(args.result_file, result_payload)
                return 0

            resume_manifest = load_manifest(output_dir) if args.resume else {}
            used_names: set = set()
            tasks: List[DownloadTask] = []
            resume_paths: Dict[int, Path] = {}
            for item in items:
                previous_path = resume_file_for_order(
                    output_dir,
                    resume_manifest,
                    item.order,
                    use_rendered=use_rendered,
                    render_format=args.render_format,
                )
                if previous_path is not None:
                    path = previous_path
                    used_names.add(path.name)
                    resume_paths[item.order] = path
                else:
                    path = output_dir / safe_filename(
                        item.url,
                        item.order,
                        used_names,
                    )
                tasks.append(
                    DownloadTask(
                        order=item.order,
                        url=item.url,
                        path=path,
                    )
                )

            user_agent = page.evaluate("navigator.userAgent")
            download_context = DownloadContext(
                cookies=browser_cookies(context),
                user_agent=user_agent,
                referer=page.url,
                retries=args.retries,
                timeout=args.timeout,
            )

            if use_rendered:
                download_mode = "rendered"
            elif use_blob_canvas:
                download_mode = "blob-canvas"
            else:
                download_mode = (
                    "browser" if args.via_browser else args.download_mode
                )
                if (
                    download_mode == "auto"
                    and args.cdp_url
                    and not prefer_direct_downloads
                ):
                    # 用户已经明确接入浏览器，优先使用该浏览器的网络环境，
                    # 避免先从本机 requests 产生一整轮 CDN ConnectionError。
                    download_mode = "browser"
            task_by_order = {task.order: task for task in tasks}

            def get_capture_page() -> Page:
                nonlocal capture_page
                if capture_page is None:
                    capture_page = context.new_page()
                    capture_page.set_default_timeout(args.timeout * 1000)
                return capture_page

            def browser_fallback(
                failed_results: Sequence[DownloadResult],
                offset: int = 0,
            ) -> Dict[int, DownloadResult]:
                retry_tasks = [
                    task_by_order[result.order]
                    for result in failed_results
                    if result.status == "failed"
                ]
                if not retry_tasks:
                    return {}
                print(
                    f"直连失败 {len(retry_tasks)} 张，"
                    "切换到 Chrome 页面网络下载……",
                    flush=True,
                )
                fallback_results = run_downloads_browser(
                    retry_tasks,
                    get_capture_page(),
                    timeout_seconds=args.timeout,
                    retries=args.retries,
                    offset=offset,
                    referer=page.url,
                )
                return {result.order: result for result in fallback_results}

            print(f"下载模式：{download_mode}", flush=True)
            if download_mode == "rendered":
                print(
                    "检测到页面使用 Canvas 还原乱序图片，"
                    "将保存浏览器中实际显示的图片。",
                    flush=True,
                )
                rendered_tasks = [
                    DownloadTask(
                        order=item.order,
                        url=item.url,
                        path=resume_paths.get(
                            item.order,
                            output_dir
                            / f"{item.order:05d}_rendered.{args.render_format}",
                        ),
                    )
                    for item in items
                ]
                results = run_rendered_downloads(
                    rendered_tasks,
                    page,
                    wait_seconds=args.render_wait,
                    timeout_seconds=args.timeout,
                    require_canvas=has_canvas_rendering,
                    render_format=args.render_format,
                    render_quality=args.render_quality,
                    force=args.force,
                )
            elif download_mode == "blob-canvas":
                results = run_blob_canvas_downloads(
                    tasks,
                    page,
                    wait_seconds=args.render_wait,
                    render_format=args.render_format,
                    render_quality=args.render_quality,
                )
            elif download_mode == "browser":
                results = run_downloads_browser(
                    tasks,
                    get_capture_page(),
                    timeout_seconds=args.timeout,
                    retries=args.retries,
                    referer=page.url,
                )
            elif download_mode == "direct":
                results = run_downloads_direct(
                    tasks, download_context, workers=args.workers
                )
            else:
                # 先用第一张探测网络路径。若 Python 直连被 DNS、代理或 CDN
                # 拒绝，不继续并发制造大量失败，立即切换到 Chrome 网络栈。
                first_result = download_with_requests(tasks[0], download_context)
                if first_result.status != "failed":
                    print(
                        f"[1/{len(tasks)}] "
                        f"{'跳过' if first_result.status == 'skipped' else '完成'}: "
                        f"{first_result.path.name}",
                        flush=True,
                    )
                    remaining_results = run_downloads_direct(
                        tasks[1:],
                        download_context,
                        workers=args.workers,
                        offset=1,
                    )
                    results = [first_result] + remaining_results
                elif tasks[1:]:
                    print(
                        f"[1/{len(tasks)}] 直连失败: {first_result.path.name}"
                        f" ({first_result.error})",
                        flush=True,
                    )
                    first_browser_result = download_with_browser(
                        tasks[0],
                        get_capture_page(),
                        timeout_seconds=args.timeout,
                        retries=args.retries,
                        referer=page.url,
                    )
                    if first_browser_result.status != "failed":
                        print(
                            "首张图片已通过 Chrome 下载，"
                            "剩余图片切换到 Chrome 网络。",
                            flush=True,
                        )
                        results = [first_browser_result] + run_downloads_browser(
                            tasks[1:],
                            get_capture_page(),
                            timeout_seconds=args.timeout,
                            retries=args.retries,
                            offset=1,
                            referer=page.url,
                        )
                    else:
                        results = [first_browser_result] + run_downloads_direct(
                            tasks[1:],
                            download_context,
                            workers=args.workers,
                            offset=1,
                        )
                else:
                    results = [first_result]

                if not args.no_browser_fallback:
                    fallback_by_order = browser_fallback(
                        [result for result in results if result.status == "failed"],
                        offset=0,
                    )
                    results = [
                        fallback_by_order.get(result.order, result)
                        if result.status == "failed"
                        else result
                        for result in results
                    ]

            write_manifest(
                output_dir,
                args.url,
                page_title,
                items,
                results,
                render_mode=download_mode,
                render_format=(
                    args.render_format
                    if download_mode in {"rendered", "blob-canvas"}
                    else "original"
                ),
                render_quality=(
                    args.render_quality
                    if download_mode in {"rendered", "blob-canvas"}
                    else None
                ),
            )
            downloaded = sum(
                result.status == "downloaded" for result in results
            )
            skipped = sum(result.status == "skipped" for result in results)
            failed = sum(result.status == "failed" for result in results)
            if failed == 0 and not args.dry_run:
                write_album_metadata(
                    output_dir,
                    args.url,
                    page_id,
                    page_title,
                    folder_name_from_title(page_title),
                )
            print(
                f"下载结束：新增 {downloaded}，已存在 {skipped}，失败 {failed}。"
            )
            print(f"输出目录：{output_dir}")
            result_payload.update(
                status="success" if failed == 0 else "failed",
                downloaded=downloaded,
                skipped=skipped,
                failed=failed,
                render_mode=download_mode,
                render_format=(
                    args.render_format
                    if download_mode in {"rendered", "blob-canvas"}
                    else "original"
                ),
                error="" if failed == 0 else f"{failed} 张图片下载失败",
            )
            write_download_marker(
                output_dir,
                args.download_task_id,
                "success" if failed == 0 else "failed",
            )
            write_result_file(args.result_file, result_payload)
            return 1 if failed else 0
        finally:
            if output_dir is not None and args.download_task_id > 0:
                marker = read_download_marker(output_dir)
                if marker.get("status") == "running":
                    write_download_marker(
                        output_dir,
                        args.download_task_id,
                        "failed",
                    )
            if args.keep_open:
                input("任务结束，按 Enter 关闭页面……")
            page.close()
            if capture_page is not None:
                capture_page.close()
            if output_dir is not None:
                for capture_file in output_dir.glob("*_rendered.capture.png"):
                    try:
                        capture_file.unlink(missing_ok=True)
                    except OSError:
                        pass
            close_browser_session(browser_session)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n任务已取消。", file=sys.stderr)
        raise SystemExit(130)
    except RuntimeError as exc:
        print(f"\n执行失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
