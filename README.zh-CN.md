# /last30days

[English](README.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Español](README.es.md) | [Português (Brasil)](README.pt-BR.md) | [日本語](README.ja.md) | 简体中文

<p align="center">
  <img src="media/pr-assets/last30days-ad.gif" width="720" alt="last30days——由 AI 智能体驱动、搜索真实用户而非编辑内容的搜索引擎" />
</p>

<p align="center">
  <a href="https://github.com/mvanhorn/last30days-skill">
    <img src="https://img.shields.io/badge/%231-Repository%20Of%20The%20Day-6f42c1?style=for-the-badge&logo=github&label=GITHUB%20TRENDING" alt="GitHub Trending 单日排名第一的仓库" />
  </a>
  <br/>
  <a href="https://trendshift.io/repositories/21997" target="_blank">
    <img src="https://trendshift.io/api/badge/repositories/21997" alt="mvanhorn/last30days-skill | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/>
  </a>
</p>

**一个由 AI 智能体驱动的搜索引擎：按赞同票、点赞和真金白银评分，而不是由编辑决定。**

本文档对应当前的 v3 流水线。运行时 Skill 规范位于 [skills/last30days/SKILL.md](skills/last30days/SKILL.md)，最新命令与配置行为以该文件为准。

**Claude Code（推荐——通过 marketplace 自动更新）：**

```
/plugin marketplace add mvanhorn/last30days-skill
/plugin install last30days
```

**Codex、Cursor、Copilot、Gemini CLI，或其他 50 多个支持 [Agent Skills](https://agentskills.io) 的宿主：**

```
npx skills add mvanhorn/last30days-skill -g
```

（`-g` 会安装到当前用户的全局环境，所有项目均可使用；去掉该参数则仅安装到当前项目。）

更多安装方式（claude.ai 网页版、OpenClaw、手动安装）见下方[安装](#安装)章节。

开箱即用。Reddit、Hacker News、Polymarket 和 GitHub 无需配置即可搜索。首次运行时，配置向导会在 30 秒内帮你解锁 X、YouTube、TikTok、arXiv、Techmeme 等更多来源。

---

Reddit 的赞同票、X 的点赞、YouTube 的完整字幕、TikTok 的互动数据，以及由真金白银和内幕信息支撑的 Polymarket 概率——每天都有数百万人用注意力和钱包投票。`/last30days` 会并行搜索这些平台，按照真实用户的参与度评分，再由 AI 智能体裁判综合成一份简报。

Google 聚合编辑选出的内容，`/last30days` 搜索真实的人。

你无法从别的单一搜索产品获得这些结果，因为没有哪个 AI 天生能访问所有平台。Google 搜不到 Reddit 评论和 X 帖子；ChatGPT 虽然与 Reddit 合作，却无法搜索 X 或 TikTok；Gemini 能访问 YouTube，却没有 Reddit；Claude 原生不具备这些能力。每个平台都是一座围墙花园，有自己的 API、令牌和认证机制。但只要接入你自己的密钥和浏览器会话，AI 智能体就能同时搜索所有平台、横向比较信号，并告诉你真正值得关注的内容。

这才是关键：不是再造一个更好的搜索引擎，而是让一个智能体把十几个彼此割裂的平台连接起来。

```
/last30days Peter Steinberger
```

假设你明天要和一个人开会。用 Google 搜他，你看到的可能还是 2023 年的 LinkedIn 页面；`/last30days` 告诉你的则是他这个月真正做了什么：加入 OpenAI 参与 Codex、反对 Anthropic 禁止第三方智能体、提交 23 个 PR 且合并率达到 85%、打造用于跨设备智能体控制的 “LobsterOS”，以及 r/ClaudeCode 上一场获得 569 个赞同票的争论——他究竟是英雄，还是“令人难以忍受”。这些信息散落在 X 帖子、Reddit 讨论、YouTube 字幕和 GitHub 提交中，Google 上根本没有。

## 为什么要做这个项目

最初，我做它是为了跟上 AI 的变化。这个领域每天都在变，而 Reddit 和 X 上的极客通常最先发现新东西。我需要更好的提示词，但模型训练数据总比社区已经摸索出的经验慢几个月。

后来，它变成了更大的东西。现在，销售通话前，我用它了解一家公司过去 30 天的真实动态；开会前，我用它读完对方最近的推文和播客字幕；去迪士尼世界前，我用它确认哪些项目停运、社区怎么看 Genie+；开始做任何产品前，我用它找出人们真正遇到的问题。

如果你要见一位 CEO，你读过他过去 30 天的所有推文和 YouTube 字幕吗？我读过。

## 由真实用户评分的信息源

| 来源 | 人们会告诉你什么 |
|------|------------------|
| **Reddit** | 未经过滤的真实看法。免费获取带实际赞同数的热门评论，无需 API 密钥；那些常被 Google 埋没的真实意见。 |
| **X / Twitter** | 犀利观点、专家长帖和突发事件的第一反应。最早知道，也最早争论。 |
| **YouTube** | 45 分钟的深度内容。搜索完整字幕，只提取真正值得引用的 5 句话。 |
| **TikTok** | 一个触达 360 万人的创作者观点——你永远不会在 Google 上搜到。 |
| **Instagram Reels** | 带口播字幕的影响者视角，反映视觉文化的信号。 |
| **Hacker News** | 开发者共识：825 分、899 条评论，技术从业者真正交锋的地方。 |
| **Polymarket** | 不是观点，而是由真金白银支撑的概率：专辑销量 96%，收购概率 4%。 |
| **GitHub** | 搜人时查看 PR 速度、按 Star 排名的热门仓库和发行说明；搜主题时查看 Issue 与 Discussion。 |
| **Digg** | 来自 Digg AI 1000 排行榜（约 1,000 个高信号 X 账号）的精选话题聚类，包含可追溯的行内引用，无需 X 认证。当 PATH 中存在 `digg-pp-cli` 时自动启用。 |
| **arXiv** | 热点背后的论文。免费查找时间窗口内的新研究，无需 API 密钥。当 PATH 中存在 `arxiv-pp-cli` 时自动启用（首次配置会安装）。 |
| **Techmeme** | 科技新闻的编辑视角，并按你设定的 30 天窗口筛选。免费且无需 API 密钥。当 PATH 中存在 `techmeme-pp-cli` 时自动启用（首次配置会安装）。 |
| **LinkedIn** | 职业领域的信号。搜索帖子和文章，其中文章被视为高价值信号。 |
| **StockTwits** | 交易者情绪。当主题是股票代码或加密货币时自动启用。 |
| **Threads** | 后 Twitter 时代的文字内容层，汇集创作者和品牌的讨论。 |
| **Pinterest** | 视觉发现：围绕产品和创意的 Pin、收藏与评论。 |
| **小红书（RED）** | 来自中国生活方式、产品和创作者的信号。当本机运行已登录的 x-mcp 浏览器插件或 `xiaohongshu-mcp` 服务时，通过 `--search xhs` 显式启用。 |
| **Bluesky** | 去中心化的社交内容层，搜索 Twitter 用户迁移后产生的 AT Protocol 帖子。 |
| **Perplexity** | 基于来源的 Sonar 综合结果、原始 Search API 数据和 Deep Research。 |
| **Web** | 编辑报道和博客对比。它只是众多信号之一，而不是唯一来源。 |

社区贡献者仍在不断加入更多平台。Truth Social 等垂直来源已经进入引擎，更多来源也在路上。

一条获得 1,500 个赞同票的 Reddit 帖子，信号强度高于一篇无人阅读的博客；一个拥有 360 万次观看的 TikTok，比新闻稿更能说明当下的文化热点；一个有 6.6 万美元成交量支撑的 Polymarket 概率，也比评论员的猜测更难反驳。

综合排序依据的是人们真正参与过的内容——看社会相关性，而不是 SEO 相关性。

## 大家实际上怎么用它

**开会之前。** `/last30days Peter Steinberger`——加入 OpenAI Codex 团队、反对 Anthropic 禁止第三方智能体、GitHub 上合并了 23 个 PR 且合并率达 85%、正在开发跨设备智能体控制系统 LobsterOS。r/ClaudeCode 上的一条评论说：“自从 OpenClaw 发布之后，大家就知道，只要你不是通过 API 运行它，迟早会被封。”（227 个赞同票）。这些不会出现在 LinkedIn 上。

**判断招聘信号。** `/last30days Listen Labs --hiring-signals`——把最新职位和招聘页面变成有引用依据的证据，从中判断公司是否正转向企业安全、客户成功、基础设施或产品扩张。报告只解释招聘看起来释放了什么信号，不会武断预测路线图一定会交付什么。

**在话题爆发前发现它。** 输入 `/last30days what's exploding in AI agents?`，Skill 会切换到发现模式：引擎扫描 Reddit 分类列表、Hacker News 的 front/best 故事、Digg AI 1000 信息流，以及认证后的 X；随后由你的智能体评审候选主题（命名、过滤垃圾、判断内容价值），并写出播客或 X 长文的切入角度；最终给出 5–10 个按增长速度排序的话题。每条结果都包含跨平台数据、势头标签，以及可直接运行的 `/last30days "<topic>"` 后续命令。

**突发事件发生时。** `/last30days Kanye West`——英国拒绝其签证，Wireless Festival 取消演出，赞助商纷纷离场；但《BULLY》首周登上 Billboard 第二名。Fantano 结束自己的 “Yay sabbatical” 回归评测（65.3 万次观看）；SoFi Homecoming 请来 Lauryn Hill 和 Travis Scott，共演出 44 首歌。Polymarket：“Kanye 还会再发推吗？”86% 认为会。共找到 23 个 Reddit 主题、17 个 YouTube 视频和 8.6 万次赞同。

**比较工具。** `/last30days OpenClaw vs Hermes vs Paperclip`——“它们并非竞品，而是处于不同层次。”OpenClaw 是执行层（GitHub 35.1 万 Star，已上线），Hermes 是会自我改进的大脑（3.1 万 Star），Paperclip 是组织结构图（4.9 万 Star）。Star 数来自 GitHub API 的实时数据，不是过期博客。报告会提供架构、记忆、安全性和适用场景的横向表格。正如 @IMJustinBrooke 所说：“OpenClaw = 小火龙，Hermes = 喷火龙。”

**理解世界。** `/last30days Iran vs USA`——战争进入第 38 天。特朗普要求伊朗在周二的最后期限前重新开放霍尔木兹海峡；两架美国战机被击落；油价涨至每桶 126 美元。IEA 称之为“全球石油市场史上最大规模的供应中断”。Polymarket 认为 12 月 31 日前停火的概率为 74%。共找到 27 条 X 帖子、10 个 YouTube 视频和 20 个预测市场。

**旅行之前。** `/last30days Universal Epic Universe`——扩建工程已经开工，“Project 680” 许可已提交；基础设施证实将有烟花表演，但官方尚未公布。Mine-Cart Madness 平均排队 148 分钟；年票仍未推出，当地居民对此不满；Stardust Racers 将停运翻修至 4 月 5 日。

**快速学习。** `/last30days Nano Banana Pro prompting`——JSON 结构化提示词正在取代标签堆砌；@pictsbyai 的嵌套格式能避免“概念串色”；以编辑为先的工作流优于反复重新生成。随后，它会严格依据社区验证有效的方法，为你写出一条可用于生产的提示词。

## 最近更新

自 5 月发布 v3.3 公告以来，截至 v3.11.1（2026 年 7 月），项目已在 15 个版本中合并 175 个 PR，其中 122 个来自 52 位社区贡献者。下面是主要变化。

### 正式支持 OpenAI Codex

`/last30days` 现在是带引导式配置的原生 Codex 插件——不是简单移植，而是一等公民。针对不同渲染器优化的引用格式，让 Codex 输出读起来像简报，而不是一团 URL（#694）。同一套引擎也运行在 Claude Code、Cursor、Copilot、Gemini CLI、Claude Desktop、OpenClaw 以及 50 多个 Agent Skills 宿主上。Codex 插件清单由 [@rfoust](https://github.com/rfoust) 贡献（#686），Codex 认证修复由 [@tmchow](https://github.com/tmchow) 贡献（#698）。

### arXiv、Techmeme 与 Digg——免费，无需 API 密钥

arXiv 提供热点背后的论文，Techmeme 提供科技新闻的编辑视角；二者均免费、无需密钥，首次配置会安装相应 CLI 并自动启用（#709）。Digg 的 AI 1000 话题聚类同样无需 X 认证——配置过程会自动安装免费的 Digg CLI（#590）。此外还加入了可选的 Trustpilot 来源，适合消费品牌研究。

### 免费 Reddit 搜索也有真实评分和热门评论

Reddit 的公开 `.json` API 停止工作后，免费的数据通路以更强的方式回归：无密钥 RSS + shreddit 抓取（#457）、通过 arctic-shift 发现垂直 subreddit 并获取真实赞同数（#696），以及相关性下限，防止病毒式传播但偏题的帖子劫持整份简报（#488，感谢 [@rzachsmith](https://github.com/rzachsmith)）。无需 API 密钥，提供真实评分和热门评论。

### 每份简报都收录最好的评论

评论现在是各来源默认启用的一层：Instagram 评论采用基于排名的多样性机制，避免五条热门观点全来自同一篇帖子（#751）；YouTube 评论配合 ScrapeCreators 字幕回退，以应对 yt-dlp 失效（#637）；经过社区投票的评论还会计入 Best Takes 的权重，让最有趣的金句不会在评分中消失（#592、#608）。

### 一个 doctor 命令解决健康检查

要求执行健康检查时，doctor 会逐一测试所有来源，并给出精确修复建议：缺少哪个密钥、哪个 CLI 不在 PATH、哪个 Cookie 已过期（#753）。不必再猜为什么 X 的结果这么少。

### 重构 X 搜索

X 流水线经过彻底重构：新增 FROM 和 ABOUT 两条通路，让某人的原创帖子和外界对他的讨论都能进入排名（#610）；按人物感知的子查询消歧（#611）；基于第一方作者身份的信息归属，并结合互动信号排序（#613）；统一的 X 来源与自动后端故障转移（#622）。另外，`--diagnose` 现在会真正探测认证状态，如实报告问题（#609）。

### 更多信息源加入

通过 ScrapeCreators 接入 LinkedIn，并将文章视为高价值信号（[@ravstr](https://github.com/ravstr)，#702）。StockTwits 会在股票代码和加密货币主题下自动启用（[@wtiwana](https://github.com/wtiwana)，#658）。Perplexity 新增直接 API 模式和异步 Deep Research（[@sk-holmes](https://github.com/sk-holmes)，#629）。

### 在社区协作下进一步加固

这一轮安全改进几乎全部来自社区：修复 HTML 渲染器中的存储型 XSS（[@iliaal](https://github.com/iliaal)、[@aaronjmars](https://github.com/aaronjmars)）；收紧 Cookie 临时文件权限；通过 OpenSSF Scorecard 和构建来源证明加固 CI 供应链（[@shaanmajid](https://github.com/shaanmajid)、[@hammadxcm](https://github.com/hammadxcm)、[@aniruddh909](https://github.com/aniruddh909)）；增加 Semgrep、OSV-Scanner 扫描以及 PR 依赖审查门禁（[@23241a6749](https://github.com/23241a6749)）；测试覆盖率门槛从 60% 起步，现已提高到 84%（[@gourab5139014](https://github.com/gourab5139014)）；Hermes 安全扫描中的所有 CRITICAL 问题也已清零（#768）。

### 覆盖范围更广

支持希伯来语和其他非拉丁文字语言（[@dudyme](https://github.com/dudyme)）；为中文来源加入 CJK 感知的分词（[@An-idd](https://github.com/An-idd)）；推进一系列 Windows 兼容性改进；支持从完整 Chromium 浏览器家族提取 Cookie——Brave、Edge、Vivaldi、Opera、Arc（[@andrey-esipov](https://github.com/andrey-esipov)）——并接入 macOS Keychain 和 Linux `pass(1)` 凭据来源。此外还有 `--as-of` 历史回溯（[@chiyi-creator](https://github.com/chiyi-creator)）、通过 uv 自动配置 Python 3.12（[@buntysomroy](https://github.com/buntysomroy)）、用于解读公司招聘页面的 `--hiring-signals`，以及多次运行之间的观察列表差异。

### v3 的核心能力仍然完整保留

v3 打下的基础都还在：真正调用 API 前先运行预研究模块，解析正确的账号、subreddit 和话题标签（由 [@j-sperling](https://github.com/j-sperling) 开发）；Best Takes 评分在相关性之外也衡量幽默感和传播力；跨来源故事聚类；单次完成对比研究（例如 “CLI vs MCP” 只需 3 分钟，而不是 12 分钟）；自动发现竞品的 `--competitors` 对比；GitHub 人物模式（`--github-user=steipete`）；任何研究结束后可开启的 ELI5 模式（输入 “eli5 on”）；以及可分享、自包含的 HTML 简报（`--emit=html`）。配置项详见 [CONFIGURATION.md](CONFIGURATION.md)。

## 安装

| 使用环境 | 安装方式 | 更新方式 |
|---------|---------|---------|
| **Claude Code**（推荐） | `/plugin marketplace add mvanhorn/last30days-skill` | 通过 marketplace 自动更新，或运行 `claude plugin update last30days@last30days-skill` |
| **Grok**（xAI Build CLI） | 先运行 `grok plugin marketplace add mvanhorn/last30days-skill`，再运行 `grok plugin install last30days` | `grok plugin update last30days` |
| **Codex、Cursor、Copilot、Gemini CLI，或其他 50 多个支持 [Agent Skills](https://agentskills.io) 的宿主** | `npx skills add mvanhorn/last30days-skill -g` | `npx skills update last30days -g` |
| **claude.ai**（网页） | [下载 `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill)，然后在 claude.ai 中依次进入 Customize > Skills > + > Create skill > Upload a skill 上传 | 重新下载并上传 |
| **Claude Desktop** | 从[最新版本](https://github.com/mvanhorn/last30days-skill/releases/latest)下载适用于你的平台的 `.mcpb`，拖入 Settings > Extensions | 重新下载新包并拖入 |
| **OpenClaw** | `clawhub install last30days-official` | `clawhub update last30days-official` |

### Claude Code（推荐）

```
/plugin marketplace add mvanhorn/last30days-skill
```

推荐这种方式，是因为 Claude Code marketplace 会替你处理更新：插件缓存按版本管理，每次发布新版本都会自动刷新。要强制检查更新，请运行 `claude plugin update last30days@last30days-skill`。

如果你更愿意在 Claude Code 中使用 Agent Skills 的安装方式，同样支持：

```
npx skills add mvanhorn/last30days-skill -g -a claude-code
```

原生插件和 `npx skills` 安装可以共存。但 Claude Code 不会对不同安装方式进行去重：若两者同时启用，`/last30days` 会出现两个条目。建议每台机器只选一种安装方式。

### Grok（xAI Build CLI）

[Grok Build](https://docs.x.ai/build/features/skills-plugins-marketplaces)（`grok`）可以将 last30days 安装为原生插件。直接安装会跟踪仓库更新：

```bash
grok plugin install mvanhorn/last30days-skill
```

也可以先把本仓库添加为 marketplace 来源，再按插件名安装：

```bash
grok plugin marketplace add mvanhorn/last30days-skill
grok plugin install last30days
```

加入 `--trust` 可跳过安装确认；使用 `grok plugin update last30days` 更新。为兼容旧机制，Grok 也会读取 Claude Code 的清单文件；原生 `.grok-plugin/` 文件是首选通路，也是 [xAI marketplace](https://github.com/xai-org/plugin-marketplace) 官方目录条目指向的对象。`npx skills add` 仍是有效的跨宿主备用方案。

### Codex、Cursor、Copilot、Gemini CLI 与其他 Agent Skills 宿主

通过开放的 [Agent Skills](https://agentskills.io) CLI 安装。它支持 50 多种运行环境，包括 `codex`、`cursor`、`github-copilot`、`gemini-cli`、`claude-code`、`windsurf`、`cline`、`continue`、`roo`、`aider-desk`、`opencode`、`goose` 等（完整列表见 [vercel-labs/skills 仓库](https://github.com/vercel-labs/skills)）。

```bash
npx skills add mvanhorn/last30days-skill -g
```

`-g`（全局）参数会把 Skill 安装到用户目录，因此所有项目均可使用。不加 `-g` 时，`npx skills` 会安装到当前项目的 `./.skills/` 中，并随仓库提交。对于一个用于研究整个世界的工具，全局安装通常更合适。

Codex 桌面版和其他以文件夹为工作区的宿主，不仅能在 Git 仓库中运行，也能在普通文件夹中工作。第一次研究前，请让宿主智能体从已加载的 Skill 目录运行随附的 `scripts/last30days.py --preflight`；若在源码仓库中，则运行等价命令 `python3 skills/last30days/scripts/last30days.py --preflight`。该命令会展示配置来源、浏览器 Cookie 方案、计划写入的文件、可选命令和被忽略的项目配置，但不会读取 Cookie、写入文件或执行研究。

默认情况下，`npx skills` 会安装到它自动检测到的宿主。若要指定一个或多个宿主：

```bash
npx skills add mvanhorn/last30days-skill -g -a codex
npx skills add mvanhorn/last30days-skill -g -a cursor
npx skills add mvanhorn/last30days-skill -g -a gemini-cli
npx skills add mvanhorn/last30days-skill -g -a codex -a cursor
```

日后可通过以下命令更新：

```bash
npx skills update last30days -g
```

也可以一次更新所有通过 `npx skills` 全局安装的 Skill：

```bash
npx skills update -g
```

使用 `npx skills list -g` 查看列表，使用 `npx skills remove last30days -g` 卸载。

### claude.ai（网页）

1. 从最新版本[下载 `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill)
2. 打开 [claude.ai > Customize > Skills](https://claude.ai/customize/skills)
3. 在 Skills 面板点击 `+`，再选择 `Create skill` > `Upload a skill`，浏览或拖入文件

请先在 Capabilities 中启用 “Code execution and file creation”——否则 Skill 无法运行。

### Claude Desktop

Claude Desktop 通过 `.mcpb` 包（一种一键式 Model Context Protocol 软件包）将 `/last30days` 安装为 MCP 服务器。

1. 打开[最新版本](https://github.com/mvanhorn/last30days-skill/releases/latest)，下载适用于你的平台的 `.mcpb`：
   - macOS Apple Silicon：`last30days-pp-mcp-darwin-arm64.mcpb`
   - macOS Intel：`last30days-pp-mcp-darwin-amd64.mcpb`
   - Linux x86_64：`last30days-pp-mcp-linux-amd64.mcpb`
2. 打开 Claude Desktop，进入 Settings > Extensions，将文件拖入。
3. 出现提示时，粘贴你想启用的数据源所需的 API 密钥。所有字段均可留空——如果全部跳过，引擎会降级为纯 Web 模式。密钥存储在操作系统的钥匙串中。
4. 重启 Claude Desktop。让 Claude “research Peter Steinberger” 或研究任意主题，它就会调用 `research` 工具。

**宿主要求：** PATH 中需要 Python 3.12+。软件包自带引擎源码，但使用本地 Python 解释器。Windows 用户可从 [python.org](https://www.python.org/downloads/) 安装；macOS 和大多数 Linux 发行版通常已提供兼容版本。

**密钥不会与 Code Skill 同步。** Claude Desktop 与 Claude Code 采用彼此独立的凭据存储，这是有意的设计。即使你已为 Code Skill 配置 `~/.config/last30days/.env`，仍需在这里重新输入一次相同的密钥。

Windows 支持需要等各平台的清单入口点确定后再实现，请关注后续 Issue。

### OpenClaw

```bash
clawhub install last30days-official
```

如果你需要在 `/last30days` 研究之外执行 X/Twitter 操作，例如发布推文或回复、导出关注者、处理媒体、监控账号或抽奖，可使用 [TweetClaw](https://github.com/Xquik-dev/tweetclaw) 作为配套 OpenClaw 插件。TweetClaw 由 Xquik-dev 维护，这里仅将其列为可选配套方案；它不是 last30days 的依赖，也不代表本项目为其背书。

### 手动安装（开发者）

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
ln -s "$(pwd)/last30days-skill/skills/last30days" ~/.claude/skills/last30days
```

这个符号链接会让安装内容随工作区代码实时同步，无需重复复制。若用于 `claude.ai`，可从源码构建 `.skill` 文件：运行 `bash skills/last30days/scripts/build-skill.sh`，产物位于 `dist/last30days.skill`。

Reddit（含评论）、Hacker News、Polymarket 和 GitHub 无需任何配置即可使用。首次运行 `/last30days` 后，配置向导会在 30 秒内解锁更多来源，包括免费的 arXiv 与 Techmeme CLI。

## 使用你自己的密钥

这些平台之间互不相通：X 不知道 Reddit 在讨论什么，YouTube 也看不到 TikTok。但只要接入你自己的 API 密钥和浏览器令牌，就能一次访问所有平台。

| 来源 | 你需要准备什么 | 成本 |
|------|----------------|------|
| Reddit（含评论）+ HN + Polymarket + GitHub + StockTwits | 无 | 免费 |
| arXiv + Techmeme | 免费 CLI，由首次配置自动安装 | 免费 |
| X / Twitter | 在任意浏览器中登录 x.com，或设置 `XQUIK_API_KEY` / `XAI_API_KEY` | 浏览器 Cookie 免费；密钥费用取决于服务商 |
| YouTube | `brew install yt-dlp` | 免费 |
| Bluesky | 来自 bsky.app 的应用密码 | 免费 |
| TikTok + Instagram + Threads + Pinterest + LinkedIn + YouTube 评论 | ScrapeCreators 密钥 | 前 10,000 次调用免费，之后按量付费 |
| 小红书（RED） | 运行已登录的 x-mcp 浏览器插件或 `xiaohongshu-mcp` 服务，并在单次运行中通过 `--search xhs` 启用，或在 `.env` 中设置 `INCLUDE_SOURCES=xiaohongshu`；last30days 会依次自动探测 `http://localhost:18060` 和 `http://host.docker.internal:18060`，也可通过 `XIAOHONGSHU_API_BASE` 指定自定义地址 | last30days 不需要 API 密钥；依赖本地浏览器会话服务 |
| DripStack（付费金融通讯） | 每次运行通过 `--search dripstack` 启用，或在 `.env` 中设置 `INCLUDE_SOURCES=dripstack` | 无需密钥；公共搜索 API 免费 |
| Perplexity Sonar / Search API / Deep Research | Perplexity 密钥，或作为 Sonar 回退方案的 OpenRouter 密钥 | 按量付费 |
| Web 搜索 | Brave Search 密钥 | 每月 2,000 次免费查询 |

### macOS Keychain（可选）

在 macOS 上，你可以把密钥存入系统 Keychain，而不是 `.env` 文件。Skill 会自动将其作为最低优先级的密钥来源；发生冲突时，`.env` 文件和进程环境变量仍然优先。

```bash
# 交互式配置——逐个询问已知密钥，留空即可跳过
skills/last30days/scripts/setup-keychain.sh

# 也可以手动存入单个密钥
security add-generic-password -a "$USER" -s last30days-XAI_API_KEY -w "xai-..."

# 查看 / 清理
skills/last30days/scripts/setup-keychain.sh --list
skills/last30days/scripts/setup-keychain.sh --delete XAI_API_KEY
```

密钥项以 `last30days-<KEY>` 作为服务名称，归当前用户所有。在非 Darwin 平台上，加载器不会执行任何操作，因此 Linux/Windows 用户的行为不受影响。

如果已有密钥使用其他 Keychain 服务名称，可按 [CONFIGURATION.md](CONFIGURATION.md#reusing-existing-macos-keychain-items) 中的说明设置不含秘密的 `LAST30DAYS_KEYCHAIN_ALIASES` 映射，无需复制密钥。

各来源的完整密钥矩阵、推理服务商优先级和 Web 搜索后端优先级，请参阅 [CONFIGURATION.md](CONFIGURATION.md)。

## 配置

第一天使用时，你大概最想知道以下两件事：

**研究文件保存在哪里。** `LAST30DAYS_MEMORY_DIR` 默认指向 `~/Documents/Last30Days/`（Windows：`C:\Users\<you>\Documents\Last30Days\`）。可以在 shell 中把该环境变量设为任意路径，也可以为单次运行传入 `--save-dir <path>`。若需要把渲染结果精确写入某个路径，请使用 `--output <file>`；文件格式由 `--emit` 决定。使用 `--save-suffix=<name>` 可分别保存同一主题的多个版本（例如按客户区分）。每次使用 `--save-dir` 都会生成 `<slug>-raw[-suffix].md`。研究前运行 `python3 skills/last30days/scripts/last30days.py --preflight`，可预览计划写入的内容。

**面向智能体和工作流的结构化输出。** 让 `/last30days` 输出机器可读的 JSON，即可获得稳定且带版本号的 agent profile。若在脚本或开发中直接调用引擎，可运行 `python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json`；只有确实需要未版本化的内部 `Report` 转储时，才添加 `--json-profile=raw`。详见 [JSON 导出字段参考与版本策略](docs/reference/json-export.md)。

**无指定主题的趋势发现。** 输入 `/last30days what's trending in AI agents?`，会得到按排名整理的发现简报，而不是研究一个你已经知道的主题。在智能体宿主上，它会执行由宿主模型评审的三段式流程：模型命名主题、过滤垃圾、判断内容价值并撰写切入角度。在脚本或定时任务中直接调用引擎时，可运行 `python3 skills/last30days/scripts/last30days.py --discover "AI agents"`（单次执行：主题名称由确定性逻辑生成，不含内容角度）；加入 `--emit=json` 可获得带版本号的发现数据契约。发现模式不能与位置参数主题或 `--drill` 同时使用。

**跨运行趋势监控。** 默认模式每次运行都会生成新的 Markdown 快照。若要长期积累结果，可添加 `--store` 写入 SQLite 数据库；随后使用 [`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py) 定时运行（发现新内容时可发送到 Slack 或 Webhook），使用 [`scripts/briefing.py`](skills/last30days/scripts/briefing.py) 生成日报或周报。完整的周期配置见 [CONFIGURATION.md](CONFIGURATION.md#trend-monitoring-store--watchlist--briefings)。

**可订阅的研究资料库。** 让 `/last30days` 构建你的资料库信息流；在脚本和开发中也可以直接运行 `python3 skills/last30days/scripts/last30days.py library feed`。该命令会把已保存的简报整理成 `index.html`、本地 Atom `feed.xml` 和便于阅读的简报页面。仅在确实想托管 HTML 索引和简报页面时添加 `--publish`；发布必须显式开启，且默认公开。若要让 Atom 信息流可订阅，请将生成目录托管到 GitHub Pages 等静态站点服务。

**搜索你做过的所有研究。** 输入 `/last30days search my library for MCP servers` 或 `/last30days have I researched MCP servers before?`。直接调用引擎时，运行 `python3 skills/last30days/scripts/last30days.py library search "MCP servers"`。搜索完全离线且结果确定：它增量索引资料库信息流使用的同一批简报，合并每次运行存储的匹配记录，并按主题和日期分组。新的研究若与历史内容重叠，还会显示精简的 **From your library** 章节；设置 `LAST30DAYS_LIBRARY_CONTEXT=off` 可关闭这种被动上下文。

各客户端包装脚本、自定义分类同类 subreddit，以及用于试验进行中定制功能的 beta 通道，也都记录在 [CONFIGURATION.md](CONFIGURATION.md) 中。

## 展示：社区研究信息流

你是否用 last30days 发布了定期 AI 动态、市场观察，或某个小众到可爱的长期专题？欢迎在[社区展示帖](https://github.com/mvanhorn/last30days-skill/issues/532)分享公开资料库 URL；若已将 `feed.xml` 托管到静态站点，也可分享 Atom URL。社区成员提交后，我们会在这里陆续添加链接；在此之前，该讨论帖就是统一的收集入口。

## 工作原理

1. **你输入一个主题。** 人物、公司、产品、技术、“X vs Y”——任何内容都可以。
2. **智能体识别关键对象。** 找出 X 账号（包括创始人）、GitHub 仓库、subreddit、TikTok 话题标签和 YouTube 频道。搜索 “Kanye West” 时，它知道该查 r/hiphopheads、@kanyewest，以及 YouTube 上的 “bully review”；搜索 “OpenClaw” 时，它会定位 GitHub 上的 openclaw/openclaw 并获取实时 Star 数。
3. **并行搜索所有来源。** 扩展多个查询，再按互动度、相关性和新鲜度评分。
4. **提供其他工具没有的深度。** 获取反应视频的完整 YouTube 字幕、带赞同数的 Reddit 热门评论、TikTok 文案和 Polymarket 概率，而不只是标题和链接。
5. **合并同一事件。** Wireless Festival 在 Reddit 官宣、在 X 上引发讨论、TikTok 出现票价信息——这些会合并为一个故事聚类，而不是三条重复结果。
6. **综合成一份简报。** 用具体数据作依据，为来源添加引用，并按真实互动排序。不是“这是我找到的内容”，而是“这是最重要的内容”。
7. **随后成为你的领域专家。** 运行一次后，当前 Claude 会话就掌握社区知道的一切。你可以继续追问，让它写提示词、起草邮件、规划旅行或设计系统架构——所有回答都基于此刻真实存在的信息。

## 用户怎么评价

> “我发现了一个 Claude Code Skill，可以研究任意主题过去 30 天在 Reddit、X、YouTube 和 HN 上的内容，然后替你写提示词。以前每写一篇内容，我都得手动在 Reddit 和 X 上做研究：一个标签页接一个标签页，一条讨论接一条讨论。光这一步就要 90 分钟。它彻底省掉了这些工作。” ——@itsjasonai

> “仅仅这一个 Skill，就取代了我的整套研究工作流。给它一个主题，它会抓取 Reddit、X 和 Web 上人们真正在谈论的内容。不是陈旧的博客，而是过去 30 天里真实发生的讨论。” ——@itswilsoncharles

> “今天 GitHub 的 10 个趋势仓库中，有 5 个是 Claude 工具。第一名：mvanhorn/last30days-skill。” ——@yieldhunter95

## 开源

采用 MIT 许可证。无跟踪、无分析，你的研究数据始终留在本机。拥有 2,700 多项测试。

项目基于 Python 3.12+、yt-dlp、Node.js（内置用于 X 搜索的 Bird 客户端）和 ScrapeCreators API 构建。v3 引擎架构由 [@j-sperling](https://github.com/j-sperling) 设计。

提交 PR 请参阅 [CONTRIBUTING.md](CONTRIBUTING.md)，完整社区贡献者名单见 [CONTRIBUTORS.md](CONTRIBUTORS.md)，版本历史见 [CHANGELOG.md](CHANGELOG.md)。

## Star 历史

<a href="https://star-history.com/#mvanhorn/last30days-skill&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
    <img alt="Star 历史图" src="https://api.star-history.com/svg?repos=mvanhorn/last30days-skill&type=Date" />
  </picture>
</a>

---

**@slashlast30days** · [github.com/mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill)
