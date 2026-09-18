#!/bin/bash
cd /home/zorro/repos/study_schema
until grep -q "LOW MATRIX DONE" bench/run_low.log 2>/dev/null; do sleep 30; done
# Isolate reasoning effort against the current winner: identical to linked_noev, pass 1 hot.
echo "===== linked_noev, pass1 high (isolated effort test) ====="
python3 bench/bench_study.py --key-file ~/.keys/portkey.key --out bench/runs2 \
  --samples pmc20,nmb --nmb-limit 40 --configs luna-low --mode linked --no-evidence \
  --effort-pass1 high --effort-pass2 low --max-out 48000 --abort-after 5 --workers 8
echo "HOT DONE"
