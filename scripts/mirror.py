#!/usr/bin/env python3
"""把 go.myykps.cn 的公开页面抓取为可离线部署的静态站点。

站点是 Drupal 7，URL 形如 /?q=it。本脚本按页面白名单爬取，
把页面存成 /it/index.html，并把站内链接统一改写为静态路径。

用法:
    python3 scripts/mirror.py --out site --archive archive/raw
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

HOST = "go.myykps.cn"
BASE = f"https://{HOST}"
UA = "Mozilla/5.0 (compatible; ykps-portal-mirror/0.1.0)"

ASSET_EXTS = {
    ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".7z", ".txt", ".csv", ".xml", ".json", ".ics", ".vcf",
    ".apk", ".ipa", ".dmg", ".pkg", ".exe", ".msi", ".deb", ".rpm",
    ".mp4", ".mp3", ".webm", ".ogg", ".wav",
}

# 不抓取的 Drupal 动态入口。注意 admin_links 是正常内容页，不能被 ^admin 误伤。
DENY_PATTERNS = [
    r"^user(?:/|$)",
    r"^admin(?:/|$)",
    r"^userloginmain$",
    r"^node/68$",                       # 登录说明页，静态化后无意义
    r"^node/\d+/(?:edit|delete|revisions)(?:/|$)",
    r"^node/add(?:/|$)",
    r"^comment/(?:reply|edit|delete)(?:/|$)",
    r"^search(?:/|$)",
    r"^logout(?:/|$)",
    r"^batch(?:/|$)",
    r"^image_captcha",
    r"^print/68$",                      # 登录页的打印视图
    r"^printmail(?:/|$)",
    r"^taxonomy/term/\d+/feed$",
    r"\.(?:xml|json)$",                 # 被误当作页面链接的文件
    r"^(?:update|install|xmlrpc|cron)\.php$",
    r"^filter/tips",
    r"^system/",
    r"^file/ajax",
    r"^views/ajax",
    r"^contextual/",
]
DENY_RE = [re.compile(p) for p in DENY_PATTERNS]

ATTR_RE = re.compile(
    r"(?P<pre>\b(?P<name>href|src|action|poster|about|data-src|data-href|srcset)\s*=\s*)"
    r"(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    re.I | re.S,
)
STYLE_ATTR_RE = re.compile(r"(\bstyle\s*=\s*)([\"'])(.*?)\2", re.I | re.S)
CSS_URL_RE = re.compile(r"url\(\s*([\"']?)([^\"')]+)\1\s*\)", re.I)
CSS_IMPORT_RE = re.compile(r"(@import\s+)([\"'])([^\"']+)\2", re.I)
SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.I | re.S)
SCRIPT_ABS_RE = re.compile(
    r"(?P<q>[\"'])(?P<url>https?://go\.myykps\.cn[^\"'\\\s)]*)(?P=q)")
BODY_OPEN_RE = re.compile(r"<body\b[^>]*>", re.I)
FORM_OPEN_RE = re.compile(r"<form\b[^>]*>", re.I)
INPUT_RE = re.compile(r"<(input|button|select|textarea)\b([^>]*)>", re.I)
GENERATOR_RE = re.compile(r"<meta[^>]*name=[\"']Generator[\"'][^>]*>\s*", re.I)
RSS_LINK_RE = re.compile(
    r"<link\b[^>]*type=[\"']application/(?:rss|atom)\+xml[\"'][^>]*>\s*", re.I)
HEAD_OPEN_RE = re.compile(r"<head\b[^>]*>", re.I)

BANNER = (
    '<div id="mirror-banner" style="background:#fff4e5;border-bottom:1px solid '
    '#f0c36d;color:#7a4b00;font:14px/1.6 -apple-system,BlinkMacSystemFont,'
    '\'Helvetica Neue\',Arial,sans-serif;padding:8px 14px;text-align:center;">'
    '本门户已下线，现为只读存档；登录、搜索与提交功能已停用。'
    '</div>'
)


class Mirror:
    def __init__(self, out_dir, archive_dir=None, delay=0.2, banner=True,
                 disable_forms=True, noindex=False):
        self.out = Path(out_dir)
        self.archive = Path(archive_dir) if archive_dir else None
        self.delay = delay
        self.banner = banner
        self.disable_forms = disable_forms
        self.noindex = noindex

        self.page_queue: deque[str] = deque()
        self.asset_queue: deque[str] = deque()
        self.seen_pages: set[str] = set()
        self.seen_assets: set[str] = set()
        self.pages_ok: list[str] = []
        self.assets_ok: list[str] = []
        self.failures: list[tuple[str, str]] = []

    # ---------- 队列 ----------

    def enqueue_page(self, key: str) -> None:
        key = key.strip("/")
        if key in self.seen_pages or self.denied(key):
            return
        self.seen_pages.add(key)
        self.page_queue.append(key)

    def enqueue_asset(self, path: str) -> None:
        # 队列里保存未转义的路径；请求时再编码，落盘时直接用原始字符。
        path = urllib.parse.unquote(path).lstrip("/")
        if not path or ".." in path.split("/"):
            return
        if path in self.seen_assets:
            return
        self.seen_assets.add(path)
        self.asset_queue.append(path)

    @staticmethod
    def denied(key: str) -> bool:
        return any(r.search(key) for r in DENY_RE)

    @staticmethod
    def page_url(key: str) -> str:
        key = key.strip("/")
        return "/" if not key else f"/{urllib.parse.quote(key, safe='/')}/"

    def page_file(self, key: str) -> Path:
        key = key.strip("/")
        return self.out / "index.html" if not key else self.out / key / "index.html"

    # ---------- 抓取 ----------

    def get(self, url: str) -> tuple[str, str, bytes]:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,text/css,*/*;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.geturl(), charset, resp.read()

    def run(self) -> int:
        self.enqueue_page("")
        self.enqueue_asset("robots.txt")
        while self.page_queue or self.asset_queue:
            if self.page_queue:
                self.fetch_page(self.page_queue.popleft())
            else:
                self.fetch_asset(self.asset_queue.popleft())
            time.sleep(self.delay)
        self.write_special_pages()
        self.write_manifest()
        self.report()
        return 1 if self.failures else 0

    def fetch_page(self, key: str) -> None:
        url = f"{BASE}/" if not key else f"{BASE}/?q={urllib.parse.quote(key, safe='/')}"
        try:
            final_url, charset, body = self.get(url)
        except urllib.error.HTTPError as exc:
            self.failures.append((url, f"HTTP {exc.code}"))
            return
        except Exception as exc:  # noqa: BLE001
            self.failures.append((url, repr(exc)))
            return

        raw = body.decode(charset, errors="replace")
        self.save_archive(key, raw)

        page_url = final_url
        doc = self.rewrite_document(raw, page_url)
        if self.banner:
            doc = self.inject_banner(doc)
        if self.disable_forms:
            doc = self.neutralize_forms(doc)
        doc = GENERATOR_RE.sub("", doc)
        doc = RSS_LINK_RE.sub("", doc)
        if self.noindex:
            doc = HEAD_OPEN_RE.sub(
                lambda m: m.group(0) + '\n<meta name="robots" content="noindex">',
                doc, count=1)

        target = self.page_file(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(doc, encoding="utf-8")
        self.pages_ok.append(key or "/")

    def fetch_asset(self, path: str) -> None:
        url = f"{BASE}/{urllib.parse.quote(path, safe='/')}"
        try:
            _, charset, body = self.get(url)
        except urllib.error.HTTPError as exc:
            self.failures.append((url, f"HTTP {exc.code}"))
            return
        except Exception as exc:  # noqa: BLE001
            self.failures.append((url, repr(exc)))
            return

        target = self.out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.lower().endswith(".css"):
            text = body.decode(charset, errors="replace")
            text = self.rewrite_css(text, url)
            target.write_text(text, encoding="utf-8")
        else:
            target.write_bytes(body)
        self.assets_ok.append(path)

    def save_archive(self, key: str, raw: str) -> None:
        if not self.archive:
            return
        name = (key or "index").replace("/", "__") + ".html"
        self.archive.mkdir(parents=True, exist_ok=True)
        (self.archive / name).write_text(raw, encoding="utf-8")

    # ---------- 改写 ----------

    def rewrite_document(self, text: str, page_url: str) -> str:
        out: list[str] = []
        pos = 0
        for match in SCRIPT_BLOCK_RE.finditer(text):
            out.append(self.rewrite_markup(text[pos:match.start()], page_url))
            block = match.group(0)
            end = block.find(">")
            opening = self.rewrite_markup(block[:end + 1], page_url)
            body = SCRIPT_ABS_RE.sub(
                lambda m: m.group("q")
                + self.rewrite_url(m.group("url"), page_url)
                + m.group("q"),
                block[end + 1:])
            out.append(opening + body)
            pos = match.end()
        out.append(self.rewrite_markup(text[pos:], page_url))
        return "".join(out)

    def rewrite_markup(self, text: str, page_url: str) -> str:
        text = ATTR_RE.sub(lambda m: self.attr_repl(m, page_url), text)
        text = STYLE_ATTR_RE.sub(lambda m: self.style_attr_repl(m, page_url), text)
        text = self.rewrite_css(text, page_url)
        return text

    def attr_repl(self, match: re.Match, page_url: str) -> str:
        name = match.group("name").lower()
        value = match.group("value")
        if name == "srcset":
            new = self.rewrite_srcset(value, page_url)
        else:
            new = self.rewrite_url(html.unescape(value), page_url)
        return f'{match.group("pre")}{match.group("quote")}{new}{match.group("quote")}'

    def style_attr_repl(self, match: re.Match, page_url: str) -> str:
        css = self.rewrite_css(match.group(3), page_url)
        return f"{match.group(1)}{match.group(2)}{css}{match.group(2)}"

    def rewrite_srcset(self, value: str, page_url: str) -> str:
        items = []
        for part in value.split(","):
            part = part.strip()
            if not part:
                continue
            bits = part.split()
            new = self.rewrite_url(html.unescape(bits[0]), page_url)
            items.append(" ".join([new, *bits[1:]]))
        return ", ".join(items)

    def rewrite_css(self, css: str, base_url: str) -> str:
        def url_repl(m: re.Match) -> str:
            quote = m.group(1) or ""
            new = self.rewrite_url(html.unescape(m.group(2)), base_url)
            return f"url({quote}{new}{quote})"

        def import_repl(m: re.Match) -> str:
            new = self.rewrite_url(html.unescape(m.group(3)), base_url)
            return f"{m.group(1)}{m.group(2)}{new}{m.group(2)}"

        css = CSS_URL_RE.sub(url_repl, css)
        css = CSS_IMPORT_RE.sub(import_repl, css)
        return css

    def rewrite_url(self, raw: str, base_url: str) -> str:
        value = raw.strip()
        if not value:
            return raw
        if value.lower().startswith(
                ("#", "mailto:", "tel:", "javascript:", "data:", "blob:")):
            return raw

        absolute = urllib.parse.urljoin(base_url, value)
        parts = urllib.parse.urlsplit(absolute)
        if parts.scheme not in ("http", "https"):
            return raw
        if parts.netloc != HOST:
            return raw  # 外链保持原样

        frag = f"#{parts.fragment}" if parts.fragment else ""
        q = urllib.parse.parse_qs(parts.query).get("q", [None])[0]
        if q is not None:
            key = q.strip("/")
            if not key:
                return "/" + frag
            if self.denied(key):
                return "/offline/" + frag
            self.enqueue_page(key)
            return self.page_url(key) + frag

        path = parts.path or "/"
        if not path.startswith("/"):
            path = "/" + path
        ext = os.path.splitext(path)[1].lower()
        if ext in ASSET_EXTS:
            self.enqueue_asset(path)
        try:
            path = urllib.parse.quote(urllib.parse.unquote(path), safe="/")
        except UnicodeDecodeError:
            pass
        return path + frag

    # ---------- 清理 ----------

    def inject_banner(self, text: str) -> str:
        return BODY_OPEN_RE.sub(lambda m: m.group(0) + BANNER, text, count=1)

    def neutralize_forms(self, text: str) -> str:
        def form_repl(m: re.Match) -> str:
            tag = m.group(0)
            tag = re.sub(r"\baction\s*=\s*([\"']).*?\1", 'action="/offline/"',
                         tag, flags=re.I | re.S)
            tag = re.sub(r"\bmethod\s*=\s*([\"']).*?\1", 'method="get"',
                         tag, flags=re.I | re.S)
            if "onsubmit" not in tag.lower():
                tag = tag[:-1] + ' onsubmit="return false;">'
            return tag

        def input_repl(m: re.Match) -> str:
            tag, attrs = m.group(1), m.group(2)
            if re.search(r"\bdisabled\b", attrs, re.I):
                return m.group(0)
            return f"<{tag}{attrs} disabled=\"disabled\">"

        text = FORM_OPEN_RE.sub(form_repl, text)
        text = INPUT_RE.sub(input_repl, text)
        return text

    # ---------- 生成 ----------

    def write_manifest(self) -> None:
        known = []
        for url, reason in self.failures:
            parts = urllib.parse.urlsplit(url)
            q = urllib.parse.parse_qs(parts.query).get("q", [None])[0]
            path = self.page_url(q) if q else (parts.path or "/")
            known.append({"url": url, "path": path, "reason": reason})

        build = Path("build")
        build.mkdir(exist_ok=True)
        (build / "manifest.json").write_text(
            json.dumps({
                "host": HOST,
                "pages": sorted(self.pages_ok),
                "assets": sorted(self.assets_ok),
                "known_missing": known,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def write_special_pages(self) -> None:
        offline = self.out / "offline" / "index.html"
        offline.parent.mkdir(parents=True, exist_ok=True)
        offline.write_text(
            "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>功能已停用</title></head><body style=\"font:16px/1.7 "
            "-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;"
            "color:#333;max-width:640px;margin:80px auto;padding:0 20px;\">"
            "<h1 style=\"font-size:22px;\">该功能已停止服务</h1>"
            "<p>本门户已下线，现为只读存档。登录、搜索、表单提交等功能不可用。</p>"
            "<p><a href=\"/\" style=\"color:#0b6bcb;\">返回首页</a></p>"
            "</body></html>",
            encoding="utf-8",
        )

        (self.out / "404.html").write_text(
            "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>页面不存在</title></head><body style=\"font:16px/1.7 "
            "-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;"
            "color:#333;max-width:640px;margin:80px auto;padding:0 20px;\">"
            "<h1 style=\"font-size:22px;\">页面不存在</h1>"
            "<p>该页面可能已随门户下线而移除。</p>"
            "<p><a href=\"/\" style=\"color:#0b6bcb;\">返回首页</a></p>"
            "</body></html>",
            encoding="utf-8",
        )

    def report(self) -> None:
        size = sum(p.stat().st_size for p in self.out.rglob("*") if p.is_file())
        print(f"\n页面 {len(self.pages_ok)} 个，资源 {len(self.assets_ok)} 个，"
              f"合计 {size / 1024 / 1024:.2f} MB")
        if self.failures:
            print(f"\n抓取失败 {len(self.failures)} 项：")
            for url, reason in self.failures:
                print(f"  {url}  ->  {reason}")
        else:
            print("没有失败项。")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="site", help="静态站点输出目录")
    parser.add_argument("--archive", default="archive/raw",
                        help="原始 HTML 存档目录，设为空字符串可关闭")
    parser.add_argument("--delay", type=float, default=0.2,
                        help="每次请求后的等待秒数")
    parser.add_argument("--no-banner", action="store_true", help="不插入只读提示条")
    parser.add_argument("--keep-forms", action="store_true", help="不禁用表单控件")
    parser.add_argument("--noindex", action="store_true",
                        help="给页面加 robots noindex")
    args = parser.parse_args()

    mirror = Mirror(
        out_dir=args.out,
        archive_dir=args.archive or None,
        delay=args.delay,
        banner=not args.no_banner,
        disable_forms=not args.keep_forms,
        noindex=args.noindex,
    )
    return mirror.run()


if __name__ == "__main__":
    sys.exit(main())
