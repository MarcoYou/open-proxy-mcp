# 연결 가이드 / Connection Guide

OpenProxy MCP를 Claude Desktop, Claude Code에서 연결하는 방법입니다.

---

## 원격 서버 (Remote Server)

### Claude Desktop

설정 > MCP 서버 추가 > URL 커넥터:

```
https://open-proxy-mcp.fly.dev/mcp?opendart=발급받은_키
```

### Claude Code

```bash
claude mcp add open-proxy-mcp --transport streamable-http "https://open-proxy-mcp.fly.dev/mcp?opendart=발급받은_키"
```

---

## 로컬 설치 (Local Installation)

로컬에서 실행하면 DART 외에 추가 API 키도 설정할 수 있습니다 (후보자 뉴스 검색, OCR fallback 등).
설치 방법은 [README](../README.md)의 "방법 B: 로컬 설치" 섹션을 참조하세요.

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`에 추가:

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

### Claude Code

```json
// .mcp.json (프로젝트 루트)
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
