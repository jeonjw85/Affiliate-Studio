"""Open DBs made by real archived versions; no production DB or remote calls."""
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from studio.core import Store


def verify(version):
    with tempfile.TemporaryDirectory(prefix='studio-upgrade-') as temp:
        root=Path(temp)
        with zipfile.ZipFile(ROOT/f'dist/affiliate-studio-{version}.zip') as z:
            z.extractall(root)
        old=root/'affiliate-studio';db=root/'upgrade.sqlite3'
        code='''import json,sys
from datetime import timedelta
import studio.core as core
s=core.Store(sys.argv[1])
p=s.add_product(dict(provider="coupang",name="업그레이드 합성 상품",source_url="https://example.com/item",affiliate_url="https://example.com/a",price=None,observed_at=core.now().isoformat(),facts=["USB 유선 연결"],audience="문서 작성자",caveats="시험 데이터",ownership_confirmed=True))
d=s.create_draft(p["id"]);s.approve(d["id"],1,True)
j=s.schedule(d["id"],1,(core.now()+timedelta(hours=1)).isoformat())
# Explicit synthetic terminal-state records exercise raw storage compatibility,
# not real AI generation, WordPress publication, or current remote readback.
if sys.argv[2]=="0.2.0":
    with s.connect() as c:
        for kind,state in (("candidate","ready"),("operation","completed"),("delivery","sent"),("publication","future")):
            s.put(c,kind,dict(id="upgrade-"+kind,draft_id=d["id"],version=1,state=state,created_at=core.now().isoformat(),fixture=True))
with s.connect() as c:
    raw=[list(row) for row in c.execute("SELECT kind,id,data FROM records ORDER BY kind,id")]
print(json.dumps(dict(module=core.__file__,product=p["id"],draft=d["id"],job=j["id"],raw=raw,html=s.detail(d["id"])["preview"]["html"])))
'''
        result=subprocess.run([sys.executable,'-c',code,str(db),version],cwd=old,env={**os.environ,'PYTHONPATH':str(old)},capture_output=True,text=True)
        assert result.returncode==0,result.stderr
        previous=json.loads(result.stdout);assert Path(previous['module']).is_relative_to(old)
        current=Store(db);state=current.state();detail=current.detail(previous['draft'])
        assert detail['preview']['html']==previous['html']
        assert detail['draft'].get('body_format','plain')==('markdown-v1' if version=='0.4.0' else 'plain')
        assert state['products'][0]['id']==previous['product']
        assert detail['draft']['status']=='approved' and detail['draft']['version']==1
        assert state['jobs'][0]['id']==previous['job'] and state['jobs'][0]['state']=='pending'
        with current.connect() as c:
            assert [list(row) for row in c.execute('SELECT kind,id,data FROM records ORDER BY kind,id')]==previous['raw']
            assert current.rows(c,'measurement')==[]
        return dict(from_version=version,archived_code_executed=True,existing_records_byte_preserved=True,html_byte_preserved=True,body_format=detail['draft'].get('body_format','plain'),product_preserved=True,approved_version_preserved=True,pending_zip_job_preserved=True,synthetic_v02_terminal_records_preserved=version=='0.2.0',new_measurements_empty=True,passed=True)


def main():
    report={'versions':[verify(v) for v in ('0.1.1','0.2.0','0.3.0','0.4.0')],'passed':True}
    (ROOT/'artifacts').mkdir(exist_ok=True)
    (ROOT/'artifacts/upgrade-v041-report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
