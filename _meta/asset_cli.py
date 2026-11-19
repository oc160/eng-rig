# -*- coding: utf-8 -*-
# ── 资产卡 ────────────────────────────────────────────────
# 名称：asset_cli.py —— 资产路由 CLI（find/show/doctor/digest/gates/index/stats）
# 来源：2026-10-21 团队版架构（Q1 自动化层）
# 提取：2026-10-21；2026-11-16 复杂度整改：纯计算核外移 _meta/hub/
#     {scorer,indexer,ingest}.py（Antigravity 实测本文件 McCabe=436 全库最高），
#     本文件保留全部可变全局状态、cmd_* 命令体与同名薄壳（读全局→调 hub 纯函数）
# Author: EngineerRig
# 验证方式：tests/test_asset_cli.py（打分/索引/过滤纯函数）+ tests/test_routing_dataset.py
#     （路由数据集回归）+ tests/test_feedback_loop.py + tests/test_ingest_capture.py + 全量 pytest
# 重跑命令：python _meta/asset_cli.py find "横断面"
# 依赖：纯标准库 + _meta/hub/ 子包（同库随 git 分发）
# ──────────────────────────────────────────────────────────
"""资产路由 CLI —— 给 AI 调用的唯一查询入口。

设计（Q2 结论 + 2026-10-27 review 二轮效率评审采纳；2026-11-16 复杂度整改）：
  - 索引 = 生成式 JSON，随 git 分发；CLI 启动时校验指纹，过期则内存重建（毫秒级）。
    覆盖：skills + INDEX §0 场景行 + scripts/snippets 带资产卡的规范入口（不再漏）。
  - 打分 = 字段加权 IDF + 相邻短语 + 触发词/别名精确命中；阈值由路由数据集标定
    （tests/routing_dataset.jsonl），改打分必须重标。
  - 注入 = select_injection 纯函数（钩子与测试共用）：top-1 + 同家族路由 + 会话去重。
  - 反馈 = find/inject 落库外 JSONL（查询只存加盐哈希，不存原文），find-stats 出周报。
  - 不引入常驻服务、向量库与第二张 ACL 表；可见性由路径约定 + visibility 字段单一推导。
  - 结构（2026-11-16）：计算核在 _meta/hub/（scorer=打分路由 / indexer=索引组装 /
    ingest=收件日志逻辑），全部显式收参、不读本模块全局；本模块持全局状态与薄壳，
    标定常量（ALIASES/FIELD_W/INJECT_*/TRIGGER_BONUS/ANTI_*/QUERY_DIV）在调用点
    取当值传 hub——tools/calibrate_routing.py 对本模块 setattr 后立即生效。

用法（AI 侧）：
  python _meta/asset_cli.py find "批量改页脚" --json
  python _meta/asset_cli.py find "横断面" --format=context   （L2 宿主 AI 友好输出）
  python _meta/asset_cli.py show skill:format-toolbox
  python _meta/asset_cli.py find-stats                       （反馈周报）
  python _meta/asset_cli.py log-open --path=<被读文件> --session=<id>   （钩子静默调用：读文件自动落 open，反馈闭环）
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent
INDEX = LIB / "_meta" / "index" / "asset-index.json"
SKILLS = LIB / "skills"
INDEX_MD = LIB / "INDEX.md"

# ── hub 子包装载（2026-11-16 复杂度整改） ──────────────────
# tests 用 spec_from_file_location 加载本文件时 sys.path 无 _meta，不能裸 import hub
# （与 _card() 同一装载口径）；hub 模块不 import 本模块（单向被依赖）。
_HUB = {}


def _hub(name):
    m = _HUB.get(name)
    if m is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(f"_hub_{name}", LIB / "_meta" / "hub" / f"{name}.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _HUB[name] = m
    return m


def _scorer():
    """打分/路由纯计算核（hub/scorer.py）。"""
    return _hub("scorer")


def _indexer():
    """索引组装纯计算核（hub/indexer.py）。"""
    return _hub("indexer")


def _ingesth():
    """收件/日志纯逻辑核（hub/ingest.py）。"""
    return _hub("ingest")


def _taskh():
    """Git-Native 多代理任务胶囊引擎（hub/tasks.py）。"""
    return _hub("tasks")


def _dash():
    """只读单页白盒仪表盘生成器（hub/dashboard.py）。"""
    return _hub("dashboard")


def _card():
    """资产卡统一解析器（2026-11-16 P3-1：索引/门禁/收尾三处口径归一的单源）。"""
    global _CARD
    if _CARD is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_card", LIB / "_meta" / "hub" / "card_parser.py")
        _CARD = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_CARD)
    return _CARD


_CARD = None

# ── 正则/常量（唯一定义源在 hub；此处重导出保持模块属性面 + 调用点传参可猴补）──
TRIGGER_RE = _indexer().TRIGGER_RE          # 触发词段（hub/indexer.py）
# 反向触发词（review 未提、DeltaReplay ρ 的零模型版）：技能"不适用：…"行=现成的适用性负标注。
# 命中且正向触发词未命中 → 罚分，防"关键词全中但场景明确不该用"的误注入。
ANTI_RE = _indexer().ANTI_RE
NEG_CUE_RE = _scorer().NEG_CUE_RE           # 否定 cue（祈使搭配收紧版，hub/scorer.py）
_SEPA = _scorer().SEPA
MULTI_INTENT_RE = _scorer().MULTI_INTENT_RE  # 多意图连接词（否决豁免判据）
DENY_USE_RE = _scorer().DENY_USE_RE          # 显式点名排除（不要用X/别用X）

# ── 检索打分标定常量（2026-10-27 review 二轮 Q1 + W2-① 提名化）──
# 人工别名：检索变体→规范词（先少量人工维护，不上拼音库/同音消歧）
ALIASES = {
    "hengduanmian": "横断面", "daoxiantu": "导线图", "chouxi": "抽稀",
    "tianwafang": "填挖方", "tufang": "土方", "zongduanmian": "纵断面",
    "公司蓝": "企业蓝",                      # W2-②：口语变体→§0 条目规范词（make-report-ppt 场景）
    "sl": "规范",
}
FIELD_W = {"trigger": 3.0, "name": 2.0, "text": 1.0, "path": 0.5}
# 注入阈值：由 tests/test_routing_dataset.py 在路由数据集上标定；改打分必须重标，不得沿用旧值
INJECT_THRESHOLD = 9.3
INJECT_GAP = 1.5                    # top1 与 top2 分差不足此值且同家族 → 改注入家族路由技能
FAMILY_ROUTER = {"cad": "skill:cad-suite"}
# 五参数标定常量（W2-① 提名化：tools/calibrate_routing.py sweep 的可变轴；
# 2026-11-02 标定证据见 _meta/calibration_20261102.md）
TRIGGER_BONUS = 7.0                 # ① 精确触发词/别名命中（3字短意图也须过阈值）
ANTI_PENALTY = 3.0                  # ⑥ 反向触发词罚分（低置信：参与排序加减）
ANTI_OVERLAP = 2                    # ⑥ 负场景 token 重叠数（≥此值才罚，防孤词误伤）
QUERY_DIV = 4                       # ④ 长查询稀释：分母 = max(sqrt(命中数), 词数/QUERY_DIV)


def _sp():
    """打分参数取当值（调用点读模块全局——calibrate_routing setattr 后立即生效）。"""
    return {"aliases": ALIASES, "field_w": FIELD_W, "trigger_bonus": TRIGGER_BONUS,
            "anti_penalty": ANTI_PENALTY, "anti_overlap": ANTI_OVERLAP, "query_div": QUERY_DIV}


# ── 索引构建（薄壳：解析体在 hub/indexer.py） ─────────────────
def _frontmatter(text):
    return _indexer().frontmatter(text)


def _fm_value(fm, key):
    return _indexer().fm_value(fm, key)


def _one_line(s, limit=46):
    return _indexer().one_line(s, limit)


def _triggers(desc):
    return _indexer().triggers(desc, TRIGGER_RE)


def _anti_triggers(desc):
    return _indexer().anti_triggers(desc, ANTI_RE)


def _carded_scripts():
    """scripts/snippets 中带资产卡的规范入口（与四闸"必须有卡"同口径，review Q4：
    只索引规范入口，helper/归档/实验件不入）。进程内缓存一次。"""
    global _CARDED_CACHE
    if _CARDED_CACHE is not None:
        return _CARDED_CACHE
    _CARDED_CACHE = _indexer().scan_carded(LIB)
    return _CARDED_CACHE


_CARDED_CACHE = None


def build_index():
    """全量索引组装（skill/§0 entry/带卡 script + W2-② anti 继承）。
    解析与组装体在 hub/indexer.py；本壳只取全局路径并拼装元信息。"""
    H = _indexer()
    assets = H.skill_assets(SKILLS)
    entries, dropped, parsed = H.s0_entries(INDEX_MD.read_text(encoding="utf-8", errors="replace"))
    assets += entries
    C = _card()
    for p, head in _carded_scripts():
        rel = p.relative_to(LIB).as_posix()
        assets.append(H.script_asset(rel, p.stem, head,
                                     C.parse_asset_card(head), C.card_fields_text(head)))
    H.inherit_anti(assets)
    return {
        "version": 2,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "fingerprint": source_fingerprint(),
        "entry_count": parsed,
        "dropped_rows": dropped,
        "assets": assets,
        "boundaries": H.boundary_assets(LIB / "_meta" / "能力边界台账.yaml"),
    }


def _sources():
    return ([p for p in sorted(SKILLS.glob("*/SKILL.md"))] + [INDEX_MD]
            + [p for p, _ in _carded_scripts()])


def source_fingerprint():
    h = hashlib.sha1()
    for p in _sources():
        h.update(p.name.encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()


def load_index(allow_rebuild=True):
    try:
        data = json.loads(INDEX.read_text(encoding="utf-8"))
        if data.get("fingerprint") == source_fingerprint():
            return data, False
    except (OSError, ValueError):
        data = None
    if not allow_rebuild:
        return data or {"assets": []}, True
    data = build_index()                    # 过期/缺失 → 内存重建（不落盘）
    return data, True


# ── 检索打分（薄壳：计算体在 hub/scorer.py） ─────────────────
def _norm(s):
    return _scorer().norm(s)


def _tokens(s):
    return _scorer().tokens(s)


def _flat(s):
    return _scorer().flat(s)


def _neg_spans(flat):
    return _scorer().neg_spans(flat, NEG_CUE_RE, _SEPA)


def _explicit_denies(flat):
    return _scorer().explicit_denies(flat, DENY_USE_RE)


def _hit_outside_span(flat, needle, spans):
    return _scorer().hit_outside_span(flat, needle, spans)


def _mask_spans(flat, spans):
    return _scorer().mask_spans(flat, spans)


def _qctx(prompt):
    return _scorer().qctx(prompt, ALIASES)


def _idf(data):
    return _scorer().idf(data)


def score(query, asset, idf=None):
    """字段加权 IDF 打分（W2-④ 否定语义统一坐标系）。计算体在 hub/scorer.score。"""
    return _scorer().score(query, asset, idf, _sp())


def search(query, n=3, scope=None, data=None):
    data = data or load_index()[0]
    return _scorer().search_rows(query, n, scope, data, _sp())


def _dedup_key(prompt, asset_id):
    return _scorer().dedup_key(prompt, asset_id)


def _inject_text(a):
    return _scorer().inject_text(a)


def _pos_trigger_hit(prompt, asset, ctx=None):
    """资产的正向触发词是否在查询里命中（平化、否定跨度之外；含别名扩展）。"""
    return _scorer().pos_trigger_hit(asset, ctx or _qctx(prompt))


def _denied_by_name(prompt, asset, ctx=None):
    """显式点名排除（W2-④，review 必修②）："不要用格式工具箱"→该资产无条件出局。
    计算体在 hub/scorer.denied_by_name。"""
    return _scorer().denied_by_name(asset, ctx or _qctx(prompt), DENY_USE_RE)


def anti_veto(prompt, asset):
    """高置信负场景一票否决（W2-④ 版）。豁免边界（多意图连接词+正向命中）与
    完整判据见 hub/scorer.anti_veto 文档。"""
    return _scorer().anti_veto(asset, _qctx(prompt), MULTI_INTENT_RE)


def select_injection(prompt, data=None, recent=None):
    """纯路由函数（review Q6）：agent_hooks 与测试共用同一份阈值/家族/去重策略。

    策略：内部取 top-3；资格否决先滤（W2-②）；top1 过阈值才注入；同家族并列只注入
    家族路由技能；recent（已注入去重键集合）命中则跳过。
    另两道闸（W2-②）：script 注入须词干触发命中（ppt_theme 曾靠 name 字段滑进 9.5）；
    ≤2 字且无任何触发命中的歧义短查询一律弃权（"结构"类——note: 歧义不自动注入）。
    返回 (text|None, decision, top3, target_id)：target_id=最终注入目标（W2-③——家族改投后
    记 cad-suite 而非原始 top1，去重键与日志同源），decision ∈
    no_hit/low_score/veto/abstain/dup/top1/family:*。
    策略体在 hub/scorer.route（阈值/家族表在调用点取当值传入）。"""
    bench = search(prompt, n=8, data=data)          # 加深候选池：否决/点名滤除后仍保证 3 席
    return _scorer().route(prompt, bench, _qctx(prompt), data, recent,
                           INJECT_THRESHOLD, INJECT_GAP, FAMILY_ROUTER,
                           MULTI_INTENT_RE, DENY_USE_RE)


# ── 子命令 ────────────────────────────────────────────────
# ── 反馈日志（review Q3 最小版）：库外 JSONL，查询只存加盐哈希，绝不存原文 ──
LOG_DIR = LIB.parent / "_asset_logs"
FIND_LOG = LOG_DIR / "find.jsonl"
# P2-1（Antigravity 评审采纳）：会话状态——注入/检索命中时记 query_id↔asset，
# 供 log-open（AI 读文件自动落 open）与 record-retro（reused 自动关联落 adopt）回查。
SESSION_STATE = LOG_DIR / "last_session_state.json"
_SALT = None


def _salt():
    global _SALT
    if _SALT is None:
        f = LOG_DIR / ".salt"
        try:
            _SALT = f.read_bytes().strip()
        except OSError:
            import secrets
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            _SALT = secrets.token_bytes(16)
            f.write_bytes(_SALT)
    return _SALT


def _qhash(q):
    return _ingesth().qhash(_salt(), q)


def _nolog():
    return os.environ.get("ASSET_HUB_FIND_NOLOG") == "1"


def _log_event(rec):
    if _nolog():
        return
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        rec.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
        rec.setdefault("host", os.environ.get("COMPUTERNAME") or "")
        with FIND_LOG.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass                                        # 日志失败不挡检索


def _session_state_path():
    # 跟随 LOG_DIR：测试只重定向 LOG_DIR 未重定向 SESSION_STATE 时一并对齐，绝不写真实日志目录
    return SESSION_STATE if SESSION_STATE.parent == LOG_DIR else LOG_DIR / SESSION_STATE.name


def _session_state_load():
    """读会话状态（P2-1）。异常一律吞掉返回 {}——日志类设施绝不挡主流程。"""
    try:
        d = json.loads(_session_state_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _session_state_save(d):
    try:
        _ingesth().atomic_write_text(_session_state_path(), json.dumps(d, ensure_ascii=False, indent=1))
    except (OSError, ValueError):
        pass                                        # 状态写失败不挡主流程


def _remember_injection(session, query_id, asset_id, asset_path, query_hash=""):
    """注入记忆（P2-1 最后一公里）：state[session] = {query_id, asset_id, asset_path, ts[, query_hash]}
    （merge 保存；无 session 落 "cli" 键）。NOLOG 时同样不记，与反馈日志同口径。"""
    try:
        if _nolog():
            return
        st = _session_state_load()
        rec = {"query_id": query_id, "asset_id": asset_id,
               "asset_path": asset_path, "ts": datetime.now().isoformat(timespec="seconds")}
        if query_hash:
            rec["query_hash"] = query_hash
        st[session or "cli"] = rec
        if len(st) > 100:                           # 防无限增长：只留最近 100 个会话
            st = dict(sorted(st.items(), key=lambda kv: str(kv[1].get("ts", "")),
                             reverse=True)[:100])
        _session_state_save(st)
    except Exception:
        pass                                        # 记忆失败不影响注入本身


def _same_file(a, b):
    """同文件判定（P2-1）。计算体在 hub/ingest.same_file。"""
    return _ingesth().same_file(a, b)


def _recent_injections(session, limit=300):
    """本会话已注入的去重键（review Q2：同会话相同查询+资产不重复注入）。"""
    if not session or _nolog() or not FIND_LOG.exists():
        return set()
    return _ingesth().recent_keys(FIND_LOG, session, limit)


def _related_assets(seed_assets, data, scope=None, limit=2):
    """1 跳同类思路关联：从命中资产的 related 解析互补条目（去重并守 visibility 边界）。"""
    seen = {a["id"] for a in seed_assets}
    by_id = {a["id"]: a for a in data.get("assets", [])}
    out = []
    for a in seed_assets:
        for ref in a.get("related") or []:
            tgt = by_id.get(ref) or by_id.get(f"skill:{ref}")
            if not tgt or tgt["id"] in seen:
                continue
            if scope not in (None, "all") and tgt.get("visibility") != scope:
                continue
            seen.add(tgt["id"])
            out.append(tgt)
            if len(out) >= limit:
                return out
    return out


def cmd_inject_candidate(argv):
    """钩子用的注入候选（review Q5/Q6）：select_injection 纯策略 + 落反馈日志。

    返回 JSON：{"text": 注入行|null, "decision": top1/family:*/low_score/no_hit/dup, "top": [...]}
    """
    import uuid
    prompt = next((a.split("=", 1)[1] for a in argv if a.startswith("--prompt=")), "")
    session = next((a.split("=", 1)[1] for a in argv if a.startswith("--session=")), "")
    if not prompt:
        print(json.dumps({"text": None, "decision": "no_prompt"}, ensure_ascii=False))
        return 0
    data, stale = load_index()
    recent = _recent_injections(session)
    text, decision, top3, target_id = select_injection(prompt, data=data, recent=recent)
    qid = uuid.uuid4().hex[:12]
    qh = _qhash(prompt)
    _log_event({"event": "search", "source": "hook", "query_id": qid,
                "session": session, "query_hash": qh, "qlen": len(prompt),
                "candidates": [{"id": a["id"], "rank": i, "score": s} for i, (s, a) in enumerate(top3, 1)],
                "injected": bool(text), "decision": decision,
                # W2-③ 修复：去重键记最终注入目标（家族改投后记路由技能，非原始 top1）——
                # 否则二次查询日志键对不上 _recent_injections 回读，家族改投路径去重失效
                "dedup_key": _dedup_key(prompt, target_id) if text else "",
                "stale": stale})
    if text and target_id:                         # P2-1：注入命中 → 记会话状态供 open/adopt 自动关联
        target_asset = next((a for a in data["assets"] if a["id"] == target_id), None)
        if target_asset is not None:
            _remember_injection(session, qid, target_id, target_asset.get("path", ""), qh)
            rels = [r for r in _related_assets([target_asset], data, limit=1)
                    if not _denied_by_name(prompt, r) and not anti_veto(prompt, r)]
            if rels:
                text = (text + f" (Related: {rels[0]['name']} -> {rels[0]['path']})")[:180]
    print(json.dumps({"text": text, "decision": decision,
                      "top": [{"id": a["id"], "score": s} for s, a in top3]}, ensure_ascii=False))
    return 0


def cmd_find_stats(argv):
    """find 反馈周报（review Q3）：检索次数/零结果率/注入率/采纳信号，只聚合不存原文。"""
    if not FIND_LOG.exists():
        print("暂无 find 日志（正常使用后自动积累于库外 _asset_logs/find.jsonl）")
        return 0
    from collections import Counter
    recs = []
    for l in FIND_LOG.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            recs.append(json.loads(l))
        except ValueError:
            continue
    searches = [r for r in recs if r.get("event") == "search"]
    zero = sum(1 for r in searches if not r.get("candidates"))
    inj = [r for r in searches if r.get("injected")]
    opens = sum(1 for r in recs if r.get("event") == "open")
    adopts = sum(1 for r in recs if r.get("event") == "adopt")
    dups = sum(1 for r in searches if r.get("decision") == "dup")
    top = Counter(r["candidates"][0]["id"] for r in inj if r.get("candidates"))
    pct = lambda a, b: f"{a * 100 // max(1, b)}%"
    print(f"检索 {len(searches)} 次：零结果 {zero}（{pct(zero, len(searches))}）、"
          f"注入 {len(inj)}（{pct(len(inj), len(searches))}）、去重拦截 {dups}")
    print(f"采纳信号：open {opens}、adopt {adopts}（retro --reused 带 --query 落账）")
    print("注入 top1 资产：" + ("，".join(f"{k}×{v}" for k, v in top.most_common(8)) or "（无）"))
    return 0


def cmd_find(argv):
    args = [a for a in argv if not a.startswith("--")]
    query = " ".join(args)
    n = 3
    for a in argv:
        if a.startswith("--n="):
            n = int(a.split("=", 1)[1])
    session = next((a.split("=", 1)[1] for a in argv if a.startswith("--session=")), "")
    scope = "public" if "--public" in argv else None
    data, stale = load_index()
    hits = search(query, n=n, scope=scope, data=data)
    # 反馈日志（review Q3）：零结果也记——零结果率是索引覆盖的核心指标
    import uuid
    qid = uuid.uuid4().hex[:12]
    qh = _qhash(query) if query else ""
    _log_event({"event": "search", "source": "cli", "query_id": qid,
                "session": session, "query_hash": qh,
                "qlen": len(query),
                "candidates": [{"id": a["id"], "rank": i, "score": s} for i, (s, a) in enumerate(hits, 1)],
                "injected": bool(hits and hits[0][0] >= INJECT_THRESHOLD),
                "stale": stale})
    if hits:                                       # P2-1：有 top1 → 记会话状态（无 session 落 "cli" 键）
        _remember_injection(session, qid, hits[0][1]["id"], hits[0][1].get("path", ""), qh)
    rels = [r for r in _related_assets([a for _, a in hits], data, scope=scope)
            if not _denied_by_name(query, r)]
    b_hits = _indexer().match_boundaries(query, data, scope, _sp())
    if "--format=context" in argv:                 # L2 宿主 AI 友好输出（review Q7）
        for i, (s, a) in enumerate(hits, 1):
            print(f"- {a['name']}（{a['kind']}）：{a['summary']} → {a['path']}")
        for r in rels:
            print(f"- [同类思路] {r['name']}（{r['kind']}）：{r['summary']} → {r['path']}")
        for _, b in b_hits:
            print(f"- [边界避坑] {b['name']}：{b['summary']} → {b['path']}")
        print('用法：让 AI 读取上述 SKILL.md/资产卡后执行；手动查询 asset find "<任务原话>"')
        return 0
    if "--json" in argv:
        print(json.dumps({"query": query, "stale": stale,
                          "results": [{"score": s, **a} for s, a in hits],
                          "related": rels, "boundaries": [{"score": s, **b} for s, b in b_hits]},
                         ensure_ascii=False, indent=2))
        return 0
    if not hits and not b_hits:
        print("（无匹配资产；可试更具体的关键词，或直接说明任务让 AI 判断）")
        return 0
    for i, (s, a) in enumerate(hits, 1):
        trig = "/".join(a.get("triggers", [])[:3])
        print(f"{i} [{a['kind']}] {a['name']} — {a['summary']} | {a['path']}")
        if trig:
            print(f"   触发: {trig}")
    if rels:
        print("💡 同类思路参考（1跳关联）：")
        for r in rels:
            print(f"   ↳ [{r['kind']}] {r['name']} — {r['summary']} | {r['path']}")
    if b_hits:
        print("⚠ 能力边界避坑（已知现象/退路）：")
        for _, b in b_hits:
            print(f"   ↳ [{b['name']}] {b['summary']} | {b['path']}")
    if stale:
        print("（索引已按当前文件重建；如需持久化运行 index）", file=sys.stderr)
    return 0


def cmd_show(argv):
    if not argv:
        print("用法：asset show <id> [--from=<query_id>]  （--from 落 open 事件，反馈闭环弱信号）")
        return 2
    qid = next((a.split("=", 1)[1] for a in argv if a.startswith("--from=")), "")
    data, _ = load_index()
    for a in data["assets"]:
        if a["id"] == argv[0] or a["name"] == argv[0]:
            if qid:
                _log_event({"event": "open", "query_id": qid, "asset": a["id"]})
            print(json.dumps(a, ensure_ascii=False, indent=2) if "--json" in argv
                  else f"{a['id']}\n{a['name']} — {a['summary']}\n路径: {a['path']}\n触发: {', '.join(a['triggers'])}")
            return 0
    print(f"未找到资产：{argv[0]}")
    return 1


def cmd_log_open(argv):
    """读文件自动落 open 事件（P2-1 反馈闭环最后一公里，钩子静默调用）。

    --path=<被读文件> --session=<id>：查会话状态里该 session（无记录则取任意 session
    最近一条），--path 与记录的 asset_path 同文件（normcase 或路径尾部匹配）→
    自动 _log_event({"event":"open", query_id, asset, session})。零异常外抛。
    """
    out = {"logged": False}
    try:
        path = next((a.split("=", 1)[1] for a in argv if a.startswith("--path=")), "")
        session = next((a.split("=", 1)[1] for a in argv if a.startswith("--session=")), "")
        if path:
            st = _session_state_load()
            rec = st.get(session) if session else None
            if not isinstance(rec, dict):
                rec = None
            if rec is None:                        # 无该 session 记录 → 最近一条任意 session
                recs = [r for r in st.values() if isinstance(r, dict)]
                rec = max(recs, key=lambda r: str(r.get("ts", "")), default=None)
            if rec and rec.get("asset_path") and _same_file(path, rec["asset_path"]):
                ev = {"event": "open", "query_id": rec.get("query_id", ""),
                      "asset": rec.get("asset_id", ""), "session": session}
                if rec.get("query_hash"):
                    ev["query_hash"] = rec["query_hash"]
                _log_event(ev)
                out = {"logged": True, "event": "open",
                       "query_id": rec.get("query_id", ""), "asset": rec.get("asset_id", "")}
    except Exception:
        out = {"logged": False}
    print(json.dumps(out, ensure_ascii=False))
    return 0


def cmd_index(argv):
    data = build_index()
    if "--check" in argv:
        cur, stale = load_index(allow_rebuild=False)
        print(f"索引{'过期' if stale else '最新'}：{len(cur.get('assets', []))} 条")
        return 1 if stale else 0
    _ingesth().atomic_write_text(INDEX, json.dumps(data, ensure_ascii=False, indent=1))
    print(f"索引已生成：{INDEX.relative_to(LIB)}（{len(data['assets'])} 条）")
    return 0


def cmd_doctor(argv):
    cmd = [sys.executable, str(LIB / "_meta" / "health_check.py")]
    if "--quick" in argv:
        cmd.append("--quick")
    return subprocess.run(cmd).returncode


def cmd_digest(argv):
    user = "me"
    for a in argv:
        if a.startswith("--user="):
            user = a.split("=", 1)[1]
    d = LIB / "_meta" / "digests"
    d.mkdir(parents=True, exist_ok=True)
    week = datetime.now().strftime("%Y-W%V")
    p = d / f"{week}-{user}.md"
    if p.exists():
        print(f"本周摘要已存在：{p.relative_to(LIB)}")
        return 0
    tpl = (LIB / "_meta" / "templates" / "digest.md").read_text(encoding="utf-8")
    p.write_text(tpl.replace("{{week}}", week).replace("{{user}}", user),
                 encoding="utf-8", newline="\n")
    print(f"已创建：{p.relative_to(LIB)}（情境/教训/证据 三栏，提交后由 maintainer 评审）")
    return 0


def _find_bash():
    """找可用的 bash。Windows 上裸 `bash` 常解析到 System32\\bash.exe（WSL 壳），
    没有发行版时直接 execvpe 失败——所以优先用 Git 自带的 bash。"""
    if os.name != "nt":
        return shutil.which("bash")
    try:
        gp = subprocess.run(["git", "--exec-path"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace").stdout.strip()
        if gp:
            cand = Path(gp).parents[1] / "bin" / "bash.exe"
            if cand.is_file():
                return str(cand)
    except OSError:
        pass
    w = shutil.which("bash")
    if w and "system32" not in w.lower():
        return w
    return None


def _staged_count():
    r = subprocess.run(["git", "-c", "core.quotepath=false", "diff", "--cached", "--name-only"],
                       cwd=LIB, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return len([x for x in r.stdout.splitlines() if x.strip()])


def cmd_gates(argv):
    """手动跑门禁（L2 宿主没有 pre-commit 时的替代）。

    行为：
      - 有暂存内容：跑 canonical pre-commit（检查的是暂存内容，与提交时同源）；
      - 无暂存内容：说明"提交前还没 add"，改为按工作树跑 gate_check + check_paths 全量检查。
    这样手动闸不会因为"没 add"而假绿。
    """
    hook = LIB / "_meta" / "hooks" / "pre-commit"
    if _staged_count() > 0:
        b = _find_bash()
        if b:
            return subprocess.run([b, str(hook)], cwd=str(LIB)).returncode
        print("! 未找到可用 bash，改为纯 Python 四闸检查（gate_check + check_paths）")
    else:
        print("! 无暂存内容：按工作树全量检查（提交前请先 git add）")
    rc1 = subprocess.run([sys.executable, str(LIB / "_meta" / "gate_check.py"), "--root", "."],
                         cwd=str(LIB)).returncode
    rc2 = subprocess.run([sys.executable, str(LIB / "_meta" / "check_paths.py")],
                         cwd=str(LIB)).returncode
    return max(rc1, rc2)


INBOX = LIB / "_meta" / "inbox"
# 库外暂存区（reviewer 评审 2026-10-27 采纳）：捕获一律先落这里，敏感预扫
# 干净才发布进库内收件箱；命中的留在暂存区等人工处置。库内永不落未扫描文件。
STAGING = LIB.parent / "_inbox_staging"


def _prescan(data: bytes, name: str):
    """发布前敏感预扫：返回命中列表（空=干净）。与 gate_check 同一套规则语义。

    fail-closed：扫描器本身抛错也返回非空（宁可隔离，不可带密进库）。
    命中描述由 gate_check 生成——只记规则号+行号，不回显内容。
    """
    flags = []
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_asset_gate", LIB / "_meta" / "gate_check.py")
        g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(g)
        if g.FORBIDDEN.search(name):
            flags.append("敏感文件名")
        flags.extend(g._scan_bytes(name, data)[:3])
    except Exception as e:  # noqa: BLE001 —— 预扫失败按隔离处理
        flags.append(f"扫描器异常:{type(e).__name__}")
    return flags
INGEST_EXT = {".py", ".js", ".ps1", ".pyw", ".md"}
INGEST_MAX_BYTES = 2 * 1024 * 1024
INGEST_SKIP = {"node_modules", ".git", "__pycache__", ".venv", ".zcode", "temp", "tmp",
               "dist", "build", "site-packages"}


def _ingest_reason(p: Path):
    """外部产出是否值得收进收件箱（判定体在 hub/ingest.ingest_reason；
    temp 开关读环境后传参）。"""
    skip = INGEST_SKIP if os.environ.get("ASSET_HUB_INGEST_ALLOW_TEMP") != "1" else INGEST_SKIP - {"temp", "tmp"}
    return _ingesth().ingest_reason(p, INGEST_EXT, skip)


def _inbox_exists(src: Path, sha: str):
    return _ingesth().inbox_exists((INBOX, STAGING), src, sha)


def _find_representative(src: Path, session: str):
    """同源同会话且仍待蒸馏的代表版本（review Q5 止血）。计算体在 hub/ingest。"""
    return _ingesth().find_representative(INBOX, src, session)


def cmd_ingest(argv):
    """产出捕获：外部可复用件 → 库外暂存 → 敏感预扫 → 干净才发布 _meta/inbox/<日期>/。

    纪律（2026-10-27 reviewer 评审采纳）：自动化只发现、不提交——本命令零 git 操作，
    发布仅落工作树，采纳与提交仍由人工过四闸把关。命中的隔离在库外暂存区待人工处置。
    返回 JSON（供 PostToolUse 钩子读取）：{"status": captured|quarantined|needs_card|skipped|inside_lib, ...}
    """
    file_arg = next((a.split("=", 1)[1] for a in argv if a.startswith("--file=")), "")
    session = next((a.split("=", 1)[1] for a in argv if a.startswith("--session=")), "")
    if not file_arg:
        print(json.dumps({"status": "skipped", "why": "no_file"}, ensure_ascii=False))
        return 0
    src = Path(file_arg)
    if not src.is_absolute():
        print(json.dumps({"status": "skipped", "why": "relative"}, ensure_ascii=False))
        return 0
    inside = LIB in src.resolve().parents
    if inside:
        rel = src.resolve().relative_to(LIB)
        if str(rel).startswith(("scripts/", "snippets/")) and src.suffix.lower() in {".py", ".js", ".ps1", ".pyw"}:
            head = src.read_text(encoding="utf-8", errors="ignore")[:4000]
            if "资产卡" not in head and "Asset Card" not in head:
                print(json.dumps({"status": "needs_card", "file": str(rel)}, ensure_ascii=False))
                return 0
        if str(rel).startswith(("skills/", "INDEX.md")):
            cmd_index(["--quiet"])
        print(json.dumps({"status": "inside_lib", "file": str(rel)}, ensure_ascii=False))
        return 0

    reason = _ingest_reason(src)
    if not reason:
        print(json.dumps({"status": "skipped"}, ensure_ascii=False))
        return 0
    if any(ord(c) < 32 or ord(c) == 127 for c in str(src)):
        print(json.dumps({"status": "skipped", "why": "bad_path"}, ensure_ascii=False))
        return 0
    try:
        if src.stat().st_size > INGEST_MAX_BYTES:
            print(json.dumps({"status": "skipped", "why": "too_large"}, ensure_ascii=False))
            return 0
    except OSError:
        pass
    data = src.read_bytes()
    sha = hashlib.sha1(data).hexdigest()
    dup = _inbox_exists(src.resolve(), sha)
    if dup:
        try:
            shown = str(dup.relative_to(LIB))
        except ValueError:                       # 收件箱被测试指向库外时显示绝对路径
            shown = str(dup)
        print(json.dumps({"status": "skipped", "why": "duplicate", "inbox": shown},
                         ensure_ascii=False))
        return 0
    if os.environ.get("ASSET_HUB_INGEST_DRY") == "1":
        print(json.dumps({"status": "captured", "inbox": "(dry-run) " + src.name,
                          "published": False, "scan": "dry"}, ensure_ascii=False))
        return 0
    day = datetime.now().strftime("%Y-%m-%d")
    # ① 捕获一律先进库外暂存区（2026-10-27 评审采纳：库内不落未扫描文件，永不自动 git）
    sdir = STAGING / day
    sdir.mkdir(parents=True, exist_ok=True)
    starget = sdir / src.name
    n = 1
    while starget.exists():
        starget = sdir / f"{src.stem}-{n}{src.suffix}"
        n += 1
    starget.write_bytes(data)
    # ② 敏感预扫（fail-closed）
    flags = _prescan(data, src.name)
    if flags:
        (starget.with_name(starget.name + ".meta.json")).write_text(json.dumps({
            "source": str(src.resolve()), "captured": datetime.now().isoformat(timespec="seconds"),
            "sha1": sha, "size": len(data), "reason": reason, "session": session,
            "status": "隔离待审", "flags": flags,
        }, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        print(json.dumps({"status": "quarantined", "staging": str(starget),
                          "flags": flags[:3]}, ensure_ascii=False))
        return 0
    # ③ 干净 → 发布：同源同会话已有代表版本则原地更新（review Q5 止血，不复制 -1..-N）；
    # 否则新件落 _meta/inbox/<日期>/（仅落工作树；git 提交仍由人工过四闸把关）
    rep = _find_representative(src.resolve(), session)
    if rep is not None:
        rep.write_bytes(data)
        rmeta = rep.with_name(rep.name + ".meta.json")
        d = json.loads(rmeta.read_text(encoding="utf-8"))
        hist = list(d.get("sha_history") or [])
        if d.get("sha1"):
            hist.append(d["sha1"])
        d.update({"sha1": sha, "size": len(data),
                  "captured": datetime.now().isoformat(timespec="seconds"),
                  "reason": reason, "versions": int(d.get("versions", 1)) + 1,
                  "sha_history": hist[-50:]})
        rmeta.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        starget.unlink(missing_ok=True)
        try:
            shown = str(rep.relative_to(LIB))
        except ValueError:
            shown = str(rep)
        print(json.dumps({"status": "captured", "inbox": shown, "published": True,
                          "updated": True, "versions": d["versions"]}, ensure_ascii=False))
        return 0
    target_dir = INBOX / day
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / src.name
    n = 1
    while target.exists():
        target = target_dir / f"{src.stem}-{n}{src.suffix}"
        n += 1
    target.write_bytes(data)
    meta = target.with_name(target.name + ".meta.json")
    meta.write_text(json.dumps({
        "source": str(src.resolve()),
        "captured": datetime.now().isoformat(timespec="seconds"),
        "sha1": sha,
        "size": len(data),
        "reason": reason,
        "session": session,
        "status": "待蒸馏",
    }, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    starget.unlink(missing_ok=True)             # 干净件不需要暂存副本
    try:
        shown = str(target.relative_to(LIB))
    except ValueError:                           # INBOX 被测试指向库外时显示绝对路径
        shown = str(target)
    print(json.dumps({"status": "captured", "inbox": shown,
                      "published": True}, ensure_ascii=False))
    return 0


def cmd_inbox(argv):
    items = sorted(INBOX.rglob("*.meta.json")) if INBOX.exists() else []
    if "--json" in argv:
        out = []
        for m in items:
            try:
                out.append(json.loads(m.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    show_all = "--full" in argv
    shown = items if show_all else items[:30]
    print(f"收件箱 {len(items)} 件（待蒸馏" + ("" if show_all or len(items) <= 30 else "；默认显前30条，加 --full 看全量") + "）：")
    for m in shown:
        try:
            d = json.loads(m.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        print(f"  {m.parent.name}/{m.name[:-len('.meta.json')]} ← {d.get('source', '?')[:60]}")
    quar = sorted(STAGING.rglob("*.meta.json")) if STAGING.exists() else []
    if quar:
        print(f"隔离待审 {len(quar)} 件（库外 {STAGING}，命中敏感预扫，人工处置后才可进库）：")
        for m in quar[:10]:
            try:
                d = json.loads(m.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            print(f"  {m.parent.name}/{m.name[:-len('.meta.json')]} ← {d.get('source', '?')[:50]}"
                  f" 命中:{','.join(d.get('flags', []))}")
    return 0


RETRO = LIB / "_meta" / "retro" / "retro.jsonl"


def cmd_record_retro(argv):
    """retro 结构化轻记录（一行 JSONL）。2026-10-27 评审采纳：retro→资产改变是闭环
    最薄弱一环——从"打印三问"升级为"每次收尾留一条可周查的记录"。
    "无需改变"也是合法结论，不强迫每次产资产。"""
    kv = {a[2:].split("=", 1)[0]: a[2:].split("=", 1)[1] for a in argv if a.startswith("--") and "=" in a}
    if not kv.get("task"):
        print("用法: record-retro --task=<一句话任务> [--evidence=<验收证据>]")
        print("               [--reused=<复用资产|不适用:原因>] [--deviation=<偏差>]")
        print("               [--produced=<提交/技能补丁/待改进项>] [--session=<id>] [--query=<query_id>]")
        return 2
    reused = kv.get("reused", "") or ""
    if kv.get("query"):
        # 采纳强信号（review Q3）：reused 指向资产 ID 时关联到当时的检索
        _log_event({"event": "adopt", "query_id": kv["query"], "reused": reused})
    elif reused:
        # P2-1 自动关联：无显式 --query 时，从会话状态全部记录里找 asset_id/asset_path
        # 与 reused 有包含关系的最近一条 → 自动补 adopt 事件；对不上则静默（不打日志）。
        st = _session_state_load()
        for rec in sorted((r for r in st.values() if isinstance(r, dict)),
                          key=lambda r: str(r.get("ts", "")), reverse=True):
            aid = str(rec.get("asset_id", ""))
            ap = str(rec.get("asset_path", ""))
            if ((aid and aid in reused)
                    or (ap and len(ap) >= 4 and len(reused) >= 4 and (ap in reused or reused in ap))):
                ev = {"event": "adopt", "query_id": rec.get("query_id", ""),
                      "asset": aid, "reused": reused}
                if rec.get("query_hash"):
                    ev["query_hash"] = rec["query_hash"]
                _log_event(ev)
                break
    rec = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "session": kv.get("session", ""),
        "task": kv.get("task", ""),
        "evidence": kv.get("evidence", ""),
        "reused": kv.get("reused", ""),
        "deviation": kv.get("deviation", ""),
        "produced": kv.get("produced", ""),
        "verdict": kv.get("verdict") or ("有产出" if kv.get("produced") else "无需改变"),
    }
    RETRO.parent.mkdir(parents=True, exist_ok=True)
    with RETRO.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"✓ retro 已记录（{rec['verdict']}）→ _meta/retro/retro.jsonl")
    return 0


def cmd_retro_stats(argv):
    """周查视图：记录数、有产出率、重复偏差（同一 deviation 文本 ≥2 次）。

    重复发生率比沉淀率更能证明闭环有效（reviewer 评审）——同类偏差反复出现
    说明该条一直没被资产化。W1-9（2026-11-02）：重复 ≥2 次且待改进中无同文条目
    → 自动追加待改进一行（类别=重复偏差），闭环从"看见"升级为"挂账"。"""
    if not RETRO.exists():
        print("暂无 retro 记录（收尾时 record-retro 一条）")
        return 0
    recs = []
    for l in RETRO.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            recs.append(json.loads(l))
        except ValueError:
            continue
    H_in = _ingesth()
    rep_items = H_in.aggregate_retro_proofs(recs)
    dev_total = sum(1 for r in recs if (r.get("deviation") or "").strip() and r.get("deviation") not in ("none", "无"))
    produced = sum(1 for r in recs if r.get("verdict") == "有产出")
    rep_occ = sum(it["count"] for it in rep_items)
    print(f"retro 记录 {len(recs)} 条；有产出 {produced} 条；偏差 {dev_total} 条中重复 {rep_occ} 条"
          f"（重复发生率 {rep_occ}/{dev_total}——同类偏差反复出现=该条一直没被资产化）：")
    for it in rep_items[:10]:
        print(f"  ×{it['count']}  {it['deviation']}  [证据:{','.join(it['proofs'][-3:])}]")
    qtext = DEBTS.read_text(encoding="utf-8", errors="ignore") if DEBTS.exists() else ""
    new_qtext, converted, promos = H_in.merge_retro_debts(qtext, datetime.now().strftime("%Y-%m-%d"), rep_items[:10])
    if converted:
        H_in.atomic_write_text(DEBTS, new_qtext)
        print(f"  → W1-9 证据链同步：{converted} 条已更新/追加至 _meta/待改进.md（含 proof count）")
    for it in promos:
        print(f"  [晋升建议] 偏离「{it['deviation']}」已累计 {it['count']} 次证据 ({','.join(it['proofs'][-3:])})，建议升格为 pre-commit 检查项或 SKILL.md 规则")
    if not rep_items:
        print("  （暂无重复）")
    if len(recs) < 20:
        print("  ! 样本不足 20 条——重复发生率仅作方向参考（W1 周报阈值纪律）")
    return 0


DEBTS = LIB / "_meta" / "待改进.md"
DEBT_RED_DAYS = 7      # W1-2 两级老化：>7 天标红升级
DEBT_CAMP_DAYS = 14    # >14 天战役科目候选（需 owner/期限）


def cmd_debts(argv):
    """待改进队列视图（W1-2）。--aging 按条目日期分级：>7 天红（应进战役或今日处置）、
    >14 天战役科目候选；并动态合并库外暂存区（STAGING）超龄隔离安全债。"""
    import re
    from datetime import date as _d
    today = _d.today()
    aging = "--aging" in argv
    rows, red, campaign, stg_aged = [], 0, 0, 0
    if DEBTS.exists():
        for l in DEBTS.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"^- (\d{4}-\d{2}-\d{2}) \|", l)
            if not (m and l.rstrip().endswith("open")):
                continue
            d = _d(*(int(x) for x in m.group(1).split("-")))
            age = (today - d).days
            rows.append((age, l))
            if age > DEBT_CAMP_DAYS:
                campaign += 1
            elif age > DEBT_RED_DAYS:
                red += 1
    if STAGING.is_dir():
        for mp in sorted(STAGING.rglob("*.meta.json")):
            try:
                md = json.loads(mp.read_text(encoding="utf-8"))
                cap = str(md.get("captured", ""))[:10]
                d = _d(*(int(x) for x in cap.split("-")))
                age = (today - d).days
            except (OSError, ValueError, TypeError):
                continue
            if age > DEBT_RED_DAYS:
                stg_aged += 1
                flags = ",".join(md.get("flags") or [])[:40]
                line = f"- {cap} | 隔离安全债 | {mp.name[:-10]}（命中:{flags or '待核'}）| 处置:人工核查或清理 | open"
                rows.append((age, line))
                if age > DEBT_CAMP_DAYS:
                    campaign += 1
                else:
                    red += 1
    if not rows and not DEBTS.exists():
        print("待改进.md 不存在")
        return 0
    stg_note = f"，含隔离超龄 {stg_aged} 件" if stg_aged else ""
    print(f"待改进 open {len(rows)} 条" + (f"（--aging 分级：红 {red} 条 >{DEBT_RED_DAYS}天，"
          f"战役候选 {campaign} 条 >{DEBT_CAMP_DAYS}天{stg_note}）" if aging else f"{stg_note}（加 --aging 看老化分级）"))
    if aging:
        for age, l in sorted(rows, key=lambda x: -x[0]):
            if age > DEBT_CAMP_DAYS:
                tag = f"🔴{age}天→战役科目候选（需 owner/期限）"
            elif age > DEBT_RED_DAYS:
                tag = f"🟠{age}天→升级：应进战役科目或今日处置"
            else:
                continue
            print(f"  {tag}  {l[:110]}")
        if red + campaign == 0:
            print("  ✓ 无超龄项")
    return 0


def _triage_suggest(src, items):
    """来源簇机器建议（review Q5）。计算体在 hub/ingest.triage_suggest。"""
    return _ingesth().triage_suggest(src, items)


def cmd_inbox_triage(argv):
    """来源簇候选卡（review Q5 最小版）：把 inbox 从"逐件通读"降为"按簇裁决"。

    产出 _meta/inbox分诊_<日期>.md：每簇=同 source 的全部捕获（版本族自然归并），
    按件数降序，附机器建议（合并/新资产/仅归档/弃用由人最终拍板）。"""
    from collections import Counter
    day = datetime.now().strftime("%Y-%m-%d")
    if "--redact" in argv:
        n_res = _ingesth().rescue_staging(STAGING, INBOX, LIB / "_meta" / "gate_check.py", day)
        print(f"脱敏放行：{n_res} 件从隔离区救回 inbox（<REDACTED> 脱敏并通过二次预扫）")
    groups = {}
    for m in (INBOX.rglob("*.meta.json") if INBOX.exists() else []):
        try:
            d = json.loads(m.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        groups.setdefault(d.get("source", "?"), []).append((m, d))
    rows = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    total = sum(len(v) for _, v in rows)
    lines = [f"# inbox 分诊报告（{day}）", "",
             f"共 **{total} 件 / {len(rows)} 簇**——按簇裁决，不逐件通读（review 二轮 Q5）。",
             "裁决选项：合并进现有资产 / 新资产候选 / 仅归档 / 弃用；裁决后本报告留档为依据。", ""]
    for src, items in rows:
        latest = max(items, key=lambda x: x[1].get("captured", ""))
        statuses = dict(Counter(d.get("status", "?") for _, d in items))
        versions = max(int(d.get("versions", 1)) for _, d in items)
        lines.append(f"## {len(items)}件｜{Path(src).name}｜建议：{_triage_suggest(src, items)}")
        lines.append(f"- 源：`{src}`")
        lines.append(f"- 状态：{statuses}；代表件：`{latest[0].relative_to(LIB)}`；"
                     f"最新捕获 {latest[1].get('captured', '?')}；理由 {latest[1].get('reason', '?')}")
        if versions > 1:
            lines.append(f"- 版本链：代表件已合并 {versions} 个版本（止血后新版本原地更新）")
        lines.append("- 裁决：[ ]合并进现有资产：____ [ ]新资产候选 [ ]仅归档 [ ]弃用"
                     "　（人工按簇拍板+抽样复核；裁决后改 [x] 并留本报告为据）")
        lines.append("")
    # W1-3：周增 SLA 与清欠 KPI（v1.1 扩：存量口径）
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    wk_new = sum(1 for _, items in rows for _, d in items
                 if str(d.get("captured", "")) >= week_ago)
    lines.append(f"## SLA / KPI（W1-3）")
    lines.append(f"- 近 7 天新增 {wk_new} 件" + ("　⚠ **>50 触发分诊战役提醒**" if wk_new > 50 else ""))
    lines.append(f"- 存量清欠 KPI：每周至少裁决 **3 簇 / 20 件**（当前存量 {total} 件 / {len(rows)} 簇，"
                 f"W5 周报跟踪趋势）")
    out = LIB / "_meta" / f"inbox分诊_{day}.md"
    out.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    quar_n = sum(1 for _ in STAGING.rglob("*.meta.json")) if STAGING.exists() else 0
    print(f"分诊报告已生成：{out.relative_to(LIB)}（{total} 件 → {len(rows)} 簇"
          + (f"；另有隔离待审 {quar_n} 件在库外" if quar_n else "") + "）")
    for src, items in rows[:5]:
        print(f"  最大簇：{len(items)}件 {Path(src).name}")
    return 0


def cmd_stats(argv):
    data, stale = load_index()
    by = {}
    for a in data["assets"]:
        by[a["kind"]] = by.get(a["kind"], 0) + 1
    print(f"资产 {len(data['assets'])} 条：" + "，".join(f"{k} {v}" for k, v in sorted(by.items()))
          + f"；索引{'过期' if stale else '最新'}")
    return 0


def cmd_weekly_report(argv):
    """周报聚合（W1-8）：find-stats + retro-stats + inbox 周吞吐/KPI + debts --aging 一键出。

    消"周清欠靠手工拼四个命令"；retro <20 条自动提示样本不足。"""
    import io
    from contextlib import redirect_stdout
    sections = [("检索注入周报（find-stats）", cmd_find_stats, []),
                ("复盘周报（retro-stats，W1-9 自动转化生效）", cmd_retro_stats, []),
                ("债务老化（debts --aging，W1-2 两级）", cmd_debts, ["--aging"])]
    for title, fn, args in sections:
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(args)
        print(f"\n===== {title} =====")
        print(buf.getvalue().rstrip())
    # inbox 周吞吐（与 inbox-triage 同口径，不出全量报告）
    if INBOX.exists():
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
        def _safe_captured(m):
            try:
                return str(json.loads(m.read_text(encoding="utf-8")).get("captured", ""))
            except (OSError, ValueError):
                return ""
        metas = list(INBOX.rglob("*.meta.json"))
        wk = sum(1 for m in metas if _safe_captured(m) >= week_ago)
        total = len(metas)
        print(f"\n===== inbox 周吞吐（W1-3 SLA） =====")
        print(f"近 7 天新增 {wk} 件" + ("　⚠ >50 触发分诊战役" if wk > 50 else ""))
        print(f"存量 {total} 件；KPI：每周裁决 ≥3 簇/20 件（inbox-triage 出带勾选栏的裁决模板）")
    try:
        cmd_ui(["--no-open"])
    except Exception:
        pass
    return 0


_EXTRA_HOST_SKILL_DIRS = (
    (".cursor", "skills"),
    (".codeium/windsurf", "skills"),
    (".codex", "skills"),
    (".cline", "skills"),
)


def _posix_link(link: Path, target: Path):
    """POSIX 下的幂等符号链接装配（冲突保护：不静默覆盖用户自建非托管目录）。"""
    want = target.resolve()
    if link.is_symlink():
        if link.resolve() == want:
            return
        link.unlink()
    elif link.exists():
        print(f"! 跳过已占用的非托管技能目录: {link}", file=sys.stderr)
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(want)


def _bridge_extra_hosts(skills_target: Path):
    """仅当用户机已装对应宿主根目录（如 ~/.cursor）时，零感知挂载技能目录；未装则跳过。"""
    home = Path.home()
    for root_rel, sub_rel in _EXTRA_HOST_SKILL_DIRS:
        host_root = home / root_rel
        if not host_root.is_dir():
            continue
        link = host_root / sub_rel
        want = skills_target.resolve()
        if link.is_symlink() or (os.name == "nt" and link.exists() and link.resolve() == want):
            if link.resolve() == want:
                continue
        if link.exists():
            if not link.is_symlink():
                print(f"! 跳过已占用的非托管技能目录: {link}", file=sys.stderr)
                continue
            link.unlink()
        link.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(want)],
                           capture_output=True, check=False)
        else:
            link.symlink_to(want)


def _ensure_asset_shim():
    """确保 ~/.local/bin/asset(.cmd) 垫片指向当前库。"""
    bindir = Path.home() / ".local" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    cli_posix = str(LIB / "_meta" / "asset_cli.py").replace("\\", "/")
    sh_p = bindir / "asset"
    sh_body = f'#!/bin/sh\nexec "{sys.executable}" "{cli_posix}" "$@"\n'
    if not sh_p.exists() or cli_posix not in sh_p.read_text(encoding="utf-8", errors="ignore"):
        sh_p.write_text(sh_body, encoding="utf-8", newline="\n")
        if os.name != "nt":
            os.chmod(sh_p, 0o755)
    if os.name == "nt":
        import locale
        enc = locale.getpreferredencoding(False) or "utf-8"
        cmd_p = bindir / "asset.cmd"
        cmd_body = f'@echo off\r\n"{sys.executable}" "{LIB / "_meta" / "asset_cli.py"}" %*\r\n'
        if not cmd_p.exists() or str(LIB) not in cmd_p.read_text(encoding=enc, errors="ignore"):
            cmd_p.write_text(cmd_body, encoding=enc, errors="replace")


def cmd_init(argv):
    """统一初始化入口（asset init）：装配技能链接 + 宿主钩子 + 垫片 + 重建索引 + 快速体检。"""
    dry = "--dry-run" in argv or "--verify" in argv
    rc = 0
    ps1 = LIB / "install.ps1"
    if os.name == "nt" and ps1.is_file():
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)]
        if dry:
            cmd.append("-Verify")
        rc = max(rc, subprocess.run(cmd, cwd=str(LIB)).returncode)
    elif not dry:
        for d in sorted(SKILLS.iterdir()):
            if d.is_dir() and d.name != "_template" and (d / "SKILL.md").is_file():
                _posix_link(Path.home() / ".agents" / "skills" / d.name, d)
                _posix_link(LIB.parent / ".agents" / "skills" / d.name, d)
        _posix_link(LIB / ".agents" / "skills", SKILLS)
        _posix_link(Path.home() / ".gemini" / "config" / "skills", SKILLS)
        if (Path.home() / ".claude").exists():
            _posix_link(Path.home() / ".claude" / "skills", Path.home() / ".agents" / "skills")
        ghooks = LIB / ".git" / "hooks"
        if ghooks.is_dir():
            for name in ("pre-commit", "pre-push"):
                hp = ghooks / name
                hp.write_text(f'#!/bin/bash\nROOT=$(git rev-parse --show-toplevel) || exit 0\nexec bash "$ROOT/_meta/hooks/{name}" "$@"\n', encoding="utf-8", newline="\n")
                os.chmod(hp, 0o755)
        rc = max(rc, subprocess.run([sys.executable, str(LIB / "_meta" / "install_hooks.py")], cwd=str(LIB)).returncode)
    if not dry:
        _bridge_extra_hosts(Path.home() / ".agents" / "skills" if (Path.home() / ".agents" / "skills").exists() else SKILLS)
        _ensure_asset_shim()
        cmd_index([])
    if "--no-doctor" not in argv:
        rc = max(rc, cmd_doctor(["--quick"]))
    return rc


def cmd_close(argv):
    """一键原子收尾入口（asset close → session_close.py）：透传 --retro/--quick/--session 等。"""
    cmd = [sys.executable, str(LIB / "_meta" / "session_close.py")] + list(argv)
    return subprocess.run(cmd, cwd=str(LIB)).returncode


def cmd_task(argv):
    """多代理任务胶囊子命令（asset task → hub/tasks.py）：new/claim/handoff/done/board/show/check-write/session-ctx/sync。"""
    return _taskh().cmd_task(argv, LIB)


def cmd_ui(argv):
    """白盒仪表盘入口（asset ui / dashboard）：汇总全库资产、收件箱、效果与任务胶囊生成单页 HTML。"""
    idx_data, _ = load_index()
    return _dash().cmd_ui(
        argv, lib_root=LIB, index_data=idx_data,
        out_default=LOG_DIR / "dashboard.html", find_log=FIND_LOG,
        inbox_dir=INBOX, staging_dir=STAGING,
    )


COMMANDS = {"find": cmd_find, "show": cmd_show, "index": cmd_index, "doctor": cmd_doctor,
            "init": cmd_init, "close": cmd_close, "task": cmd_task,
            "ui": cmd_ui, "dashboard": cmd_ui,
            "digest": cmd_digest, "gates": cmd_gates, "stats": cmd_stats,
            "ingest": cmd_ingest, "inbox": cmd_inbox, "inbox-triage": cmd_inbox_triage,
            "inject-candidate": cmd_inject_candidate, "find-stats": cmd_find_stats,
            "log-open": cmd_log_open,
            "record-retro": cmd_record_retro, "retro-stats": cmd_retro_stats,
            "debts": cmd_debts, "weekly-report": cmd_weekly_report}


def main():
    for _s in (sys.stdout, sys.stderr):
        hasattr(_s, "reconfigure") and _s.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0 if len(sys.argv) > 1 else 2
    cmd = COMMANDS.get(sys.argv[1])
    if not cmd:
        print(f"未知命令：{sys.argv[1]}；可用：{', '.join(COMMANDS)}")
        return 2
    return cmd(sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
