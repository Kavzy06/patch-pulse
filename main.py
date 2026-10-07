"""Patch Pulse web server: uvicorn main:app --reload"""
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import engine

app = FastAPI(title="Patch Pulse")
BASE = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")

CACHE_TTL = 6 * 3600
ALLOWED_COUNTS = (5000, 10000, 20000)
COUNTRIES = {"in", "us", "gb", "ca", "au", "sg", "ae", "ng", "za", "ph"}

pool = ThreadPoolExecutor(max_workers=int(os.getenv("WORKERS", "2")))
jobs: dict = {}
cache: dict = {}
lock = threading.Lock()


class AnalyzeRequest(BaseModel):
    apps: list[str] = Field(min_length=1, max_length=2)
    country: str = "in"
    count: int = 10000


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE, "static", "index.html"))


@app.get("/api/search")
def search(q: str, country: str = "in"):
    if len(q.strip()) < 2:
        return []
    try:
        return engine.search_apps(q, country if country in COUNTRIES else "in")
    except Exception as e:
        raise HTTPException(502, f"Couldn't reach the Play Store: {e}")


def _run(job_id, req: AnalyzeRequest):
    job = jobs[job_id]
    try:
        for i, app_id in enumerate(req.apps):
            key = (app_id, req.country, req.count)
            hit = cache.get(key)
            if hit and time.time() - hit[0] < CACHE_TTL:
                job["results"][app_id] = hit[1]
                job["progress"][app_id] = req.count
                continue

            job["stage"] = f"info:{app_id}"
            info = engine.app_details(app_id, req.country)

            def prog(n, a=app_id):
                job["progress"][a] = n

            job["stage"] = f"reviews:{app_id}"
            df = engine.fetch_reviews(app_id, req.country, req.count, prog)
            job["stage"] = f"analyze:{app_id}"
            result = {"info": info, **engine.analyze(df)}
            with lock:
                cache[key] = (time.time(), result)
            job["results"][app_id] = result
        job["status"] = "done"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e) or e.__class__.__name__


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    if req.count not in ALLOWED_COUNTS:
        req.count = 10000
    if req.country not in COUNTRIES:
        req.country = "in"
    req.apps = list(dict.fromkeys(a.strip() for a in req.apps if a.strip()))
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {
        "status": "running", "stage": "starting", "apps": req.apps, "count": req.count,
        "progress": {a: 0 for a in req.apps}, "results": {}, "error": None, "created": time.time(),
    }
    pool.submit(_run, job_id, req)
    # forget jobs older than an hour
    for k in [k for k, j in jobs.items() if time.time() - j["created"] > 3600]:
        jobs.pop(k, None)
    return {"job": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "That analysis has expired. Run it again.")
    out = {k: v for k, v in job.items() if k != "results"}
    if job["status"] == "done":
        out["results"] = job["results"]
    return out


@app.get("/api/categories")
def categories():
    return list(engine.CATEGORIES)
