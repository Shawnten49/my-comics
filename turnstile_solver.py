#!/usr/bin/env python3
"""
通过 CDP 定位 Cloudflare Turnstile 复选框，并使用 pyautogui 点击。

安装：
    python3 -m pip install -r requirements.txt

运行：
    python3 turnstile_solver.py

只点击“请验证您是真人”复选框，不处理后续图片题、滑块或其他挑战。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from playwright.sync_api import Browser, Page, sync_playwright


DEFAULT_URL = "https://18comic.vip/photo/601116"
DEFAULT_CDP_URL = "http://127.0.0.1:9222"
TARGET_LABEL = re.compile(
    r"请验证您是真人|Verify you are human",
    re.IGNORECASE,
)


@dataclass
class ClickTarget:
    viewport_x: float
    viewport_y: float
    description: str


class TurnstileSolver:
    """使用 CDP 定位，再使用 pyautogui 执行一次系统级鼠标点击。"""

    def __init__(
        self,
        cdp_url: str,
        page_url: str,
        locate_timeout: float,
        verify_timeout: float,
        dry_run: bool,
    ) -> None:
        self.cdp_url = cdp_url
        self.page_url = page_url
        self.locate_timeout = locate_timeout
        self.verify_timeout = verify_timeout
        self.dry_run = dry_run

    @staticmethod
    def log(message: str) -> None:
        print(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}",
            flush=True,
        )

    @staticmethod
    def page_is_challenged(page: Page) -> bool:
        try:
            state = page.evaluate(
                """() => ({
                    title: document.title || '',
                    url: location.href || '',
                    text: document.body ? document.body.innerText.slice(0, 1500) : '',
                    html: document.documentElement
                        ? document.documentElement.outerHTML.slice(0, 16000)
                        : ''
                })"""
            )
        except Exception:
            return False
        combined = (
            state["title"] + "\n" + state["url"] + "\n"
            + state["text"] + "\n" + state["html"]
        ).lower()
        return any(
            marker in combined
            for marker in (
                "just a moment",
                "__cf_chl",
                "challenges.cloudflare.com",
                "正在进行安全验证",
                "checking your browser",
                "请稍候",
            )
        )

    @staticmethod
    def _visible_box(locator: Any) -> Optional[Tuple[float, float]]:
        try:
            if not locator.is_visible():
                return None
            box = locator.bounding_box()
            if not box or box["width"] < 1 or box["height"] < 1:
                return None
            return (
                float(box["x"] + box["width"] / 2),
                float(box["y"] + box["height"] / 2),
            )
        except Exception:
            return None

    def find_checkbox_target(self, page: Page) -> Optional[ClickTarget]:
        selectors = (
            'input[type="checkbox"][aria-label*="请验证您是真人"]',
            '[role="checkbox"][aria-label*="请验证您是真人"]',
            'input[type="checkbox"][aria-label*="Verify you are human" i]',
            '[role="checkbox"][aria-label*="Verify you are human" i]',
        )

        # 1. 普通 DOM、iframe 和开放 Shadow DOM。
        for frame in page.frames:
            frame_selectors = selectors
            if "challenges.cloudflare.com" in frame.url:
                frame_selectors = selectors + ('input[type="checkbox"]',)
            for selector in frame_selectors:
                try:
                    locator = frame.locator(selector)
                    count = locator.count()
                except Exception:
                    continue
                for index in range(count):
                    candidate = locator.nth(index)
                    point = self._visible_box(candidate)
                    if point:
                        label = candidate.get_attribute("aria-label") or selector
                        return ClickTarget(
                            viewport_x=point[0],
                            viewport_y=point[1],
                            description=f"DOM checkbox: {label}",
                        )

        # 2. 封闭 Shadow DOM：通过 Chrome Accessibility Tree 定位。
        try:
            cdp = page.context.new_cdp_session(page)
            tree = cdp.send("Accessibility.getFullAXTree")
            for node in tree.get("nodes", []):
                role = str((node.get("role") or {}).get("value", "")).lower()
                name = str((node.get("name") or {}).get("value", ""))
                if role != "checkbox" or not TARGET_LABEL.search(name):
                    continue
                backend_node_id = node.get("backendDOMNodeId")
                if backend_node_id is None:
                    continue
                box = cdp.send(
                    "DOM.getBoxModel",
                    {"backendNodeId": backend_node_id},
                )
                quad = box["model"]["border"]
                return ClickTarget(
                    viewport_x=(quad[0] + quad[2] + quad[4] + quad[6]) / 4,
                    viewport_y=(quad[1] + quad[3] + quad[5] + quad[7]) / 4,
                    description=f"Accessibility checkbox: {name}",
                )
        except Exception:
            pass

        # 3. Turnstile 控件通常为 300×65，复选框位于左侧约 30px 处。
        for frame in page.frames:
            if "challenges.cloudflare.com" not in frame.url:
                continue
            try:
                iframe = frame.frame_element()
                box = iframe.bounding_box()
                if not box or box["width"] < 20 or box["height"] < 20:
                    continue
                return ClickTarget(
                    viewport_x=float(box["x"] + min(30, box["width"] * 0.2)),
                    viewport_y=float(box["y"] + box["height"] / 2),
                    description="Turnstile iframe checkbox area",
                )
            except Exception:
                continue
        return None

    @staticmethod
    def viewport_to_screen(
        page: Page,
        viewport_x: float,
        viewport_y: float,
    ) -> Tuple[float, float]:
        metrics = page.evaluate(
            """() => ({
                screenX: window.screenX,
                screenY: window.screenY,
                outerWidth: window.outerWidth,
                outerHeight: window.outerHeight,
                innerWidth: window.innerWidth,
                innerHeight: window.innerHeight,
                devicePixelRatio: window.devicePixelRatio || 1
            })"""
        )
        horizontal_border = max(
            0.0,
            (float(metrics["outerWidth"]) - float(metrics["innerWidth"])) / 2,
        )
        vertical_border = max(
            0.0,
            float(metrics["outerHeight"]) - float(metrics["innerHeight"]),
        )
        return (
            float(metrics["screenX"]) + horizontal_border + viewport_x,
            float(metrics["screenY"]) + vertical_border + viewport_y,
        )

    def activate_chrome(self, page: Page) -> None:
        page.bring_to_front()
        time.sleep(0.4)
        if sys.platform == "darwin":
            try:
                subprocess.run(
                    [
                        "osascript",
                        "-e",
                        'tell application "Google Chrome" to activate',
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
                time.sleep(0.4)
            except Exception:
                pass

    def solve(self) -> int:
        try:
            import pyautogui
        except ImportError:
            self.log(
                "缺少 pyautogui。请执行："
                "python3 -m pip install -r requirements.txt"
            )
            return 2

        pyautogui.PAUSE = 0.2
        pyautogui.FAILSAFE = True

        with sync_playwright() as playwright:
            browser: Browser = playwright.chromium.connect_over_cdp(self.cdp_url)
            if not browser.contexts:
                self.log("CDP 已连接，但没有可用的浏览器上下文。")
                return 2
            context = browser.contexts[0]
            page = context.new_page()
            try:
                self.log(f"打开页面：{self.page_url}")
                page.goto(
                    self.page_url,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                page.bring_to_front()

                deadline = time.monotonic() + self.locate_timeout
                target: Optional[ClickTarget] = None
                while time.monotonic() < deadline:
                    target = self.find_checkbox_target(page)
                    if target is not None:
                        break
                    if (
                        not self.page_is_challenged(page)
                        and page.locator(".scramble-page img").count() > 0
                    ):
                        self.log("页面无需 Turnstile 验证，未执行点击。")
                        return 0
                    page.wait_for_timeout(500)

                if target is None:
                    self.log(
                        f"在 {self.locate_timeout:g} 秒内没有找到 Turnstile 复选框。"
                    )
                    return 1

                self.log(f"已定位：{target.description}")
                screen_x, screen_y = self.viewport_to_screen(
                    page,
                    target.viewport_x,
                    target.viewport_y,
                )
                screen_width, screen_height = pyautogui.size()
                if not (
                    0 <= screen_x < screen_width
                    and 0 <= screen_y < screen_height
                ):
                    self.log(
                        "计算出的点击坐标在屏幕范围之外："
                        f"({screen_x:.1f}, {screen_y:.1f})"
                    )
                    return 1

                self.log(
                    "pyautogui 点击屏幕坐标："
                    f"({screen_x:.1f}, {screen_y:.1f})"
                )
                if self.dry_run:
                    self.log("dry-run 已开启，不执行实际点击。")
                    return 0

                self.activate_chrome(page)
                pyautogui.moveTo(screen_x, screen_y, duration=0.25)
                pyautogui.click()
                self.log("已发送 pyautogui 鼠标点击。")

                verify_deadline = time.monotonic() + self.verify_timeout
                while time.monotonic() < verify_deadline:
                    page.wait_for_timeout(1000)
                    if not self.page_is_challenged(page):
                        self.log("Cloudflare 验证已通过。")
                        return 0
                self.log(
                    "点击已执行，但 Cloudflare 验证页仍未消失。"
                    "可能需要人工完成后续验证。"
                )
                return 1
            finally:
                page.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="通过 CDP 定位 Turnstile 复选框，并用 pyautogui 点击。"
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="目标网页 URL")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL, help="Chrome CDP 地址")
    parser.add_argument(
        "--locate-timeout",
        type=float,
        default=25,
        help="等待复选框出现的秒数，默认 25",
    )
    parser.add_argument(
        "--verify-timeout",
        type=float,
        default=20,
        help="点击后等待验证通过的秒数，默认 20",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只计算并显示屏幕坐标，不执行 pyautogui 点击",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    solver = TurnstileSolver(
        cdp_url=args.cdp_url,
        page_url=args.url,
        locate_timeout=args.locate_timeout,
        verify_timeout=args.verify_timeout,
        dry_run=args.dry_run,
    )
    return solver.solve()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        raise SystemExit(130)
