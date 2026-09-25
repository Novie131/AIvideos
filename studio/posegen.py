"""姿勢抽取 —— 影片 → OpenPose 骨架線框序列。

用途：才藝軌（ADR-011）。使用者上傳一段舞蹈影片，抽出每一格的人體骨架，
再交給 Draw Things 的 VACE 依骨架生成「人形小橘跳那支舞」。

**為什麼一定要人形角色**：OpenPose 是人體骨架（肩、肘、腕、髖、膝、踝）。
四足動物沒有對應關節，套上去會生出怪物。見 character.json 的 forms。

輸出刻意畫成 **OpenPose COCO-18 的標準配色與連線**，因為 ControlNet / VACE
的姿勢模型就是拿這個格式訓練的 —— 自創畫法會讓控制失效。

**為什麼用 Apple Vision 而不是 MediaPipe**（2026-09-25 實測）：
- mediapipe 1.0.1 的 Tasks API 在 macOS 上必崩 —— TensorsToDetectionsCalculator
  初始化 Metal 時直接 abort（DrishtiMetalHelper），設 delegate=CPU 也擋不住
- mediapipe 0.10.x 的舊 solutions API **根本沒包進 arm64 的 wheel**
- Vision 是系統內建、零下載、Apple 自己維護，而且原生就有 neck 關節
  （MediaPipe 要用兩肩中點推算）
"""
from __future__ import annotations
import json, math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent

# ---------- OpenPose COCO-18 規格 ----------
# 關節順序（不可更動，模型是照這個訓練的）
JOINTS = ("nose", "neck", "r_shoulder", "r_elbow", "r_wrist", "l_shoulder", "l_elbow",
          "l_wrist", "r_hip", "r_knee", "r_ankle", "l_hip", "l_knee", "l_ankle",
          "r_eye", "l_eye", "r_ear", "l_ear")

LIMBS = ((1, 2), (1, 5), (2, 3), (3, 4), (5, 6), (6, 7), (1, 8), (8, 9), (9, 10),
         (1, 11), (11, 12), (12, 13), (1, 0), (0, 14), (14, 16), (0, 15), (15, 17))

COLORS = ((255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0), (170, 255, 0),
          (85, 255, 0), (0, 255, 0), (0, 255, 85), (0, 255, 170), (0, 255, 255),
          (0, 170, 255), (0, 85, 255), (0, 0, 255), (85, 0, 255), (170, 0, 255),
          (255, 0, 255), (255, 0, 170), (255, 0, 85))

# MediaPipe BlazePose(33 點) → OpenPose COCO-18 的對應。
# neck 沒有直接對應，用兩肩中點推算，所以填 None 另外處理。
MP_TO_COCO = (0, None, 12, 14, 16, 11, 13, 15, 24, 26, 28, 23, 25, 27, 5, 2, 8, 7)

MIN_VIS = 0.35          # 低於此可見度視為沒偵測到，寧缺勿錯
STICK_W, JOINT_R = 4, 4


# ---------- Apple Vision 的關節名稱 → OpenPose COCO-18 ----------
# Vision 用 19 個關節，其中 root（骨盆中心）OpenPose 沒有，捨棄。
# 順序必須與 JOINTS 完全一致。
# Vision 沒有 nose，用 head_joint 當 OpenPose 的 0 號點。
# Vision 的命名習慣：forearm = 肘、hand = 腕、upLeg = 髖、leg = 膝、foot = 踝。
VISION_TO_COCO = (
    "head_joint",
    None,                                                    # neck，見 NECK_JOINT
    "right_shoulder_1_joint", "right_forearm_joint", "right_hand_joint",
    "left_shoulder_1_joint", "left_forearm_joint", "left_hand_joint",
    "right_upLeg_joint", "right_leg_joint", "right_foot_joint",
    "left_upLeg_joint", "left_leg_joint", "left_foot_joint",
    "right_eye_joint", "left_eye_joint", "right_ear_joint", "left_ear_joint",
)
NECK_JOINT = "neck_1_joint"     # Vision 原生就有，不用推算


def to_coco18(points: dict, w: int, h: int) -> list[tuple[float, float] | None]:
    """Vision 的關節字典 → OpenPose 的 18 點（像素座標）。

    Vision 的座標是正規化的，且**原點在左下角**（Quartz 慣例），
    所以 y 要翻轉才會對上影像座標系。
    """
    def pick(name):
        pt = points.get(name)
        if pt is None or pt.confidence() < MIN_VIS:
            return None
        loc = pt.location()
        return (loc.x * w, (1.0 - loc.y) * h)     # ← y 翻轉

    pts = [pick(n) if n else None for n in VISION_TO_COCO]
    pts[1] = pick(NECK_JOINT)                     # neck，Vision 原生提供
    if pts[1] is None:                            # 保險：退回兩肩中點
        rs, ls = pts[2], pts[5]
        if rs and ls:
            pts[1] = ((rs[0] + ls[0]) / 2, (rs[1] + ls[1]) / 2)
    return pts


def _detect(rgb: "np.ndarray"):
    """對一張 RGB numpy 影像做人體姿勢偵測，回傳關節字典（沒偵測到回 None）。"""
    import Quartz, Vision
    from CoreFoundation import CFDataCreate
    h, w = rgb.shape[:2]
    data = CFDataCreate(None, rgb.tobytes(), rgb.size)
    prov = Quartz.CGDataProviderCreateWithCFData(data)
    cg = Quartz.CGImageCreate(
        w, h, 8, 24, w * 3, Quartz.CGColorSpaceCreateDeviceRGB(),
        Quartz.kCGBitmapByteOrderDefault, prov, None, False,
        Quartz.kCGRenderingIntentDefault)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    req = Vision.VNDetectHumanBodyPoseRequest.alloc().init()
    ok, _ = handler.performRequests_error_([req], None)
    obs = req.results()
    if not ok or not obs:
        return None

    # 畫面裡可能有路人。挑「主體」＝骨架在畫面上最高的那一個
    #（取代單純取 obs[0]）—— 用關節的垂直跨度當身高代理，離鏡頭近的人跨度大。
    best, best_h = None, -1.0
    for o in obs:
        pts, _ = o.recognizedPointsForJointsGroupName_error_(
            Vision.VNHumanBodyPoseObservationJointsGroupNameAll, None)
        ys = [pt.location().y for pt in pts.values() if pt.confidence() >= MIN_VIS]
        if len(ys) < 6:
            continue
        span = max(ys) - min(ys)
        if span > best_h:
            best, best_h = pts, span
    return best


def draw_pose(pts, w: int, h: int) -> Image.Image:
    """畫成 OpenPose 標準線框：黑底、彩色骨幹、關節圓點。"""
    img = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, (a, b) in enumerate(LIMBS):
        pa, pb = pts[a], pts[b]
        if pa and pb:
            d.line([pa, pb], fill=COLORS[i % len(COLORS)], width=STICK_W)
    for i, p in enumerate(pts):
        if p:
            d.ellipse([p[0] - JOINT_R, p[1] - JOINT_R, p[0] + JOINT_R, p[1] + JOINT_R],
                      fill=COLORS[i % len(COLORS)])
    return img


def _probe(video: Path) -> tuple[int, int, float, float]:
    """回傳 (寬, 高, fps, 秒數)。"""
    import subprocess
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate", "-show_entries", "format=duration",
         "-of", "json", str(video)], capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    st = d["streams"][0]
    num, den = (st["r_frame_rate"].split("/") + ["1"])[:2]
    return int(st["width"]), int(st["height"]), int(num) / int(den), float(d["format"]["duration"])


def extract(video: str | Path, out_dir: str | Path, *, fps: int = 16,
            size: tuple[int, int] | None = None,
            on_step=lambda text, pct=None: None) -> dict:
    """影片 → 骨架線框 PNG 序列 + 關節座標 JSON。

    用 ffmpeg 解影格（不用 opencv）—— ffmpeg 本來就是這個專案的硬需求，
    而且重取樣影格率與縮放可以在同一個濾鏡鏈裡做完。

    fps：重取樣到這個影格率。影片模型通常吃 16 或 24。
    size：輸出尺寸（None = 沿用原片）。VACE 要求長寬可被 32 整除。
    """
    import subprocess
    video, out_dir = Path(video), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sw, sh, src_fps, dur = _probe(video)
    ow, oh = size or (sw, sh)
    if ow % 32 or oh % 32:
        on_step(f"⚠ 輸出 {ow}x{oh} 不是 32 的倍數，VACE 會自行調整尺寸", None)
    expect = max(int(dur * fps), 1)

    on_step(f"來源 {sw}x{sh} @ {src_fps:.1f}fps / {dur:.1f} 秒 → "
            f"輸出 {ow}x{oh} @ {fps}fps，預計 {expect} 格", 0)

    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(video),
         "-vf", f"fps={fps},scale={ow}:{oh}:force_original_aspect_ratio=increase,"
                f"crop={ow}:{oh}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    nbytes = ow * oh * 3
    kept, missed, frames, i = 0, 0, [], 0
    while True:
        raw = proc.stdout.read(nbytes)
        if len(raw) < nbytes:
            break
        rgb = np.frombuffer(raw, np.uint8).reshape(oh, ow, 3)
        found = _detect(np.ascontiguousarray(rgb))
        if found:
            pts = to_coco18(found, ow, oh)
            draw_pose(pts, ow, oh).save(out_dir / f"pose-{kept:04d}.png")
            frames.append({"i": kept, "src_frame": i,
                           "pts": [list(p) if p else None for p in pts]})
            kept += 1
        else:
            missed += 1
        i += 1
        if i % 20 == 0:
            on_step(f"已處理 {i}/{expect} 格，抽到 {kept} 個姿勢",
                    round(min(i / expect, 1) * 100))
    proc.stdout.close()
    proc.wait()

    meta = {"source": str(video), "src_fps": round(src_fps, 2), "out_fps": fps,
            "size": [ow, oh], "frames_total": i, "frames_kept": kept,
            "frames_missed": missed,
            "detect_rate": round(kept / max(i, 1), 3), "frames": frames}
    (out_dir / "poses.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    on_step(f"完成：{i} 格中抽到 {kept} 個姿勢、{missed} 格沒偵測到"
            f"（偵測率 {meta['detect_rate']:.0%}）", 100)
    return meta


def to_video(pose_dir: str | Path, out: str | Path, fps: int = 16) -> Path:
    """把骨架序列壓成一支影片，方便當 VACE 的控制輸入。"""
    import subprocess
    pose_dir, out = Path(pose_dir), Path(out)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps),
                    "-i", str(pose_dir / "pose-%04d.png"),
                    "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(out)],
                   check=True)
    return out


def _main() -> None:
    import argparse, asyncio
    p = argparse.ArgumentParser(description="舞蹈影片 → OpenPose 骨架線框")
    p.add_argument("video")
    p.add_argument("-o", "--out", default=None, help="輸出資料夾，預設 <影片名>-pose/")
    p.add_argument("--fps", type=int, default=16, help="重取樣影格率")
    p.add_argument("--width", type=int, default=480)
    p.add_argument("--height", type=int, default=832, help="VACE 要求可被 32 整除")
    a = p.parse_args()
    out = Path(a.out or (Path(a.video).with_suffix("").name + "-pose"))
    m = extract(a.video, out, fps=a.fps, size=(a.width, a.height),
                on_step=lambda t, pct=None: print(f"  {t}"))
    v = to_video(out, out / "control.mp4", fps=a.fps)
    print(f"\n骨架序列：{out}/pose-*.png（{m['frames_kept']} 格）")
    print(f"控制影片：{v}")
    print(f"偵測率  ：{m['detect_rate']:.0%}" +
          ("   ⚠ 偏低，換一段人物更清楚、全身入鏡的影片" if m["detect_rate"] < 0.8 else ""))


if __name__ == "__main__":
    _main()
