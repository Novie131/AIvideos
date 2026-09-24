"""AI Shorts Studio —— 本機編排器。"""
from __future__ import annotations
import asyncio, json, re, time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import backends as be
from . import imagegen as ig
from . import render as rd
from . import scriptgen as sg
from . import tts
from .jobs import QUEUE, Job

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
PROJECTS, CHARS = ROOT / "projects", ROOT / "characters"

app = FastAPI(title="AI Shorts Studio")


AUTO_UNLOAD = {"on": True}


@app.get("/api/autounload")
async def api_autounload_get():
    return AUTO_UNLOAD


class AutoUnloadReq(BaseModel):
    on: bool


@app.post("/api/autounload")
async def api_autounload(r: AutoUnloadReq):
    AUTO_UNLOAD["on"] = r.on
    return AUTO_UNLOAD


@app.on_event("startup")
async def _startup():
    QUEUE.start()


# ---------- 系統 / 模型 ----------

@app.get("/api/health")
async def api_health():
    return await be.health()


@app.get("/api/system")
async def api_system():
    return {"sys": be.system_stats(), "loaded": await be.ollama_loaded()}


@app.get("/api/suggest_shots")
async def api_suggest(duration: int = 15):
    return {"n_shots": sg.suggest_shots(duration)}


@app.get("/api/models")
async def api_models():
    try:
        return await be.ollama_models()
    except Exception as e:
        raise HTTPException(503, f"連不到 Ollama：{e}")


class UnloadReq(BaseModel):
    model: str


@app.post("/api/unload")
async def api_unload(r: UnloadReq):
    return {"ok": await be.ollama_unload(r.model)}


# ---------- 角色庫 ----------

@app.get("/api/characters")
async def api_characters():
    out = []
    for d in sorted(CHARS.glob("*/character.json")):
        try:
            data = json.loads(d.read_text(encoding="utf-8"))
        except Exception:
            continue
        ref = d.parent / "reference.png"
        out.append({"id": d.parent.name, "name": data.get("name", d.parent.name),
                    "appearance_en": data.get("appearance_en", ""),
                    "has_ref": ref.exists(), "data": data})
    return out


class CharReq(BaseModel):
    data: dict


@app.put("/api/characters/{cid}")
async def api_char_save(cid: str, r: CharReq):
    p = CHARS / cid
    p.mkdir(parents=True, exist_ok=True)
    (p / "character.json").write_text(
        json.dumps(r.data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}


# ---------- 專案 ----------

@app.get("/api/projects")
async def api_projects():
    out = []
    for d in sorted(PROJECTS.glob("*/"), key=lambda p: p.stat().st_mtime, reverse=True):
        sp = d / "script.json"
        if not sp.exists():
            continue
        try:
            s = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            continue
        shots = s.get("shots", [])
        out.append({"slug": d.name, "title": s.get("title", d.name),
                    "shots": len(shots),
                    "duration": sum(x.get("duration_sec", 0) for x in shots),
                    # 資料夾名以 pipeline-plan.md §7 與 .gitignore 為準：shots/，不是 images/
                    "images": len(list((d / "shots").glob("shot-*.png"))) if (d / "shots").exists() else 0,
                    "audios": len(list((d / "audio").glob("*.wav"))) if (d / "audio").exists() else 0,
                    "video": (d / "out" / "final.mp4").exists(),
                    "mtime": d.stat().st_mtime})
    return out


@app.get("/api/projects/{slug}")
async def api_project(slug: str):
    d = PROJECTS / slug
    sp = d / "script.json"
    if not sp.exists():
        raise HTTPException(404, "找不到這個專案")
    script = json.loads(sp.read_text(encoding="utf-8"))
    total, issues = sg.check(script)
    char_app = ""
    for c in await api_characters():
        if c["name"] == script.get("character") or c["id"] in slug:
            char_app = c["appearance_en"]
            break
    cons = sg.consistency(script, char_app) if char_app else []
    crit = (d / "critique.md")
    # 產出物清單 —— 前端用它決定顯示縮圖、播放器與各階段完成度
    media = {"shots": {}, "audio": {}, "video": None, "manifest": None}
    for sh in script.get("shots", []):
        sid = sh.get("id")
        png, wav = d / "shots" / f"shot-{sid:02d}.png", d / "audio" / f"shot-{sid:02d}.wav"
        if png.exists():
            media["shots"][sid] = f"/media/{slug}/shots/{png.name}?t={int(png.stat().st_mtime)}"
        if wav.exists():
            media["audio"][sid] = f"/media/{slug}/audio/{wav.name}"
    mp4 = d / "out" / "final.mp4"
    if mp4.exists():
        media["video"] = f"/media/{slug}/out/final.mp4?t={int(mp4.stat().st_mtime)}"
    mf = d / "shots" / "manifest.json"
    if mf.exists():
        media["manifest"] = json.loads(mf.read_text(encoding="utf-8"))

    return {"slug": slug, "script": script, "total": total, "issues": issues,
            "consistency": cons, "media": media,
            "critique": crit.read_text(encoding="utf-8") if crit.exists() else "",
            "idea": (d / "idea.txt").read_text(encoding="utf-8") if (d / "idea.txt").exists() else "",
            "endings": json.loads((d / "endings.json").read_text(encoding="utf-8"))
                       if (d / "endings.json").exists() else None}


class SaveReq(BaseModel):
    script: dict


@app.put("/api/projects/{slug}")
async def api_project_save(slug: str, r: SaveReq):
    d = PROJECTS / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "script.json").write_text(
        json.dumps(r.script, ensure_ascii=False, indent=2), encoding="utf-8")
    total, issues = sg.check(r.script)
    return {"ok": True, "total": total, "issues": issues}


# ---------- 腳本生成 ----------

class ScriptReq(BaseModel):
    idea: str
    slug: str
    character: str = "orange-cat"
    model: str = "qwen3:8b"
    think: bool = False
    n_shots: int = 0   # 0 = 依片長自動決定
    duration: int = 15
    temperature: float = 0.8
    script_type: str = "story"      # story | performance
    form: str = "quadruped"         # quadruped | anthro，見 character.json 的 forms
    music: str = ""                 # 才藝軌的配樂註記；有值就回傳版權提醒


def _slugify(s: str) -> str:
    s = re.sub(r"[^\w一-鿿-]+", "-", s).strip("-")
    return s or f"ep{int(time.time())}"


@app.post("/api/script")
async def api_script(r: ScriptReq):
    """薄殼：編排在 scriptgen.generate_script()，Web 與 CLI 共用同一份。"""
    slug = _slugify(r.slug)

    async def run(job: Job):
        return await sg.generate_script(
            r.idea, slug, character=r.character, model=r.model, think=r.think,
            n_shots=r.n_shots, duration=r.duration, temperature=r.temperature,
            script_type=r.script_type, form=r.form, music=r.music,
            # 佇列還有排隊時不釋放，避免連續產稿反覆載入
            release_llm=AUTO_UNLOAD["on"] and not QUEUE.q.qsize(),
            on_step=lambda t, pct=None: QUEUE.step(job, t, pct))

    job = QUEUE.submit("script", f"腳本：{r.idea}", run)
    return {"job": job.id, "slug": slug}


# ---------- 出圖 / 配音 / 合成 ----------

class SlugReq(BaseModel):
    slug: str
    force: bool = False


class ImagesReq(SlugReq):
    preset: str = "style-v1"
    only: list[int] | None = None


@app.post("/api/images")
async def api_images(r: ImagesReq):
    async def run(job: Job):
        return await ig.render_shots(
            r.slug, preset_name=r.preset, force=r.force, only=r.only,
            on_step=lambda t, pct=None: QUEUE.step(job, t, pct))
    job = QUEUE.submit("images", f"出圖：{r.slug}", run)
    return {"job": job.id}


class TTSReq(SlugReq):
    voice: str | None = None


@app.get("/api/voices")
async def api_voices():
    return tts.voices()


@app.post("/api/tts")
async def api_tts(r: TTSReq):
    async def run(job: Job):
        return await tts.synth_shots(
            r.slug, voice=r.voice or tts.VOICE, force=r.force,
            on_step=lambda t, pct=None: QUEUE.step(job, t, pct))
    job = QUEUE.submit("tts", f"配音：{r.slug}", run)
    return {"job": job.id}


class RenderReq(SlugReq):
    bgm: str | None = None


@app.post("/api/render")
async def api_render(r: RenderReq):
    async def run(job: Job):
        return await rd.render(
            r.slug, bgm=Path(r.bgm) if r.bgm else None, force=r.force,
            on_step=lambda t, pct=None: QUEUE.step(job, t, pct))
    job = QUEUE.submit("render", f"合成：{r.slug}", run)
    return {"job": job.id}


# ---------- 結尾 A/B/C ----------

class EndingReq(BaseModel):
    slug: str
    model: str = "qwen3:8b"


@app.post("/api/endings")
async def api_endings(r: EndingReq):
    d = PROJECTS / r.slug
    sp = d / "script.json"
    if not sp.exists():
        raise HTTPException(404, "找不到這個專案")

    async def run(job: Job):
        QUEUE.step(job, "生成 3 個結尾方向…", 20)
        script = json.loads(sp.read_text(encoding="utf-8"))
        data = sg.parse_json(await be.ollama_chat(r.model, sg.ENDINGS.format(
            script=json.dumps(script, ensure_ascii=False, indent=2)), json_mode=True))
        for e in data.get("endings", []):
            sg.fix_simplified({"shots": [e.get("shot", {})]})
        (d / "endings.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        QUEUE.step(job, f"完成，{len(data.get('endings', []))} 個版本", 100)
        await _release_llm(r.model)
        return data

    job = QUEUE.submit("endings", f"結尾方案：{r.slug}", run)
    return {"job": job.id}


class ApplyEndingReq(BaseModel):
    slug: str
    label: str


@app.post("/api/apply_ending")
async def api_apply_ending(r: ApplyEndingReq):
    d = PROJECTS / r.slug
    script = json.loads((d / "script.json").read_text(encoding="utf-8"))
    data = json.loads((d / "endings.json").read_text(encoding="utf-8"))
    pick = next((e for e in data["endings"] if e["label"] == r.label), None)
    if not pick:
        raise HTTPException(404, "找不到這個結尾版本")
    shots = script["shots"]
    new = dict(pick["shot"])
    new["id"] = shots[-1]["id"]
    shots[-1] = new
    sg.assign_cameras(shots)
    sg.fix_simplified(script)
    (d / "script.json").write_text(
        json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    total, issues = sg.check(script)
    return {"ok": True, "total": total, "issues": issues, "script": script}


# ---------- 佇列 / SSE ----------

@app.get("/api/jobs")
async def api_jobs():
    return {"current": QUEUE.current.dict() if QUEUE.current else None,
            "jobs": [j.dict() for j in list(QUEUE.jobs.values())[-25:]]}


@app.post("/api/jobs/{jid}/cancel")
async def api_cancel(jid: str):
    return {"ok": QUEUE.cancel(jid)}


@app.get("/api/events")
async def api_events():
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    QUEUE.subs.add(q)

    async def gen():
        try:
            yield f"data: {json.dumps({'type': 'hello'})}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=2.0)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    sysinfo = {"type": "sys", "sys": be.system_stats(),
                               "loaded": await be.ollama_loaded()}
                    yield f"data: {json.dumps(sysinfo, ensure_ascii=False)}\n\n"
        finally:
            QUEUE.subs.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ---------- 靜態檔 ----------

@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
# 產出物（shots/*.png、out/final.mp4）要能被 <img> 與 <video> 讀到
app.mount("/media", StaticFiles(directory=PROJECTS), name="media")
