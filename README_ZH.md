# OpenProxy MCP

[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-lightgrey.svg)](https://polyformproject.org/licenses/noncommercial/1.0.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/tools-31-orange.svg)](#工具结构31-项工具)
[![Release](https://img.shields.io/badge/release-v2.5-blue.svg)](docs/RELEASE_NOTES_ENG.md)

[한국어](README.md) · [English](README_ENG.md)

[快速开始](#快速开始) · [主要功能](#主要功能) · [工具结构](#工具结构31-项工具) · [数据来源](#数据来源)

## 为什么选择 OpenProxy？

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="screenshot/opm-readme-particle-flow-dark-en-20260906.png">
  <source media="(prefers-color-scheme: light)" srcset="screenshot/opm-readme-particle-flow-light-en-20260906.png">
  <img alt="将公司披露数据转化为 AI 财务分析与投票建议的过程" src="screenshot/opm-readme-particle-flow-light-en-20260906.png">
</picture>

**一项议案也许只有一行，但可靠的判断需要完整的信息。**

OpenProxy 最初用于股东大会和代理投票分析。为了综合阅读财务报表、股权结构、分红记录、董事会信息和相关法律而开发的功能，逐步扩展为面向 DART 公司披露的通用分析引擎。从财务分析到投票建议，AI 会同时给出结论及其原始依据。

## 快速开始

**无需安装，只需一个 DART API 密钥即可连接。**

### 1. 获取免费的 API 密钥

DART 是韩国的公司电子披露系统。请在 [English OpenDART](https://engopendart.fss.or.kr/) 注册并申请免费的认证密钥。

### 2. 连接 AI 服务

在添加连接器或应用时，将以下 URL 填入服务器地址：

```
https://open-proxy-mcp.fly.dev/mcp?opendart=YOUR_DART_API_KEY
```

> 请仅在连接器设置中输入此 URL。OpenProxy 不会保存原始密钥，并会在日志中隐藏密钥。

| 服务 | 设置路径 | 可用范围 |
|---|---|---|
| [**Claude**](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp) | `Customize → Connectors → + → Add custom connector` | Free（一个连接器）、Pro、Max；Team/Enterprise 由管理员设置 |
| [**ChatGPT**](https://developers.openai.com/api/docs/guides/developer-mode) | `Settings → Security and login → Developer mode`，然后选择 `Plugins → +` | 网页版 Plus、Pro、Business、Enterprise、Education |
| [**Perplexity**](https://www.perplexity.ai/help-center/en/articles/13915507-adding-custom-remote-connectors.html) | `Account settings → Connectors → + Custom connector → Remote` | Pro、Max、Enterprise |

将连接器命名为 `open-proxy-mcp`，并在新对话中选择它。具体可用范围可能因账户或工作区设置而异。

### 3. 发送第一个问题

可以先问：`Show Samsung Electronics' company information and three recent filings.` 如果返回公司信息和披露列表，说明连接成功。之后可以继续使用自然语言提问，无需记住工具名称。

- `Review LG Chem's next AGM agenda and give an evidence-backed voting view on each item.`
- `Compare Samsung Electronics' last three years of results with the next two years of consensus estimates.`

更多示例问题请参阅[工具目录](wiki/tools/README.md)中的各个工具页面。

在支持 MCP resources 的客户端中，可以打开 `tools_guide`（`opm://tools_guide`）查看当前提供的工具及其说明。该指南会根据服务器注册的工具自动生成。

在支持 MCP prompts 的客户端中，可以选择 `company_snapshot`（公司单页概览）并输入公司名称或股票代码。它会引导 AI 汇总业务结构、三年实际业绩、最多两年的年度一致预期、估值、股权、分红、近期披露以及后续应核实的问题。表格会区分实际值（A）与预期值（E）；支持可视化的客户端还会生成营收柱状图和营业利润折线图。

---

## 主要功能

**阅读披露，连接数据，并为每项结论保留依据。**

| 分析领域 | 核心问题 | OpenProxy 提供的结果 |
|---|---|---|
| 🗳️ [股东大会与代理投票](docs/features/en/proxy-voting.md) | 这项议案应该如何投票？ | 基于披露、投票政策和法律条文给出 **赞成 / 反对 / 需要审查**；明确区分无需投票和数据不足的情况 |
| 📊 [财务与业绩](docs/features/en/financials.md) | 经营表现发生了什么变化？ | 比较已确认、[初步](docs/features/en/provisional-earnings.md)和一致预期数据，并分析盈利能力、现金流和杜邦指标 |
| 💹 [估值与预期](docs/features/en/price_multiple_data.md) | 当前价格反映了什么？ | 历史及远期 PER/PBR/PSR、股息率，以及[未来两年的预期](wiki/tools/forward_estimates_data.md) |
| 🏭 [业务与资产](docs/features/en/business-details.md) | 公司如何赚钱，又持有哪些资产？ | 业务分部、产能利用率、投入成本、订单储备、[富余资产与持股 NAV](docs/features/en/asset-holdings.md) |
| 🧭 [股权与股东回报](docs/features/en/ownership.md) | 谁控制公司，资本流向哪里？ | 股权结构、分红、股份回购与注销，以及[价值提升计划与实际执行情况](docs/features/en/shareholder-return.md) |
| 🔔 [市场与风险](wiki/tools/screener.md) | 今天发生了什么变化？ | 市场披露摘要，以及[控制权争夺](docs/features/en/control-contest.md)、交易、股权稀释和[风险事件](docs/features/en/risk-events.md)跟踪 |

这六类分析流程由 **31 项工具**支持，其中包括来源追踪，以及公司章程与法律条文之间的双向查询。完整列表见[工具结构](#工具结构31-项工具)。

---

## 工具结构（31 项工具）

分类与 [wiki/tools 工具目录](wiki/tools/README.md)中的“想了解什么 → 使用哪个工具”表保持一致，该表是工具分类的唯一权威来源。

| 分类 | 工具 | 作用 |
|---|---|---|
| 🏢 起点：查找公司 | [`company`](wiki/tools/company.md) | 识别公司并列出近期披露，是所有分析的起点 |
| 🔔 全市场扫描与摘要 | [`screener`](wiki/tools/screener.md) | 全市场披露筛选与晨间披露摘要 |
| 🗳️ 股东大会与投票 | [`shareholder_meeting_notice`](wiki/tools/shareholder_meeting_notice.md)、[`shareholder_meeting_results`](wiki/tools/shareholder_meeting_results.md)、[`proxy_advise_before_meeting`](wiki/tools/proxy_advise_before_meeting.md)、[`proxy_guideline`](wiki/tools/proxy_guideline.md) | 会前通知、会后结果、逐项赞成/反对/审查建议以及投票政策原文 |
| 💰 股权、财务与治理 | [`ownership_structure`](wiki/tools/ownership_structure.md)、[`financial_metrics`](wiki/tools/financial_metrics.md)、[`provisional_earnings`](wiki/tools/provisional_earnings.md)、[`business_details`](wiki/tools/business_details.md)、[`asset_holdings`](wiki/tools/asset_holdings.md)、[`price_multiple_data`](wiki/tools/price_multiple_data.md)、[`forward_estimates_data`](wiki/tools/forward_estimates_data.md)、[`trading_data`](wiki/tools/trading_data.md)、[`corp_gov_report`](wiki/tools/corp_gov_report.md)、[`director_board`](wiki/tools/director_board.md) | 股权结构、已确认/初步业绩、业务说明、资产价值、PER/PBR、一致预期、价格/市值、治理报告和董事会 |
| 🎁 股东回报与资本 | [`dividend_disclosure`](wiki/tools/dividend_disclosure.md)、[`dividend_data`](wiki/tools/dividend_data.md)、[`treasury_share`](wiki/tools/treasury_share.md)、[`value_up`](wiki/tools/value_up.md)、[`shareholder_commitment`](wiki/tools/shareholder_commitment.md)、[`corporate_restructuring`](wiki/tools/corporate_restructuring.md)、[`dilutive_issuance`](wiki/tools/dilutive_issuance.md) | 分红披露与历史、自有股份、价值提升、承诺与执行、合并/分拆、增发/可转债/认股权证/减资 |
| ⚔️ 争夺、交易与风险 | [`proxy_contest`](wiki/tools/proxy_contest.md)、[`corporate_deals`](wiki/tools/corporate_deals.md)、[`order_contracts`](wiki/tools/order_contracts.md)、[`risk_events`](wiki/tools/risk_events.md)、[`financial_notes`](wiki/tools/financial_notes.md)、[`director_news`](wiki/tools/director_news.md) | 控制权争夺信号、股权交易、订单/供应合同、风险事件、金融机构附注和董事候选人新闻 |
| 🔗 证据与参考 | [`evidence`](wiki/tools/evidence.md)、[`law_lookup`](wiki/tools/law_lookup.md) | 披露受理编号到原文查看链接，以及公司章程与法律条文的双向查询（不消耗 API 调用） |

> 各工具的示例问题、详细结构和数据来源请参阅 [wiki/tools 工具目录](wiki/tools/README.md)。自然语言示例位于各工具页面的使用方法部分。

### 投票政策

**政策中的反对标准并不一定会自动生成“反对”建议。** 需要进一步判断的问题会标记为“需要审查（REVIEW）”；董事出席率目前不作为自动判断条件。可以通过 `proxy_guideline` 查询引用的政策章节，或查看 `0-A` 章节中的政策与引擎对应关系。另见[如何理解投票建议、会议选择和信息截止日期](docs/features/en/proxy-voting.md)。默认报告和详细政策引用以韩文提供；可要求 AI 在保留证据和状态的同时用中文说明。

`proxy_advise_before_meeting` 默认使用 OPM 自有的 **Open Proxy Guideline**。判断标准包括少数股东保护、治理透明度、长期价值和可追溯性。主要资产管理公司通过 KRX 披露的投票记录以及韩国国民年金公布的投票记录用于交叉核对。每个响应都包含 `data.usage` 区块，显示 DART 和工具调用次数；DART 的每分钟限制为 1,000 次，服务器上限设为 910 次。

**核对财务依据。** 应区分待批准年度的已确认数据、会议通知中的初步数据以及上一年度的已确认数据。初步数据不会替代所有指标，因此需要核对报告年度、来源和初步数据标记。基于初步数据对资本亏损作出的判断，不能替代需要经审计财务报表的监管结论。有关信息截止日期和后续披露的处理方式，请参阅[功能指南](docs/features/en/proxy-voting.md)。

---

## 数据来源

| 来源 | 用途 | 说明 |
|---|---|---|
| [English OpenDART](https://engopendart.fss.or.kr/) | 披露元数据、财务接口、分红、自有股份和股权数据 | **必需**：免费 API 密钥；每分钟最多 1,000 次，服务器安全上限为 910 次 |
| DART 网站（`dart.fss.or.kr`） | 解析披露正文，例如股东大会通知和重大事项报告 | 请求之间随机等待 1–2 秒 |
| [KRX KIND](https://kind.krx.co.kr/) | 交叉核对交易所披露 | 辅助数据来源 |
| 基于[韩国国家法令信息中心](https://www.law.go.kr/)的法律原文 | 查询章程修订和投票判断所依据的法律 | 每周从 [legalize-kr](https://github.com/legalize-kr/legalize-kr)同步 |
| 主要资产管理公司通过 KRX 披露的投票记录，以及韩国国民年金公布的投票记录 | 投票判断的交叉参考 | 预先收集并结构化的公开资料 |

> OpenProxy 分析的是韩国公司的披露资料。部分原始文件、政策引用和详细工具文档仅提供韩文或英文版本。

---

## 发布说明

版本更新记录见 **[docs/RELEASE_NOTES_ENG.md](docs/RELEASE_NOTES_ENG.md)**。

---

## 免责声明

OpenProxy 是一项将 DART 公司披露数据结构化并提供给 AI 使用的工具。AI 可能产生幻觉或给出不准确的分析。AI 提供的意见不代表开发者或其所属机构的意见。所有输出仅供参考；最终的投资或投票决定必须核对原始披露，并经过专业人员审查。

---

## 许可证

[PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/)：仅允许非商业用途，完整文本见根目录中的 [`LICENSE`](LICENSE)。

- **非商业用途**：允许个人研究、学习、非营利组织和公共机构自由使用。
- **商业用途**：需要另行签署许可协议（OpenProxy AI）。
- **重新分发时的署名**：须保留 `Copyright (c) 2026 OpenProxy AI (https://github.com/MarcoYou/open-proxy-mcp)`（PolyForm “Notices”条款）。

> 商业许可及其他咨询：gunhoqw20@gmail.com
