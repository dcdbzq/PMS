"""Read-only 100-client regression; stale QA credentials are skipped, never reset."""
import argparse
import json
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor
from server_acceptance import Client,Metrics,read_admin

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--admin-file',required=True);p.add_argument('--accounts-file',required=True)
    p.add_argument('--release-accounts-file',required=True);p.add_argument('--out',required=True)
    args=p.parse_args();out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    info=read_admin(args.admin_file);url=info['url'];metrics=Metrics()
    admin=Client(url);state=admin.login(info['username'],info['password'])
    active={u['id'] for u in state['users'] if u['active']}
    cohort=json.loads(Path(args.accounts_file).read_text(encoding='utf-8'))
    release=json.loads(Path(args.release_accounts_file).read_text(encoding='utf-8'))
    accounts=cohort['accounts']+[release['accounts']['member'],release['accounts']['second']]
    clients=[];skipped=0;ids=set()
    for account in accounts:
        if account['id'] not in active:
            skipped+=1;continue
        c=Client(url,metrics,'load-login',timeout=90)
        try:c.login(account['username'],account['password'])
        except AssertionError as error:
            if 'HTTP 401' in str(error):skipped+=1;continue
            raise
        clients.append(c);ids.add(account['id'])
        if len(clients)==100:break
    assert len(clients)==100,(len(clients),skipped)
    print(f'Authenticated {len(clients)} distinct clients; skipped stale/disabled accounts: {skipped}',flush=True)
    passed=False
    try:
        for n in (10,25,50,100):
            begin=threading.Event()
            def worker(client):
                begin.wait()
                for _ in range(3):client.request('GET','/api/state')
            for c in clients[:n]:c.phase=f'read-{n}'
            with ThreadPoolExecutor(max_workers=n) as pool:
                tasks=[pool.submit(worker,c) for c in clients[:n]];begin.set()
                for task in tasks:task.result()
            summary=next(r for r in metrics.summary() if r['phase']==f'read-{n}' and r['path']=='/api/state')
            print(json.dumps(summary,ensure_ascii=False),flush=True)
        passed=True
    finally:
        summaries=metrics.summary()
        hundred=next((r for r in summaries if r['phase']=='read-100' and r['path']=='/api/state'),{})
        result=dict(completed=passed,distinct_users=len(ids),skipped_stale_or_disabled_accounts=skipped,
            request_errors=sum(r['errors'] for r in summaries if r['phase'].startswith('read-')),
            target_read_p95_ms=2000,read_100_p95_ms=hundred.get('p95_ms'),
            target_passed=bool(hundred) and hundred['p95_ms']<=2000,metrics=summaries)
        (out/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
