"""Writer -> Critic -> Rewrite -> 硬檢查修復迴圈。可被 CLI 或 server 呼叫。"""
from __future__ import annotations
import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CPS = 4.5  # 中文 TTS 約 4.5 字/秒，旁白長度的硬約束
CAMERAS = ("push_in", "pull_out", "pan_left", "pan_right", "static")
SEC_PER_SHOT = 3.2  # I2V 與 Ken Burns 都在 2~4 秒最好看


def suggest_shots(duration: int) -> int:
    return max(3, min(12, round(duration / SEC_PER_SHOT)))


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

1. 總長 {duration} 秒，切成 {n_shots} 個 shot，每個 shot 2~4 秒。
2. 每句 narration 最多 {max_chars} 個字，含標點。心裡話本來就短。
3. 第 1 個 shot 是 Hook：要有懸念、反差或情緒衝擊，讓人不想滑走。
4. 最後一個 shot 必須有情感落點或轉折——一個沒想到的真相、一個失去、一個重逢。
   「成功逃走了」「安全了」「牠適應了」不算落點，那是沒有結尾。
5. image_prompt 用英文寫給圖片模型。每個 shot 都要完整重複主角的外觀描述，一個字都不能省。
   再加上場景、光線、鏡頭角度、鏡頭距離，結尾固定加 "vertical composition, cinematic lighting"。
6. camera 只能從這五個選：push_in, pull_out, pan_left, pan_right, static。
7. subtitle 是螢幕字幕，通常等於 narration，最多 12 個字。

只輸出 JSON，不要任何其他文字。格式：
{{"title":"","hook":"","character":"","shots":[{{"id":1,"duration_sec":3,"action":"","image_prompt":"","narration":"","subtitle":"","camera":"push_in"}}]}}"""

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
    lo, hi = len(shots) * 2, len(shots) * 4
    if not lo <= total <= hi:
        issues.append(f"總長 {total} 秒，不在 {lo}~{hi} 秒的合理範圍")
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
        if d > 4:
            issues.append(f"Shot {sid} 長 {d} 秒，超過 4 秒（I2V 與 Ken Burns 的實用上限），須拆成兩格")

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
