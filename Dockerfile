FROM python:3.12-slim

WORKDIR /app

# uv.lock 을 함께 복사해 **로컬과 같은 버전**을 설치한다.
# lock 없이 `pip install .` 로 범위를 다시 풀면, 의존성이 새 버전을 내놓은 날 코드 변경과
# 무관하게 배포만 깨진다(260729 실측: mcp 2.0.0 이 fastmcp 제거 → 운영 중단).
COPY pyproject.toml uv.lock ./
COPY open_proxy_mcp/ open_proxy_mcp/
# 260814: 규칙 데이터(룰 40 · 조항 대장 SSOT · 동의어 사전)는
#   `open_proxy_mcp/data/laws/` 로 옮겨 **코드와 함께** 배포된다 — 여기서 챙길 필요가 없다.
#   종전에는 이 COPY 한 줄이 빠지면 룰 40개가 조용히 0이 되고 강행규정 판정이 통째로
#   사라졌다(경고·로그·헬스체크 전무). 이제 로더가 로그를 남기고 /health 가 개수를 싣는다.
#
# 아래는 **corpus 만** 남긴다 — 법령 원문+인덱스 11MB 라 휠에 싣지 않는다.
#   law_lookup 이 조문 전문을 돌려줄 때 쓴다. 없으면 조문 검색이 빈 결과가 되므로
#   production 필수이고, 빠지면 /health 의 law_corpus_articles 가 0으로 보인다.
COPY wiki/rules/laws/corpus/ wiki/rules/laws/corpus/

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv
# --locked: lock 과 pyproject 가 어긋나면 **빌드를 실패시킨다**(조용히 다른 버전 설치 금지)
# --no-dev: 테스트 의존성 제외 · --no-editable: 소스 링크 대신 실제 설치
RUN uv sync --locked --no-dev --no-editable && uv cache clean
# 선택 확장 (260906) — 빌드 시크릿 `opm_ext_spec` 에 설치 스펙(pip 가 받는 문자열)이 있으면 설치하고,
# 없으면 건너뛴다. 레포를 클론한 사람은 시크릿이 없으니 훅이 빈 서버를 그대로 빌드한다.
# 🔴 BuildKit 은 시크릿 내용을 캐시 키에 넣지 않는다 — 시크릿 없이 빌드한 층이 그대로 재사용돼
#   확장이 **조용히 빠진다**(260906 원격 빌드 실측: 시크릿을 줘도 `#14 CACHED`). 그래서 스펙의 해시를
#   build-arg 로 함께 준다. 해시는 비밀이 아니고, 값이 바뀌면 이 층부터 다시 돈다.
#   fly deploy --build-secret opm_ext_spec="$SPEC" --build-arg OPM_EXT_REV="$(printf %s "$SPEC" | shasum -a 256 | cut -c1-16)"
# slim 이미지엔 git 이 없다 — git+https 스펙이면 같은 층 안에서 잠깐 깔고 지운다(이미지 크기 유지).
# 런타임 환경에는 토큰이 남지 않는다(빌드 시크릿은 이 RUN 안에서만 마운트).
ARG OPM_EXT_REV=none
RUN --mount=type=secret,id=opm_ext_spec \
    echo "opm_ext_rev=${OPM_EXT_REV}" && \
    if [ -s /run/secrets/opm_ext_spec ]; then \
        apt-get update -qq && apt-get install -y -qq --no-install-recommends git >/dev/null \
        && uv pip install --python /app/.venv/bin/python --no-cache "$(cat /run/secrets/opm_ext_spec)" \
        && apt-get purge -y -qq git >/dev/null && apt-get autoremove -y -qq >/dev/null && rm -rf /var/lib/apt/lists/* \
        && /app/.venv/bin/python -c "from importlib.metadata import entry_points as e; n=[x.name for x in e(group='open_proxy_mcp.extensions')]; assert n, 'extension installed but no entry point'; print('extensions:', n)"; \
    fi
RUN useradd -r -s /bin/false appuser
USER appuser

EXPOSE 8000

CMD ["/app/.venv/bin/python", "-m", "open_proxy_mcp.server", "--transport", "streamable-http"]
