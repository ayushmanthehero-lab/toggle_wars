"""Seed a new terminal demonstration on a disposable loopback Flagsmith."""
import os
from pathlib import Path

from api_keys.models import MasterAPIKey
from django.db import transaction
from environments.models import Environment
from features.models import Feature
from organisations.models import Organisation
from projects.models import Project

path = Path(os.environ.get("CI_GUARD_TERMINAL_CONFIG", "/bootstrap/terminal.env"))
url = os.environ.get("FLAGSMITH_PUBLIC_URL", "http://127.0.0.1:18000").rstrip("/")
if url != "http://127.0.0.1:18000" or path.exists():
    raise RuntimeError("Only a fresh loopback terminal lab is supported.")
with transaction.atomic():
    organisation = Organisation.objects.create(name="CI Guard Terminal Lab")
    project = Project.objects.create(name="CI Guard Terminal", organisation=organisation)
    dev = Environment.objects.create(name="Development", project=project)
    staging = Environment.objects.create(name="Staging", project=project)
    production = Environment.objects.create(name="Production", project=project)
    for env in (dev, staging, production):
        # The isolated fixture uses base-state writes. The terminal never attempts
        # to disable versioning or approval settings on a connected project.
        Environment.objects.filter(pk=env.pk).update(use_v2_feature_versioning=False)
    for name, value in {
        "ci_guard_mode": "observe",
        "ci_guard_max_job_retries": "2",
        "ci_guard_shared_dependency_retry_budget": "2",
        "ci_guard_dependency_cooldown_seconds": "30",
        "show_retry_summary": None,
    }.items():
        Feature.objects.create(project=project, name=name, initial_value=value,
                               default_enabled=value is not None)
    _, key = MasterAPIKey.objects.create_key(name="CI Guard Terminal Lab", organisation=organisation, is_admin=True)
    values = {
        "FLAGSMITH_API_URL": f"{url}/api/v1/",
        "FLAGSMITH_PROJECT_ID": project.id,
        "FLAGSMITH_ADMIN_API_KEY": key,
        "TERMINAL_DEVELOPMENT_ID": dev.id,
        "TERMINAL_STAGING_ID": staging.id,
        "TERMINAL_PRODUCTION_ID": production.id,
        "TERMINAL_DEVELOPMENT_KEY": dev.api_key,
        "TERMINAL_STAGING_KEY": staging.api_key,
    }
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as file:
    file.write("".join(f"{name}={value}\n" for name, value in values.items()))
owner = path.parent.stat()
os.chown(path, owner.st_uid, owner.st_gid)
print("Seeded disposable Flagsmith terminal lab.")
