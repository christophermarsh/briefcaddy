"""Real local algorithm on fictional canvas geometry and quality degradation."""
import os
from pathlib import Path

import pytest
from capture_browser_fixture import sandboxed_chromium  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get('E2E'), reason='E2E=1 requests Chromium algorithm probes')


@pytest.fixture
def vision():
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as driver:
        browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None, chromium_sandbox=True)
        page = browser.new_page()
        page.set_content('<html><body></body></html>')
        page.add_script_tag(content=(Path(__file__).resolve().parents[1]/'src/portal/static/capture.js').read_text())
        page.evaluate("""() => {
          window.fictional=(p,size=[1600,1200],degrade='none')=>{
            const c=document.createElement('canvas');c.width=size[0];c.height=size[1];const ctx=c.getContext('2d');
            ctx.fillStyle='#353535';ctx.fillRect(0,0,c.width,c.height);ctx.fillStyle='#dedede';ctx.beginPath();p.forEach((q,i)=>i?ctx.lineTo(q[0]*c.width,q[1]*c.height):ctx.moveTo(q[0]*c.width,q[1]*c.height));ctx.closePath();ctx.fill();ctx.save();ctx.clip();
            ctx.fillStyle='#181818';for(let y=.25;y<.78;y+=.055)ctx.fillRect(.28*c.width,y*c.height,.40*c.width,.012*c.height);
            if(degrade==='glare'){ctx.fillStyle='white';ctx.fillRect(.45*c.width,.4*c.height,.16*c.width,.22*c.height);}
            ctx.restore();
            if(degrade==='blur'){const blurred=document.createElement('canvas');blurred.width=c.width;blurred.height=c.height;const b=blurred.getContext('2d');b.filter='blur(24px)';b.drawImage(c,0,0);return blurred;}
            return c;
          };
        }""")
        yield page
        browser.close()


@pytest.mark.parametrize('p', [
    [[.15,.10],[.85,.10],[.85,.90],[.15,.90]],
    [[.18,.15],[.80,.08],[.89,.85],[.12,.92]],
    [[.12,.25],[.9,.18],[.82,.80],[.20,.84]],
    [[.25,.08],[.70,.20],[.83,.87],[.14,.78]],
])
def test_detects_fictional_perspective_quadrilaterals(vision,p):
    result = vision.evaluate('p => CaptureVision.analyze(fictional(p))',p)
    assert result['corners'] is not None
    for found,expected in zip(result['corners'],p):
        assert max(abs(found[i]-expected[i]) for i in range(2)) < .025
    assert 'blur' not in result['flags']
    assert 'readable' not in result and 'readable' not in result['flags']


def test_quality_degradation_actionable_flags_and_resolution(vision):
    results = vision.evaluate("""() => {
      const p=[[.1,.1],[.9,.1],[.9,.9],[.1,.9]];
      return Object.fromEntries(['none','blur','glare'].map(kind=>[kind,CaptureVision.analyze(fictional(p,[1600,1200],kind))]).concat([['small',CaptureVision.analyze(fictional(p,[640,480]))],['cutoff',CaptureVision.analyze(fictional([[0,.1],[.9,.1],[.9,.9],[0,.9]]))]]));
    }""")
    assert 'blur' not in results['none']['flags']
    assert 'glare' not in results['none']['flags']
    assert 'blur' in results['blur']['flags']
    assert 'glare' in results['glare']['flags']
    assert 'resolution' in results['small']['flags']
    assert 'cutoff' in results['cutoff']['flags']


def test_real_projective_pixels_and_transformed_dimensions(vision):
    result = vision.evaluate("""async () => {
      const p=[[.18,.15],[.80,.08],[.89,.85],[.12,.92]],source=fictional(p),ctx=source.getContext('2d'),m=CaptureVision.homography(p);
      const center=CaptureVision.map(m,.5,.5);ctx.fillStyle='#ef1212';ctx.fillRect(center[0]*source.width-30,center[1]*source.height-30,60,60);
      const corrected=await CaptureVision.correct(source,p);const sample=Array.from(corrected.getContext('2d').getImageData(Math.floor(corrected.width/2),Math.floor(corrected.height/2),1,1).data);
      return {size:[corrected.width,corrected.height],sample,corners:[[0,0],[1,0],[1,1],[0,1]].map(q=>CaptureVision.map(m,...q))};
    }""")
    assert result['size'][0]*result['size'][1] <= 2_000_000 and max(result['size']) <= 1600
    assert result['sample'][0] > 220 and result['sample'][1] < 30 and result['sample'][2] < 30
    for found,expected in zip(result['corners'],[[.18,.15],[.80,.08],[.89,.85],[.12,.92]]):
        assert found == pytest.approx(expected,abs=1e-9)


def test_invalid_geometry_bound_analysis_and_correction_cancellation(vision):
    result = vision.evaluate("""async () => {
      const invalid=[[[0,0],[1,1],[1,0],[0,1]],[[0,0],[1,0],[1,1],[0,NaN]],[[0,0],[0,0],[0,0],[0,0]]];
      const answers=invalid.map(p=>{try{CaptureVision.homography(p);return false;}catch(e){return true;}});
      for(const dimensions of [[10000,10000],[1,4],[-2,4],[NaN,4],[2.5,4]]){try{CaptureVision.detect({width:dimensions[0],height:dimensions[1],data:[]});answers.push(false);}catch(e){answers.push(true);}}
      try{await CaptureVision.correct({width:10000,height:10000},[[0,0],[1,0],[1,1],[0,1]]);answers.push(false);}catch(e){answers.push(e.message==='original_size');}
      const p=[[.1,.1],[.9,.1],[.9,.9],[.1,.9]];try{await CaptureVision.correct(fictional(p),p,()=>false);answers.push(false);}catch(e){answers.push(e.message==='capture_canceled');}
      return answers;
    }""")
    assert all(result)


@pytest.mark.parametrize('size', [[1200,1200],[2400,400],[400,2400]])
def test_square_and_extreme_aspect_frames_stay_bounded(vision,size):
    result=vision.evaluate('size => CaptureVision.analyze(fictional([[.1,.1],[.9,.1],[.9,.9],[.1,.9]],size))',size)
    assert result['corners'] is not None


def test_white_printed_page_and_near_total_washout_are_distinct_warnings(vision):
    result=vision.evaluate("""() => {
      const p=[[.1,.1],[.9,.1],[.9,.9],[.1,.9]],normal=fictional(p),ctx=normal.getContext('2d');
      // Change paper to white while retaining many fictional dark text lines.
      const pixels=ctx.getImageData(0,0,normal.width,normal.height);for(let i=0;i<pixels.data.length;i+=4)if(pixels.data[i]===222)pixels.data[i]=pixels.data[i+1]=pixels.data[i+2]=255;ctx.putImageData(pixels,0,0);
      const washed=document.createElement('canvas');washed.width=normal.width;washed.height=normal.height;const w=washed.getContext('2d');w.fillStyle='#353535';w.fillRect(0,0,washed.width,washed.height);w.fillStyle='white';w.fillRect(.1*washed.width,.1*washed.height,.8*washed.width,.8*washed.height);w.fillStyle='#111';w.fillRect(.48*washed.width,.48*washed.height,.025*washed.width,.012*washed.height);
      return {normal:CaptureVision.analyze(normal),washed:CaptureVision.analyze(washed)};
    }""")
    assert 'glare' not in result['normal']['flags']
    assert 'glare' in result['washed']['flags']
