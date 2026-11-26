"""Configure a disposable experiment project through Flagsmith's Django shell."""
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from api_keys.models import MasterAPIKey
from django.db import transaction
from environments.models import Environment
from features.models import Feature
from features.multivariate.models import MultivariateFeatureOption
from organisations.models import Organisation
from projects.models import Project


output = Path(os.environ.get("CI_GUARD_EXPERIMENT_CONFIG", "/bootstrap/experiment.env"))
url = os.environ.get("FLAGSMITH_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")
parsed = urlsplit(url)
if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.username:
    raise SystemExit("Use a fresh, loopback-only Flagsmith instance.")
if output.exists():
    raise SystemExit("Refusing to overwrite an experiment configuration.")
suffix = secrets.token_hex(5)
with transaction.atomic():
    organisation = Organisation.objects.create(name=f"CI Guard Experiment {suffix}")
    project = Project.objects.create(name=f"CI Guard Experiment {suffix}", organisation=organisation)
    environment = Environment.objects.create(name="Experiment", project=project)
    mode = Feature.objects.create(
        name="ci_guard_mode", type="MULTIVARIATE", project=project,
        initial_value="observe", default_enabled=True,
    )
    variant = MultivariateFeatureOption.objects.create(
        feature=mode, key="enforce", default_percentage_allocation=0, string_value="enforce", type="unicode",
    )
    for name, value in {
        "ci_guard_max_job_retries": "2",
        "ci_guard_shared_dependency_retry_budget": "2",
        "ci_guard_dependency_cooldown_seconds": "1",
    }.items():
        Feature.objects.create(name=name, project=project, initial_value=value, default_enabled=True)
    _, key = MasterAPIKey.objects.create_key(
        name="Disposable CI Guard Experiment", organisation=organisation, is_admin=True,
    )
    values = {
        "FLAGSMITH_API_URL": url + "/api/v1/",
        "FLAGSMITH_ENVIRONMENT_KEY": environment.api_key,
        "FLAGSMITH_ADMIN_API_KEY": key,
        "CI_GUARD_EXPERIMENT_MODE_FEATURE_ID": mode.pk,
        "CI_GUARD_EXPERIMENT_ENFORCE_VARIANT_ID": variant.pk,
    }
    with os.fdopen(os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as file:
        file.writelines(f"{name}={value}\n" for name, value in values.items())
    owner = output.parent.stat()
    if os.geteuid() == 0:
        os.chown(output, owner.st_uid, owner.st_gid)
print("Created an isolated Flagsmith experiment project.")
