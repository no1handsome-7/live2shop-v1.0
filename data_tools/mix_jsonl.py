import argparse,random
from common import read_jsonl,write_jsonl
ap=argparse.ArgumentParser()
ap.add_argument("--input",action="append",required=True,help="path[:repeat], e.g. muge.jsonl:2")
ap.add_argument("--output",required=True)
ap.add_argument("--seed",type=int,default=42)
a=ap.parse_args()
rows=[]
for spec in a.input:
    path,sep,rep=spec.rpartition(":")
    if sep and rep.isdigit(): real=path;n=int(rep)
    else: real=spec;n=1
    x=list(read_jsonl(real))
    rows.extend(x*n)
random.Random(a.seed).shuffle(rows)
write_jsonl(a.output,rows)
print("mixed",len(rows))
