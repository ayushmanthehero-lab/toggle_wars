"""Serve the coordinator and built dashboard; optionally install the controlled demo."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'server'), str(ROOT/'runner'), str(ROOT)]

def load_local_config():
    for path in [ROOT/'.local/runtime.env', ROOT/'.local/flagsmith.env']:
        if path.exists():
            for line in path.read_text().splitlines():
                if '=' in line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key, value)


def create_application(port=8080):
    load_local_config()
    from fastapi import HTTPException
    from fastapi.staticfiles import StaticFiles
    from starlette.responses import FileResponse
    from ci_guard.app import app
    import httpx

    demo_lock = asyncio.Lock()

    async def demo_handler(body):
        data = body.model_dump() if hasattr(body, 'model_dump') else body
        scenario = data.get('scenario', 'outage')
        jobs = int(data.get('jobs', 4))
        if scenario not in ('outage', 'healthy', 'assertion') or not 1 <= jobs <= 8:
            raise HTTPException(422, 'Choose outage, healthy, or assertion and 1–8 jobs.')
        if demo_lock.locked():
            raise HTTPException(409, 'A demonstration is already running. Wait for it to finish.')
        async with demo_lock:
            inventory = os.environ.get('CI_GUARD_DEMO_INVENTORY_URL', 'http://127.0.0.1:8090')
            coordinator = f'http://127.0.0.1:{port}'
            token = os.environ.get('CI_GUARD_ADMIN_TOKEN', os.environ.get('CI_GUARD_TOKEN',''))
            headers = {'Authorization':f'Bearer {token}'} if token else {}
            async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
                try:
                    state = await client.post(inventory+'/__control', json={'healthy':scenario!='outage'})
                    state.raise_for_status()
                    dependencies = await client.get(coordinator+'/api/dependencies', headers=headers)
                    dependencies.raise_for_status()
                    if not any(d['id']=='inventory-api' for d in dependencies.json()):
                        registered = await client.post(coordinator+'/api/dependencies', headers=headers, json={
                            'id':'inventory-api','name':'Inventory API','health_url':inventory+'/health',
                            'error_patterns':['INVENTORY_DEPENDENCY_UNAVAILABLE'],'timeout_seconds':3})
                        registered.raise_for_status()
                    if scenario != 'outage':
                        recovered = await client.post(coordinator+'/api/dependencies/inventory-api/probe', headers=headers, json={})
                        recovered.raise_for_status()
                except httpx.HTTPError as exc:
                    raise HTTPException(503, 'Demo dependency is not ready. Start the inventory service; '+type(exc).__name__) from exc
            batch = uuid.uuid4().hex[:12]
            directory = ROOT/'.local'/'reports'/batch
            directory.mkdir(parents=True, exist_ok=True)
            async def job(index):
                report_path = directory/f'job-{index}.json'
                env = os.environ.copy()
                env.update(INVENTORY_URL=inventory, DEMO_ASSERTION_FAILURE=str(scenario=='assertion').lower(),
                           PYTHONPATH=os.pathsep.join([str(ROOT/'runner'),str(ROOT/'server')]), CI_GUARD_DEMO_RUN='true')
                command = [sys.executable,'-m','ci_guard_runner','run','--server',coordinator,
                           '--repository','demo/inventory-app','--dependency','inventory-api',
                           '--job-id',f'{batch}-{index}','--report',str(report_path),'--timeout','20','--',
                           sys.executable,'-m','pytest',str(ROOT/'examples/test_inventory.py'),'-q']
                proc = await asyncio.create_subprocess_exec(*command, cwd=ROOT, env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                try:
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=90)
                except asyncio.TimeoutError:
                    proc.kill(); await proc.communicate()
                    return {'job_id':f'{batch}-{index}','exit_code':124,'error':'Demo wrapper exceeded 90 seconds.'}
                report = json.loads(report_path.read_text()) if report_path.exists() else {}
                result = {'job_id':f'{batch}-{index}','exit_code':proc.returncode,'report':report}
                if not report:
                    result['error'] = stdout.decode(errors='replace')[-1000:]
                return result
            results = await asyncio.gather(*(job(i) for i in range(jobs)))
            return {'demo':True,'scenario':scenario,'batch_id':batch,'results':results,
                    'message':'Actual local test commands executed. These are controlled demo runs, not GitHub-hosted jobs.'}

    app.state.demo_handler = demo_handler
    dist = ROOT/'web/dist'
    if dist.exists():
        assets = dist/'assets'
        if assets.exists():
            app.mount('/assets', StaticFiles(directory=assets), name='assets')
        @app.get('/{path:path}', include_in_schema=False)
        def dashboard(path: str):
            if path.startswith('api/'):
                raise HTTPException(404, 'API route not found')
            target = dist/path
            if target.is_file() and target.resolve().is_relative_to(dist.resolve()):
                return FileResponse(target)
            return FileResponse(dist/'index.html')
    return app

if __name__ == '__main__':
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    uvicorn.run(create_application(args.port), host=args.host, port=args.port)
