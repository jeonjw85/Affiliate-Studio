"""Opt-in live local model probe. No production data, WP or public writes."""
import json
import tempfile
from pathlib import Path
import time
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from studio.ai import LocalAI
from studio.core import Store, now

started=time.monotonic()
with tempfile.TemporaryDirectory(prefix='studio-v04-real-ai-') as temporary:
    s=Store(Path(temporary)/'test.db')
    p=s.add_product(dict(provider='coupang',name='합성 예시 노트북 거치대',source_url='https://example.com/product',affiliate_url='https://example.com/affiliate',price=None,observed_at=now().isoformat(),facts=['접이식 구조','각도 조절 가능'],audience='외장 키보드로 문서를 작성하는 사람',caveats='무게, 지원 노트북 크기와 타이핑 안정성은 아직 확인하지 않았습니다.',ownership_confirmed=True))
    d=s.create_draft(p['id'])
    ai=LocalAI('http://127.0.0.1:18086/v1','qwen3:4b-instruct-2507-q4_K_M',timeout=240)
    candidate=s.generate_ai(d['id'],1,'guide',True,ai)
    assert candidate['body_format']=='markdown-v1'
    assert s.detail(d['id'])['draft']['version']==1
    applied=s.apply_candidate(d['id'],candidate['id'],1)
    assert applied['approved_at'] is None and applied['version']==2
    detail=s.detail(d['id'])
    assert '<h2>' in detail['preview']['html']
    assert not detail['checks']['errors'], detail['checks']
    report=dict(scope='Actual local Qwen3 4B Instruct 2507; synthetic manual facts; temporary SQLite; no WP or publication; human approval still required.',model=candidate['model'],body=candidate['body'],evidence_refs=candidate['evidence_refs'],html=detail['preview']['html'],review=detail['checks'],elapsed_seconds=round(time.monotonic()-started,2),passed=True)
    target=Path(__file__).resolve().parents[1]/'artifacts/v04-live-ai.json'
    target.parent.mkdir(exist_ok=True);target.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))
