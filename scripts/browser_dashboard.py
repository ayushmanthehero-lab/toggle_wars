"""Keyboard, responsive and error checks against a fresh local CI Guard demo.

Build web/dist and start the coordinator in demo mode with a configured local
admin token and an empty, isolated database. This script never supplies the
token, writes configuration, starts test runs, or connects to GitHub.
Install Playwright separately; use --chromium to choose an existing browser.
"""

import argparse
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


ROUTES = {
    "overview": "Your CI, under control.",
    "runs": "Workflow runs",
    "projects": "Projects, connected.",
    "dependencies": "Dependency health",
    "policies": "Protection policies",
    "settings": "Workspace settings",
}


def go(page, url, route):
    page.goto(f"{url}/#{route}")
    expect(page.get_by_role("heading", name=ROUTES[route], exact=True)).to_be_visible()


def check_skip_link(page, url):
    go(page, url, "runs")
    page.keyboard.press("Tab")
    expect(page.get_by_role("link", name="Skip to content")).to_be_focused()
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"#runs$"))
    expect(page.get_by_role("main")).to_be_focused()
    expect(page.get_by_role("heading", name=ROUTES["runs"], exact=True)).to_be_visible()


def check_dialog(page, url):
    go(page, url, "overview")
    trigger = page.get_by_role("button", name="Run a demo")
    trigger.focus()
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog", name="See CI Guard in action")
    expect(dialog).to_be_focused()
    close = dialog.get_by_role("button", name="Close dialog")
    submit = dialog.get_by_role("button", name="Run scenario")
    page.keyboard.press("Tab")
    expect(close).to_be_focused()
    page.keyboard.press("Shift+Tab")
    expect(submit).to_be_focused()
    page.keyboard.press("Tab")
    expect(close).to_be_focused()
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(trigger).to_be_focused()


def check_states(page, url):
    go(page, url, "runs")
    expect(page.get_by_role("heading", name="No workflow runs yet")).to_be_visible()
    go(page, url, "dependencies")
    expect(page.get_by_role("heading", name="Give your tests a safety net")).to_be_visible()
    go(page, url, "projects")
    expect(page.get_by_role("heading", name="Unlock your GitHub workspace.")).to_be_visible()
    expect(page.get_by_role("button", name="Connect GitHub")).to_have_count(0)
    page.get_by_role("button", name="Open connection settings").click()
    expect(page).to_have_url(re.compile(r"#settings$"))
    expect(page.get_by_role("heading", name=ROUTES["settings"], exact=True)).to_be_visible()

    # Simulate unreachable transport at the browser boundary. The real API
    # responds to the retried request after the one interception is removed.
    go(page, url, "overview")
    page.route("**/api/overview", lambda route: route.abort(), times=1)
    page.reload()
    expect(page.get_by_role("alert")).to_contain_text("Unable to reach CI Guard")
    expect(page.get_by_role("heading", name="Your workspace is not connected")).to_be_visible()
    page.get_by_role("button", name="Try again").click()
    expect(page.get_by_role("alert")).to_have_count(0)
    expect(page.get_by_role("heading", name="Attempt activity")).to_be_visible()


def check_responsive(page, url):
    checks = 0
    for width in (320, 390, 768, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        for route in ROUTES:
            go(page, url, route)
            # Wait for the route body, not just the persistent page heading.
            if route == "projects":
                expect(page.get_by_role("heading", name="Unlock your GitHub workspace.")).to_be_visible()
            elif route == "overview":
                expect(page.get_by_role("heading", name="Attempt activity")).to_be_visible()
            else:
                expect(page.locator(".loading-state")).to_have_count(0)
            width_data = page.evaluate("""async () => {
                await document.fonts.ready;
                return [document.documentElement.scrollWidth, innerWidth];
            }""")
            assert width_data[0] <= width_data[1], f"{width}px #{route}: {width_data}"
            if route == "overview":
                for name in ("Refresh recorded data", "Run a demo", "Connect a repository"):
                    button = page.get_by_role("button", name=name, exact=True)
                    box = button.bounding_box()
                    assert box is not None and 0 <= box["x"] and box["x"] + box["width"] <= width, (
                        f"{width}px: {name} is outside the viewport: {box}"
                    )
            checks += 1
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--chromium")
    parser.add_argument("--skip-link-only", action="store_true")
    parser.add_argument("--screenshots-dir", type=Path)
    args = parser.parse_args()
    url = args.url.rstrip("/")
    if urlsplit(url).hostname not in {"127.0.0.1", "::1", "localhost"}:
        parser.error("Use a loopback URL for this isolated smoke test.")

    with sync_playwright() as p:
        options = {"headless": True}
        if args.chromium:
            options["executable_path"] = args.chromium
        browser = p.chromium.launch(**options)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.set_default_timeout(4500)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        check_skip_link(page, url)
        print("PASS: skip link preserves the current route and focuses main content.")
        if not args.skip_link_only:
            check_dialog(page, url)
            check_states(page, url)
            cases = check_responsive(page, url)
            if args.screenshots_dir:
                args.screenshots_dir.mkdir(parents=True, exist_ok=True)
                page.set_viewport_size({"width": 320, "height": 900})
                go(page, url, "overview")
                expect(page.get_by_role("heading", name="Attempt activity")).to_be_visible()
                page.screenshot(path=args.screenshots_dir / "overview-320.png", full_page=True)
                page.set_viewport_size({"width": 1440, "height": 900})
                go(page, url, "projects")
                expect(page.get_by_role("heading", name="Unlock your GitHub workspace.")).to_be_visible()
                page.screenshot(path=args.screenshots_dir / "projects-signed-out.png", full_page=True)
            assert not errors, errors
            print(f"PASS: dialog keyboard and focus, signed-out and empty states, error recovery, {cases} responsive route checks; no uncaught JS errors.")
        browser.close()


if __name__ == "__main__":
    main()
