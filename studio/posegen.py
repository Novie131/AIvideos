"""姿勢抽取 —— 影片 → OpenPose 骨架線框序列。

用途：才藝軌（ADR-011）。使用者上傳一段舞蹈影片，抽出每一格的人體骨架，
再交給 Draw Things 的 VACE 依骨架生成「人形小橘跳那支舞」。

**為什麼一定要人形角色**：OpenPose 是人體骨架（肩、肘、腕、髖、膝、踝）。
四足動物沒有對應關節，套上去會生出怪物。見 character.json 的 forms。

輸出刻意畫成 **OpenPose COCO-18 的標準配色與連線**，因為 ControlNet / VACE
的姿勢模型就是拿這個格式訓練的 —— 自創畫法會讓控制失效。
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


def _landmarker():
    """建立 MediaPipe 姿勢偵測器。模型檔首次使用會自動下載（約 10MB）。"""
    import mediapipe as mp
    return mp.solutions.pose.Pose(static_image_mode=False, model_complexity=1,
                                  min_detection_confidence=0.5,
                                  min_tracking_confidence=0.5)


def to_coco18(lms, w: int, h: int) -> list[tuple[float, float] | None]:
    """MediaPipe 的 33 點 → OpenPose 的 18 點（像素座標）。"""
    pts: list[tuple[float, float] | None] = []
    for mp_i in MP_TO_COCO:
        if mp_i is None:
            pts.append(None)                     # neck，稍後補
            continue
        lm = lms[mp_i]
        vis = getattr(lm, "visibility", 1.0)
        pts.append((lm.x * w, lm.y * h) if vis >= MIN_VIS else None)
    # neck = 兩肩中點
    rs, ls = pts[2], pts[5]
    if rs and ls:
        pts[1] = ((rs[0] + ls[0]) / 2, (rs[1] + ls[1]) / 2)
    return pts


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


def extract(video: str | Path, out_dir: str | Path, *, fps: int | None = None,
            size: tuple[int, int] | None = None,
            on_step=lambda text, pct=None: None) -> dict:
    """影片 → 骨架線框 PNG 序列 + 關節座標 JSON。

    fps：重取樣到這個影格率（None = 沿用原片）。影片模型通常吃 16 或 24。
    size：輸出尺寸（None = 沿用原片）。VACE 要求長寬可被 32 整除。
    """
    import cv2
    video, out_dir = Path(video), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"打不開影片：{video}")

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    sw, sh = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    ow, oh = size or (sw, sh)
    step = max(1, round(src_fps / fps)) if fps else 1

    on_step(f"來源 {sw}x{sh} @ {src_fps:.1f}fps，共 {total} 格；輸出 {ow}x{oh}"
            + (f"，每 {step} 格取 1" if step > 1 else ""), 0)

    pose = _landmarker()
    kept, missed, frames = 0, 0, []
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % step:
            i += 1
            continue
        res = pose.process(frame[:, :, ::-1])           # BGR → RGB
        if res.pose_landmarks:
            pts = to_coco18(res.pose_landmarks.landmark, ow, oh)
            draw_pose(pts, ow, oh).save(out_dir / f"pose-{kept:04d}.png")
            frames.append({"i": kept, "src_frame": i,
                           "pts": [list(p) if p else None for p in pts]})
            kept += 1
        else:
            missed += 1
        i += 1
        if total and i % 30 == 0:
            on_step(f"已處理 {i}/{total} 格，抽到 {kept} 個姿勢", round(i / total * 100))
    cap.release()
    pose.close()

    meta = {"source": str(video), "src_fps": round(src_fps, 2), "out_fps": fps or src_fps,
            "size": [ow, oh], "frames_kept": kept, "frames_missed": missed,
            "detect_rate": round(kept / max(kept + missed, 1), 3), "frames": frames}
    (out_dir / "poses.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    on_step(f"完成：{kept} 格有骨架、{missed} 格沒偵測到"
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
