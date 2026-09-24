# AI Shorts 地端產線

在一台 **MacBook M2 / 16GB** 上，**零成本、全地端**地把一句題材變成可發布的
YouTube Shorts：`idea.txt` → 腳本 → 分鏡圖 → 旁白 → `1080x1920` mp4。

固定角色（橘貓「小橘」）跨鏡頭、跨集維持同一個長相。

<p align="left">
  <img src="Document/assets/frame-ep001.jpg" width="240" alt="成片畫格，含燒錄字幕">
  <img src="Document/assets/shot-01.jpg" width="150" alt="分鏡 1">
  <img src="Document/assets/shot-04.jpg" width="150" alt="分鏡 4">
</p>

<sup>左：成片畫格（Ken Burns 推進 + 燒錄字幕）。中右：同一集的兩個分鏡——同一隻貓、同樣的項圈與黃銅鈴鐺。</sup>

---

## 硬約束

這些限制是這個專案所有技術決策的來源：

| 約束 | 內容 | 可否放寬 |
|---|---|---|
| 硬體 | M2 / 16GB unified memory | 不可 |
| 成本 | 每支片邊際成本為零 | 不可 |
| 地端 | **推論全部在本機**，不呼叫任何雲端 API | 不可 |
| 品質 | 要能真的發布，不是 demo | 不可 |

## 管線

```
  idea.txt
     │  ① scriptgen   Ollama qwen3:8b        451 s/集
     │     Writer → Critic → Rewrite → 硬檢查 → Repair×3
     ▼
  script.json ──────────────┬──────────────┐
     │  ② imagegen          │ ③ tts        │
     │     Draw Things      │   macOS say  │
     │     Z-Image Turbo    │   3.7 s/集   │
     │     162 s/張         │              │
     ▼                      ▼              │
  shots/*.png          audio/*.wav         │
     └──────────┬───────────┘              │
                │  ④ render   ffmpeg  20 s/集 ◄┘
                ▼
        out/final.mp4    1080x1920 · H.264 · AAC
```

**一支 15 秒 / 8 鏡頭的片約 40 分鐘，其中 90% 是出圖。**

## 模型

全部在本機執行，無任何雲端呼叫。

| 用途 | 模型 | 佔用 |
|---|---|---|
| 編劇 | Qwen3 8B Q4（Ollama） | 5.2 GB |
| 出圖 | Z-Image Turbo 1.0 8-bit（Draw Things） | 10.2 GB |
| 影片（實驗中） | Wan 2.2 TI2V 5B 8-bit | 12.8 GB |
| 旁白 | macOS 內建 `say` | 0 |
| 合成 | ffmpeg | 0 |

## 幾個值得一提的設計

**四個核心模組簽名一致。** 純函式核心 + `on_step(text, pct)` callback，
不知道有 web 也不知道有 CLI；Web endpoint 與 CLI 各自只是 3 行薄殼。

> 這個約束是踩坑換來的。腳本編排原本在 HTTP endpoint 的 closure 與 CLI 各存在一份，
> 然後就分岔了——CLI 版漏掉「跑完釋放 LLM」，違反了 16GB 的記憶體生存條件。

**可續跑靠磁碟，不靠狀態檔。** 目標檔存在就跳過，重做某一項＝刪掉那個檔案。
任何獨立的進度檔都有跟磁碟不同步的一天。

**參數在 preset，不在 App UI 裡。** 出圖參數寫成帶版號的 JSON，每次呼叫都完整帶入，
絕不依賴 GUI 狀態。改風格開 `style-v2.json`，舊圖才追溯得到是哪組參數出的。

**規則性的事收回程式做。** 運鏡指派原本交給 LLM，8B 模型連續三輪拒絕執行，
改成純規則函式。

## 文件

| | |
|---|---|
| [`Document/Spec_v1.md`](Document/Spec_v1.md) | **它是什麼**——架構、流程、資料契約、API、實測數據、已知限制、踩過的坑 |
| [`Document/pipeline-plan.md`](Document/pipeline-plan.md) | **為什麼是這樣**——12 份 ADR，含被推翻的決策與推翻的理由 |

兩份刻意分工。ADR 保留所有走錯的路：SDXL 被 Z-Image Turbo 取代、
Kokoro TTS 被 macOS 內建語音取代、「16GB 記憶體會爆」這個假設被實測推翻
（Draw Things 用 mmap，瓶頸其實是磁碟頻寬）。**歷史比結論值錢。**

## 快速開始

前置：Ollama 已載 `qwen3:8b`；Draw Things 開著且 `設定 → 所有 → HTTP API 伺服器`
協議選 HTTP、port 7860。

```bash
./run.sh                    # 本機 Studio → http://127.0.0.1:8765
```

或走 CLI（每個模組都有薄殼）：

```bash
uv run python scripts/make_script.py --slug ep004 --idea "橘貓穿越古埃及" --duration 15
uv run python -m studio.imagegen ep004
uv run python -m studio.tts      ep004
uv run python -m studio.render   ep004
```

## 現況

腳本層與影像層都已跑通，`out/final.mp4` 可產出。進行中：
I2V 真動態（動作幅度有硬上限，見 ADR-012）、才藝軌（跳舞／演奏，角色型態可選四足或人形）。

## 授權

程式碼 MIT。使用的模型各有授權——Z-Image Turbo 為 Apache 2.0（可商用）。
