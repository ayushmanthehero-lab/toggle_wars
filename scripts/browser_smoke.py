"""Browser checks against an already-running CI Guard with recorded demo runs.

Install playwright separately; use --chromium for an existing browser. No
repository mutations or policy changes. Screenshots include only demo activity.
"""
import argparse
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8080')
    parser.add_argument('--chromium',default=None)
    args=parser.parse_args()
    output=Path('.local/browser');output.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        options={'headless':True}
        if args.chromium: options['executable_path']=args.chromium
        browser=p.chromium.launch(**options)
        page=browser.new_page(viewport={'width':1440,'height':1000},accept_downloads=True)
        token=os.getenv('CI_GUARD_ADMIN_TOKEN','')
        if token:
            import json
            page.add_init_script('sessionStorage.setItem("ci-guard-token", '+json.dumps(token)+')')
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(args.url+'/#runs',wait_until='networkidle')
        expect(page.locator('tbody tr').first).to_be_visible()
        page.get_by_label('Search runs',exact=True).fill('no-such-run-browser-check')
        expect(page.locator('tbody tr')).to_have_count(0)
        page.get_by_label('Search runs',exact=True).fill('')
        expect(page.locator('tbody tr').first).to_be_visible()
        page.locator('tbody tr button').first.click()
        expect(page.get_by_role('dialog')).to_be_visible()
        with page.expect_download() as result:
            page.get_by_role('button',name='Export JSON').click()
        result.value.save_as(output/'run.json')
        page.keyboard.press('Escape')
        expect(page.get_by_role('dialog')).not_to_be_visible()
        for width in (1440,768,390):
            page.set_viewport_size({'width':width,'height':1000})
            for route in ('overview','runs','dependencies','policies','settings'):
                page.goto(args.url+'/#'+route,wait_until='networkidle')
                expect(page.locator('h1')).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),(width,route)
        page.emulate_media(reduced_motion='reduce')
        page.goto(args.url+'/#overview',wait_until='networkidle')
        page.screenshot(path=str(output/'mobile.png'),full_page=True)
        page.set_viewport_size({'width':1440,'height':1000})
        page.screenshot(path=str(output/'overview.png'),full_page=True)
        assert not errors,errors
        browser.close()
        print('Browser smoke passed: search, details, JSON export, keyboard close, 15 responsive route checks, no JS errors.')

if __name__=='__main__':main()
