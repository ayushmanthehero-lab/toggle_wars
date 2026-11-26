# Flagsmith Terminal Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. Verify red, then green.

**Goal:** Compare and safely edit real Flagsmith environments from CI Guard's terminal.
**Architecture:** A project-scoped Management API client separates discovery, safe base-state updates, and terminal display. An isolated Docker demo and two coordinator processes prove end-to-end behavior.
**Tech Stack:** Python 3.12, HTTPX, Flagsmith, PostgreSQL, CI Guard's existing runner.
**Spec:** docs/superpowers/specs/2026-11-26-ci-guard-terminal-design.md

## Global Constraints
One selected environment per confirmed write; opt-in by numeric ID. No secret output, no Production writes in the demo, no changes to web UI or the existing experiment. No paid services.

## Review Focus
- Missing or duplicate environment/state: report and never guess.
- Redirect, non-success response, or pagination beyond a safe bound: refuse or report unknown; don't leak the key.
- Unapproved environment, protected flag, versioned approval, or multivariate flag: keep read-only.
- Change between preview and write: re-read and ask to start over.
- Saved state does not match: report unverified; no automatic retries.

## Task 1: Real terminal
- [ ] Test discovery, isolation, cancellation, stale preview, matching readback and safe errors in tests/test_flagsmith_terminal.py.
- [ ] Run the file with pytest; fail before implementation.
- [ ] Implement FlagsmithTerminal and menu in scripts/flagsmith_terminal.py.
- [ ] Verify full suite.

## Task 2: Disposable integration
- [ ] Write integration assertions for a raw flag, CI Guard policy, and actual outage results.
- [ ] Build the bootstrap and entry point around the existing disposable Flagsmith image.
- [ ] Verify against the real pinned Flagsmith source with independent coordinators and the default web/build tests.
- [ ] Document how to start the terminal in README and docs/FLAGSMITH_TERMINAL.md.
