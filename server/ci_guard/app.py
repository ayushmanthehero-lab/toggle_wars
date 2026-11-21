"""FastAPI application and explicit single-organization authentication boundary."""
import hmac
import inspect
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .db import Database
from .health import probe_health
from .models import AttemptInput, DemoInput, DependencyInput, FinishInput, PolicyInput, ProbeInput, RunInput
from .policy import PolicyProvider, PolicyWriteError
from .service import Coordinator


def create_app(database_path=None):
    demo_mode = os.getenv('CI_GUARD_DEMO_MODE','').lower()=='true'
    token = os.getenv('CI_GUARD_TOKEN','')
    admin_token = os.getenv('CI_GUARD_ADMIN_TOKEN','')
    db = Database(database_path or os.getenv('CI_GUARD_DATABASE_PATH') or os.getenv('CI_GUARD_DB_PATH','.local/ci-guard.sqlite3'))
    provider = PolicyProvider(db,demo_mode=demo_mode)
    service = Coordinator(db,provider,demo_mode)
    app = FastAPI(title='CI Guard',version=__version__)
    app.state.db,app.state.policy_provider,app.state.coordinator = db,provider,service
    app.state.health_probe = probe_health
    app.state.demo_handler = None

    def anonymous_demo_enabled():
        if not demo_mode:
            return False
        with db.transaction() as con:
            enforced = con.execute("SELECT data FROM configuration WHERE key='github_auth_enforced'").fetchone()
            if enforced is not None and enforced['data']=='true':
                return False
            if con.execute('SELECT 1 FROM github_connections LIMIT 1').fetchone():
                # Project history remains private even after the account disconnects.
                con.execute("INSERT INTO configuration VALUES ('github_auth_enforced','true') ON CONFLICT(key) DO UPDATE SET data='true'")
                return False
        return True

    def bearer(authorization):
        return authorization.removeprefix('Bearer ') if authorization and authorization.startswith('Bearer ') else ''

    def matches(supplied,expected):
        return bool(expected) and hmac.compare_digest(supplied.encode(),expected.encode())

    def authenticate(authorization: str | None = Header(default=None)):
        supplied = bearer(authorization)
        demo_access = anonymous_demo_enabled()
        # Scoped/revoked credentials must never fall through to local demo access.
        if supplied.startswith('cg_proj_'):
            principal = app.state.github_store.authenticate_project_token(supplied)
            if principal is None:
                raise HTTPException(401,'Project runner token is invalid or revoked',headers={'WWW-Authenticate':'Bearer'})
            return {'role':'project',**principal}
        if matches(supplied,admin_token):
            return 'admin'
        if matches(supplied,token):
            return 'runner'
        if not authorization and demo_access:
            return 'demo'
        if not token and not admin_token and not authorization:
            raise HTTPException(503,'Configure CI_GUARD_ADMIN_TOKEN or CI_GUARD_TOKEN before using the coordinator, or explicitly enable CI_GUARD_DEMO_MODE=true for local demonstrations')
        raise HTTPException(401,'A valid Bearer token is required',headers={'WWW-Authenticate':'Bearer'})

    def administrator(role=Depends(authenticate)):
        if role not in ('admin','demo'):
            raise HTTPException(403,'This operation requires CI_GUARD_ADMIN_TOKEN; the runner token cannot change configuration')
        return role

    def strict_administrator(authorization: str | None = Header(default=None)):
        if not admin_token:
            raise HTTPException(503,'Configure CI_GUARD_ADMIN_TOKEN before connecting GitHub or administering projects')
        if not matches(bearer(authorization),admin_token):
            raise HTTPException(401,'An administrator Bearer token is required for GitHub and project administration',headers={'WWW-Authenticate':'Bearer'})
        anonymous_demo_enabled()
        return 'admin'

    def team_access(role=Depends(authenticate)):
        if isinstance(role,dict):
            raise HTTPException(403,'Project runner tokens cannot access team-wide administration or history')
        return role

    def project_repository(role,repository):
        if not isinstance(role,dict):
            return repository
        if repository and repository.casefold()!=role['repository'].casefold():
            raise HTTPException(403,'Project runner token does not authorize this repository')
        return role['repository']

    def project_dependency(role,dependency_id):
        if isinstance(role,dict) and dependency_id not in role['dependency_ids']:
            raise HTTPException(403,'Project runner token does not authorize this dependency')

    def authorized_run(role,run_id):
        run = service.get_run(run_id)
        if isinstance(role,dict) and (run.get('project_id')!=role['project_id']
                or run['repository'].casefold()!=role['repository'].casefold()
                or run['dependency'] not in role['dependency_ids']):
            raise HTTPException(404,'Run not found')
        return run

    def project_dependency_view(role,dependency):
        if not isinstance(role,dict):
            return dependency
        # Health/allowance are shared; other projects' active-run counts are not.
        result = dict(dependency)
        with db.transaction() as con:
            result['active_runs'] = con.execute("SELECT count(*) FROM runs WHERE json_extract(data,'$.project_id')=? AND json_extract(data,'$.dependency')=? AND json_extract(data,'$.status') IN ('queued','running')",
                (role['project_id'],dependency['id'])).fetchone()[0]
        return result

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, error):
        # Pydantic errors include rejected input values; do not echo secrets or command output.
        details = '; '.join(f'{".".join(str(part) for part in item["loc"])}: {item["msg"]}' for item in error.errors())
        return JSONResponse(status_code=422,content={'detail':details[:2000]})

    @app.get('/api/health')
    def health():
        return {'status':'ok','version':__version__,'demo_mode':demo_mode}

    @app.get('/api/ready')
    def ready():
        if not anonymous_demo_enabled() and not (token or admin_token):
            raise HTTPException(503,'CI_GUARD_ADMIN_TOKEN or CI_GUARD_TOKEN must be configured')
        with db.transaction() as con:
            con.execute('SELECT 1').fetchone()
        policy = provider.evaluate('')
        if policy.error:
            raise HTTPException(503,policy.error)
        return {'status':'ready','policy_source':policy.source}

    @app.get('/api/overview',dependencies=[Depends(team_access)])
    def overview():
        return service.overview()

    @app.get('/api/runs')
    def runs(status: str | None=None,repository: str | None=None,search: str | None=Query(default=None,max_length=200),limit: int=Query(default=50,ge=1,le=500),role=Depends(authenticate)):
        repository = project_repository(role,repository)
        return service.list_runs(status,repository,search,limit,
            project_id=role['project_id'] if isinstance(role,dict) else None,
            dependency_ids=role['dependency_ids'] if isinstance(role,dict) else None)

    @app.post('/api/runs',status_code=201)
    def create_run(body: RunInput,role=Depends(authenticate)):
        values = body.model_dump()
        values['repository'] = project_repository(role,body.repository)
        project_dependency(role,body.dependency)
        if isinstance(role,dict):
            values['project_id'] = role['project_id']
        return service.create_run(values)

    @app.get('/api/runs/{run_id}')
    def run(run_id: str,role=Depends(authenticate)):
        return authorized_run(role,run_id)

    @app.post('/api/runs/{run_id}/attempts')
    def attempt(run_id: str,body: AttemptInput,role=Depends(authenticate)):
        authorized_run(role,run_id)
        return service.record_attempt(run_id,body.model_dump())

    @app.post('/api/runs/{run_id}/finish')
    def finish(run_id: str,body: FinishInput,role=Depends(authenticate)):
        authorized_run(role,run_id)
        return service.finish(run_id,body.model_dump())

    @app.get('/api/dependencies')
    def dependencies(role=Depends(authenticate)):
        dependencies = service.dependencies(repository=role['repository'] if isinstance(role,dict) else '')
        return [project_dependency_view(role,dep) for dep in dependencies
            if not isinstance(role,dict) or dep['id'] in role['dependency_ids']]

    @app.post('/api/dependencies',status_code=201,dependencies=[Depends(administrator)])
    def create_dependency(body: DependencyInput):
        return service.register(body.model_dump())

    @app.put('/api/dependencies/{dependency_id}',dependencies=[Depends(administrator)])
    def update_dependency(dependency_id: str,body: DependencyInput):
        if dependency_id!=body.id:
            raise HTTPException(422,'Dependency ID cannot be changed')
        return service.register(body.model_dump(),update=True)

    @app.post('/api/dependencies/{dependency_id}/probe')
    def probe(dependency_id: str,body: ProbeInput | None=None,role=Depends(authenticate)):
        body = body or ProbeInput()
        project_dependency(role,dependency_id)
        if body.run_id is None and role not in ('admin','demo'):
            raise HTTPException(403,'Manual probes require CI_GUARD_ADMIN_TOKEN')
        if body.run_id:
            authorized_run(role,body.run_id)
        result = service.probe(dependency_id,body.model_dump(),app.state.health_probe)
        if isinstance(role,dict):
            dependency = project_dependency_view(role,result['dependency'])
            result.update(dependency)
            result['dependency'] = dependency
        return result

    @app.get('/api/policy')
    def policy(repository: str=Query(default='',max_length=200),role=Depends(authenticate)):
        return provider.evaluate(project_repository(role,repository))

    @app.put('/api/policy',dependencies=[Depends(administrator)])
    def update_policy(body: PolicyInput):
        try:
            result = provider.update(body.model_dump())
        except PolicyWriteError as error:
            raise HTTPException(409,str(error)) from error
        with db.transaction() as con:
            db.audit(con,'policy_updated',f'Policy updated to {result.mode}',policy=result.model_dump())
        return result

    @app.get('/api/settings')
    def settings(request: Request,role=Depends(team_access)):
        authentication_required = not anonymous_demo_enabled()
        return {'version':__version__,'demo_mode':demo_mode,
            'authentication_required':authentication_required,'auth_required':authentication_required,
            'token_configured':bool(token),'admin_configured':bool(admin_token),
            'policy_source':provider.source,'flagsmith_configured':bool(os.getenv('FLAGSMITH_ENVIRONMENT_KEY')),
            'policy_writable':role in ('admin','demo') and provider.editable,
            'database':{'engine':'sqlite','mode':'wal','replicas':1},
            'limitations':['Run one coordinator process/replica with SQLite on local persistent disk.',
                'Use HTTPS at a reverse proxy for production.','Runner tokens cannot edit policy or dependency configuration.',
                'GitHub required checks must preserve the test wrapper exit code; skipped jobs are not a test gate.'],
            'installation':{'server_url':str(request.base_url).rstrip('/'),'action_ref':'./'}}

    @app.get('/api/audit',dependencies=[Depends(team_access)])
    def audit(limit: int=Query(default=100,ge=1,le=500)):
        return service.audit(limit)

    @app.post('/api/demo/run',dependencies=[Depends(administrator)])
    async def demo(body: DemoInput):
        if not demo_mode:
            raise HTTPException(404,'Demo execution is disabled; explicitly enable CI_GUARD_DEMO_MODE=true')
        handler = app.state.demo_handler
        if handler is None:
            raise HTTPException(503,'The demo subprocess handler is not installed in this deployment; use the supplied local demo script')
        result = handler(body.model_dump())
        return await result if inspect.isawaitable(result) else result

    from .github import install_github_routes
    app.state.github_store = install_github_routes(app,db,strict_administrator)

    # API routes are registered first; production may serve the built dashboard from here.
    web_dist = Path(os.getenv('CI_GUARD_WEB_DIST','web/dist'))
    if web_dist.is_dir():
        app.mount('/',StaticFiles(directory=str(web_dist),html=True),name='dashboard')
    return app


app = create_app()
