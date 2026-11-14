#!/usr/bin/env bash
# 宽基ETF量价跟踪器 · data/ 目录异地备份
# 由 /etc/cron.d/etf-watch 每日 16:10 调用（需先在 crontab 里取消注释）
#
# 为什么单独备份 data/：
#   snapshot.json 里的 shares_history 只能靠每天准点抓取一点点长出来，
#   公开渠道不提供历史份额回溯。机器丢了可以重买，这份历史丢了无法重建。
#
# 前提：安装 ossutil 并配置好 AccessKey
#   curl -o ossutil https://gosspublic.alicdn.com/ossutil/1.7.19/ossutil64
#   chmod +x ossutil && mv ossutil /usr/local/bin/
#   ossutil config     # 填 endpoint / ak / sk
#
# 若不用 OSS，把 upload() 换成 rsync 到 NAS 或另一台机器同样成立。

set -uo pipefail

APP_DIR="/opt/etf-watch"
OSS_BUCKET="oss://YOUR-BUCKET/etf-watch"      # ← 改成你的 bucket 和路径
KEEP_LOCAL_DAYS=7

LOG="$APP_DIR/logs/backup-$(date +%Y%m).log"
TODAY="$(date +%Y%m%d)"
PKG="/tmp/etf-data-$TODAY.tar.gz"

mkdir -p "$APP_DIR/logs"
cd "$APP_DIR" || exit 1

tar -czf "$PKG" data/ 2>>"$LOG" || { echo "$(date '+%F %T') tar 失败" >> "$LOG"; exit 1; }

if command -v ossutil >/dev/null 2>&1; then
  ossutil cp -f "$PKG" "$OSS_BUCKET/" >>"$LOG" 2>&1 \
    && echo "$(date '+%F %T') 已上传 $OSS_BUCKET/$(basename "$PKG") ($(du -h "$PKG" | cut -f1))" >>"$LOG" \
    || echo "$(date '+%F %T') 上传失败，本地包保留在 $PKG" >>"$LOG"
else
  echo "$(date '+%F %T') 未找到 ossutil，跳过上传；本地包 $PKG" >>"$LOG"
fi

# 清理本地临时包
find /tmp -maxdepth 1 -name 'etf-data-*.tar.gz' -type f -mtime +$KEEP_LOCAL_DAYS -delete 2>/dev/null || true

exit 0
