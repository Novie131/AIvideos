"""出圖 —— Draw Things HTTP API。

ADR-002：Draw Things 關在這個檔案後面，對外只有 render_shots()。換後端＝改這一個檔案，
腳本層與合成層不動。所有參數從 presets/style-v*.json 帶入，**絕不依賴 App 的 UI 狀態**。

實測數字（2026-09-24，M2 16GB，Z Image Turbo 1.0 8-bit，768x1344，8 步）：
  冷啟動第一張 312.8s（含載入 10.2GB 模型），**穩態 162s/張**。
  對照 pipeline-plan.md §6.1 的門檻（3 分鐘/張可接受）：過關。
  ep001 五張約 13.5 分鐘，ep002 九張約 24 分鐘。

注意 Draw Things 的 API 欄位名與 A1111 不完全相同：CFG 是 guidance_scale 不是 cfg_scale。
完整欄位可用 GET /sdapi/v1/options 取得（83 個）。
"""
from __future__ import annotations
import asyncio, base64, json, time
from pathlib import Path

import httpx
from PIL import Image

from . import backends as be

ROOT = Path(__file__).resolve().parent.parent
PROJECTS, PRESETS, CHARS = ROOT / "projects", ROOT / "presets", ROOT / "characters"

TIMEOUT = 900.0          # 單張的上限。實測穩態 162s，冷啟動 313s，900 留足餘裕。
FACE_TOP = 0.22          # 臉部裁切的起點（距頂端比例）。直幅構圖中臉通常落在上三分之一。


def load_preset(name: str = "style-v1") -> dict:
    return json.loads((PRESETS / f"{name}.json").read_text(encoding="utf-8"))


def build_prompt(shot: dict, preset: dict, form_modifier: str = "") -> str:
    """最終 prompt = [型態前綴] + shot.image_prompt（已內嵌 appearance_en）+ style_suffix。

    scriptgen 產出的 image_prompt 已經完整包含 character.json 的 appearance_en
    （character.json 的 note 明文要求，`sg.consistency()` 會檢查），所以這裡不再重複拼。

    **型態修飾詞是前綴不是後綴。** 2026-09-25 實測：把 anthropomorphic 放在
    appearance_en 之後完全無效 —— 模型被前面那段「正常貓」的長描述錨定，
    產出的仍是四足貓。搬到最前面就成功了，且不需要動 cfg。
    """
    suffix = (preset.get("prompt") or {}).get("style_suffix") or ""
    p = shot.get("image_prompt", "").strip()
    if form_modifier:
        p = f"{form_modifier.rstrip(', ')} {p}"
    return f"{p}, {suffix}" if suffix else p


def form_modifier(character: str, form: str) -> str:
    """取出角色型態的提示詞前綴（四足通常為空字串）。"""
    cp = CHARS / character / "character.json"
    if not cp.exists():
        return ""
    forms = (json.loads(cp.read_text(encoding="utf-8")).get("forms") or {})
    return (forms.get(form) or {}).get("modifier", "")


def api_payload(shot: dict, preset: dict, form_mod: str = "") -> dict:
    m, out, seed = preset["model"], preset["output"], preset["seed"]
    return {
        "prompt": build_prompt(shot, preset, form_mod),
        "negative_prompt": (preset.get("prompt") or {}).get("negative") or "",
        "model": m["checkpoint"],
        "sampler": m["sampler"],
        "steps": m["steps"],
        "guidance_scale": m["cfg"],          # ← Draw Things 叫 guidance_scale
        "shift": m.get("shift", 3.0),
        "resolution_dependent_shift": m.get("resolution_dependent_shift", False),
        "cfg_zero_star": m.get("cfg_zero_star", False),
        "clip_skip": m.get("clip_skip", 1),
        "width": out["main"]["width"],
        "height": out["main"]["height"],
        "seed": seed["base_seed"] + shot.get("id", 0),
        "batch_count": 1,
        "batch_size": m.get("batch_size", 1),
    }


def face_crop(src: Path, dst: Path, size: int) -> None:
    """另存一張方形臉部裁切 —— §6.6 第 2 點：現在加是零成本，事後補要全部重跑。

    這是啟發式裁切（水平置中、垂直取上段），不是臉部偵測。用途是萬一走方案 D
    需要訓練資料時細節有足夠像素；dataset/README.md 要求人工精選，所以裁歪的會被篩掉。
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    im = Image.open(src)
    w, h = im.size
    side = min(w, h)
    left = (w - side) // 2
    top = min(max(int(h * FACE_TOP), 0), h - side)
    im.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS).save(dst)


async def render_one(shot: dict, preset: dict, out: Path, *, api: str | None = None,
                     form_mod: str = "") -> dict:
    """出一張圖。對外的最小單位 —— 換後端只需要換掉這個函式。"""
    url = (api or preset["model"].get("api") or be.DRAWTHINGS).rstrip("/")
    body = api_payload(shot, preset, form_mod)
    t0 = time.time()
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(f"{url}/sdapi/v1/txt2img", json=body)
        r.raise_for_status()
        data = r.json()
    el = time.time() - t0
    imgs = data.get("images") or []
    if not imgs:
        raise RuntimeError(f"API 沒有回傳圖片。回應鍵：{list(data)}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(imgs[-1].split(",")[-1]))
    fc = preset["output"].get("face_crop")
    if fc:
        face_crop(out, out.parent / "face" / out.name, fc["width"])
    return {"file": out.name, "sec": round(el, 1), "seed": body["seed"],
            "size": Image.open(out).size}


async def render_shots(slug: str, *, preset_name: str = "style-v1", force: bool = False,
                       only: list[int] | None = None, api: str | None = None,
                       on_step=lambda text, pct=None: None) -> dict:
    """出一集的所有圖。

    可續跑：png 已存在就跳過（磁碟就是進度，不另存狀態檔）。要重抽某張就刪掉那個 png。
    每張出完立刻寫檔 —— 跑到第 4 張掛掉，前 3 張還在。
    """
    d = PROJECTS / slug
    script = json.loads((d / "script.json").read_text(encoding="utf-8"))
    preset = load_preset(preset_name)
    # 型態由腳本帶（scriptgen 寫入 script.json 的 form 欄位），不是出圖時才選
    fmod = form_modifier(script.get("character_id", "orange-cat"),
                         script.get("form", "quadruped"))
    shots = script.get("shots", [])
    if only:
        shots = [s for s in shots if s.get("id") in only]
    if not shots:
        raise RuntimeError(f"{slug} 沒有要出的 shot")

    sdir = d / preset["output"].get("dir", "shots")
    results, n = [], len(shots)

    for i, s in enumerate(shots, 1):
        sid = s.get("id", i)
        out = sdir / f"shot-{sid:02d}.png"
        pct = round(i / n * 100)
        if out.exists() and not force:
            on_step(f"Shot {sid}：已存在，跳過", pct)
            results.append({"id": sid, "file": out.name, "skipped": "已存在"})
            continue
        on_step(f"Shot {sid}：出圖中（約 162 秒）…", pct)
        r = await render_one(s, preset, out, api=api, form_mod=fmod)
        r["id"] = sid
        results.append(r)
        on_step(f"Shot {sid}：完成 {r['sec']}s", pct)

    done = [r for r in results if "sec" in r]
    total = round(sum(r["sec"] for r in done), 1)
    manifest = {
        "slug": slug, "preset": preset["preset_id"],
        "preset_status": preset.get("status"),
        "checkpoint": preset["model"]["checkpoint"],
        "sampler": preset["model"]["sampler"], "steps": preset["model"]["steps"],
        "cfg": preset["model"]["cfg"], "base_seed": preset["seed"]["base_seed"],
        "form": script.get("form", "quadruped"),
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "shots": results,
    }
    (sdir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    on_step(f"完成 {len(done)} 張，共 {total}s"
            + (f"（{len(results)-len(done)} 張沿用既有）" if len(done) < len(results) else ""), 100)
    return manifest


# ---------- CLI 薄殼 ----------

def _main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="用 Draw Things 出一集的圖")
    p.add_argument("slug")
    p.add_argument("--preset", default="style-v1")
    p.add_argument("--force", action="store_true", help="已存在也重出")
    p.add_argument("--only", help="只出這幾個 shot，逗號分隔，例如 2,4")
    p.add_argument("--api", default=None, help="覆蓋 API 位址")
    a = p.parse_args()

    only = [int(x) for x in a.only.split(",")] if a.only else None
    m = asyncio.run(render_shots(a.slug, preset_name=a.preset, force=a.force,
                                 only=only, api=a.api,
                                 on_step=lambda t, pct=None: print(f"  {t}")))
    print(f"\n輸出：projects/{a.slug}/shots/   preset {m['preset']}（{m['preset_status']}）")
    for s in m["shots"]:
        print(f"  shot {s['id']:>2}  " + (s.get("skipped") or
              f"{s['sec']}s  seed {s['seed']}  {s['size'][0]}x{s['size'][1]}"))


if __name__ == "__main__":
    _main()
