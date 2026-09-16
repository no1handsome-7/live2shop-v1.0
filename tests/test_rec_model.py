import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"ml"/"rec_model"))

import torch
from live2shop_rec import SessionRecConfig,SessionRecModel

def test_session_model_shapes():
    cfg=SessionRecConfig()
    m=SessionRecModel(cfg)
    b,l=2,16
    content=torch.randn(b,l,128)
    action=torch.ones(b,l,dtype=torch.long)
    tb=torch.zeros(b,l,dtype=torch.long)
    db=torch.zeros(b,l,dtype=torch.long)
    mask=torch.ones(b,l,dtype=torch.bool)
    u=m.encode_user(content,action,tb,db,mask)
    assert u.shape==(b,128)
    assert torch.allclose(u.norm(dim=-1),torch.ones(b),atol=1e-4)
    cand=torch.randn(b,5,128)
    score=m.rank(u,cand)
    assert score.shape==(b,5)
