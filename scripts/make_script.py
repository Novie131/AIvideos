#!/usr/bin/env python3
"""CLI 版腳本產線。UI 版見 studio/server.py，兩者共用 studio/scriptgen.py。"""
import argparse, asyncio, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from studio import backends as be, scriptgen as sg

ROOT = Path(__file__).resolve().parent.parent


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--idea", required=True)
    p.add_argument("--slug", required=True)
    p.add_argument("--character", default="orange-cat")
    p.add_argument("--model", default="qwen3:8b")
    p.add_argument("--think", action="store_true")
    p.add_argument("--shots", type=int, default=0, help="0 = 依片長自動決定")
    p.add_argument("--duration", type=int, default=15)
    a = p.parse_args()

    a.shots = a.shots or sg.suggest_shots(a.duration)
    cp = ROOT / "characters" / a.character / "character.json"
    char = cp.read_text(encoding="utf-8") if cp.exists() else "（未指定）"
    app_en = json.loads(char).get("appearance_en", "") if cp.exists() else ""
    out = ROOT / "projects" / a.slug
    out.mkdir(parents=True, exist_ok=True)
    (out / "idea.txt").write_text(a.idea, encoding="utf-8")

    async def ask(prompt, **kw):
        return await be.ollama_chat(a.model, prompt, think=a.think, **kw)

    t0 = time.time()
    print("[1/3] Writer   … ", end="", flush=True)
    draft = sg.parse_json(await ask(sg.WRITER.format(
        character=char, idea=a.idea, n_shots=a.shots, duration=a.duration,
        max_chars=int(a.duration / a.shots * sg.CPS),
        structure=sg.beats(a.duration)), json_mode=True))
    sg.assign_cameras(draft.get("shots", []))
    sg.fix_simplified(draft)
    (out / "script.draft.json").write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{time.time()-t0:.0f}s")

    _, issues = sg.check(draft)
    hard = "\n".join(f"- {i}" for i in issues) or "-（程式檢查無誤）"

    t = time.time(); print("[2/3] Critic   … ", end="", flush=True)
    crit = sg.strip_think(await ask(sg.CRITIC.format(
        script=json.dumps(draft, ensure_ascii=False, indent=2))))
    crit += f"\n\n【程式硬檢查】\n{hard}"
    (out / "critique.md").write_text(crit, encoding="utf-8")
    print(f"{time.time()-t:.0f}s")

    t = time.time(); print("[3/3] Rewrite  … ", end="", flush=True)
    final = sg.parse_json(await ask(sg.REWRITE.format(
        script=json.dumps(draft, ensure_ascii=False, indent=2), critique=crit), json_mode=True))
    sg.assign_cameras(final.get("shots", []))
    sg.fix_simplified(final)
    print(f"{time.time()-t:.0f}s")

    for i in range(1, 4):
        _, issues = sg.check(final)
        if not issues:
            break
        t = time.time(); print(f"[修] 第 {i} 輪，{len(issues)} 個問題 … ", end="", flush=True)
        final = sg.parse_json(await ask(sg.REPAIR.format(
            script=json.dumps(final, ensure_ascii=False, indent=2),
            issues="\n".join(f"- {x}" for x in issues)), json_mode=True))
        sg.assign_cameras(final.get("shots", []))
        sg.fix_simplified(final)
        print(f"{time.time()-t:.0f}s")

    (out / "script.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    total, issues = sg.check(final)
    print(f"\n定稿：{final.get('title','')}  |  {len(final.get('shots',[]))} shots / {total} 秒  |  總計 {time.time()-t0:.0f}s")
    for i in issues:
        print(f"  ! {i}")
    if not issues:
        print("硬檢查全數通過。")
    for c in sg.consistency(final, app_en):
        if not c["ok"]:
            print(f"  ⚠ Shot {c['id']} 角色描述缺：{', '.join(c['missing'])}")
    print(f"\n輸出：projects/{a.slug}/")


if __name__ == "__main__":
    asyncio.run(main())
