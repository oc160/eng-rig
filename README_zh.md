# EngineerRig (`eng-rig`) —— 面向传统工程场景的 AI 工具箱与防错脚手架

> [English](README.md) | **中文说明** · **开源协议：[MIT License](LICENSE)**

**开源协议：MIT · 100% Python 标准库 · 离线内网直接可用（Windows / Linux）· 零数据库 · 零后台进程 · `<10ms` 工具直达 · 强制校核交差**

**EngineerRig** 专为**水利、土木、建筑、机械、测绘及设计院**等传统工程一线使用 AI 编程助手设计。它用本地 Git 仓库管理你调通的 CAD 改图脚本、水力/结构计算工具、Word/Excel 报告批处理与工作规范（`SKILL.md`），让 **Claude Code、Antigravity、Codex、Cursor、Windsurf** 直接复用现成可靠工具、写完安全回收不泄密、并行改文件不互相覆盖；也可与 **Mem0 / MemoraX** 等对话记忆工具配合使用。

> **Claude Code 安全设计**：所有自动注入模型上下文的钩子输出、技能文件与智能体规范均已转为纯英文且无私有客户端标识，可安心用于 Claude Code。

---

## 一、 项目针对解决的工程问题

| 传统工程现场痛点 | EngineerRig 怎么解决 |
| :--- | :--- |
| **1. 调通的计算/改图脚本换个项目就忘，AI 每次现场重写还算错**<br>花了半天让 AI 调好一套 DXF 断面重画、工程计算校核或报告排版脚本；开新会话或接新项目时 AI 又从零瞎编，公式算错、图层改乱、原版面被毁。 | **工具带卡片归档，`<10ms` 自动把现成脚本递给 AI**<br>脚本头部强制带 8 行“资产卡”（用途、来源、验证命令、依赖）。下次提需求时，钩子在 `<10ms` 内将 verified 脚本路径递给 AI（说“不要用某工具”能自动排除，同时给出 `related` 同类思路），让 AI 直接调用你验证过的工具。 |
| **2. 工程成果容错率为零，怕 AI 乱改无关图纸/报告、没校核就交差**<br>图纸、计算书与送审报告需要签名负责，不能靠 AI 嘴上说“改好了”；多窗口同时处理不同章节或图纸时极易互相覆盖。 | **锁死可写范围 + 长对话防失忆 + 校核命令不过不结项**<br>用 `.tasks/<id>.md` 单文件任务锁死只准修改的目录（`scope_globs`），AI 写错路径当场拦截（`deny`）；压缩上下文后及每 12 轮自动提醒任务边界；结束任务必须跑通指定校核命令（`verify_cmd`，退出码为 `0` 才算完成），提交前还会拦截“说明写了支持参数却在代码里写死路径”的假通用脚本。 |
| **3. 项目做完工具散落一地，想攒团队库又怕泄漏业主工程信息或内网口令**<br>赶工期写的转坐标、批量改字、规范核查脚本散在各项目文件夹里；脚本中常夹带业主单位名、工程绝对路径或服务器口令，不敢让 AI 自动提交。 | **仓外先安检、干净才入收件箱、绝不擅自提交**<br>钩子自动跳过 `tmp`/`probe` 等调试垃圾与外部代码仓，把新脚本先复制到仓外扫描 UTF-8、GB18030 及 `.docx`/`.xlsx`/`.zip` 内部敏感词：命中敏感特征留在仓外隔离（超 7 天进超期提醒），合格才进 `_meta/inbox/` 并支持 `inbox-triage` 合并同一脚本的多个版本，**永不自动 `git commit`**。 |
| **4. 单位电脑多为受控 Windows / 离线内网，装不了 Docker 也禁连外网**<br>工程电脑主力是 Windows（常配合本地 CAD/Office 接口），常处物理隔离或受限内网，装不上 Docker、向量库和后台服务，也禁止插件后台传数据。 | **纯 Python 标准库 + AST 代码自检零外连**<br>零第三方包、零向量库、零后台守护进程，有 Python 3.10+ 和 Git 就能离线跑。随时运行 `python _meta/arch_metrics.py --check` 直接解析全部核心代码语法树（AST），实锤确认零第三方包、零未审计网络请求。 |

---

## 二、 30 秒快速上手

克隆仓库后，直接把下面这句话发给你的 AI 助手即可自动完成配置：

```text
请运行 python _meta/asset_cli.py init 完成技能链接与钩子配置，然后运行 python _meta/asset_cli.py find "clean markdown" --format=context 测试检索。
```

或在终端手动执行：

```bash
# 1. 一键初始化：软链技能目录、安装宿主与 Git 钩子、生成 asset 命令并建索引
python _meta/asset_cli.py init

# 2. 测试 <10ms 工具检索与否定句绕开
python _meta/asset_cli.py find "clean markdown" --format=context
python _meta/asset_cli.py inject-candidate --prompt="don't use clean_markdown, format headings"

# 3. 打开本地单文件 HTML 看板与清理收件箱多版本脚本
python _meta/asset_cli.py ui --no-open
python _meta/asset_cli.py inbox-triage

# 4. 创建带写入边界与校核命令的工程任务单
python _meta/asset_cli.py task new "Check channel sections" --scope="scripts/**" --verify="python -m pytest -q"

# 5. 运行 AST 零依赖/零外连检查、提交规则检查、路径检查与单元测试
python _meta/arch_metrics.py --check
python _meta/gate_check.py --root .
python _meta/check_paths.py
python -m pytest -q
```

---

## 三、 常用命令与智能体详细规范入口

| 命令 | 作用 |
| :--- | :--- |
| `asset init` | 自动软链各客户端技能目录、装配钩子、重建索引并做快速体检 |
| `asset find "<关键词>"` | `<10ms` 查找已验证脚本、操作规程与同类替代方案 |
| `asset task <new/claim/handoff/done/board/sync>` | 管理单文件任务边界、租约、校核命令与独立分支跨机同步 |
| `asset ui` | 生成零服务器的单文件本地 HTML 资产状态看板（`dashboard.html`） |
| `asset close --retro="任务\|复用\|偏离\|产出"` | 结束时记录总结并核验工作区、软链与远端同步状态 |

- **给智能体看的详细执行规范**（8 行资产卡格式、钩子信号、提交前七项检查、任务状态机与外部记忆并存规则）：**[AGENTS.md](AGENTS.md)** · **[BOOTSTRAP.md](BOOTSTRAP.md)**

---

## 四、 开源协议

本项目严格遵循 **[MIT License](LICENSE)** 开源协议：
- **允许范围**：个人工程师、高校科研团队、设计院及企业可免费使用、修改、内部私有化部署或集成到商业项目中；
- **唯一义务**：二次分发源码或衍生版本时保留原版权声明与 [LICENSE](LICENSE) 许可文本；
- **免责提示**：软件按“原样（AS IS）”提供，所有工程计算书、设计图纸与成果文件仍须严格按照相应工程行业规范履行专业校审把关责任。
