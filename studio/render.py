"""ffmpeg 合成 —— 出圖 + 旁白 → 1080x1920 mp4。

兩個非顯而易見的決定：

1. **字幕用 PNG 疊圖，不用 subtitles/drawtext 濾鏡。**
   實測這台機器的 ffmpeg 9.0.1（Homebrew）沒有編進 libass 與 freetype，
   `subtitles`、`drawtext`、`ass` 三個濾鏡全部不存在，`overlay` 和 `zoompan` 有。
   自己用 Pillow 畫成透明 PNG 再 overlay，零系統安裝，描邊與自動斷行也好控制。
   pipeline-plan.md 的 Q10 問「選哪個 CJK 字型」問錯了方向 —— 問題不是字型，是畫不出字。

2. **一個 shot 一段 mp4，最後再 concat。**
   不用單一巨大的 filter_complex。好處：跑到一半掛掉能接（段落已存在就跳過）、
   出錯時知道是哪一個 shot、單段可以重做。跟 tts.py 一樣，磁碟就是進度。
"""
from __future__ import annotations
import asyncio, json, shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ROOT / "projects"

W, H, FPS = 1080, 1920, 30
SS = 2                      # zoompan 前先放大的倍率，避免縮放抖動
ZOOM = 1.28                 # push/pull 的最大縮放。1.15 太保守，3 秒內幾乎看不出動
ZOOM_PAN = 1.22             # 搖鏡用的縮放：要有餘裕可以橫移，但不必像推拉那麼多
ZOOM_STATIC = 1.06          # "static" 也給一點極慢漂移 —— 完全靜止會讓觀眾以為影片卡住
XFADE = 0.30                # 鏡頭之間的轉場秒數。硬切是「翻投影片」感的主因

# 轉場型態依「前一個鏡頭的鏡位」決定，讓轉場延續運鏡方向而不是打斷它。
# **刻意不用 fade（交叉溶接）**：兩張圖的主體都在畫面中央，溶接會出現疊影
#（同時看到兩隻貓、四隻眼睛）。方向性推移沒有這個問題，而且對 Shorts 更有節奏感。
TRANSITIONS = {
    "pan_left":  "smoothleft",
    "pan_right": "smoothright",
    "push_in":   "smoothup",
    "pull_out":  "smoothdown",
    "static":    "fadeblack",
}
DEFAULT_TRANSITION = "smoothleft"
CRF, PRESET = "20", "medium"

# 字幕
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"
_FONT_ALT = ["/System/Library/Fonts/Hiragino Sans GB.ttc",
             "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"]
FONT_SIZE, STROKE = 68, 7
SUB_BOTTOM = 0.22           # 字幕基線距底部的比例（避開 Shorts 的 UI 遮擋）
SUB_MAX_W = 0.86            # 字幕最大寬度佔畫面比例

PLACEHOLDER_BG = [(46, 58, 82), (82, 58, 46), (46, 82, 62), (72, 46, 82), (82, 74, 46)]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for p in [FONT, *_FONT_ALT]:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    raise RuntimeError("找不到任何 CJK 字型")


async def _run(*cmd) -> bytes:
    p = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await p.communicate()
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} 失敗\n{' '.join(map(str, cmd))[:300]}\n"
                           f"{err.decode(errors='replace')[-800:]}")
    return out


# ---------- 假圖：讓 imagegen.py 還沒好之前也能合成 ----------

def placeholder(shot: dict, out: Path, idx: int) -> None:
    """純色底 + shot 編號 + 動作描述。用途是驗證合成管線，不是美術。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    w, h = 768, 1344
    img = Image.new("RGB", (w, h), PLACEHOLDER_BG[idx % len(PLACEHOLDER_BG)])
    d = ImageDraw.Draw(img)
    d.text((w // 2, h // 2 - 120), f"SHOT {shot.get('id', idx + 1)}",
           font=_font(120), fill=(255, 255, 255), anchor="mm")
    d.text((w // 2, h // 2 + 20), shot.get("camera", ""),
           font=_font(52), fill=(190, 200, 215), anchor="mm")
    for i, line in enumerate(_wrap(shot.get("action", ""), _font(40), int(w * 0.8))):
        d.text((w // 2, h // 2 + 110 + i * 54), line,
               font=_font(40), fill=(150, 162, 180), anchor="mm")
    img.save(out)


# ---------- 字幕 ----------

def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """CJK 沒有空格，所以逐字累加來斷行；標點不放行首。"""
    if not text:
        return []
    lines, cur = [], ""
    for ch in text:
        probe = cur + ch
        if font.getlength(probe) > max_w and cur:
            if ch in "，。、！？；：）」』":      # 標點跟著上一行
                lines.append(probe)
                cur = ""
                continue
            lines.append(cur)
            cur = ch
        else:
            cur = probe
    if cur:
        lines.append(cur)
    return lines


def subtitle_png(text: str, out: Path) -> None:
    """整張 1080x1920 的透明 PNG，字幕已定位好，overlay 時貼 0:0 即可。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if text.strip():
        d = ImageDraw.Draw(img)
        f = _font(FONT_SIZE)
        lines = _wrap(text.strip(), f, int(W * SUB_MAX_W))
        lh = FONT_SIZE + 22
        base = int(H * (1 - SUB_BOTTOM)) - (len(lines) - 1) * lh
        for i, line in enumerate(lines):
            d.text((W // 2, base + i * lh), line, font=f, anchor="mm",
                   fill=(255, 255, 255), stroke_width=STROKE, stroke_fill=(0, 0, 0))
    img.save(out)


# ---------- Ken Burns ----------

def _ease(n: int) -> str:
    """smoothstep 緩動：t*t*(3-2t)，把等速推軌變成「起步慢→中間快→收尾慢」。

    真實運鏡不會等速。線性 zoompan 是畫面像簡報的第二個原因（第一個是硬切）。
    """
    t = f"(on/{n})"
    return f"({t}*{t}*(3-2*{t}))"


def _kenburns(camera: str, frames: int) -> str:
    """zoompan 表達式。on 是目前影格序號，d 是總影格數。"""
    n = max(frames - 1, 1)
    e = _ease(n)
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    span = "(iw-iw/zoom)"
    if camera == "push_in":
        z, x, y = f"'1+{ZOOM - 1:.3f}*{e}'", cx, cy
    elif camera == "pull_out":
        z, x, y = f"'{ZOOM}-{ZOOM - 1:.3f}*{e}'", cx, cy
    elif camera == "pan_left":
        z, x, y = f"'{ZOOM_PAN}'", f"'{span}*(1-{e})'", cy
    elif camera == "pan_right":
        z, x, y = f"'{ZOOM_PAN}'", f"'{span}*{e}'", cy
    else:                                            # static：極慢推近，不是真的不動
        z, x, y = f"'1+{ZOOM_STATIC - 1:.3f}*{e}'", cx, cy
    return (f"scale={W*SS}:{H*SS}:force_original_aspect_ratio=increase,"
            f"crop={W*SS}:{H*SS},"
            f"zoompan=z={z}:x={x}:y={y}:d={frames}:s={W}x{H}:fps={FPS}")


# ---------- 單段 ----------

async def build_clip(img: Path, out: Path, sec: float, camera: str) -> None:
    """單一 shot 的運鏡片段。**不含字幕** —— 字幕在 xfade 之後才疊，
    否則轉場那 0.35 秒會看到前後兩句字幕互相溶接，很髒。"""
    frames = max(int(round(sec * FPS)), 2)
    await _run("ffmpeg", "-y", "-loglevel", "error",
               "-loop", "1", "-t", f"{sec}", "-i", str(img),
               "-filter_complex", f"[0:v]{_kenburns(camera, frames)},format=yuv420p,setsar=1[v]",
               "-map", "[v]", "-t", f"{sec}",
               "-c:v", "libx264", "-preset", PRESET, "-crf", CRF,
               "-pix_fmt", "yuv420p", "-r", str(FPS), str(out))


async def build_audio(wav: Path | None, out: Path, sec: float) -> None:
    """把旁白補靜音到剛好 sec 秒。沒有旁白就產生純靜音。"""
    if wav and wav.exists():
        await _run("ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
                   "-af", f"apad=whole_dur={sec},aformat=sample_fmts=s16:channel_layouts=mono",
                   "-ar", "24000", "-t", f"{sec}", str(out))
    else:
        await _run("ffmpeg", "-y", "-loglevel", "error",
                   "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                   "-t", f"{sec}", str(out))


# ---------- 主流程 ----------

async def render(slug: str, *, bgm: Path | None = None, bgm_db: float = -22.0,
                 force: bool = False, on_step=lambda t, pct=None: None) -> dict:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("找不到 ffmpeg")
    d = PROJECTS / slug
    script = json.loads((d / "script.json").read_text(encoding="utf-8"))
    shots = script.get("shots", [])
    if not shots:
        raise RuntimeError(f"{slug} 沒有 shots")

    shots_dir, audio_dir = d / "shots", d / "audio"
    work, outdir = d / ".work", d / "out"
    work.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    if force:
        for f in work.glob("*"):
            f.unlink()

    n, used_placeholder = len(shots), []
    vlist, alist, subs, timeline = [], [], [], []
    cum = 0.0

    for i, sh in enumerate(shots):
        sid = sh.get("id", i + 1)
        sec = float(sh.get("duration_sec", 3))
        last = (i == n - 1)
        clip_sec = sec if last else sec + XFADE   # 多出來的部分會被下一段吃掉，總長不變
        pct = round((i + 1) / n * 75)

        img = shots_dir / f"shot-{sid:02d}.png"
        if not img.exists():
            img = work / f"ph-{sid:02d}.png"
            if not img.exists():
                placeholder(sh, img, i)
            used_placeholder.append(sid)

        sub = work / f"sub-{sid:02d}.png"
        if not sub.exists() or force:
            subtitle_png(sh.get("subtitle") or sh.get("narration") or "", sub)
        subs.append(sub)
        timeline.append((cum, cum + sec))

        # 檔名帶秒數：改了 XFADE 或片長，快取自動失效
        clip = work / f"clip-{sid:02d}-{clip_sec:.2f}.mp4"
        if clip.exists() and not force:
            on_step(f"Shot {sid}：影片段已存在，跳過", pct)
        else:
            on_step(f"Shot {sid}：運鏡 {sec}s（{sh.get('camera', 'static')}）", pct)
            await build_clip(img, clip, clip_sec, sh.get("camera", "static"))
        vlist.append(clip)

        aud = work / f"aud-{sid:02d}.wav"
        if not aud.exists() or force:
            await build_audio(audio_dir / f"shot-{sid:02d}.wav", aud, sec)
        alist.append(aud)
        cum += sec

    total_sec = cum

    # 影片：xfade 串接。offset_k = 前 k 段的原始片長總和，總長因此維持 sum(duration_sec)
    on_step(f"交叉溶接 {n} 段（每段 {XFADE}s）…", 80)
    vcat = work / "v.mp4"
    if n == 1:
        await _run("ffmpeg", "-y", "-loglevel", "error", "-i", str(vlist[0]),
                   "-c", "copy", str(vcat))
    else:
        args, fc, prev, off = [], [], "0:v", 0.0
        for c in vlist:
            args += ["-i", str(c)]
        for k in range(1, n):
            off += timeline[k - 1][1] - timeline[k - 1][0]
            lbl = f"x{k}"
            tr = TRANSITIONS.get(shots[k - 1].get("camera", ""), DEFAULT_TRANSITION)
            fc.append(f"[{prev}][{k}:v]xfade=transition={tr}:"
                      f"duration={XFADE}:offset={off:.3f}[{lbl}]")
            prev = lbl
        await _run("ffmpeg", "-y", "-loglevel", "error", *args,
                   "-filter_complex", ";".join(fc), "-map", f"[{prev}]",
                   "-c:v", "libx264", "-preset", PRESET, "-crf", CRF,
                   "-pix_fmt", "yuv420p", "-r", str(FPS), str(vcat))

    # 字幕：溶接完才疊，每句用 enable 卡在自己的時間窗內，不跨轉場
    on_step("疊上字幕…", 86)
    vsub = work / "vs.mp4"
    args, fc, prev = ["-i", str(vcat)], [], "0:v"
    for k, (sp, (t0, t1)) in enumerate(zip(subs, timeline), start=1):
        args += ["-i", str(sp)]
        lbl = f"s{k}"
        a, b = t0 + XFADE / 2, t1 - XFADE / 2
        fc.append(f"[{prev}][{k}:v]overlay=0:0:enable='between(t,{a:.3f},{b:.3f})'[{lbl}]")
        prev = lbl
    await _run("ffmpeg", "-y", "-loglevel", "error", *args,
               "-filter_complex", ";".join(fc), "-map", f"[{prev}]",
               "-c:v", "libx264", "-preset", PRESET, "-crf", CRF,
               "-pix_fmt", "yuv420p", "-r", str(FPS), str(vsub))
    vcat = vsub

    # 音軌：照原始片長串接，與影片總長對齊
    atxt = work / "a.txt"
    atxt.write_text("".join(f"file '{p.name}'\n" for p in alist), encoding="utf-8")
    acat = work / "a.wav"
    await _run("ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
               "-i", str(atxt), "-c", "copy", str(acat))

    # 混音 + 輸出
    on_step("混音與輸出…", 92)
    final = outdir / "final.mp4"
    if bgm and bgm.exists():
        await _run("ffmpeg", "-y", "-loglevel", "error",
                   "-i", str(vcat), "-i", str(acat), "-stream_loop", "-1", "-i", str(bgm),
                   "-filter_complex",
                   f"[2:a]volume={bgm_db}dB[b];[1:a][b]amix=inputs=2:duration=first:"
                   f"dropout_transition=0,aformat=sample_fmts=fltp:sample_rates=48000[a]",
                   "-map", "0:v", "-map", "[a]", "-c:v", "copy",
                   "-c:a", "aac", "-b:a", "160k", "-shortest", str(final))
    else:
        await _run("ffmpeg", "-y", "-loglevel", "error",
                   "-i", str(vcat), "-i", str(acat),
                   "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                   "-c:a", "aac", "-b:a", "160k", "-shortest", str(final))

    out = await _run("ffprobe", "-v", "error", "-show_entries",
                     "format=duration,size", "-of", "json", str(final))
    meta = json.loads(out)["format"]
    on_step(f"完成：{final.relative_to(ROOT)}", 100)
    return {"slug": slug, "file": str(final.relative_to(ROOT)),
            "sec": round(float(meta["duration"]), 2),
            "mb": round(int(meta["size"]) / 1e6, 2),
            "shots": n, "placeholder_shots": used_placeholder,
            "bgm": str(bgm) if bgm else None}


# ---------- CLI 薄殼 ----------

def _main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="把出圖與旁白合成 1080x1920 mp4")
    p.add_argument("slug")
    p.add_argument("--bgm", type=Path, default=None, help="背景音樂檔（會自動循環並壓低音量）")
    p.add_argument("--bgm-db", type=float, default=-22.0)
    p.add_argument("--force", action="store_true", help="全部重做，不沿用既有片段")
    a = p.parse_args()

    r = asyncio.run(render(a.slug, bgm=a.bgm, bgm_db=a.bgm_db, force=a.force,
                           on_step=lambda t, pct=None: print(f"  {t}")))
    print(f"\n{r['file']}   {r['sec']}s   {r['mb']} MB   {r['shots']} shots")
    if r["placeholder_shots"]:
        print(f"  ⚠ 這些 shot 用的是假圖（還沒出圖）：{r['placeholder_shots']}")


if __name__ == "__main__":
    _main()
