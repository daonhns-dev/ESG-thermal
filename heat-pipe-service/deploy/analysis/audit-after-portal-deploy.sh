#!/usr/bin/env bash
# Read-only deployment audit. No jobs, restarts, config changes, or large source hashes.
set -uo pipefail
echo '=== CONTAINERS ==='
docker ps -a --format 'table {{.Names}}\t{{.Status}}'
echo '=== IMAGES AND MOUNTS ==='
docker ps -q | xargs -r docker inspect --format '{{.Name}} image={{.Image}} restart={{.HostConfig.RestartPolicy.Name}}{{range .Mounts}}{{println}}{{.Source}} -> {{.Destination}} rw={{.RW}}{{end}}'
echo '=== SHARED ROOT IN BOTH SERVICES ==='
for container in heatpipe-service-api-1 heatpipe-service-worker-1; do
    echo "$container"
    docker exec "$container" python -c 'import os; from pathlib import Path; from heatpipe_service.storage.shared_inputs import validate_shared_root; p=os.environ.get("HEATPIPE_SHARED_INPUT_ROOT",""); assert p=="/shared-input"; validate_shared_root(Path(p)); print("PASS: shared root configured and marker readable")'
done
echo '=== WORKER TO API ==='
docker exec heatpipe-service-worker-1 python -c 'import urllib.request; print(urllib.request.urlopen("http://daon-api:8000/health",timeout=5).read().decode())'
echo '=== DATABASE AND PATH RESULTS ==='
docker exec -i heatpipe-service-api-1 python - <<'PY'
import hashlib,json,os,sqlite3
from pathlib import Path
from urllib.error import HTTPError
import urllib.request
from heatpipe_service.security import redact_url_query

with sqlite3.connect('file:/data/jobs.sqlite3?mode=ro',uri=True) as db:
    db.row_factory=sqlite3.Row
    print('DB_QUICK_CHECK=',db.execute('pragma quick_check').fetchall()[0][0])
    print('DB_BASELINE=',tuple(db.execute('select count(*),max(id),max(job_id) from jobs').fetchone()))
    print('JOB_COUNTS=',[tuple(r) for r in db.execute('select status,count(*) from jobs group by status')])
    rows=db.execute('select job_id,status,progress_percent,error_code,error_message,request_json,created_at,processed_frame_count,total_frame_count from jobs order by id desc limit 200').fetchall()
recent=[]; path_rows=[]
for row in rows:
    body=json.loads(row['request_json'])
    modes={k:'path' if body[k].get('path') is not None else 'url' if body[k].get('url') is not None else 'object_key' for k in ('att','atg')}
    item={k:row[k] for k in ('job_id','status','progress_percent','error_code','created_at','processed_frame_count','total_frame_count')}
    item['modes']=modes
    if row['error_message']:
        item['error_message']=redact_url_query(row['error_message'])[:250]
    if len(recent)<8: recent.append(item)
    if 'path' in modes.values(): path_rows.append((row,body))
print('RECENT_JOBS=',json.dumps(recent,ensure_ascii=False))
print('PATH_JOBS_IN_LAST_200=',len(path_rows))

def get(path):
    assert path.startswith('/api/v1/analysis-jobs/')
    req=urllib.request.Request('http://127.0.0.1:8000'+path,headers={'Authorization':'Bearer '+os.environ['HEATPIPE_API_TOKEN']})
    with urllib.request.urlopen(req,timeout=10) as response:
        return response.read(),response.headers.get_content_type()

if path_rows:
    latest,body=path_rows[0]
    root=Path('/shared-input').resolve()
    for kind in ('att','atg'):
        relative=body[kind].get('path')
        if relative is None: continue
        try:
            source=(root/relative).resolve()
            assert source.is_relative_to(root) and source.is_file()
            size=source.stat().st_size
            assert size==body[kind]['size_bytes']
            with source.open('rb') as stream: stream.read(1)
            print('LATEST_PATH_SOURCE=',json.dumps({'kind':kind,'exists':True,'readable':True,'size_match':True,'size_bytes':size}))
        except Exception as error:
            print('LATEST_PATH_SOURCE=',json.dumps({'kind':kind,'check':'FAILED','error_type':type(error).__name__}))

completed=[r for r,body in path_rows if r['status']=='SUCCEEDED']
if not completed:
    print('PATH_E2E_NOT_YET_VERIFIED: no completed path job in last 200 records')
else:
    job=completed[0]['job_id']
    try:
        result=json.loads(get('/api/v1/analysis-jobs/'+job+'/results')[0])
        for candidate in result['candidates']:
            metadata=candidate['result_image']; png,mime=get(metadata['path'])
            assert mime=='image/png' and png.startswith(b'\x89PNG\r\n\x1a\n')
            assert len(png)==metadata['size_bytes']
            assert hashlib.sha256(png).hexdigest()==metadata['sha256']
        print('PATH_RESULT_VERIFIED=',json.dumps({'job_id':job,'candidates':len(result['candidates']),'pngs_verified':len(result['candidates'])}))
    except HTTPError as error:
        print('PATH_RESULT_FAILED=',json.dumps({'job_id':job,'http_status':error.code}))
    except Exception as error:
        print('PATH_RESULT_FAILED=',json.dumps({'job_id':job,'error_type':type(error).__name__}))

if rows:
    try:
        urllib.request.urlopen('http://127.0.0.1:8000/api/v1/analysis-jobs/'+rows[0]['job_id'],timeout=5)
        print('AUTH_CHECK_FAILED: protected endpoint accepted no token')
    except HTTPError as error:
        print('AUTH_CHECK=',error.code,'expected=401')
PY
echo '=== STORAGE ==='
findmnt /data
df -hT / /data
echo '=== PUBLIC ENDPOINTS ==='
curl -fsS --max-time 15 https://heatpipe.daonhns.com/health
echo
curl -sS -o /dev/null -w 'PORTAL_HTTP=%{http_code}\n' --max-time 15 https://heatpipe-portal.daonhns.com/
echo 'READ_ONLY_AUDIT_FINISHED: assess each result; this is not an automatic all-clear'
