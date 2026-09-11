#!/bin/sh
# 课表照片 OCR 兜底（macOS Vision）。用法：
#   sh scripts/ocr_macos.sh 课表.png [作息时间表.png ...]
#
# 这是豆包看不了图时的兜底手段；能直接看图就不要跑它。
# 首次运行要编译 Swift，可能需要十几秒。模块缓存写到临时目录，避免只读环境写不进去。

set -e
HERE=$(cd "$(dirname "$0")" && pwd)
CACHE_DIR="${TMPDIR:-/tmp}/schedule-to-ics-ocr-cache"
mkdir -p "$CACHE_DIR"
CLANG_MODULE_CACHE_PATH="$CACHE_DIR" \
SWIFT_MODULECACHE_PATH="$CACHE_DIR" \
exec swift "$HERE/ocr_macos.swift" "$@"
