"""Capture probes require the Chromium sandbox and retain actual launch arguments."""
import json
import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def sandboxed_chromium(monkeypatch):
    api = pytest.importorskip('playwright.sync_api')
    real = api.BrowserType.launch
    def launch(self, *args, **kwargs):
        kwargs['chromium_sandbox'] = True
        kwargs['args'] = [*(kwargs.get('args') or []), '--enable-automation']
        browser = real(self, *args, **kwargs)
        command = browser.new_browser_cdp_session().send('Browser.getBrowserCommandLine')['arguments']
        assert '--no-sandbox' not in command
        assert '--disable-setuid-sandbox' not in command
        if os.environ.get('E2E_LAUNCHES'):
            target = Path(os.environ['E2E_LAUNCHES'])
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('a',encoding='utf-8') as out:
                out.write(json.dumps({'chromium_sandbox':True,'arguments':command})+'\n')
        return browser
    monkeypatch.setattr(api.BrowserType,'launch',launch)
