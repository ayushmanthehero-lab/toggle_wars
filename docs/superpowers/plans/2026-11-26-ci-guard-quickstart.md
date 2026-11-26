# CI Guard Quick Start Implementation Plan

**Goal:** Make the terminal lab Docker-only and make adopting it for a real repository a guided check.

**Architecture:** Compose owns a private Flagsmith network and one-shot seeding; a Python entry point reuses the native terminal, demo coordination, and verification. A separate Docker service runs the read-only setup checker and emits workflow YAML.

**Tech Stack:** Docker Compose v2, self-hosted Flagsmith, PostgreSQL, Python, GitHub Actions.

**Spec:** docs/superpowers/specs/2026-11-26-ci-guard-quickstart-design.md

- [ ] Test doctor gates and starter safety, then implement doctor and template.
- [ ] Test private Compose shape and lab commands, then implement packaged stack, script and docs.
- [ ] Run regression suite and cold Compose replay. Record actual terminal and verify the silent output.
