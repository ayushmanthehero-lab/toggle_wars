"""Executed inside Flagsmith's Django shell, with a private mounted output dir."""
import os
from pathlib import Path
from django.db import transaction
from users.models import FFAdminUser
from organisations.models import Organisation, UserOrganisation
from projects.models import Project
from environments.models import Environment
from features.models import Feature
from api_keys.models import MasterAPIKey

output = Path('/bootstrap/flagsmith.env')
with transaction.atomic():
    email = os.environ['FLAGSMITH_ADMIN_EMAIL']
    user = FFAdminUser.objects.filter(email=email).first()
    if user is None:
        user = FFAdminUser.objects.create_superuser(
            email=email, password=os.environ['FLAGSMITH_ADMIN_PASSWORD'],
            first_name='CI', last_name='Guard')
    organisation, _ = Organisation.objects.get_or_create(name='CI Guard')
    UserOrganisation.objects.get_or_create(user=user, organisation=organisation, defaults={'role':'ADMIN'})
    project, _ = Project.objects.get_or_create(name='CI Guard', organisation=organisation)
    environment, _ = Environment.objects.get_or_create(name='Development', project=project)
    defaults = {'ci_guard_mode':'observe', 'ci_guard_max_job_retries':'2',
                'ci_guard_shared_dependency_retry_budget':'2', 'ci_guard_dependency_cooldown_seconds':'30'}
    for name, value in defaults.items():
        Feature.objects.get_or_create(name=name, project=project,
            defaults={'initial_value':value, 'default_enabled':True})
    existing = {}
    if output.exists():
        existing = dict(line.split('=',1) for line in output.read_text().splitlines() if '=' in line)
    key = existing.get('FLAGSMITH_ADMIN_API_KEY')
    if not key:
        _, key = MasterAPIKey.objects.create_key(name='CI Guard local coordinator', organisation=organisation, is_admin=True)
    public_url = os.environ.get('FLAGSMITH_PUBLIC_URL', 'http://localhost:8000').rstrip('/')
    values = {'FLAGSMITH_API_URL':public_url+'/api/v1/',
              'FLAGSMITH_ENVIRONMENT_KEY':environment.api_key,
              'FLAGSMITH_ENVIRONMENT_ID':environment.api_key,
              'FLAGSMITH_ADMIN_API_KEY':key,
              'FLAGSMITH_PROJECT_ID':str(project.id),
              'FLAGSMITH_DASHBOARD_URL':public_url}
    temporary = output.with_suffix('.tmp')
    fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd,'w') as file:
        file.write(''.join(f'{name}={value}\n' for name,value in values.items()))
    temporary.replace(output)
    owner = output.parent.stat()
    os.chown(output, owner.st_uid, owner.st_gid)
print('Flagsmith project, environment, and four policy flags are ready. Credentials saved privately.')
