<div align="center">

# OpenProxy MCP

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/tools-32-orange.svg)](#工具结构32-项工具)
[![Release](https://img.shields.io/badge/release-v2.7.3-blue.svg)](docs/RELEASE_NOTES_ENG.md)
[![Stars](https://img.shields.io/github/stars/MarcoYou/open-proxy-mcp?label=stars&color=f5c518&logo=github&logoColor=white)](https://github.com/MarcoYou/open-proxy-mcp)
[![Sponsor](https://img.shields.io/badge/Sponsor-%E2%9D%A4-ea4aaa?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/MarcoYou)

[한국어](README.md) · [English](README_ENG.md)

[快速开始](#快速开始) · [可以问什么](#可以问什么) · [如何阅读结果](#如何阅读结果) · [最近更新](#最近更新) · [工具结构](#工具结构32-项工具) · [数据来源](#数据来源)

</div>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="screenshot/opm-readme-particle-flow-dark-en-20260906.png">
  <source media="(prefers-color-scheme: light)" srcset="screenshot/opm-readme-particle-flow-light-en-20260906.png">
  <img alt="将公司披露数据转化为 AI 财务分析与投票建议的过程" src="screenshot/opm-readme-particle-flow-light-en-20260906.png">
</picture>

**一项议案也许只有一行，但可靠的判断需要完整的信息。**

OpenProxy 是一个 MCP 服务器，它把**韩国上市公司**向韩国电子披露系统 DART 提交的公告结构化，供 AI 阅读。它最初用于股东大会和代理投票分析，后来扩展到财务、估值、业务、股权、股东回报和全市场披露。AI 在给出答案的同时提供**原始依据（公告接收编号）**。

---

## 快速开始

**无需安装，只需一个 DART API 密钥即可连接。** 带界面截图和示例的说明见 📘 [User Guide（英文 PDF，13 页）](docs/manual/OpenProxy_User_Guide.pdf)。

**1. 获取免费的 API 密钥** — 在 [English OpenDART](https://engopendart.fss.or.kr/) 注册并申请认证密钥（免费，立即发放）。

**2. 连接 AI 服务** — 在添加连接器或应用时，将以下 URL 填入服务器地址，并命名为 `open-proxy-mcp`。

```
https://open-proxy-mcp.fly.dev/mcp?opendart=您的_DART_API_密钥
```

| 服务 | 设置路径 | 适用范围 |
|---|---|---|
| [**Claude**](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp) | `Customize → Connectors → + → Add custom connector` | Free（1 个）、Pro、Max；Team/Enterprise 由管理员添加 |
| [**ChatGPT**](https://developers.openai.com/api/docs/guides/developer-mode) | `Settings → Security and login → Developer mode`，然后选择 `Plugins → +` | 网页版 Plus、Pro、Business、Enterprise、Education |
| [**Perplexity**](https://www.perplexity.ai/help-center/en/articles/13915507-adding-custom-remote-connectors.html) | `Account settings → Connectors → + Custom connector → Remote` | Pro、Max、Enterprise |

> 请只在连接器设置中填写此 URL。OpenProxy 不保存密钥原文，并在日志中隐去密钥。

**3. 发送第一个问题** — 在新对话中输入 `显示三星电子的公司信息和最近 3 条公告`。如果出现公司信息和公告列表，说明连接成功。之后无需知道工具名称，直接用自然语言提问即可。

<details>
<summary>在 Claude 中打开提示词与资源</summary>

点击聊天输入框中的 `+`，然后选择 `Connectors → Add from open-proxy-mcp`（[菜单位置](screenshot/claude-resource-picker-en-20260907.png)）。

- `Company Snapshot`（公司概览）— 快速浏览公司、业务、业绩、估值与股东信息
- `OpenProxy Feature Guide`（功能指南）— 当前已注册的工具与功能（`tools_guide` resource）
- `Open Proxy Guideline` — 代理投票分析使用的政策原文

</details>

---

## 可以问什么

无需记住工具名称，说出想做的事，AI 会自行选择工具。以下是各领域实际可用的提问示例，以及每个领域值得了解的一点。

| 领域 | 可以这样问 | 值得了解 |
|---|---|---|
| 🗳️ [**股东大会与代理投票**](docs/features/en/proxy-voting.md) | 「列出 LG 化学下次股东大会的议案，并附上每项议案的投票意见和依据」<br>「三星电子去年股东大会的议案实际是如何表决的？」 | 每项议案给出赞成、反对或**需要审查**，并附披露、政策和法律依据。没有任何反对意见是常见情况 |
| 📊 [**财务与业绩**](docs/features/en/financials.md) | 「比较三星电子近 3 年业绩与未来 2 年的一致预期」<br>「现代汽车的[初步业绩](docs/features/en/provisional-earnings.md)与一致预期相比如何？」 | 已确认（A）、初步和预期（E）数据来源不同，不会放在同一行 |
| 💹 [**估值与预期**](docs/features/en/price_multiple_data.md) | 「NAVER 的 PER、PBR 与历史相比处于什么水平？」<br>「按行业并列显示 KOSPI 的远期 PER 与历史 PER」 | 业绩基准日与股价基准日分开标注 |
| 📈 **业绩表现与风险** | 「把三星电子 50%、SK 海力士 50% 的组合与 KOSPI 比较近一年表现，并按个股拆分贡献」 | 按需计算收益率、波动率、超额收益、贡献和行业分解 |
| 🏭 [**业务与资产**](docs/features/en/business-details.md) | 「韩华 Solutions 靠什么赚钱？按业务分部列出」<br>「计算泰光产业持有的关联公司股权价值」 | 按分部、产品、地区划分的收入是同一总额的不同切分，不相加 |
| 🧭 [**股权与股东回报**](docs/features/en/ownership.md) | 「三星物产的最大股东及关联方持股是多少？」<br>「[价值提升计划](docs/features/en/shareholder-return.md)中的承诺与实际执行相符程度如何？」 | 每股股息按各类股份计算；股息总额和派息率按全公司计算 |
| 🔔 **市场与风险** | 「只整理今天早上的重要公告」<br>「最近有哪些公司出现[控制权争夺](docs/features/en/control-contest.md)信号？」 | 实时全市场扫描以最近 3 个月为限；行业动向使用存储的记录，约覆盖 1 年 |
| 🔗 **核对依据** | 「刚才那个数字出自哪份公告的哪一项？」<br>「公司章程的这一条对应《商法》第几条？」 | 每个数字都附有接收编号，可转为 DART 原文地址。章程与法律查询不使用 DART API |

**治理审查试点** — `governance_screen` 收集最多 30 家指定公司的公告原文，由已连接的 AI 判读少数股东待遇、利益冲突和董事会责任，并排出审查顺序。结果标注为**基于部分依据的 AI 评估 · 未经人工审查**。

如果结果不完整或需要反复提问，请参阅[工具调用、追加查询与错误处理指南](docs/TOOL_CALLING.md)。

---

## 如何阅读结果

不同披露项目的口径各不相同。掌握以下六点，大部分情况都能正确阅读。

1. **「未找到」不等于「没有」。** 表示在所读公告中没有找到，缺失值不会填为 0。
2. **先看 `status` 和 `warnings`。** 其中会写明没有找到什么，以及改用了什么口径。
3. **公司只需确定一次。** 名称有歧义时，服务器会先询问再继续。
4. **不同口径的数字不混用。** 已确认、初步与预期，合并与单独，归属母公司与全部 —— 口径不同时不计算增长率，只显示差异。
5. **投票意见中「反对为 0」很正常。** 政策中的反对标准并不等于自动反对条件；需要进一步判断的问题保留为**需要审查** —— [如何理解投票建议与信息截止日期](docs/features/en/proxy-voting.md)。
6. **可能出现 📢 公告。** 维护或变更预告等运营方公告会出现在回复顶部或 JSON 的 `notices` 字段中。它们是通知，不是数据。

<details>
<summary><b>更多细节 —— 会计年度、金融公司、分红、扫描期限</b></summary>

<br>

| 内容 | 原因 |
|---|---|
| **会计年度以结算月为准** | 非 12 月结算的公司与日历年不一致。新荣证券（001720）的 `2025-06-30` 为 FY2026-Q1 |
| **金融公司没有部分指标** | 营业收入和一般企业比率显示为**不提供**，而不是 0 |
| **市场与行业远期倍数只包含有预期数据的公司** | 与历史倍数的样本不同，会同时注明公司数量 |
| **接收编号的前两位区分来源** | `00` 为 DART 定期公告（会议通知），`80` 为交易所公告（会议结果） |
| **每个 DART 密钥每分钟 1,000 次** | 超出后该密钥会被封锁两到三小时。服务器在 910 次时自行停止，并在 `data.usage` 中记录调用次数 |
| **网页解析每分钟 40 页** | 请求间隔 0.4–1 秒随机；封锁按 IP 计算，整台机器都会受影响 |
| **不保存您的查询结果** | 只保留缓存、市场快照和调用量统计 |

</details>

---

## 最近更新

完整记录见 **[发布说明](docs/RELEASE_NOTES_ENG.md)**。这里只列出用户能感知到的近期变化。

| 日期 | 变化 |
|---|---|
| 2026-10-23 | **财务数据准确性** —— 按会计科目代码读取现金流项目；修正小数 EPS、报告货币变更年度的上年数值以及金融公司的警示；把查询失败误报为「没有」的地方一并修正 |
| 2026-10-23 | **运营方公告** —— 维护与变更预告会出现在工具回复中，按重要程度决定出现频率 |
| 2026-10-22 | **新工具 `performance_analysis`** —— 收益率、风险、贡献和行业分解，可与官方指数和国债利率比较 |
| 2026-10-18 | **初步业绩「与一致预期相比」** —— 早间公告摘要中的初步业绩卡片也会显示 |

---

## 工具结构（32 项工具）

上述八个领域共由 **32 项工具**支持。分类以[工具目录](wiki/tools/README.md)中的「想了解什么时用什么」表为准，各工具的提问示例与结构也在其中。

<details>
<summary><b>展开工具列表</b></summary>

<br>

| 分类 | 工具 | 作用 |
|---|---|---|
| 🏢 查找公司 | [`company`](wiki/tools/company.md) | 识别公司并列出最近公告 —— 所有分析的起点 |
| 🔔 公告扫描与审查 | [`screener`](wiki/tools/screener.md), [`governance_screen`](wiki/tools/governance_screen.md) | 全市场公告摘要与行业动向 · 指定公司的治理审查顺序（试点） |
| 🗳️ 股东大会与代理投票 | [`shareholder_meeting_notice`](wiki/tools/shareholder_meeting_notice.md), [`shareholder_meeting_results`](wiki/tools/shareholder_meeting_results.md), [`proxy_guideline`](wiki/tools/proxy_guideline.md) | 会议通知（会前）与结果（会后）· 政策原文 |
| 💰 股权、财务与治理 | [`ownership_structure`](wiki/tools/ownership_structure.md), [`financial_metrics`](wiki/tools/financial_metrics.md), [`provisional_earnings`](wiki/tools/provisional_earnings.md), [`business_details`](wiki/tools/business_details.md), [`asset_holdings`](wiki/tools/asset_holdings.md), [`price_multiple_data`](wiki/tools/price_multiple_data.md), [`forward_estimates_data`](wiki/tools/forward_estimates_data.md), [`trading_data`](wiki/tools/trading_data.md), [`performance_analysis`](wiki/tools/performance_analysis.md), [`corp_gov_report`](wiki/tools/corp_gov_report.md), [`director_board`](wiki/tools/director_board.md) | 股权结构 · 已确认/初步业绩 · 业务内容 · 资产股 · PER/PBR · 一致预期与修正 · 行情、指数、利率 · 收益率、风险、贡献 · 公司治理报告 · 董事会 |
| 🎁 股东回报与资本 | [`dividend_disclosure`](wiki/tools/dividend_disclosure.md), [`dividend_data`](wiki/tools/dividend_data.md), [`treasury_share`](wiki/tools/treasury_share.md), [`value_up`](wiki/tools/value_up.md), [`shareholder_commitment`](wiki/tools/shareholder_commitment.md), [`corporate_restructuring`](wiki/tools/corporate_restructuring.md), [`dilutive_issuance`](wiki/tools/dilutive_issuance.md) | 分红公告与历史 · 库存股 · 价值提升 · 承诺与执行 · 合并/分拆 · 增资、可转债/附认股权证债券、减资 |
| ⚔️ 争夺、交易与风险 | [`proxy_contest`](wiki/tools/proxy_contest.md), [`corporate_deals`](wiki/tools/corporate_deals.md), [`order_contracts`](wiki/tools/order_contracts.md), [`risk_events`](wiki/tools/risk_events.md), [`financial_notes`](wiki/tools/financial_notes.md), [`director_news`](wiki/tools/director_news.md) | 控制权争夺信号 · 股权收购/出售 · 订单与供货合同 · [风险事件](docs/features/en/risk-events.md) · 金融公司附注 · 董事候选人新闻 |
| 🔗 依据与参考 | [`evidence`](wiki/tools/evidence.md), [`law_lookup`](wiki/tools/law_lookup.md) | 接收编号 → 原文地址 · 章程 ↔ 法律双向查询（DART API 0 次） |

</details>

**投票建议** —— 在公开服务器上，四个投票建议工具（`proxy_advice_start` → `proxy_advice_read` → `proxy_advice_rules` → `proxy_advice_finish`）为每项议案给出赞成、反对或需要审查的草案及理由。它们只读取判断基准时间之前公开的披露（会议通知、年度报告、审计与征集文件、法院披露），按投票指引（10 类议案）判断，并由服务器逐项检查。指引与检查在非公开服务器上运行，不在本仓库中。旧的 `proxy_advise_before_meeting` 已于 2026-10-26 退役。草案未经人工审核，也不会发送任何投票。

---

## 数据来源

| 来源 | 用途 | 说明 |
|------|------|------|
| [DART OpenAPI](https://opendart.fss.or.kr/) | 公告元数据、财务 API、分红、库存股、股权等结构化数据 | **必需** —— 免费 API 密钥 |
| DART 网页（`dart.fss.or.kr`） | 解析公告正文（会议通知、主要事项报告等） | 每分钟 40 页 |
| [KRX KIND](https://kind.krx.co.kr/) | 交易所公告的辅助核对 | 辅助来源 |
| 基于[国家法令信息中心](https://www.law.go.kr/)的法律原文 | 章程变更和投票判断的法律依据 | 每周从 [legalize-kr](https://github.com/legalize-kr/legalize-kr) 同步 |
| 主要资产管理公司与国民年金公团的公开投票记录 | 投票判断的交叉核对 | 预先收集并结构化的公开资料 |

---

## 安全 · 免责声明 · 许可证

**安全** —— 发现漏洞（尤其是可能泄露 API 密钥的途径）时，请按 [SECURITY.md](SECURITY.md) 的流程报告，**不要提交公开 issue**。

**免责声明** —— OpenProxy 是将 DART 公告数据结构化后提供给 AI 的工具。AI 可能给出不准确的分析，其意见不代表开发者或其所属机构。最终的投资或投票决定必须核对原始公告，并经过专业人员审查。

**许可证** —— [PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/) · 完整文本见 [`LICENSE`](LICENSE)

- **非商业用途**（个人研究、学习、非营利组织和公共机构）可自由使用。
- **商业用途**需要另行签署许可协议（OpenProxy AI）。
- **重新分发时的署名**：须保留 `Copyright (c) 2026 OpenProxy AI (https://github.com/MarcoYou/open-proxy-mcp)`（PolyForm “Notices”条款）。

> 商业许可及其他咨询：gunhoqw20@gmail.com
