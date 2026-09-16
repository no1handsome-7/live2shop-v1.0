from __future__ import annotations
import argparse
import numpy as np
import torch
from torch.utils.data import DataLoader
from sklearn.metrics import roc_auc_score

from live2shop_rec import SessionRecModel
from live2shop_rec.data import NPZSessionDataset

ap=argparse.ArgumentParser();ap.add_argument("--checkpoint",required=True);ap.add_argument("--data",required=True);ap.add_argument("--batch-size",type=int,default=256);a=ap.parse_args()
device=torch.device("cuda" if torch.cuda.is_available() else "cpu")
m=SessionRecModel.from_checkpoint(a.checkpoint).to(device).eval()
dl=DataLoader(NPZSessionDataset(a.data),batch_size=a.batch_size)
rankings=[];ys=[];ps=[]
with torch.inference_mode():
    for b in dl:
        b={k:v.to(device) for k,v in b.items()}
        u=m.encode_user(b["content"],b["action"],b["time_bucket"],b["duration_bucket"],b["mask"])
        logits=m.retrieval_logits(u,b["positive"],b["negatives"])
        order=torch.argsort(logits,dim=1,descending=True)
        rank=(order==0).nonzero()[:,1]+1
        rankings.extend(rank.cpu().tolist())
        if "click_label" in b:
            cand=b.get("candidate",b["positive"])[:,None,:]
            bf=b.get("business");bf=bf[:,None,:] if bf is not None else None
            p=torch.sigmoid(m.rank(u,cand,bf).squeeze(1))
            ys.extend(b["click_label"].cpu().tolist());ps.extend(p.cpu().tolist())
r=np.asarray(rankings)
for k in (10,20,40):
    recall=(r<=k).mean()
    ndcg=np.where(r<=k,1/np.log2(r+1),0).mean()
    print(f"Recall@{k}: {recall:.6f} NDCG@{k}: {ndcg:.6f}")
print("MRR:",np.mean(1/r))
if len(set(ys))>1:print("AUC:",roc_auc_score(ys,ps))
