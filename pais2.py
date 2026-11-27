#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ChatGPT 自动化脚本（Playwright + Chrome persistent_context）
------------------------------------------------------------
用法:
    python pais2.py --login     # 第一次：弹 Chrome，手动过验证+登录
    python pais2.py             # 之后：复用 profile 直接干活
    python pais2.py --debug     # 有头慢速调试
"""

import argparse
import logging
import os
import sys
import time
from typing import Optional

from playwright.sync_api import (
    sync_playwright, Page, BrowserContext, TimeoutError as PWTimeoutError
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("chatgpt")

# ★ Chrome 专用 profile 目录（和 Edge 那个分开，避免冲突）
USER_DATA = r"D:\Path\others\test\chrome_profile"
BROWSER_CHANNEL = "chrome"          # ← 用 Chrome


class ChatGPTBot:
    def __init__(self, headless: bool = False, slow_mo: int = 0):
        self.headless = headless
        self.slow_mo = slow_mo
        self._pw = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------- 启动 ----------------
    def start(self):
        os.makedirs(USER_DATA, exist_ok=True)
        os.makedirs("output", exist_ok=True)

        self._pw = sync_playwright().start()

        try:
            self.context = self._pw.chromium.launch_persistent_context(
                user_data_dir=USER_DATA,
                channel=BROWSER_CHANNEL,          # ★ chrome
                headless=self.headless,
                slow_mo=self.slow_mo,
                viewport={"width": 1440, "height": 900},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--start-maximized",
                    # ★ 不要加 --remote-debugging-port
                ],
            )
        except Exception as e:
            log.error("=" * 60)
            log.error("启动 Chrome 失败: %s", e)
            log.error("请确认已安装 Google Chrome，或改用 channel='msedge'")
            log.error("=" * 60)
            raise

        self.context.set_default_timeout(30000)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

        # 反检测注入
        self.page.add_init_script("""
            Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
            Object.defineProperty(navigator,'languages',{get:()=>['zh-CN','zh','en']});
            Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
            Object.defineProperty(navigator,'platform',{get:()=>'Win32'});
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator,'permissions',{
                get:()=>({query:()=>Promise.resolve({state:'granted'})})
            });
        """)

        log.info("Chrome 已启动 (persistent) headless=%s", self.headless)
        log.info("profile: %s", USER_DATA)

    def close(self):
        try:
            self.context and self.context.close()   # persistent 下 close 即落盘
        except Exception:
            pass
        try:
            self._pw and self._pw.stop()
        except Exception:
            pass
        log.info("浏览器已关闭，登录态已写入 profile")

    # ---------------- 工具方法 ----------------
    def goto(self, url: str, wait_ms: int = 4000):
            """导航到 url，被中断/超时都不致命"""
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except PWTimeoutError:
                log.warning("goto 超时，改用当前页面继续")
            except Exception as e:
                log.warning("goto 被中断（正常，忽略）: %s", str(e)[:100])
            self.page.wait_for_timeout(wait_ms)

    def manual_login(self):
        self.goto("https://chatgpt.com/")
        self.where_am_i()
        self.screenshot("01_open.png")
        self.dump(30)

        log.info("=" * 62)
        log.info("请在 Chrome 窗口里操作：")
        log.info("  1) 点'登录' → 邮箱 → 继续 → 密码 → 验证码")
        log.info("  2) 直到看见能打字的聊天输入框")
        log.info("完成后回终端按回车（不用管页面跳到哪）")
        log.info("=" * 62)
        input()

        # ★ 关键：不重新 goto，直接看当前页面
        self.page.wait_for_timeout(2000)
        ok = self.wait_for_editor(timeout=30000)
        self.where_am_i()
        self.screenshot("02_after_login.png")

        if ok:
            log.info("✅ 登录成功，已写入 profile: %s", USER_DATA)
        else:
            log.error("❌ 未见输入框，请看 output/02_after_login.png")
            log.error("   如果图里是登录页，说明还没走完登录流程")

    def run(self):
        # ★ 已在目标站就不重复导航
        if "chatgpt.com" not in (self.page.url or ""):
            self.goto("https://chatgpt.com/")
        else:
            self.page.wait_for_timeout(2000)

        self.where_am_i()
        self.screenshot("10_home.png")

        if not self.wait_for_editor(timeout=15000):
            log.error("❌ 未登录。先跑: python pais2.py --login")
            self.dump(30)
            return

        log.info("✅ 已登录，开始业务操作")
        try:
            editor = self.page.locator("textarea, [contenteditable='true']").first
            editor.click()
            self.page.wait_for_timeout(300)
            editor.type("你好，这是一条自动化测试消息", delay=40)
            log.info("已输入消息")
            self.screenshot("11_typed.png")
        except Exception as e:
            log.warning("输入失败: %s", e)
        self.screenshot("12_final.png")

    def screenshot(self, name: str):
        path = os.path.join("output", name)
        self.page.screenshot(path=path, full_page=False)
        log.info("截图: %s", path)

    def dump(self, limit: int = 40):
        nodes = self.page.evaluate("""(limit) => {
            const sel='button,a,input,textarea,[contenteditable],[role="button"]';
            return [...document.querySelectorAll(sel)].slice(0,limit).map(el=>({
                tag:el.tagName,
                text:(el.innerText||el.value||'').trim().slice(0,40),
                aria:el.getAttribute('aria-label')||'',
                ph:el.getAttribute('placeholder')||'',
                type:el.getAttribute('type')||'',
                visible:!!(el.offsetWidth||el.offsetHeight)
            }));
        }""", limit)
        log.info("=== 可交互元素 ===")
        for i, n in enumerate(nodes):
            log.info("  [%d] <%s> text='%s' aria='%s' ph='%s' type=%s vis=%s",
                     i, n['tag'], n['text'], n['aria'], n['ph'], n['type'], n['visible'])

    def where_am_i(self):
        log.info("URL   : %s", self.page.url)
        log.info("TITLE : %s", self.page.title())
        try:
            body = self.page.inner_text("body", timeout=5000)[:500]
            log.info("BODY  : %s", body.replace("\n", " | "))
        except Exception as e:
            log.info("BODY  : <读取失败 %s>", e)

    def wait_for_editor(self, timeout: int = 120000) -> bool:
        """等聊天输入框出现 = 登录成功的标志"""
        try:
            self.page.wait_for_selector(
                "textarea, [contenteditable='true']",
                state="visible", timeout=timeout
            )
            log.info("✅ 检测到聊天输入框")
            return True
        except PWTimeoutError:
            log.warning("未检测到输入框")
            return False

    # ---------------- 登录流程 ----------------
    def manual_login(self):
        self.goto("https://chatgpt.com/")
        self.where_am_i()
        self.screenshot("01_open.png")
        self.dump(30)

        log.info("=" * 62)
        log.info("请在 Chrome 窗口里操作：")
        log.info("  1) 出现 Cloudflare '验证您是真人' → 手动点掉")
        log.info("  2) 点'登录' → 走完邮箱/密码/验证码")
        log.info("  3) 直到看见能打字聊天的输入框")
        log.info("完成后回到这里按回车")
        log.info("=" * 62)
        input()

        # 重新进一次，确认登录态保住了
        self.goto("https://chatgpt.com/")
        ok = self.wait_for_editor(timeout=30000)
        self.where_am_i()
        self.screenshot("02_after_login.png")

        if ok:
            log.info("✅ 登录态已写入 Chrome profile，以后直接 python pais2.py")
        else:
            log.error("❌ 仍未见输入框，请看 output/02_after_login.png")

    # ---------------- 业务流程 ----------------
    def run(self):
        self.goto("https://chatgpt.com/")
        self.where_am_i()
        self.screenshot("10_home.png")

        if not self.wait_for_editor(timeout=15000):
            log.error("❌ 未登录或被拦截。先跑: python pais2.py --login")
            self.dump(30)
            return

        log.info("✅ 已确认登录状态，开始业务操作")

        # ↓↓↓ 在这里写你的业务逻辑 ↓↓↓
        try:
            editor = self.page.locator("textarea, [contenteditable='true']").first
            editor.click()
            self.page.wait_for_timeout(300)
            editor.type("你好，这是一条自动化测试消息", delay=40)
            log.info("已输入消息")
            self.screenshot("11_typed.png")
        except Exception as e:
            log.warning("输入失败: %s", e)
        # ↑↑↑ 业务逻辑 end ↑↑↑

        self.screenshot("12_final.png")


def main():
    ap = argparse.ArgumentParser(description="ChatGPT 自动化 (Chrome)")
    ap.add_argument("--login", action="store_true", help="手动过验证+登录并存入 profile")
    ap.add_argument("--debug", action="store_true", help="有头慢速")
    ap.add_argument("--headless", action="store_true", help="无头（易触发风控）")
    args = ap.parse_args()

    headless = args.headless and not args.debug
    slow_mo = 500 if args.debug else 0

    if args.login:
        log.info("🔑 手动登录模式 (Chrome)")
        with ChatGPTBot(headless=False, slow_mo=300) as bot:
            bot.manual_login()
        return

    if not os.path.isdir(USER_DATA) or not os.listdir(USER_DATA):
        log.error("❌ profile 为空，请先运行: python pais2.py --login")
        sys.exit(1)

    log.info("🚀 复用 Chrome profile 启动")
    with ChatGPTBot(headless=headless, slow_mo=slow_mo) as bot:
        try:
            bot.run()
        except PWTimeoutError as e:
            log.error("超时: %s", e)
            bot.screenshot("error.png")
            bot.dump(30)
        except Exception as e:
            log.error("异常: %s", e)
            bot.screenshot("error.png")


if __name__ == "__main__":
    main()