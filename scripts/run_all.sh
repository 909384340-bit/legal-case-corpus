#!/usr/bin/env bash
# 数据闭环一键复现：采集 -> 清洗 -> 脱敏 -> 元信息
# 用法：bash scripts/run_all.sh [采集篇数，默认 15]
set -e
cd "$(dirname "$0")/.."

PY=${PY:-python3}
LIMIT=${1:-15}

echo "==> [1/4] 采集（最高法指导性案例，上限 ${LIMIT} 篇）"
"$PY" scripts/01_fetch.py "$LIMIT"

echo "==> [2/4] 清洗（去噪、去重、结构化）"
"$PY" scripts/02_clean.py

echo "==> [3/4] 脱敏（正则 + 词典 + 规则/NER + 大模型复核）"
"$PY" scripts/03_desensitize.py

echo "==> [4/4] 元信息注入（YAML Front Matter + 索引）"
"$PY" scripts/04_frontmatter.py

echo "==> 完成：data/final/ 下为脱敏成品，index.md 为全库索引"
