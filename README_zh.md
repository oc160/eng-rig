# EngineerRig (`eng-rig`) —— 面向 AI 编程助手的零依赖工程工具与技能管理框架

> [English](README.md) | **中文说明**

**100% Python 标准库 · 零外部数据库 · 零后台常驻进程 · `<10ms` 双语确定性检索 · AST 静态边界审计 · 基于 Git 的单文件多代理协作**

**EngineerRig** 用纯 Python 标准库和本地 Git 仓库管理工程计算脚本、CAD/Office 批处理工具与操作规程（`SKILL.md`）。专为传统工程与高约束开发环境（离线或受控内网 Windows/Linux、严格的图纸与报告格式要求、强制计算校核）打造，支持 **Claude Code、Antigravity、Codex、Cursor、Windsurf** 等客户端共用同一套本地工具库，并可与 **Mem0、MemoraX** 等对话记忆工具无缝配合（记忆工具管历史交谈背景，EngineerRig 管可执行工具、操作规程与代码提交校验）。

> **Claude Code 安全设计**：所有自动注入模型上下文的钩子输出、技能定义（`skills/*/SKILL.md`）与智能体规范文件（`AGENTS.md`）均为纯英文且不含任何国内模型或私有客户端标识，可直接安全用于 Claude Code。

---

## 一、 项目针对解决的工程问题

| 实际工程痛点 | EngineerRig 的实现手段与效果 |
| :--- | :--- |
| **1. 离线内网与 Windows 终端无法装重型服务**<br>工程单位电脑常处于受限内网，无法装 Docker、向量数据库或常驻后台服务。 | **纯 Python 标准库 + AST 静态审计**<br>仅需 Python 3.10+ 与 Git。运行 `python _meta/arch_metrics.py --check` 直接通过 AST（抽象语法树）静态扫描全部治理代码，确保零第三方包依赖、零未审计网络请求。 |
| **2. 跨会话、换客户端后重复从零写脚本**<br>调通的计算或批处理脚本在新会话里被遗忘，模型重新写代码、重复踩坑。 | **`<10ms` 确定性检索 + 否定句过滤 + 1 跳同类关联**<br>钩子对 `SKILL.md`、`INDEX.md` 与脚本头部 8 行资产卡做字段加权打分，自动剔除 `不要用 X` / `don't use X` 等否定片段；仅在高分命中时注入一行 `≤130 字` 的脚本路径与同类关联提示，未命中保持静默。 |
| **3. 自动提交易泄密，不自动收又全靠手整理**<br>工程脚本常含内部路径或参数，AI 自动 `git commit` 易泄密，人工归档又会积压。 | **仓外隔离预扫 + 收件箱分流**<br>写文件钩子自动跳过 `tmp`/`probe`/`test_` 临时脚本与外部常规 Git 项目，先在仓外隔离目录扫描 UTF-8、GB18030 及 `.docx`/`.xlsx`/`.zip` 内嵌敏感词：合格才入 `_meta/inbox/`，命中敏感特征留在仓外隔离（超 7 天进 `debts --aging`），永不自动 `git commit`。 |
| **4. 多代理并行互踩文件、长对话失忆、未校核即报完成**<br>多窗口同时改代码互相覆盖、改同一看板文件撞 Git 冲突、压缩上下文后忘掉边界。 | **`.tasks/<id>.md` 单文件任务 + `PreToolUse` 写拦截 + 强制校核**<br>每个任务独立单文件（`os.replace` 原子更新 + TTL 租约）；写文件前核对 `scope_globs` 越界即 `deny`；会话首轮、上下文压缩后及每 12 轮自动提醒任务边界；必须跑通 `verify_cmd` 返回退出码 `0` 才允许结项，并通过独立 `tasks` 分支 worktree 同步。 |

---

## 二、 30 秒快速上手

克隆仓库后，把下面这句话发给你的 AI 助手即可自动完成装配：

```text
请运行 python _meta/asset_cli.py init 完成本机技能链接与钩子配置，然后运行 python _meta/asset_cli.py find "clean markdown" --format=context 测试检索。
```

或在终端手动执行：

```bash
# 1. 一键初始化：创建技能软链接、配置宿主钩子与 Git 钩子、生成 asset 命令并构建索引
python _meta/asset_cli.py init

# 2. 测试毫秒级检索与否定语义过滤
python _meta/asset_cli.py find "clean markdown" --format=context
python _meta/asset_cli.py inject-candidate --prompt="don't use clean_markdown, format headings"

# 3. 生成本地单文件 HTML 状态看板与查看检索统计
python _meta/asset_cli.py ui --no-open
python _meta/asset_cli.py find-stats

# 4. 创建带路径写保护与退出码校核的任务胶囊
python _meta/asset_cli.py task new "Add section check script" --scope="scripts/**" --verify="python -m pytest -q"

# 5. 运行 AST 架构边界审计、提交前规则检查、路径检查与单元测试
python _meta/arch_metrics.py --check
python _meta/gate_check.py --root .
python _meta/check_paths.py
python -m pytest -q
```

---

## 三、 核心命令与详细规范入口

| 命令 | 作用 |
| :--- | :--- |
| `asset init` | 创建跨客户端技能软链接、安装宿主与 Git 钩子、重建索引并执行快速体检 |
| `asset find "<关键词>"` | `<10ms` 检索技能、场景表与带 8 行资产卡脚本（含 1 跳同类关联） |
| `asset task <new/claim/handoff/done/board/sync>` | 管理 `.tasks/<id>.md` 单文件任务、TTL 租约、路径写范围、退出码校核与独立分支同步 |
| `asset ui` | 生成零外部依赖的单文件本地 HTML 状态看板（`dashboard.html`） |
| `asset close --retro="任务\|复用\|偏离\|产出"` | 记录结构化总结、校验工作区与软链接状态、核对远端同步 |

- **给智能体阅读的完整执行规范**（8 行资产卡字段定义、钩子返回格式、提交前七项检查细则、任务状态机与外部记忆并存边界）见 **[AGENTS.md](AGENTS.md)** 与 **[BOOTSTRAP.md](BOOTSTRAP.md)**。
