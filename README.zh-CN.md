# obsidian2date

[English](README.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Español](README.es.md) | [Português (Brasil)](README.pt-BR.md) | [日本語](README.ja.md) | 简体中文

**研究任意近期时间窗口，把有用的部分留在 Obsidian。**

[![License: MIT](https://img.shields.io/badge/license-MIT-0f766e.svg)](LICENSE)
[![Tests](https://github.com/pauleschwarz/obsidian2date/actions/workflows/validate.yml/badge.svg)](https://github.com/pauleschwarz/obsidian2date/actions/workflows/validate.yml)

`obsidian2date` 研究人们在 Reddit、X、YouTube、HN、GitHub、Polymarket 和
全网范围内关于某个话题的真实言论 —— 覆盖你指定的任意时间窗口（上周、最近
7 天、最近 90 天；30 天只是默认值）—— 并把每次运行转化为持久、互相关联的
Obsidian 笔记。

每次运行产出：

- 有来源支撑的 **运行笔记**
- 精炼的 **简报**
- 指向相关运行的 `[[双链]]`
- 更新后的 **索引** 和 **仪表盘**

无追踪。MIT。
[last30days-skill](https://github.com/mvanhorn/last30days-skill) 的公开
fork；上游研究引擎保持可合并。需要 Python 3.12+ 和一个 Obsidian
仓库（vault）；数据源和 API 密钥可选 —— 详见
[CONFIGURATION.md](CONFIGURATION.md)。

## 以斜杠命令使用（主路径）

`obsidian2date` 是一个 Agent Skill：安装本仓库一次，之后在智能体中直接输入
`/obsidian2date <主题>` 即可。技能会运行研究引擎、解析你的仓库、写入笔记并
汇报路径。无需记忆任何旗标 —— 在请求中说"上周"或"最近 90 天"，技能会把它
翻译成正确的引擎参数。

| 宿主 | 安装 | 之后 |
| --- | --- | --- |
| Claude Code | `npx skills add pauleschwarz/obsidian2date -g -y`（或将本仓库作为 `.claude-plugin` 添加） | `/obsidian2date <主题>` |
| Codex | 仓库自带 `.codex-plugin/plugin.json` | `/obsidian2date <主题>` |
| Grok | `grok plugin marketplace add pauleschwarz/obsidian2date` | `/obsidian2date <主题>` |
| Gemini CLI | 仓库自带 `gemini-extension.json` | `/obsidian2date <主题>` |
| OpenClaw / agents.md 宿主 | 仓库自带 `.agents/` 清单 | `/obsidian2date <主题>` |
| pi / 任何支持技能的智能体 | 将 `skills/obsidian2date/` 软链接或复制到智能体的技能目录 | `/obsidian2date <主题>` |

技能在每次运行时做什么（见
[`skills/obsidian2date/SKILL.md`](skills/obsidian2date/SKILL.md) —— 模型
读取的规范运行时说明）：

1. 解析你的仓库（询问一次，会话内记住）
2. 从请求中推导时间窗口（默认 30 天）
3. 以 `--emit=obsidian` 运行研究引擎
4. 诚实汇报简报路径、运行笔记路径，以及部分或不可用的数据源

## 快速开始（CLI 兜底）

用于脚本、cron 或开发期引擎测试时，直接调用 CLI。这是兜底路径，不是主路径
—— 上面的斜杠命令才是产品。

```bash
git clone https://github.com/pauleschwarz/obsidian2date.git
cd obsidian2date

python3 skills/last30days/scripts/last30days.py \
  "local LLM agent frameworks" \
  --emit=obsidian \
  --obsidian-vault /path/to/your/vault
```

或者一次性配置仓库：

```bash
export OBSIDIAN2DATE_VAULT=/path/to/your/vault
python3 skills/last30days/scripts/last30days.py "topic" --emit=obsidian
```

### 时间窗口

`30` 天只是默认值。想查多久的都行：

```bash
python3 skills/last30days/scripts/last30days.py "AI video tools" --emit=obsidian --days 7    # 上周
python3 skills/last30days/scripts/last30days.py "rust async runtimes" --emit=obsidian --days 90  # 季度扫描
python3 skills/last30days/scripts/last30days.py "election odds" --emit=obsidian --days 14 --as-of 2026-08-15
```

在斜杠命令里直接说："研究 AI video tools 最近 7 天的动态"。

### 仓库解析

导出目标按以下顺序解析：

1. `--obsidian-vault PATH`（显式指定且不存在的路径会为导出而创建）
2. `OBSIDIAN2DATE_VAULT`
3. `LAST30DAYS_OBSIDIAN_VAULT`
4. 已存在的 `~/Desktop/brain-paul`

环境变量和桌面候选必须已经是目录。环境变量存在但为空或只含空白时，会刻意
禁用所有隐式兜底。如果都无法解析，命令会停止并输出：

```text
No Obsidian vault found. Pass --obsidian-vault or set OBSIDIAN2DATE_VAULT.
```

在 `.env` 文件中使用 `~/...` 或绝对路径；`$HOME` 在其中不会被展开。已有笔记
永远不会被覆盖；文件名冲突会追加数字后缀。

## 写入什么

仓库根目录下的默认布局：

```text
90_Quellen/obsidian2date/
  runs/YYYY-MM-DD-<slug>.md
  briefings/YYYY-MM-DD-<slug>-briefing.md
  Index.md
  Dashboard.md
```

笔记绝不覆盖。同日冲突追加数字后缀。检测到词元重叠时，相关历史运行会通过
Obsidian 的 `[[双链]]` 关联起来。

## 数据源与密钥

与上游同一底线：

- **默认免密钥：** Reddit、Hacker News、Polymarket、GitHub、Web
- **可选：** X（浏览器 cookie / 后端）、YouTube（`yt-dlp`）、TikTok/IG
  （ScrapeCreators），以及其他付费/选择性开启的后端

完整矩阵与密钥配置见
[`CONFIGURATION.md`](CONFIGURATION.md)。

## 安全诊断

研究开始前运行仅权限检查：

```text
$ python3 skills/last30days/scripts/last30days.py --preflight
last30days preflight
Status: Ready to research with safe defaults.
...
Local writes:
- none planned
```

`--preflight` 是安全的：它**不读取 cookie、不写文件、不运行研究**。排查数据
源或已安装后端时，请改用健康检查：

```bash
python3 skills/last30days/scripts/last30days.py doctor
```

## 上游模式依然可用

```bash
# 原始的紧凑综合输出
python3 skills/last30days/scripts/last30days.py "topic" --emit=compact

# 智能体 JSON
python3 skills/last30days/scripts/last30days.py "topic" --emit=json

# 生产简报
python3 skills/last30days/scripts/last30days.py "topic" --emit=brief
```

## 与上游的关系

| 事项 | 策略 |
| --- | --- |
| 研究引擎 | 与 `upstream/main` 保持可合并 |
| Obsidian 导出 | 附加模块：`lib/obsidian_export.py` |
| 品牌 / 技能 | `obsidian2date` |
| 许可证 | MIT；保留上游版权声明 |

```bash
git remote add upstream https://github.com/mvanhorn/last30days-skill.git
git fetch upstream
git merge upstream/main
```

## 致谢

- 上游研究引擎：[Matt Van Horn / last30days](https://github.com/mvanhorn/last30days-skill)
- Obsidian 导出路径 + 公开 fork 打包：[pauleschwarz](https://github.com/pauleschwarz)

## 许可证

MIT。见 [LICENSE](LICENSE)。
