#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
01_fetch.py —— 数据采集环节（对应课程「数据采集」环节）

数据源：中华人民共和国最高人民法院官网 · 审判业务 · 指导案例栏目
采集方式：全自动（HTTP 请求 + 列表页/详情页解析），即课程所讲「全自动采集（API 调用或爬虫脚本）」

合规约束（课程要求）：
  1. 仅自用、小批量，不用于商业传播；
  2. 单线程、每篇间隔 1.2 秒，不并发、不对服务器造成负担；
  3. 尊重 robots 与版权，原始 HTML 只落本地，脱敏后才进入知识库；
  4. 可断点续采：已采集过的 URL 会跳过。
"""
import hashlib
import html
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

BASE = "https://www.court.gov.cn"
LIST_URLS = [f"{BASE}/shenpan/gengduo/77.html"] + [f"{BASE}/shenpan/gengduo/77_{i}.html" for i in range(2, 4)]
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
DELAY = 1.2          # 礼貌爬取间隔（秒）
MAX_ITEMS = 15       # 采集上限（作业要求不少于 10 个独立文件）

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(ROOT, "data", "raw")
LOG_DIR = os.path.join(ROOT, "logs")
os.makedirs(RAW_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

CST = timezone(timedelta(hours=8))


def now_cst():
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    line = f"[{now_cst()}] {msg}"
    print(line, flush=True)
    with open(os.path.join(LOG_DIR, "fetch.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def http_get(url, retry=3):
    """带重试与退避的 GET，返回 str（UTF-8）"""
    for attempt in range(1, retry + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": BASE + "/",
            })
            with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
                raw = resp.read()
                enc = resp.headers.get_content_charset() or "utf-8"
                return raw.decode(enc, errors="ignore")
        except Exception as exc:  # noqa: BLE001
            log(f"  ! 第 {attempt} 次请求失败 {url} -> {exc}")
            time.sleep(2 * attempt)
    return None


def parse_list(page_html):
    """从列表页解析 (url, title)"""
    if not page_html:
        return []
    pattern = re.compile(r'href="(/shenpan/xiangqing/(\d+)\.html)"[^>]*>\s*([^<]{5,200})')
    seen, items = set(), []
    for m in pattern.finditer(page_html):
        path, doc_id, title = m.group(1), m.group(2), html.unescape(m.group(3)).strip()
        url = BASE + path
        if url in seen:
            continue
        seen.add(url)
        items.append({"url": url, "doc_id": doc_id, "title": title})
    return items


def strip_html(fragment):
    """HTML 片段 -> 保留段落结构的纯文本"""
    txt = re.sub(r"(?is)<(script|style).*?</\1>", "", fragment)
    txt = re.sub(r"(?i)<br\s*/?>", "\n", txt)
    txt = re.sub(r"(?i)</p>", "\n", txt)
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = html.unescape(txt)
    txt = txt.replace("\u3000", " ").replace("\xa0", " ")
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n\s*\n\s*\n+", "\n\n", txt)
    return txt.strip()


def parse_detail(page_html, url):
    """从详情页提取标题、发布时间、来源、正文"""
    if not page_html:
        return None
    title = None
    m = re.search(r"<title>(.*?)</title>", page_html, re.S)
    if m:
        title = html.unescape(m.group(1)).strip()
        title = re.sub(r"\s*-\s*中华人民共和国最高人民法院\s*$", "", title).strip()

    pub_date = None
    for pat in [r"(20\d{2}-\d{2}-\d{2})", r"(20\d{2})年(\d{1,2})月(\d{1,2})日"]:
        m = re.search(pat, page_html)
        if m:
            if len(m.groups()) == 3:
                pub_date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            else:
                pub_date = m.group(1)
            break

    # 正文：优先 content / TRS_Editor 等容器
    body_html = None
    for pat in [r'(?is)<div[^>]+class="[^"]*(?:content|article|TRS_Editor|detail)[^"]*"[^>]*>(.*?)<div[^>]+class="[^"]*(?:footer|share|related)',
                r'(?is)<div[^>]+class="[^"]*(?:content|article|TRS_Editor|detail)[^"]*"[^>]*>(.*?)</div>\s*</div>']:
        m = re.search(pat, page_html)
        if m and len(m.group(1)) > 300:
            body_html = m.group(1)
            break
    if not body_html:
        # 兜底：取 <p> 段落最多的连续区块
        paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", page_html)
        paras = [p for p in paras if len(re.sub(r"<[^>]+>", "", p)) > 15]
        body_html = "".join(f"<p>{p}</p>" for p in paras)

    body_text = strip_html(body_html or "")
    # 兜底：若正文过短，用整页去标签后截取
    if len(body_text) < 400:
        full = strip_html(re.sub(r"(?is)<(script|style|head).*?</\1>", "", page_html))
        body_text = full[:8000]

    source = None
    m = re.search(r"(来源|稿件来源)[：:]\s*([^<\s]{2,30})", page_html)
    if m:
        source = html.unescape(m.group(2)).strip()

    return {"title": title, "publish_date": pub_date, "source": source,
            "body_text": body_text, "body_html": body_html}


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else MAX_ITEMS
    log(f"=== 采集开始，目标上限 {limit} 篇 ===")

    entries, seen = [], set()
    for lurl in LIST_URLS:
        log(f"列表页: {lurl}")
        page = http_get(lurl)
        for it in parse_list(page):
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            entries.append(it)
        time.sleep(DELAY)
        if len(entries) >= limit:
            break
    entries = entries[:limit]
    log(f"列表解析完成，共 {len(entries)} 条候选")

    manifest = []
    for idx, it in enumerate(entries, 1):
        url = it["url"]
        safe = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", it["title"])[:40] or it["doc_id"]
        txt_path = os.path.join(RAW_DIR, f"{idx:02d}_{safe}.txt")
        html_path = os.path.join(RAW_DIR, f"{idx:02d}_{safe}.html")

        if os.path.exists(txt_path):
            log(f"[{idx}/{len(entries)}] 已存在，跳过 {safe}")
            with open(txt_path, encoding="utf-8") as f:
                body = f.read()
            manifest.append({"seq": idx, "url": url, "title": it["title"], "raw_txt": os.path.basename(txt_path)})
            continue

        log(f"[{idx}/{len(entries)}] 抓取 {it['title'][:30]}...")
        page = http_get(url)
        detail = parse_detail(page, url)
        if not detail or len(detail["body_text"]) < 200:
            log(f"  ! 正文过短或解析失败，跳过: {url}")
            continue
        time.sleep(DELAY)

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page or "")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"# {detail['title'] or it['title']}\n\n")
            f.write(f"来源URL: {url}\n")
            f.write(f"发布时间: {detail['publish_date'] or '未知'}\n")
            f.write(f"来源: {detail['source'] or '中华人民共和国最高人民法院'}\n")
            f.write(f"采集时间: {now_cst()}\n\n")
            f.write(detail["body_text"])

        manifest.append({
            "seq": idx,
            "doc_id": it["doc_id"],
            "title": (detail["title"] or it["title"]),
            "url": url,
            "publish_date": detail["publish_date"],
            "source": detail["source"] or "中华人民共和国最高人民法院",
            "collected_at": now_cst(),
            "raw_txt": os.path.basename(txt_path),
            "raw_html": os.path.basename(html_path),
            "text_length": len(detail["body_text"]),
            "sha256": hashlib.sha256(detail["body_text"].encode("utf-8")).hexdigest()[:16],
        })
        log(f"  -> 已保存 {os.path.basename(txt_path)}（{len(detail['body_text'])} 字）")

    with open(os.path.join(ROOT, "data", "raw", "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    log(f"=== 采集完成，有效文件 {len(manifest)} 个，manifest.json 已写入 ===")


if __name__ == "__main__":
    main()
