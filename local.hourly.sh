#!/bin/zsh
# Hourly runs without launchd (e.g. while the repo is still in ~/Documents, which launchd jobs can't read):
#   caffeinate -i ./local.hourly.sh &      (from the repo; logs to logs/run.log)
# Extra arguments go to every run, e.g. `./local.hourly.sh --delay 1.0` while a big backfill is queued.
# Each run starts an hour after the previous one started. Search results are crawled by the first runs after 05:00
# and 15:00 (just after SUUMO's two daily updates); the other runs work the listing-page queue until :50.
cd "${0:A:h}"
mkdir -p logs
next=$(date +%s)
while true; do
  now=$(date +%s); (( now < next )) && sleep $(( next - now ))
  next=$(( $(date +%s) + 3600 ))
  echo "=== $(date '+%F %T') ===" >> logs/run.log
  caffeinate -i uv run python -u -m suumo "$@" run --crawl-at 5,15 --budget 3h --until-minute 50 --push >> logs/run.log 2>&1
done
