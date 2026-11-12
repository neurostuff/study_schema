#!/bin/bash
cd /home/zorro/repos/study_schema
# luna-low across the four shapes, with and without the evidence payload.
# Evidence-inline only for the single-call shapes (that is the "evidence in schema" question);
# the split shapes are the ones that would hand evidence to a later pass.
run () { echo "===== $* ====="; python3 bench/bench_study.py --key-file ~/.keys/portkey.key \
   --out bench/runs2 --samples pmc20,nmb --nmb-limit 40 --configs luna-low --workers 8 \
   --max-out 48000 --abort-after 5 "$@"; }
run --mode schema
run --mode analyses_first
run --mode linked
run --mode linked --no-evidence
run --mode two_pass --no-evidence
echo "LOW MATRIX DONE"
