"""How much space coordinate parses take as JSON and as Parquet, and whether Parquet is exact.

    pip install -e ".[parquet]"
    python tools/benchmark_parquet.py <nimads-studyset.json> [n_parses]

Builds synthetic parses -- tables of 4 to 30 points, t values, labels, cluster sizes -- whose
coordinates are drawn from a real NIMADS studyset, such as NiMARE's
`nimare/resources/neurosynth_laird_studyset.json`, then writes them every way and reads the
Parquet back. The numbers in the README came from that file and 5,000 parses.
"""

import hashlib
import json
import os
import random
import sys
import tempfile
import time

import pyarrow as pa
from study_schema.models import paper_parse as pp
from study_schema import parquet as P
from study_schema.keys import table_key
random.seed(0)
ss = json.load(open(sys.argv[1]))
coords = [p["coordinates"] for s in ss["studies"] for a in s["analyses"] for p in a["points"]]
regions = ["Insula","Ventral striatum","Amygdala","Precuneus","Posterior cingulate","Angular gyrus","Middle frontal gyrus","Superior temporal gyrus","Cerebellum","Thalamus","Inferior parietal lobule","Fusiform gyrus","Anterior cingulate cortex","Caudate","Putamen"]
def parse(i):
    sha = hashlib.sha256(str(i).encode()).hexdigest()
    analyses=[]; row=0
    for t in range(random.randint(1,3)):
        tid=f"tbl{t+1}"
        for a in range(random.randint(1,3)):
            n=random.randint(4,30); cells=[]; pts=[]
            for r in range(n):
                x,y,z=random.choice(coords); tv=round(random.uniform(3,9)*random.choice([1,1,1,-1]),2)
                pts.append(pp.ParsedPoint(coordinates=[round(x),round(y),round(z)], space="MNI", row=row, column_group=0,
                    values=[pp.PointValue(kind="t", value=tv, level="peak")], sign="positive" if tv>0 else "negative",
                    cluster_size=random.choice([None, random.randint(10,900)]), cluster_measure="voxels", is_subpeak=random.random()<0.2,
                    label=random.choice(regions), text_span=pp.TextSpan(start_char=1000+row*40, end_char=1030+row*40)))
                cells.append((row,0)); row+=1
            analyses.append(pp.ParsedAnalysis(key=table_key(tid,cells,f"Condition {a} > Baseline"), cells=[pp.CellRef(row=r,column_group=c) for r,c in cells],
                origin="table", table_id=tid, name=f"Condition {a} > Baseline", name_is_printed=True, coordinate_space="MNI",
                role="result", statistic="t", thresholds=[pp.Threshold(level="height", quantity="p", value=0.001, correction="uncorrected")], points=pts))
    hdr = pp.ArtifactHeader(artifact_kind="coordinate_parse", schema_version=pp.version, article_id=f"art-{i:06d}",
        identifiers=pp.ArticleIdentifiers(pmid=str(10_000_000+i)), producer=pp.Producer(name="ns-pond-ingestion-workflow", version="1.0", stage="parse"), created_at="2026-10-09T12:00:00Z")
    return pp.CoordinateParse(header=hdr, parse_id=sha, text_sha256=sha, analyses=analyses,
        tables=[pp.TableReading(table_id=f"tbl{t+1}", reading="coordinates") for t in range(3)])
N = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
parses = [parse(i) for i in range(N)]
npts = sum(len(a.points) for p in parses for a in p.analyses); nan = sum(len(p.analyses) for p in parses)
pretty = sum(len(p.model_dump_json(indent=2)) for p in parses)
mini = b"\n".join(p.model_dump_json().encode() for p in parses)
mini_nonull = b"\n".join(p.model_dump_json(exclude_none=True).encode() for p in parses)
zst = len(pa.compress(mini, codec="zstd", asbytes=True)); zst_nn = len(pa.compress(mini_nonull, codec="zstd", asbytes=True))
d = tempfile.mkdtemp(); t0=time.time(); paths = P.write(parses, d); tw=time.time()-t0
t0=time.time(); back = P.read(d); tr=time.time()-t0
pq_total = sum(os.path.getsize(p) for p in paths.values())
print(f"{N} parses, {nan} analyses, {npts} points; round trip equal: {back == parses}")
for label, b in [("JSON, indented (what the layouts write)", pretty), ("JSON Lines, minified", len(mini)), ("JSON Lines, no nulls", len(mini_nonull)), ("JSON Lines minified + zstd", zst), ("JSON Lines no nulls + zstd", zst_nn), ("Parquet (zstd), 3 tables", pq_total)]:
    print(f"  {label:42s} {b/1e6:8.2f} MB  {b/npts:7.1f} B/point  {pretty/b:5.1f}x")
for n,p in paths.items(): print(f"    {n:9s} {os.path.getsize(p)/1e6:6.2f} MB")
print(f"  write {tw:.1f}s, read {tr:.1f}s")
import pyarrow.parquet as pq
t0=time.time(); pts = pq.read_table(paths["points"], columns=["article_id","analysis_key","x","y","z"]); print(f"  NiMARE-shaped read (5 columns of points): {time.time()-t0:.3f}s, {pts.num_rows} rows")
