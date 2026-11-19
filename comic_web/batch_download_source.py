from __future__ import annotations

import html as html_module
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlsplit

import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


ANCHOR_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.IGNORECASE | re.DOTALL)
HREF_RE = re.compile(
    r"""href\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class BatchChapterPreview:
    chapter_key: str
    chapter_title: str
    sort_order: int
    download_url: str

    def public_dict(self) -> dict:
        return {
            "chapter_key": self.chapter_key,
            "chapter_title": self.chapter_title,
            "sort_order": self.sort_order,
            "download_url": self.download_url,
        }


class BatchSourceError(Exception):
    def __init__(self, message: str, code: str = "batch_source_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class XiaoaimanhuaSource:
    allowed_hosts = {"www.xiaoaimanhua.com", "xiaoaimanhua.com"}

    def __init__(self, cdp_url: Optional[str] = None) -> None:
        self.cdp_url = (
            cdp_url
            or os.environ.get("COMIC_DOWNLOAD_CDP_URL")
            or "http://127.0.0.1:9222"
        )
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Chrome/157 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )

    def validate(
        self,
        parent_id: str,
        series_url: str,
    ) -> None:
        if not re.fullmatch(r"\d+", str(parent_id or "")):
            raise BatchSourceError(
                "漫画 ID 必须是数字。",
                code="invalid_parent_id",
            )
        parsed = urlsplit(str(series_url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise BatchSourceError(
                "系列 URL 不合法。",
                code="invalid_series_url",
            )
        host = parsed.hostname.lower()
        if host not in self.allowed_hosts:
            raise BatchSourceError(
                "暂不支持该系列站点。",
                code="unsupported_source",
            )
        if not re.fullmatch(rf"/{re.escape(parent_id)}/?", parsed.path):
            raise BatchSourceError(
                "系列 URL 中的漫画 ID 与输入不一致。",
                code="series_url_id_mismatch",
            )

    def resolve_series(
        self,
        parent_id: str,
        series_url: str,
    ) -> List[BatchChapterPreview]:
        self.validate(parent_id, series_url)
        content = ""
        try:
            response = self.session.get(series_url, timeout=(10, 30))
            response.raise_for_status()
            content = response.text
        except requests.RequestException:
            pass
        chapters = self.parse_html(parent_id, series_url, content)
        if chapters:
            return chapters
        content = self._load_with_chrome(series_url)
        chapters = self.parse_html(parent_id, series_url, content)
        if not chapters:
            raise BatchSourceError(
                "没有解析到章节链接。",
                code="no_chapters",
            )
        return chapters

    @staticmethod
    def parse_html(
        parent_id: str,
        series_url: str,
        content: str,
    ) -> List[BatchChapterPreview]:
        selected: Dict[str, tuple[int, str, str, int]] = {}
        path_pattern = re.compile(rf"/{re.escape(parent_id)}/(\d+)\.html/?")
        for occurrence, match in enumerate(ANCHOR_RE.finditer(content)):
            attrs = match.group(1)
            href_match = HREF_RE.search(attrs)
            if href_match is None:
                continue
            href = html_module.unescape(href_match.group(1)).strip()
            absolute = urljoin(series_url, href)
            parsed = urlsplit(absolute)
            if (parsed.hostname or "").lower() not in XiaoaimanhuaSource.allowed_hosts:
                continue
            path_match = path_pattern.fullmatch(parsed.path)
            if path_match is None:
                continue
            key = path_match.group(1)
            raw_text = TAG_RE.sub(" ", match.group(2))
            title = SPACE_RE.sub(
                " ",
                html_module.unescape(raw_text),
            ).strip()
            priority = (
                2 if "comic-chapter-link" in attrs.lower()
                else 1 if title else 0
            )
            candidate = (priority, title, absolute, occurrence)
            current = selected.get(key)
            if current is None or candidate[:2] > current[:2]:
                selected[key] = candidate

        def sort_key(item):
            key, (_, title, _, occurrence) = item
            return (int(key), occurrence, title)

        result = []
        for order, (key, (_, title, url, _)) in enumerate(
            sorted(selected.items(), key=sort_key),
            start=1,
        ):
            result.append(
                BatchChapterPreview(
                    chapter_key=key,
                    chapter_title=title or f"第{key}话",
                    sort_order=order,
                    download_url=url,
                )
            )
        return result

    def _load_with_chrome(self, series_url: str) -> str:
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(self.cdp_url)
                context = (
                    browser.contexts[0]
                    if browser.contexts
                    else browser.new_context()
                )
                page = context.new_page()
                try:
                    page.goto(
                        series_url,
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )
                    try:
                        page.wait_for_load_state("networkidle", timeout=15000)
                    except PlaywrightError:
                        pass
                    page.wait_for_timeout(1000)
                    return page.content()
                finally:
                    page.close()
        except Exception as exc:
            raise BatchSourceError(
                f"无法读取系列页面：{exc}",
                code="source_unavailable",
            ) from exc
