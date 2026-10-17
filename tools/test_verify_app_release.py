#!/usr/bin/env python3
import io
import json
import zipfile
from verify_app_release import verify, PayloadError
sha='a'*40
record={'service':'epilykos','image':'irunmole/epilykos','digest':'sha256:'+'b'*64,'ref':'dev','commit':sha,'version':'1.0.0'}
payload={'ref':'dev','commit':sha,'records':[record]}
buf=io.BytesIO()
with zipfile.ZipFile(buf,'w') as z:z.writestr('digest-epilykos.json',json.dumps(record))
responses={'branches/dev':{'commit':{'sha':sha}},f'actions/workflows/docker-build-dev.yml/runs?head_sha={sha}&per_page=20':{'workflow_runs':[{'id':1,'head_sha':sha,'head_branch':'dev','event':'push'}]},'actions/runs/1/jobs?per_page=100':{'jobs':[{'name':'build-and-push (epilykos)','conclusion':'success'}]},'actions/runs/1/artifacts?per_page=100':{'artifacts':[{'id':2,'name':'image-digest-epilykos','expired':False}]},'actions/artifacts/2/zip':buf.getvalue()}
def fetch(p):
    r=responses[p];return r if isinstance(r,bytes) else json.dumps(r).encode()
assert verify(payload,fetch)==1
run=responses[f'actions/workflows/docker-build-dev.yml/runs?head_sha={sha}&per_page=20']['workflow_runs'][0]
for event in ('schedule','workflow_dispatch'):
    run['event']=event;assert verify(payload,fetch)==1
run['event']='push'
for mode in ('stale','failed','digest','expired'):
    if mode=='stale':responses['branches/dev']['commit']['sha']='c'*40
    elif mode=='failed':responses['actions/runs/1/jobs?per_page=100']['jobs'][0]['conclusion']='failure'
    elif mode=='digest':record['digest']='sha256:'+'d'*64
    elif mode=='expired':responses['actions/runs/1/artifacts?per_page=100']['artifacts'][0]['expired']=True
    try:verify(payload,fetch)
    except PayloadError:pass
    else:raise AssertionError(mode)
    responses['branches/dev']['commit']['sha']=sha
    responses['actions/runs/1/jobs?per_page=100']['jobs'][0]['conclusion']='success'
    record['digest']='sha256:'+'b'*64
print('PASS source release authentication: real artifact match; stale, failed, changed digest and expired artifact rejected')
