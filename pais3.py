#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ChatGPT 自动化 —— 专用自动化 profile 版（修复 about:blank 问题）
================================================================
★ 为什么不再挂真实 Chrome profile：
  Chrome 136+ 起，当 --user-data-dir 是 Chrome 默认数据目录时，
  Chrome 会静默忽略 --remote-debugging-pipe / --remote-debugging-port，
  Playwright 连不上浏览器 → 窗口停在 about:blank，30s 后启动超时。
  这是官方安全策略，无法绕过。

★ 正确做法：
  用一个"专用自动化 profile"（非默认目录，CDP 正常）。
  首次运行 --login 手动登录一次，登录态会持久保存在该目录，
  之后日常运行直接复用，也不影响你日常使用的 Chrome。

用法:
    python pais3.py --login     # 首次：手动登录一次（顺便过 Cloudflare）
    python pais3.py             # 日常：复用登录态直接干活
    python pais3.py --debug     # 有头慢速
"""

import argparse
import logging
import os
import shutil
import subprocess
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

# ══════════════════ ★ 配置区 ★ ══════════════════
# 专用自动化 profile（必须是非默认目录，否则 Chrome 136+ 禁用 CDP）
USER_DATA = r"D:\Path\others\test\chrome_profile"
CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# 可选：你日常 Chrome 的真实 profile，仅在 --clone 时作为克隆源
REAL_USER_DATA = r"C:\Users\26074\AppData\Local\Google\Chrome\User Data"
# ══════════════════════════════════════════════════

HOME = "https://chatgpt.com/"


def clone_real_profile():
    """可选：把日常 Chrome 的 profile 克隆到专用目录（需先彻底退出 Chrome）。

    注意：Chrome 127+ 的 cookie 使用 App-Bound Encryption，克隆后
    部分 cookie 可能解密失败 → 仍可能需要重新登录一次。
    """
    if not os.path.isdir(REAL_USER_DATA):
        log.error("真实 profile 不存在: %s", REAL_USER_DATA)
        sys.exit(1)
    if os.path.isdir(os.path.join(USER_DATA, "Default")):
        log.warning("专用 profile 已存在，跳过克隆（想重新克隆请先删除 %s）", USER_DATA)
        return
    log.info("克隆真实 profile → %s（请确认所有 chrome.exe 已退出）", USER_DATA)
    # robocopy：排除锁文件和缓存，速度快且不会因占用中断
    cmd = [
        "robocopy", REAL_USER_DATA, USER_DATA, "/E",
        "/XF", "Singleton*", "lockfile", "*.tmp",
        "/XD", "Cache", "Code Cache", "GPUCache", "Crashpad",
        "BrowserMetrics", "GrShaderCache", "ShaderCache",
        "/NFL", "/NDL", "/NJH", "/NJS", "/NP",
    ]
    subprocess.run(cmd, check=False)  # robocopy 退出码 <8 即成功
    log.info("克隆完成。若登录态丢失属正常（cookie 加密绑定原目录），--login 重登一次即可")


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

    # ─────────────────── 启动 ───────────────────
    def start(self):
        os.makedirs(USER_DATA, exist_ok=True)
        os.makedirs("output", exist_ok=True)

        # 防御：如果 USER_DATA 被误配成 Chrome 默认目录，直接拒绝并说明
        default_dir = os.path.expandvars(
            r"%LOCALAPPDATA%\Google\Chrome\User Data"
        ).lower()
        if os.path.normpath(USER_DATA).lower() == os.path.normpath(default_dir):
            log.error("=" * 66)
            log.error("❌ USER_DATA 指向了 Chrome 默认数据目录！")
            log.error("   Chrome 136+ 会禁用 CDP 调试管道，Playwright 无法连接，")
            log.error("   表现为：窗口打开后停在 about:blank，30 秒后启动超时。")
            log.error("   请改用专用目录，例如: %s", r"D:\Path\others\test\chrome_profile")
            log.error("=" * 66)
            sys.exit(1)

        self._pw = sync_playwright().start()

        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",                    # 跳过首次运行向导
            "--no-default-browser-check",        # 跳过默认浏览器检查
            "--disable-session-crashed-bubble",  # 跳过"恢复页面"气泡
            "--hide-crash-restore-bubble",
            "--start-maximized",
        ]
        if self.headless:
            log.warning("⚠ headless 模式下 Cloudflare 拦截率极高，建议有头运行")

        try:
            self.context = self._pw.chromium.launch_persistent_context(
                user_data_dir=USER_DATA,
                executable_path=CHROME_EXE if os.path.exists(CHROME_EXE) else None,
                headless=False,                 # ★ 强制有头
                slow_mo=self.slow_mo,
                viewport=None,                  # ★ 用真实窗口尺寸
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                args=args,
                ignore_default_args=["--enable-automation"],
                timeout=45000,
            )
        except PWTimeoutError:
            log.error("=" * 66)
            log.error("❌ 启动超时：Chrome 窗口开了但 Playwright 连不上。")
            log.error("   最常见原因：")
            log.error("   1) USER_DATA 指向了 Chrome 默认目录（Chrome 136+ 禁 CDP）")
            log.error("   2) 上一个 chrome.exe 僵尸进程占着该 profile →")
            log.error("      taskkill //F //IM chrome.exe  （注意会关掉你日常 Chrome）")
            log.error("=" * 66)
            raise
        except Exception as e:
            log.error("启动失败: %s", str(e)[:300])
            raise

        self.context.set_default_timeout(30000)

        # 恢复会话时可能有多个页签，选最后一个普通页面
        pages = [p for p in self.context.pages
                 if not (p.url or "").startswith("chrome://")]
        self.page = pages[-1] if pages else self.context.new_page()

        log.info("✅ 已挂载专用自动化 profile: %s", USER_DATA)

    def close(self):
        try:
            self.context and self.context.close()
        except Exception:
            pass
        try:
            self._pw and self._pw.stop()
        except Exception:
            pass
        log.info("浏览器已关闭（登录态保存在专用 profile 目录中）")

    # ─────────────────── 工具方法 ───────────────────
    def goto(self, url: str, wait_ms: int = 3000):
        log.info("→ 导航: %s", url)
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except PWTimeoutError:
            log.warning("goto 超时，用当前页面继续")
        except Exception as e:
            # ★ 不再静默吞掉：连接断开时这里能第一时间暴露
            log.error("goto 异常: %s", str(e)[:120])
            raise
        self.page.wait_for_timeout(wait_ms)

    def safe_goto(self, url: str, wait_ms: int = 3000):
        """已在目标站就不重复导航，避免打断页面自身跳转"""
        try:
            cur = self.page.url or ""
        except Exception:
            cur = ""
        if url.rstrip("/") in cur.rstrip("/"):
            log.info("已在目标站，不重复导航 (%s)", cur)
            self.page.wait_for_timeout(wait_ms)
            return
        self.goto(url, wait_ms)

    def screenshot(self, name: str):
        try:
            self.page.screenshot(path=os.path.join("output", name), full_page=False)
            log.info("截图: output\\%s", name)
        except Exception as e:
            log.warning("截图失败: %s", str(e)[:80])

    def where_am_i(self):
        log.info("URL   : %s", self.page.url)
        log.info("TITLE : %s", self.page.title())
        try:
            body = self.page.inner_text("body", timeout=5000)[:400]
            log.info("BODY  : %s", body.replace("\n", " | ") or "<空>")
        except Exception as e:
            log.info("BODY  : <读取失败 %s>", str(e)[:60])

    def dump(self, limit: int = 40):
        nodes = self.page.evaluate("""(limit) => {
            const sel='button,a,input,textarea,[contenteditable],[role="button"]';
            return [...document.querySelectorAll(sel)].slice(0,limit).map(el=>({
                tag:el.tagName,
                text:(el.innerText||el.value||'').trim().slice(0,36),
                aria:el.getAttribute('aria-label')||'',
                ph:el.getAttribute('placeholder')||'',
                vis:!!(el.offsetWidth||el.offsetHeight)
            }));
        }""", limit)
        log.info("=== 可交互元素 ===")
        for i, n in enumerate(nodes):
            log.info("  [%d] <%s> text='%s' aria='%s' ph='%s' vis=%s",
                     i, n['tag'], n['text'], n['aria'], n['ph'], n['vis'])

    # ─────────────── Cloudflare 处理 ───────────────
    def is_blocked(self) -> bool:
        """判断当前是否停在 Cloudflare 验证页"""
        try:
            title = self.page.title()
        except Exception:
            return True
        if any(k in title for k in ("请稍候", "Just a moment", "Attention Required")):
            return True
        try:
            body = self.page.inner_text("body", timeout=3000)
            # "正在验证" 是 CF 挑战页的典型文案
            if "正在验证" in body or "Verify you are human" in body:
                return True
        except Exception:
            pass
        return False

    def wait_pass_cloudflare(self, timeout: int = 90000) -> bool:
        """等 Cloudflare 自动放行；出现可点复选框则提示手动点"""
        log.info("⏳ 检测 Cloudflare 状态（最多 %ds）...", timeout // 1000)
        if not self.is_blocked():
            log.info("✅ 未被拦截")
            return True

        deadline = time.time() + timeout / 1000
        tick = 0
        while time.time() < deadline:
            tick += 1
            try:
                frame = None
                for f in self.page.frames:
                    if "challenges.cloudflare.com" in (f.url or ""):
                        frame = f
                        break
                if frame:
                    for sel in ("input[type=checkbox]", "#challenge-stage label", "label"):
                        try:
                            el = frame.locator(sel).first
                            if el.is_visible(timeout=1500):
                                el.click()
                                log.info("🖱 已代点 Cloudflare 复选框")
                                break
                        except Exception:
                            pass
            except Exception:
                pass

            self.page.wait_for_timeout(3000)
            if not self.is_blocked():
                log.info("✅ Cloudflare 已放行 (title=%s)", self.page.title())
                return True
            if tick % 5 == 0:
                log.info("   仍在验证中... 已等 %ds（可在浏览器窗口手动点一下复选框）", tick * 3)

        log.error("❌ Cloudflare 超时未放行")
        return False

    # ─────────────── 登录态判定 ───────────────
    def wait_for_editor(self, timeout: int = 20000) -> bool:
        """聊天输入框出现 = 已进入聊天界面"""
        try:
            self.page.wait_for_selector(
                "#prompt-textarea, textarea, [contenteditable='true']",
                state="visible", timeout=timeout
            )
            log.info("✅ 检测到聊天输入框")
            return True
        except PWTimeoutError:
            log.warning("未见聊天输入框")
            return False

    def check_login(self) -> bool:
        """无可见'登录'按钮 或 已有输入框 → 视为已登录"""
        btns = self.page.get_by_role("button", name="登录", exact=True)
        visible = 0
        for i in range(btns.count()):
            try:
                if btns.nth(i).is_visible():
                    visible += 1
            except Exception:
                pass
        log.info("可见'登录'按钮: %d 个", visible)
        editor = self.page.locator(
            "#prompt-textarea, textarea, [contenteditable='true']"
        )
        has_editor = editor.count() > 0 and editor.first.is_visible()
        return visible == 0 or has_editor

    # ─────────────── 流程 ───────────────
    def manual_login(self):
        """首次：过 Cloudflare + 手动登录，登录态持久化到专用 profile"""
        self.goto(HOME)
        self.screenshot("01_open.png")

        if not self.wait_pass_cloudflare():
            log.error("被 Cloudflare 拦住，建议：")
            log.error("  ① 用你日常的 Chrome 手动开 chatgpt.com 看是否也弹验证")
            log.error("     也弹 → IP/网络被标记，换网络（如手机热点）再试")
            log.error("  ② 在脚本弹出的窗口里手动完成验证后重跑")
            return

        self.where_am_i()

        if self.check_login():
            log.info("✅ 专用 profile 里已有登录态，可以直接用")
            self.screenshot("02_logged_in.png")
            return

        log.info("=" * 62)
        log.info("请在弹出的 Chrome 窗口里手动完成：")
        log.info("  点'登录' → 邮箱/账号 → 密码 → 验证码")
        log.info("  直到看见能打字的聊天框，然后回终端按回车")
        log.info("=" * 62)
        input()

        self.safe_goto(HOME)
        self.wait_pass_cloudflare(timeout=60000)
        self.where_am_i()
        self.screenshot("02_after_login.png")

        if self.check_login():
            log.info("✅ 登录成功，登录态已保存在专用 profile，以后无需再登")
        else:
            log.error("❌ 仍未登录，看 output/02_after_login.png")

    def run(self):
        """日常业务入口"""
        self.safe_goto(HOME)
        self.screenshot("10_home.png")

        if not self.wait_pass_cloudflare():
            log.error("❌ Cloudflare 拦截，退出")
            return

        if not self.wait_for_editor(timeout=15000):
            log.error("❌ 未登录。先跑: python pais3.py --login")
            self.dump(30)
            return

        if not self.check_login():
            log.error("❌ 页面有'登录'按钮，登录态失效。重跑: python pais3.py --login")
            return

        log.info("✅ 已登录，开始业务操作")

        # ══════════ 在这里写你的业务逻辑 ══════════
        try:
            editor = self.page.locator(
                "#prompt-textarea, textarea, [contenteditable='true']"
            ).first
            editor.click()
            self.page.wait_for_timeout(300)
            editor.type("你好，这是一条自动化测试消息", delay=40)
            log.info("已输入消息")
            self.screenshot("11_typed.png")
            # 发送：
            # self.page.keyboard.press("Enter")
            # self.page.wait_for_timeout(5000)
            # self.screenshot("12_answered.png")
        except Exception as e:
            log.warning("输入失败: %s", e)
        # ══════════ 业务逻辑 end ══════════

        self.screenshot("12_final.png")


def main():
    ap = argparse.ArgumentParser(description="ChatGPT 自动化（专用 Chrome profile）")
    ap.add_argument("--login", action="store_true", help="首次手动登录 / 确认登录态")
    ap.add_argument("--clone", action="store_true",
                    help="先克隆日常 Chrome 的 profile（需先退出所有 chrome.exe）")
    ap.add_argument("--debug", action="store_true", help="有头慢速")
    ap.add_argument("--headless", action="store_true", help="无头（Cloudflare 拦截率极高）")
    args = ap.parse_args()

    if args.headless:
        log.warning("⚠ --headless 已忽略：必须走有头模式")

    if args.clone:
        clone_real_profile()

    slow_mo = 500 if args.debug else 0

    if args.login:
        log.info("🔑 登录模式（专用 profile）")
        with ChatGPTBot(slow_mo=slow_mo or 300) as bot:
            bot.manual_login()
        return

    log.info("🚀 日常模式（复用专用 profile 登录态）")
    with ChatGPTBot(slow_mo=slow_mo) as bot:
        try:
            bot.run()
        except PWTimeoutError as e:
            log.error("超时: %s", str(e)[:200])
            bot.screenshot("error.png")
            bot.dump(30)
        except Exception as e:
            log.error("异常: %s", str(e)[:200])
            bot.screenshot("error.png")


if __name__ == "__main__":
    main()
