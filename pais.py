#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基础爬虫框架
----------------------------------------
安装依赖:
    pip install requests beautifulsoup4 lxml fake-useragent tenacity
可选:
    pip install pandas pymongo
"""

import csv
import json
import logging
import os
import random
import time
from typing import Any, Dict, Iterable, List, Optional

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------- 随机 UA
try:  # fake-useragent 偶尔联网失败，做降级处理
    from fake_useragent import UserAgent

    _UA = UserAgent()

    def random_ua() -> str:
        return _UA.random
except Exception:
    _UA_LIST = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.3 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    ]

    def random_ua() -> str:
        return random.choice(_UA_LIST)


# ---------------------------------------------------------------- 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("spider")


# ---------------------------------------------------------------- 基类
class BaseSpider:
    """所有爬虫继承此类，只需要实现 parse() 和 build_urls()"""

    # ---- 可被子类覆盖的配置 ----
    timeout = 10                 # 请求超时(秒)
    retry_times = 3              # 失败重试次数
    delay = (1.0, 2.5)           # 每次请求后随机延时区间(秒)
    output_dir = "output"
    use_proxy = False
    proxies: Optional[Dict[str, str]] = None   # {"http": "...", "https": "..."}
    cookies: Optional[Dict[str, str]] = None

    def __init__(self) -> None:
        os.makedirs(self.output_dir, exist_ok=True)
        self.session = self._build_session()
        self.seen: set = set()   # 简单去重

    # ---------------------------- 构建 Session ----------------------------
    def _build_session(self) -> requests.Session:
        s = requests.Session()

        # 连接池 + 自动重试(429/5xx)
        retry = Retry(
            total=self.retry_times,
            backoff_factor=1,                      # 1s, 2s, 4s ...
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
        s.mount("http://", adapter)
        s.mount("https://", adapter)

        # 默认请求头
        s.headers.update({
            "User-Agent": random_ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
        })

        if self.cookies:
            s.cookies.update(self.cookies)
        if self.use_proxy and self.proxies:
            s.proxies.update(self.proxies)

        return s

    # ---------------------------- 发请求 ----------------------------
    def fetch(self, url: str, **kwargs: Any) -> Optional[requests.Response]:
        """GET 请求，带异常捕获；失败返回 None"""
        try:
            # 每个请求换一次 UA，降低被封概率
            self.session.headers["User-Agent"] = random_ua()
            resp = self.session.get(url, timeout=self.timeout, **kwargs)
            resp.raise_for_status()

            # 修复中文乱码：requests 猜错编码时用 apparent_encoding
            if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = resp.apparent_encoding
            return resp

        except requests.exceptions.Timeout:
            log.warning("超时: %s", url)
        except requests.exceptions.HTTPError as e:
            log.warning("HTTP错误 %s: %s", e.response.status_code, url)
        except requests.exceptions.RequestException as e:
            log.warning("请求失败 %s: %s", url, e)
        return None

    def sleep(self) -> None:
        """随机延时，做个有礼貌的爬虫"""
        time.sleep(random.uniform(*self.delay))

    # ---------------------------- 需要子类实现 ----------------------------
    def build_urls(self) -> Iterable[str]:
        """返回待抓取的 URL 列表 / 生成器"""
        raise NotImplementedError

    def parse(self, html: str, url: str) -> List[Dict[str, Any]]:
        """解析页面，返回结构化数据列表"""
        raise NotImplementedError

    # ---------------------------- 保存 ----------------------------
    def save_csv(self, items: List[Dict[str, Any]], filename: str = "data.csv") -> None:
        if not items:
            return
        path = os.path.join(self.output_dir, filename)
        file_exists = os.path.isfile(path)
        with open(path, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(items[0].keys()))
            if not file_exists:
                writer.writeheader()
            writer.writerows(items)

    def save_json(self, items: List[Dict[str, Any]], filename: str = "data.json") -> None:
        if not items:
            return
        path = os.path.join(self.output_dir, filename)
        with open(path, "a", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    # ---------------------------- 主流程 ----------------------------
    def run(self, save_func: str = "csv") -> None:
        total = 0
        for url in self.build_urls():
            if url in self.seen:
                continue
            self.seen.add(url)

            log.info("抓取: %s", url)
            resp = self.fetch(url)
            if resp is None:
                continue

            try:
                items = self.parse(resp.text, url)
            except Exception as e:                      # 解析失败不影响后续
                log.error("解析失败 %s: %s", url, e)
                continue

            if not items:
                log.info("未解析到数据: %s", url)
                continue

            if save_func == "csv":
                self.save_csv(items)
            else:
                self.save_json(items)

            total += len(items)
            log.info("解析 %d 条，累计 %d 条", len(items), total)

            self.sleep()

        log.info("完成，共抓取 %d 条数据", total)


# ---------------------------------------------------------------- 示例实现
class QuotesSpider(BaseSpider):
    """示例：抓取 https://quotes.toscrape.com （官方练习站点）"""

    delay = (0.5, 1.2)
    max_page = 3

    def build_urls(self) -> Iterable[str]:
        for page in range(1, self.max_page + 1):
            yield f"https://quotes.toscrape.com/page/{page}/"

    def parse(self, html: str, url: str) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")
        results = []
        for box in soup.select("div.quote"):
            results.append({
                "text": box.select_one("span.text").get_text(strip=True),
                "author": box.select_one("small.author").get_text(strip=True),
                "tags": ",".join(t.get_text(strip=True) for t in box.select("a.tag")),
            })
        return results


if __name__ == "__main__":
    QuotesSpider().run(save_func="csv")