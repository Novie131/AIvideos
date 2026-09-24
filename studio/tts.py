"""旁白合成 —— macOS 內建 say。

為什麼不是 Kokoro（ADR-007 / M3）：
  實測這台機器有 9 個 zh_TW 語音，零安裝、零下載。ADR-010 的目標是兩週內發一支片，
  先用 say 把管線打通；Kokoro 值不值得裝，等第一支片上架、用真耳朵比較過再決定。
  換 TTS 後端只需要換掉 synth_one()，對外介面不變。

架構（三個模組共用）：核心是純函式 + on_step callback，不知道 web 也不知道 cli 的存在。
"""
from __future__ import annotations
import asyncio, json, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ROOT / "projects"

VOICE = "Meijia"          # zh_TW。其他可選：Sandy / Shelley / Eddy / Flo / Reed / Rocko
BASE_RATE = 180           # say 的預設語速（字/分），量測基準
MIN_RATE, MAX_RATE = 120, 320
MIN_AUDIBLE_SEC = 0.15   # 低於此視為「語音未安裝」，見 voices() 的說明
FMT = ("--file-format=WAVE", "--data-format=LEI16@24000")


def voices(locale: str = "zh_TW", *, installed_only: bool = True) -> list[str]:
    """機器上可用的中文語音，給 UI 做選單用。

    兩個坑：
    1. say -v ? 會把同一個語音的 zh_CN / zh_TW 兩種口音各列一行，名字相同 → 去重。
    2. **它也會列出「可下載但尚未安裝」的語音**（Sandy / Shelley / Eddy / Flo / Reed /
       Rocko / Grandma / Grandpa 等）。叫它用這些語音不會報錯，只會產出 0.02 秒的空檔案。
       所以預設實際試合成一次來篩 —— 這是唯一可靠的判斷方式。
    """
    import subprocess, tempfile, wave
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    seen, names = set(), []
    for l in out.splitlines():
        if locale not in l:
            continue
        name = l.split()[0]
        if name not in seen:
            seen.add(name)
            names.append(name)
    if not installed_only:
        return names

    res = []
    with tempfile.TemporaryDirectory() as td:
        for n in names:
            f = Path(td) / f"{n}.wav"
            try:
                subprocess.run(["say", "-v", n, *FMT, "-o", str(f), "測試"],
                               capture_output=True, timeout=20)
                with wave.open(str(f)) as w:
                    if w.getnframes() / w.getframerate() >= MIN_AUDIBLE_SEC:
                        res.append(n)
            except Exception:
                pass
    return res


async def _run(*cmd) -> bytes:
    p = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await p.communicate()
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} 失敗：{err.decode(errors='replace')[:300]}")
    return out


async def duration(path: Path) -> float:
    out = await _run("ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "csv=p=0", str(path))
    return float(out.decode().strip())


async def synth_one(text: str, out: Path, *, voice: str = VOICE,
                    target_sec: float | None = None) -> dict:
    """合成一段旁白。

    target_sec 有給時只會「加速」不會「放慢」——旁白比鏡頭短是正常的（留白），
    硬把 5 個字拖成 3 秒會變成鬼片。太長才需要壓縮。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    rate = BASE_RATE
    await _run("say", "-v", voice, "-r", str(rate), *FMT, "-o", str(out), text)
    dur = await duration(out)

    if dur < MIN_AUDIBLE_SEC:
        out.unlink(missing_ok=True)
        raise RuntimeError(
            f"語音「{voice}」產出空音檔（{dur:.2f}s）—— 它被 say -v ? 列出來了，"
            f"但沒有實際安裝在這台機器上。\n"
            f"可用的語音：{', '.join(voices()) or '（偵測不到，請確認 say 能用）'}\n"
            f"要安裝更多／更好的語音：系統設定 → 輔助使用 → 語音內容 → 系統語音 → 管理語音")

    fitted = False
    if target_sec and dur > target_sec:
        # 語速與時長成反比，一次換算通常就夠
        rate = min(MAX_RATE, max(MIN_RATE, round(rate * dur / target_sec)))
        await _run("say", "-v", voice, "-r", str(rate), *FMT, "-o", str(out), text)
        dur = await duration(out)
        fitted = True

    return {"file": out.name, "sec": round(dur, 2), "rate": rate,
            "voice": voice, "fitted": fitted,
            "over": round(dur - target_sec, 2) if target_sec and dur > target_sec else 0.0}


async def synth_shots(slug: str, *, voice: str = VOICE, force: bool = False,
                      on_step=lambda text, pct=None: None) -> dict:
    """把一集的每個 shot 旁白合成成 wav。

    可續跑：wav 已存在就跳過。磁碟就是進度，不另外存狀態檔
    —— 任何獨立的進度檔都有跟磁碟不同步的一天。要重做某一段，刪掉那個 wav 即可。
    """
    if not shutil.which("say"):
        raise RuntimeError("找不到 say（這個模組只在 macOS 上能跑）")
    d = PROJECTS / slug
    sp = d / "script.json"
    if not sp.exists():
        raise FileNotFoundError(f"找不到 {sp}")

    script = json.loads(sp.read_text(encoding="utf-8"))
    shots = script.get("shots", [])
    adir = d / "audio"
    results, n = [], len(shots)

    for i, s in enumerate(shots, 1):
        sid = s.get("id", i)
        text = (s.get("narration") or "").strip()
        out = adir / f"shot-{sid:02d}.wav"
        pct = round(i / n * 100)

        if not text:
            on_step(f"Shot {sid}：無旁白，跳過", pct)
            results.append({"id": sid, "file": None, "sec": 0.0, "skipped": "無旁白"})
            continue
        if out.exists() and not force:
            on_step(f"Shot {sid}：已存在，跳過", pct)
            results.append({"id": sid, "file": out.name,
                            "sec": round(await duration(out), 2), "skipped": "已存在"})
            continue

        on_step(f"Shot {sid}：合成「{text[:12]}…」", pct)
        r = await synth_one(text, out, voice=voice,
                            target_sec=s.get("duration_sec"))
        r["id"] = sid
        r["target_sec"] = s.get("duration_sec")
        results.append(r)

    total = round(sum(r["sec"] for r in results), 2)
    over = [r["id"] for r in results if r.get("over", 0) > 0.15]
    on_step(f"完成：{len(results)} 段，共 {total} 秒"
            + (f"；仍超時的 shot {over}（語速已到上限）" if over else ""), 100)
    return {"slug": slug, "voice": voice, "total_sec": total,
            "dir": str(adir.relative_to(ROOT)), "shots": results, "over": over}


# ---------- CLI 薄殼（除錯用；正式操作走 Web UI） ----------

def _main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="產生一集的旁白 wav")
    p.add_argument("slug", nargs="?", help="projects/ 底下的資料夾名，例如 ep001-egypt")
    p.add_argument("--voice", default=VOICE)
    p.add_argument("--force", action="store_true", help="已存在也重做")
    p.add_argument("--list-voices", action="store_true")
    a = p.parse_args()

    if a.list_voices:
        print("\n".join(voices()))
        return
    if not a.slug:
        p.error("要指定 slug（或用 --list-voices）")

    r = asyncio.run(synth_shots(a.slug, voice=a.voice, force=a.force,
                                on_step=lambda t, pct=None: print(f"  {t}")))
    print(f"\n輸出：{r['dir']}/   語音 {r['voice']}   共 {r['total_sec']} 秒")
    for s in r["shots"]:
        tag = f"（{s['skipped']}）" if s.get("skipped") else \
              (f"  rate {s['rate']}" + ("  ← 已壓縮" if s.get("fitted") else ""))
        print(f"  shot {s['id']:>2}  {s['sec']:>5.2f}s / 目標 {s.get('target_sec','-')}s{tag}")


if __name__ == "__main__":
    _main()
