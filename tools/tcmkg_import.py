"""从 TCM-MKG (GraphAI-for-TCM) 导入知识数据，仅增强现有药材名单。

原则（写死在本工具中，不可被数据覆盖）：
  1. 只增不删：所有增强都是追加字段，绝不动已有值；
  2. 已有优先：冲突时以我们现有数据为准；
  3. 安全优先：任何来源数据都不允许削弱毒性/禁忌警示（十八反/十九畏硬规则）；
  4. 名单锁定：仅匹配 herbs.csv 现有 202 味，绝不扩张名单；
  5. 来源可溯：扩充数据在 CSV 中加 source 标记列。

数据来源: TCM-MKG (Zeng Jingqi, GraphAI-for-TCM, Zenodo DOI 见仓库 README)

子命令:
  python tools/tcmkg_import.py download  # 下载原始 TSV 到 tools/_tcmkg/
  python tools/tcmkg_import.py match     # 匹配率验证（第 0 步）
  python tools/tcmkg_import.py aliases   # 别名扩充（第 1 步）
  python tools/tcmkg_import.py properties# 药性校验/回填（第 2 步）
  python tools/tcmkg_import.py pairs     # 药对补充（第 3 步）
"""
import os
import sys
import urllib.request
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KG_DIR = os.path.join(ROOT, "knowledge_graph")
RAW_DIR = os.path.join(ROOT, "tools", "_tcmkg")

HERBS_CSV = os.path.join(KG_DIR, "herbs.csv")
EXTRA_CSV = os.path.join(KG_DIR, "herb_extra.csv")

# TCM-MKG 原始数据（GitHub raw）
BASE_URL = "https://raw.githubusercontent.com/ZENGJingqi/GraphAI-for-TCM/main/Data"
FILES = {
    "pieces.tsv": "Chinese_herbal_pieces.tsv",   # 中文名 + 别名 + 拼音
    "props.tsv": "CHP_Medicinal_properties.tsv",  # 四气/五味/归经
}


def download():
    os.makedirs(RAW_DIR, exist_ok=True)
    for local, remote in FILES.items():
        url = f"{BASE_URL}/{remote}"
        dst = os.path.join(RAW_DIR, local)
        if os.path.exists(dst):
            print(f"[skip] {dst} 已存在")
            continue
        print(f"[get ] {url}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r, open(dst, "wb") as f:
                f.write(r.read())
            print(f"[ok  ] {dst} ({os.path.getsize(dst)} bytes)")
        except Exception as e:
            print(f"[fail] {url}: {e}")
    # 列出文件
    for fn in os.listdir(RAW_DIR):
        print("  -", fn, os.path.getsize(os.path.join(RAW_DIR, fn)), "bytes")


def _load_herbs() -> pd.DataFrame:
    df = pd.read_csv(HERBS_CSV, encoding="utf-8-sig")
    df["name"] = df["name"].astype(str).str.strip()
    return df


def _load_extra() -> pd.DataFrame:
    df = pd.read_csv(EXTRA_CSV, encoding="utf-8-sig")
    df["name"] = df["name"].astype(str).str.strip()
    return df


def _load_pieces() -> pd.DataFrame:
    fp = os.path.join(RAW_DIR, "pieces.tsv")
    df = pd.read_csv(fp, sep="\t", encoding="utf-8")
    return df


def _load_props() -> pd.DataFrame:
    fp = os.path.join(RAW_DIR, "props.tsv")
    df = pd.read_csv(fp, sep="\t", encoding="utf-8")
    return df


def match():
    """第 0 步：匹配率验证。中文名精确匹配 + 别名交叉匹配两策略。"""
    herbs = _load_herbs()
    extra = _load_extra()
    pieces = _load_pieces()

    # TCM-MKG 字段探测
    print("== pieces.tsv 列 ==", list(pieces.columns))
    print("== props.tsv 列 ==", list(_load_props().columns))
    print("pieces 行数:", len(pieces))

    # 收集现有名单的"名 + 已有别名"作为待匹配集合
    target_names = set(herbs["name"])
    target_aliases = set()
    for a in extra["aliases"].dropna():
        for x in str(a).replace("、", ",").split(","):
            x = x.strip()
            if x:
                target_aliases.add(x)

    # 1) 中文名精确匹配：TCM-MKG 中文名 直接命中 我们的名/别名
    name_col = "Chinese_name" if "Chinese_name" in pieces.columns else pieces.columns[1]
    syn_col = "Chinese_synonyms" if "Chinese_synonyms" in pieces.columns else None
    print("\n[策略1] 精确匹配列:", name_col, "| 别名列:", syn_col)

    exact_hit = set()
    for v in pieces[name_col].dropna():
        v = str(v).strip()
        if v in target_names or v in target_aliases:
            exact_hit.add(v)
    print(f"精确命中(名/别名): {len(exact_hit)} 条")

    # 2) TCM-MKG 别名 命中 我们的名
    alias_hit = set()
    if syn_col:
        for v in pieces[syn_col].dropna():
            for a in str(v).split("|"):
                a = a.strip()
                if a in target_names:
                    alias_hit.add(a)
    print(f"别名交叉命中: {len(alias_hit)} 条")

    # 3) 匹配率 = 能被 TCM-MKG 任一命中的我们药材数 / 总数
    matched = set()
    matched_pairs = []  # (herb, tcmkg_name, via)
    for h in sorted(target_names):
        # 直接找 pieces 中该名
        row = pieces[pieces[name_col].astype(str).str.strip() == h]
        if len(row):
            matched.add(h)
            matched_pairs.append((h, h, "exact"))
            continue
        # 找 pieces 别名列中含该名
        if syn_col:
            hit = pieces[pieces[syn_col].fillna("").astype(str)
                         .apply(lambda s: any(a.strip() == h for a in s.split("|")))]
            if len(hit):
                matched.add(h)
                matched_pairs.append((h, hit.iloc[0][name_col], "alias"))
                continue
        # 我们已有别名命中 pieces 中文名
        row_extra = extra[extra["name"] == h]
        if len(row_extra):
            for a in str(row_extra.iloc[0]["aliases"]).replace("、", ",").split(","):
                a = a.strip()
                if a and len(pieces[pieces[name_col].astype(str).str.strip() == a]):
                    matched.add(h)
                    matched_pairs.append((h, a, "our_alias"))
                    break

    rate = len(matched) / len(target_names) * 100
    print(f"\n==== 匹配率: {len(matched)}/{len(target_names)} = {rate:.1f}% ====")
    unmatch = sorted(target_names - matched)
    print(f"未匹配 {len(unmatch)} 味:", "、".join(unmatch))

    # 导出匹配明细供后续步骤使用
    out = os.path.join(RAW_DIR, "match_detail.csv")
    pd.DataFrame(matched_pairs, columns=["herb", "tcmkg_name", "via"]).to_csv(
        out, index=False, encoding="utf-8-sig")
    print(f"\n匹配明细已导出: {out}")
    return rate


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "match"
    fn = {"download": download, "match": match}.get(cmd)
    if fn is None:
        print("未知子命令:", cmd)
        sys.exit(1)
    fn()
