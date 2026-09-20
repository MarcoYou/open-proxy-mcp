#!/usr/bin/env python3
"""파싱 실험용 DART 문서 응답만 누락분 수집한다. 정답·파서 결과는 만들지 않는다."""
from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def read_boundary(path: Path) -> bytes:
    raw = gzip.decompress(path.read_bytes()) if path.name.endswith('.gz') else path.read_bytes()
    doc = json.loads(raw)
    if not isinstance(doc, dict) or any(not isinstance(doc.get(k), str) or not doc[k].strip()
                                        for k in ('html', 'text')):
        raise ValueError('invalid_document_boundary')
    return raw


def locate(root: Path, receipt: str) -> Path | None:
    paths = [root / (receipt + ext) for ext in ('.json', '.json.gz')]
    found = [p for p in paths if p.exists() or p.is_symlink()]
    if len(found) > 1:
        raise ValueError('duplicate_document_boundary')
    return found[0] if found else None


async def collect(receipts: list[str], destination: Path, reuse: Path, *, fetch=None,
                  sleep=asyncio.sleep) -> dict:
    """한 프로세스에서 순차 수집. 예외는 원문 메시지를 출력하지 않고 즉시 중단한다."""
    if not 1 <= len(receipts) <= 30 or len(set(receipts)) != len(receipts):
        raise ValueError('receipt_count_or_duplicate')
    if any(not re.fullmatch(r'[0-9]{14}', r) for r in receipts):
        raise ValueError('invalid_receipt')
    destination = destination.resolve()
    if destination == Path(destination.anchor) or destination.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError('private_destination_required')
    destination.mkdir(parents=True, exist_ok=True)
    # 같은 데이터셋을 동시에 수집하지 않는다. 파일은 남아도 잠금은 프로세스 종료시 해제된다.
    import fcntl
    report = {'aborted': False, 'requested': len(receipts), 'fetch_attempts': 0, 'documents': []}
    client = None
    with (destination / '.collection.lock').open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('collection_already_running') from None
        try:
            for receipt in receipts:
                try:
                    target = locate(destination, receipt)
                    existing = target or locate(reuse, receipt)
                    if existing:
                        raw = read_boundary(existing)
                        origin = 'existing' if target else 'reused'
                    else:
                        if fetch is None and client is None:
                            from open_proxy_mcp.dart.client import DartClient
                            key = os.getenv('OPENDART_API_KEY')
                            if not key:
                                raise ValueError('missing_key')
                            client = DartClient(api_keys=[key])  # 제한 때 다른 키로 회전하지 않는다.
                        report['fetch_attempts'] += 1
                        try:
                            # 입력 경계는 get_document_cached의 반환과 같은 완전한 문서 응답이다.
                            doc = await (fetch(receipt) if fetch else client.get_document_cached(receipt))
                        finally:
                            await sleep(1.1)
                        if not isinstance(doc, dict) or any(not isinstance(doc.get(k), str) or not doc[k].strip()
                                                            for k in ('html', 'text')):
                            raise ValueError('invalid_document_boundary')
                        raw = json.dumps(doc, ensure_ascii=False).encode('utf-8')
                        origin = 'requested'
                    if not target:
                        # x 모드: 이전 표본은 덮어쓰지 않는다. 기존 gzip/평문도 위에서 중복 검사한다.
                        with (destination / (receipt + '.json')).open('xb') as stream:
                            stream.write(raw)
                    report['documents'].append({'rcept_no': receipt, 'origin': origin,
                                                'source_sha256': hashlib.sha256(raw).hexdigest()})
                except Exception as exc:
                    # HTTP 예외의 URL·키·메시지나 원문을 절대로 직렬화하지 않는다.
                    status = getattr(exc, 'status', None)
                    report['aborted'] = True
                    report['error'] = {'rcept_no': receipt, 'kind': type(exc).__name__,
                                       'status': status if isinstance(status, str) and re.fullmatch(r'\d{3}', status) else None}
                    break
        finally:
            if client is not None:
                await client._http.aclose()
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('receipts', nargs='+', help='접수번호. 한 번에 최대 30건, 누락분만 요청')
    ap.add_argument('--destination', type=Path, required=True, help='공개 저장소 밖의 실험 원문 폴더')
    ap.add_argument('--env-file', type=Path, help='키가 이미 있는 환경 파일을 읽기만 함. 복사하지 않음')
    ap.add_argument('--reuse-cache', type=Path, default=Path(os.getenv('OPM_DOC_CACHE_DIR') or Path(tempfile.gettempdir()) / 'opm_cache'))
    args = ap.parse_args()
    # URL을 포함할 수 있는 HTTP 라이브러리 로그를 이 수집 프로세스에서만 차단한다.
    logging.disable(logging.CRITICAL)
    try:
        if args.env_file:
            from dotenv import load_dotenv
            if not args.env_file.is_file():
                raise ValueError('missing_env_file')
            load_dotenv(args.env_file, override=False)
        report = asyncio.run(collect(args.receipts, args.destination, args.reuse_cache))
    except Exception as exc:
        print(json.dumps({'aborted': True, 'kind': type(exc).__name__}))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report['aborted'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
