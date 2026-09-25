# AI Shorts 地端產線

**把一句題材變成可發布的直式短影片，全程在自己的電腦上跑，不呼叫任何雲端 API。**

```
一句話 ──► 分鏡腳本 ──► 分鏡圖 ──► 旁白 ──► 1080x1920 mp4
```

一支 15 秒的成片約 40 分鐘，**每支片的邊際成本是零**。

<p align="left">
  <img src="Document/assets/frame-ep001.jpg" width="220" alt="成片畫格">
  <img src="Document/assets/shot-01.jpg" width="140" alt="分鏡 1">
  <img src="Document/assets/shot-04.jpg" width="140" alt="分鏡 4">
</p>

<sup>左：成片畫格（運鏡推進 + 燒錄字幕）。中右：同一集的兩個分鏡——同一個角色、同樣的項圈與鈴鐺。</sup>

---

## 這個專案在解什麼問題

用 AI 做短影片不難，難的是**做成一個能持續產出的系列**：

- **角色會飄** —— 每一張圖都是重新生成的，上個鏡頭和這個鏡頭不是同一隻貓，觀眾不會追蹤
- **成本會累積** —— 用雲端 API 每支片都要付錢，題材試錯的成本會讓人不敢多試
- **流程會斷** —— 腳本、出圖、配音、合成各用各的工具，每支片都要手動搬檔案

這個專案的做法是：**一條從題材到 mp4 的完整管線，跑在一台 16GB 的 MacBook 上，角色定義成可重用的設定檔。**

角色不是寫死的。`characters/<id>/character.json` 定義外觀、性格、型態，
`presets/style-v*.json` 鎖住出圖參數。換一個角色＝多一個資料夾，管線不用動。

repo 內附的橘貓「小橘」是**第一個示範角色**，不是這個系統的全部。

## 功能

- **編劇**：題材 → 分鏡腳本。編劇 → 監製審稿 → 改稿 → **程式硬檢查 → 自動修復**（最多 3 輪）
- **12 項不依賴 LLM 自評的硬檢查**：旁白字數、鏡頭長度、動態姿勢、場景完整度、提示詞重複、運鏡與句式塌陷……模型可以說自己寫得很好，但字數是數得出來的
- **出圖**：角色外觀自動內嵌每個鏡頭，`seed = base_seed + shot.id` 讓每格可單獨重抽
- **旁白**：中文 TTS，自動依鏡頭長度調整語速
- **合成**：運鏡（含緩動）、方向性轉場、燒錄中文字幕、混音、輸出 1080x1920
- **兩種腳本類型**：故事劇情（敘事拍子）／才藝展示（節拍段落）
- **兩種角色型態**：四足動物／人形，決定角色能做哪些動作
- **可續跑**：任一階段中斷都能接著跑；重做某一格＝刪掉那個檔案
- **Web 介面 + CLI**：同一套核心，兩種操作方式

---

## 需求

| | 最低 | 說明 |
|---|---|---|
| 作業系統 | **macOS（Apple Silicon）** | 用到 Metal 加速與系統內建 TTS |
| 記憶體 | **16 GB** | 一次只載一個重模型 |
| 磁碟 | **25 GB** | 模型檔約 23 GB |
| 網路 | 只在安裝時需要 | 之後完全離線 |

> **為什麼綁 macOS**：出圖用的 Draw Things 只有 Apple 平台版本，旁白用的是系統內建語音。
> 換 Linux + NVIDIA 需要另寫一個出圖後端（介面已經隔離好，見 `studio/imagegen.py`）。

## 安裝

### 1. 基礎工具

```bash
# Homebrew（已有請跳過）
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

brew install ffmpeg ollama uv
```

### 2. 語言模型（編劇用）

```bash
ollama serve          # 另開一個終端機視窗讓它常駐
ollama pull qwen3:8b  # 5.2 GB
```

### 3. 出圖模型

1. 從 [drawthings.ai](https://drawthings.ai) 下載 **Draw Things**（免費）
2. 開啟後建立一個專案，左側 **設定 → 模型**
3. **Manage Models** 搜尋 `Z-Image`，下載 **Z-Image Turbo 1.0 (8-bit)**
   實際會下載三個檔案共約 10 GB：主模型、文字編碼器、VAE

### 4. 開啟 Draw Things 的 API

```
設定 → 所有 → HTTP API 伺服器
  協議   選 HTTP       ← 預設是 gRPC，一定要改
  啟用   打開
  埠     7860
```

驗證：

```bash
curl -s http://127.0.0.1:7860/sdapi/v1/options | head -c 100
```

有回傳 JSON 就成功了。

### 5. 專案

```bash
git clone https://github.com/Novie131/AIvideos.git
cd AIvideos
uv sync
```

### 6.（選用）影片模型

要讓角色真的動起來，再下載 **Wan 2.2 TI2V 5B (8-bit)**（約 12.8 GB）。
**不裝也能完整產出影片**，只是畫面靠運鏡而非角色本身的動作。

---

## 使用

### Web 介面

```bash
./run.sh          # http://127.0.0.1:8765
```

左欄輸入題材、選類型與角色型態 → 生成腳本 → 編輯分鏡 →
按 **① 出圖 → ② 配音 → ③ 合成**，完成後可直接在頁面上播放。

### 命令列

```bash
uv run python scripts/make_script.py --slug ep001 --idea "橘貓穿越古埃及" --duration 15
uv run python -m studio.imagegen ep001     # 約 162 秒/張
uv run python -m studio.tts      ep001     # 數秒
uv run python -m studio.render   ep001     # 約 20 秒

open projects/ep001/out/final.mp4
```

才藝類型：

```bash
uv run python scripts/make_script.py --slug ep002 \
  --type performance --form anthro \
  --idea "橘貓跳街舞" --music "YouTube 音訊庫" --duration 15
```

重做某一格就刪掉對應檔案再跑一次，或加 `--force` 全部重做。

### 產出結構

```
projects/<slug>/
  idea.txt          題材
  script.json       分鏡腳本
  critique.md       監製審稿意見
  shots/            分鏡圖 768x1344
    face/           512x512 臉部裁切（訓練集候選）
    manifest.json   每張圖的 seed 與出圖參數
  audio/            旁白 wav
  out/final.mp4     成片
```

---

## 建立自己的角色

```bash
mkdir -p characters/my-character/{ref,dataset}
```

`characters/my-character/character.json`：

```jsonc
{
  "name": "角色名",
  "appearance_en": "英文外觀描述，會被完整內嵌進每個鏡頭的提示詞",
  "appearance_zh": "中文外觀描述，給人看的",
  "personality": "性格，影響編劇寫出來的旁白語氣",
  "note": "每個 image_prompt 都必須完整包含 appearance_en，不可簡寫或省略。",
  "forms": {
    "quadruped": { "label": "四足", "modifier": "",
                   "can": "走、跑、跳、撲", "cannot": "人類舞步、彈鋼琴" },
    "anthro":    { "label": "人形",
                   "modifier": "anthropomorphic, standing upright on two hind legs, …",
                   "can": "跳舞、彈奏樂器", "cannot": "自然的四足動作" }
  }
}
```

`appearance_en` 越具體，角色越穩。程式會**逐格檢查**每個提示詞有沒有帶全這些特徵。

`forms` 的 `cannot` 會送進編劇提示詞，讓模型不要寫出這個型態做不到的動作。

---

## 管線與實測效能

```
  idea.txt
     │  ① scriptgen   Ollama qwen3:8b          451 s/集
     │     編劇 → 監製 → 改稿 → 硬檢查 → 自動修復×3
     ▼
  script.json ──────────────┬──────────────┐
     │  ② imagegen          │ ③ tts        │
     │     Draw Things      │   系統 TTS   │
     │     162 s/張         │   3.7 s/集   │
     ▼                      ▼              │
  shots/*.png          audio/*.wav         │
     └──────────┬───────────┘              │
                │  ④ render   ffmpeg  20 s/集 ◄┘
                ▼
        out/final.mp4    1080x1920 · H.264 · AAC
```

全部在 M2 / 16GB 上實測。**一支 15 秒 / 8 鏡頭的片約 40 分鐘，90% 花在出圖。**

| 用途 | 模型 | 佔用 |
|---|---|---|
| 編劇 | Qwen3 8B Q4 | 5.2 GB |
| 出圖 | Z-Image Turbo 1.0 8-bit | 10.2 GB |
| 旁白 | 系統內建 TTS | 0 |
| 合成 | ffmpeg | 0 |
| 影片（選用） | Wan 2.2 TI2V 5B 8-bit | 12.8 GB |

---

## 幾個設計決定

**四個核心模組簽名一致。** 純函式 + `on_step(text, pct)` callback，
不知道有 web 也不知道有 CLI；Web endpoint 與 CLI 各自只是 3 行薄殼。

> 這個約束是踩坑換來的。腳本編排原本在 HTTP endpoint 與 CLI 各存在一份，
> 然後就分岔了——CLI 版漏掉「跑完釋放記憶體」，違反了 16GB 的生存條件。

**可續跑靠磁碟，不靠狀態檔。** 目標檔存在就跳過。
任何獨立的進度檔都有跟磁碟不同步的一天。

**參數在設定檔，不在 GUI 裡。** 出圖參數寫成帶版號的 JSON，每次呼叫完整帶入，
絕不依賴 Draw Things 的介面狀態。改風格開新版號，舊圖才追溯得到參數。

**規則性的事收回程式做。** 運鏡指派原本交給 LLM，8B 模型連續三輪拒絕執行，
改成純規則函式。

**硬檢查不依賴 LLM 自評。** 12 項檢查每一項都是踩到才加的：
模型會把提示詞裡的英文範例逐字照抄、會偷加攝影術語破壞畫風、
會在鏡頭太短時寫出被字數砍斷的殘句——這些都靠程式擋。

## 文件

| | |
|---|---|
| [`Document/Spec_v1.md`](Document/Spec_v1.md) | **它是什麼**——架構、流程、資料契約、API、實測數據、已知限制、踩過的坑 |
| [`Document/pipeline-plan.md`](Document/pipeline-plan.md) | **為什麼是這樣**——12 份 ADR，含被推翻的決策與推翻的理由 |

兩份刻意分工。ADR 保留所有走錯的路：SDXL 被 Z-Image Turbo 取代、
Kokoro TTS 被系統內建語音取代、「16GB 記憶體會爆」這個假設被實測推翻
（出圖用的是記憶體映射，瓶頸其實是磁碟頻寬）。**歷史比結論值錢。**

## 已知限制

- **減法特徵畫不出來。** 「右耳缺一小塊」這類「少一塊」的特徵，擴散模型 5/5 失敗；
  加法特徵（項圈、鈴鐺）5/5 成功。設計角色時優先用加法特徵
- **影片模型的動作幅度有上限。** 提高強度會先毀掉角色才換到動作，
  目前靠「把一個大動作拆成連續三格」讓觀眾腦補連成流暢動作
- **Draw Things 必須開著並手動啟用 API**，無法完全無人值守
- **ffmpeg 若未編入 libass／freetype**，字幕改用 Pillow 畫成 PNG 再疊圖（本專案已如此實作）

## 授權

程式碼 MIT。使用的模型各有授權，Z-Image Turbo 為 Apache 2.0（可商用）。
