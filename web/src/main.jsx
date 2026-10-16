import React, {useEffect, useMemo, useRef, useState} from "react";
import {createRoot} from "react-dom/client";
import Hls from "hls.js";
import "./style.css";

const GATEWAY = import.meta.env.VITE_GATEWAY_URL || "http://localhost:8088";
const REC = import.meta.env.VITE_REC_URL || "http://localhost:8090";
const HLS_BASE = import.meta.env.VITE_HLS_URL || "http://localhost:8080";

const roomId = 20001;
const userId = 10001;

function App(){
  const videoRef = useRef(null);
  const hlsRef = useRef(null);
  const seqRef = useRef(1);
  const sessionRef = useRef(crypto.randomUUID());
  const previousPtsRef = useRef(null);
  const lastHeartbeatRef = useRef(Date.now());
  const timelineRef = useRef(null);
  const lastBehaviorTimeRef = useRef(0);
  const seenImpressions = useRef(new Set());

  const [timeline,setTimeline]=useState(null);
  const [content,setContent]=useState(null);
  const [interest,setInterest]=useState(null);
  const [events,setEvents]=useState([]);
  const [recommendations,setRecommendations]=useState([]);
  const [recMeta,setRecMeta]=useState(null);
  const [joined,setJoined]=useState(false);

  const hlsUrl = `${HLS_BASE}/live/${roomId}.m3u8`;

  useEffect(()=>{
    const video=videoRef.current;
    if(!video) return;
    if(Hls.isSupported()){
      const hls=new Hls({lowLatencyMode:true, liveSyncDurationCount:2});
      hls.loadSource(hlsUrl);
      hls.attachMedia(video);
      hlsRef.current=hls;
      return ()=>hls.destroy();
    } else if(video.canPlayType("application/vnd.apple.mpegurl")){
      video.src=hlsUrl;
    }
  },[]);

  useEffect(()=>{
    const id=setInterval(async()=>{
      try{
        const [t,c,i]=await Promise.allSettled([
          fetch(`${GATEWAY}/api/v1/live/${roomId}/timeline`).then(r=>r.ok?r.json():null),
          fetch(`${GATEWAY}/api/v1/debug/live/${roomId}/state`).then(r=>r.ok?r.json():null),
          fetch(`${GATEWAY}/api/v1/debug/users/${userId}/interest`).then(r=>r.ok?r.json():null)
        ]);
        if(t.status==="fulfilled"&&t.value){
          timelineRef.current=t.value;
          setTimeline(t.value);
        }
        if(c.status==="fulfilled"&&c.value)setContent(c.value);
        if(i.status==="fulfilled"&&i.value)setInterest(i.value);
      }catch{}
    },1500);
    return ()=>clearInterval(id);
  },[]);

  function currentPts(){
    const t=timelineRef.current;
    if(!t) return null;
    let latencyMs=6000;
    const hls=hlsRef.current;
    if(hls && Number.isFinite(hls.latency)) latencyMs=Math.max(0,hls.latency*1000);
    return Math.max(0,Math.round(t.latest_end_pts_ms-latencyMs));
  }

  async function sendBehavior(action, extra={}){
    const t=timelineRef.current;
    if(!t) throw new Error("直播时间线尚未就绪");
    const now=Date.now();
    lastBehaviorTimeRef.current=now;
    const body={
      user_id:userId,
      session_id:sessionRef.current,
      room_id:roomId,
      stream_session_id:t.stream_session_id,
      action,
      event_time_ms:now,
      client_seq:seqRef.current++,
      foreground:document.visibilityState==="visible",
      playback_rate:videoRef.current?.playbackRate || 1,
      playhead_pts_ms:currentPts(),
      ...extra
    };
    const r=await fetch(`${GATEWAY}/api/v1/events/behavior`,{
      method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)
    });
    if(!r.ok) throw new Error(await r.text());
    const data=await r.json();
    setEvents(x=>[{ts:new Date().toLocaleTimeString(),action,pts:body.playhead_pts_ms},...x].slice(0,12));
    return data;
  }

  async function join(){
    await videoRef.current?.play();
    await sendBehavior("ENTER_ROOM");
    lastHeartbeatRef.current=Date.now();
    previousPtsRef.current=currentPts();
    setJoined(true);
  }

  async function leave(){
    // EXIT is a lifecycle signal, not preference evidence. Freshness waits for
    // the last meaningful watch/like/follow/click event instead.
    const meaningfulBarrier=lastBehaviorTimeRef.current;
    if(joined){
      await sendBehavior("EXIT_ROOM");
      setJoined(false);
    }
    const r=await fetch(`${REC}/api/v1/recommendations`,{
      method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({
        user_id:userId,
        session_id:sessionRef.current,
        scene:"POST_LIVE",
        trigger:"EXIT_ROOM",
        context_room_id:roomId,
        min_source_event_time_ms:meaningfulBarrier || undefined,
        limit:20
      })
    });
    const data=await r.json();
    setRecommendations(data.items||[]);
    setRecMeta(data);
    window.scrollTo({top:document.body.scrollHeight,behavior:"smooth"});
  }

  useEffect(()=>{
    if(!joined) return;
    const id=setInterval(async()=>{
      const now=Date.now();
      const pts=currentPts();
      const prev=previousPtsRef.current;
      const elapsed=now-lastHeartbeatRef.current;
      lastHeartbeatRef.current=now;
      previousPtsRef.current=pts;
      const video=videoRef.current;
      const effective=(document.visibilityState==="visible" && video && !video.paused) ? elapsed : 0;
      try{
        await sendBehavior("HEARTBEAT",{
          previous_playhead_pts_ms:prev,
          effective_watch_ms:effective
        });
      }catch(e){console.warn(e)}
    },10000);
    return ()=>clearInterval(id);
  },[joined]);

  async function feedback(item,action){
    if(!recMeta) return;
    await fetch(`${GATEWAY}/api/v1/events/feedback`,{
      method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({
        impression_id:item.impression_id,
        request_id:recMeta.request_id,
        user_id:userId,item_id:item.item_id,position:item.rank,
        action,event_time_ms:Date.now(),
        experiment_id:recMeta.experiment_id,
        experiment_variant:recMeta.experiment_variant,
        rec_model_name:recMeta.rec_model_name,
        rec_model_version:recMeta.rec_model_version
      })
    });
  }

  function impressionRef(item){
    return el=>{
      if(!el || seenImpressions.current.has(item.impression_id)) return;
      const ob=new IntersectionObserver(entries=>{
        if(entries.some(x=>x.isIntersecting && x.intersectionRatio>=0.5)){
          seenImpressions.current.add(item.impression_id);
          feedback(item,"IMPRESSION").catch(console.warn);
          ob.disconnect();
        }
      },{threshold:[0.5]});
      ob.observe(el);
    };
  }

  return <div className="page">
    <header>
      <div><b>Live2Shop</b><span>Temporal multimodal live-commerce recommendation</span></div>
      <div className="status">{timeline?"LIVE":"WAITING"}</div>
    </header>

    <main className="grid">
      <section className="panel videoPanel">
        <video ref={videoRef} controls playsInline muted />
        <div className="actions">
          <button onClick={join} disabled={joined || !timeline}>进入直播</button>
          <button onClick={()=>sendBehavior("LIKE")} disabled={!joined}>♥ 点赞</button>
          <button onClick={()=>sendBehavior("FOLLOW")} disabled={!joined}>＋ 关注</button>
          <button className="exit" onClick={leave}>退出并推荐</button>
        </div>
        <small>Room {roomId} · User {userId} · canonical PTS {currentPts() ?? "-"}</small>
      </section>

      <section className="panel">
        <h2>AI Content State</h2>
        {content ? <dl>
          <DT k="SKU" v={content.active_item_id ?? "unknown"}/>
          <DT k="Category" v={content.category || "-"}/>
          <DT k="Brand" v={content.brand || "-"}/>
          <DT k="Style" v={(content.style_tags||[]).join(" / ") || "-"}/>
          <DT k="Price" v={content.price_cent?`¥${(content.price_cent/100).toFixed(2)}`:"-"}/>
          <DT k="Transition" v={content.transition}/>
          <DT k="Confidence" v={Number(content.confidence||0).toFixed(2)}/>
          <DT k="Model" v={content.model_version}/>
          <DT k="PTS" v={`${content.start_pts_ms} – ${content.end_pts_ms}`}/>
        </dl>:<Empty/>}
        <div className="evidence">
          <b>ASR</b><p>{content?.asr||"-"}</p>
          <b>OCR</b><p>{(content?.ocr||[]).join(" · ")||"-"}</p>
        </div>
      </section>

      <section className="panel">
        <h2>Realtime Interest</h2>
        {interest?.top_interests?.length ? interest.top_interests.map((x,i)=>
          <div className="barRow" key={i}>
            <span>{x.tag}</span>
            <div className="bar"><i style={{width:`${Math.min(100,Math.max(3,x.score*100))}%`}}/></div>
            <em>{Number(x.score).toFixed(2)}</em>
          </div>
        ):<Empty/>}
        <small>version {interest?.version ?? "-"} · {interest?.model_version ?? "-"}</small>
      </section>

      <section className="panel">
        <h2>Event Timeline</h2>
        <div className="timeline">
          {events.length ? events.map((e,i)=><div key={i}><b>{e.ts}</b><span>{e.action}</span><small>PTS {e.pts}</small></div>):<Empty/>}
        </div>
      </section>
    </main>

    <section className="recs">
      <div className="recHeader">
        <div><h2>Post-live Recommendations</h2>
          {recMeta&&<small>{recMeta.experiment_id}/{recMeta.experiment_variant} · interest v{recMeta.interest_version} · {recMeta.latency_ms} ms</small>}
        </div>
      </div>
      <div className="cards">
        {recommendations.map(item=><article ref={impressionRef(item)} key={item.impression_id}
          onClick={()=>feedback(item,"CLICK")} className="card">
          <div className="thumb">{item.image_uri?<img src={item.image_uri}/>:<span>{item.style_tags?.[0]||"商品"}</span>}</div>
          <h3>{item.title}</h3>
          <b>¥{(item.price_cent/100).toFixed(2)}</b>
          <p>{(item.reason_codes||[]).join(" · ")}</p>
          <small>rank #{item.rank} · score {Number(item.score).toFixed(3)}</small>
        </article>)}
      </div>
    </section>
  </div>
}

function DT({k,v}){return <div className="dt"><dt>{k}</dt><dd>{String(v??"-")}</dd></div>}
function Empty(){return <div className="empty">waiting for data…</div>}

createRoot(document.getElementById("root")).render(<App/>);
