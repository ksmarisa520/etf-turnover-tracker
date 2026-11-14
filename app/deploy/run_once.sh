#!/usr/bin/env bash
# 宽基ETF量价跟踪器 · 单次抓数包装脚本
# 被 /etc/cron.d/etf-watch 调用，也可手动执行：/opt/etf-watch/deploy/run_once.sh manual
#
# 它做四件事：切目录、跑脚本、判成败、留日志。
# 判断成败不只看退出码——update_data.py 在接口全挂时也可能返回 0，
# 所以额外比对 snapshot.json 里的 generated_at 有没有真的变新。

set -uo pipefail

APP_DIR="/opt/etf-watch"
TAG="${1:-manual}"
LOG_DIR="$APP_DIR/logs"
LOG="$LOG_DIR/cron-$(date +%Y%m).log"

# 可选告警：填一个能收 POST 的 webhook（企业微信机器人 / 钉钉 / Server酱 均可）
WEBHOOK_URL=""

mkdir -p "$LOG_DIR"
cd "$APP_DIR" || { echo "无法进入 $APP_DIR" >&2; exit 1; }

stamp() { date '+%F %T'; }

gen_at() {
  [ -f data/snapshot.json ] || { echo ""; return; }
  python3 -c "import json;print(json.load(open('data/snapshot.json')).get('generated_at',''))" 2>/dev/null || echo ""
}

notify() {
  local msg="$1"
  echo "[$TAG] $msg" >> "$LOG"
  if [ -n "$WEBHOOK_URL" ]; then
    curl -s -m 10 -H 'Content-Type: application/json' \
      -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"ETF跟踪器告警：$msg\"}}" \
      "$WEBHOOK_URL" >/dev/null 2>&1 || true
  fi
}

before="$(gen_at)"
echo "----- $(stamp) [$TAG] 开始 -----" >> "$LOG"

python3 update_data.py >> "$LOG" 2>&1
rc=$?

after="$(gen_at)"

if [ "$rc" -ne 0 ]; then
  notify "脚本退出码 $rc，抓数失败，请查 $LOG"
  echo "----- $(stamp) [$TAG] 失败(rc=$rc) -----" >> "$LOG"
elif [ -z "$after" ] || [ "$before" = "$after" ]; then
  notify "脚本跑完但快照未更新，可能接口异常或无完整交易日数据"
  echo "----- $(stamp) [$TAG] 未更新 -----" >> "$LOG"
else
  echo "----- $(stamp) [$TAG] 完成，快照时间 $after -----" >> "$LOG"
fi

# 日志保留 90 天
find "$LOG_DIR" -name 'cron-*.log' -type f -mtime +90 -delete 2>/dev/null || true

exit 0
