"""The video's motion graphics: full-screen HTML/CSS cards laid over the page
being recorded (title, the handwriting scan, the phone, the packet, the end).
Plain CSS animation and inline SVG; one font (Inter, SIL Open Font License),
embedded so nothing is fetched while recording."""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path

NAVY, NAVY2, MINT, BLUE = "#0a1830", "#10264a", "#5fd4ad", "#3b82f6"


def font_face(font: Path) -> str:
    data = base64.b64encode(font.read_bytes()).decode()
    return f"@font-face{{font-family:DemoInter;src:url(data:font/ttf;base64,{data}) format('truetype');font-weight:100 900;}}"


BACKDROP = """
<div class="bg"><div class="glow g1"></div><div class="glow g2"></div><div class="grid"></div></div>
"""

BASE_CSS = f"""
.card *{{box-sizing:border-box;margin:0}}
.card{{position:absolute;inset:0;overflow:hidden;font-family:DemoInter,system-ui,sans-serif;color:#fff;background:{NAVY};-webkit-font-smoothing:antialiased}}
.bg{{position:absolute;inset:0}}
.glow{{position:absolute;border-radius:50%;filter:blur(80px)}}
.g1{{width:620px;height:620px;left:-120px;top:-160px;background:#1d4ed8;opacity:.42;animation:drift1 14s ease-in-out infinite alternate}}
.g2{{width:540px;height:540px;right:-140px;bottom:-200px;background:#14b8a6;opacity:.26;animation:drift2 16s ease-in-out infinite alternate}}
.grid{{position:absolute;inset:-60px;background-image:linear-gradient(rgba(255,255,255,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.05) 1px,transparent 1px);
  background-size:56px 56px;-webkit-mask-image:radial-gradient(ellipse at center,#000 30%,transparent 75%);animation:pan 18s linear infinite}}
@keyframes drift1{{to{{transform:translate(160px,90px) scale(1.12)}}}}
@keyframes drift2{{to{{transform:translate(-140px,-70px) scale(1.1)}}}}
@keyframes pan{{to{{transform:translate(56px,56px)}}}}
@keyframes rise{{from{{transform:translateY(105%)}}to{{transform:none}}}}
@keyframes fadeup{{from{{opacity:0;transform:translateY(14px);filter:blur(6px)}}to{{opacity:1;transform:none;filter:none}}}}
@keyframes track{{from{{opacity:0;letter-spacing:.7em}}to{{opacity:.8;letter-spacing:.32em}}}}
@keyframes pop{{0%{{opacity:0;transform:scale(.6)}}70%{{opacity:1;transform:scale(1.06)}}100%{{opacity:1;transform:scale(1)}}}}
@keyframes draw{{to{{stroke-dashoffset:0}}}}
@keyframes ring{{0%{{opacity:.55;transform:scale(1)}}100%{{opacity:0;transform:scale(1.9)}}}}
"""

ICONS = {
    "phone": '<rect x="-11" y="-17" width="22" height="34" rx="4" fill="none" stroke="#fff" stroke-width="2.2"/><line x1="-4" y1="12" x2="4" y2="12" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/>',
    "review": '<rect x="-13" y="-16" width="26" height="32" rx="4" fill="none" stroke="#fff" stroke-width="2.2"/><path d="M-6 -5l3 3 6-7M-6 8h12" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>',
    "packet": '<path d="M-12 -17h16l8 8v26h-24z" fill="none" stroke="#fff" stroke-width="2.2" stroke-linejoin="round"/><path d="M4 -17v8h8M-6 3h12M-6 9h8" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/>',
}


def title() -> str:
    nodes = [(110, "phone", "Client's phone", 2.15), (410, "review", "Paralegal review", 2.75), (710, "packet", "Ready-to-mail packet", 3.35)]
    svg_nodes = "".join(f"""
      <g transform="translate({x},60)"><g style="transform-origin:0 0;opacity:0;animation:pop .55s cubic-bezier(.2,.9,.3,1.2) {d:.2f}s forwards">
        <circle r="38" fill="{NAVY2}"/><circle r="38" fill="url(#nodefill)" stroke="rgba(255,255,255,.35)" stroke-width="1.5"/>
        <circle r="38" fill="none" stroke="{MINT}" stroke-width="2" style="transform-origin:0 0;animation:ring 1.6s ease-out {d + 0.6:.2f}s infinite"/>
        {ICONS[icon]}</g>
        <text y="78" text-anchor="middle" font-size="16" font-weight="500" fill="rgba(255,255,255,.85)" style="opacity:0;animation:fadeup .6s ease-out {d + 0.15:.2f}s forwards">{label}</text></g>"""
                        for x, icon, label, d in nodes)
    return f"""<style>{BASE_CSS}
      .t-wrap{{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;padding-bottom:20px}}
      .eyebrow{{font-size:14px;font-weight:600;text-transform:uppercase;opacity:0;animation:track 1.2s cubic-bezier(.2,.7,.2,1) .2s forwards}}
      .headline{{display:flex;gap:18px;margin-top:22px;font-size:72px;font-weight:750;letter-spacing:-.025em;line-height:1.05}}
      .mask{{overflow:hidden;padding:0 2px 8px}} .mask span{{display:inline-block;transform:translateY(105%);animation:rise .9s cubic-bezier(.2,.8,.2,1) forwards}}
      .grad{{background:linear-gradient(100deg,#fff 0%,{MINT} 85%);-webkit-background-clip:text;background-clip:text;color:transparent}}
      .sub{{margin-top:16px;font-size:22px;font-weight:400;color:rgba(255,255,255,.78);opacity:0;animation:fadeup .9s ease-out 1.25s forwards}}
      .flow{{margin-top:54px}}
    </style>
    <div class="card">{BACKDROP}<div class="t-wrap">
      <div class="eyebrow">Georges | Cote LLP</div>
      <div class="headline"><div class="mask"><span style="animation-delay:.45s">The</span></div>
        <div class="mask"><span class="grad" style="animation-delay:.57s">I-485</span></div>
        <div class="mask"><span style="animation-delay:.69s">Pipeline</span></div></div>
      <div class="sub">From the client's phone to a ready-to-mail packet</div>
      <svg class="flow" width="820" height="150" viewBox="0 0 820 150">
        <defs><radialGradient id="nodefill"><stop offset="0" stop-color="rgba(255,255,255,.16)"/><stop offset="1" stop-color="rgba(255,255,255,.04)"/></radialGradient>
          <linearGradient id="line" gradientUnits="userSpaceOnUse" x1="148" y1="60" x2="672" y2="60"><stop offset="0" stop-color="{BLUE}"/><stop offset="1" stop-color="{MINT}"/></linearGradient>
          <filter id="glow" x="-200%" y="-200%" width="500%" height="500%"><feGaussianBlur stdDeviation="4"/></filter></defs>
        <path id="flowpath" d="M148 60 H672" stroke="url(#line)" stroke-width="2.5" stroke-linecap="round" fill="none"
          stroke-dasharray="524" stroke-dashoffset="524" style="animation:draw 1.5s cubic-bezier(.5,0,.2,1) 2.1s forwards"/>
        <g style="opacity:0;animation:fadeup .3s linear 3.6s forwards">
          <circle r="9" fill="{MINT}" filter="url(#glow)"><animateMotion dur="2.2s" begin="3.6s" repeatCount="indefinite" path="M148 60 H672"/></circle>
          <circle r="4" fill="#fff"><animateMotion dur="2.2s" begin="3.6s" repeatCount="indefinite" path="M148 60 H672"/></circle></g>
        {svg_nodes}
      </svg></div></div>"""


def end() -> str:
    return f"""<style>{BASE_CSS}
      .e-wrap{{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center}}
      .e-title{{font-size:60px;font-weight:750;letter-spacing:-.025em;opacity:0;animation:fadeup 1s cubic-bezier(.2,.8,.2,1) .15s forwards}}
      .e-sub{{margin-top:14px;font-size:22px;color:rgba(255,255,255,.8);opacity:0;animation:fadeup .9s ease-out .55s forwards}}
      .e-bar{{width:72px;height:4px;border-radius:2px;background:linear-gradient(90deg,{BLUE},{MINT});margin-top:30px;transform:scaleX(0);animation:grow .8s ease-out .9s forwards}}
      .e-firm{{margin-top:26px;font-size:14px;font-weight:600;letter-spacing:.32em;text-transform:uppercase;opacity:0;animation:fadeup .8s ease-out 1.1s forwards}}
      .e-note{{position:absolute;bottom:34px;font-size:13px;color:rgba(255,255,255,.5);opacity:0;animation:fadeup .8s ease-out 1.5s forwards}}
      @keyframes grow{{to{{transform:scaleX(1)}}}}
    </style>
    <div class="card">{BACKDROP}<div class="e-wrap">
      <div class="e-title">The <span style="background:linear-gradient(100deg,#fff,{MINT});-webkit-background-clip:text;background-clip:text;color:transparent">I-485</span> Pipeline</div>
      <div class="e-sub">From the client's phone to a ready-to-mail packet</div>
      <div class="e-bar"></div><div class="e-firm">Georges | Cote LLP</div>
      <div class="e-note">Demonstration with made-up clients · no real client data shown</div></div></div>"""


def scan(preview: Path, reads: list[tuple[str, str]]) -> str:
    """The paper form being read: a scan line passes over the page and what was
    read appears beside it (values taken from the pipeline's own reading)."""
    img = base64.b64encode(preview.read_bytes()).decode()
    chips = "".join(f"""<div class="chip" style="animation-delay:{1.6 + i * 0.55:.2f}s"><div class="k">{html.escape(k)}</div><div class="v">{html.escape(v)}</div></div>"""
                    for i, (k, v) in enumerate(reads))
    return f"""<style>{BASE_CSS}
      .s-wrap{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;gap:64px}}
      .paper{{position:relative;height:600px;transform:rotate(-1.2deg);box-shadow:0 30px 70px rgba(0,0,0,.5);border-radius:3px;overflow:hidden;
        opacity:0;animation:fadeup .7s ease-out .1s forwards}}
      .paper img{{height:100%;display:block}}
      .beam{{position:absolute;left:0;right:0;height:90px;top:-90px;background:linear-gradient(180deg,transparent,rgba(95,212,173,.38) 60%,rgba(95,212,173,.85) 98%,transparent);
        animation:sweep 2.6s cubic-bezier(.45,0,.55,1) .7s 2 forwards;mix-blend-mode:multiply}}
      @keyframes sweep{{to{{top:100%}}}}
      .side{{width:430px}}
      .s-eyebrow{{font-size:13px;font-weight:600;letter-spacing:.24em;text-transform:uppercase;color:{MINT};opacity:0;animation:fadeup .6s ease-out .4s forwards}}
      .s-title{{font-size:34px;font-weight:700;letter-spacing:-.02em;margin:10px 0 26px;line-height:1.15;opacity:0;animation:fadeup .7s ease-out .6s forwards}}
      .chip{{background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.14);border-radius:12px;padding:12px 16px;margin-bottom:12px;opacity:0;animation:fadeup .6s ease-out forwards}}
      .chip .k{{font-size:12.5px;color:rgba(255,255,255,.6);text-transform:uppercase;letter-spacing:.08em}}
      .chip .v{{font-size:19px;font-weight:600;margin-top:3px}}
    </style>
    <div class="card">{BACKDROP}<div class="s-wrap">
      <div class="paper"><img src="data:image/png;base64,{img}"><div class="beam"></div></div>
      <div class="side"><div class="s-eyebrow">Paper questionnaire</div><div class="s-title">Handwriting in,<br>form-ready answers out</div>{chips}</div>
    </div></div>"""


def phone(body: str, sender: str = "Georges | Cote") -> str:
    """The reminder arriving as a text: the real message from the outbox, its
    link made tappable (#__sms_link)."""
    text, _, rest = body.partition("http")
    link, _, after = ("http" + rest).partition(" ")
    msg = f"""{html.escape(text)}<a id="__sms_link">{html.escape(link)}</a> {html.escape(after)}"""
    return f"""<style>{BASE_CSS}
      .p-wrap{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;gap:90px}}
      .p-side{{width:420px}}
      .p-eyebrow{{font-size:13px;font-weight:600;letter-spacing:.24em;text-transform:uppercase;color:{MINT};opacity:0;animation:fadeup .6s ease-out .2s forwards}}
      .p-title{{font-size:36px;font-weight:700;letter-spacing:-.02em;margin:10px 0 18px;line-height:1.15;opacity:0;animation:fadeup .7s ease-out .35s forwards}}
      .p-point{{display:flex;gap:12px;align-items:center;font-size:17px;color:rgba(255,255,255,.85);margin-top:12px;opacity:0;animation:fadeup .6s ease-out forwards}}
      .p-point i{{width:24px;height:24px;border-radius:50%;background:rgba(95,212,173,.18);border:1.5px solid {MINT};flex:none;
        display:flex;align-items:center;justify-content:center;font-style:normal;font-size:13px;color:{MINT};font-weight:700}}
      .device{{width:300px;height:610px;border-radius:46px;background:#0b0f17;padding:12px;box-shadow:0 40px 90px rgba(0,0,0,.55),0 0 0 2px #2a3242 inset;
        opacity:0;animation:phonein .8s cubic-bezier(.2,.8,.2,1) .1s forwards}}
      @keyframes phonein{{from{{opacity:0;transform:translateY(60px) rotate(3deg)}}to{{opacity:1;transform:none}}}}
      .screen{{position:relative;height:100%;border-radius:36px;background:#f2f3f7;overflow:hidden;color:#111}}
      .notch{{position:absolute;top:8px;left:50%;transform:translateX(-50%);width:96px;height:26px;border-radius:14px;background:#0b0f17}}
      .status{{display:flex;justify-content:space-between;padding:12px 26px 0;font-size:13px;font-weight:600}}
      .head{{text-align:center;padding:26px 0 12px;border-bottom:1px solid #e3e5ea;background:#f7f8fa}}
      .avatar{{width:46px;height:46px;border-radius:50%;margin:0 auto 6px;background:linear-gradient(135deg,#1e3a6b,#2f6bd8);color:#fff;font-size:15px;font-weight:700;display:flex;align-items:center;justify-content:center}}
      .who{{font-size:13px;font-weight:600}}
      .thread{{padding:16px 14px}}
      .when{{text-align:center;font-size:11px;color:#8a8f98;margin-bottom:10px;opacity:0;animation:fadeup .3s ease-out .9s forwards}}
      .typing{{position:absolute;display:inline-flex;gap:5px;background:#e3e5ea;border-radius:18px;padding:12px 14px;opacity:0;animation:typing 1.1s ease .9s forwards}}
      .typing b{{width:7px;height:7px;border-radius:50%;background:#9aa0a8;animation:dot 1s infinite}} .typing b:nth-child(2){{animation-delay:.15s}} .typing b:nth-child(3){{animation-delay:.3s}}
      @keyframes typing{{0%{{opacity:0}}15%{{opacity:1}}85%{{opacity:1}}100%{{opacity:0;height:0;padding:0}}}}
      @keyframes dot{{50%{{transform:translateY(-4px);opacity:.5}}}}
      .bubble{{background:#e3e5ea;border-radius:18px 18px 18px 6px;padding:11px 14px;font-size:14px;line-height:1.4;max-width:240px;opacity:0;transform-origin:left bottom;
        animation:pop .45s cubic-bezier(.2,.9,.3,1.2) 2.05s forwards}}
      .bubble a{{display:block;margin:2px 0;color:#1d63d8;text-decoration:underline;word-break:break-all}}
    </style>
    <div class="card">{BACKDROP}<div class="p-wrap">
      <div class="p-side"><div class="p-eyebrow">Follow-up</div><div class="p-title">A reminder, straight<br>to her phone</div>
        <div class="p-point" style="animation-delay:.9s"><i>✓</i>A new secure sign-in link</div>
        <div class="p-point" style="animation-delay:1.25s"><i>✓</i>No case details in the message</div>
        <div class="p-point" style="animation-delay:1.6s"><i>✓</i>Only on channels she agreed to</div></div>
      <div class="device"><div class="screen"><div class="notch"></div><div class="status"><span>9:41</span><span>●●● 5G</span></div>
        <div class="head"><div class="avatar">G|C</div><div class="who">{html.escape(sender)}</div></div>
        <div class="thread"><div class="when">Text Message · Today 9:41 AM</div>
          <div class="typing"><b></b><b></b><b></b></div><div class="bubble">{msg}</div></div></div></div>
    </div></div>"""


def gallery(pages_dir: Path) -> str:
    info = json.loads((pages_dir / "pages.json").read_text())
    cards = []
    for i, p in enumerate(info["pages"][:4]):  # index, G-28, I-485, I-765: four fit across the screen
        data = base64.b64encode((pages_dir / p["file"]).read_bytes()).decode()
        cards.append(f"""<div class="pg" style="animation-delay:{0.35 + i * 0.45:.2f}s"><img src="data:image/png;base64,{data}">
          <div class="lbl">{html.escape(p['label'])} <span>· page {p['page']}</span></div></div>""")
    return f"""<style>{BASE_CSS}
      .g-wrap{{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:36px}}
      .g-title{{font-size:32px;font-weight:700;letter-spacing:-.02em;opacity:0;animation:fadeup .7s ease-out .1s forwards}}
      .g-row{{display:flex;gap:24px;align-items:flex-end}}
      .pg{{display:flex;flex-direction:column;align-items:center;gap:12px;opacity:0;animation:fadeup .7s cubic-bezier(.2,.8,.2,1) forwards}}
      .pg img{{height:360px;border-radius:4px;box-shadow:0 22px 50px rgba(0,0,0,.5);background:#fff}}
      .lbl{{font-size:16px;font-weight:600}} .lbl span{{font-weight:400;color:rgba(255,255,255,.6)}}
    </style>
    <div class="card">{BACKDROP}<div class="g-wrap"><div class="g-title">One packet · {info['total']} pages · every signature page marked</div>
      <div class="g-row">{''.join(cards)}</div></div></div>"""
