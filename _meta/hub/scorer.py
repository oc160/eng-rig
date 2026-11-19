# -*- coding: utf-8 -*-
# ── 资产卡 ────────────────────────────────────────────────
# 名称：scorer.py —— 检索打分与注入路由的纯计算核（asset_cli 同名薄壳委托至此）
# 来源：2026-11-16 复杂度整改（Antigravity 实测 asset_cli.py McCabe=436 全库最高，
#     纯计算体外移 _meta/hub/ 子包）
# 提取：2026-11-16（自 _meta/asset_cli.py 打分/否定语义/路由策略体逐行原样外移）
# Author: EngineerRig
# 验证方式：行为保真由 tests/test_asset_cli.py + tests/test_routing_dataset.py 回归兜底
#     （路由数据集断言打分不改权重；INJECT_THRESHOLD 标定线见 _meta/calibration_20261102.md）
# 重跑命令：python -m pytest -q tests/test_routing_dataset.py tests/test_asset_cli.py
# 依赖：纯标准库；不 import asset_cli（单向被依赖）；函数一律显式收参、不读模块级可变状态
# ──────────────────────────────────────────────────────────
"""打分/路由纯计算核（2026-11-16 自 asset_cli 外移，行为逐行保真）。

纪律：
  - 权重/阈值/别名等标定参数由调用方（asset_cli 薄壳）在调用点取当值传入——
    tools/calibrate_routing.py 对 asset_cli 模块 setattr 常量后立即生效；
    改打分必须重标 INJECT_THRESHOLD（tests/test_routing_dataset.py 校验）。
  - 否定 cue / 多意图连接词 / 点名排除正则以本模块为唯一定义源，
    asset_cli 顶部重导出同名对象保持既有模块属性面。
  - 本模块的 DEFAULT_PARAMS 仅作独立调用兜底；权威值永远是 asset_cli 的模块常量。
"""
import hashlib
import hmac
import math
import re
import unicodedata

# W2-④ cue 收紧（review 必修②）："别"只认祈使搭配（别动/别用/别改…），裸"别"不再当 cue
# （"识别报告中的假标题"曾因"别"从第 1 字划到串尾而整句误杀）；兼容英文否定 cue。
NEG_CUE_RE = re.compile(
    r"别动|别用|别改|别碰|别删|别转|别加|别管|别乱|别做|不要|不用|不动|不改|保持|保留|无需"
    r"|(?<![a-z])(?:don't|donot|never|without|avoid)"
)
SEPA = "，。；、！？,;.!"
# 多意图连接词（review 必修①）：显式连接词在场才允许"正向命中解除否决"——
# "先量图再批量改图"（量图/批量改图=两个子任务）放行；"弧形闸门，出结构计算书"
# （同一意图的两个短语）不放行。零模型、可判定的最强词面信号。
MULTI_INTENT_RE = re.compile(r"先.{0,10}再|然后|接着|也要|也要做|同时|以及|并(?=改|做|加|生成|出|配|排)")
DENY_USE_RE = re.compile(
    r"(?:不要|别|不)用([\u4e00-\u9fffA-Za-z0-9_\-]{2,10})"
    r"|(?<![a-z])(?:don't|donot|never|avoid)use([a-z0-9_\-]{3,20})"
)

# 打分参数缺省（兜底值；asset_cli 以调用点当值全覆盖——两处一致，标定证据同源）
DEFAULT_PARAMS = {
    "aliases": {},
    "field_w": {"trigger": 3.0, "name": 2.0, "text": 1.0, "path": 0.5},
    "trigger_bonus": 7.0,               # ① 精确触发词/别名命中
    "anti_penalty": 3.0,                # ⑥ 反向触发词罚分
    "anti_overlap": 2,                  # ⑥ 负场景 token 重叠数下限
    "query_div": 4,                     # ④ 长查询稀释分母系数
}


def params(sp=None):
    """合并打分参数（sp 覆盖缺省；asset_cli 薄壳永远传全键）。"""
    p = dict(DEFAULT_PARAMS)
    p.update(sp or {})
    return p


# ── 文本规整 ──────────────────────────────────────────────
def norm(s):
    s = unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"\s+", " ", s).strip()


def tokens(s):
    s = (s or "").lower()
    out = set(re.findall(r"[a-z0-9_\-]{2,}", s))
    zh = re.findall(r"[\u4e00-\u9fff]+", s)
    for seg in zh:
        out.update(seg[i:i + 2] for i in range(max(1, len(seg) - 1)))
        out.add(seg)
    return out


def flat(s):
    """平化归一（W2-② 归一统一）：norm 后再去全部空白并小写——触发词/anti/查询
    三方同口径，修"Word 批量格式"带空格丢 +7 的不对称。"""
    return re.sub(r"\s+", "", norm(s)).lower()


# ── W2-②④ 打分修正基建：平化归一 / 否定跨度 / 跨度外命中 ──────────────────
def neg_spans(fl, cue_re=None, sepa=None):
    """否定/约束跨度（review 更优解，零模型；W2-④ 升级）：
    - cue 起、到最近句读或串尾；
    - **宾语前置回扩**：cue 紧跟"动"或 cue 后无内容（"X 不要动"/"X 一律保留"）时，
      跨度向前吞并宾语短语（至句读，≤8 字）——"报告格式不要动"里的"报告格式"不再喂分；
    - 跨度语义：其内字词不算触发命中、不罚 anti、不参与豁免，**并掩蔽名称/ngram/IDF 计分**
      （W2-④ 字段级掩蔽：kimi"不要用格式工具箱"曾因名称分照拿而 9.955 注入）。"""
    cue_re = cue_re or NEG_CUE_RE
    sepa = sepa or SEPA
    spans = []
    for m in cue_re.finditer(fl):
        e = len(fl)
        for i in range(m.end(), len(fl)):
            if fl[i] in sepa:
                e = i
                break
        s = m.start()
        # 宾语前置回扩：cue 后首字是"动"（不要动/不动型）或 cue 后到句读几乎为空（保留型收尾）
        after_empty = all(fl[i] in sepa for i in range(m.end(), e)) if e > m.end() else True
        verb_head = m.end() < len(fl) and fl[m.end()] == "动"
        if verb_head or (after_empty and m.group(0) in ("保持", "保留")):
            bs = s
            j = s - 1
            while j >= 0 and fl[j] not in sepa and (s - j) <= 8:
                bs = j
                j -= 1
            if bs < s:
                s = bs
        if e > s:
            spans.append((s, e))
    merged = []
    for s, e in sorted(spans):
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def explicit_denies(fl, deny_re=None):
    """显式排除通道（review 必修②）："不要用X/别用X/不用X"或英文 "don't use X" 的 X 是资产名片段 →
    该资产无条件丧失注入资格（用户点名排除优先于一切词面证据）。返回被点名的原文片段列表。"""
    out = []
    for m in (deny_re or DENY_USE_RE).finditer(fl):
        grp = next((g for g in m.groups() if g), "")
        if grp:
            out.append(grp)
    return out


def hit_outside_span(fl, needle, spans):
    """needle 在 fl 中存在出现点，且该出现与任何否定跨度不相交。"""
    if not needle:
        return False
    start = 0
    while True:
        i = fl.find(needle, start)
        if i < 0:
            return False
        if not any(i < e and s < i + len(needle) for s, e in spans):
            return True
        start = i + 1


def mask_spans(fl, spans):
    """把否定跨度内容替换为句读占位（防跨边界拼出假 bigram），用于 token 重叠口径。"""
    if not spans:
        return fl
    chars = []
    span_set = set()
    for s, e in spans:
        span_set.update(range(s, e))
    for i, c in enumerate(fl):
        chars.append("，" if i in span_set else c)
    return "".join(chars)


def qctx(prompt, aliases=None):
    """查询上下文一次算清（W2-④ 统一坐标系）：平化串/否定跨度/掩蔽串/存活 token/别名扩展串。

    字段掩蔽两路实现：
    - 连续字段（名称/ngram）吃 masked 串；
    - token 字段（IDF）吃"存活 token 集"——从**保空格**的 norm 分词（"daoxiantu chouxi"
      才拆得开两个别名源），再剔除平化形只出现在跨度内的 token；
      别名只给存活源注入（review 必修②："不用 hengduanmian"的源词在跨度内→横断面不进扩展区）。"""
    aliases = aliases or {}
    qf = flat(prompt)
    spans = neg_spans(qf)
    masked = mask_spans(qf, spans)
    qt = set()
    for t in tokens(norm(prompt)):
        tf = re.sub(r"\s+", "", t).lower()
        if hit_outside_span(qf, tf, spans):
            qt.add(t)
    ext = qf + "·" + "".join(aliases[t] for t in qt
                             if t in aliases and aliases[t] not in qf)
    return qf, spans, masked, qt, ext


# ── 检索打分（2026-10-27 review 二轮 Q1 最小版：字段权重 + 相邻短语 + 轻量 IDF + 人工别名）──
def idf(data):
    """轻量 IDF：df 按资产全字段 token 并集计。"""
    df = {}
    assets = data.get("assets", [])
    for a in assets:
        toks = set()
        for f in ("name", "summary", "text", "path"):
            toks |= tokens(norm(a.get(f, "")))
        toks |= {norm(t) for t in a.get("triggers", []) if t}
        for t in toks:
            df[t] = df.get(t, 0) + 1
    n = max(1, len(assets))
    return {t: math.log((n + 1) / (d + 1)) + 1 for t, d in df.items()}


def score(query, asset, idf=None, sp=None, ctx=None):
    """字段加权 IDF 打分（W2-④ 修正版，否定语义统一坐标系）：
    ① 触发词/别名精确命中 +trigger_bonus（平化三方同口径）；
    ⑥ 反向触发词：完整负短语或 ≥anti_overlap 个独立子句重叠 → -anti_penalty
       （正向触发命中豁免的边界在 anti_veto 层裁决——score 内保持线性加减）；
    否定跨度（cue 收紧+宾语回扩版）：**字段级掩蔽**——名称/②③ngram/④IDF 计分全部
       吃掩蔽串（review 必修②："不要用格式工具箱"曾靠名称分 9.955 注入）；
    ④ 分母 = max(sqrt(命中), 词数/query_div)。"""
    p = params(sp)
    qn = norm(query)
    if not qn:
        return 0.0
    qf, spans, masked, qt, ext = ctx or qctx(query, p["aliases"])
    for t in list(qt):                              # 别名注入 IDF 词集（存活源才注入）
        if t in p["aliases"]:
            qt.add(p["aliases"][t])
    if not qt:
        return 0.0
    name = norm(asset.get("name", ""))
    text = norm(" ".join([asset.get("summary", ""), asset.get("text", ""),
                          " ".join(asset.get("triggers", []))]))
    path = norm(asset.get("path", ""))
    triggers = {flat(t) for t in asset.get("triggers", []) if t}
    s = 0.0
    hit_len = max((len(t) for t in triggers
                   if t and hit_outside_span(ext, t, spans)), default=0)
    if hit_len:
        # ① 精确触发词/别名 + 特异性小项：长触发词=更具体意图，抑制泛词抢跑
        s += p["trigger_bonus"] + min(hit_len - 2, 6) * 0.3
    else:
        # ⑥ 反向触发词：完整短语命中，或 ≥anti_overlap 个"独立子句"各自与查询重叠
        anti = [flat(a) for a in (asset.get("anti_triggers") or []) if len(flat(a)) >= 3]
        if anti:
            clauses = sum(1 for a in anti if tokens(a) & qt)
            if any(hit_outside_span(ext, a, spans) for a in anti) or clauses >= p["anti_overlap"]:
                s -= p["anti_penalty"]
    name_f = flat(name)
    # ② 名称短语（掩蔽口径；查询≥3 字才允许"查询⊂名称"防 2 字泛查词白得 +3）
    if name_f and (name_f in masked or (len(masked) >= 3 and masked in name_f) or masked == name_f):
        s += 3.0
    zh_segs = re.findall(r"[\u4e00-\u9fff]{2,}", masked)
    grams = set()
    for seg in zh_segs:
        if len(seg) <= 3:
            grams.add(seg)
        else:                                       # ③ 真 n-gram：相邻 2/3-gram 皆可命中
            grams.update(seg[i:i + 3] for i in range(len(seg) - 2))
            grams.update(seg[i:i + 2] for i in range(len(seg) - 1))
    if any(g in flat(text) or g in name_f for g in grams if len(g) >= 2):
        s += 2.0
    idf = idf or {}
    acc = 0.0
    matched_n = 0
    text_f = flat(text)
    path_f = flat(path)
    fw = p["field_w"]
    for t in qt:
        tf = re.sub(r"\s+", "", t).lower()
        w = 0.0
        if tf in triggers:
            w = fw["trigger"]
        elif tf and (tf in name_f or name_f in tf):
            w = fw["name"]
        elif tf and tf in text_f:
            w = fw["text"]
        elif tf and tf in path_f:
            w = fw["path"]
        if w:
            acc += idf.get(t, 2.0) * w
            matched_n += 1
    # ④ 归一分母（W2-② 混合口径）：max(sqrt(命中数), 词数/query_div)——
    # 短查询保持 review 二轮的 sqrt 行为；长查询被稀释，无关词持续命中不再累分抬名次
    denom = max(math.sqrt(max(1, matched_n)), len(qt) / p["query_div"])
    s += acc / denom if matched_n else 0.0
    return round(s, 3)


def search_rows(query, n, scope, data, sp=None):
    rows = [a for a in data["assets"] if scope in (None, "all") or a.get("visibility") == scope]
    idf_ = idf(data)
    scored = sorted(((score(query, a, idf_, sp), a) for a in rows),
                    key=lambda x: (-x[0], x[1]["id"]))
    return [(s, a) for s, a in scored[:n] if s > 0]


# ── 注入资格与路由策略（select_injection 的纯计算体） ──────────────────
def dedup_key(prompt, asset_id):
    return norm(prompt)[:24] + "|" + asset_id


def inject_text(a):
    summary = (a.get("summary") or "")[:40]
    return ("Candidate: " + a.get("name", "")[:26] + " — " + summary + " | " + a.get("path", "")[:52])[:130]


def pos_trigger_hit(asset, ctx):
    """资产的正向触发词是否在查询里命中（平化、否定跨度之外；含别名扩展）。"""
    qf, spans, masked, qt, ext = ctx
    if not qf:
        return False
    return any(hit_outside_span(ext, flat(t), spans)
               for t in asset.get("triggers", []) if t)


def denied_by_name(asset, ctx, deny_re=None):
    """显式点名排除（W2-④，review 必修②）："不要用格式工具箱"→该资产无条件出局。
    片段与资产名**或任一触发词**互相包含（≥3 字）即命中——技能 frontmatter 名常为
    英文 slug（format-toolbox），中文名在触发词里；优先级最高，不参与任何豁免。"""
    qf, spans, masked, qt, ext = ctx
    name_f = flat(asset.get("name", ""))
    trig_fs = [flat(t) for t in asset.get("triggers", []) if t]
    frag_ok = [f for f in explicit_denies(qf, deny_re) if len(f) >= 3]
    if not frag_ok:
        return False
    for f in frag_ok:
        if name_f and (f in name_f or name_f in f):
            return True
        if any(f in tf or tf in f for tf in trig_fs if len(tf) >= 3):
            return True
    return False


def anti_veto(asset, ctx, multi_re=None):
    """高置信负场景一票否决（W2-④ 版，review 必修①的边界重划）。

    完整 anti 短语（平化、≥3 字、否定跨度之外）出现在查询里 → 丧失自动注入资格；
    检索结果照常展示（只否决"自动注入"）。entry/script 继承的 anti 同样生效。

    豁免边界（必修①"硬边界 vs 多意图"的零模型判据）：
    - **多意图连接词在场**（先…再/然后/也要/并改…）且资产自身正向触发词在跨度外命中
      → 判为"独立、受支持的子任务"，放行交回排序（"先量图再批量改图"量图 ✓）；
    - 无连接词 → 同一意图内的泛词命中（"弧形闸门，出结构计算书"的计算书）**不解除否决**。
    显式点名排除（不要用X）优先于一切，在路由的存活过滤里执行。"""
    qf, spans, masked, qt, ext = ctx
    if not qf:
        return False
    anti = [flat(a) for a in (asset.get("anti_triggers") or []) if len(flat(a)) >= 3]
    if not anti or not any(hit_outside_span(ext, a, spans) for a in anti):
        return False                                # 无完整负短语命中：谈不上否决
    if (multi_re or MULTI_INTENT_RE).search(qf) and pos_trigger_hit(asset, ctx):
        return False                                # 独立子任务放行
    return True


def route(prompt, bench, ctx, data, recent, threshold, gap, family_router,
          multi_re=None, deny_re=None):
    """注入路由策略（review Q6）：asset_cli.select_injection 薄壳的计算体。

    策略：内部取 top-3；资格否决先滤（W2-②）；top1 过阈值才注入；同家族并列只注入
    家族路由技能；recent（已注入去重键集合）命中则跳过。
    另两道闸（W2-②）：script 注入须词干触发命中（ppt_theme 曾靠 name 字段滑进 9.5）；
    ≤2 字且无任何触发命中的歧义短查询一律弃权（"结构"类——note: 歧义不自动注入）。
    返回 (text|None, decision, top3, target_id)：target_id=最终注入目标（W2-③——家族改投后
    记 cad-suite 而非原始 top1，去重键与日志同源），decision ∈
    no_hit/low_score/veto/abstain/dup/top1/family:*。"""
    alive = [(s, a) for s, a in bench
             if not denied_by_name(a, ctx, deny_re)     # 显式点名排除：优先级最高
             and not anti_veto(a, ctx, multi_re)
             and not (a["kind"] == "script" and not pos_trigger_hit(a, ctx))]
    top3 = alive[:3]
    if not top3:
        return None, ("veto" if bench else "no_hit"), top3, None
    s1, a1 = top3[0]
    if len(flat(prompt)) <= 2 and not pos_trigger_hit(a1, ctx):
        return None, "abstain", top3, None          # 歧义短查询：宁漏不误，弃权
    s2 = top3[1][0] if len(top3) > 1 else 0.0
    if s1 < threshold:
        return None, "low_score", top3, None
    fam = a1["id"].split(":", 1)[1].split("-", 1)[0] if a1["id"].startswith("skill:") else ""
    target = a1
    if s2 >= s1 - gap and fam in family_router:
        router = next((a for _, a in top3 if a["id"] == family_router[fam]), None)
        if router is None and data:
            # W2-③：按 id 直查，不走 search——"cad-suite"字样检索会先撞上 §0 入口行
            router = next((a for a in data["assets"] if a["id"] == family_router[fam]), None)
        if router is not None:
            target = router
            fam = fam + "(router)"
    key = dedup_key(prompt, target["id"])
    if recent and key in recent:
        return None, "dup", top3, target["id"]      # dup 也返回目标：日志/测试可核对最终去向
    return inject_text(target), ("family:" + fam if "router" in fam else "top1"), top3, target["id"]
