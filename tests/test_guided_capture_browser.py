"""Fictional routed portal and canvas streams; never requests a real camera."""
# ruff: noqa: F811 -- imported pytest fixture deliberately reused by parameter
import os
from pathlib import Path

import pytest
from capture_browser_fixture import sandboxed_chromium  # noqa: F401
from test_portal_recovery_browser import (
    phone,  # noqa: F401 -- fictional browser scaffold
)

pytestmark = pytest.mark.skipif(not os.environ.get("E2E"), reason="E2E=1 requests Chromium probes")

MOCK_CAMERA = """() => {
  window.cameraStreams = []; window.cameraConstraints = [];
  window.makeCamera = () => {
    const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 480;
    const ctx = canvas.getContext('2d'); ctx.fillStyle = '#555'; ctx.fillRect(0, 0, 640, 480);
    ctx.fillStyle = 'white'; ctx.fillRect(120, 45, 400, 390);
    ctx.fillStyle = 'black'; ctx.font = '24px sans-serif'; ctx.fillText('FICTIONAL DOCUMENT', 140, 110);
    for (let y = 160; y < 380; y += 40) ctx.fillRect(145, y, 330, 5);
    const stream = canvas.captureStream(5); cameraStreams.push(stream); return stream;
  };
  navigator.mediaDevices.getUserMedia = async constraints => {
    cameraConstraints.push(constraints); return makeCamera();
  };
}"""


def open_mock(page):
    page.evaluate(MOCK_CAMERA)
    page.evaluate("() => openCapture('passport', 'Fictional passport', document.querySelector('#helpbtn'))")
    page.get_by_role("button", name="Take this photo", exact=True).wait_for()
    page.wait_for_function("() => !document.querySelector('#document-capture .capture-controls button').disabled")


def assert_stopped(page):
    assert page.evaluate("() => cameraStreams.every(s => s.getTracks().every(t => t.readyState === 'ended'))")


def accept_warnings(page):
    page.get_by_role('button', name='Use this photo', exact=True).wait_for()
    checkbox = page.locator('#document-capture input[type=checkbox]')
    if checkbox.is_visible():
        checkbox.check()


def shot(page, name):
    if os.environ.get("E2E_SHOTS"):
        target = Path(os.environ["E2E_SHOTS"]) / name
        page.screenshot(path=str(target), full_page=True)


def test_manual_capture_preview_retake_confirm_keeps_full_image(phone):
    page, _state, faults = phone
    faults["hold"] = None
    uploads = []
    page.on("request", lambda request: uploads.append(request) if request.url.endswith("/api/upload") else None)
    open_mock(page)
    assert page.locator(".capture-guide span").count() == 4
    assert page.evaluate("() => cameraConstraints[0].video.facingMode.ideal") == "environment"
    assert page.evaluate("() => cameraConstraints[0].audio") is False
    assert uploads == []
    shot(page, "guided-camera-position.png")
    page.get_by_role("button", name="Take this photo", exact=True).click()
    page.get_by_role("button", name="Use this photo", exact=True).wait_for()
    assert_stopped(page)
    page.locator("#document-capture img").wait_for()
    page.wait_for_function("() => document.querySelector('#document-capture img').naturalWidth === 640")
    assert page.locator("#document-capture img").evaluate("n => n.naturalHeight") == 480
    assert uploads == []
    shot(page, "guided-camera-preview.png")
    page.get_by_role("button", name="Retake", exact=True).click()
    page.wait_for_function("() => cameraStreams.length === 2 && !document.querySelector('#document-capture .capture-controls button').disabled")
    page.get_by_role("button", name="Take this photo", exact=True).click()
    accept_warnings(page)
    page.get_by_role("button", name="Use this photo", exact=True).click()
    page.wait_for_function("() => recovery.uploads.size === 0 && !document.querySelector('#document-capture')")
    assert len(uploads) == 1 and "document-photo.jpg" in uploads[0].post_data_buffer.decode("latin1")
    assert_stopped(page)


@pytest.mark.parametrize("exit_action", ["escape", "close", "outside", "navigate", "language", "signout", "pagehide", "hidden"])
def test_camera_tracks_stop_on_every_exit(phone, exit_action):
    page, _state, faults = phone
    faults["hold"] = None
    open_mock(page)
    if exit_action == "escape":
        page.keyboard.press("Escape")
    elif exit_action == "close":
        page.get_by_role("dialog").get_by_role("button", name="Close", exact=True).click()
    elif exit_action == "outside":
        page.locator(".sheet").click(position={"x": 2, "y": 85})
    elif exit_action == "navigate":
        page.evaluate("() => go(0)")
    elif exit_action == "language":
        page.locator("#lang").select_option("es", force=True)
    elif exit_action == "signout":
        page.evaluate("() => signOutNow()")
    elif exit_action == "pagehide":
        page.evaluate("() => window.dispatchEvent(new Event('pagehide'))")
    else:
        page.evaluate("() => { Object.defineProperty(document, 'hidden', {configurable:true, value:true}); document.dispatchEvent(new Event('visibilitychange')); }")
    page.wait_for_function("() => !document.querySelector('#document-capture')")
    assert_stopped(page)


def test_late_camera_permission_after_close_stops_acquired_tracks(phone):
    page, _, _ = phone
    page.evaluate(MOCK_CAMERA)
    page.evaluate("() => { navigator.mediaDevices.getUserMedia = () => new Promise(resolve => window.finishCamera = resolve); openCapture('passport', 'Fictional passport'); }")
    page.keyboard.press("Escape")
    page.evaluate("() => finishCamera(makeCamera())")
    page.wait_for_function("() => cameraStreams[0].getTracks()[0].readyState === 'ended'")
    assert not page.locator("#document-capture").count()


@pytest.mark.parametrize("language", ["en", "pt", "es", "ht"])
def test_denied_camera_has_localized_accessible_file_fallback(phone, language):
    page, _, _ = phone
    page.evaluate("lang => { S.lang = lang; navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('fictional denial', 'NotAllowedError'); }; openCapture('passport', 'Fictional passport'); }", language)
    page.wait_for_function("() => document.querySelector('#document-capture [role=status]').textContent === cw('unavailable')")
    fallback = page.locator("#document-capture input[type=file]")
    assert fallback.get_attribute("accept") == "image/jpeg,image/png,application/pdf"
    assert fallback.get_attribute("aria-label")
    assert page.locator("#document-capture .capture-controls button").is_disabled()
    page.keyboard.press("Escape")


def test_camera_dialog_traps_keyboard_and_restores_focus(phone):
    page, _, _ = phone
    open_mock(page)
    file_input = page.locator("#document-capture input[type=file]")
    file_input.focus()
    page.keyboard.press("Tab")
    assert page.get_by_role("dialog").get_by_role("button", name="Close", exact=True).evaluate("n => n === document.activeElement")
    page.keyboard.press("Shift+Tab")
    assert file_input.evaluate("n => n === document.activeElement")
    page.keyboard.press("Escape")
    assert page.locator("#helpbtn").evaluate("n => n === document.activeElement")


def test_background_snapshot_render_preserves_unconfirmed_photo_preview(phone):
    page, _, _ = phone
    open_mock(page)
    page.get_by_role("button", name="Take this photo", exact=True).click()
    page.get_by_role("button", name="Use this photo", exact=True).wait_for()
    page.evaluate("() => render()")
    assert page.locator("#document-capture img").count() == 1
    assert page.get_by_role("button", name="Use this photo", exact=True).is_visible()
    page.keyboard.press("Escape")
    assert_stopped(page)


def test_optional_money_is_reachable_without_request_and_handles_unknown_zero(phone):
    page, state, faults = phone
    faults["hold"] = None
    page.evaluate("() => { S.step = steps().findIndex(s => s.id === 'monthly_money'); render(); }")
    assert state["money"] is None
    assert page.locator("[data-question^=fw_]").count() == 9
    question = page.locator('[data-question="fw_income_work"]')
    question.get_by_role("button", name="I'm not sure", exact=True).click()
    page.wait_for_function("() => S.data.answers.fw_income_work === 'Unsure' && !recovery.pending.size")
    question.locator("input").fill("0")
    page.wait_for_function("() => S.data.answers.fw_income_work === '0' && !recovery.pending.size")
    question.get_by_role("button", name="Leave blank", exact=True).click()
    page.wait_for_function("() => S.data.answers.fw_income_work == null && !recovery.pending.size")
    assert not any(x.startswith("fw_") for x in state["missing"])


def test_locked_shared_money_fields_are_disabled_in_intake(phone):
    page, _, _ = phone
    page.evaluate("() => { S.data.money = null; S.data.money_locked = true; S.step = steps().findIndex(s => s.id === 'monthly_money'); render(); }")
    assert page.locator('[data-question^="fw_"] input:disabled').count() == 9
    assert page.locator('[data-question^="fw_"] button:disabled').count() == 18


@pytest.mark.parametrize("kind", ["text", "textarea", "a_number"])
def test_reference_unknown_control_can_be_cleared_and_replaced(phone, kind):
    page, _, _ = phone
    page.evaluate("kind => { window.referenceChanges = []; const q = {id:'fictional-gap-control',type:kind,label:'Fictional detail',allow_unsure:true}; document.querySelector('#main').replaceChildren(input(q,null,(v) => referenceChanges.push(v),'fictional-gap-control')); }", kind)
    field = page.locator('#main')
    box = field.locator('input,textarea')
    box.fill('12345678' if kind == 'a_number' else 'Fictional detail')
    field.get_by_role('button', name="I'm not sure", exact=True).click()
    assert box.is_disabled() and box.input_value() == ''
    assert page.evaluate('referenceChanges.at(-1)') == 'Unsure'
    field.get_by_role('button', name='Leave blank', exact=True).click()
    assert box.is_enabled() and page.evaluate('referenceChanges.at(-1)') is None
    box.fill('87654321' if kind == 'a_number' else 'Replacement fictional detail')
    assert page.evaluate('referenceChanges.at(-1)') != 'Unsure'


def test_unsupported_camera_file_fallback_uploads_only_selected_fictional_file(phone):
    page, _, _faults = phone
    uploads = []
    page.on("request", lambda request: uploads.append(request) if request.url.endswith("/api/upload") else None)
    page.evaluate("() => { navigator.mediaDevices.getUserMedia = undefined; openCapture('passport', 'Fictional passport'); }")
    page.wait_for_function("() => document.querySelector('#document-capture [role=status]').textContent === cw('unavailable')")
    assert uploads == []
    page.locator("#document-capture input[type=file]").set_input_files({"name": "fictional.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-fictional"})
    page.wait_for_function("() => recovery.uploads.size === 0 && !document.querySelector('#document-capture')")
    assert len(uploads) == 1


@pytest.mark.parametrize("entry", ["documents", "retake"])
def test_document_and_retake_actions_open_the_shared_capture_flow(phone, entry):
    page, _, faults = phone
    faults["hold"] = None
    page.evaluate(MOCK_CAMERA)
    if entry == "documents":
        page.evaluate("() => go(steps().findIndex(s => s.kind === 'documents'))")
        page.locator("#main .upl button").first.click()
    else:
        page.evaluate("() => { S.step = 0; S.data.tasks = [{id:'retake-fictional',kind:'retake',doc_id:'passport',text:'Fictional retake'}]; render(); }")
        page.get_by_role("button", name="Take photo: Fictional retake", exact=True).click()
    page.wait_for_function("() => document.querySelectorAll('.capture-guide span').length === 4")
    assert page.locator("#document-capture input[type=file]").count() == 1
    page.keyboard.press("Escape")
    assert_stopped(page)


def test_live_outline_warns_and_corrected_preview_requires_explicit_acceptance(phone):
    page, _, faults = phone
    faults['hold'] = None
    uploads = []
    page.on('request', lambda r: uploads.append(r) if r.url.endswith('/api/upload') else None)
    open_mock(page)
    page.wait_for_function("() => document.querySelector('.capture-outline polygon').getAttribute('points').length > 0")
    assert uploads == []
    page.get_by_role('button', name='Take this photo', exact=True).click()
    page.get_by_role('button', name='Use this photo', exact=True).wait_for()
    assert page.get_by_role('button', name='Use this photo', exact=True).is_disabled()
    assert 'move closer' in page.locator('.capture-warning').inner_text().lower()
    assert page.locator('.capture-corners input').count() == 8
    corner = page.get_by_role('spinbutton', name='Corner 1 horizontal (%)', exact=True)
    corner.fill('15')
    assert page.locator('.capture-outline polygon').get_attribute('points').startswith('96,')
    page.get_by_role('button', name='Preview corrected crop', exact=True).click()
    page.get_by_alt_text('Corrected crop preview', exact=True).wait_for()
    shot(page, 'guided-corrected-preview-warning.png')
    assert_stopped(page)
    assert uploads == []
    accept_warnings(page)
    page.get_by_role('button', name='Use this photo', exact=True).click()
    page.wait_for_function('() => !recovery.uploads.size')
    assert len(uploads) == 1
    multipart = uploads[0].post_data_buffer.decode('latin1')
    assert 'document-photo.jpg' in multipart and 'document-corrected.jpg' in multipart
    assert 'capture_metadata' in multipart and '"selection":"corrected"' in multipart
    assert '"override":true' in multipart and '"source_dimensions":[640,480]' in multipart


def test_undetected_manual_capture_original_fallback_and_retry_keeps_metadata(phone):
    page, _, faults = phone
    faults['drop_upload'] = True
    page.evaluate(MOCK_CAMERA)
    page.evaluate("() => { makeCamera = () => { const c=document.createElement('canvas');c.width=640;c.height=480;c.getContext('2d').fillRect(0,0,640,480); const s=c.captureStream(5);cameraStreams.push(s);return s; }; openCapture('passport','Fictional damaged document'); }")
    page.wait_for_function("() => !document.querySelector('.capture-controls button').disabled")
    page.get_by_role('button', name='Take this photo', exact=True).click()
    page.get_by_role('button', name='Use this photo', exact=True).wait_for()
    assert 'No reliable outline' in page.locator('.capture-warning').inner_text()
    page.get_by_role('button', name='Use full original image', exact=True).click()
    accept_warnings(page)
    page.get_by_role('button', name='Use full original image', exact=True).scroll_into_view_if_needed()
    shot(page, 'guided-manual-controls-warning.png')
    page.get_by_role('button', name='Use this photo', exact=True).click()
    page.wait_for_function('() => recovery.uploads.size === 1 && ![...recovery.uploads.values()][0].busy')
    assert page.evaluate("() => [...recovery.uploads.values()][0].capture.selection") == 'original'
    assert page.evaluate("() => [...recovery.uploads.values()][0].derivative") is None
    faults['drop_upload'] = False
    page.evaluate('() => retryUpload([...recovery.uploads.keys()][0])')
    page.wait_for_function('() => !recovery.uploads.size')
    assert_stopped(page)


def test_repeated_open_and_cancel_during_correction_cannot_upload_or_restore_preview(phone):
    page, _, _ = phone
    open_mock(page)
    page.evaluate("() => openCapture('passport','Second fictional capture')")
    page.wait_for_function("() => cameraStreams.length===2 && !document.querySelector('.capture-controls button').disabled")
    assert page.evaluate("() => cameraStreams[0].getTracks().every(t=>t.readyState==='ended')")
    page.get_by_role('button', name='Take this photo', exact=True).click()
    page.get_by_role('button', name='Preview corrected crop', exact=True).wait_for()
    page.evaluate("() => { CaptureVision.correct = () => new Promise(resolve=>window.finishCorrection=resolve); }")
    page.get_by_role('button', name='Preview corrected crop', exact=True).click()
    page.keyboard.press('Escape')
    page.evaluate("() => { const c=document.createElement('canvas');c.width=10;c.height=10;finishCorrection(c); }")
    page.wait_for_timeout(100)
    assert not page.locator('#document-capture').count()
    assert page.evaluate('() => recovery.uploads.size') == 0
    assert_stopped(page)


def test_file_selection_supersedes_pending_camera_permission(phone):
    page, _, _ = phone
    page.evaluate(MOCK_CAMERA)
    page.evaluate("() => { navigator.mediaDevices.getUserMedia=()=>new Promise(resolve=>window.lateCamera=resolve);openCapture('passport','Fictional source fallback'); }")
    page.locator('#document-capture input[type=file]').set_input_files({'name':'fictional.pdf','mimeType':'application/pdf','buffer':b'%PDF-fictional'})
    page.evaluate('() => lateCamera(makeCamera())')
    page.wait_for_function("() => cameraStreams[0].getTracks().every(t=>t.readyState==='ended')")
    assert not page.locator('#document-capture').count()


def test_adjusted_small_crop_adds_resolution_warning_and_original_fallback_clears_it(phone):
    page, _, _ = phone
    large = MOCK_CAMERA.replace('canvas.width = 640; canvas.height = 480','canvas.width = 1920; canvas.height = 1440').replace("const ctx = canvas.getContext('2d');", "const ctx = canvas.getContext('2d'); ctx.scale(3,3);")
    page.evaluate(large)
    page.evaluate("() => openCapture('passport','Fictional large page')")
    page.wait_for_function("() => !document.querySelector('.capture-controls button').disabled")
    page.get_by_role('button',name='Take this photo',exact=True).click()
    page.get_by_role('button',name='Use this photo',exact=True).wait_for()
    assert 'Low document resolution' not in page.locator('.capture-warning').inner_text()
    for i,q in enumerate([[40,40],[60,40],[60,60],[40,60]],1):
        for axis,n in zip(['horizontal','vertical'],q):
            page.get_by_role('spinbutton',name=f'Corner {i} {axis} (%)',exact=True).fill(str(n))
    page.get_by_role('button',name='Preview corrected crop',exact=True).click()
    page.get_by_alt_text('Corrected crop preview',exact=True).wait_for()
    assert 'Low document resolution' in page.locator('.capture-warning').inner_text()
    assert page.get_by_role('button',name='Use this photo',exact=True).is_disabled()
    page.get_by_role('button',name='Use full original image',exact=True).click()
    assert 'Low document resolution' not in page.locator('.capture-warning').inner_text()
    page.keyboard.press('Escape')


def test_algorithm_failure_keeps_original_preview_and_manual_corners(phone):
    page, _, _ = phone
    open_mock(page)
    page.evaluate("() => { CaptureVision.analyze=()=>{throw Error('fictional unsupported analysis')}; }")
    page.get_by_role('button',name='Take this photo',exact=True).click()
    page.get_by_role('button',name='Use this photo',exact=True).wait_for()
    assert page.locator('#document-capture img').count()==1
    assert page.locator('.capture-corners input').count()==8
    assert 'No reliable outline' in page.locator('.capture-warning').inner_text()
    accept_warnings(page)
    assert page.get_by_role('button',name='Use this photo',exact=True).is_enabled()
    page.keyboard.press('Escape')


@pytest.mark.parametrize('exit_action',['close','retake'])
def test_late_pointer_callbacks_after_preview_cancellation_are_ignored(phone,exit_action):
    page,_,_=phone
    open_mock(page)
    page.get_by_role('button',name='Take this photo',exact=True).click()
    page.get_by_role('button',name='Use this photo',exact=True).wait_for()
    page.evaluate("""() => {
      window.oldHandle=document.querySelector('.capture-outline circle');
      // Mock pointer capture only; callbacks/DOM geometry use the real UI.
      oldHandle.setPointerCapture=()=>{};
      oldHandle.dispatchEvent(new PointerEvent('pointerdown',{pointerId:7,clientX:100,clientY:400}));
    }""")
    if exit_action=='close':page.keyboard.press('Escape')
    else:
        page.get_by_role('button',name='Retake',exact=True).click()
        page.wait_for_function("() => cameraStreams.length===2 && !document.querySelector('.capture-controls button').disabled")
        page.get_by_role('button',name='Take this photo',exact=True).click()
        page.get_by_role('button',name='Use this photo',exact=True).wait_for()
        accept_warnings(page)
        page.evaluate("() => window.newPreviewPoints=document.querySelector('.capture-outline polygon').getAttribute('points')")
    page.evaluate("""() => {
      oldHandle.dispatchEvent(new PointerEvent('pointermove',{pointerId:7,clientX:120,clientY:410}));
      oldHandle.dispatchEvent(new PointerEvent('pointerup',{pointerId:7}));
    }""")
    assert page.evaluate('() => recovery.uploads.size')==0
    if exit_action=='retake':
        assert page.evaluate("() => document.querySelector('.capture-outline polygon').getAttribute('points')===newPreviewPoints")
        assert page.locator('#document-capture input[type=checkbox]').is_checked()
        page.keyboard.press('Escape')
    assert_stopped(page)
