"""本機後端：Ollama / Draw Things / ffmpeg / 系統資源。"""
from __future__ import annotations
import asyncio, json, os, shutil
import httpx, psutil

# 後端位址不寫死 —— ADR-002：Draw Things 關在 imagegen.py 後面，換機器只換設定。
# 指向另一台機器時設環境變數即可，例如：
#   AIV_DRAWTHINGS_URL=http://192.168.1.50:7860 ./run.sh
# 注意：Draw Things 只跑 Apple 平台。若外部機器是 Linux + NVIDIA，那是換後端
# （要另寫一個 adapter），不是換網址就好。
OLLAMA = os.getenv("AIV_OLLAMA_URL", "http://127.0.0.1:11434")
DRAWTHINGS = os.getenv("AIV_DRAWTHINGS_URL", "http://127.0.0.1:7860")


async def ollama_models() -> list[dict]:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{OLLAMA}/api/tags")
        r.raise_for_status()
        return [
            {"name": m["name"], "size_gb": round(m.get("size", 0) / 1e9, 1),
             "family": m.get("details", {}).get("family", ""),
             "params": m.get("details", {}).get("parameter_size", "")}
            for m in r.json().get("models", [])
        ]


async def ollama_loaded() -> list[dict]:
    """目前常駐在記憶體裡的模型 —— 16GB 的關鍵資訊。"""
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{OLLAMA}/api/ps")
            return [
                {"name": m["name"], "size_gb": round(m.get("size", 0) / 1e9, 1),
                 "until": m.get("expires_at", "")}
                for m in r.json().get("models", [])
            ]
    except Exception:
        return []


async def ollama_unload(model: str) -> bool:
    """keep_alive=0 立刻踢出記憶體。"""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{OLLAMA}/api/generate",
                         json={"model": model, "keep_alive": 0, "prompt": ""})
        return r.status_code == 200


async def ollama_chat(model: str, prompt: str, *, json_mode=False, think=False,
                      temperature=0.8, num_ctx=8192, timeout=900) -> str:
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "stream": False, "think": think,
            "options": {"temperature": temperature, "num_ctx": num_ctx}}
    if json_mode:
        body["format"] = "json"
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(f"{OLLAMA}/api/chat", json=body)
        r.raise_for_status()
        return r.json()["message"]["content"]


async def drawthings_status() -> dict:
    """Draw Things 需要在 App 的 Advanced 分頁手動開啟 API Server。"""
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{DRAWTHINGS}/")
            cfg = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            return {"ok": True, "model": cfg.get("model", ""), "config": cfg}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__,
                "hint": "開啟 Draw Things App → Advanced → 啟用 API Server（HTTP / 7860 / localhost）"}


async def health() -> dict:
    ol, dt = {"ok": False}, {"ok": False}
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            await c.get(f"{OLLAMA}/api/tags")
        ol = {"ok": True}
    except Exception as e:
        ol = {"ok": False, "error": type(e).__name__, "hint": "brew services start ollama"}
    dt = await drawthings_status()
    ff = shutil.which("ffmpeg")
    return {"ollama": ol, "drawthings": dt,
            "ffmpeg": {"ok": bool(ff), "path": ff or "", "hint": "brew install ffmpeg"}}


def system_stats() -> dict:
    vm = psutil.virtual_memory()
    return {
        "total_gb": round(vm.total / 1e9, 1),
        "used_gb": round((vm.total - vm.available) / 1e9, 1),
        "available_gb": round(vm.available / 1e9, 1),
        "percent": vm.percent,
        "swap_gb": round(psutil.swap_memory().used / 1e9, 2),
        "cpu": psutil.cpu_percent(interval=None),
    }
