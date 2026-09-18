#!/bin/bash
cd /home/zorro/repos/study_schema
for cfg in luna-high luna-low; do
  for mode in schema analyses_first two_pass; do
    echo "===== $cfg / $mode ====="
    python3 bench/bench_study.py --key-file ~/.keys/portkey.key --out bench/runs \
      --samples pmc20,nmb --nmb-limit 40 --configs $cfg --mode $mode --workers 8
  done
done
echo "ALL DONE"
