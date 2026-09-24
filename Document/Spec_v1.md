# AI Shorts 地端產線 — 系統規格書

- 版本：**Spec v1**
- 日期：2026-09-25
- 對應程式碼：`feat/pipeline-complete` 分支
- 關係文件：[`pipeline-plan.md`](pipeline-plan.md) —— **決策的來源與推翻紀錄在那份，這份只描述「現在長什麼樣」**

> **這份文件的定位**：規格書回答「它是什麼、介面是什麼、實測多快」；
> `pipeline-plan.md` 回答「為什麼是這樣、當初試過什麼、為什麼被推翻」。
> 兩份不重複。要改架構先讀 ADR，要接程式先讀這份。

---

## 1. 系統目的與硬約束

在一台 **MacBook M2 / 16GB** 上，**完全免費**、**完全地端**地把一句題材變成可以發布到
YouTube Shorts 的 1080x1920 影片，並讓固定角色（橘貓「小橘」）跨集維持同一個長相。

| 約束 | 內容 | 可否放寬 |
|---|---|---|
| 硬體 | M2 / 16GB unified memory | 不可 |
| 成本 | 零邊際成本 | 不可 |
| 地端 | 推論全部在本機 | **不可**（一次性訓練任務可例外） |
| 品質 | 要能真的發布，不是 demo | 不可 |

---

## 2. 架構

### 2.1 分層

```
┌─ 介面層 ──────────────────────────────────────────────────────┐
│  studio/static/     index.html · app.js · style.css            │
│  studio/server.py   FastAPI :8765 · 23 endpoint · SSE 推播      │
│  scripts/           make_script.py（CLI 薄殼，除錯用）           │
├─ 編排層 ──────────────────────────────────────────────────────┤
│  studio/jobs.py     序列佇列，**固定單 worker**                  │
├─ 核心層（四模組，彼此不知道對方存在）─────────────────────────┤
│  scriptgen.py  題材 → script.json                               │
│  imagegen.py   script.json → shots/*.png                        │
│  tts.py        script.json → audio/*.wav                        │
│  render.py     三者 → out/final.mp4                             │
├─ 後端層 ──────────────────────────────────────────────────────┤
│  studio/backends.py  外部服務位址（可用環境變數覆蓋）             │
└────────────────────────────────────────────────────────────────┘
          ↓ 外部進程，全部在本機
   Ollama :11434          Draw Things :7860         ffmpeg
   qwen3:8b  5.2G         Z-Image Turbo  10.2G      無 libass/freetype
                          Wan 2.2 TI2V   12.8G      （字幕改用 PNG 疊圖）
```

### 2.2 核心模組的統一形狀

四個模組**簽名一致**，這是刻意的約束，不是巧合：

```python
async def <動詞>_<名詞>(slug, *, force=False,
                        on_step=lambda text, pct=None: None) -> dict
```

| 規則 | 理由 |
|---|---|
| 核心是**純函式**，不 import server、不碰 HTTP | 換介面不用動邏輯 |
| 進度用 **`on_step(文字, 百分比)` callback** | CLI 傳 `print`，Web 傳 `QUEUE.step` |
| **可續跑**：目標檔存在就跳過 | 磁碟就是進度，不另存狀態檔 |
| 每產出一個檔案就立刻寫盤 | 跑到第 4 張掛掉，前 3 張還在 |
| 重做某一項 = **刪掉那個檔案** | 不需要額外的 UI 或旗標 |

> **這個約束是踩坑換來的。** 2026-09-25 之前，腳本編排在
> `server.py` 的 endpoint closure 與 `make_script.py` 各有一份約 45 行的複製，
> 然後就分岔了 —— CLI 版漏掉「跑完釋放 LLM」，違反 ADR-008 的記憶體生存條件。
> 現在編排只有 `scriptgen.generate_script()` 一份，兩邊都是 3 行薄殼。

### 2.3 後端隔離

外部工具只出現在單一檔案裡，對外暴露最小介面：

| 外部工具 | 只出現在 | 對外介面 |
|---|---|---|
| Draw Things | `imagegen.py` | `render_one(shot, preset) -> png` |
| macOS `say` | `tts.py` | `synth_one(text, out, target_sec) -> dict` |
| ffmpeg | `render.py` | `render(slug) -> dict` |
| Ollama | `backends.py` | `ollama_chat(...)` |

換後端 = 改一個檔案，腳本層與合成層不動。位址也不寫死：

```bash
AIV_OLLAMA_URL=…      AIV_DRAWTHINGS_URL=http://192.168.1.50:7860 ./run.sh
```

**注意**：Draw Things 只跑 Apple 平台。外部機器若是 Linux + NVIDIA，
那是**換後端**（要另寫 adapter），不是換網址。

---

## 3. 流程

### 3.1 資料流

```
  projects/<slug>/idea.txt          ← 一句題材
        │
        │ ① scriptgen.generate_script()        Ollama qwen3:8b · 451s
        │   ┌───────────────────────────────────────────────┐
        │   │ WRITER → CRITIC → REWRITE → check() → REPAIR   │
        │   │                      ▲                  │      │
        │   │                      └──── 最多 3 輪 ───┘      │
        │   └───────────────────────────────────────────────┘
        ▼
  script.json ─────────────────────────────┬─────────────────┐
        │                                  │                 │
        │ ② imagegen.render_shots()        │ ③ tts.synth_    │
        │   Draw Things · 162s/張          │   shots()       │
        │   ← presets/style-v1.json        │   macOS say     │
        │   ← characters/<id>/             │   3.7s/集       │
        ▼                                  ▼                 │
  shots/shot-NN.png   768×1344       audio/shot-NN.wav       │
  shots/face/*.png    512×512（訓練集候選）                    │
  shots/manifest.json（seed / 參數追溯）                        │
        │                                  │                 │
        └──────────────┬───────────────────┘                 │
                       │ ④ render.render()   ffmpeg · 20s ◄──┘
                       │   Ken Burns → xfade 轉場 → 疊字幕 → 混音
                       ▼
             out/final.mp4    1080×1920 · H.264 · AAC
```

### 3.2 合成階段的內部順序

順序有意義，不能調換：

```
  每個 shot ── zoompan（Ken Burns，smoothstep 緩動）──► clip-NN.mp4
                （不含字幕）                              長度 = duration + 0.3s
        │
        ▼ xfade 串接，轉場型態由「前一鏡的 camera」決定
     v.mp4        總長 = Σ duration_sec（多出的 0.3s 被下一段吃掉）
        │
        ▼ overlay 疊字幕，每句用 enable='between(t,a,b)' 卡在自己的時間窗
     vs.mp4
        │
        ▼ 音軌 concat（旁白 + apad 補靜音到各 shot 長度）→ 混音
   out/final.mp4
```

**為什麼字幕要在轉場之後才疊**：若跟畫面一起 xfade，轉場那 0.3 秒會看到
前後兩句字幕互相溶接。分開處理後每句字幕硬切進出，乾淨。

**為什麼轉場不用 fade**：主體（貓）都在畫面中央，交叉溶接會出現
「兩隻貓、四隻眼睛」的疊影。改用方向性推移（`smoothleft` / `smoothup` …），
且方向延續前一鏡的運鏡。

---

## 4. 資料契約

### 4.1 `projects/<slug>/script.json`

```jsonc
{
  "type": "story",              // story | performance（performance 結構未定）
  "title": "...",
  "hook": "...",
  "character": "小橘",
  "shots": [{
    "id": 1,
    "duration_sec": 2,          // 1.5~2.5；check() 上限 2.5
    "action": "...",            // 中文，畫面上發生什麼
    "image_prompt": "...",      // 英文。必須含 appearance_en 全文 + 動態姿勢線索
    "narration": "...",         // 主角第一人稱心聲。**可為空字串**（留白）
    "subtitle": "...",          // ≤ 12 字
    "camera": "push_in"         // push_in|pull_out|pan_left|pan_right|static
  }]
}
```

### 4.2 `presets/style-v<N>.json` —— 出圖參數的單一事實來源

**檔名帶版號。改風格開 v2，不改舊檔** —— 風格一變，先前所有圖與訓練集作廢，
版號留著才分得出某批圖是哪組參數出的。

| 區塊 | 內容 |
|---|---|
| `output` | `main` 768×1344、`face_crop` 512×512、`dir: "shots"` |
| `model` | checkpoint / sampler / steps / cfg / shift / cfg_zero_star / clip_skip / batch_size |
| `seed` | `strategy: base_plus_shot_id` —— **seed = base_seed + shot.id** |
| `prompt` | `style_suffix`（鎖死）、`negative` |
| `character_lock` | 驗收時必須保住的特徵與門檻 |

**seed 策略的意義**：同集每個 shot 各自穩定；整集可用同一個 `base_seed` 重現；
要重抽某個 shot 只改它自己的偏移，不動其他 shot。

### 4.3 `projects/<slug>/shots/manifest.json`

每批圖的來源追溯。`.gitignore` 把 png 擋在版控外，manifest 是唯一能回答
「這張圖是哪組參數出的」的線索，也是 `dataset/README.md` 要求的訓練集規約。

```jsonc
{ "preset": "style-v1", "preset_status": "provisional_zimage",
  "checkpoint": "...", "sampler": "...", "steps": 8, "cfg": 1.0,
  "base_seed": 20260920, "generated": "2026-09-24T…",
  "shots": [{ "id": 1, "file": "shot-01.png", "sec": 162.6, "seed": 20260921 }] }
```

### 4.4 角色

`characters/<id>/character.json` 的 `appearance_en` **必須被每個 `image_prompt`
完整內嵌，一字不可省**；`scriptgen.consistency()` 會逐格檢查。

`dataset/` 收人工精選的訓練集候選（觸發詞 `xj_cat`，**固定外觀特徵不寫進 caption**）；
`ref/` 收定妝照。兩者是 `.gitignore` 的明文例外 —— 人工精選、不可重生，必須進版控。

---

## 5. HTTP API

Base `http://127.0.0.1:8765`。**所有長任務都回 `{"job": "<id>"}` 後立刻返回**，
進度走 SSE。

| Method | Path | 說明 |
|---|---|---|
| GET | `/api/health` | Ollama / Draw Things / ffmpeg 可用性 |
| GET | `/api/system` | 記憶體、swap、目前載入的模型 |
| POST | `/api/unload` · GET/POST `/api/autounload` | 手動 / 自動釋放 LLM |
| GET | `/api/models` · `/api/characters` · PUT `/api/characters/{cid}` | |
| GET | `/api/projects` | 列表，含 `images` / `audios` / `video` 完成度 |
| GET/PUT | `/api/projects/{slug}` | 單一專案，含 `media`（縮圖、影片、manifest 路徑） |
| **POST** | **`/api/script`** | 題材 → script.json |
| **POST** | **`/api/images`** | `{slug, preset?, only?, force?}` |
| **GET/POST** | **`/api/voices` · `/api/tts`** | 可用中文語音 / 產旁白 |
| **POST** | **`/api/render`** | `{slug, bgm?, force?}` |
| POST | `/api/endings` · `/api/apply_ending` | 結尾 A/B/C 變體 |
| GET | `/api/jobs` · POST `/api/jobs/{jid}/cancel` | 佇列 |
| GET | `/api/events` | SSE：`job` / `sys` / `notice` |
| — | `/static/*` · **`/media/*`** | 前端資源 / **產出物（png、mp4）** |

---

## 6. 實測效能

全部在 M2 16GB 上量得，**不是規格推算**。

| 階段 | 工具 | 實測 | 備註 |
|---|---|---|---|
| 腳本 | qwen3:8b | **451 s/集** | 四次 LLM 呼叫（含 1 輪修復） |
| 出圖 | Z-Image Turbo 1.0 8-bit | **162 s/張** | 冷啟動首張 313 s |
| 配音 | macOS `say` | **3.7 s/集** | 零安裝 |
| 合成 | ffmpeg | **20 s/集** | |
| （選）I2V | Wan 2.2 TI2V 5B | **128 s / 1 秒影片** | 384×640 · 25 步 |

**一支 15 秒 / 8 鏡頭的片約 40 分鐘，其中 90% 是出圖。**

### 記憶體

| 觀察 | 數值 |
|---|---|
| 日常可用 | 4~5.5 GB，swap 1.5 GB |
| 出圖時 Draw Things RSS | **0.1 GB** —— 用 mmap + JIT 權重載入，不整包佔 RAM |
| 出圖時瓶頸 | **磁碟頻寬**（冷啟動讀取 236 MB/s），不是 RAM |
| I2V 時 swap | 升至 4 GB —— 影片模型必須同時持有所有影格的潛在表示 |

> ADR-008 原本假設「模型整包佔住 RAM，所以必須清場」。
> **對圖像模型是錯的**（mmap），**對影片模型才成立**。

---

## 7. 已知限制

| # | 限制 | 影響 | 現況 |
|---|---|---|---|
| 1 | **右耳白色缺角出不來** | §3 兩個驗收點之一，5/5 失敗 | 減法特徵，擴散模型畫不出「少一塊」。加法特徵（鈴鐺、項圈）5/5 成功。**辨識點設計需重排** |
| 2 | **I2V 動作幅度有硬上限** | 做得了呼吸感，做不了舞蹈 | strength 0.6→0.95，動作僅 +54%，失真 +92%（0.95 時項圈鈴鐺消失）。見 ADR-012 |
| 3 | **才藝軌未定** | 跳舞路線卡住 | 四足貓做不了人類舞步；pose-driven 技術全是人體骨架。卡在兩個設定決定：是否開擬人雙足角色、音樂來源 |
| 4 | Draw Things 必須開著 App 並手動啟用 API | 無法無人值守 | 短期接受 |
| 5 | ffmpeg 無 `libass` / `freetype` | 無法用 `subtitles`/`drawtext` | 已用 Pillow 畫 PNG + `overlay` 繞過。**`brew install ffmpeg` 修不了**，formula 本身就沒編進去 |
| 6 | git 無遠端 | 無備份，作品集無法分享 | 待使用者決定 |

---

## 8. 踩過的坑（接手前先讀）

| 坑 | 症狀 | 正確做法 |
|---|---|---|
| **Draw Things 沒有 `/sd-models`、`/samplers`** | 404 | 只有 `/options` 與 `/txt2img`、`/img2img`。參數從 `/options` 讀（83 欄位） |
| **CFG 的欄位名是 `guidance_scale`** | 參數靜默失效 | 不是 A1111 的 `cfg_scale` |
| **UI 開關的截圖判讀不可信** | `cfg_zero_star`、`resolution_dependent_shift` 都被看成啟用，實際 `false` | **一律以 `/options` 為準** |
| **Wan 要求長寬可被 32 整除** | 送 672 **不報錯**，悄悄改成 640，init 圖尺寸對不上被丟棄 → **靜默退回 T2V**，產出完全無關的畫面 | 事先對齊到 32 的倍數 |
| **`init_images` 尺寸必須等於 `width`/`height`** | 422 | Draw Things 不會自己縮放 |
| **`strength: 1.0` = 忽略起始圖** | I2V 變 T2V | I2V 用 0.85 |
| **Wan 是非蒸餾模型** | 12 步產生色階斷層的廢圖 | 要 25 步。不能套用 Z-Image Turbo 的 8 步直覺 |
| **`say -v ?` 會列出未安裝的語音** | 產出 0.02 秒空檔案且**不報錯** | `tts.voices()` 實際試合成來篩；`synth_one()` 擋空音檔 |

---

## 9. 決策索引

細節全在 [`pipeline-plan.md`](pipeline-plan.md) §4。

| ADR | 主題 | 狀態 |
|---|---|---|
| 001 | LLM：Ollama + Qwen3 8B Q4 | ✅ |
| 002 | 圖像後端：Draw Things | ✅ 理由已更新 |
| 003 | 底模：SDXL → **Z-Image Turbo** | ⛔ v1 已推翻 |
| 004 | 一致性：IP-Adapter → **Qwen-Image-Edit** | ⛔ v1 已推翻，且 ADR-010 後延後 |
| 005 | LoRA 訓練：必經 → 保險方案 | ⬇️ 降級 |
| 006 | 動態：ffmpeg Ken Burns 先行 | ✅ ADR-012 補強 |
| 007 | TTS：Kokoro → **macOS `say`** | ⛔ 實測後改用 `say` |
| 008 | 記憶體：序列載入 | ⚠️ 前提對影像模型不成立 |
| 009 | 已排除項目 | ✅ |
| 010 | 目標重排：先發一支片 | ✅ 期限已解除 |
| 011 | 內容分兩軌：故事 / 才藝 | ✅ |
| 012 | 動作感靠切與出圖姿勢，不靠 I2V 參數 | ✅ |

---

## 10. 快速開始

```bash
# 前置：Ollama 已載 qwen3:8b；Draw Things 開著且 設定→所有→HTTP API 伺服器
#      協議選 HTTP、port 7860

./run.sh                                             # http://127.0.0.1:8765

# 或走 CLI（每個模組都有薄殼）
uv run python scripts/make_script.py --slug ep004 --idea "橘貓穿越古埃及" --duration 15
uv run python -m studio.imagegen ep004               # 約 162s × shot 數
uv run python -m studio.tts      ep004               # 數秒
uv run python -m studio.render   ep004               # 約 20s
open projects/ep004/out/final.mp4
```

重做某一項就刪掉對應的檔案再跑一次；或加 `--force`。
