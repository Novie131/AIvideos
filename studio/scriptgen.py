"""Writer -> Critic -> Rewrite -> 硬檢查修復迴圈。

**編排（generate_script）住在這裡，不在 endpoint 裡。**
2026-09-25 之前，這段約 45 行的編排在 server.py 的 /api/script closure 與
scripts/make_script.py 各存在一份，然後就飄了 —— CLI 版漏掉了跑完釋放 LLM 那步，
違反 ADR-008「一次只載一個重模型」。同一件事寫兩遍的標準結局不是一次寫壞，是慢慢分岔。
現在 Web endpoint 與 CLI 都只是 3 行薄殼，進度用 on_step callback 回報。
"""
from __future__ import annotations
import asyncio, json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ROOT / "projects"
CHARS = ROOT / "characters"
CPS = 4.5  # 中文 TTS 約 4.5 字/秒，旁白長度的硬約束
CAMERAS = ("push_in", "pull_out", "pan_left", "pan_right", "static")
SEC_PER_SHOT = 1.8
# 2026-09-25 從 3.2 改為 1.8。原因見 pipeline-plan.md ADR-012：
# 實測 I2V（Wan 2.2 TI2V 5B）的動作幅度有硬上限，拉高 strength 會先毀掉角色才換到動作
#（strength 0.95 時項圈與鈴鐺直接消失）。既然單一鏡頭內產生不了大動作，
# 節奏就得靠「切」來給 —— 同樣 15 秒，從 5 個鏡頭變成 8~9 個，
# 這不花任何運算，而且熱門 Shorts 本來就是 1~2 秒一切。

# image_prompt 必須帶的動態線索。I2V 只能延續「已經在發生的動作」，
# 不會讓一隻站著的貓開始跑 —— 所以動作必須在出圖階段就凍結進畫面裡。
MOTION_CUES = (
    "mid-stride", "mid-leap", "mid-air", "mid-jump", "mid-turn", "mid-fall",
    "running", "leaping", "jumping", "pouncing", "dashing", "sprinting", "bounding",
    "turning", "spinning", "twisting", "lunging", "climbing", "tumbling", "skidding",
    "reaching", "stretching", "swiping", "pawing", "shaking", "arching", "crouching",
    "falling", "landing", "rushing", "chasing", "fleeing", "stumbling", "recoiling",
    "paw lifted", "paw raised", "in motion", "about to", "just as",
)


def suggest_shots(duration: int) -> int:
    return max(4, min(20, round(duration / SEC_PER_SHOT)))


def beats(duration: int) -> str:
    """片長不同，敘事骨架就不同。三拍硬撐 30 秒必定變流水帳。"""
    if duration <= 18:
        return """這是短片，用三拍結構：
  拍 1  Hook —— 開場就要有懸念或反差
  拍 2  轉折 —— 一個發現或意外
  拍 3  落點 —— 情感收束
不要有任何鋪陳或過場，每個 shot 都要推進故事。"""
    if duration <= 35:
        return """這是中長片，必須用四拍結構，中段一定要有「複雜化」：
  拍 1  Hook      —— 開場就要有懸念或反差
  拍 2  建立      —— 讓觀眾知道主角要什麼
  拍 3  複雜化    —— 事情變得更糟，或出現意料外的阻礙。這一拍最重要
  拍 4  落點      —— 情感收束

警告：如果你只是把 15 秒的故事拉長、多加幾個「牠走著走著」「牠看了看四周」
這種沒有推進的鏡頭，這個腳本就是失敗的。中段必須真的有事情發生且變糟。"""
    return """這是長片，用五拍結構：
  拍 1  Hook  拍 2  建立  拍 3  第一次挫敗
  拍 4  最低點 —— 主角失去最重要的東西
  拍 5  落點
每一拍至少 2 個 shot。禁止任何沒有推進故事的過場鏡頭。"""


WRITER = """你是專門寫 YouTube Shorts 的編劇，擅長 15 秒內講完一個有情緒轉折的故事。

固定主角設定（每一集都必須完全一致）：
{character}

本集題目：{idea}

# 最重要的規則：旁白是主角的內心話，不是畫面說明

narration 是主角的第一人稱心聲，是「牠心裡想的一句話」。
絕對不可以描述畫面上看得到的東西。觀眾看得到的，不要用講的。

錯誤示範：
  action   : 小橘驚訝地睜大眼睛，周圍是古埃及神廟與金字塔
  narration: 小橘驚訝地睜大眼睛，周圍是古埃及神廟與金字塔   ← 完全錯誤，這是抄 action
  narration: 周圍充滿金色光暈                              ← 錯誤，這是在描述畫面

正確示範：
  action   : 小橘驚訝地睜大眼睛，周圍是古埃及神廟與金字塔
  narration: 這裡…不是我家。                              ← 正確，這是心聲

narration 和 action 的內容必須完全不同。

# 敘事結構

{structure}

# 其他規則

1. 總長 {duration} 秒，切成 {n_shots} 個 shot，**每個 shot 1.5~2.5 秒**。
   節奏要快。Shorts 觀眾的耐心以秒計算，一個鏡頭停超過 2.5 秒就開始流失。
2. 每句 narration 最多 {max_chars} 個字，含標點。心裡話本來就短。
3. **不是每個 shot 都要有旁白。** 鏡頭變多之後，句句都講會變成連珠砲。
   只在關鍵的 shot 放旁白，其餘留 narration 為空字串，讓畫面自己說話。
   留白是節奏的一部分，不是偷懶。
4. 第 1 個 shot 是 Hook：要有懸念、反差或情緒衝擊，讓人不想滑走。
5. 最後一個 shot 必須有情感落點或轉折——一個沒想到的真相、一個失去、一個重逢。
   「成功逃走了」「安全了」「牠適應了」不算落點，那是沒有結尾。

# image_prompt 的鐵則：每一格都必須是「動作進行到一半」的瞬間

image_prompt 用英文寫給圖片模型。三件事缺一不可：

  (a) 完整重複主角的外觀描述，一個字都不能省
  (b) **一個正在發生的動作姿勢** ← 最常被忽略，但最重要
  (c) 場景、光線、鏡頭距離，結尾固定加 "vertical composition, cinematic lighting"

為什麼 (b) 是鐵則：產出的圖之後會交給影片模型讓它動起來，
而影片模型**只能延續畫面裡已經在發生的動作，不會讓一隻站著的貓開始跑**。
動作必須在圖裡就存在。

  ❌ standing in a pyramid, looking around
     ← 站著。做成影片只會是一隻站著微微呼吸的貓，等於靜止畫面
  ✅ mid-stride running deeper into the pyramid, one front paw lifted off the ground,
     ears pinned back, tail streaming behind
     ← 動作已經凍結在畫面裡，影片模型只要把它延續下去

每個 image_prompt 都必須包含至少一個這類動態線索：
mid-stride / mid-leap / mid-air / paw lifted / turning / about to leap /
crouching to pounce / falling / landing / reaching / recoiling / skidding to a stop

# 相鄰鏡頭要有動作連續性

把一個大動作拆成連續的兩三格，讓觀眾的腦補把它們連成流暢的動作。
這比任何特效都有效，而且完全免費。

  shot 3: crouching low, hind legs coiled, about to leap
  shot 4: mid-air, body fully stretched, paws reaching forward
  shot 5: landing hard, front paws skidding, dust kicked up

# 其他

6. camera 只能從這五個選：push_in, pull_out, pan_left, pan_right, static。
7. subtitle 是螢幕字幕，通常等於 narration，最多 12 個字。沒有旁白的 shot，subtitle 也留空。

只輸出 JSON，不要任何其他文字。格式：
{{"title":"","hook":"","character":"","shots":[{{"id":1,"duration_sec":3,"action":"","image_prompt":"","narration":"","subtitle":"","camera":"push_in"}}]}}"""


# ---------- 才藝軌（type = "performance"）----------
# 與故事軌的差別：跟著節拍走，不跟著劇情走。沒有轉折，只有段落。
# 角色型態（四足／人形）由使用者每次選，不是角色的固定屬性 —— 見 character.json 的 forms。

PERFORMANCE = """你是 YouTube Shorts 的才藝短片分鏡師。這一支**不是故事，是表演**。

固定主角設定（每一集都必須完全一致）：
{character}

表演內容：{idea}
角色型態：{form_label}
  這個型態做得到：{form_can}
  做不到：{form_cannot}
  **絕對不要寫出「做不到」清單裡的動作。**

# 這支片沒有劇情，不要硬塞

沒有起承轉合、沒有轉折、沒有情感落點。觀眾來看的是**表演本身**。
你要做的是把一段表演切成連續的動作瞬間。

# 結構：跟著節拍，不跟著劇情

  段落 1  起手式    —— 第一格就要有辨識度，讓人停下來
  段落 2  主要段落  —— 動作的主體，幅度最大
  段落 3  高潮      —— 最誇張、最有記憶點的一個動作
  段落 4  收尾      —— 一個定格 pose

# 鐵則：每一格都是動作進行到一半

產出的圖之後會交給影片模型讓它動起來，而影片模型**只能延續畫面裡已經在發生的動作**。
動作必須在圖裡就存在。連續的格子要能串成一個完整動作。

  ❌ dancing in a studio                      ← 太籠統，模型不知道畫什麼姿勢
  ✅ mid-spin, one arm thrown up, head tilted back, tail whipping around
     ← 具體到可以畫出來的單一瞬間

# 旁白

**才藝片通常不需要旁白**，narration 一律留空字串。
subtitle 只在需要標示段落時才用（例如「第一次嘗試」），通常也留空。

# 其他規則

1. 總長 {duration} 秒，切成 {n_shots} 個 shot，**每個 shot 1.5~2.5 秒**。
2. image_prompt 用英文。三件事缺一不可：
   (a) 完整重複主角的外觀描述，一個字都不能省
   (b) {form_hint}
   (c) 一個正在發生的動作姿勢 + 場景光線，結尾固定加 "vertical composition, cinematic lighting"
3. camera 只能從這五個選：push_in, pull_out, pan_left, pan_right, static。
4. action 用中文描述這一格在做什麼動作。

只輸出 JSON，不要任何其他文字。格式：
{{"type":"performance","title":"","hook":"","character":"","shots":[{{"id":1,"duration_sec":2,"action":"","image_prompt":"","narration":"","subtitle":"","camera":"push_in"}}]}}"""


# 音樂版權提醒 —— 只提醒，不阻擋。使用者要用什麼音樂是他的決定。
MUSIC_WARNING = (
    "提醒：使用有版權的音樂（流行歌、K-pop、動漫主題曲等）會被 YouTube Content ID 認領，"
    "該片將無法營利。免費且可營利的來源：YouTube 創作者工作室的音訊庫、"
    "Pixabay Music、Free Music Archive（需確認個別授權）。"
)

CRITIC = """你是嚴格的 Shorts 監製。審查以下腳本。

腳本：
{script}

逐項檢查，每項給「通過」或「不通過」：
1. narration 有沒有跟 action 重複或只是在描述畫面？必須是主角的第一人稱心聲。逐個 shot 檢查。
2. Hook 夠不夠強？觀眾會不會直接滑走？
3. 不聽聲音只看畫面，故事看得懂嗎？
4. 最後一個 shot 有沒有真正的情感落點？「逃走了」「安全了」不算。
5. 每個 shot 的 narration 是否在字數上限內？逐個數給我看。
6. 各個 image_prompt 裡主角外觀描述是否完全一致？有沒有漏寫？
7. **每個 image_prompt 是否都是「動作進行到一半」的瞬間？** 逐個 shot 檢查。
   只要出現 standing / sitting / looking at / posing 這種靜態擺拍，就是不通過，
   要指出該改成哪個具體動作姿勢。
8. 相鄰的 shot 之間有沒有動作連續性？有沒有把一個大動作拆成連續幾格？
9. 是不是每一格都硬塞了旁白？鏡頭多的時候應該有留白。

用中文回答，不通過的要寫出具體怎麼改。最後給總分（1-10）。不要客氣。"""

REWRITE = """根據監製意見修正腳本。

原腳本：
{script}

監製意見：
{critique}

把所有「不通過」的項目都改掉。特別注意旁白字數和主角外觀一致性。
只輸出修正後的 JSON，格式與原腳本完全相同，不要任何其他文字。"""

REPAIR = """以下腳本沒有通過自動檢查。

腳本：
{script}

未通過項目：
{issues}

只修正這些項目，其他不要動。特別注意：narration 必須是主角的第一人稱內心話，
不可以描述畫面，不可以跟 action 重複。

只輸出修正後的 JSON，格式與原腳本完全相同，不要任何其他文字。"""

ENDINGS = """以下是一支 Shorts 的腳本，但結尾不夠有力。

腳本：
{script}

請提供 3 個**完全不同方向**的結尾（取代最後一個 shot），每個都要有真正的情感落點：
- 版本 A：一個沒想到的真相
- 版本 B：一個失去
- 版本 C：一個重逢或和解

只輸出 JSON：
{{"endings":[{{"label":"A","why":"這個結尾的後勁在哪，一句話","shot":{{"id":0,"duration_sec":3,"action":"","image_prompt":"","narration":"","subtitle":"","camera":"static"}}}}]}}"""


def strip_think(t: str) -> str:
    return re.sub(r"<think>.*?</think>", "", t, flags=re.S).strip()


def parse_json(text: str):
    text = strip_think(text)
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError(f"模型沒有回傳合法 JSON：{text[:200]}")
        return json.loads(m.group())


def check(script: dict) -> tuple[int, list[str]]:
    """程式面的硬檢查，不依賴 LLM 自我評估。"""
    issues: list[str] = []
    shots = script.get("shots", [])
    if not shots:
        return 0, ["腳本沒有任何 shot"]
    total = sum(s.get("duration_sec", 0) for s in shots)
    lo, hi = len(shots) * 1.2, len(shots) * 2.8
    if not lo <= total <= hi:
        issues.append(f"總長 {total} 秒，不在 {lo:.0f}~{hi:.0f} 秒的合理範圍")
    for s in shots:
        sid = s.get("id")
        nar, act = s.get("narration", ""), s.get("action", "")
        d = s.get("duration_sec", 0)
        cap = int(d * CPS)
        if len(nar) > cap:
            issues.append(f"Shot {sid} 旁白 {len(nar)} 字 > {d} 秒可容納的 {cap} 字，須改短")
        if nar and act and (nar == act or nar in act or act in nar):
            issues.append(f"Shot {sid} 旁白與 action 重複，須改成主角的第一人稱心聲")
        if len(s.get("subtitle", "")) > 12:
            issues.append(f"Shot {sid} 字幕 {len(s['subtitle'])} 字，超過 12")
        if s.get("camera") not in CAMERAS:
            issues.append(f"Shot {sid} camera 值不合法：{s.get('camera')}")
        if not s.get("image_prompt"):
            issues.append(f"Shot {sid} 缺少 image_prompt")
        if d > 2.5:
            issues.append(f"Shot {sid} 長 {d} 秒，超過 2.5 秒。Shorts 節奏要快，且單鏡頭再長也產生不了更多動作，須拆成兩格")
        ip = (s.get("image_prompt") or "").lower()
        if ip and not any(c in ip for c in MOTION_CUES):
            issues.append(f"Shot {sid} 的 image_prompt 沒有任何動態姿勢線索，"
                          f"畫面會是靜止的擺拍。須改寫成動作進行到一半的瞬間"
                          f"（例如 mid-stride / paw lifted / turning / about to leap）")

    # 以下兩項只在 shot 數多時才會塌陷，5 格看不出來
    if len(shots) >= 6:
        from collections import Counter
        cams = Counter(s.get("camera") for s in shots)
        top, n = cams.most_common(1)[0]
        if n > len(shots) * 0.5:
            issues.append(f"鏡頭運動塌陷：{len(shots)} 格裡有 {n} 格都是 {top}，畫面會很單調，須分散使用不同 camera")
        heads = Counter((s.get("narration") or "")[:2] for s in shots if s.get("narration"))
        h, hn = heads.most_common(1)[0] if heads else ("", 0)
        if hn > len(shots) * 0.4 and h:
            issues.append(f"旁白句式塌陷：{hn} 句都以「{h}」開頭，須改寫成不同的說話方式")
    return total, issues


def consistency(script: dict, appearance_en: str) -> list[dict]:
    """檢查每個 image_prompt 是否帶了主角外觀的關鍵特徵。"""
    feats = [f.strip() for f in appearance_en.split(",") if len(f.strip()) > 12]
    out = []
    for s in script.get("shots", []):
        p = (s.get("image_prompt") or "").lower()
        missing = [f for f in feats if f.lower() not in p]
        out.append({"id": s.get("id"), "missing": missing, "ok": not missing})
    return out


# ---------- 鏡頭指派：規則性決定，不交給 LLM ----------
# 8B 模型連續三輪都拒絕修改 camera 欄位，所以這件事收回來自己做。

_MOVE = ("逃", "跑", "追", "衝", "奔", "走", "撞", "飛", "轉身", "穿過")
_REVEAL = ("發現", "看到", "注意", "察覺", "睜大", "抬頭", "望", "凝視")
_WIDE = ("落在", "出現", "龐大", "廣闊", "全景", "遠處", "巨大", "整座", "天空")


def assign_cameras(shots: list[dict]) -> list[dict]:
    """依鏡頭在故事裡的角色指派運動，並保證多樣性。"""
    n = len(shots)
    pans = ("pan_left", "pan_right")
    pi = 0
    for i, s in enumerate(shots):
        text = f"{s.get('action','')}{s.get('narration','')}"
        if i == 0:
            cam = "push_in"                      # Hook：把觀眾拉進去
        elif i == n - 1:
            cam = "static"                       # 落點：讓情緒停住
        elif any(k in text for k in _MOVE):
            cam = pans[pi % 2]; pi += 1          # 有動作 → 橫搖，左右交替
        elif any(k in text for k in _WIDE):
            cam = "pull_out"                     # 建立場景 → 拉遠
        elif any(k in text for k in _REVEAL):
            cam = "push_in"                      # 發現 → 推近
        else:
            cam = ("push_in", "pull_out", pans[pi % 2])[i % 3]
            if cam.startswith("pan"):
                pi += 1
        s["camera"] = cam

    # 保底：任一運動超過 40% 就強制打散
    from collections import Counter
    order = ("push_in", "pull_out", "pan_left", "pan_right", "static")
    for _ in range(n):
        c = Counter(s["camera"] for s in shots)
        top, cnt = c.most_common(1)[0]
        if cnt <= max(2, n * 0.4):
            break
        least = min(order, key=lambda k: (c[k], order.index(k)))
        for i, s in enumerate(shots):
            if s["camera"] == top and 0 < i < n - 1:
                s["camera"] = least
                break
    return shots


# ---------- 簡體字偵測 ----------
# 8B 模型偶爾會混入簡體字（實測抓到「时间尽头」）。對繁中頻道是硬傷。
# 只收「不會與繁體正字混淆」的字，避免誤判（例如 只/后/面/干 在繁中另有其義，故排除）。
SIMPLIFIED = {
    "尽": "盡", "时": "時", "间": "間", "个": "個", "们": "們", "这": "這",
    "么": "麼", "说": "說", "见": "見", "现": "現", "发": "發", "会": "會",
    "来": "來", "对": "對", "学": "學", "应": "應", "图": "圖", "关": "關",
    "门": "門", "问": "問", "无": "無", "与": "與", "为": "為", "电": "電",
    "车": "車", "东": "東", "马": "馬", "鸟": "鳥", "鱼": "魚", "头": "頭",
    "语": "語", "边": "邊", "过": "過", "还": "還", "进": "進", "远": "遠",
    "连": "連", "运": "運", "达": "達", "记": "記", "认": "認", "让": "讓",
    "该": "該", "请": "請", "谁": "誰", "读": "讀", "谢": "謝", "变": "變",
    "点": "點", "热": "熱", "爱": "愛", "觉": "覺", "结": "結", "给": "給",
    "经": "經", "续": "續", "万": "萬", "乐": "樂", "习": "習", "书": "書",
    "买": "買", "卖": "賣", "儿": "兒", "内": "內", "军": "軍", "农": "農",
    "决": "決", "击": "擊", "医": "醫", "单": "單", "卫": "衛", "厂": "廠",
    "厅": "廳", "历": "歷", "压": "壓", "参": "參", "双": "雙", "号": "號",
    "叶": "葉", "呜": "嗚", "国": "國", "园": "園", "圆": "圓", "场": "場",
    "块": "塊", "坏": "壞", "声": "聲", "壮": "壯", "妈": "媽", "宝": "寶",
    "实": "實", "宁": "寧", "对": "對", "寻": "尋", "导": "導", "尔": "爾",
    "层": "層", "岁": "歲", "岛": "島", "币": "幣", "带": "帶", "帮": "幫",
    "开": "開", "张": "張", "强": "強", "归": "歸", "当": "當", "录": "錄",
    "态": "態", "怀": "懷", "总": "總", "恶": "惡", "惊": "驚", "惧": "懼",
    "战": "戰", "扫": "掃", "报": "報", "担": "擔", "拟": "擬", "挂": "掛",
    "换": "換", "据": "據", "挥": "揮", "损": "損", "捡": "撿", "搂": "摟",
    "断": "斷", "旧": "舊", "时": "時", "显": "顯", "晓": "曉", "术": "術",
    "机": "機", "杀": "殺", "样": "樣", "标": "標", "树": "樹", "桥": "橋",
    "检": "檢", "楼": "樓", "权": "權", "欢": "歡", "汉": "漢", "泪": "淚",
    "济": "濟", "浅": "淺", "测": "測", "济": "濟", "灭": "滅", "灯": "燈",
    "灵": "靈", "炉": "爐", "烦": "煩", "烧": "燒", "环": "環", "现": "現",
    "画": "畫", "疗": "療", "监": "監", "盘": "盤", "县": "縣", "确": "確",
    "离": "離", "种": "種", "积": "積", "称": "稱", "笔": "筆", "简": "簡",
    "级": "級", "纪": "紀", "纸": "紙", "线": "線", "细": "細", "终": "終",
    "组": "組", "绝": "絕", "统": "統", "继": "繼", "维": "維", "网": "網",
    "肃": "肅", "脏": "髒", "苍": "蒼", "苏": "蘇", "药": "藥", "虽": "雖",
    "蚁": "蟻", "补": "補", "装": "裝", "见": "見", "观": "觀", "规": "規",
    "视": "視", "论": "論", "识": "識", "诉": "訴", "词": "詞", "试": "試",
    "话": "話", "误": "誤", "护": "護", "轻": "輕", "较": "較", "辉": "輝",
    "边": "邊", "选": "選", "遗": "遺", "邮": "郵", "释": "釋", "长": "長",
    "间": "間", "闪": "閃", "队": "隊", "阳": "陽", "阴": "陰", "际": "際",
    "陆": "陸", "难": "難", "题": "題", "颗": "顆", "风": "風", "飞": "飛",
    "饿": "餓", "馆": "館", "验": "驗", "骨": "骨", "体": "體", "龙": "龍",
}


def find_simplified(script: dict) -> list[dict]:
    """回傳每個 shot 裡出現的簡體字與建議的繁體寫法。"""
    out = []
    for s in script.get("shots", []):
        hits = {}
        for f in ("narration", "subtitle", "action"):
            for ch in s.get(f) or "":
                if ch in SIMPLIFIED:
                    hits[ch] = SIMPLIFIED[ch]
        if hits:
            out.append({"id": s.get("id"), "hits": hits})
    return out


def fix_simplified(script: dict) -> int:
    """直接改成繁體。純字元替換，不需要 LLM。"""
    n = 0
    for s in script.get("shots", []):
        for f in ("narration", "subtitle", "action", "title"):
            v = s.get(f)
            if not isinstance(v, str):
                continue
            new = "".join(SIMPLIFIED.get(c, c) for c in v)
            if new != v:
                n += sum(1 for a, b in zip(v, new) if a != b)
                s[f] = new
    for f in ("title", "hook"):
        v = script.get(f)
        if isinstance(v, str):
            new = "".join(SIMPLIFIED.get(c, c) for c in v)
            if new != v:
                n += sum(1 for a, b in zip(v, new) if a != b)
                script[f] = new
    return n


# ---------- 編排：唯一的一份 ----------

async def generate_script(idea: str, slug: str, *, character: str = "orange-cat",
                          model: str = "qwen3:8b", think: bool = False,
                          n_shots: int = 0, duration: int = 15,
                          script_type: str = "story", form: str = "quadruped",
                          music: str = "",
                          temperature: float = 0.8, repair_rounds: int = 3,
                          release_llm: bool = True,
                          on_step=lambda text, pct=None: None) -> dict:
    """題材 → script.json。Web 與 CLI 共用這一份。

    release_llm：跑完把 LLM 踢出記憶體（ADR-008）。Web 端在佇列還有排隊時會傳 False
    以免反覆載入，CLI 一律 True。
    """
    from . import backends as be

    cp = CHARS / character / "character.json"
    char_json = cp.read_text(encoding="utf-8") if cp.exists() else "（未指定）"
    cdata = json.loads(char_json) if cp.exists() else {}
    app_en = cdata.get("appearance_en", "")
    fm = (cdata.get("forms") or {}).get(form) or {}
    mod = fm.get("modifier", "")
    n = n_shots or suggest_shots(duration)
    max_chars = int(duration / n * CPS)

    out = PROJECTS / slug
    out.mkdir(parents=True, exist_ok=True)
    (out / "idea.txt").write_text(idea, encoding="utf-8")

    async def ask(prompt, **kw):
        return await be.ollama_chat(model, prompt, think=think,
                                    temperature=temperature, **kw)

    def _post(d):
        assign_cameras(d.get("shots", []))
        return fix_simplified(d)

    on_step("Writer 寫初稿…", 5)
    if script_type == "performance":
        tmpl = PERFORMANCE.format(
            character=char_json, idea=idea, n_shots=n, duration=duration,
            form_label=fm.get("label", form), form_can=fm.get("can", "—"),
            form_cannot=fm.get("cannot", "—"),
            form_hint=(f"務必在外觀描述後加上「{mod}」，這決定角色是四足還是人形"
                       if mod else "維持一般四足貓的體態，不要寫成人形"))
        if music:
            on_step(MUSIC_WARNING, 5)
    else:
        tmpl = WRITER.format(
            character=char_json, idea=idea, n_shots=n, duration=duration,
            max_chars=max_chars, structure=beats(duration))
        if mod:
            tmpl += (f"\n\n# 角色型態（{fm.get('label', form)}）\n"
                     f"每個 image_prompt 的外觀描述後面都必須加上：{mod}\n"
                     f"這個型態做得到：{fm.get('can','—')}；做不到：{fm.get('cannot','—')}。"
                     f"不要寫出做不到的動作。")
    draft = parse_json(await ask(tmpl, json_mode=True))
    _post(draft)
    (out / "script.draft.json").write_text(
        json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    on_step("初稿完成", 30)

    _, issues = check(draft)
    hard = "\n".join(f"- {i}" for i in issues) or "-（程式檢查無誤）"

    on_step("Critic 審稿…", 35)
    crit = strip_think(await ask(CRITIC.format(
        script=json.dumps(draft, ensure_ascii=False, indent=2))))
    crit = f"{crit}\n\n【程式硬檢查】\n{hard}"
    (out / "critique.md").write_text(crit, encoding="utf-8")
    on_step("審稿完成", 60)

    on_step("Rewrite 修稿…", 65)
    final = parse_json(await ask(REWRITE.format(
        script=json.dumps(draft, ensure_ascii=False, indent=2),
        critique=crit), json_mode=True))
    nz = _post(final)
    on_step("修稿完成，鏡頭已自動指派"
            + (f"，修正 {nz} 個簡體字" if nz else ""), 85)

    for i in range(1, repair_rounds + 1):
        _, issues = check(final)
        if not issues:
            break
        on_step(f"硬檢查未過，自動修復第 {i} 輪（{len(issues)} 項）", 85 + i * 4)
        final = parse_json(await ask(REPAIR.format(
            script=json.dumps(final, ensure_ascii=False, indent=2),
            issues="\n".join(f"- {x}" for x in issues)), json_mode=True))
        _post(final)

    final["type"] = script_type
    final["form"] = form
    if music:
        final["music"] = music
    (out / "script.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    total, issues = check(final)
    cons = consistency(final, app_en) if app_en else []
    bad = [c["id"] for c in cons if not c["ok"]]
    on_step(f"完成：{total} 秒 / {len(final.get('shots', []))} shots"
            + (f"，殘留 {len(issues)} 項問題" if issues else "，硬檢查全過")
            + (f"；角色描述缺漏 shot {bad}" if bad else ""), 100)

    if release_llm:
        before = be.system_stats()["available_gb"]
        await be.ollama_unload(model)
        await asyncio.sleep(2)
        on_step(f"已釋放 {model}，可用記憶體 {before} → "
                f"{be.system_stats()['available_gb']} GB", 100)

    return {"slug": slug, "total": total, "issues": issues,
            "consistency": cons, "script": final,
            "music_warning": MUSIC_WARNING if music else None}
