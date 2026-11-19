# -*- coding: utf-8 -*-
# ── 资产卡 ────────────────────────────────────────────────
# 名称：dashboard.py —— 零基建单页白盒仪表盘（资产全景/收件箱/效果雷达/协作与宿主）
# 来源：2026-11-17 PromptHub 对比反思（解决“半黑盒运作”，0 常驻服务）
# 提取：2026-11-17
# Author: EngineerRig
# 验证方式：tests/test_dashboard.py（快照采集/脱敏隔离/XSS转义/单页HTML渲染）
# 重跑命令：python _meta/asset_cli.py ui --no-open
# 依赖：纯标准库；单向被 asset_cli 调用
# ──────────────────────────────────────────────────────────
"""只读单页白盒仪表盘生成器（纯标准库，零后端服务）。

数据来源：
  1. asset-index.json（或动态索引）+ 资产卡/SKILL 头部摘要预览
  2. _meta/inbox（待蒸馏捕获件）与库外隔离区（仅元数据+命中规则，绝不回显密文内容）
  3. _asset_logs/find.jsonl + _meta/retro/retro.jsonl + _meta/待改进.md
  4. .tasks/*.md 任务胶囊与本地 AI 宿主挂载状态
"""
import importlib.util
import json
import os
import sys
import webbrowser
from collections import Counter
from datetime import datetime
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent.parent


def _read_preview(path: Path, max_lines: int = 32, max_chars: int = 750) -> str:
    try:
        if not path.is_file():
            return ""
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[:max_lines]
        txt = "\n".join(lines).strip()
        return txt[:max_chars] + ("…" if len(txt) > max_chars else "")
    except OSError:
        return ""


def _collect_assets(lib_root: Path, index_data: dict) -> list:
    out = []
    for a in index_data.get("assets", []):
        rel = a.get("path", "")
        p = Path(rel) if Path(rel).is_absolute() else (lib_root / rel)
        preview = _read_preview(p) if a.get("kind") in ("skill", "script") else ""
        out.append({
            "id": a.get("id", ""),
            "kind": a.get("kind", ""),
            "name": a.get("name", ""),
            "summary": a.get("summary", ""),
            "triggers": a.get("triggers") or [],
            "anti_triggers": a.get("anti_triggers") or [],
            "related": a.get("related") or [],
            "path": rel,
            "visibility": a.get("visibility", "internal"),
            "preview": preview,
        })
    return out


def _collect_inbox(lib_root: Path, inbox_dir: Path, staging_dir: Path) -> dict:
    inbox_items, staging_items = [], []
    if inbox_dir.is_dir():
        for m in sorted(inbox_dir.rglob("*.meta.json"), reverse=True):
            try:
                d = json.loads(m.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            target = m.with_name(m.name[:-len(".meta.json")])
            try:
                rel = target.relative_to(lib_root).as_posix()
            except ValueError:
                rel = str(target)
            inbox_items.append({
                "name": target.name,
                "path": rel,
                "source": d.get("source", ""),
                "captured": d.get("captured", ""),
                "status": d.get("status", "待蒸馏"),
                "reason": d.get("reason", ""),
                "versions": int(d.get("versions", 1) or 1),
                "preview": _read_preview(target, 28, 700),
            })
    if staging_dir.is_dir():
        for m in sorted(staging_dir.rglob("*.meta.json"), reverse=True):
            try:
                d = json.loads(m.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            # 安全纪律：隔离区命中敏感规则，严禁嵌入原文内容
            staging_items.append({
                "name": m.name[:-len(".meta.json")],
                "source": d.get("source", ""),
                "captured": d.get("captured", ""),
                "flags": d.get("flags") or [],
                "status": d.get("status", "隔离待审"),
            })
    return {"inbox": inbox_items, "staging": staging_items}


def _collect_telemetry(lib_root: Path, find_log: Path) -> dict:
    searches = inj = zero = opens = adopts = 0
    top_ctr = Counter()
    if find_log.is_file():
        for ln in find_log.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not ln.strip():
                continue
            try:
                rec = json.loads(ln)
            except ValueError:
                continue
            ev = rec.get("event")
            if ev == "search":
                searches += 1
                cands = rec.get("candidates") or []
                if not cands:
                    zero += 1
                if rec.get("injected"):
                    inj += 1
                    if cands:
                        top_ctr[cands[0].get("id", "?")] += 1
            elif ev == "open":
                opens += 1
            elif ev == "adopt":
                adopts += 1

    retros = []
    rj = lib_root / "_meta" / "retro" / "retro.jsonl"
    if rj.is_file():
        for ln in rj.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not ln.strip():
                continue
            try:
                retros.append(json.loads(ln))
            except ValueError:
                continue
    debts_open = []
    debts_done = 0
    dp = lib_root / "_meta" / "待改进.md"
    if dp.is_file():
        for ln in dp.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = ln.strip()
            if not s.startswith("- "):
                continue
            if s.endswith("done"):
                debts_done += 1
            elif s.endswith("open"):
                debts_open.append(s[2:-4].strip(" |"))
    return {
        "searches": searches, "injected": inj, "zero": zero,
        "opens": opens, "adopts": adopts,
        "top_assets": [{"id": k, "count": v} for k, v in top_ctr.most_common(10)],
        "retros": retros[-20:][::-1],
        "debts_open": debts_open[::-1],
        "debts_done": debts_done,
    }


def _collect_tasks_and_hosts(lib_root: Path) -> dict:
    tasks = []
    tp = Path(__file__).with_name("tasks.py")
    if tp.is_file():
        spec = importlib.util.spec_from_file_location("_hub_tasks_dash", tp)
        tm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tm)
        tdir = tm.tasks_dir(lib_root)
        if tdir.is_dir():
            for p in sorted(tdir.glob("*.md")):
                t = tm.parse_task_file(p)
                if t.get("id"):
                    tasks.append({
                        "id": t["id"], "title": t.get("title", ""),
                        "status": t.get("status", "todo"),
                        "owner": t.get("owner", ""), "session": t.get("session", ""),
                        "lease_active": tm.lease_active(t),
                        "lease_until": t.get("lease_until", ""),
                        "scope_globs": t.get("scope_globs") or [],
                        "subtasks": t.get("subtasks") or [],
                        "handoff": t.get("handoff", ""),
                        "updated_at": t.get("updated_at", ""),
                    })

    home = Path.home()
    checks = [
        ("本仓 Antigravity", (lib_root / ".agents" / "skills").exists() and (lib_root / ".agents" / "hooks.json").is_file(), ".agents/skills + hooks.json"),
        ("全局 ~/.agents", (home / ".agents" / "skills").exists(), "~/.agents/skills"),
        ("Claude Code 钩子", (home / ".zcode" / "cli" / "config.json").is_file(), "~/.zcode/cli/config.json"),
        ("Claude Code", (home / ".claude" / "skills").exists(), "~/.claude/skills"),
        ("Cursor", (home / ".cursor" / "skills").exists(), "~/.cursor/skills"),
        ("Windsurf", (home / ".codeium" / "windsurf" / "skills").exists(), "~/.codeium/windsurf/skills"),
        ("Codex CLI", (home / ".codex" / "skills").exists(), "~/.codex/skills"),
        ("Cline", (home / ".cline" / "skills").exists(), "~/.cline/skills"),
    ]
    hosts = [{"name": n, "active": bool(ok), "target": tgt} for n, ok, tgt in checks]
    return {"tasks": tasks, "hosts": hosts}


def collect_snapshot(lib_root=None, index_data=None, find_log=None, inbox_dir=None, staging_dir=None) -> dict:
    """采集白盒仪表盘全量快照（纯标准库，只读无副作用）。"""
    root = Path(lib_root or LIB)
    idx = index_data or {"assets": []}
    flog = Path(find_log) if find_log else (root.parent / "_asset_logs" / "find.jsonl")
    ibx = Path(inbox_dir) if inbox_dir else (root / "_meta" / "inbox")
    stg = Path(staging_dir) if staging_dir else (root.parent / "_inbox_staging")
    assets = _collect_assets(root, idx)
    inbox_pack = _collect_inbox(root, ibx, stg)
    tele = _collect_telemetry(root, flog)
    th = _collect_tasks_and_hosts(root)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "repo": root.name,
        "assets": assets,
        "inbox": inbox_pack["inbox"],
        "staging": inbox_pack["staging"],
        "telemetry": tele,
        "tasks": th["tasks"],
        "hosts": th["hosts"],
    }


_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Asset Hub 白盒工作台</title>
<style>
:root{--bg:#f4f7fb;--card:#fff;--ink:#132238;--muted:#5b6b82;--line:#dce4f0;--accent:#1f5fa8;--ok:#15803d;--warn:#b45309;--bad:#b91c1c;--soft:#eaf2fc}
*{box-sizing:border-box}
body{margin:0;font:14px/1.55 "Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink)}
header{background:linear-gradient(120deg,#0f294a,#1f5fa8);color:#fff;padding:18px 28px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px}
header h1{margin:0;font-size:20px;font-weight:700}
header .sub{opacity:.85;font-size:12px}
.wrap{max-width:1320px;margin:0 auto;padding:18px 22px 48px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-bottom:18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kpi .lbl{font-size:12px;color:var(--muted)}
.kpi .val{font-size:24px;font-weight:700;color:var(--accent);margin:2px 0}
.kpi .sub{font-size:12px;color:var(--muted)}
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.tab{border:1px solid var(--line);background:var(--card);color:var(--ink);padding:8px 16px;border-radius:8px;cursor:pointer;font-weight:600}
.tab.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.panel{display:none}
.panel.active{display:block}
.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px;align-items:center}
input[type=search]{flex:1;min-width:240px;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:#fff;font-size:14px}
select{padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:#fff}
.split{display:grid;grid-template-columns:1.25fr 1fr;gap:14px}
@media(max-width:920px){.split{grid-template-columns:1fr}}
.box{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.box h3{margin:0;padding:11px 14px;border-bottom:1px solid var(--line);font-size:14px;background:#f9fbff}
.list{max-height:560px;overflow:auto}
.item{padding:10px 14px;border-bottom:1px solid #eef2f7;cursor:pointer}
.item:hover,.item.sel{background:var(--soft)}
.row{display:flex;justify-content:space-between;gap:8px;align-items:center}
.badge{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11px;font-weight:600;background:#e6eef9;color:var(--accent)}
.b-ok{background:#dcfce7;color:var(--ok)}
.b-warn{background:#fef3c7;color:var(--warn)}
.b-bad{background:#fee2e2;color:var(--bad)}
.detail{padding:14px;max-height:560px;overflow:auto}
pre{background:#0f172a;color:#e2e8f0;padding:10px 12px;border-radius:8px;overflow:auto;font-size:12px;white-space:pre-wrap;word-break:break-word}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0}
.chip{background:#eef3fa;border:1px solid var(--line);padding:2px 8px;border-radius:6px;font-size:12px}
.chip.anti{background:#fef2f2;border-color:#fecaca;color:var(--bad)}
.chip.rel{background:#ecfdf5;border-color:#a7f3d0;color:var(--ok);cursor:pointer}
.bar-row{display:grid;grid-template-columns:190px 1fr 48px;gap:10px;align-items:center;padding:6px 14px;font-size:12px}
.bar-track{background:#e9eff7;height:10px;border-radius:99px;overflow:hidden}
.bar-fill{background:var(--accent);height:100%}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.muted{color:var(--muted);font-size:12px}
</style>
</head>
<body>
<header>
  <div>
    <h1>Asset Hub 白盒工作台</h1>
    <div class="sub" id="meta-line"></div>
  </div>
  <div class="sub">纯标准库只读视图 · 零常驻进程</div>
</header>
<div class="wrap">
  <div class="kpis" id="kpis"></div>
  <div class="tabs">
    <button class="tab active" data-tab="assets">资产全景</button>
    <button class="tab" data-tab="inbox">收件箱与隔离</button>
    <button class="tab" data-tab="tele">效果雷达与复盘</button>
    <button class="tab" data-tab="collab">四跨任务与宿主</button>
  </div>
  <section class="panel active" id="p-assets">
    <div class="toolbar">
      <input id="q" type="search" placeholder="搜索名称 / 触发词 / 路径 / 同类关联…"/>
      <select id="kind"><option value="">全部类型</option><option value="skill">skill</option><option value="entry">entry</option><option value="script">script</option></select>
    </div>
    <div class="split">
      <div class="box"><h3 id="list-title">资产列表</h3><div class="list" id="asset-list"></div></div>
      <div class="box"><h3>详情与源码预览</h3><div class="detail" id="asset-detail"><div class="muted">点击左侧任意资产查看触发词、1 跳同类思路与源码头部。</div></div></div>
    </div>
  </section>
  <section class="panel" id="p-inbox"><div class="grid2" id="inbox-grid"></div></section>
  <section class="panel" id="p-tele"><div class="split" id="tele-grid"></div></section>
  <section class="panel" id="p-collab"><div class="grid2" id="collab-grid"></div></section>
</div>
<script id="hub-data" type="application/json">__DATA_JSON__</script>
<script>
const D=JSON.parse(document.getElementById("hub-data").textContent);
const el=(t,c,txt)=>{const n=document.createElement(t);if(c)n.className=c;if(txt!==undefined)n.textContent=txt;return n;};
document.getElementById("meta-line").textContent=`仓库：${D.repo} ｜ 快照生成：${D.generated_at}`;
const byKind=D.assets.reduce((m,a)=>(m[a.kind]=(m[a.kind]||0)+1,m),{});
const t=D.telemetry;
const activeHosts=D.hosts.filter(h=>h.active).length;
const activeTasks=D.tasks.filter(x=>x.lease_active).length;
const kpis=[
  ["资产总数",D.assets.length,`skill ${byKind.skill||0} · entry ${byKind.entry||0} · script ${byKind.script||0}`],
  ["检索注入率",t.searches?Math.round(t.injected*100/t.searches)+"%":"0%",`总 ${t.searches} 次 · 零结果 ${t.zero} · open ${t.opens}/adopt ${t.adopts}`],
  ["收件箱待审",D.inbox.length,`库外隔离 ${D.staging.length} 件（含敏感命中）`],
  ["活跃任务胶囊",activeTasks,`总胶囊 ${D.tasks.length} 个 · 待改进 open ${t.debts_open.length}`],
  ["宿主链路绿灯",`${activeHosts}/${D.hosts.length}`,"本机检测已挂载宿主"]
];
const kWrap=document.getElementById("kpis");
kpis.forEach(([l,v,s])=>{const c=el("div","kpi");c.append(el("div","lbl",l),el("div","val",String(v)),el("div","sub",s));kWrap.append(c);});

document.querySelectorAll(".tab").forEach(b=>b.onclick=()=>{
  document.querySelectorAll(".tab,.panel").forEach(x=>x.classList.remove("active"));
  b.classList.add("active");document.getElementById("p-"+b.dataset.tab).classList.add("active");
});

const byId=Object.fromEntries(D.assets.map(a=>[a.id,a]));
function showAsset(a){
  const box=document.getElementById("asset-detail");box.innerHTML="";
  box.append(el("h3","",a.name+" "),el("span","badge",a.id));
  box.append(el("p","muted","路径："+a.path+" ｜ 可见性："+a.visibility));
  box.append(el("p","",a.summary));
  if(a.triggers.length){box.append(el("div","muted","正向触发词"));const ch=el("div","chips");a.triggers.forEach(x=>ch.append(el("span","chip",x)));box.append(ch);}
  if(a.anti_triggers.length){box.append(el("div","muted","反向否决词"));const ch=el("div","chips");a.anti_triggers.forEach(x=>ch.append(el("span","chip anti",x)));box.append(ch);}
  if(a.related.length){box.append(el("div","muted","1 跳同类思路参考"));const ch=el("div","chips");a.related.forEach(id=>{const c=el("span","chip rel",id);c.onclick=()=>{if(byId[id])showAsset(byId[id]);};ch.append(c);});box.append(ch);}
  if(a.preview){box.append(el("div","muted","文件头部预览"),el("pre","",a.preview));}
}

function renderAssets(){
  const q=document.getElementById("q").value.trim().toLowerCase(),k=document.getElementById("kind").value;
  const rows=D.assets.filter(a=>(!k||a.kind===k)&&(!q||[a.id,a.name,a.summary,a.path,...a.triggers,...a.related].join(" ").toLowerCase().includes(q)));
  document.getElementById("list-title").textContent=`资产列表 (${rows.length})`;
  const list=document.getElementById("asset-list");list.innerHTML="";
  rows.slice(0,250).forEach(a=>{
    const it=el("div","item");const r=el("div","row");
    r.append(el("strong","",a.name),el("span","badge",a.kind));
    it.append(r,el("div","muted",a.summary+" · "+a.path));
    it.onclick=()=>{list.querySelectorAll(".item").forEach(x=>x.classList.remove("sel"));it.classList.add("sel");showAsset(a);};
    list.append(it);
  });
}
document.getElementById("q").oninput=renderAssets;document.getElementById("kind").onchange=renderAssets;renderAssets();

const ig=document.getElementById("inbox-grid");
const b1=el("div","box");b1.append(el("h3","",`收件箱待蒸馏 (${D.inbox.length})`));const l1=el("div","list");
(D.inbox.length?D.inbox:[{name:"收件箱为空",path:"可执行 asset inbox-triage 生成分诊报告",captured:"",status:"干净",reason:"",source:"",preview:""}]).forEach(x=>{
  const it=el("div","item");const r=el("div","row");r.append(el("strong","",x.name),el("span","badge b-ok",x.status));
  it.append(r,el("div","muted",`${x.captured} · 来源：${x.source||"-"}`));if(x.preview)it.append(el("pre","",x.preview));l1.append(it);
});b1.append(l1);
const b2=el("div","box");b2.append(el("h3","",`库外敏感隔离区 (${D.staging.length})`));const l2=el("div","list");
(D.staging.length?D.staging:[{name:"无隔离待审件",captured:"",flags:[],source:"",status:"安全"}]).forEach(x=>{
  const it=el("div","item");const r=el("div","row");r.append(el("strong","",x.name),el("span","badge b-bad",x.status));
  it.append(r,el("div","muted",`${x.captured} · 命中规则：${(x.flags||[]).join(", ")||"无"}（内容已脱敏隐藏）`));l2.append(it);
});b2.append(l2);ig.append(b1,b2);

const tg=document.getElementById("tele-grid");
const tb=el("div","box");tb.append(el("h3","","高频注入 Top 10 & 待改进队列"));
const maxC=Math.max(1,...t.top_assets.map(x=>x.count));
t.top_assets.forEach(x=>{const r=el("div","bar-row"),tr=el("div","bar-track"),fl=el("div","bar-fill");fl.style.width=(x.count*100/maxC)+"%";tr.append(fl);r.append(el("span","",x.id),tr,el("span","",String(x.count)));tb.append(r);});
tb.append(el("h3","",`待改进 Open (${t.debts_open.length}) / Done (${t.debts_done})`));
const dl=el("div","list");t.debts_open.slice(0,40).forEach(d=>dl.append(el("div","item",d)));tb.append(dl);
const rb=el("div","box");rb.append(el("h3","","最近复盘 (retro)"));const rl=el("div","list");
t.retros.forEach(r=>{const it=el("div","item");it.append(el("strong","",`${r.date||""} · ${r.task||""}`),el("div","muted",`复用：${r.reused||"无"} ｜ 产出：${r.produced||"无"}`));rl.append(it);});
rb.append(rl);tg.append(tb,rb);

const cg=document.getElementById("collab-grid");
const tbox=el("div","box");tbox.append(el("h3","",`任务胶囊 (${D.tasks.length})`));const tl=el("div","list");
(D.tasks.length?D.tasks:[{id:"无活跃任务",title:"运行 asset task new <标题> 创建",status:"idle",owner:"",lease_active:false,scope_globs:[],subtasks:[],handoff:""}]).forEach(x=>{
  const it=el("div","item"),r=el("div","row");
  r.append(el("strong","",`${x.id} ${x.title}`),el("span","badge "+(x.lease_active?"b-warn":"b-ok"),x.status));
  it.append(r,el("div","muted",`持有人：${x.owner||"-"} ｜ 边界：${(x.scope_globs||[]).join(", ")||"*"}`));
  if(x.handoff)it.append(el("div","muted","交接："+x.handoff));
  tl.append(it);
});tbox.append(tl);
const hbox=el("div","box");hbox.append(el("h3","","本机宿主适配矩阵"));const hl=el("div","list");
D.hosts.forEach(h=>{const it=el("div","item"),r=el("div","row");r.append(el("strong","",h.name),el("span","badge "+(h.active?"b-ok":""),h.active?"已挂载":"未检出"));it.append(r,el("div","muted",h.target));hl.append(it);});
hbox.append(hl);cg.append(tbox,hbox);
</script>
</body>
</html>"""


def render_html(snapshot: dict) -> str:
    """将快照渲染为自包含单文件 HTML（对 </ 转义，防脚本标签注入）。"""
    safe_json = json.dumps(snapshot, ensure_ascii=False).replace("</", "<\\/")
    return _HTML_TEMPLATE.replace("__DATA_JSON__", safe_json)


def cmd_ui(argv, lib_root=None, index_data=None, out_default=None, find_log=None, inbox_dir=None, staging_dir=None) -> int:
    """asset ui / dashboard 入口：生成白盒单页 HTML 并默认在浏览器打开。"""
    root = Path(lib_root or LIB)
    out_arg = next((a.split("=", 1)[1] for a in argv if a.startswith("--out=")), "")
    snap = collect_snapshot(root, index_data=index_data, find_log=find_log,
                            inbox_dir=inbox_dir, staging_dir=staging_dir)
    if "--json" in argv:
        print(json.dumps(snap, ensure_ascii=False, indent=2))
        return 0
    dest = Path(out_arg) if out_arg else Path(out_default or (root.parent / "_asset_logs" / "dashboard.html"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_html(snap), encoding="utf-8", newline="\n")
    print(f"✓ 白盒仪表盘已生成：{dest}")
    if "--no-open" not in argv and os.environ.get("ASSET_HUB_NO_OPEN") != "1":
        try:
            webbrowser.open(dest.resolve().as_uri())
        except Exception:
            pass
    return 0
