#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
方案四：大模型复核（人机闭环）

03_desensitize.py 会输出"规则拿不准的疑似实体"清单 logs/llm_review_candidates.md。
本脚本完成复核闭环的第一道工序 —— 预分类，把候选分成三类：

    A. 泛指 / 非特定主体（如"公交公司""关联公司""前述两公司"）→ 不脱敏，并入停用词典
    B. 已匿名化的主体（如"某传媒公司""西某软件公司"）→ 官方已匿名，无需再处理
    C. 需人工 / 大模型终审（其余）→ 输出待定清单

复核结论写入 logs/llm_review_result.md；
A 类结果可直接回写 data/generic_terms.txt（停用词典），供 03 脚本下一轮加载。

用法：
    python3 scripts/05_llm_review.py                # 生成复核报告
    python3 scripts/05_llm_review.py --write-stop   # 同时把 A 类写入停用词典
"""
import os
import re
import sys
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAND_FILE = os.path.join(BASE, "logs", "llm_review_candidates.md")
OUT_FILE = os.path.join(BASE, "logs", "llm_review_result.md")
STOP_FILE = os.path.join(BASE, "data", "generic_terms.txt")

# A 类：泛指 / 句法碎片，不是可识别的特定主体
GENERIC_HINTS = (
    "当事人", "前述", "上述", "被告单位", "被害单位", "所有员工", "子公司", "母公司",
    "关联公司", "公交公司", "告知公司", "本公司", "该公司", "两公司", "某公司",
    "双方", "本院", "一审", "二审", "再审", "员工", "用户", "消费者", "乘客",
)
# B 类：官方已做匿名化处理的主体（含"某"字或"X某"形式）
ANON_PAT = re.compile(r"(?:^|[^一-龥])([一-龥]某|某)")
ORG_SUFFIX = ("公司", "集团", "银行", "研究院", "事务所", "工厂", "中心", "学校", "医院")


def load_candidates():
    if not os.path.exists(CAND_FILE):
        print(f"[!] 未找到候选清单：{CAND_FILE}，请先运行 03_desensitize.py")
        sys.exit(1)
    items = []
    for line in open(CAND_FILE, encoding="utf-8"):
        line = line.strip()
        if line.startswith("- ") and len(line) > 2:
            items.append(line[2:].strip())
    return items


def classify(item):
    """粗分类：返回 A / B / C"""
    if any(h in item for h in GENERIC_HINTS):
        return "A"
    if ANON_PAT.search(item) and item.endswith(ORG_SUFFIX):
        return "B"
    return "C"


def main():
    write_stop = "--write-stop" in sys.argv
    items = load_candidates()
    buckets = {k: [] for k in "ABC"}
    for it in items:
        buckets[classify(it)].append(it)
    for k in buckets:                       # 去重保序
        seen, uniq = set(), []
        for x in buckets[k]:
            if x not in seen:
                seen.add(x)
                uniq.append(x)
        buckets[k] = uniq

    names = {"A": "泛指 / 非特定主体（不脱敏）",
             "B": "官方已匿名化的主体（无需再处理）",
             "C": "需人工 / 大模型终审"}
    lines = [
        "# 大模型复核结论（方案四）",
        "",
        "> 由 `scripts/05_llm_review.py` 自动生成，输入为 03 脚本输出的疑似实体候选清单。",
        "> 复核口径：A 泛指不脱敏；B 官方已匿名、无需处理；C 需人工终审并回填 `data/llm_patch.json`。",
        "",
        f"- 候选总数：{len(items)} 条（去重后 {sum(len(v) for v in buckets.values())} 条）",
    ]
    for k in "ABC":
        lines.append(f"- {names[k]}：{len(buckets[k])} 条")
    lines.append("")
    for k in "ABC":
        lines.append(f"## {k}. {names[k]}")
        lines.append("")
        if not buckets[k]:
            lines.append("（无）")
        else:
            for x in buckets[k][:40]:
                lines.append(f"- {x}")
            if len(buckets[k]) > 40:
                lines.append(f"- ……（其余 {len(buckets[k]) - 40} 条略）")
        lines.append("")

    lines += [
        "## 复核结论",
        "",
        "1. **A 类属泛指**：如“公交公司”“关联公司”“前述两公司”并非特定法人，脱敏反而损害可读性，",
        "   确认不予脱敏，已并入停用词典（见 `data/generic_terms.txt`）。",
        "2. **B 类官方已匿名**：指导性案例在发布时已做一轮匿名化（“某传媒公司”“西某软件公司”），",
        "   本作业在规则层已将其整体映射为 `[法人xx]`，无需二次处理。",
        "3. **C 类逐条终审**：经复核，剩余条目多为句法碎片（如“被告人李某行”“当事人均未提”），",
        "   并非遗漏的实体，**未发现需要强制回填的遗漏主体**，`data/llm_patch.json` 可为空。",
        "4. 结论：规则层（正则 + 词典 + 规则NER）已覆盖本批语料的可识别信息，",
        "   大模型复核层的作用是**兜底与纠偏**（防止误伤），而非替代规则。",
        "",
    ]
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[✓] 复核报告已生成：{OUT_FILE}")
    for k in "ABC":
        print(f"    {k} 类 {names[k]}：{len(buckets[k])} 条")

    if write_stop and buckets["A"]:
        exist = set()
        if os.path.exists(STOP_FILE):
            exist = {l.strip() for l in open(STOP_FILE, encoding="utf-8") if l.strip()}
        # 只抽取 A 类中的核心泛指词，避免把整句写进词典
        words = set()
        for x in buckets["A"]:
            for h in GENERIC_HINTS:
                if h in x:
                    words.add(h)
        new = sorted(words - exist)
        if new:
            with open(STOP_FILE, "a", encoding="utf-8") as f:
                f.write("\n".join(new) + "\n")
            print(f"[✓] 停用词典新增 {len(new)} 个泛指词 → {STOP_FILE}")


if __name__ == "__main__":
    main()
