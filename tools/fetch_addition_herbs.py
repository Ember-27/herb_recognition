"""为「相似药推荐中不在训练名单」的 34 味药材准备图片，放入 images/addition/。

背景:
    - 视觉模型训练集共 163 类（156 味去重），图鉴 images/图鉴/ 只覆盖这些类。
    - 知识图谱的相似药推荐会带出 34 味训练集之外的药材（黄芪/当归/黄连…），
      这些药材在相似药卡片里取不到缩略图。
    - 本脚本把它们的图片放进独立的 images/addition/ 目录，并生成
      images/addition/herb_map.json（中文名 -> 拼音文件名），供后端兜底取图用。

可独立删除:
    - 删除 images/addition/ 目录 + 本脚本即可彻底移除本次补充，不影响图鉴与主流程。

取图策略（按优先级）:
    1) --from <本地图源根>：递归在该目录下按拼音文件名(如 huangqi.jpg)匹配并拷贝。
       最可靠，推荐用你自己的药材图库。也支持按「中文名.jpg」命名，会自动转拼音名。
    2) 不带 --from 时尝试 best-effort 联网下载（Wikimedia，中文药材图覆盖不全，
       可能多数失败，失败项会列清单让你手动补）。

用法:
    python tools/fetch_addition_herbs.py --from D:/my_herb_images
    python tools/fetch_addition_herbs.py --only huangqi danggui
    python tools/fetch_addition_herbs.py --skip-download     # 仅生成映射(手动放图)

依赖: requests（联网模式需要）；本地拷贝模式无额外依赖。
"""
import argparse
import json
import os
import shutil
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "images", "addition")
MAP_FILE = os.path.join(OUT_DIR, "herb_map.json")

# 34 味「相似药推荐候选但不在训练名单」的药材：中文名 -> 拼音文件名
# 拼音规则与 build_herb_atlas.py / 图鉴目录一致（全拼小写、无空格、无声调）
HERB_LIST = [
    ("黄芪", "huangqi"),
    ("当归", "danggui"),
    ("熟地", "shudi"),
    ("生地", "shengdi"),
    ("地黄", "dihuang"),
    ("沙参", "shashen"),
    ("玄参", "xuanshen"),
    ("黄连", "huanglian"),
    ("栀子", "zhizi"),
    ("桂枝", "guizhi"),
    ("薄荷", "bohe"),
    ("苍耳子", "cangerzi"),
    ("藿香", "huoxiang"),
    ("杏仁", "xingren"),
    ("款冬花", "kuandonghua"),
    ("半夏", "banxia"),
    ("龟甲", "guijia"),
    ("石决明", "shijueming"),
    ("朱砂", "zhusha"),
    ("蛤蚧", "gejie"),
    ("高良姜", "gaoliangjiang"),
    ("吴茱萸", "wuzhuyu"),
    ("乌药", "wuyao"),
    ("王不留行", "wangbuliuxing"),
    ("自然铜", "zirantong"),
    ("郁李仁", "yuliren"),
    ("木通", "mutong"),
    ("忍冬藤", "rendongteng"),
    ("半枝莲", "banzhilian"),
    ("小蓟", "xiaoji"),
    ("旱莲草", "hanliancao"),
    ("黑芝麻", "heizhima"),
    ("诃子", "hezi"),
    ("山豆根", "shandougen"),
    ("胖大海", "pangdahai"),
]

EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


def _valid_image(path: str) -> bool:
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:
        return False


# ---- 本地图源拷贝 -----------------------------------------------------------

def _collect_by_basename(src_root: str):
    """返回 {小写无扩展文件名: 绝对路径} 与 {中文名: 绝对路径} 两个索引。"""
    by_py, by_zh = {}, {}
    for dp, _, fns in os.walk(src_root):
        for fn in fns:
            if not fn.lower().endswith(EXTS):
                continue
            full = os.path.join(dp, fn)
            base = os.path.splitext(fn)[0]
            by_py.setdefault(base.lower(), full)
            by_zh.setdefault(base, full)  # 中文名原样
    return by_py, by_zh


def _copy_from_local(src_root: str, items):
    """从本地图源拷贝。优先按拼音名匹配，其次按中文名匹配(转成拼音名输出)。"""
    by_py, by_zh = _collect_by_basename(src_root)
    ok, fail = [], []
    for zh, py in items:
        dst = os.path.join(OUT_DIR, py + ".jpg")
        if os.path.exists(dst):
            ok.append(py)
            continue
        src = by_py.get(py) or by_py.get(py.lower()) or by_zh.get(zh)
        if src and _valid_image(src):
            shutil.copyfile(src, dst)
            ok.append(py)
            print(f"  [OK] {zh} <- {os.path.relpath(src, src_root)} -> {py}.jpg")
        else:
            fail.append((zh, py))
            print(f"  [MISS] {zh} ({py}) 在本地图源中未找到，请手动补图")
    return ok, fail


# ---- best-effort 联网下载 (Wikimedia) --------------------------------------

def _wikimedia_image_url(zh_name: str):
    try:
        import requests
    except ImportError:
        return None
    api = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query", "format": "json", "prop": "pageimages",
        "piprop": "original",
        "titles": f"File:{zh_name} (药材).jpg|File:{zh_name}.jpg|File:{zh_name}",
        "redirects": 1,
    }
    try:
        r = requests.get(api, params=params, timeout=15,
                         headers={"User-Agent": "herb-recognition/1.0"})
        if r.status_code != 200:
            return None
        pages = (r.json().get("query") or {}).get("pages") or {}
        for p in pages.values():
            orig = (p.get("original") or {}).get("source")
            if orig:
                return orig
    except Exception:
        return None
    return None


def _download(url: str, dst: str) -> bool:
    try:
        import requests
        r = requests.get(url, timeout=25,
                        headers={"User-Agent": "herb-recognition/1.0"})
        if r.status_code != 200 or not r.content:
            return False
        with open(dst, "wb") as f:
            f.write(r.content)
        return _valid_image(dst)
    except Exception:
        return False


def _download_online(items):
    ok, fail = [], []
    for zh, py in items:
        dst = os.path.join(OUT_DIR, py + ".jpg")
        if os.path.exists(dst):
            ok.append(py)
            continue
        url = _wikimedia_image_url(zh)
        if url and _download(url, dst):
            ok.append(py)
            print(f"  [OK] {zh} -> {py}.jpg")
        else:
            fail.append((zh, py))
            print(f"  [FAIL] {zh} ({py}) 联网取图失败，请手动补图")
        time.sleep(0.3)
    return ok, fail


# ---- 主流程 -----------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", default=None,
                    help="本地图源根目录，递归按拼音/中文名匹配拷贝")
    ap.add_argument("--only", nargs="*", default=None,
                    help="只处理指定拼音(如 huangqi danggui)")
    ap.add_argument("--skip-download", action="store_true",
                    help="仅生成 herb_map.json，不取图(用于手动放图)")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)

    items = HERB_LIST
    if args.only:
        wanted = set(args.only)
        items = [(zh, py) for zh, py in HERB_LIST if py in wanted]
        if not items:
            print(f"[WARN] --only 指定的拼音都不在清单内: {args.only}")
            return

    # 1) 始终写映射文件，便于后端引用
    herb_map = {zh: py + ".jpg" for zh, py in HERB_LIST}
    with open(MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(herb_map, f, ensure_ascii=False, indent=2)
    print(f"[INFO] 已写入映射文件 {MAP_FILE}（{len(herb_map)} 条）")

    if args.skip_download:
        print("[INFO] --skip-download：跳过取图，请手动把图片放到 images/addition/")
        return

    if args.src:
        ok, fail = _copy_from_local(args.src, items)
    else:
        print("[INFO] 未指定 --from，尝试 best-effort 联网下载(Wikimedia 中文药材图覆盖不全)…")
        ok, fail = _download_online(items)

    print(f"\n[SUMMARY] 成功 {len(ok)} / 失败 {len(fail)} / 共 {len(items)}")
    if fail:
        print("[需手动补图] 把对应图片命名为以下文件名放到 images/addition/ :")
        for zh, py in fail:
            print(f"    {zh} -> {py}.jpg")


if __name__ == "__main__":
    main()
