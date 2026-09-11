#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
04_frontmatter.py —— 元数据注入环节（对应课程「增加元信息以实现结构化」）

为每个独立文件补写 YAML Front Matter，使原始语料变成"可被检索、可被治理"的结构化数据：
  · 标识层：id / title / doc_type
  · 溯源层：source.name / source.url / publish_date / collected_at / collect_method
  · 内容层：keywords / cause_of_action / court_level / legal_basis / summary
  · 治理层：desensitization.*（是否脱敏、使用方法、脱敏处数）
  · 加工层：processing.pipeline / checksum
  · 使用层：usage / license_note（版权与合规声明）

同时生成 index.md（全库索引，便于人或 Agent 一眼看清库里有什么）。
"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLEAN_DIR = os.path.join(ROOT, "data", "clean")
FINAL_DIR = os.path.join(ROOT, "data", "final")
LOG_DIR = os.path.join(ROOT, "logs")
CST = timezone(timedelta(hours=8))

COURT_LEVELS = [
    ("最高人民法院", "最高人民法院"),
    ("高级人民法院", "高级人民法院"),
    ("中级人民法院", "中级人民法院"),
    ("知识产权法院", "专门法院（知识产权）"),
    ("互联网法院", "专门法院（互联网）"),
    ("海事法院", "专门法院（海事）"),
    ("金融法院", "专门法院（金融）"),
    ("铁路运输法院", "专门法院（铁路）"),
    ("人民法院", "基层人民法院"),
]


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def yaml_escape(s):
    s = str(s).replace('"', "'").strip()
    return s


def infer_court_level(clean_text):
    # 排除"最高人民法院审判委员会讨论通过"这一发布表述，避免误判为最高法审理
    text = clean_text.replace("最高人民法院审判委员会", "")
    courts = re.findall(r"[\u4e00-\u9fa5]{2,10}?人民法院", text)
    courts = [c for c in courts if c != "最高人民法院"]
    for key, level in COURT_LEVELS:
        if key == "最高人民法院":
            continue
        if any(key in c for c in courts):
            return level
    if any(k in text for k in ("知识产权法院", "互联网法院", "海事法院", "金融法院")):
        return "专门法院"
    return "未载明"


def extract_masked_title(body_text, fallback):
    """从已脱敏正文中取标题（正文首两行，如 '指导性案例266号 / [自然人]诉[法人]...纠纷案'）"""
    lines = [l.strip().lstrip("# ").strip() for l in body_text.splitlines() if l.strip()]
    if not lines:
        return fallback
    title_parts = [lines[0]]
    for ln in lines[1:3]:                       # 标题可能折行，最多拼三行
        if ln.startswith(("（", "(")) or ln.startswith("##"):
            break
        title_parts.append(ln)
    return "：".join(title_parts[:2]) if len(title_parts) > 1 else title_parts[0]


def strip_existing_front_matter(text):
    """幂等：若已有 YAML Front Matter 则先剥离，避免重复叠加"""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5:].lstrip("\n")
    return text


def extract_keywords(clean_text):
    m = re.search(r"关键词\s*(.+)", clean_text)
    if not m:
        return [], ""
    raw = m.group(1).strip()
    parts = [p.strip() for p in re.split(r"[/、,，]", raw) if p.strip()]
    return parts[:10], (parts[0] if parts else "")


def extract_legal_basis(clean_text):
    block = re.search(r"##\s*相关法条\s*\n(.+?)(?=\n##|\Z)", clean_text, re.S)
    if not block:
        return []
    items = re.findall(r"《[^》]+》", block.group(1))
    seen, out = set(), []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out[:8]


def extract_summary(clean_text, limit=110):
    m = re.search(r"##\s*裁判要点\s*\n(.+?)(?=\n##|\Z)", clean_text, re.S)
    body = m.group(1).strip() if m else clean_text.strip()
    body = re.sub(r"\s+", " ", body)
    return (body[:limit] + "…") if len(body) > limit else body


def build_front_matter(item, clean_text, body_text, ditem):
    keywords, cause = extract_keywords(clean_text)
    level = infer_court_level(clean_text)
    basis = extract_legal_basis(clean_text)
    checksum = hashlib.sha256(body_text.encode("utf-8")).hexdigest()[:16]
    cid = re.search(r"指导性案例(\d+)号", item["title"])
    case_id = f"gzcase-{cid.group(1)}" if cid else f"gzcase-{item['seq']:02d}"

    def q(v):
        return f'"{yaml_escape(v)}"'

    lines = [
        "---",
        f"id: {case_id}",
        f"title: {q(item['title'])}",
        "doc_type: 指导性案例",
        "language: zh-CN",
        "source:",
        "  name: 中华人民共和国最高人民法院",
        f"  url: {q(item.get('url', ''))}",
        "  channel: 官网·审判业务·指导案例栏目",
        "  authority_level: 国家级（最高审判机关）",
        f"  publish_date: {q(item.get('publish_date') or '未知')}",
        f"collected_at: {q(item.get('collected_at') or '')}",
        "collect_method: 自动采集脚本 scripts/01_fetch.py（HTTP 请求 + 解析）",
        "keywords:",
    ]
    lines += [f"  - {q(k)}" for k in keywords] if keywords else ["  - 未标注"]
    lines += [
        f"cause_of_action: {q(cause or '未标注')}",
        f"court_level: {q(level)}",
        "legal_basis:",
    ]
    lines += [f"  - {q(b)}" for b in basis] if basis else ["  - 未标注"]
    lines += [
        "desensitization:",
        "  applied: true",
        "  methods:",
        "    - regex（案号/证件号/联系方式/车牌等结构化敏感信息）",
        "    - dictionary（法院/检察/公安/律所/公司/行政区划词典）",
        "    - rule_ner（匿名化姓氏模式 + 频次确认 + 简称归并）",
        "    - llm_review（大模型复核候选清单，人工确认后回填）",
        f"  masked_count: {ditem.get('mask_total', 0)}",
        "  mapping_table: 本地留存（data/.masking_map.json），不随仓库分发",
        "processing:",
        "  pipeline: 01_fetch -> 02_clean -> 03_desensitize -> 04_frontmatter",
        "  cleaning:",
        "    - html_to_text（去标签、还原实体）",
        "    - noise_removal（页眉页脚/字号按钮/责任编辑/来源时间行）",
        "    - dedup_and_whitespace_normalization",
        "    - section_structuring（裁判要点/基本案情/裁判结果/裁判理由/相关法条）",
        f"  checksum: sha256:{checksum}",
        f"  chars: {len(body_text)}",
        "usage: 个人知识库构建与学习研究，禁止商业传播",
        "license_note: 原文著作权归中华人民共和国最高人民法院所有；本文件为经脱敏、清洗与结构化处理的二次加工版本",
        "---",
        "",
    ]
    return "\n".join(lines)


def main():
    clean_items = read_json(os.path.join(CLEAN_DIR, "clean_report.json"))
    des_items = {d["seq"]: d for d in read_json(os.path.join(LOG_DIR, "desensitize_report.json"))}

    index_rows = []
    for item in clean_items:
        clean_path = os.path.join(CLEAN_DIR, item["clean_file"])
        final_path = os.path.join(FINAL_DIR, item["clean_file"].replace(".md", ".desensitized.md"))
        if not os.path.exists(final_path):
            continue
        clean_text = open(clean_path, encoding="utf-8").read()
        body_text = strip_existing_front_matter(open(final_path, encoding="utf-8").read())

        ditem = des_items.get(item["seq"], {})
        # 标题取脱敏后的正文首行，避免元信息里残留真实主体名称
        masked_title = extract_masked_title(body_text, item["title"])
        item = dict(item, title=masked_title)
        fm = build_front_matter(item, clean_text, body_text, ditem)

        cid = re.search(r"指导性案例(\d+)号", item["title"]) or re.search(r"指导性案例(\d+)号", masked_title)
        case_id = f"gzcase-{cid.group(1)}" if cid else f"gzcase-{item['seq']:02d}"
        new_name = f"{item['seq']:02d}_{case_id}.md"      # 文件名中性化，避免主体名残留在路径中
        new_path = os.path.join(FINAL_DIR, new_name)
        if os.path.abspath(new_path) != os.path.abspath(final_path) and os.path.exists(final_path):
            os.replace(final_path, new_path)      # 重命名而非删除，避免中断流水线

        cid = re.search(r"指导性案例(\d+)号", item["title"]) or re.search(r"指导性案例(\d+)号", masked_title)
        case_id = f"gzcase-{cid.group(1)}" if cid else f"gzcase-{item['seq']:02d}"
        new_name = f"{item['seq']:02d}_{case_id}.md"      # 文件名中性化，避免主体名残留在路径中
        new_path = os.path.join(FINAL_DIR, new_name)
        if os.path.abspath(new_path) != os.path.abspath(final_path) and os.path.exists(final_path):
            os.replace(final_path, new_path)      # 重命名而非删除，避免中断流水线

        # 正文首行规范为一级标题（若尚未是标题）
        lines = body_text.splitlines()
        if lines and not lines[0].startswith("# "):
            lines[0] = f"# {lines[0].strip()}"
        body_text = "\n".join(lines).strip() + "\n"

        with open(new_path, "w", encoding="utf-8") as f:
            f.write(fm + "\n" + body_text)

        index_rows.append({
            "seq": item["seq"],
            "file": new_name,
            "title": item["title"],
            "case_id": case_id,
            "date": item.get("publish_date") or "未知",
            "mask": ditem.get("mask_total", 0),
            "chars": len(body_text),
            "url": item.get("url", ""),
        })
        print(f"[{item['seq']:02d}] 元信息写入完成 {new_name}")

    # 生成索引
    idx = ["# 指导性案例知识库索引", "",
           f"> 自动生成于 {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}（04_frontmatter.py）",
           f"> 共 {len(index_rows)} 篇，全部经脱敏与元信息注入。", "",
           "| # | 编号 | 标题（已脱敏） | 发布日期 | 脱敏处数 | 字数 |",
           "|---|---|---|---|---|---|"]
    for r in index_rows:
        idx.append(f"| {r['seq']:02d} | {r['case_id']} | [{r['title']}](data/final/{r['file']}) "
                   f"| {r['date']} | {r['mask']} | {r['chars']} |")
    with open(os.path.join(ROOT, "index.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(idx) + "\n")

    with open(os.path.join(LOG_DIR, "final_index.json"), "w", encoding="utf-8") as f:
        json.dump(index_rows, f, ensure_ascii=False, indent=2)
    print(f"=== 元信息注入完成：{len(index_rows)} 个文件，index.md 已生成 ===")


if __name__ == "__main__":
    main()
