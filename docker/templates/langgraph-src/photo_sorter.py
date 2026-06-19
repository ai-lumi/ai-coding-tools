"""
照片分拣工作流

用于整理备份到 NAS 上的各种照片（手机相册、单反相机备份等）。

整理规则：
1. 普通照片按 【yyyyMMdd-地点】 放入目标目录；无法识别地点时仅使用 【yyyyMMdd】。
2. 截图、证件、发票等图文类照片统一放入 【图文】 目录，不再细分日期/地点。
3. 处理完成后生成 Markdown 报告，列出每个目录下的重复文件以及相似度超过 90% 的图片对。

输入状态（PhotoState）：
- source_dir: 待整理照片根目录
- target_dir: 整理后照片存放目录
"""

import hashlib
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TypedDict

from PIL import Image

from langgraph.graph import StateGraph, END

# 导入统一记忆层
from src.memory import append_daily_note

# piexif 用于读取 EXIF；若不可用则 gracefully 降级
_PIEXIF_AVAILABLE = False
try:
    import piexif

    _PIEXIF_AVAILABLE = True
except Exception:  # pragma: no cover
    piexif = None  # type: ignore


# ============================================================
# 常量与配置
# ============================================================

PHOTO_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    ".heif",
    ".raw",
    ".cr2",
    ".nef",
    ".arw",
    ".rw2",
    ".webp",
    ".bmp",
    ".gif",
    ".tiff",
    ".tif",
}

TEXT_IMAGE_DIR = "图文"
UNKNOWN_LOCATION = ""  # 地点未知时不添加后缀

# dHash 汉明距离阈值：<= 6 视为高度相似（约 90%+ 视觉相似，经验值）
SIMILARITY_THRESHOLD = 6

# 常见地点关键词（文件名/路径中命中即可作为地点）
LOCATION_KEYWORDS = {
    "北京": ["beijing", "北京", "peking"],
    "上海": ["shanghai", "上海"],
    "广州": ["guangzhou", "广州", "canton"],
    "深圳": ["shenzhen", "深圳"],
    "杭州": ["hangzhou", "杭州"],
    "成都": ["chengdu", "成都"],
    "西安": ["xian", "西安"],
    "重庆": ["chongqing", "重庆"],
    "武汉": ["wuhan", "武汉"],
    "南京": ["nanjing", "南京"],
    "苏州": ["suzhou", "苏州"],
    "厦门": ["xiamen", "厦门"],
    "青岛": ["qingdao", "青岛"],
    "大连": ["dalian", "大连"],
    "香港": ["hongkong", "香港", "hk"],
    "澳门": ["macao", "澳门", "macau"],
    "台湾": ["taiwan", "台湾", "taipei", "台北"],
    "东京": ["tokyo", "东京"],
    "大阪": ["osaka", "大阪"],
    "京都": ["kyoto", "京都"],
    "首尔": ["seoul", "首尔"],
    "曼谷": ["bangkok", "曼谷"],
    "新加坡": ["singapore", "新加坡"],
    "巴黎": ["paris", "巴黎"],
    "伦敦": ["london", "伦敦"],
    "纽约": ["new york", "纽约", "nyc"],
    "旧金山": ["san francisco", "旧金山"],
    "洛杉矶": ["los angeles", "洛杉矶"],
    "悉尼": ["sydney", "悉尼"],
}


# ============================================================
# 状态定义
# ============================================================

class PhotoState(TypedDict):
    """照片分拣任务状态"""

    source_dir: str
    target_dir: str
    photos: List[str]
    results: List[Dict[str, Any]]
    duplicates: List[Dict[str, Any]]
    similars: List[Dict[str, Any]]
    report_path: Optional[str]
    errors: List[str]


# ============================================================
# 通用工具函数
# ============================================================

def _safe_relative(path: str, base: str) -> str:
    """计算相对路径，失败则返回原路径"""
    try:
        return str(Path(path).relative_to(base))
    except Exception:
        return path


def parse_date_from_filename(filename: str) -> Optional[datetime]:
    """从文件名中提取日期（支持 20240101 与 2024-01-01 等）"""
    # 优先匹配 8 位连续数字日期
    match = re.search(r"(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])", filename)
    if match:
        try:
            return datetime.strptime(match.group(0), "%Y%m%d")
        except ValueError:
            pass
    # 再尝试 yyyy-MM-dd / yyyy/MM/dd
    match = re.search(r"(19|20)\d{2}[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])", filename)
    if match:
        try:
            sep = match.group(0)[4]
            return datetime.strptime(match.group(0), f"%Y{sep}%m{sep}%d")
        except ValueError:
            pass
    return None


def parse_exif_datetime(value: Any) -> Optional[datetime]:
    """解析 EXIF 日期字符串，常见格式 2024:01:01 12:00:00"""
    if value is None:
        return None
    text = value.decode() if isinstance(value, bytes) else str(value)
    text = text.strip()
    try:
        return datetime.strptime(text[:19], "%Y:%m:%d %H:%M:%S")
    except Exception:
        return None


def _dms_to_decimal(dms: Any, ref: Any) -> float:
    """将 EXIF GPS 度分秒转换为十进制度数"""
    degrees = float(dms[0][0]) / float(dms[0][1])
    minutes = float(dms[1][0]) / float(dms[1][1])
    seconds = float(dms[2][0]) / float(dms[2][1])
    decimal = degrees + minutes / 60.0 + seconds / 3600.0
    ref_str = ref.decode() if isinstance(ref, bytes) else str(ref)
    if ref_str in ("S", "W"):
        decimal = -decimal
    return decimal


def compute_md5(path: str) -> str:
    """计算文件 MD5"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_dhash(path: str) -> Optional[str]:
    """
    计算图片的 dHash（差值哈希）。
    返回 16 进制字符串，用于感知相似度比较。
    """
    try:
        with Image.open(path) as img:
            # 转换为灰度并缩放到 9x8（dHash 标准尺寸）
            gray = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
            pixels = list(gray.getdata())
            diff_bits: List[bool] = []
            for row in range(8):
                row_start = row * 9
                for col in range(8):
                    diff_bits.append(pixels[row_start + col] > pixels[row_start + col + 1])
            # 64 bits -> 16 进制字符串
            hex_chars = []
            for i in range(0, 64, 4):
                nibble = 0
                for j in range(4):
                    nibble = (nibble << 1) | int(diff_bits[i + j])
                hex_chars.append(format(nibble, "x"))
            return "".join(hex_chars)
    except Exception:
        return None


def hamming_distance(hash_a: str, hash_b: str) -> int:
    """计算两个十六进制哈希字符串的汉明距离"""
    if len(hash_a) != len(hash_b):
        return 9999
    return sum(bin(int(a, 16) ^ int(b, 16)).count("1") for a, b in zip(hash_a, hash_b))


# ============================================================
# 照片元数据提取
# ============================================================

def extract_date_str(photo_path: str) -> str:
    """提取照片日期，优先级：文件名 > EXIF DateTimeOriginal > EXIF DateTimeDigitized > EXIF DateTime > 文件 mtime"""
    path = Path(photo_path)

    # 1) 文件名日期
    parsed = parse_date_from_filename(path.name)
    if parsed:
        return parsed.strftime("%Y%m%d")

    # 2) EXIF
    if _PIEXIF_AVAILABLE:
        try:
            exif_dict = piexif.load(str(path))  # type: ignore
            exif_tags = exif_dict.get("Exif", {})
            image_tags = exif_dict.get("0th", {})
            for tag in (
                piexif.ExifIFD.DateTimeOriginal,  # type: ignore
                piexif.ExifIFD.DateTimeDigitized,  # type: ignore
                piexif.ImageIFD.DateTime,  # type: ignore
            ):
                value = exif_tags.get(tag) or image_tags.get(tag)
                parsed = parse_exif_datetime(value)
                if parsed:
                    return parsed.strftime("%Y%m%d")
        except Exception:
            pass

    # 3) 文件修改时间兜底
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    return mtime.strftime("%Y%m%d")


def extract_gps(photo_path: str) -> Optional[Tuple[float, float]]:
    """从 EXIF 提取 GPS 经纬度（十进制度数）"""
    if not _PIEXIF_AVAILABLE:
        return None
    try:
        exif_dict = piexif.load(str(photo_path))  # type: ignore
        gps = exif_dict.get("GPS", {})
        if not gps:
            return None
        lat = gps.get(piexif.GPSIFD.GPSLatitude)  # type: ignore
        lat_ref = gps.get(piexif.GPSIFD.GPSLatitudeRef)  # type: ignore
        lon = gps.get(piexif.GPSIFD.GPSLongitude)  # type: ignore
        lon_ref = gps.get(piexif.GPSIFD.GPSLongitudeRef)  # type: ignore
        if lat and lon and lat_ref and lon_ref:
            return (_dms_to_decimal(lat, lat_ref), _dms_to_decimal(lon, lon_ref))
    except Exception:
        pass
    return None


def infer_location_from_path(photo_path: str) -> str:
    """
    基于文件名/路径关键词推断地点。
    返回空字符串表示无法识别。
    """
    text = photo_path.lower()
    for location, keywords in LOCATION_KEYWORDS.items():
        if any(k.lower() in text for k in keywords):
            return location
    return ""


def classify_photo(photo_path: str) -> str:
    """
    判断照片是否属于图文类（截图、证件、发票、笔记等）。
    返回 "text_image" 或 "photo"。
    """
    name_lower = Path(photo_path).name.lower()

    screenshot_keywords = [
        "screenshot",
        "screen",
        "截屏",
        "截图",
        "snip",
        "screensnap",
        "wx_camera_",
        "mmexport",  # 微信导出
        "wechat",
        "weixin",
    ]
    if any(k in name_lower for k in screenshot_keywords):
        return "text_image"

    document_keywords = [
        "id",
        "身份证",
        "护照",
        "驾照",
        "驾驶证",
        "证件",
        "passport",
        "license",
        "certificate",
        "card",
        "note",
        "笔记",
        "doc",
        "文档",
        "text",
        "文字",
        "receipt",
        "发票",
        "ticket",
        "票",
        "合同",
        "contrast",
    ]
    if any(k in name_lower for k in document_keywords):
        return "text_image"

    return "photo"


def analyze_single_photo(photo_path: str, source_dir: str) -> Dict[str, Any]:
    """分析单张照片，提取日期、地点、类型等元数据"""
    path = Path(photo_path)

    date_str = extract_date_str(photo_path)
    category = classify_photo(photo_path)
    gps = extract_gps(photo_path)

    # 地点优先级：EXIF GPS 坐标 > 文件名/路径关键词
    if gps:
        # 保留两位小数，避免坐标过于细碎导致目录过多
        location = f"GPS{gps[0]:.2f},{gps[1]:.2f}"
    else:
        location = infer_location_from_path(photo_path)

    return {
        "source_path": str(path),
        "relative_path": _safe_relative(str(path), source_dir),
        "date_str": date_str,
        "location": location,
        "category": category,
        "is_text_image": category == "text_image",
        "gps": gps,
    }


# ============================================================
# LangGraph 节点
# ============================================================

def scan_photos(state: PhotoState) -> PhotoState:
    """扫描源目录中的所有照片文件"""
    source = state["source_dir"]
    photos = []
    for f in Path(source).rglob("*"):
        if f.suffix.lower() in PHOTO_EXTENSIONS:
            photos.append(str(f))
    photos.sort()

    append_daily_note(f"扫描到 {len(photos)} 张照片，来源: {source}", "photo_sorter")
    return {
        **state,
        "photos": photos,
        "results": [],
        "duplicates": [],
        "similars": [],
        "errors": [],
    }


def analyze_photos(state: PhotoState) -> PhotoState:
    """分析所有照片：提取日期、地点、类型"""
    source_dir = state["source_dir"]
    results: List[Dict[str, Any]] = []
    errors: List[str] = []

    for photo in state.get("photos", []):
        try:
            info = analyze_single_photo(photo, source_dir)
            results.append(info)
        except Exception as e:
            errors.append(f"分析失败 {photo}: {e}")
            # 记录一个降级结果，避免该照片完全丢失
            results.append(
                {
                    "source_path": photo,
                    "relative_path": _safe_relative(photo, source_dir),
                    "date_str": datetime.fromtimestamp(Path(photo).stat().st_mtime).strftime("%Y%m%d"),
                    "location": "",
                    "category": "photo",
                    "is_text_image": False,
                    "gps": None,
                    "error": str(e),
                }
            )

    append_daily_note(f"已分析 {len(results)} 张照片", "photo_sorter")
    return {**state, "results": results, "errors": errors}


def detect_duplicates(state: PhotoState) -> PhotoState:
    """检测完全重复文件和高度相似的图片"""
    results = state.get("results", [])
    errors = state.get("errors", [])

    md5_map: Dict[str, List[str]] = {}
    dhash_map: Dict[str, str] = {}

    for r in results:
        source = r.get("source_path")
        if not source or r.get("error"):
            continue

        # 完全重复
        try:
            file_hash = compute_md5(source)
            md5_map.setdefault(file_hash, []).append(source)
        except Exception as e:
            errors.append(f"计算 MD5 失败 {source}: {e}")

        # 感知哈希
        try:
            dhash = compute_dhash(source)
            if dhash:
                dhash_map[source] = dhash
        except Exception as e:
            errors.append(f"计算 dHash 失败 {source}: {e}")

    duplicates = [
        {"type": "duplicate", "hash": file_hash, "paths": paths}
        for file_hash, paths in md5_map.items()
        if len(paths) > 1
    ]

    similars: List[Dict[str, Any]] = []
    sources = list(dhash_map.keys())
    for i in range(len(sources)):
        for j in range(i + 1, len(sources)):
            dist = hamming_distance(dhash_map[sources[i]], dhash_map[sources[j]])
            if dist <= SIMILARITY_THRESHOLD:
                # 粗略将哈希距离映射为相似度百分比，仅作报告参考
                similarity_pct = max(0, 100 - dist * 2)
                similars.append(
                    {
                        "type": "similar",
                        "path_a": sources[i],
                        "path_b": sources[j],
                        "similarity": similarity_pct,
                        "hash_distance": dist,
                    }
                )

    return {**state, "duplicates": duplicates, "similars": similars, "errors": errors}


def organize_photos(state: PhotoState) -> PhotoState:
    """将照片复制到目标目录结构中"""
    target_base = state["target_dir"]
    results = state.get("results", [])
    errors = state.get("errors", [])

    for r in results:
        source = r.get("source_path")
        if not source:
            continue

        try:
            date_str = r.get("date_str", "unknown")
            location = r.get("location", "")
            is_text_image = r.get("is_text_image", False)

            if is_text_image:
                target_dir = os.path.join(target_base, TEXT_IMAGE_DIR)
            else:
                if location:
                    target_dir = os.path.join(target_base, f"{date_str}-{location}")
                else:
                    target_dir = os.path.join(target_base, date_str)

            os.makedirs(target_dir, exist_ok=True)
            dest = os.path.join(target_dir, Path(source).name)
            shutil.copy2(source, dest)
            r["target_path"] = dest
        except Exception as e:
            errors.append(f"复制失败 {source}: {e}")

    return {**state, "errors": errors}


def generate_report(state: PhotoState) -> PhotoState:
    """生成分拣报告 Markdown 文件"""
    target_base = state["target_dir"]
    results = state.get("results", [])
    duplicates = state.get("duplicates", [])
    similars = state.get("similars", [])
    errors = state.get("errors", [])

    # 按目标目录分组结果
    dir_to_results: Dict[str, List[Dict[str, Any]]] = {}
    for r in results:
        tp = r.get("target_path")
        if tp:
            dir_path = str(Path(tp).parent)
            dir_to_results.setdefault(dir_path, []).append(r)

    # 建立 source_path -> target_path 映射，便于后续查找
    source_to_target: Dict[str, str] = {
        r["source_path"]: r.get("target_path", "")
        for r in results
        if r.get("target_path")
    }

    def _target_dir_of(source_path: str) -> Optional[str]:
        tp = source_to_target.get(source_path)
        if tp:
            return str(Path(tp).parent)
        return None

    # 按目标目录分组重复/相似问题
    dir_to_duplicates: Dict[str, List[Dict[str, Any]]] = {}
    for dup in duplicates:
        seen_dirs = set()
        for p in dup.get("paths", []):
            d = _target_dir_of(p)
            if d and d not in seen_dirs:
                dir_to_duplicates.setdefault(d, []).append(dup)
                seen_dirs.add(d)

    dir_to_similars: Dict[str, List[Dict[str, Any]]] = {}
    for sim in similars:
        for p in (sim.get("path_a"), sim.get("path_b")):
            d = _target_dir_of(p)
            if d:
                dir_to_similars.setdefault(d, []).append(sim)
                break

    problem_dirs = sorted(set(dir_to_duplicates.keys()) | set(dir_to_similars.keys()))

    report_path = os.path.join(target_base, "分拣报告.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 照片分拣报告\n\n")
        f.write(f"- **处理时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- **源目录**: {state['source_dir']}\n")
        f.write(f"- **目标目录**: {target_base}\n")
        f.write(f"- **处理照片总数**: {len(results)}\n\n")

        # 统计
        text_image_count = sum(1 for r in results if r.get("is_text_image"))
        f.write("## 统计\n\n")
        f.write(f"- 普通照片: {len(results) - text_image_count}\n")
        f.write(f"- 图文类照片: {text_image_count}\n")
        f.write(f"- 完全重复组: {len(duplicates)}\n")
        f.write(f"- 高度相似图片对: {len(similars)}\n\n")

        if errors:
            f.write("## 错误与警告\n\n")
            for err in errors[:50]:
                f.write(f"- {err}\n")
            if len(errors) > 50:
                f.write(f"- ... 还有 {len(errors) - 50} 条错误未显示\n")
            f.write("\n")

        # 按目录问题汇总
        f.write("## 按目录问题汇总\n\n")
        if not problem_dirs:
            f.write("未发现重复或高度相似的图片。\n\n")
        else:
            f.write("以下目录存在重复文件或相似度超过 90% 的图片：\n\n")
            for dir_path in problem_dirs:
                rel_dir = os.path.relpath(dir_path, target_base)
                dup_count = len(dir_to_duplicates.get(dir_path, []))
                sim_count = len(dir_to_similars.get(dir_path, []))
                items = []
                if dup_count:
                    items.append(f"重复组 {dup_count} 个")
                if sim_count:
                    items.append(f"相似对 {sim_count} 个")
                f.write(f"- **{rel_dir}**: {', '.join(items)}\n")
            f.write("\n")

        # 目录结构
        f.write("## 目录结构\n\n")
        if not dir_to_results:
            f.write("未生成任何目标目录。\n\n")
        for dir_path in sorted(dir_to_results.keys()):
            rel_dir = os.path.relpath(dir_path, target_base)
            has_problem = dir_path in problem_dirs
            f.write(f"### {rel_dir}")
            if has_problem:
                f.write(" ⚠️ 存在重复或相似图片")
            f.write("\n\n")
            for r in dir_to_results[dir_path]:
                source_name = Path(r["source_path"]).name
                f.write(f"- {source_name}")
                if r.get("relative_path") and r["relative_path"] != source_name:
                    f.write(f" （来自 `{r['relative_path']}`）")
                f.write("\n")
            f.write("\n")

        # 重复文件
        f.write("## 重复文件（按 MD5 完全一致）\n\n")
        if not duplicates:
            f.write("未发现完全重复的文件。\n\n")
        else:
            for dup in duplicates:
                f.write(f"- MD5: `{dup['hash']}`\n")
                for p in dup["paths"]:
                    tp = source_to_target.get(p)
                    rel = os.path.relpath(tp, target_base) if tp else p
                    f.write(f"  - {rel}\n")
                f.write("\n")

        # 相似文件
        f.write(f"## 相似图片（感知哈希距离 <= {SIMILARITY_THRESHOLD}，约 90%+ 视觉相似）\n\n")
        if not similars:
            f.write("未发现相似度超过 90% 的图片。\n\n")
        else:
            for sim in similars:
                tp_a = source_to_target.get(sim["path_a"])
                tp_b = source_to_target.get(sim["path_b"])
                rel_a = os.path.relpath(tp_a, target_base) if tp_a else sim["path_a"]
                rel_b = os.path.relpath(tp_b, target_base) if tp_b else sim["path_b"]
                f.write(
                    f"- 估算相似度: {sim['similarity']}%, 哈希距离: {sim['hash_distance']}\n"
                )
                f.write(f"  - {rel_a}\n")
                f.write(f"  - {rel_b}\n")
                f.write("\n")

    append_daily_note(f"照片分拣完成，报告: {report_path}", "photo_sorter")
    return {**state, "report_path": report_path}


# ============================================================
# 工作流图构建
# ============================================================

def build_graph():
    """构建照片分拣工作流图"""
    graph = StateGraph(PhotoState)

    graph.add_node("scan", scan_photos)
    graph.add_node("analyze", analyze_photos)
    graph.add_node("detect", detect_duplicates)
    graph.add_node("organize", organize_photos)
    graph.add_node("report", generate_report)

    graph.set_entry_point("scan")
    graph.add_edge("scan", "analyze")
    graph.add_edge("analyze", "detect")
    graph.add_edge("detect", "organize")
    graph.add_edge("organize", "report")
    graph.add_edge("report", END)

    return graph.compile()


graph = build_graph()
