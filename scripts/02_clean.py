#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
02_clean.py —— 数据加工/清洗环节（对应课程「数据加工与清洗」）

课程要求落地为以下步骤：
  1. 格式转换：网页 HTML → 纯文本（在 01_fetch.py 已完成），此处再做 Markdown 结构化；
  2. 去格式噪音：删除页眉页脚、导航条、责任编辑、打印本页等网站固有噪声；
  3. 去重：删除与标题重复的正文首行；
  4. 空白规范化：全角空格、行首行尾空格、连续空行；
  5. 段落结构化：识别「裁判要点 / 基本案情 / 裁判结果 / 裁判理由 / 相关法条」等
     指导性案例固定板块，提升为 Markdown 二级标题（便于后续 RAG 切片与关键词检索）；
  6. 元信息外置：把「来源 / 发布时间 / 采集时间」从正文搬到 YAML Front Matter（下一步完成）。
"""
import json
import os
import re
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(ROOT, "data", "raw")
CLEAN_DIR = os.path.join(ROOT, "data", "clean")
LOG_DIR = os.path.join(ROOT, "logs")
os.makedirs(CLEAN_DIR, exist_ok=True)

# 网站固有噪声行（页眉页脚、功能按钮、编辑信息）
NOISE_PATTERNS = [
    r"^\s*字号[:：]?\s*小?\s*中?\s*大?\s*$",
    r"^\s*打印本页\s*$",
    r"^\s*关闭窗口\s*$",
    r"^\s*(上一篇|下一篇)[:：]?.*$",
    r"^\s*责任编辑[:：].*$",
    r"^\s*(来源|稿件来源)[:：].*$",
    r"^\s*发布时间[:：].*$",
    r"^\s*(浏览量|访问量)[:：].*$",
    r"^\s*【大\s*中\s*小】\s*$",
    r"^\s*分享到[:：]?.*$",
    r"^\s*版权声明[:：]?.*$",
    r"^\s*Copyright.*$",
    r"^\s*地址[:：].*$",
    r"^\s*技术支持[:：].*$",
    r"^\s*最高人民法院\s*$",
    r"^\s*(小|中|大)\s*$",                  # 字号切换按钮
    r"^\s*【?字体[:：]?.*$",
    r"^\s*(来源URL|采集时间|抓取时间)[:：].*$",   # 采集脚本写入的元信息，后续移入 YAML
    r"^\s*扫一扫在手机\w*.*$",
    r"^\s*(微信|微博|QQ)\s*$",
]
NOISE_RE = [re.compile(p) for p in NOISE_PATTERNS]

# 指导性案例固定板块 → Markdown 标题
SECTIONS = ["裁判要点", "基本案情", "裁判结果", "裁判理由", "相关法条", "生效裁判", "关键词"]
SECTION_RE = re.compile(r"^\s*(" + "|".join(SECTIONS) + r")\s*$")

CST = timezone(timedelta(hours=8))


def log(msg):
    line = f"[{datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(os.path.join(LOG_DIR, "clean.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_raw_meta(text):
    """从 01 步落盘的 txt 顶部读回元信息"""
    meta, body_lines = {}, []
    in_meta = False
    for i, line in enumerate(text.splitlines()):
        if i == 0 and line.startswith("# "):
            meta["title"] = line[2:].strip()
            in_meta = True
            continue
        if in_meta:
            m = re.match(r"^(来源URL|发布时间|来源|采集时间)[:：]\s*(.*)$", line)
            if m:
                meta[m.group(1)] = m.group(2).strip()
                continue
            if line.strip() == "":
                continue          # 元信息区允许空行，继续等待后续键值
            in_meta = False       # 遇到正文行才退出元信息区
        body_lines.append(line)
    return meta, "\n".join(body_lines)


def clean_text(text, title):
    lines = text.splitlines()
    out, removed = [], {"noise": 0, "blank": 0, "dup_title": 0}

    # 1) 噪声行过滤
    for ln in lines:
        if any(r.match(ln) for r in NOISE_RE):
            removed["noise"] += 1
            continue
        out.append(ln)

    # 2) 删除与标题重复的正文首行
    body = "\n".join(out)
    norm_title = re.sub(r"\s+", "", title or "")
    lines = body.splitlines()
    first_idx = next((i for i, l in enumerate(lines) if l.strip()), None)
    if first_idx is not None and norm_title and re.sub(r"\s+", "", lines[first_idx]) == norm_title:
        lines[first_idx] = ""
        removed["dup_title"] += 1
    body = "\n".join(lines)

    # 3) 空白规范化
    body = body.replace("\u3000", " ").replace("\xa0", " ").replace("\u200b", "")
    body = re.sub(r"[ \t]+", " ", body)
    body = "\n".join(l.strip() for l in body.splitlines())
    body = re.sub(r"\n{3,}", "\n\n", body)

    # 4) 板块结构化
    lines = body.splitlines()
    for i, ln in enumerate(lines):
        m = SECTION_RE.match(ln)
        if m and 0 < len(ln) < 12:
            lines[i] = f"## {m.group(1)}"
    body = "\n".join(lines)

    # 5) 去掉文首多余空行
    body = body.strip() + "\n"
    return body, removed


def main():
    manifest_path = os.path.join(RAW_DIR, "manifest.json")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    report = []
    for item in manifest:
        raw_path = os.path.join(RAW_DIR, item["raw_txt"])
        if not os.path.exists(raw_path):
            log(f"! 缺失源文件 {item['raw_txt']}，跳过")
            continue
        with open(raw_path, encoding="utf-8") as f:
            text = f.read()
        meta, body = read_raw_meta(text)
        cleaned, removed = clean_text(body, meta.get("title", item["title"]))

        out_name = item["raw_txt"].replace(".txt", ".md")
        with open(os.path.join(CLEAN_DIR, out_name), "w", encoding="utf-8") as f:
            f.write(cleaned)

        report.append({
            "seq": item["seq"],
            "title": meta.get("title", item["title"]),
            "url": item.get("url"),
            "publish_date": meta.get("发布时间") or item.get("publish_date"),
            "source": meta.get("来源") or item.get("source"),
            "collected_at": meta.get("采集时间") or item.get("collected_at"),
            "raw_chars": len(text),
            "clean_chars": len(cleaned),
            "removed_noise_lines": removed["noise"],
            "removed_dup_title": removed["dup_title"],
            "clean_file": out_name,
        })
        log(f"[{item['seq']:02d}] 清洗完成 {out_name}（{len(text)}→{len(cleaned)} 字，去噪 {removed['noise']} 行）")

    with open(os.path.join(CLEAN_DIR, "clean_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    log(f"=== 清洗完成，共 {len(report)} 个文件 ===")


if __name__ == "__main__":
    main()
