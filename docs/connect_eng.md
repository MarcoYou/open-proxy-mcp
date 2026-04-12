# Local Installation Guide

Local installation lets you configure additional API keys beyond DART (news search, OCR fallback, etc.).

---

## 1. Clone and install

```bash
git clone https://github.com/MarcoYou/open-proxy-mcp.git
cd open-proxy-mcp
uv sync                    # Creates .venv + installs all dependencies
cp .env.example .env
```

## 2. Configure environment

Edit `.env` and add your API keys. **Only `OPENDART_API_KEY` is required** -- all core features work with it alone.

```bash
# .env (required)
OPENDART_API_KEY=your_key_here

# Optional - enables additional features
OPENDART_API_KEY_2=backup_key                      # Auto-switches on rate limit (1,000/min)
NAVER_SEARCH_API_CLIENT_ID=naver_id                # Candidate news search
NAVER_SEARCH_API_CLIENT_SECRET=naver_secret         # Candidate news search
UPSTAGE_API_KEY=upstage_key                         # OCR fallback (Tier 3)
```

| API Key | Required | Where to Get | Purpose |
|---------|----------|-------------|---------|
| `OPENDART_API_KEY` | **Yes** | [DART OpenAPI](https://opendart.fss.or.kr/) -> Sign up -> Request API key | AGM/OWN/DIV (all core) |
| `OPENDART_API_KEY_2` | No | Same (backup key) | Auto-switches on rate limit (1,000/min) |
| `NAVER_SEARCH_API_CLIENT_ID` | No | [Naver Developers](https://developers.naver.com/) -> Register app -> Search API | Candidate news search |
| `NAVER_SEARCH_API_CLIENT_SECRET` | No | Same | Same |
| `UPSTAGE_API_KEY` | No | [Upstage AI](https://www.upstage.ai/) -> Sign up -> API key | OCR fallback (Tier 3) |

## 3. Editable install

```bash
uv pip install -e .
```

## 4. Connect to Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "open-proxy-mcp": {
      "command": "/path/to/open-proxy-mcp/.venv/bin/python",
      "args": ["-m", "open_proxy_mcp"],
      "cwd": "/path/to/open-proxy-mcp"
    }
  }
}
```

## 5. Connect to Claude Code

```json
// .mcp.json (project root)
{
  "mcpServers": {
    "open-proxy-mcp": {
      "command": "/path/to/open-proxy-mcp/.venv/bin/python",
      "args": ["-m", "open_proxy_mcp"],
      "cwd": "/path/to/open-proxy-mcp"
    }
  }
}
```

## 6. Optional dependencies

```bash
uv pip install -e ".[pdf]"               # + PDF/OCR fallback
uv pip install -e ".[llm]"               # + LLM fallback (Claude/OpenAI)
uv pip install -e ".[all]"               # Everything
```

## Fallback Parsing

AGM parsers resolve most filings at the XML tier (97%+ accuracy across KOSPI 200). PDF and OCR fallbacks are available for non-standard formats. PDF/OCR fallback works in local installations only.

```
_xml (DART API, free, <1s)  <- most filings complete here
  ↓ non-standard format
_pdf (PDF download, free, 4s+)
  ↓ still failing
_ocr (Upstage OCR API, paid, 8s+)  <- 100% accuracy
```

The AI evaluates result quality and autonomously escalates to the next tier. No user intervention required.

<details>
<summary>KOSPI 200 parser accuracy (click to expand)</summary>

| Parser | XML | PDF | OCR |
|--------|-----|-----|-----|
| Agenda list | 99.5% | 98.0% | 100% |
| Balance Sheet | 97.4% | 97.9% | 100% |
| Income Statement | 100% | 95.7% | 100% |
| Director/Auditor | 98.9% | 97.9% | 100% |
| Articles of Incorporation | 97.8% | 99.0% | 100% |
| Compensation | 98.4% | 99.5% | 100% |
| Treasury Shares | 93.6% | 100% | 100% |
| Capital Reserve | 100% | 100% | 100% |
| Retirement Pay | 93.3% | 86.7% | 86.7% |

</details>
