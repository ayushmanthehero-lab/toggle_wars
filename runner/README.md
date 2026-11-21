# CI Guard runner

Install with `python -m pip install ./runner`, then run:

```bash
export CI_GUARD_TOKEN='your-coordinator-runner-token'
ci-guard run --server https://ci-guard.example.com \
  --repository owner/repository --dependency inventory-api --job-id integration \
  --report ci-guard-report.json --timeout 120 -- python -m pytest -q tests/
```

The dependency and literal error signatures must already be registered with the coordinator. The token is read only from the environment. Commands execute as argv without a shell. The composite action uses `--command-string`, which supports shell-style argument quoting but does not expand variables, substitutions, pipelines, or redirections.

Direct `pytest`, `py.test`, and `python -m pytest` commands load a small CI Guard pytest plugin. It writes bounded, redacted exception evidence and outcome counts into a private temporary report. Source excerpts and captured logs do not qualify as dependency evidence. Assertions, missing expected exceptions/warnings, and mixed unknown failures prevent infrastructure retries. A configured dependency signature in an actual exception message, including an explicit `pytest.fail` message, still requires a confirmed unhealthy coordinator probe before a retry is allowed.

Pytest success requires at least one passed test call (including an unexpected pass), a successful process exit, and no failed/error outcomes. All-skipped, all-expected-failure, zero-test, and collection-only runs fail. Skipped and expected-failure counts remain visible when other tests pass. Disabling or breaking the plugin cannot turn missing outcome evidence into success.

Other commands, including shell scripts or tools that invoke pytest internally, use command exit status and conservative literal output classification. Their reports explicitly say that individual test outcomes were not verified. Use a direct pytest invocation when verified pytest execution counts are required.

Exit codes are `0` for verified pytest success or a successful generic command, `1` for failure, `2` for a blocked run, `3` for configuration/coordination errors, and `124` for timeout. Cancellation returns `128 + signal` (normally `130` or `143`). A blocked command never passes or becomes a skipped check. The runner does not wait for dependency recovery.

JSON reports are written on a best-effort basis on every execution exit. GitHub summaries and outputs are appended when their environment paths exist. Logs are bounded and known environment secrets are redacted before truncation, including across read boundaries. Actual execution time and prevented attempts remain separate from observe-mode would-prevent decisions; avoided time and cash savings are not invented.

Cancellation terminates the process group and records an interrupted attempt and runtime locally when possible. The current coordinator contract records the cancelled run but does not accept partial cancelled-attempt timing, so that timing is available in the local report rather than coordinator aggregate metrics.
