"""知识图谱构建与查询 (内存版 Networkx)。

提供：从 CSV 加载草药节点与多类型关系、查询某草药的药性/归经/功效、
配伍推荐(相须相使)、配伍禁忌(十八反/十九畏)、按功效分类的相似药推荐，
以及"方剂推荐"——综合主治功效匹配、常用配伍、禁忌规避进行打分排序。

关系类型 (edge relation):
  - paired        : 相须/相使，常一起配伍 (双向)
  - incompatible  : 十八反，绝对禁用同方 (双向)
  - restraint     : 十九畏，尽量避免同方 (双向)
方剂君臣佐使通过 external 方剂表加载后为节点打 role 标签。

如需更大规模/持久化，可将 use_neo4j 置为 true 并改用 py2neo（见 TODO）。
"""
from typing import Dict, List, Optional, Tuple, Union
import os
import re
import json
import pandas as pd
import networkx as nx
from knowledge_graph.confusable_herbs import get_confusable


# ---------------------------------------------------------------------------
# 中医药学经典配伍禁忌规则（领域常识，独立于数据文件，保证可用性）
# 来源：十八反、十九畏传统歌诀。
# ---------------------------------------------------------------------------
# 十八反：a 与 b 不可同方
EIGHTEEN_INCOMPATIBLE: List[Tuple[str, str]] = [
    ("甘草", "海藻"), ("甘草", "大戟"), ("甘草", "甘遂"), ("甘草", "芫花"),
    ("乌头", "半夏"), ("乌头", "瓜蒌"), ("乌头", "贝母"), ("乌头", "白蔹"), ("乌头", "白及"),
    ("藜芦", "人参"), ("藜芦", "沙参"), ("藜芦", "丹参"), ("藜芦", "玄参"),
    ("藜芦", "苦参"), ("藜芦", "细辛"), ("藜芦", "芍药"),
]
# 十九畏：a 与 b 相制，尽量避免同方
NINETEEN_RESTRAINT: List[Tuple[str, str]] = [
    ("硫黄", "朴硝"), ("水银", "砒霜"), ("狼毒", "密陀僧"),
    ("巴豆", "牵牛"), ("丁香", "郁金"), ("川乌", "犀角"),
    ("牙硝", "三棱"), ("官桂", "石脂"), ("人参", "五灵脂"),
]
# 乌头类泛指（含附子、川乌、草乌）
_TOU_HEAD = {"乌头", "附子", "川乌", "草乌"}
_BEI_MU = {"贝母", "浙贝母", "川贝母", "瓜蒌", "半夏", "白蔹", "白及"}
_SHAO = {"芍药", "白芍", "赤芍"}


def _name_in(text: str, names) -> bool:
    return any(n in text for n in names)


def _strip_pinyin(name: str) -> str:
    """去掉模型标签中的括号拼音后缀：人参(renshen) -> 人参。

    仅用于展示层与查询映射，数据文件（label2idx.json / CSV）保持原样。
    """
    if not name:
        return name
    idx = name.find("(")
    if idx > 0:
        return name[:idx].strip()
    return name


def _normalize_name(name: str) -> str:
    """别名归一化：让模型输出的异形/带后缀名称映射到图谱节点。

    例: 枸杞子 -> 枸杞; 北沙参块/北沙参条 -> 北沙参; 天麻块/天麻片 -> 天麻
       肉苁蓉根/肉苁蓉片 -> 肉苁蓉; 枳壳片/枳壳条 -> 枳壳
       首乌藤块/首乌藤片 -> 首乌藤; 玉竹条/玉竹片 -> 玉竹
       人参切片 -> 人参; 野菊花 -> 菊花(功效近似, 仅做候选)
       人参(renshen) -> 人参（括号拼音后缀同样剥离）
    """
    name = _strip_pinyin(name)
    if name in {"枸杞子", "枸杞"}:
        return "枸杞"
    if name in {"北沙参块", "北沙参条"}:
        return "北沙参"
    if name in {"天麻块", "天麻片"}:
        return "天麻"
    if name in {"肉苁蓉根", "肉苁蓉片"}:
        return "肉苁蓉"
    if name in {"枳壳片", "枳壳条"}:
        return "枳壳"
    if name in {"首乌藤块", "首乌藤片"}:
        return "首乌藤"
    if name in {"玉竹条", "玉竹片"}:
        return "玉竹"
    if name == "人参切片":
        return "人参"
    if name == "野菊花":
        return "菊花"
    return name


def _is_incompatible(a: str, b: str) -> bool:
    pairs = [
        (a, b), (b, a),
        (a, b.replace("枸杞子", "枸杞").replace("金银花", "银花")),
    ]
    for x, y in pairs:
        if (x, y) in EIGHTEEN_INCOMPATIBLE or (y, x) in EIGHTEEN_INCOMPATIBLE:
            return True
    # 乌头类 ↔ 半夏/贝母/瓜蒌/白蔹/白及
    if (_name_in(a, _TOU_HEAD) and _name_in(b, _BEI_MU)) or \
       (_name_in(b, _TOU_HEAD) and _name_in(a, _BEI_MU)):
        return True
    # 藜芦 ↔ 参类/芍药/细辛
    if ("藜芦" in a and (_name_in(b, {"人参", "沙参", "丹参", "玄参", "苦参", "细辛"}) or _name_in(b, _SHAO))) or \
       ("藜芦" in b and (_name_in(a, {"人参", "沙参", "丹参", "玄参", "苦参", "细辛"}) or _name_in(a, _SHAO))):
        return True
    return False


def _is_restraint(a: str, b: str) -> bool:
    for x, y in [(a, b), (b, a)]:
        if (x, y) in NINETEEN_RESTRAINT or (y, x) in NINETEEN_RESTRAINT:
            return True
    # 人参 ↔ 五灵脂
    if (_name_in(a, {"人参"}) and "五灵脂" in b) or (_name_in(b, {"人参"}) and "五灵脂" in a):
        return True
    # 官桂/肉桂 ↔ 赤石脂
    if (_name_in(a, {"官桂", "肉桂"}) and "赤石脂" in b) or (_name_in(b, {"官桂", "肉桂"}) and "赤石脂" in a):
        return True
    return False


# 功效关键词 -> 分类（轻量映射，用于相似药推荐与方剂功效匹配）
FUNCTION_CATEGORY: Dict[str, List[str]] = {
    "补虚": ["补", "益气", "补血", "滋阴", "壮阳", "健脾", "温阳", "养血", "生津"],
    "清热": ["清热", "解毒", "凉血", "泻火", "除蒸", "解暑"],
    "解表": ["发散", "解表", "祛风", "散寒", "透疹", "疏风"],
    "活血": ["活血", "化瘀", "止血", "通络", "调经", "散瘀"],
    "利水渗湿": ["利水", "渗湿", "祛湿", "燥湿", "消肿"],
    "安神": ["安神", "宁心", "养心", "潜阳"],
    "化痰止咳": ["化痰", "止咳", "平喘", "润肺", "降气"],
    "消食": ["消食", "化积", "开胃", "健脾"],
    "温里": ["温中", "散寒", "回阳", "通脉", "暖肝"],
}


def _classify_function(func_text: str) -> List[str]:
    cats = []
    for cat, kws in FUNCTION_CATEGORY.items():
        if any(kw in func_text for kw in kws):
            cats.append(cat)
    return cats or ["其他"]


# ---------------------------------------------------------------------------
# 毒性识别：从性味文本中提取毒性等级，用于强制风险警示
# ---------------------------------------------------------------------------
_TOXICITY_LEVELS: List[str] = ["大毒", "有毒", "小毒", "微毒"]


def _parse_toxicity(property_text: str) -> str:
    """从性味文本中识别毒性等级，返回 大毒/有毒/小毒/微毒/无毒。

    例: 辛甘大热有毒 -> 有毒; 苦微寒有小毒 -> 小毒; 甘平 -> 无毒
    """
    text = property_text or ""
    for level in _TOXICITY_LEVELS:
        if level in text:
            return level
    return "无毒"


def _shingles(text: str, n: int = 2) -> set:
    """提取中文文本的连续 n 字片段，用于功效文本相似度比较。"""
    text = re.sub(r"[^\u4e00-\u9fa5]", "", text or "")
    return {text[i:i + n] for i in range(len(text) - n + 1)} if len(text) >= n else set()


# ---------------------------------------------------------------------------
# 特性检索：把用户描述解析为 性味/归经/功效 三类条件
# ---------------------------------------------------------------------------
_FLAVOR_WORDS = ["甘", "苦", "辛", "酸", "咸", "淡", "涩"]
_NATURE_WORDS = ["寒", "热", "温", "凉", "平"]
_NATURE_COMPOUND = ["微寒", "微温", "大寒", "大热", "微凉", "微热"]
_MERIDIAN_WORDS = ["心包", "三焦", "大肠", "小肠", "膀胱",
                   "肝", "心", "脾", "肺", "肾", "胃", "胆"]


def _parse_herb_query(text: str) -> Dict:
    """从用户描述中解析 性味/归经/功效 三类条件。

    支持写法：味甘平 / 甘微寒 / 味甘性平 / 归肝肾经 / 入肝经 /
    滋补肝肾、清热明目 等。若某字已被识别为功效关键词的一部分
    （如"清热"里的"热"），则不再当作"性"解析，避免误判。
    """
    flavor, nature, meridian, function_kws = [], [], [], []
    # 1) 功效关键词（FUNCTION_CATEGORY 体系）
    for cat, kws in FUNCTION_CATEGORY.items():
        for kw in kws:
            if kw in text and kw not in function_kws:
                function_kws.append(kw)
    kw_text = "".join(function_kws)
    # 2) 性（寒热温凉平，复合词优先；被功效词包含的字跳过）
    for w in _NATURE_COMPOUND:
        if w in text and w not in kw_text:
            nature.append(w)
    for ch in _NATURE_WORDS:
        if ch in text and ch not in "".join(nature) and ch not in kw_text:
            nature.append(ch)
    # 3) 味（甘苦辛酸咸淡涩）
    for ch in _FLAVOR_WORDS:
        if ch in text:
            flavor.append(ch)
    # 4) 归经（十二经，直接子串匹配）
    for m in _MERIDIAN_WORDS:
        if m in text:
            meridian.append(m)
    # 5) 功效片段：剔除性味/归经/连接词后的连续汉字（2~4 字），
    #    用于兜底匹配如"滋补"、"益精明目"这类未收录进关键词表的表达。
    stop = set(flavor + nature + meridian +
               ["味", "性", "气", "归", "入", "经", "微", "大", "小", "和",
                "及", "与", "同", "为", "之", "其", "而", "则",
                "、", "，", ",", "；", ";", "。", " ", ":", "："])
    cleaned = text
    for w in sorted(stop, key=len, reverse=True):
        cleaned = cleaned.replace(w, " ")
    function_segs = []
    for p in re.split(r"[^\u4e00-\u9fa5]+", cleaned):
        if not p:
            continue
        for L in range(2, min(4, len(p)) + 1):
            for i in range(len(p) - L + 1):
                seg = p[i:i + L]
                if seg not in function_segs:
                    function_segs.append(seg)
    return {"flavor": flavor, "nature": nature, "meridian": meridian,
            "function_kws": function_kws, "function_segs": function_segs}


class HerbKnowledgeGraph:
    def __init__(self, data_path: str):
        self.data_path = data_path
        self.graph = nx.Graph()
        self.formulas: Dict[str, Dict] = {}
        self.user_herbs_path = "data/user_herbs.json"
        self.user_herbs = []
        self._load()
        self._load_formulas()
        self._load_herb_extra()
        self._load_user_herbs()

    def _load(self):
        if not os.path.exists(self.data_path):
            raise FileNotFoundError(f"知识图谱数据不存在: {self.data_path}")
        df = pd.read_csv(self.data_path)
        # 1) 节点 + 属性
        for _, row in df.iterrows():
            func = str(row.get("function", "") or "")
            cats = _classify_function(func)
            prop = str(row.get("property", "") or "")
            self.graph.add_node(
                row["name"],
                property=prop,
                meridian=str(row.get("meridian", "") or ""),
                function=func,
                categories=cats,
                toxicity=_parse_toxicity(prop),
            )
            # 分类节点：herb -[:category]-> Category
            for c in cats:
                if c not in self.graph:
                    self.graph.add_node(c, node_type="category", toxicity="未知")
                self.graph.add_edge(row["name"], c, relation="category")
            # 归经节点：herb -[:meridian]-> Meridian
            for m in str(row.get("meridian", "") or "").replace("、", ",").split(","):
                m = m.strip()
                if m:
                    if m not in self.graph:
                        self.graph.add_node(m, node_type="meridian", toxicity="未知")
                    self.graph.add_edge(row["name"], m, relation="meridian")
        # 2) 相须相使配对（数据自带）
        for _, row in df.iterrows():
            paired = str(row.get("paired_herb", "") or "").strip()
            if paired and paired != "nan":
                if paired not in self.graph:
                    self.graph.add_node(paired, categories=["其他"],
                                        toxicity="未知")
                self.graph.add_edge(row["name"], paired, relation="paired")
        # 2.5) 将内置十八反/十九畏涉及的药材纳入图谱（即使不在数据表中，
        #      经典禁忌也能被查询、检索与可视化，避免"甘草×甘遂"等盲区）
        for a, b in EIGHTEEN_INCOMPATIBLE + NINETEEN_RESTRAINT:
            for x in (a, b):
                x = _normalize_name(x)
                if x not in self.graph:
                    self.graph.add_node(x, categories=["其他"],
                                        property="", meridian="", function="",
                                        toxicity="未知", builtin=True)
        # 3) 内置禁忌规则（十八反 / 十九畏）
        names = [n for n in self.graph.nodes if self.graph.nodes[n].get("node_type") != "category"]
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if _is_incompatible(a, b):
                    self.graph.add_edge(a, b, relation="incompatible")
                elif _is_restraint(a, b):
                    self.graph.add_edge(a, b, relation="restraint")

    def _load_herb_extra(self):
        """加载增量字段表（herb_extra.csv）：别名、适用病症、个体禁忌。

        与药材表同目录；按归一化药材名合并到对应节点属性，
        供 get_info/describe 输出，满足知识库「别名/适用病症/个体禁忌」字段需求。
        """
        fp = os.path.join(os.path.dirname(self.data_path), "herb_extra.csv")
        if not os.path.exists(fp):
            return
        df = pd.read_csv(fp)
        for _, row in df.iterrows():
            n = _normalize_name(str(row["name"]).strip())
            if n not in self.graph:
                continue
            nd = self.graph.nodes[n]
            if pd.notna(row.get("aliases")):
                nd["aliases"] = [a.strip() for a in
                                 str(row["aliases"]).replace("、", ",").split(",")
                                 if a.strip()]
            if pd.notna(row.get("indications")):
                nd["indications"] = str(row["indications"]).strip()
            if pd.notna(row.get("cautions")):
                nd["cautions"] = str(row["cautions"]).strip()

    def _load_formulas(self):
        """加载经典方剂库（formulas.csv），构建 方剂 实体节点与 组成 边。

        方剂表与药材表同目录；组成边只对知识库内已收录的药材建立，
        其余药材保留在 composition 文本中供展示。
        """
        fp = os.path.join(os.path.dirname(self.data_path), "formulas.csv")
        if not os.path.exists(fp):
            return
        df = pd.read_csv(fp)
        for _, row in df.iterrows():
            name = str(row["name"]).strip()
            comps = [c.strip()
                     for c in str(row.get("composition", "") or "")
                     .replace("、", ",").split(",") if c.strip()]
            self.formulas[name] = {
                "name": name,
                "source": str(row.get("source", "") or "").strip(),
                "category": str(row.get("category", "") or "").strip(),
                "composition": comps,
                "composition_text": str(row.get("composition", "") or "").strip(),
                "effects": str(row.get("effects", "") or "").strip(),
                "indications": str(row.get("indications", "") or "").strip(),
                "usage": str(row.get("usage", "") or "").strip(),
                "warning": str(row.get("warning", "") or "").strip(),
            }
            if name not in self.graph:
                self.graph.add_node(name, node_type="formula", toxicity="未知")
            # 组成边：精确名优先，其次按「库内药材名 ⊆ 组成原文」最长包含匹配
            # （如「熟地黄」匹配库内「熟地」/「地黄」，避免因命名差异漏建边）
            ordered = list(self.graph.nodes)
            for c in comps:
                target = None
                if c in self.graph:
                    target = c
                else:
                    cands = [n for n in ordered
                             if not self.graph.nodes[n].get("node_type")
                             and len(n) >= 2 and n in c]
                    if cands:
                        target = max(cands, key=len)
                if target:
                    self.graph.add_edge(target, name, relation="formula_in")

    def classic_formulas(self, herb: str, top_k: int = 3) -> List[Dict]:
        """返回包含 herb 的经典方剂（按功效分类匹配度排序，最多 top_k 首）。"""
        norm = _normalize_name(herb)
        scored = []
        info = self.get_info(norm)
        cats = set(info["categories"]) if info else set()
        for f in self.formulas.values():
            if norm in f["composition_text"]:
                # 方剂功效分类与药材功效分类重叠越多，排序越靠前
                overlap = len(cats & set(f["category"].split("、")))
                scored.append((overlap, f))
        scored.sort(key=lambda x: -x[0])
        return [f for _, f in scored[:top_k]]

    # --------------------------- 查询 API ---------------------------
    def get_info(self, name: str) -> Optional[Dict]:
        name = _normalize_name(name)
        if name not in self.graph:
            return None
        n = self.graph.nodes[name]
        return {
            "name": name,
            "property": n.get("property", ""),
            "meridian": n.get("meridian", ""),
            "function": n.get("function", ""),
            "categories": n.get("categories", []),
            "toxicity": n.get("toxicity", "无毒"),
            "aliases": n.get("aliases", []),
            "indications": n.get("indications", ""),
            "cautions": n.get("cautions", ""),
        }

    def recommend_pairs(self, name: str) -> List[str]:
        """返回与 name 存在'相须相使'配伍关系的草药列表。"""
        name = _normalize_name(name)
        if name not in self.graph:
            return []
        return [n for n in self.graph.neighbors(name)
                if self.graph[name][n].get("relation") == "paired"]

    def contraindications(self, name: str) -> Dict[str, List[str]]:
        """返回 name 的配伍禁忌：十八反 / 十九畏。"""
        name = _normalize_name(name)
        if name not in self.graph:
            return {"incompatible": [], "restraint": []}
        inc, res = [], []
        for n in self.graph.neighbors(name):
            rel = self.graph[name][n].get("relation")
            if rel == "incompatible":
                inc.append(n)
            elif rel == "restraint":
                res.append(n)
        return {"incompatible": inc, "restraint": res}

    def similar_by_function(self, name: str, top_k: int = 5) -> List[str]:
        """返回与 name 功效相近的其它草药（相似药推荐）。

        相似度打分 = 功效分类重叠×3 + 功效文本 n-gram 重叠×2
                   + 性味字符重叠×1 + 归经重叠×1，按总分降序。
        分类无重叠的直接不推荐，保持"功效相近"的语义。
        """
        name = _normalize_name(name)
        info = self.get_info(name)
        if info is None:
            return []
        cats = set(info["categories"])
        func_grams = (_shingles(info["function"], 4)
                      | _shingles(info["function"], 3)
                      | _shingles(info["function"], 2))
        prop_chars = set(info["property"])
        meridians = set(m.strip() for m in
                        str(info["meridian"]).replace("、", ",").split(",") if m.strip())
        scored = []
        for n in self.graph.nodes:
            if n == name or self.graph.nodes[n].get("node_type"):
                continue
            nd = self.graph.nodes[n]
            other = set(nd.get("categories", []))
            if not (cats & other):
                continue
            s = 3.0 * len(cats & other)
            other_func = str(nd.get("function", "") or "")
            s += 2.0 * len((_shingles(other_func, 4) | _shingles(other_func, 3)
                            | _shingles(other_func, 2)) & func_grams)
            s += 1.0 * len(set(str(nd.get("property", "") or "")) & prop_chars)
            other_mer = set(m.strip() for m in
                            str(nd.get("meridian", "") or "").replace("、", ",").split(",")
                            if m.strip())
            s += 1.0 * len(other_mer & meridians)
            scored.append((s, n))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [n for _, n in scored[:top_k]]

    def recommend_formula(self, target: str, symptoms: str = None,
                          top_k: int = 5, exclude: List[str] = None) -> List[Dict]:
        """方剂推荐：基于主治功效匹配 + 常用配伍 + 禁忌规避打分。

        参数:
          target : 已识别出的主药（或可输入症状文本做检索）
          symptoms: 用户补充的症状描述，用于功效匹配（可选）
          top_k  : 返回候选配伍药数量
          exclude: 已确定的方中其它药，用于禁忌检查

        返回: [{herb, score, reason}] 按打分降序。
        """
        exclude = set(exclude or [])
        target = _normalize_name(target)
        target_info = self.get_info(target)
        candidates = [n for n in self.graph.nodes
                      if n != target and not self.graph.nodes[n].get("node_type")
                      and n not in exclude]
        # 若 target 不在图谱中（例如用症状检索），则按症状关键词全局匹配
        if target_info is None:
            target_cats = self._match_categories_by_text(symptoms or target)
        else:
            target_cats = set(target_info["categories"])

        results = []
        for c in candidates:
            ci = self.get_info(c)
            c_cats = set(ci["categories"]) if ci else set()
            # 1) 功效匹配分：分类重叠数
            overlap = len(target_cats & c_cats)
            # 2) 症状直接命中分：症状文本中出现该药功效关键词
            symptom_hit = 0
            if symptoms:
                symptom_hit = sum(1 for kw in (ci["function"] if ci else "")
                                  if kw in symptoms)
            # 3) 常用配伍加分
            paired = (target in self.recommend_pairs(c)) or (c in self.recommend_pairs(target))
            paired_bonus = 1.0 if paired else 0.0
            # 4) 禁忌惩罚：与 target 或方中其它药冲突则重罚
            conflict = False
            if target_info and (self.graph.has_edge(target, c)
                                and self.graph[target][c].get("relation") in
                                ("incompatible", "restraint")):
                conflict = True
            for ex in exclude:
                if self.graph.has_edge(ex, c) and self.graph[ex][c].get("relation") in \
                        ("incompatible", "restraint"):
                    conflict = True
            if conflict:
                continue  # 禁忌药直接剔除，不推荐
            score = overlap * 2.0 + symptom_hit * 1.5 + paired_bonus
            if score <= 0:
                continue
            reason = []
            if overlap:
                reason.append(f"功效同属{', '.join(target_cats & c_cats)}")
            if paired:
                reason.append("常相须相使配伍")
            if symptom_hit:
                reason.append("贴合所述症状")
            results.append({"herb": c, "score": round(score, 2),
                            "reason": "；".join(reason) or "功效相近",
                            "toxicity": ci.get("toxicity", "无毒") if ci else "未知"})
        results.sort(key=lambda x: -x["score"])
        return results[:top_k]

    def _match_categories_by_text(self, text: str) -> set:
        cats = set()
        for cat, kws in FUNCTION_CATEGORY.items():
            if any(kw in text for kw in kws):
                cats.add(cat)
        return cats

    def confusable_of(self, name: str) -> Optional[Dict]:
        """返回药材 name 的易混淆外观鉴别信息（无则 None）。

        用于「相似药材提示」：当识别结果为易混淆药材时，
        输出外观差异与简易鉴别法，帮助用户区分（对应需求 1.4 用户画像）。
        """
        return get_confusable(_normalize_name(name))

    def describe(self, name: str) -> str:
        """生成可读的药性说明，供演示界面展示。"""
        norm = _normalize_name(name)
        info = self.get_info(name)
        if info is None:
            return f"知识库中暂无「{name}」的详细记录。"
        display_name = name if norm != name else info["name"]
        pairs = self.recommend_pairs(name)
        contra = self.contraindications(name)
        inc_txt = "、".join(contra["incompatible"]) if contra["incompatible"] else "无"
        res_txt = "、".join(contra["restraint"]) if contra["restraint"] else "无"
        pair_txt = "、".join(pairs) if pairs else "无"
        desc = (
            f"【{display_name}】\n"
            f"药性：{info['property']}\n"
            f"归经：{info['meridian']}\n"
            f"功效：{info['function']}\n"
            f"功效分类：{'、'.join(info['categories'])}\n"
        )
        if info.get("aliases"):
            desc += f"别名：{'、'.join(info['aliases'])}\n"
        if info.get("indications"):
            desc += f"适用病症：{info['indications']}\n"
        desc += (
            f"毒性：{info['toxicity']}\n"
            f"常用配伍(相须相使)：{pair_txt}\n"
            f"配伍禁忌-十八反：{inc_txt}\n"
            f"配伍禁忌-十九畏：{res_txt}"
        )
        if info.get("cautions"):
            desc += f"\n个体禁忌：{info['cautions']}"
        # 毒性强制警示（安全红线）：有毒/大毒醒目提示，小毒/微毒提醒限量
        tox = info.get("toxicity", "无毒")
        if tox in ("大毒", "有毒"):
            desc += (f"\n⚠️【毒性警示】本品为{tox}药材，严禁自行煎服或超量使用，"
                     "必须在执业中医师指导下辨证用药！")
        elif tox in ("小毒", "微毒"):
            desc += (f"\n⚠️【注意】本品含{tox}成分，用量需谨慎控制，"
                     "请遵医嘱或在医师指导下使用。")
        if self.graph.nodes[norm].get("builtin"):
            desc += ("\n（注：该药为内置十八反/十九畏经典条目，"
                     "数据表未收录详细药性，此处仅展示其配伍禁忌关系。）")
        return desc

    def all_names(self) -> List[str]:
        return [n for n in self.graph.nodes
                if not self.graph.nodes[n].get("node_type")]

    # ----------------------- 可视化导出 API -----------------------
    def export_graph_json(self, focus: Optional[Union[str, List[str]]] = None,
                          include_meta: bool = True) -> Dict:
        """导出图谱为 JSON（供前端可视化，纯前端力导向渲染）。

        参数:
          focus       : 聚焦药材名（单味或多味）；多味时传入列表，导出各药及其
                        一阶邻居的并集子图；None 表示全图
          include_meta: 聚焦模式下是否包含 功效分类/归经 节点（默认包含）
        """
        if focus is None:
            focus_list = []
        elif isinstance(focus, str):
            focus_list = [focus]
        else:
            focus_list = list(focus)

        focus_set = set()
        for f in focus_list:
            fn = _normalize_name(f) if f else None
            if fn and fn in self.graph:
                focus_set.add(fn)
        if len(focus_set) > 1:
            focus = list(focus_set)  # 多味聚焦
        elif len(focus_set) == 1:
            focus = next(iter(focus_set))
        else:
            focus = None

        nodes, herb_ids = [], set()

        def add_herb(n):
            if n in herb_ids:
                return
            herb_ids.add(n)
            nd = self.graph.nodes[n]
            contra = self.contraindications(n)
            nodes.append({
                "id": n,
                "type": "herb",
                "focus": (n in focus_set),
                "property": nd.get("property", ""),
                "meridian": nd.get("meridian", ""),
                "function": nd.get("function", ""),
                "categories": nd.get("categories", []),
                "toxicity": nd.get("toxicity", "无毒"),
                "aliases": nd.get("aliases", []),
                "indications": nd.get("indications", ""),
                "cautions": nd.get("cautions", ""),
                "image": nd.get("image"),
                "user_added": nd.get("user_added", False),
                "pairs": self.recommend_pairs(n),
                "incompatible": contra["incompatible"],
                "restraint": contra["restraint"],
            })

        if focus is None:
            for n in self.graph.nodes:
                if self.graph.nodes[n].get("node_type"):
                    continue
                add_herb(n)
        else:
            # focus 可能是单味（str）或多味（list），统一处理
            focus_items = focus if isinstance(focus, list) else [focus]
            for fi in focus_items:
                add_herb(fi)
                for n in self.graph.neighbors(fi):
                    nd = self.graph.nodes[n]
                    if nd.get("node_type"):
                        if include_meta:
                            extra = {}
                            # 方剂节点附带详情，供前端详情面板展示
                            if nd["node_type"] == "formula":
                                f = self.formulas.get(n, {})
                                extra = {
                                    "category": f.get("category", ""),
                                    "source": f.get("source", ""),
                                    "composition_text": f.get("composition_text", ""),
                                    "effects": f.get("effects", ""),
                                    "indications": f.get("indications", ""),
                                    "usage": f.get("usage", ""),
                                    "warning": f.get("warning", ""),
                                }
                            nodes.append({"id": n, "type": nd["node_type"],
                                          "toxicity": "未知", **extra})
                    else:
                        add_herb(n)
                # 补充同功效分类的相似药（经分类节点相连），增强聚焦子图信息量
                for n in self.similar_by_function(fi, top_k=6):
                    add_herb(n)

        node_ids = {n["id"] for n in nodes}
        links = []
        for u, v, d in self.graph.edges(data=True):
            if u not in node_ids or v not in node_ids:
                continue
            links.append({"source": u, "target": v, "relation": d.get("relation")})
        return {"nodes": nodes, "links": links}

    def _match_names_in_text(self, text: str):
        """从自由文本中识别多个已知药材名，支持「逗号/顿号/空格/分号」分隔。

        返回: (items, query_names)
          items  : 与 search_herbs_by_text 结构一致的 full 列表项
          query_names: 归一化后命中的药材名列表（保持输入顺序、去重）
        """
        if not text or not text.strip():
            return [], []
        raw_parts = [p.strip() for p in
                     re.split(r"[，,、;；\s]+", text.strip()) if p.strip()]
        # 同时尝试整句直接匹配（未切分时也识别，如「枸杞子」）
        candidates = list(dict.fromkeys(raw_parts + [text.strip()]))

        names = []
        for c in candidates:
            n = _normalize_name(c)
            if n in self.graph and not self.graph.nodes[n].get("node_type") \
                    and n not in names:
                names.append(n)

        items = []
        for n in names:
            info = self.get_info(n)
            if info is None:
                continue
            items.append({
                "name": n,
                "score": 1.0,
                "dims": {"flavor": True, "meridian": True, "function": True},
                "hits": {},
                "info": info,
                "name_hit": True,
            })
        return items, names

    # ----------------------- 特性检索 API -----------------------
    def search_herbs_by_text(self, text: str, top_k: int = 25) -> Dict:
        """根据特性描述检索所有符合的中草药（完全匹配优先，部分匹配在后）。

        两类用法：
          1) 按药材名检索：输入含已知药材名（如「枸杞 黄芪」「枸杞、黄芪、
             枸杞子」），直接返回这些药材档案（支持同时查多个），标 name_hit。
          2) 按特性检索：输入性味/归经/功效描述，逐味打分匹配。

        解析输入文本中的 性味/归经/功效 三类条件，遍历图谱逐味打分；
        全部维度命中的药材归入 full，命中部分维度的归入 partial。
        每条结果附带各维度命中/缺失明细与命中关键词。

        返回: {
          parsed: 解析出的条件, total_conditions: 条件类数(1~3),
          full: 完全匹配列表, partial: 部分匹配列表, hint: 提示文本
        }
        """
        # —— 按药材名检索（支持多味，逗号/顿号/空格/分号分隔）——
        name_items, name_query = self._match_names_in_text(text)
        if name_items:
            # 同时若也解析到性味/归经/功效条件，仍按名返回（特性条件作为附加提示）
            return {
                "parsed": {"flavor": [], "nature": [], "meridian": [],
                           "function_kws": [], "function_segs": []},
                "total_conditions": 0,
                "full": name_items,
                "partial": [],
                "name_hit": True,
                "name_query": name_query,
                "hint": None,
            }

        parsed = _parse_herb_query(text)
        flavor_nature = parsed["flavor"] + parsed["nature"]
        func_cands = list(dict.fromkeys(parsed["function_kws"] + parsed["function_segs"]))
        conditions = 0
        if flavor_nature:
            conditions += 1
        if parsed["meridian"]:
            conditions += 1
        if func_cands:
            conditions += 1
        if conditions == 0:
            return {"parsed": parsed, "total_conditions": 0, "full": [],
                    "partial": [],
                    "hint": "未解析出有效条件，请按「性味 + 归经 + 功效」描述。"}

        full, partial = [], []
        for name in self.all_names():
            info = self.get_info(name)
            if info is None:
                continue
            prop, mer, func = info["property"], info["meridian"], info["function"]
            # 性味维度：解析出的味/性全部命中才算
            flavor_hits = [w for w in flavor_nature if w in prop]
            flavor_dim = bool(flavor_nature) and len(flavor_hits) == len(flavor_nature)
            # 归经维度：解析出的经全部命中才算
            meridian_hits = [m for m in parsed["meridian"] if m in mer]
            meridian_dim = bool(parsed["meridian"]) and len(meridian_hits) == len(parsed["meridian"])
            # 功效维度：命中任一关键词/片段即可（描述往往宽泛）
            func_hits = [w for w in func_cands if w in func]
            func_dim = bool(func_cands) and len(func_hits) > 0

            hit_dims = int(flavor_dim) + int(meridian_dim) + int(func_dim)
            if hit_dims == 0:
                continue
            # 打分：性味/归经命中 +2/词，功效命中 +1/词（用于组内排序）
            score = (2.0 * len(flavor_hits) if flavor_dim else 0.0) \
                  + (2.0 * len(meridian_hits) if meridian_dim else 0.0) \
                  + (1.0 * len(func_hits) if func_dim else 0.0)
            item = {
                "name": name,
                "score": round(score, 1),
                "dims": {"flavor": flavor_dim, "meridian": meridian_dim,
                         "function": func_dim},
                "hits": {"flavor": flavor_hits, "meridian": meridian_hits,
                         "function": func_hits[:8]},
                "info": info,
            }
            if hit_dims == conditions:
                full.append(item)
            else:
                partial.append(item)
        full.sort(key=lambda x: -x["score"])
        partial.sort(key=lambda x: -x["score"])
        return {"parsed": parsed, "total_conditions": conditions,
                "full": full[:top_k], "partial": partial[:top_k],
                "hint": "未匹配到任何药材，请补充更明确的性味/归经/功效关键词。"
                if not full and not partial else None}

    # ----------------------- 用户增补药材库（本草补遗库） -----------------------
    def _clean_user_record(self, record: Dict) -> Dict:
        """规整用户药材记录（统一字段名与类型，便于落盘与回传）。"""
        aliases = record.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [a.strip() for a in
                       re.split(r"[，,、;；\s]+", aliases) if a.strip()]
        return {
            "name": _normalize_name(record.get("name", "")),
            "property": (record.get("property") or "").strip(),
            "meridian": (record.get("meridian") or "").strip(),
            "function": (record.get("function") or "").strip(),
            "aliases": aliases,
            "indications": (record.get("indications") or "").strip(),
            "cautions": (record.get("cautions") or "").strip(),
            "paired_herb": (record.get("paired_herb") or "").strip(),
            "image": record.get("image"),
        }

    def _add_user_herb_node(self, record: Dict):
        """把一个用户药材记录作为真实图谱节点加入（无 node_type → 可被检索/可视化）。"""
        name = _normalize_name(record.get("name", ""))
        if not name:
            return
        prop = (record.get("property") or "").strip()
        mer = (record.get("meridian") or "").strip()
        func = (record.get("function") or "").strip()
        cats = record.get("categories") or _classify_function(func)
        if not cats:
            cats = ["其他"]
        attrs = {
            "property": prop,
            "meridian": mer,
            "function": func,
            "categories": cats,
            "toxicity": _parse_toxicity(prop),
            "aliases": record.get("aliases") or [],
            "indications": (record.get("indications") or "").strip(),
            "cautions": (record.get("cautions") or "").strip(),
            "image": record.get("image"),
            "user_added": True,
            "source": "user",
        }
        if self.graph.has_node(name):
            self.graph.nodes[name].update(attrs)
        else:
            self.graph.add_node(name, **attrs)
            for c in cats:
                if c not in self.graph:
                    self.graph.add_node(c, node_type="category", toxicity="未知")
                self.graph.add_edge(name, c, relation="category")
            for m in mer.replace("、", ",").split(","):
                m = m.strip()
                if m:
                    if m not in self.graph:
                        self.graph.add_node(m, node_type="meridian", toxicity="未知")
                    self.graph.add_edge(name, m, relation="meridian")
        # 常用配伍（配对）
        paired = (record.get("paired_herb") or "").strip()
        if paired:
            n2 = _normalize_name(paired)
            if n2 and n2 != name:
                if not self.graph.has_node(n2):
                    self.graph.add_node(n2, categories=["其他"], toxicity="未知",
                                        user_added=True, source="user-implied")
                self.graph.add_edge(name, n2, relation="paired")
        # 十八反 / 十九畏（与已有药材节点逐对判定）
        for other in self.graph.nodes:
            if other == name or self.graph.nodes[other].get("node_type"):
                continue
            if _is_incompatible(name, other):
                self.graph.add_edge(name, other, relation="incompatible")
            elif _is_restraint(name, other):
                self.graph.add_edge(name, other, relation="restraint")

    def _save_user_herbs(self):
        try:
            d = os.path.dirname(self.user_herbs_path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(self.user_herbs_path, "w", encoding="utf-8") as f:
                json.dump(self.user_herbs, f, ensure_ascii=False, indent=2)
        except Exception as e:  # pragma: no cover - 落盘失败不应阻断主流程
            print("保存用户药材库失败：", e)

    def _load_user_herbs(self):
        self.user_herbs = []
        if not os.path.exists(self.user_herbs_path):
            return
        try:
            with open(self.user_herbs_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for r in data:
                    self.user_herbs.append(self._clean_user_record(r))
                    self._add_user_herb_node(r)
        except Exception as e:  # pragma: no cover
            print("加载用户药材库失败：", e)

    def add_user_herb(self, record: Dict) -> Dict:
        name = _normalize_name(record.get("name", ""))
        if not name:
            raise ValueError("请填写药名")
        if self.graph.has_node(name):
            raise ValueError("「%s」已存在于知识库中" % name)
        self._add_user_herb_node(record)
        self.user_herbs.append(self._clean_user_record(record))
        self._save_user_herbs()
        return record

    def update_user_herb(self, old_name: str, record: Dict) -> Dict:
        old = _normalize_name(old_name)
        idx = next((i for i, r in enumerate(self.user_herbs)
                    if _normalize_name(r.get("name", "")) == old), None)
        if idx is None:
            raise ValueError("未找到该药材")
        if self.graph.has_node(old):
            self.graph.remove_node(old)
        self._add_user_herb_node(record)
        self.user_herbs[idx] = self._clean_user_record(record)
        self._save_user_herbs()
        return record

    def delete_user_herb(self, name: str) -> bool:
        n = _normalize_name(name)
        idx = next((i for i, r in enumerate(self.user_herbs)
                    if _normalize_name(r.get("name", "")) == n), None)
        if idx is None:
            return False
        if self.graph.has_node(n):
            self.graph.remove_node(n)
        del self.user_herbs[idx]
        self._save_user_herbs()
        return True

    def get_user_herb(self, name: str) -> Optional[Dict]:
        n = _normalize_name(name)
        for r in self.user_herbs:
            if _normalize_name(r.get("name", "")) == n:
                return r
        return None

    def list_user_herbs(self) -> List[Dict]:
        return [self._clean_user_record(r) for r in self.user_herbs]


def build_knowledge_graph(config: Dict) -> HerbKnowledgeGraph:
    kg_cfg = config["knowledge_graph"]
    if kg_cfg.get("use_neo4j", False):
        raise NotImplementedError("Neo4j 模式尚未启用，请先设置 use_neo4j: false 使用内存版。")
    return HerbKnowledgeGraph(kg_cfg["data_path"])


if __name__ == "__main__":
    g = HerbKnowledgeGraph("knowledge_graph/herbs_sample.csv")
    print(g.describe("枸杞"))
    print("\n配伍推荐(相须相使):", g.recommend_pairs("枸杞"))
    print("禁忌:", g.contraindications("甘草"))
    print("\n相似功效药(枸杞):", g.similar_by_function("枸杞"))
    print("\n方剂推荐(主药=枸杞, 症状=眼干目涩):")
    for r in g.recommend_formula("枸杞", symptoms="眼干目涩、腰膝酸软"):
        print(f"  {r['herb']}  score={r['score']}  ({r['reason']})")
