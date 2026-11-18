#!/usr/bin/env bash
# Linux：在終端機執行 ./啟動翻譯.sh
# 第一次會自動安裝套件、下載模型（約 3GB，需要網路），之後離線可用。
# Ubuntu 需要先裝：sudo apt install python3-tk python3-venv
cd "$(dirname "$0")"
PY=""
for p in python3 python3.13 python3.12 python3.11 python3.10; do
  if command -v "$p" >/dev/null && "$p" -c "import tkinter, venv" 2>/dev/null; then PY="$p"; break; fi
done
if [ -z "$PY" ]; then
  echo "找不到含 tkinter 的 Python 3。Ubuntu 請執行：sudo apt install python3-tk python3-venv"
  exit 1
fi
"$PY" install.py || exit 1
exec .venv/bin/python translator.py
