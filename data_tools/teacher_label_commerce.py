"""
Weak-label commerce windows using Qwen3-VL-2B-Instruct.

Teacher:
  https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct

Run multiple shards in parallel on separate GPUs:
  CUDA_VISIBLE_DEVICES=0 python ... --shard-id 0 --num-shards 3
  CUDA_VISIBLE_DEVICES=1 python ... --shard-id 1 --num-shards 3
  CUDA_VISIBLE_DEVICES=2 python ... --shard-id 2 --num-shards 3

The teacher labels are NOT ground truth. Run audit_commerce_labels.py and manually
review a gold validation set before using them for SFT/RL evaluation.
"""
from __future__ import annotations
import argparse,json,re
from pathlib import Path
import cv2,numpy as np,torch
from PIL import Image
from transformers import AutoProcessor, AutoModelForImageTextToText
from common import read_jsonl,write_jsonl

SCHEMA={
 "product_visible":True,"active_item_id":None,"category":"","brand":"",
 "style_tags":[],"color":"","attributes":{},"price_cent":None,
 "promotion_tags":[],"evidence":{"asr":[],"ocr":[],"visual":[]},"confidence":0.0
}

def frames(path,n=8):
    cap=cv2.VideoCapture(path);count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out=[]
    for idx in np.linspace(0,max(count-1,0),n).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES,int(idx));ok,f=cap.read()
        if ok:out.append(Image.fromarray(cv2.cvtColor(f,cv2.COLOR_BGR2RGB)))
    cap.release()
    while out and len(out)<n:out.append(out[-1].copy())
    return out

def loose_json(s):
    try:return json.loads(s)
    except:
        a,b=s.find("{"),s.rfind("}")
        return json.loads(s[a:b+1]) if a>=0 and b>a else None

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input",required=True)
    ap.add_argument("--output",required=True)
    ap.add_argument("--model",default="Qwen/Qwen3-VL-2B-Instruct")
    ap.add_argument("--shard-id",type=int,default=0)
    ap.add_argument("--num-shards",type=int,default=1)
    ap.add_argument("--max-new-tokens",type=int,default=384)
    a=ap.parse_args()

    processor=AutoProcessor.from_pretrained(a.model)
    model=AutoModelForImageTextToText.from_pretrained(
        a.model,torch_dtype=torch.bfloat16,device_map="auto",attn_implementation="sdpa"
    ).eval()
    rows=list(read_jsonl(a.input))[a.shard_id::a.num_shards];out=[]
    for idx,row in enumerate(rows):
        ims=frames(row["media"],8)
        if not ims:continue
        cats=row.get("room_candidates") or []
        prompt=(
          "你是直播电商数据标注器。根据连续8帧、ASR、OCR及直播间候选商品，"
          "标注这个5秒窗口的商品状态。不得把不确定信息当事实；SKU无法可靠匹配时必须为null。"
          "价格统一为人民币分。evidence字段只引用输入里真实出现的信息。只输出JSON。\n"
          f"ASR={row.get('asr','')}\nOCR={row.get('ocr',[])}\n"
          f"候选商品={json.dumps(cats,ensure_ascii=False)}\n"
          f"目标schema={json.dumps(SCHEMA,ensure_ascii=False)}"
        )
        content=[{"type":"image"} for _ in ims]+[{"type":"text","text":prompt}]
        messages=[{"role":"user","content":content}]
        text=processor.apply_chat_template(messages,tokenize=False,add_generation_prompt=True)
        inputs=processor(text=[text],images=ims,padding=True,return_tensors="pt").to(model.device)
        with torch.inference_mode():
            ids=model.generate(**inputs,max_new_tokens=a.max_new_tokens,do_sample=False)
        generated=ids[:,inputs["input_ids"].shape[1]:]
        answer=processor.batch_decode(generated,skip_special_tokens=True)[0]
        obj=loose_json(answer)
        if obj is None:
            obj={"_parse_error":answer}
        out.append({
          "media":row["media"],"media_type":"video",
          "prompt":(
             "根据直播视频窗口、ASR、OCR和候选商品池输出当前CommerceEvent JSON；"
             "未知字段不要猜测。\\n"
             f"ASR={row.get('asr','')}\\n"
             f"OCR={row.get('ocr',[])}\\n"
             f"候选商品={json.dumps(cats,ensure_ascii=False)}"
          ),
          "target":json.dumps(obj,ensure_ascii=False,sort_keys=True),
          "target_json":obj,
          "retrieval_text":json.dumps(obj,ensure_ascii=False,sort_keys=True),
          "context":{"asr":row.get("asr",""),"ocr":row.get("ocr",[]),
                     "room_candidates":cats,"start_ms":row.get("start_ms"),
                     "end_ms":row.get("end_ms"),"room_id":row.get("room_id")},
          "meta":{"source":"teacher","teacher_model":a.model}
        })
        if (idx+1)%20==0:print("labeled",idx+1)
    write_jsonl(a.output,out)
    print("teacher rows:",len(out))

if __name__=="__main__":main()
