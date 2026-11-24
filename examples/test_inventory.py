"""Actual example integration test. Invoked explicitly by the demo, not the unit suite."""
import json
import os
import urllib.error
import urllib.request
import pytest

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def test_inventory_returns_stock():
    endpoint = os.environ.get('INVENTORY_URL', 'http://127.0.0.1:8090') + '/inventory'
    try:
        with urllib.request.build_opener(NoRedirect()).open(endpoint, timeout=3) as response:
            item = json.load(response)
    except (urllib.error.URLError, TimeoutError):
        pytest.fail('INVENTORY_DEPENDENCY_UNAVAILABLE: inventory request failed')
    expected = 99 if os.environ.get('DEMO_ASSERTION_FAILURE') == 'true' else 42
    assert item['stock'] == expected, 'Inventory stock must match the expected count'
