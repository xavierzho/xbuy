import ddddocr
import cv2
import numpy as np
from io import BytesIO
from PIL import Image
from typing import Union, List, Optional, Dict, Any, Tuple
import os
import time


CAPTCHA_LOGIC_WIDTH = 328
SLIDER_HANDLE_OFFSET = 27


class OCRService:
    def __init__(self):
        self.ocr = ddddocr.DdddOcr()
        self.det = ddddocr.DdddOcr(det=True)
        self.slide = ddddocr.DdddOcr(det=False, ocr=False)

    def ocr_classification(self, image: bytes, probability: bool = False, charsets: Optional[str] = None, png_fix: bool = False) -> Union[str, dict]:
        if charsets:
            self.ocr.set_ranges(charsets)
        return self.ocr.classification(image, probability=probability, png_fix=png_fix)

    def slide_match(self, target: bytes, background: bytes, simple_target: bool = False) -> Dict[str, Any]:
        if simple_target and not target:
            return background_only_slide_match(self.det, background)
        try:
            return yidun_slide_match(self.slide, target, background)
        except Exception as e:
            result = enhanced_slide_match(target, background, simple_target)
            result['fallback_reason'] = str(e)
            return result

    def detection(self, image: bytes) -> List[List[int]]:
        return self.det.detection(image)


def load_image_bytes(data: bytes):
    """加载图片，返回 (bgr, alpha_or_None)"""
    img = Image.open(BytesIO(data))
    if img.mode == 'RGBA':
        arr = np.array(img)
        bgr = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2BGR)
        return bgr, arr[:, :, 3]
    arr = np.array(img.convert('RGB'))
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR), None


def find_top_matches(result_map, template_h, template_w, top_n=3, min_conf=0.3):
    """
    从模板匹配结果中提取 top-N 个互不重叠的峰值。
    返回 [(x, y, confidence), ...]
    """
    res = result_map.copy()
    peaks = []
    for _ in range(top_n):
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val < min_conf:
            break
        peaks.append((max_loc[0], max_loc[1], float(max_val)))
        # 抑制已选峰值周围区域，避免重复选取
        x, y = max_loc
        y1 = max(0, y - template_h // 2)
        y2 = min(res.shape[0], y + template_h // 2)
        x1 = max(0, x - template_w // 2)
        x2 = min(res.shape[1], x + template_w // 2)
        res[y1:y2, x1:x2] = 0
    return peaks


def pick_hole_from_candidates(bg_gray, peaks, template_h, template_w):
    """
    从多个候选位置中，选出真正的缺口（而非诱饵）。
    策略：缺口有阴影 → 区域平均亮度更低 / 标准差更低
    """
    if len(peaks) <= 1:
        return peaks[0] if peaks else None

    scored = []
    bh, bw = bg_gray.shape[:2]
    for px, py, conf in peaks:
        # 取匹配区域
        ry1, ry2 = py, min(py + template_h, bh)
        rx1, rx2 = px, min(px + template_w, bw)
        region = bg_gray[ry1:ry2, rx1:rx2]
        if region.size == 0:
            continue
        avg = float(np.mean(region))
        std = float(np.std(region))
        # 缺口特征：更暗（低 avg）且更均匀（低 std，被阴影填充）
        # 诱饵特征：半透明叠加 → 接近正常背景亮度，纹理保留
        # 综合评分：越暗越好，乘以置信度
        darkness_score = (255 - avg) / 255.0
        uniformity_score = max(0, 1 - std / 80.0)
        # 综合评分 = 置信度 * 0.4 + 暗度 * 0.4 + 均匀度 * 0.2
        score = conf * 0.4 + darkness_score * 0.4 + uniformity_score * 0.2
        scored.append((px, py, conf, score, avg, std))

    if not scored:
        return peaks[0]

    scored.sort(key=lambda s: s[3], reverse=True)
    best = scored[0]

    # Debug log
    print(f'[slide] candidates: {[(s[0], round(s[4],1), round(s[5],1), round(s[3],3)) for s in scored]}')

    return (best[0], best[1], best[2])


def normalize_yidun_slider_x(raw_x: int, bg_width: int) -> int:
    if bg_width <= 0:
        return 0
    scale = CAPTCHA_LOGIC_WIDTH / bg_width
    x = int(round(raw_x * scale - SLIDER_HANDLE_OFFSET))
    return max(0, min(CAPTCHA_LOGIC_WIDTH - 1, x))


def background_only_slide_match(det_ocr, bg_bytes: bytes, slider_width: int = 60) -> Dict[str, Any]:
    bg_width = Image.open(BytesIO(bg_bytes)).size[0]
    bboxes = det_ocr.detection(bg_bytes) or []
    best = None
    best_diff = float('inf')
    ideal_area = slider_width * slider_width
    for box in bboxes:
        if not isinstance(box, (list, tuple)) or len(box) < 4:
            continue
        x1, y1, x2, y2 = [float(v) for v in box[:4]]
        w = x2 - x1
        h = y2 - y1
        if w <= 0 or h <= 0:
            continue
        diff = abs(w * h - ideal_area) + abs(w - slider_width) * 5
        if diff < best_diff:
            best = (x1, y1, x2, y2, w, h)
            best_diff = diff
    if best is None:
        return {
            'target': [0, 0],
            'target_x': 0,
            'target_y': 0,
            'raw_target_x': 0,
            'bg_width': bg_width,
            'method': 'background_detection_none',
            'confidence': 0,
            'bboxes': bboxes,
        }

    x1, y1, x2, y2, w, h = best
    raw_x = int(round(((x1 + x2) / 2) - (slider_width / 2)))
    x = normalize_yidun_slider_x(raw_x, bg_width)
    print(f'[slide] background_only raw_x={raw_x} bg_width={bg_width} target_x={x} bbox={best[:4]}')
    return {
        'target': [x, int(round(y1))],
        'target_x': x,
        'target_y': int(round(y1)),
        'raw_target_x': raw_x,
        'bg_width': bg_width,
        'scale': round(CAPTCHA_LOGIC_WIDTH / bg_width, 4) if bg_width else 0,
        'slider_offset': SLIDER_HANDLE_OFFSET,
        'method': 'background_detection',
        'confidence': 0,
        'bbox': [int(round(v)) for v in best[:4]],
        'bboxes': bboxes,
    }


def yidun_slide_match(slide_ocr, target_bytes: bytes, bg_bytes: bytes) -> Dict[str, Any]:
    bg_width = Image.open(BytesIO(bg_bytes)).size[0]
    res = slide_ocr.slide_match(target_bytes, bg_bytes)
    if isinstance(res, dict):
        if isinstance(res.get('target'), (list, tuple)) and res['target']:
            raw_x = res['target'][0]
        elif 'target_x' in res:
            raw_x = res['target_x']
        else:
            raise ValueError(f'ddddocr result missing target: {res}')
    elif isinstance(res, (list, tuple)) and res:
        raw_x = res[0]
    else:
        raise ValueError(f'unexpected ddddocr result: {res}')

    raw_x = int(round(float(raw_x)))
    x = normalize_yidun_slider_x(raw_x, bg_width)
    print(f'[slide] ddddocr_scaled raw_x={raw_x} bg_width={bg_width} target_x={x}')
    return {
        'target': [x],
        'target_x': x,
        'target_y': 0,
        'raw_target_x': raw_x,
        'bg_width': bg_width,
        'scale': round(CAPTCHA_LOGIC_WIDTH / bg_width, 4) if bg_width else 0,
        'slider_offset': SLIDER_HANDLE_OFFSET,
        'method': 'ddddocr_scaled',
        'confidence': 0,
        'agree': 1,
    }


def enhanced_slide_match(target_bytes: bytes, bg_bytes: bytes, simple_target: bool = False) -> Dict[str, Any]:
    """
    高精度滑块匹配 — 多方法 + 诱饵排除 + 共识投票。

    对每种匹配方法，提取 top-3 候选位置，然后用亮度分析
    区分真实的缺口（暗/阴影）和诱饵（半透明覆盖/正常亮度）。
    """
    target_bgr, target_alpha = load_image_bytes(target_bytes)
    bg_bgr, _ = load_image_bytes(bg_bytes)

    bh, bw = bg_bgr.shape[:2]
    candidates = []

    # ── 裁剪 alpha 透明边距（高阈值：只取不透明实体）──
    pad_left = 0
    pad_top = 0
    cropped = target_bgr

    if target_alpha is not None:
        opaque_hard = (target_alpha > 128).astype(np.uint8)
        coords_hard = cv2.findNonZero(opaque_hard)
        if coords_hard is not None:
            rx, ry, rw, rh = cv2.boundingRect(coords_hard)
            if rw > 5 and rh > 5:
                pad_left = rx
                pad_top = ry
                cropped = target_bgr[ry:ry+rh, rx:rx+rw]

    ch, cw = cropped.shape[:2]

    if ch >= bh or cw >= bw or ch < 5 or cw < 5:
        return {'target': [0, 0], 'target_x': 0, 'target_y': 0,
                'confidence': 0, 'method': 'fallback', 'agree': 0,
                'debug': f'crop={cw}x{ch} bg={bw}x{bh} pad_left={pad_left}'}

    cg = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)
    bg_gray = cv2.cvtColor(bg_bgr, cv2.COLOR_BGR2GRAY)

    # ── 方法 A: Alpha 轮廓形状匹配 ──
    if target_alpha is not None:
        try:
            alpha_binary = (target_alpha > 128).astype(np.uint8) * 255
            contours, _ = cv2.findContours(alpha_binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if contours:
                shape_edges = np.zeros_like(alpha_binary)
                cv2.drawContours(shape_edges, contours, -1, 255, 2)
                shape_cropped = shape_edges[pad_top:pad_top+ch, pad_left:pad_left+cw]
                bg_edges = cv2.Canny(bg_gray, 50, 150)
                if shape_cropped.shape[0] < bg_edges.shape[0] and shape_cropped.shape[1] < bg_edges.shape[1]:
                    res = cv2.matchTemplate(bg_edges, shape_cropped, cv2.TM_CCOEFF_NORMED)
                    peaks = find_top_matches(res, ch, cw, top_n=3, min_conf=0.2)
                    best = pick_hole_from_candidates(bg_gray, peaks, ch, cw)
                    if best:
                        candidates.append(('shape', best[0], best[1], best[2]))
        except Exception as e:
            print(f'[slide] shape error: {e}')

    # ── 方法 B: 灰度 CCOEFF_NORMED ──
    try:
        res = cv2.matchTemplate(bg_gray, cg, cv2.TM_CCOEFF_NORMED)
        peaks = find_top_matches(res, ch, cw, top_n=3, min_conf=0.3)
        best = pick_hole_from_candidates(bg_gray, peaks, ch, cw)
        if best:
            candidates.append(('ccoeff', best[0], best[1], best[2]))
    except Exception:
        pass

    # ── 方法 C: 灰度 SQDIFF_NORMED ──
    try:
        res = cv2.matchTemplate(bg_gray, cg, cv2.TM_SQDIFF_NORMED)
        # SQDIFF: 反转结果，取最小值区域
        res_inv = 1.0 - res
        peaks = find_top_matches(res_inv, ch, cw, top_n=3, min_conf=0.5)
        best = pick_hole_from_candidates(bg_gray, peaks, ch, cw)
        if best:
            candidates.append(('sqdiff', best[0], best[1], best[2]))
    except Exception:
        pass

    # ── 方法 D: Canny 边缘 CCOEFF_NORMED ──
    try:
        te = cv2.Canny(cg, 50, 150)
        be = cv2.Canny(bg_gray, 50, 150)
        res = cv2.matchTemplate(be, te, cv2.TM_CCOEFF_NORMED)
        peaks = find_top_matches(res, ch, cw, top_n=3, min_conf=0.2)
        best = pick_hole_from_candidates(bg_gray, peaks, ch, cw)
        if best:
            candidates.append(('edge', best[0], best[1], best[2]))
    except Exception:
        pass

    # ── 方法 E: 高斯模糊灰度 CCOEFF ──
    try:
        cg_blur = cv2.GaussianBlur(cg, (3, 3), 0)
        bg_blur = cv2.GaussianBlur(bg_gray, (3, 3), 0)
        res = cv2.matchTemplate(bg_blur, cg_blur, cv2.TM_CCOEFF_NORMED)
        peaks = find_top_matches(res, ch, cw, top_n=3, min_conf=0.3)
        best = pick_hole_from_candidates(bg_gray, peaks, ch, cw)
        if best:
            candidates.append(('blur', best[0], best[1], best[2]))
    except Exception:
        pass

    if not candidates:
        return {'target': [0, 0], 'target_x': 0, 'target_y': 0,
                'confidence': 0, 'method': 'none', 'agree': 0}

    # ── 共识投票：找最大一致簇 ──
    AGREE_THRESHOLD = 18  # px
    best_cluster = []
    best_cluster_score = 0

    for i, anchor in enumerate(candidates):
        cluster = [anchor]
        for j, other in enumerate(candidates):
            if i != j and abs(anchor[1] - other[1]) <= AGREE_THRESHOLD:
                cluster.append(other)
        score = len(cluster) * 100 + sum(c[3] for c in cluster) / len(cluster)
        if score > best_cluster_score:
            best_cluster = cluster
            best_cluster_score = score

    # 簇内取置信度加权平均坐标
    total_w = sum(max(c[3], 0.01) for c in best_cluster)
    bx = int(round(sum(c[1] * max(c[3], 0.01) for c in best_cluster) / total_w))
    by = int(round(sum(c[2] * max(c[3], 0.01) for c in best_cluster) / total_w))
    normalized_x = normalize_yidun_slider_x(bx, bw)

    method_names = '+'.join(c[0] for c in best_cluster)
    conf = sum(c[3] for c in best_cluster) / len(best_cluster)

    return {
        'target': [normalized_x, by],
        'target_x': normalized_x,
        'target_y': by,
        'raw_target_x': bx,
        'bg_width': bw,
        'scale': round(CAPTCHA_LOGIC_WIDTH / bw, 4) if bw else 0,
        'slider_offset': SLIDER_HANDLE_OFFSET,
        'confidence': round(conf, 4),
        'method': method_names,
        'agree': len(best_cluster),
        'pad_left': pad_left,
        'all': [{'m': c[0], 'x': c[1], 'conf': round(c[3], 4)} for c in candidates],
    }


ocr_service = OCRService()
