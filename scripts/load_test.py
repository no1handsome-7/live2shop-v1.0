"""
Small local load test for the fast recommendation path.

Requires a populated user interest and item index. It measures request latency
without pretending one workstation is a production capacity benchmark.
"""
import argparse,asyncio,json,statistics,time
import aiohttp

ap=argparse.ArgumentParser()
ap.add_argument("--url",default="http://localhost:8090/api/v1/recommendations")
ap.add_argument("--user-id",type=int,default=10001)
ap.add_argument("--requests",type=int,default=500)
ap.add_argument("--concurrency",type=int,default=20)
a=ap.parse_args()

lat=[];errors=0
sem=asyncio.Semaphore(a.concurrency)

async def one(session,i):
  global errors
  payload={"user_id":a.user_id,"scene":"POST_LIVE","trigger":"MANUAL_REFRESH","limit":20}
  async with sem:
    t=time.perf_counter()
    try:
      async with session.post(a.url,json=payload) as r:
        await r.read()
        if r.status>=400:errors+=1
    except:errors+=1
    lat.append((time.perf_counter()-t)*1000)

async def main():
  start=time.perf_counter()
  timeout=aiohttp.ClientTimeout(total=5)
  async with aiohttp.ClientSession(timeout=timeout) as s:
    await asyncio.gather(*(one(s,i) for i in range(a.requests)))
  total=time.perf_counter()-start
  xs=sorted(lat)
  def p(q):return xs[min(len(xs)-1,int(q*len(xs)))]
  print(json.dumps({
    "requests":a.requests,"concurrency":a.concurrency,"errors":errors,
    "qps":round(a.requests/total,2),
    "p50_ms":round(p(.50),2),"p95_ms":round(p(.95),2),"p99_ms":round(p(.99),2)
  },indent=2))
asyncio.run(main())
