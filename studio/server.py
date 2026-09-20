"""AI Shorts Studio —— 本機編排器。"""
from __future__ import annotations
import asyncio, json, re, time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import backends as be
from . import scriptgen as sg
from .jobs import QUEUE, Job

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
PROJECTS, CHARS = ROOT / "projects", ROOT / "characters"

app = FastAPI(title="AI Shorts Studio")


AUTO_UNLOAD = {"on": True}


async def _release_llm(model: str):
    """LLM 任務跑完且佇列已空 → 釋放記憶體給下一階段（圖片模型）。
    佇列還有排隊時不釋放，避免連續產稿時反覆載入。"""
    if not AUTO_UNLOAD["on"] or QUEUE.q.qsize():
        return
    before = be.system_stats()["available_gb"]
    await be.ollama_unload(model)
    await asyncio.sleep(2)
    after = be.system_stats()["available_gb"]
    QUEUE.broadcast({"type": "notice",
                     "text": f"已自動釋放 {model}，可用記憶體 {before} → {after} GB"})


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
                    "images": len(list((d / "images").glob("*.png"))) if (d / "images").exists() else 0,
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
    return {"slug": slug, "script": script, "total": total, "issues": issues,
            "consistency": cons,
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


def _slugify(s: str) -> str:
    s = re.sub(r"[^\w一-鿿-]+", "-", s).strip("-")
    return s or f"ep{int(time.time())}"


@app.post("/api/script")
async def api_script(r: ScriptReq):
    slug = _slugify(r.slug)
    cpath = CHARS / r.character / "character.json"
    char = cpath.read_text(encoding="utf-8") if cpath.exists() else "（未指定）"
    char_app = ""
    if cpath.exists():
        char_app = json.loads(cpath.read_text(encoding="utf-8")).get("appearance_en", "")
    n_shots = r.n_shots or sg.suggest_shots(r.duration)
    max_chars = int(r.duration / n_shots * sg.CPS)

    async def run(job: Job):
        out = PROJECTS / slug
        out.mkdir(parents=True, exist_ok=True)
        (out / "idea.txt").write_text(r.idea, encoding="utf-8")

        async def ask(p, **kw):
            return await be.ollama_chat(r.model, p, think=r.think,
                                        temperature=r.temperature, **kw)

        QUEUE.step(job, "Writer 寫初稿…", 5)
        draft = sg.parse_json(await ask(sg.WRITER.format(
            character=char, idea=r.idea, n_shots=n_shots,
            duration=r.duration, max_chars=max_chars,
            structure=sg.beats(r.duration)), json_mode=True))
        sg.assign_cameras(draft.get("shots", []))
        sg.fix_simplified(draft)
        (out / "script.draft.json").write_text(
            json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        QUEUE.step(job, "初稿完成", 30)

        _, issues = sg.check(draft)
        hard = "\n".join(f"- {i}" for i in issues) or "-（程式檢查無誤）"

        QUEUE.step(job, "Critic 審稿…", 35)
        crit = sg.strip_think(await ask(sg.CRITIC.format(
            script=json.dumps(draft, ensure_ascii=False, indent=2))))
        crit = f"{crit}\n\n【程式硬檢查】\n{hard}"
        (out / "critique.md").write_text(crit, encoding="utf-8")
        QUEUE.step(job, "審稿完成", 60)

        QUEUE.step(job, "Rewrite 修稿…", 65)
        final = sg.parse_json(await ask(sg.REWRITE.format(
            script=json.dumps(draft, ensure_ascii=False, indent=2),
            critique=crit), json_mode=True))
        sg.assign_cameras(final.get("shots", []))
        nz = sg.fix_simplified(final)
        QUEUE.step(job, "修稿完成，鏡頭已自動指派"
                   + (f"，修正 {nz} 個簡體字" if nz else ""), 85)

        for i in range(1, 4):
            _, issues = sg.check(final)
            if not issues:
                break
            QUEUE.step(job, f"硬檢查未過，自動修復第 {i} 輪（{len(issues)} 項）", 85 + i * 4)
            final = sg.parse_json(await ask(sg.REPAIR.format(
                script=json.dumps(final, ensure_ascii=False, indent=2),
                issues="\n".join(f"- {x}" for x in issues)), json_mode=True))
            sg.assign_cameras(final.get("shots", []))
            sg.fix_simplified(final)

        (out / "script.json").write_text(
            json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
        total, issues = sg.check(final)
        cons = sg.consistency(final, char_app) if char_app else []
        bad = [c["id"] for c in cons if not c["ok"]]
        QUEUE.step(job, f"完成：{total} 秒 / {len(final.get('shots', []))} shots"
                        + (f"，殘留 {len(issues)} 項問題" if issues else "，硬檢查全過")
                        + (f"；角色描述缺漏 shot {bad}" if bad else ""), 100)
        await _release_llm(r.model)
        return {"slug": slug, "total": total, "issues": issues, "consistency": cons}

    job = QUEUE.submit("script", f"腳本：{r.idea}", run)
    return {"job": job.id, "slug": slug}


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
