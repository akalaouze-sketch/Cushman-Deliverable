#!/bin/bash
# Install / remove a self-terminating morning cron job for the DFW CRE news aggregator.
# It runs every morning at 7:33am for ~7 days; the aggregator removes the cron entry itself
# once the window is up (see _self_terminate_if_done in scripts/dfw_cre_news.py).
#
#   ./scripts/news_schedule.sh install     # start the 7-day morning run
#   ./scripts/news_schedule.sh uninstall   # stop early
#   ./scripts/news_schedule.sh status
set -e
DIR="/Users/a.kalalouze/cushman-dashboard"
PY="$DIR/.venv/bin/python"
TAG="# dfw_cre_news"
LINE="33 7 * * * cd $DIR && $PY scripts/dfw_cre_news.py >> $DIR/data/news/run.log 2>&1 $TAG"

case "${1:-}" in
  install)
    mkdir -p "$DIR/data/news"
    date -v+7d +%Y-%m-%d > "$DIR/data/news/until.txt" 2>/dev/null \
      || date -d "+7 days" +%Y-%m-%d > "$DIR/data/news/until.txt"
    ( crontab -l 2>/dev/null | grep -v 'dfw_cre_news' || true; echo "$LINE" ) | crontab -
    echo "installed — runs 7:33am daily, stops on $(cat "$DIR/data/news/until.txt")"
    ;;
  uninstall)
    ( crontab -l 2>/dev/null | grep -v 'dfw_cre_news' || true ) | crontab -
    rm -f "$DIR/data/news/until.txt"
    echo "uninstalled — morning schedule removed"
    ;;
  status)
    echo "crontab entry:"; crontab -l 2>/dev/null | grep dfw_cre_news || echo "  (none)"
    [ -f "$DIR/data/news/until.txt" ] && echo "stops on: $(cat "$DIR/data/news/until.txt")"
    ;;
  *) echo "usage: $0 install|uninstall|status" ;;
esac
