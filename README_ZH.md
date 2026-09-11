# OpenProxy MCP

[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-lightgrey.svg)](https://polyformproject.org/licenses/noncommercial/1.0.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/tools-32-orange.svg)](#工具结构32-项工具)
[![Release](https://img.shields.io/badge/release-v2.6.0-blue.svg)](docs/RELEASE_NOTES_ENG.md)
[![Sponsor](https://img.shields.io/badge/Sponsor-%E2%9D%A4-ea4aaa?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/MarcoYou)

[한국어](README.md) · [English](README_ENG.md)

[快速开始](#快速开始) · [可以这样提问](#可以这样提问) · [主要功能](#主要功能) · [工具结构](#工具结构32-项工具) · [阅读注意](#阅读结果时的注意事项) · [数据来源](#数据来源)

## 为什么选择 OpenProxy？

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="screenshot/opm-readme-particle-flow-dark-en-20260906.png">
  <source media="(prefers-color-scheme: light)" srcset="screenshot/opm-readme-particle-flow-light-en-20260906.png">
  <img alt="将公司披露数据转化为 AI 财务分析与投票建议的过程" src="screenshot/opm-readme-particle-flow-light-en-20260906.png">
</picture>

**一项议案也许只有一行，但可靠的判断需要完整的信息。**

OpenProxy 分析**韩国上市公司**的监管披露 —— 即企业向韩国电子披露系统 DART 提交的公告。它最初用于股东大会和代理投票分析；为了综合阅读财务报表、股权结构、分红记录、董事会信息和韩国相关法律而开发的功能，逐步扩展为面向这些披露的通用分析引擎。从财务分析到投票建议，AI 会同时给出结论及其原始依据。

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

#### 在 Claude 中打开提示词与资源

点击聊天输入框中的 `+`，然后选择 `Connectors → Add from open-proxy-mcp`。

[查看 Claude 英文界面中的菜单位置](screenshot/claude-resource-picker-en-20260907.png)

- `Company Snapshot`（公司概览）— 根据公司名称或股票代码启动分析流程的 MCP prompt
- `OpenProxy Feature Guide`（功能指南）— 列出当前已注册工具与功能的 `tools_guide` resource
- `Open Proxy Guideline` — 代理投票分析使用的政策原文

### 3. 发送第一个问题

可以先问：`Show Samsung Electronics' company information and three recent filings.` 如果返回公司信息和披露列表，说明连接成功。之后可以继续使用自然语言提问，无需记住工具名称。

按主题分类的示例问题汇总在下方的[可以这样提问](#可以这样提问)。各工具的详细参数见[工具目录](wiki/tools/README.md)。

`OpenProxy Feature Guide` 会根据服务器注册的工具自动生成。`Company Snapshot` 会引导 AI 汇总业务结构、三年实际业绩、最多两年的年度一致预期、估值、股权、分红、近期披露以及后续应核实的问题。表格会区分实际值（A）与预期值（E）；支持可视化的客户端还会生成营收柱状图和营业利润折线图。

---

## 可以这样提问

不需要记住工具名称。把想问的话直接说出来，AI 会自行选择工具。
以下都是实际可用的问题。

<details open>
<summary><b>🗳️ 想了解股东大会与投票</b></summary>

> - 请审阅 LG 化学下次股东大会的议案，并逐项给出有依据的投票意见。
> - Kakao 本次董事选举，每位候选人需要关注什么？
> - 三星电子去年的议案实际表决结果如何？
> - 找出判断该议案所依据的章程条款与商法条文。

每个议案都会返回**赞成／反对／需要审查**的意见，并附披露、政策与法律依据。不属于表决范围的议案
和资料不足的议案会分开标示。反对为零的公司属于正常情况 — [原因见此](#阅读结果时的注意事项)

</details>

<details open>
<summary><b>📊 想了解业绩</b></summary>

> - 对比三星电子最近三年的业绩与未来两年的一致预期。
> - 展示 SK 海力士的季度业绩趋势，并包含经营现金流。
> - 现代汽车是否已发布暂定业绩？与确定值有何差异？
> - 用杜邦分析拆解 LG 化学的盈利能力。

确定业绩来自 DART 财务 API，暂定值来自暂定业绩披露，预期值来自分析师一致预期。
**三者口径不同，不会并列在同一行** — 表格中以确定（A）与预期（E）分别标示。

</details>

<details>
<summary><b>💹 想了解当前价格</b></summary>

> - Naver 的 PER 与 PBR 相对自身历史处于什么水平？
> - 用未来两年的预期值计算 POSCO 控股的前瞻 PER。
> - 三星电子在 2024 年末时点的估值如何？

市值、PER、PBR、PSR 与股息率都会附上历史区间。**业绩基准日与股价基准日分别标示** — 二者并非同一天。

</details>

<details>
<summary><b>🏭 想了解靠什么赚钱</b></summary>

> - 韩华思路信靠什么赚钱？请按业务板块拆分。
> - 展示高丽亚铅的开工率与原材料价格走势。
> - 计算泰光实业持有的关联公司股权与闲置资产价值。
> - 这家公司还有多少在手订单？

按板块、产品、地区划分的营收，是**对同一笔营收的不同切分方式，不做加总。**
持股按上市部分市值与非上市部分账面价值分别计算。

</details>

<details>
<summary><b>🧭 想了解股权与股东回报</b></summary>

> - 三星物产的最大股东与关联方持股情况如何？
> - 展示 KB 金融最近三年的分红走势与分红率。
> - 宣布回购的公司里，哪些真正完成了注销？
> - 企业价值提升计划的披露内容与实际执行相差多少？

分红有**两种不同口径。** 每股分红与股息率是**按每一类股份的单股**计算，
分红总额与分红率则是**公司整体**（各类股份合计、合并口径） — [详见](#阅读结果时的注意事项)

</details>

<details>
<summary><b>⚔️ 想了解发生了什么变化</b></summary>

> - 整理今天早上值得关注的披露。
> - 最近有哪些公司出现了控制权争夺信号？
> - 展示斗山机器人的增资与可转债发行记录。
> - 这家公司是否有诉讼或处罚记录？

全市场扫描**最多回溯三个月。** 超出该范围时不会声称“没有发生过”。

</details>

<details>
<summary><b>🔗 想核实依据</b></summary>

> - 刚才那个数字出自哪份披露的第几项？
> - 这个受理编号的原文在哪里可以查看？
> - 章程的这一条对应商法第几条？是否为强制性规定？

每个数字都附带受理编号，受理编号可转换为 DART 原文查看地址。章程与法律条文支持双向查询，
且该查询不消耗 DART API 调用。

</details>

### 值得知道的几点

- **公司只需确认一次** — 首次查询得到的名称、股票代码与公司编号会沿用到后续提问。
- **名称重复时会先确认** — 存在多个候选时，在确定之前不会进行后续查询。
- **请先阅读响应中的 `status` 与 `warnings`** — 其中写明了缺失的内容以及替代所用的口径。
- **未取得的值表示“在所读披露中未找到”** — 既不是 0，也不是“不存在”。

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

这六类分析流程由 **32 项工具**支持，其中包括来源追踪，以及公司章程与法律条文之间的双向查询。完整列表见[工具结构](#工具结构32-项工具)。

**治理审查试点** — `governance_screen` 会汇总最多 30 家指定公司的披露原文，由所连接的 AI 判读小股东待遇、利益冲突与董事会问责等方面，并依据依据强度与重要性整理审查顺序。要约收购、股东行动主义与诉讼本身不构成负面评价。结果标示为**基于部分依据的 LLM 评估 · 未经人工复核**；出现缺失时只就该项告知，其余审查继续进行。可通过 `since` 与 `known_receipts` 缩小新披露范围后再次调用。不会创建定时执行或实际投票。该功能为本分支的试点实现，是否已部署到生产环境需另行确认。

---

## 工具结构（32 项工具）

分类与 [wiki/tools 工具目录](wiki/tools/README.md)中的“想了解什么 → 使用哪个工具”表保持一致，该表是工具分类的唯一权威来源。

| 分类 | 工具 | 作用 |
|---|---|---|
| 🏢 起点：查找公司 | [`company`](wiki/tools/company.md) | 识别公司并列出近期披露，是所有分析的起点 |
| 🔔 披露扫描与审查 | [`screener`](wiki/tools/screener.md)、[`governance_screen`](wiki/tools/governance_screen.md) | 全市场披露摘要 · 指定公司的披露原文与 LLM 治理审查顺序（试点） |
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

## 阅读结果时的注意事项

披露几乎每一项都有各自的口径。不了解以下十一点，就会把正确的数字读错。

| 内容 | 原因 |
|---|---|
| **“无资料”不等于“不存在”** | 它表示在所读的披露中未找到。响应的 `status` 与 `warnings` 会说明缺失了什么、用什么替代 |
| **反对为零属于正常** | 政策中的反对标准并不等同于引擎的自动反对条件。需要进一步判断的疑虑会保留为**需要审查（REVIEW）** |
| **分红有两种不同口径** | 每股分红与股息率按**每一类股份的单股**计算；分红总额与分红率为**公司整体**（各类股份合计、合并口径）。不能相互直接相除 |
| **确定值、暂定值与预期值不放在同一行** | 三者分别来自 DART 财务 API、暂定业绩披露与分析师一致预期。表格中以 A 与 E 区分。暂定值也不会替代全部指标 |
| **请确认合并口径与归属** | 净利润归属（归母与全部）或合并口径不同时，不计算增长率，只标示差异 |
| **会计年度以结算月为准** | 非 12 月结算的公司与日历年度错开。信永证券（001720）的 `2025-06-30` 属于 FY2026-Q1 |
| **金融公司缺少部分指标** | 营业收入与一般企业适用的比率属于**未提供**，而非 0。应改用营业利润、净利润与该行业的稳健性资料 |
| **业绩基准日与股价基准日不同** | 前瞻倍数会分别标注预期基准日与股价基准日。不会把过去的数字当作今天的值 |
| **受理编号前两位标明来源** | 以 `00` 开头为 DART 定期披露（股东大会通知），`80` 为交易所临时披露（股东大会结果） |
| **全市场扫描最多回溯三个月** | 超出该范围不会声称“没有发生过”。指定公司后可查看更长区间 |
| **治理审查未经人工复核** | `governance_screen` 的结果标示为**基于部分依据的 LLM 评估 · 未经人工复核**。要约收购、股东行动主义与诉讼本身不构成负面评价 |

<details>
<summary><b>详细 — 数字与依据</b></summary>

<br>

- **类别股曾覆盖普通股分红**（2026-09-06 已修正）。不含“优先”字样的类别股标示（종류주식、
  1종 종류주식、전환주 等，KOSPI 台账中共 235 行）被旧规则读作普通股。韩国投资控股 FY2024 的
  普通股每股分红 3,980 韩元被输出为类别股的 4,042 韩元，斗山的 2,000 韩元被输出为 2,050 韩元，
  按现价计算的收益率也随之出错。现已由同一个分类器同时服务于期末汇总与多年走势
  → [`dividend_disclosure`](wiki/tools/dividend_disclosure.md)
- **DART 每个 API 密钥每分钟上限为 1,000 次**，超出会导致该密钥被封锁两到三小时。
  服务器在 910 次时自行停止。每个响应都会在 `data.usage` 中带上本次消耗的 DART 调用次数。
- **网页解析由单一进程时钟统一控制** — 请求之间随机间隔 0.4 至 1 秒，每分钟 40 次；
  出现封锁信号后放宽至 1–2 秒。封锁按 IP 生效，整台机器都会受影响。
- **存在两套分类体系** — 行业分类（KSIC）与 DART 披露类型（`pblntf_ty`）。披露检索先按类型过滤，
  未指定公司的全市场检索最多回溯三个月。
- **章程与法律条文查询不消耗 DART API 调用。** 法律原文以韩国国家法令信息中心的资料为基础按周同步
  → [`law_lookup`](wiki/tools/law_lookup.md)
- **不保存用户的查询结果。** 仅保留缓存、市场快照与调用量统计。

</details>

---

## 数据来源

| 来源 | 用途 | 说明 |
|---|---|---|
| [English OpenDART](https://engopendart.fss.or.kr/) | 披露元数据、财务接口、分红、自有股份和股权数据 | **必需**：免费 API 密钥；每分钟最多 1,000 次，服务器安全上限为 910 次 |
| DART 网站（`dart.fss.or.kr`） | 解析披露正文，例如股东大会通知和重大事项报告 | 请求之间随机等待 0.4–1 秒，每分钟 40 次；出现封锁信号后放宽至 1–2 秒 |
| [KRX KIND](https://kind.krx.co.kr/) | 交叉核对交易所披露 | 辅助数据来源 |
| 基于[韩国国家法令信息中心](https://www.law.go.kr/)的法律原文 | 查询章程修订和投票判断所依据的法律 | 每周从 [legalize-kr](https://github.com/legalize-kr/legalize-kr)同步 |
| 主要资产管理公司通过 KRX 披露的投票记录，以及韩国国民年金公布的投票记录 | 投票判断的交叉参考 | 预先收集并结构化的公开资料 |

> OpenProxy 分析的是韩国公司的披露资料。部分原始文件、政策引用和详细工具文档仅提供韩文或英文版本。

---

## 发布说明

版本更新记录见 **[docs/RELEASE_NOTES_ENG.md](docs/RELEASE_NOTES_ENG.md)**。

---

## 安全

漏洞请按 [SECURITY.md](SECURITY.md) 的流程私下报告，**不要开公开 issue** —— 尤其是任何会泄露 API 密钥的路径。

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
