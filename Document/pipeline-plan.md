# AI Shorts 地端產線 — 規劃與研究方向

- 版本：**v2.2**
- 更新日期：**2026-09-22**
- 上一版：v2（2026-09-20，Z-Image + Qwen-Edit 路線，本版沿用，僅補實測）
- 再上一版：v1（2026-09-08，SDXL + IP-Adapter 路線，v2 已部分推翻）

**v2.2 改了什麼**：新增 ADR-010（目標重排：先發一支片，bake-off 延後）與 §10（兩週計畫）。這是本文件第一次因為**目的改變**而非技術發現而調整，詳見 ADR-010。

**v2.1 改了什麼**：Q0 實測完成（版本過關、模型全未下載），補上 §2 記憶體實況與 §6.2 清場步驟，§6.6 三件事已落成檔案。**沒有推翻任何 v2 的決策。**

---

## 0. 這份文件的用途與閱讀規則

這是本專案的**單一事實來源**。任何人（或任何 AI session）接手時，先讀這份，不要重新從 `Document/ChatGPT.txt` 推導 —— 那是最初的外部建議，已被多次修正。

每個結論都標記可信度，不要把推定當事實：

| 標記 | 意思 |
|---|---|
| `[已驗證]` | 在這台機器上實際跑過 / 直接看到檔案 |
| `[官方規格]` | 官方文件或 release note 說的，但沒在這台機器上驗證 |
| `[推定]` | 從規格推算，沒有直接來源 |
| `[待驗]` | 假設，必須實測才能定 |

**改動規則**：推翻任何決策時，不要刪掉舊決策 —— 移到 §4 的 ADR 並標記「已推翻」，寫清楚為什麼。歷史比結論值錢。

---

## 1. 目標與硬約束

**目標**：產出 1080x1920 的 YouTube Shorts，題材含貓狗、悲情、擬人水果、穿越時空。以**固定角色形成 IP**（首位角色：橘貓「小橘」）。

**硬約束**：

| 約束 | 內容 | 可不可以放寬 |
|---|---|---|
| 硬體 | MacBook M2 / 16GB unified memory | 不可 |
| 成本 | 完全免費 | 不可 |
| 地端 | 全部在本機跑 | **一次性任務可例外**，見 §6.5 |
| 品質 | 要能真的發布，不是 demo | 不可 |

**「地端」這條為什麼可以有例外**：這條約束真正要保護的是「每支片的邊際成本為零 + 不依賴外部 API 存活」。一次性的訓練任務（每個角色只做一次）拿去免費雲端跑，不破壞這個目的。但**推論永遠留在地端**，沒有商量餘地。

---

## 2. 現況快照（2026-09-20）

### 已完成

| 階段 | 狀態 | 檔案 |
|---|---|---|
| 本機 Web Studio（FastAPI，:8765） | ✅ 可用 | `studio/server.py`、`run.sh` |
| 編劇 → 監製審查 → 自動改寫 | ✅ 可用 | `studio/scriptgen.py` |
| 硬性檢查（旁白 4.5 字/秒、角色外觀一致性、簡體字偵測） | ✅ 可用 | `scriptgen.py:151` `check()` |
| 運鏡自動分配 | ✅ 可用 | `scriptgen.py:212` `assign_cameras()` |
| 結尾 A/B 變體 | ✅ 可用 | `scriptgen.py` `ENDINGS` |
| 序列工作佇列（單 worker） | ✅ 可用 | `studio/jobs.py` |
| 後端健康檢查 + 記憶體監控 + 主動 unload | ✅ 可用 | `studio/backends.py` |
| 角色設定 | ✅ 小橘已鎖定 | `characters/orange-cat/character.json` |
| 腳本產出 | ✅ ep001（15秒）、ep002（30秒） | `projects/` |

### 未完成（從這裡開始就是空白）

| 階段 | 狀態 |
|---|---|
| 文生圖 | ❌ 零程式碼 |
| 角色一致性機制 | ❌ 未選定（本文件要解決的主題） |
| TTS 配音 | ❌ 未安裝、零程式碼 |
| ffmpeg 合成（Ken Burns / 字幕 / 混音） | ❌ 零程式碼（只在健康檢查出現過） |
| BGM / 音效 | ❌ 未規劃 |
| 上片 metadata | ❌ 未規劃 |

### 環境 `[已驗證]`

```
ffmpeg   /opt/homebrew/bin/ffmpeg
ollama   /opt/homebrew/bin/ollama    （已載 qwen3:8b，5.2GB）
uv       /opt/homebrew/bin/uv        （Python 3.12，不碰系統 3.9.6）
Draw Things.app  /Applications/      1.20260716.0  ← Q0 已解，見下
macOS    Darwin 25.6.0
git      已初始化（2026-09-20，main 分支，僅本機無遠端）
```

**Q0 實測結果（2026-09-20）** `[已驗證]`：

| 項目 | 結果 |
|---|---|
| Draw Things 版本 | **1.20260716.0**，遠高於 Qwen-Edit 2509 所需的 1.20250930.0 → **版本不是障礙** |
| 已下載模型 | **一個都沒有**。容器 `~/Library/Containers/com.liuliu.draw-things/` 總共僅 912KB，`Models/` 與 `Downloads/` 全空，全碟搜尋無 `.ckpt` / `.safetensors` |
| API Server | 未開（:7860 無回應） |
| 磁碟餘裕 | 133GB，下載約 22GB 的三個模型沒問題 |

**這改寫了 §6.2 的性質**：原本把「下載模型」寫成一行前置條件，實際上它是 bake-off 的**主要工作量**。

**記憶體實況** `[已驗證]`：`/api/system` 回報總 17.2GB、**可用僅 4.3GB、swap 已用 1.73GB**。
被 VS Code + 多個 claude 進程 + Discord + 一個 Virtualization VM 累積吃掉 12.9GB（非單一巨獸）。
**照這個狀態，Qwen-Edit 的 11GB 連載都載不進去。** 見 §6.2 清場步驟與 ADR-008 補註。

**腳本產出欄位完整性** `[已驗證]`：ep002 九個 shot 的 `id/duration_sec/action/image_prompt/narration/subtitle/camera` 全數無缺，每個 image_prompt 385~413 字元且完整內嵌 `appearance_en`。**`imagegen.py` 一寫好就能直接餵，不用回頭補腳本。**（實際總長 27 秒、9 shots，非標稱 30 秒，不影響。）

**一句話總結進度**：腳本層做完且做得紮實，**影像層一行都沒有**。手上只有 JSON，離 mp4 差三個模組。

---

## 3. 核心問題

> **在 M2 16GB、免費、地端的條件下，讓「小橘」在每個 shot、每一集之間長得一模一樣。**

這不是加分項，是**存亡點**，理由有二：

1. **商業面**：YouTube 2025-07 的 inauthentic content 政策，大量重複無差異的 AI 內容不能營利。固定角色 IP 是對沖這條政策的路線。
2. **複利面**：角色能延續，觀眾才會追。每集換一隻不同的橘貓，這條線就只是內容農場。

小橘的辨識特徵（`character.json` 已鎖定，**任何方案都必須保住這幾項**）：

- 圓臉橘白虎斑
- 額頭 **M 形條紋**
- **右耳尖端一小塊白色缺角** ← 最難保、最關鍵
- 琥珀色大眼
- 磨損的深藍色布項圈 + **黃銅小鈴鐺** ← 第二難保
- 尾巴末端白色

**驗收時盯這兩項**：右耳缺角、鈴鐺。這兩個守得住，其他都守得住。

---

## 4. 架構決策紀錄（ADR）

### ADR-001 — LLM：Ollama + Qwen3 8B Q4 `[已驗證]` ✅ 沿用

中文能力鎖定在具體型號，不是泛稱 4B~8B。佔 5.2GB。已實際產出兩份可用腳本。

### ADR-002 — 圖像後端：Draw Things `[官方規格]` ✅ 沿用（**理由已更新**）

v1 的理由是「它有 SDXL 的 IP-Adapter」。ADR-003/004 推翻底模後，那個理由失效了，但結論**更強**了。現在的理由：

1. **不用 PyTorch** — 自己的 Metal 推論引擎（s4nnc）。torch-MPS 在 16GB 上有兩個慢性病：部分 op 無 Metal 實作會 fallback 回 CPU（慢 10 倍）、記憶體不釋放導致 swap。
2. **它是 Mac 上唯一同時支援 Z-Image + Qwen-Image-Edit 2509 + 這兩者 LoRA 訓練的原生應用**。ComfyUI 要在 16GB 湊齊這三件事是地獄。
3. **有 HTTP API（:7860）** — 管線只需要「能程式呼叫」，這點滿足。
4. 內建訓練器，見 ADR-005。

**已知代價**（接受，但要管理）：

- App 必須開著、API 要手動勾 → **不能 headless、不能排程**。見 Q6。
- 模型設定活在 App 裡不在 repo 裡 → 可重現性差。
  **緩解**：所有參數（checkpoint / steps / cfg / seed / 負面詞）一律寫進專案的 style preset，每次呼叫都從 API 帶過去，**絕不依賴 App 的 UI 狀態**。
- API 文件比 A1111 少。

**不綁死的做法**：Draw Things 關在 `studio/imagegen.py` 後面，對外只暴露
`render_shot(prompt, seed, refs) -> png`。換後端 = 改一個檔案，腳本層與合成層不動。

### ADR-003 — 底模：SDXL → **Z-Image Turbo** ⛔ v1 已推翻

**v1 決定**：SDXL，768x1344 生成 → 1.4x upscale → 1080x1920。

**推翻理由** `[官方規格]`：Z-Image Turbo（阿里，6B）在每個維度都贏：

| | SDXL | Z-Image Turbo |
|---|---|---|
| 參數 | 3.5B | 6B |
| 量化後佔用 | ~6GB+ | **~4GiB（6-bit）** |
| 步數 | 25~30 | **8** |
| 16GB 上速度 | 數十秒 | **2~3 秒/張** |
| 提示詞遵循 | 普通 | 強 |
| 中文字渲染 | 不行 | 可以 |
| Draw Things 最佳化 | 一般 | 比其他實作快 54% |

**2~3 秒/張的意義**：一個 shot 可以重抽 20 次都不心疼。這直接改變工作流 —— 從「珍惜每一次生成」變成「暴力篩選」。

**Z-Image 的缺口** `[官方規格]`：

- ❌ **Z-Image-Edit 尚未釋出**
- ❌ **ControlNet 尚未支援**（Draw Things issue #73 仍開著）
- 一致性只能靠固定提示詞區塊 + 固定 seed + 參考圖 → **會飄**

所以 Z-Image Turbo 是最好的**產生器**，不是一致性的答案。一致性交給 ADR-004。

### ADR-004 — 一致性：IP-Adapter → **Qwen-Image-Edit 2509** ⛔ v1 已推翻

**v1 決定**：SDXL + IP-Adapter。

**推翻理由** `[官方規格]`：出現了專門做這件事的模型。**Qwen-Image-Edit 2509**（Draw Things 自 1.20250930.0 起支援）：

- 吃**多張輸入圖**，換場景 / 換姿勢 / 換服裝時保住主體身分
- 官方說法：直接取代 FLUX Kontext 與所有前代
- 內建 ControlNet（depth / lineart / pose）
- 代價：**20B 模型**，FP16 約 45GB，**INT4 約 11GB** → 16GB 上塞得下，但很緊

**備選**：FLUX.1 Kontext dev（12B），同概念但更輕，4-bit `[推定]` ~7GB。若 Qwen-Edit 在 16GB 上慢到不可用，這是降級選項。

**關鍵未知**：沒有任何公開數據測過「M2 16GB + Qwen-Edit 2509 INT4」的秒/張。**這個數字決定整條線可不可行**，必須實測（§6）。

### ADR-005 — LoRA 訓練：必經 → **保險方案** ⬇️ 降級

**v1 認知**：M2 16GB 訓練很痛苦，需自建 20~40 張資料集。

**修正** `[官方規格]`：那是 kohya_ss / diffusers 走 MPS 那條路的實況（macOS 支援本來就差）。Draw Things 自己的訓練器是另一回事：

- 支援 SD1.5 / SDXL / FLUX.1 / **FLUX.2 [klein] 4B、9B / Z Image / Qwen Image / ERNIE**
- **Minimal / Balanced 記憶體模式 + Just-in-Time 權重載入 + Metal FlashAttention v2**（省 20~25% RAM）
- 官方門檻降到 **8GB RAM**
- 舊的「SDXL 512 需 ~12GB」數字只適用 SDXL，不代表 Z-Image / Qwen

**新定位**：訓練不是必經之路，是 §6 全掛之後的保險。而且屆時是訓 6B 的 Z-Image LoRA，不是原本那個 SDXL 苦工。

### ADR-006 — 動態：ffmpeg Ken Burns（一階段）✅ 沿用

先用 ffmpeg 的推拉搖做假動態就上架。LTX-MLX I2V 列為第二階段可插拔模組，先 benchmark 再決定。理由：先把整條線打通拿到回饋，比先把單一環節做到最好重要。

### ADR-007 — TTS：Kokoro v1.1-zh `[待驗]` ✅ 沿用但未驗證

Piper 中文品質差，已排除。Kokoro **尚未安裝、尚未試聽**。見 Q4。

### ADR-008 — 記憶體策略：序列載入 ✅ 沿用（**強化**）

16GB unified memory 的唯一活路：**一次只載一個重模型，每階段用完立刻 unload**。`studio/jobs.py` 的單 worker 佇列就是為此設計。

新的記憶體預算表（含 §5 的新模型）：

| 元件 | 佔用 | 來源 |
|---|---|---|
| macOS 基線 | ~3~4GB | `[推定]` |
| Ollama Qwen3 8B Q4 | 5.2GB | `[已驗證]` |
| Z-Image Turbo 6-bit | ~4GiB | `[官方規格]` |
| Qwen-Image-Edit 2509 INT4 | ~11GB | `[官方規格]` |
| FLUX Kontext dev 4-bit | ~7GB | `[推定]` |
| Kokoro TTS | <1GB | `[推定]` |
| ffmpeg | 可忽略 | — |

**結論**：`Qwen-Edit(11) + 基線(4) = 15GB`，已經貼著天花板。**任何兩個重模型同時在記憶體裡都會 swap。** 序列載入不是最佳化，是生存條件。

**2026-09-20 實測補註** `[已驗證]`：上表「macOS 基線 ~3~4GB」是**乾淨開機**的數字，不是日常工作狀態。實際量到日常狀態下被佔用 12.9GB、可用僅 4.3GB、swap 已 1.73GB。

這不是推翻預算表，是補上它漏掉的前提：**那 4GB 基線只有在清場後才成立**。所以「序列載入」這條生存條件要擴充成兩條：

1. 一次只載一個重模型（原有）
2. **跑重模型前必須清場**——關掉 VS Code / Discord / VM 等常駐程式（新增）

§6.4 量 swap 增量時，**必須先記錄清場後的乾淨基線**，否則量到的數字混入了其他程式的佔用，無法判讀。

### ADR-009 — 已排除項目（不要再回頭研究）

| 排除項 | 理由 |
|---|---|
| vLLM | 無 Metal backend，Mac 上根本跑不動 |
| ComfyUI | torch-MPS 記憶體與 CPU fallback；湊齊新模型支援是地獄 |
| 裸 diffusers | 同上，且要自己補 MPS 的洞 |
| 原生 1080p diffusion | 16GB 不可能 |
| 同時載入多模型 | 見 ADR-008 |
| kohya_ss on Mac | macOS 支援差，社群一路踩坑；Draw Things 內建訓練器取代之 |
| IP-Adapter FaceID / InstantID / PuLID / PhotoMaker | **全部基於人臉辨識，對貓無效** |

### ADR-010 — 目標重排：先發一支片，bake-off 延後 ⬆️ 新增（2026-09-22）

**背景**：本專案的目的一直沒有被明確寫下來。釐清後有三項，優先序如下：

1. **作品集** —— 主要讀者是**求職時看作品的工程主管**，次要是同行工程師，最後才是一般人
2. **產出的影片發到 YouTube Shorts**，賺取分潤
3.（隱含）驗證這個題材到底有沒有觀眾

**釐清過程中暴露的三件事**（記錄下來，因為它們會再犯）：

- **舉不出三個同類頻道與其數據** —— 整份計畫建立在直覺上，不是資料。這不是罪，但它決定了第一支片的角色：**那是探針，不是作品**。
- **「給大家看」沒有指定讀者** —— 與上一條是同一個盲點的第二次出現。差別在於，YouTube 的觀眾不由你決定，但作品集的讀者完全由你決定。
- **一份高品質規劃文件 + 零產出，對「找工作」這個主要目的是危險組合** —— 它會被讀成「很會想，還沒交付」。**文件越好，這個對比越刺眼。**

**決策**：§6 的 bake-off 從「下一步」延後。改為用最小成本把整條線端到端打通，發布一支片。

**這推翻了 §6.1 嗎？不。前提變了。**

§6.1 主張「在量到秒/張之前寫任何 `imagegen.py` 都是賭博」。那個論證在「蓋一條可持續的產線」這個目標下**完全正確，現在也依然正確**。但目標換成「兩週內發一支片」之後，推論反向：**這一支片不需要可持續，它需要存在。**

bake-off 不取消，移到第一支片發布之後。屆時它的輸入還更好 —— 會帶著真實的產出經驗進去。

**代價（明確承認，不粉飾）**：第一支片走方案 C（Z-Image Turbo + 鎖死提示詞 + 固定 seed），**角色一致性會飄**。接受。一支 15 秒 5 個鏡頭的片，飄的可見度遠低於它擋住發布所造成的損失。

**三條獨立推理指向同一個動作**（這是採用它的主要理由）：

1. 舉不出同類頻道數據 → 需要真實數據 → 得先發一支
2. 作品集需要看得見的東西 → 得先有一支
3. 求職最忌「只規劃不交付」→ 得先有一支

前提不同，結論相同。

---

## 5. 角色一致性 — 候選方案全景

| # | 方案 | 需訓練 | RAM | 預期一致性 | 狀態 |
|---|---|---|---|---|---|
| **A** | Qwen-Image-Edit 2509（多圖輸入） | ❌ | ~11GB | 最高 | `[待驗]` **首選** |
| **B** | FLUX.1 Kontext dev | ❌ | ~7GB `[推定]` | 高 | `[待驗]` 降級選項 |
| **C** | Z-Image Turbo + 鎖死提示詞區塊 + 固定 seed | ❌ | ~4GiB | 中（會飄） | `[待驗]` 最快最省 |
| **D** | Z-Image / Qwen LoRA（Draw Things 內建訓練器） | ✅ | 訓練 8GB+ | 最高 | 保險方案 |
| **E** | 去背 PNG 素材 + ffmpeg 疊圖 | ❌ | ~0 | **100%** | 保底，永遠可行 |

### 方案 E 補充（保底方案，不要低估它）

小橘做成**透明背景 PNG 素材庫**（數種姿勢 / 表情），AI 只生背景，用 ffmpeg overlay 疊上去。

- **一致性 100%** —— 因為根本是同一張圖，不是「很像」
- 每個 shot 的邊際成本趨近於零
- 代價：畫面變成剪紙 / 紙偶動畫風，姿勢受限，需要去背（alpha matting）
- **這是一種合法的 Shorts 美術風格，不是妥協失敗** —— 很多高播放量頻道就長這樣

A~D 全掛才用，但它保證這個專案有路可走。

### Character-Adapter（研究線索，未納入主線）

論文方法，宣稱可延伸到動物與物件的一致性。但沒有 Draw Things 支援，要跑得自己接 diffusers → 違反 ADR-002/009。**僅列為長期觀察**，見 Q8。

---

## 6. Bake-off 實驗設計 ⏸ **已延後 —— 見 ADR-010 與 §10**

> **2026-09-22 註**：這一節的論證依然成立，但**適用的目標已經改變**。
> ADR-010 把目標改為「兩週內發一支片」，在那個目標下推論反向：
> 這一支片不需要可持續，需要存在。bake-off 移到第一支片發布之後。
> 下面的內容保留不動，屆時直接接著用。

### 6.1 為什麼先做實驗，不先寫程式

§5 全部是規格表，**沒有一條在 M2 16GB 上被驗證過**。一集 8 個 shot：

- 3 分鐘/張 → 24 分鐘/集 → **可接受**
- 15 分鐘/張 → 2 小時/集 → **只能掛整夜，勉強**
- 40 分鐘/張 → **這條路直接死**

這個數字決定整條線的架構。**在量到它之前寫任何 `imagegen.py` 都是賭博。**

### 6.2 前置條件

1. ~~確認 Draw Things 版本 ≥ **1.20250930.0**~~ ✅ **已完成**：實測 1.20260716.0，過關。
2. **下載模型** ← **目前的主要工作量，約 22GB**
   實測**一個都沒下載**。需要：Z-Image Turbo（6-bit ~4GB）、Qwen-Image-Edit 2509（INT4 ~11GB）、FLUX Kontext dev（4-bit ~7GB）。磁碟剩 133GB，空間無虞。
3. 開啟 App → Advanced → 啟用 API Server（HTTP / 7860 / localhost）
4. **清場記憶體** ← **新增，不可略過**
   關掉 VS Code / Discord / Virtualization VM 等常駐程式，用 `/api/system` 確認可用記憶體回到 12GB 以上，並**記下這個乾淨基線**供 §6.4 的 swap 增量對照。不清場則 Qwen-Edit 的 11GB 載不進去。
5. 產出**小橘定妝照**一張（正面、中景、中性背景、特徵全可見）—— 這是所有方案的共同輸入，存放規約見 `characters/orange-cat/ref/README.md`

### 6.3 測試材料

固定 5 個場景，全部取自已完成的 `projects/ep002-egypt-30s/script.json`，避免另編素材：

1. 現代客廳（起點）
2. 金字塔前躺著（遠景 + 小主體 ← 最難）
3. 被埃及工人抱著（與人互動）
4. 金盒中（受限構圖）
5. 臉部特寫（細節驗收用）

### 6.4 量測指標與驗收門檻

| 指標 | 怎麼量 | 門檻 |
|---|---|---|
| **右耳白色缺角保留率** | 5 張目視計數 | ≥ 4/5 |
| **鈴鐺保留率** | 5 張目視計數 | ≥ 4/5 |
| **整體可辨識為同一隻貓** | 目視 | 5/5 |
| **秒/張** | 計時 | ≤ 180 秒 |
| **峰值 RAM / swap** | `/api/system` 既有監控 | swap 增量 < 2GB |

**注意**：`studio/backends.py` 的 `system_stats()` 已經能回報 swap，實驗時直接輪詢記錄，不用另外寫工具。

### 6.5 決策樹

```
A（Qwen-Edit 2509）過門檻？
 ├─ 是 → 定案 A。LoRA 與資料集這件事永遠不用碰。
 └─ 否 ┬ 是「太慢」→ 試 B（FLUX Kontext，更輕）
       └ 是「一致性不足」→ 直接跳 D

B 過門檻？
 ├─ 是 → 定案 B
 └─ 否 → 試 C

C 一致性可接受？
 ├─ 是 → 定案 C（最快最省，理想結果）
 └─ 否 → 走 D：用 C 大量產圖 → 人工篩 25~40 張 → 訓 Z-Image LoRA
          └─ 若地端訓練仍不可行 → 一次性丟 Colab 免費 T4
             （仍免費；每角色一次，不影響單片邊際成本；見 §1 例外條款）

D 也不行 → 走 E（去背 PNG + ffmpeg 疊圖），保證有片可出
```

### 6.6 無論走哪條，現在就要鎖死的三件事 ✅ **已完成（2026-09-20）**

即使最後走 D 需要資料集，只要下面三件事現在就定好，**從 ep001 產出的每一張圖都自動是訓練集候選**，不用回頭重跑：

1. **鎖 style preset**：checkpoint / 風格詞 / 負面詞 / sampler / cfg / seed 策略，寫成檔案。
   *風格一變，先前所有圖都不能混進同一個資料集。*
2. **鎖出圖規格**：主圖 768x1344，**同時另存一張 512 方形臉部裁切**。
   *現在加是零成本，事後補要全部重跑。512 裁切是為了萬一要訓練時，細節有足夠像素。*
3. **鎖資料夾規約**：`characters/orange-cat/dataset/` 收精選圖 + 對應 caption。
   *出圖時順手挑，不要等到要訓練才回頭翻三集的檔案。*

**落成的檔案**：

| 項目 | 檔案 | 內容 |
|---|---|---|
| 1. style preset | `presets/style-v1.json` | 檔名帶版號（改風格開 v2，不改舊檔，讓每批圖都追溯得到參數）。已定案欄位：768x1344、512 臉部裁切、`seed = base_seed + shot.id`。未定案欄位（checkpoint / cfg / sampler / 風格詞 / 負面詞）標 `null`，待 §6.5 填入 |
| 2. 出圖規格 | 同上 `output` 區塊 | — |
| 3. 資料集規約 | `characters/orange-cat/dataset/README.md` | 圖文同名成對、觸發詞 `xj_cat`、**固定外觀特徵不寫進 caption**（寫了等於告訴模型這些特徵可變，訓練就白做）、只收右耳缺角與鈴鐺皆清晰的圖、manifest 記錄來源 preset |
| 附帶 | `characters/orange-cat/ref/README.md` | §6.2 定妝照的規格與存放規約 |

`.gitignore` 已驗證：產出的影音檔（`shots/` `audio/` `out/`）擋在版控外，但 `dataset/` 與 `ref/` 的圖（人工精選、不可重生）確實進得去。

---

## 7. 目標管線（bake-off 定案後）

```
idea.txt
   │
   ▼  [Ollama Qwen3 8B — 已完成]
script.json  ── 編劇→監製→改寫→硬檢查→運鏡→結尾變體
   │
   │  ◀── unload LLM（keep_alive=0）
   ▼  [Draw Things — 待建 studio/imagegen.py]
shots/*.png  ── 768x1344，依 §6.5 定案的方案產出
   │           另存 shots/face/*.png（512 方形裁切）
   │
   │  ◀── unload 圖像模型
   ▼  [Kokoro v1.1-zh — 待建 studio/tts.py]
audio/*.wav  ── 依 narration，4.5 字/秒
   │
   ▼  [ffmpeg — 待建 studio/render.py]
out/final.mp4 ── Ken Burns（依 camera 欄位）
                 + 燒字幕（需 CJK 字型）
                 + 混音 + BGM
                 + 1080x1920 / H.264
```

**待建模組清單**：

| 模組 | 職責 | 關鍵設計 |
|---|---|---|
| `studio/imagegen.py` | 呼叫 Draw Things API 批次出圖 | **可續跑**（跑到一半掛掉能接），參數全從 style preset 帶入不依賴 UI |
| `studio/tts.py` | Kokoro 產旁白 | 長度對齊 `duration_sec` |
| `studio/render.py` | ffmpeg 合成 | Ken Burns 對應 `camera` 五種值；字幕燒錄需選定 CJK 字型 |

`scriptgen.py:8` 已定義 `CAMERAS = ("push_in", "pull_out", "pan_left", "pan_right", "static")`，`render.py` 直接對應這五種即可。

---

## 8. 未來研究方向（Open Questions）

按優先序。**這是接手時的待辦清單。**

| # | 問題 | 為什麼重要 | 怎麼解 | 狀態 |
|---|---|---|---|---|
| **Q0** | ~~Draw Things 版本與已下載模型？~~ | 決定 §6 能不能開始 | 已實測，見 §2 | ✅ **已解**：版本 1.20260716.0 過關；**但模型全未下載**，需先補約 22GB |
| **Q1** | Qwen-Edit 2509 在 M2 16GB 的秒/張？ | 決定整條線可行性 | §6 bake-off | 🔴 最高優先 |
| **Q2** | 右耳缺角與鈴鐺守得住嗎？ | 角色 IP 成立與否 | §6.4 驗收 | 🔴 最高優先 |
| **Q3** | Z-Image ControlNet 何時支援？ | 有了才能精準控姿勢 / 構圖 | 追蹤 drawthings community issue #73 | 🟡 觀察 |
| **Q4** | Kokoro v1.1-zh 中文品質實際如何？ | 配音爛則整支片廢 | 安裝後用 ep002 旁白試聽 | 🟡 未驗證 |
| **Q5** | LTX-MLX I2V 在 16GB 的可行性與畫質？ | 決定第二階段要不要上真動態 | benchmark；ADR-006 已言明先 benchmark 再決定 | 🟢 二階段 |
| **Q6** | Draw Things 能否 headless / 排程？ | 決定能不能無人值守批次產片 | 查 API 能力；或改用 CLI / 社群版 | 🟡 影響自動化 |
| **Q7** | Z-Image-Edit 釋出了嗎？ | 若釋出，可能同時取代 A 與 C，一次解決速度與一致性 | 追蹤阿里發布 | 🟡 **高報酬觀察項** |
| **Q8** | Character-Adapter 有無 Metal / Draw Things 路徑？ | 專為動物一致性設計 | 觀察；目前只有論文與 diffusers 實作 | 🟢 長期 |
| **Q9** | 免費可商用 BGM / 音效來源？ | 上片必需 | 待調查 | 🟡 上片前必須解 |
| **Q10** | 字幕 CJK 字型選哪個（授權可商用）？ | `render.py` 需要 | 待調查 | 🟡 |
| **Q11** | 上片 metadata（標題/描述/標籤）要不要也讓 LLM 產？ | 省人力，且 `scriptgen.py` 已有現成基礎設施 | 加一個 prompt 即可 | 🟢 低成本加分 |
| **Q12** | YouTube inauthentic content 政策後續變化？ | 直接影響營利資格 | 定期追蹤 | 🟡 持續 |

---

## 9. 風險登錄

| 風險 | 影響 | 緩解 |
|---|---|---|
| Qwen-Edit 在 16GB 上慢到不可用 | 主線方案死 | 決策樹已備 B→C→D→E 四層退路 |
| 16GB 撞天花板開始 swap | 速度崩 + SSD 磨損 | ADR-008 序列載入；`/api/system` 監控 swap |
| 風格中途更換 | 先前所有圖作廢、資料集報廢 | §6.6 第 1 點：**先鎖 style preset** |
| Draw Things 必須開著 App | 無法無人值守 | Q6；短期接受人工啟動 |
| 模型設定活在 App 不在 repo | 無法重現 | 參數全寫進 style preset，每次呼叫帶入 |
| YouTube 政策收緊 | 無法營利 | 固定角色 IP 路線本身就是對沖 |
| Kokoro 中文品質不如預期 | 配音環節卡住 | Q4 提早驗；Piper 已排除，屆時需另找 |

---

## 10. 兩週計畫 —— 目標：一支片上架 YouTube Shorts

定於 2026-09-22，期限兩週（至 **2026-10-06**）。依 ADR-010。

**完成的定義（唯一判準）**：**一支 1080x1920 的 mp4 發布在 YouTube Shorts 上。**
其餘一切 —— 畫質、角色一致性、產線可重複性 —— 在這個期限內都是次要。

### 11.1 為什麼第一支選 ep001 而不是 ep002

ep001 是 15 秒 5 個 shot，ep002 是 27 秒 9 個 shot。

**第一支片的每一個 shot 都是一份風險**（出圖失敗、角色飄、合成出錯、時間軸對不上）。shot 數砍一半，風險砍一半。15 秒的 Shorts 表現不會比 27 秒差。**ep002 留給第二支** —— 那時候管線已經驗證過了。

### 11.2 里程碑

| # | 里程碑 | 完成的判準 | 關鍵決定 |
|---|---|---|---|
| **M1** | 環境就緒 | 清場後 `/api/system` 可用記憶體 > 12GB；Draw Things API 開著；`curl` 出得了一張圖 | **只下載 Z-Image Turbo（~4GB），不是 22GB。** 方案 A/B 的模型留到 bake-off 再說，省下 18GB 與數天 |
| **M2** | `studio/imagegen.py` | ep001 的 5 張圖出現在 `projects/ep001-egypt/shots/`，同時另存 512 臉部裁切 | 參數全部從 `presets/style-v1.json` 帶入，不依賴 App UI 狀態；**可續跑** |
| **M3** | `studio/tts.py` | 5 段旁白 wav，長度與各 shot 的 `duration_sec` 對得上 | Kokoro 仍未驗證（Q4）。**先試 Kokoro，兩小時內不通就改用 macOS 內建 `say`** —— 品質較差但零安裝，不讓配音擋住上架 |
| **M4** | `studio/render.py` | `out/final.mp4`：1080x1920、H.264、Ken Burns 對應 `camera` 五種值、字幕燒錄 | 字型須可商用（Q10）：**Noto Sans CJK / 思源黑體是 OFL 授權，安全** |
| **M5** | 上架 | 影片在 YouTube Shorts 上，有標題、描述、標籤 | BGM 用 **YouTube 自己的音訊庫** —— 授權問題直接消失（解 Q9）。metadata 用既有的 Ollama 基礎設施產（解 Q11） |
| **M6** | 作品集頁 | 一頁案例研究：問題、硬約束、架構決策、成品嵌入 | 讀者是**求職時看作品的工程主管**。主角是**判斷力與收尾能力**，不是操作截圖 —— 截圖滿網路都是，那份 ADR 文件才是稀有的 |

### 11.3 這兩週刻意不做的事

寫下來，是為了之後想做的時候知道當初為什麼不做：

- **§6 的 bake-off** —— 移到 M6 之後
- 方案 A（Qwen-Edit）/ B（FLUX Kontext）的模型下載
- LoRA 訓練（方案 D）
- LTX I2V 真動態（Q5）
- **角色一致性的極致化** —— 第一支片接受它會飄

### 11.4 兩週後的回頭檢查

一支片的數據幾乎沒有統計意義，但它會回答三個是非題，而這三題目前全部無解：

1. **這條線跑得完嗎？**（純工程）
2. **中文 TTS 旁白聽得下去嗎？**（Q4，用真人耳朵驗收，不是用規格表）
3. **角色飄到什麼程度會被察覺？** —— 這一題直接決定 bake-off 還要不要跑、要跑哪一段。§6.4 的驗收門檻（右耳缺角 4/5、鈴鐺 4/5）是**工程指標，不是觀眾指標**；觀眾在手機上看 3 秒的鏡頭不會檢查耳朵，但會察覺「這兩個鏡頭不是同一隻貓」。第一支片會給出這兩者的落差。

---

## 11. 參考來源

- [Qwen Image Edit — Draw Things WIKI](https://wiki.drawthings.ai/wiki/Qwen_Image_Edit)
- [Draw Things 宣布支援 Qwen Image Edit 2509（1.20250930.0）](https://x.com/drawthingsapp/status/1973532852785758618)
- [Quantify Z Image Turbo efficiency gains — Draw Things](https://releases.drawthings.ai/p/quantify-z-image-turbo-efficiency)
- [Z-Image Turbo: Alibaba's 6B-Parameter SOTA Model — RunDiffusion](https://www.rundiffusion.com/z-image)
- [LoRA Training — Draw Things WIKI](https://wiki.drawthings.ai/wiki/LoRA_Training)
- [LoRa Training Notes — Draw Things WIKI](https://wiki.drawthings.ai/wiki/LoRa_Training_Notes)
- [Draw Things democratizes local large model fine-tuning](https://engineering.drawthings.ai/p/draw-things-democratizes-local-large-model-fine-tuning-on-iphone-ipad-and-mac-2ceb60b5b462)
- [Flux Kontext — Draw Things WIKI](https://wiki.drawthings.ai/wiki/Flux_Kontext)
- [Feature Request: ControlNet support for Z Image — issue #73](https://github.com/drawthingsai/draw-things-community/issues/73)
- [Character-Adapter（論文）](https://arxiv.org/html/2406.16537v2)
- [kohya_ss on Apple Silicon — issue #577](https://github.com/bmaltais/kohya_ss/issues/577)
