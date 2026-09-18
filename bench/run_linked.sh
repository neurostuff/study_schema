#!/bin/bash
cd /home/zorro/repos/study_schema
until grep -q "ALL DONE" bench/run_all.log 2>/dev/null; do sleep 30; done
for cfg in luna-high luna-low; do
  echo "===== $cfg / linked ====="
  python3 bench/bench_study.py --key-file ~/.keys/portkey.key --out bench/runs \
    --samples pmc20,nmb --nmb-limit 40 --configs $cfg --mode linked --workers 8
done
echo "LINKED DONE"
