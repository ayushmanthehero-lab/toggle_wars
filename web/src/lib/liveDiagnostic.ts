export const splitCiDocs = "https://github.com/ayushmanthehero-lab/toggle_wars/blob/feat/ci-guard-isolated-checks/docs/GITHUB.md#github-checks-and-release-smoke";

export function liveDiagnosticWorkflow(dependency?: string): string {
  // Only a registered dependency ID can be included in the copied YAML.
  const id = /^[A-Za-z0-9_-]{1,100}$/.test(dependency ?? "")
    ? dependency
    : "<registered-dependency-id>";
  return `# .github/workflows/ci-guard-diagnostic.yml
# Manual live diagnostic only, not a merge or release gate.
# See the CI Guard README for isolated PR checks and the strict live release check.
name: CI Guard live diagnostic
on: workflow_dispatch
permissions:
  contents: read
jobs:
  live-diagnostic:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - name: Require a protected branch or tag
        shell: bash
        env:
          EVENT: \${{ github.event_name }}
          REF_PROTECTED: \${{ github.ref_protected }}
          REF_TYPE: \${{ github.ref_type }}
        run: |
          if [[ "$EVENT" != workflow_dispatch || "$REF_PROTECTED" != true ]] ||
             [[ "$REF_TYPE" != branch && "$REF_TYPE" != tag ]]; then
            echo "::error::Run this diagnostic manually on a trusted protected branch or tag."
            exit 1
          fi
      - name: Require live coordinator configuration
        shell: bash
        env:
          COORDINATOR_URL: \${{ vars.CI_GUARD_URL }}
          PROJECT_TOKEN: \${{ secrets.CI_GUARD_PROJECT_TOKEN }}
        run: test -n "$COORDINATOR_URL" && test -n "$PROJECT_TOKEN"
      - uses: actions/checkout@v4
        with:
          ref: \${{ github.sha }}
          persist-credentials: false
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: python -m pip install -r requirements.txt
      - uses: ayushmanthehero-lab/toggle_wars@73806878b945f27ed4cad320f9b360c29cfb4449
        with:
          server: \${{ vars.CI_GUARD_URL }}
          token: \${{ secrets.CI_GUARD_PROJECT_TOKEN }}
          repository: \${{ github.repository }}
          dependency: ${JSON.stringify(id)}
          command: python -m pytest tests/integration
`;
}
