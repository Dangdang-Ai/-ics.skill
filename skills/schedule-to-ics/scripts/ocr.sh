#!/bin/sh
# 课表照片 OCR 兜底（macOS Vision）。用法：
#   scripts/ocr.sh 课表.png [作息时间表.png ...]
#
# 识别需要访问系统 Vision 服务，沙箱内会失败，需要用 require_escalated 重试。
# 首次运行要编译 Swift，可能需要十几秒；模块缓存写到临时目录，避免沙箱下不可写。

set -e
HERE=$(cd "$(dirname "$0")" && pwd)
CACHE_DIR="${TMPDIR:-/tmp}/schedule-to-ics-ocr-cache"
mkdir -p "$CACHE_DIR"
CLANG_MODULE_CACHE_PATH="$CACHE_DIR" \
SWIFT_MODULECACHE_PATH="$CACHE_DIR" \
exec swift "$HERE/ocr_image.swift" "$@"
