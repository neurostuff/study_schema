#!/bin/bash
cd /home/zorro/repos/study_schema
until grep -q "HOT DONE" bench/run_hot.log 2>/dev/null; do sleep 30; done
common="--key-file $HOME/.keys/portkey.key --out bench/runs2 --samples pmc20,nmb --nmb-limit 40 \
 --configs luna-low --mode linked --no-evidence --max-out 48000 --abort-after 5 --workers 8"
echo "===== p1=low p2=high ====="
python3 bench/bench_study.py $common --effort-pass1 low  --effort-pass2 high
echo "===== p1=high p2=high ====="
python3 bench/bench_study.py $common --effort-pass1 high --effort-pass2 high
echo "EFFORT MATRIX DONE"
