from typing import Any, Literal
import math

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .health import validate_health_url


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class DependencyInput(Input):
    id: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    name: str = Field(min_length=1, max_length=150)
    health_url: str = Field(max_length=2000)
    error_patterns: list[str] = Field(min_length=1, max_length=20)
    timeout_seconds: float = Field(default=3, ge=0.1, le=10)

    @field_validator('health_url')
    @classmethod
    def safe_url(cls, value):
        return validate_health_url(value)

    @field_validator('error_patterns')
    @classmethod
    def patterns(cls, value):
        if any(not item.strip() or len(item) > 200 for item in value):
            raise ValueError('Each literal error signature must contain 1–200 characters')
        return value


class RunInput(Input):
    repository: str = Field(min_length=3, max_length=200, pattern=r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
    dependency: str = Field(min_length=1, max_length=100)
    job_id: str = Field(min_length=1, max_length=200)
    workflow_run_id: str | None = Field(default=None, max_length=200)
    command: list[str] | str | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator('command')
    @classmethod
    def bounded_command(cls, value):
        if value is not None and len(str(value)) > 4000:
            raise ValueError('Command metadata must be at most 4000 characters')
        return value


class AttemptInput(Input):
    idempotency_key: str = Field(min_length=1, max_length=200)
    attempt_number: int = Field(ge=1, le=100)
    exit_code: int = Field(ge=-255, le=255)
    duration_seconds: float = Field(ge=0, le=604800, allow_inf_nan=False)
    classification: Literal['passed','assertion','dependency','unknown','timeout']
    evidence: str | dict[str, Any] | list[Any] | None = None
    health_status: Literal['healthy','unhealthy','unknown'] = 'unknown'
    health_detail: str | None = Field(default=None, max_length=2000)

    @model_validator(mode='after')
    def valid_success(self):
        if (self.classification == 'passed') != (self.exit_code == 0):
            raise ValueError('Passed classification requires exit_code zero; every nonpassed attempt requires nonzero exit_code')
        if self.evidence is not None and len(str(self.evidence)) > 32000:
            raise ValueError('Evidence must be at most 32000 characters')
        return self


class FinishInput(Input):
    status: Literal['passed','failed','blocked','error','cancelled']
    reason: str | None = Field(default=None, max_length=2000)


class ProbeInput(Input):
    run_id: str | None = Field(default=None, max_length=100)
    probe_token: str | None = Field(default=None, max_length=100)
    probe_generation: int | None = Field(default=None, ge=0)


class PolicyInput(Input):
    mode: Literal['off','observe','enforce']
    max_job_retries: int = Field(ge=0, le=10)
    shared_dependency_retry_budget: int = Field(ge=0, le=100)
    dependency_cooldown_seconds: int = Field(ge=0, le=3600)


class DemoInput(Input):
    scenario: Literal['outage','healthy','assertion']
    jobs: int = Field(default=4, ge=1, le=8)
