# diag.py —— 连到已启动的浏览器，把页面真实状态打出来
import json, os, time
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    # 连接到我们启动的 Edge（需要启动时加 --remote-debugging-port，见下文）
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    ctx = browser.contexts[0]
    page = ctx.pages[0]

    print("URL    :", page.url)
    print("TITLE  :", page.title())
    print("VIEWPORT:", page.viewport_size)

    # 截全图 + 只截登录按钮周边
    page.screenshot(path="output/diag_full.png", full_page=True)
    page.screenshot(path="output/diag_viewport.png")

    # 找出所有可见的"登录"按钮，报告它们在屏幕上的坐标
    btns = page.get_by_role("button", name="登录")
    for i in range(btns.count()):
        b = btns.nth(i)
        try:
            vis = b.is_visible()
            box = b.bounding_box()
            print(f"  登录按钮[{i}] visible={vis} box={box}")
            if vis and box:
                # 圈出来给你看
                page.screenshot(path=f"output/diag_btn_{i}.png", clip=box)
        except Exception as e:
            print(f"  登录按钮[{i}] err={e}")

    # 把 cookie 和 localStorage 情况也打出来
    cookies = ctx.cookies()
    print("COOKIE 域名:", sorted({c['domain'] for c in cookies}))
    print("COOKIE 数量:", len(cookies))
    ls = page.evaluate("() => Object.keys(localStorage)")
    print("localStorage keys:", ls)

    browser.close()