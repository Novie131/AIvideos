#!/usr/bin/env python3
"""CLI 版腳本產線 —— 薄殼。

編排在 studio/scriptgen.py 的 generate_script()，與 Web 端共用同一份。
2026-09-25 之前這裡有一份重複的編排，且漏掉了跑完釋放 LLM 那步（違反 ADR-008）。
"""
import argparse, asyncio, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from studio import scriptgen as sg


def main() -> None:
    p = argparse.ArgumentParser(description="題材 → script.json")
    p.add_argument("--idea", required=True)
    p.add_argument("--slug", required=True)
    p.add_argument("--character", default="orange-cat")
    p.add_argument("--model", default="qwen3:8b")
    p.add_argument("--think", action="store_true")
    p.add_argument("--shots", type=int, default=0, help="0 = 依片長自動決定")
    p.add_argument("--duration", type=int, default=15)
    p.add_argument("--type", dest="script_type", default="story",
                   choices=["story", "performance"], help="故事劇情 / 才藝展示")
    p.add_argument("--form", default="quadruped", choices=["quadruped", "anthro"],
                   help="四足貓 / 人形（可雙足跳舞、演奏）")
    p.add_argument("--music", default="", help="才藝軌的配樂註記（會提醒版權）")
    p.add_argument("--keep-llm", action="store_true", help="跑完不釋放 LLM")
    a = p.parse_args()

    r = asyncio.run(sg.generate_script(
        a.idea, a.slug, character=a.character, model=a.model, think=a.think,
        n_shots=a.shots, duration=a.duration, release_llm=not a.keep_llm,
        script_type=a.script_type, form=a.form, music=a.music,
        on_step=lambda t, pct=None: print(f"  {t}", flush=True)))

    s = r["script"]
    print(f"\n定稿：{s.get('title','')}  |  {len(s.get('shots',[]))} shots / {r['total']} 秒")
    for i in r["issues"]:
        print(f"  ! {i}")
    for c in r["consistency"]:
        if not c["ok"]:
            print(f"  ⚠ Shot {c['id']} 角色描述缺：{', '.join(c['missing'])}")
    if r.get("music_warning"):
        print(f"\n⚠ {r['music_warning']}")
    print(f"\n輸出：projects/{r['slug']}/")


if __name__ == "__main__":
    main()
