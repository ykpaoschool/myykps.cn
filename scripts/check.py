#!/usr/bin/env python3
"""检查静态站：站内断链、残留动态链接、动态入口。

用法:
    python3 scripts/check.py [site] [build/manifest.json]
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
from pathlib import Path

ATTR_RE = re.compile(
    r"\b(?:href|src|action|poster|data-src|data-href)\s*=\s*[\"']([^\"']+)[\"']",
    re.I,
)

BAD_PATTERNS = [
    (re.compile(r"[\"'](?:https?://go\.myykps\.cn)?/?\?q="), "残留的站内 ?q= 链接"),
    (re.compile(r"[\"']https?://go\.myykps\.cn", re.I), "残留的绝对站内链接"),
    (re.compile(r"(?:update|install|xmlrpc)\.php"), "Drupal 动态入口"),
    (re.compile(r"/user/(?:login|register|password)"), "登录/注册入口"),
]

SKIP_PREFIXES = ("#", "mailto:", "tel:", "javascript:", "data:", "blob:")


def link_path(page: Path, site: Path, link: str) -> str | None:
    """把站内链接解析为站点根路径；外链返回 None。"""
    if link.lower().startswith(SKIP_PREFIXES):
        return None

    if link.startswith("//"):
        rest = link[2:]
        netloc, _, path = rest.partition("/")
        if netloc.lower() != "go.myykps.cn":
            return None
        path = "/" + path
    else:
        parts = urllib.parse.urlsplit(link)
        if parts.scheme in ("http", "https"):
            if parts.netloc.lower() != "go.myykps.cn":
                return None
            path = parts.path or "/"
        elif parts.scheme:
            return None
        else:
            base = "/" + page.relative_to(site).as_posix()
            path = urllib.parse.urlsplit(urllib.parse.urljoin(base, link)).path

    return urllib.parse.quote(urllib.parse.unquote(path), safe="/") or "/"


def resolve(site: Path, url_path: str) -> Path:
    target = site / urllib.parse.unquote(url_path).lstrip("/")
    if target.is_dir():
        return target / "index.html"
    if not target.exists() and not target.suffix:
        return target / "index.html"
    return target


def main() -> int:
    site = Path(sys.argv[1] if len(sys.argv) > 1 else "site").resolve()
    manifest_path = Path(
        sys.argv[2] if len(sys.argv) > 2 else "build/manifest.json")
    if not site.is_dir():
        print(f"目录不存在: {site}")
        return 2

    known: set[str] = set()
    if manifest_path.is_file():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        known = {item["path"] for item in data.get("known_missing", [])}

    broken: list[tuple[str, str]] = []
    already_missing: list[tuple[str, str]] = []
    leftovers: list[tuple[str, str, str]] = []
    pages = sorted(site.rglob("*.html"))

    for page in pages:
        text = page.read_text(encoding="utf-8", errors="replace")
        for m in ATTR_RE.finditer(text):
            link = m.group(1)
            path = link_path(page, site, link)
            if path is None:
                continue
            if path in known or not resolve(site, path).exists():
                bucket = already_missing if path in known else broken
                bucket.append((page.relative_to(site).as_posix(), link))
        for pattern, label in BAD_PATTERNS:
            for m in pattern.finditer(text):
                snippet = text[max(0, m.start() - 40):m.end() + 40].replace("\n", " ")
                leftovers.append((page.relative_to(site).as_posix(), label, snippet.strip()))

    print(f"扫描页面 {len(pages)} 个")
    if broken:
        print(f"\n断链 {len(broken)} 处：")
        for page, link in broken[:80]:
            print(f"  {page}  ->  {link}")
        if len(broken) > 80:
            print(f"  ... 其余 {len(broken) - 80} 处省略")
    else:
        print("没有站内断链。")

    if already_missing:
        print(f"\n线上即缺失的资源 {len(already_missing)} 处（镜像前就 404，非抓取遗漏）：")
        for page, link in already_missing[:20]:
            print(f"  {page}  ->  {link}")
        if len(already_missing) > 20:
            print(f"  ... 其余 {len(already_missing) - 20} 处省略")

    if leftovers:
        print(f"\n需要人工确认的残留 {len(leftovers)} 处：")
        for page, label, snippet in leftovers[:40]:
            print(f"  [{label}] {page}: {snippet}")
        if len(leftovers) > 40:
            print(f"  ... 其余 {len(leftovers) - 40} 处省略")
    else:
        print("\n没有残留的动态链接或动态入口。")

    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
