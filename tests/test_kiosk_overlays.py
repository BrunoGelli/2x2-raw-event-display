"""Asset and presentation regressions; no DAQ, WebGL or browser required."""
from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / 'raw_display' / 'static'


def test_official_logo_is_bundled_and_transparent_format():
    logo = STATIC / '2x2-official-logo.webp'
    data = logo.read_bytes()
    assert data[:4] == b'RIFF' and data[8:12] == b'WEBP'
    assert len(data) > 1000
    html = (STATIC / 'index.html').read_text()
    assert 'id="kiosk-logo"' in html
    assert 'src="/static/2x2-official-logo.webp"' in html
    assert 'loading="lazy"' in html


def test_kiosk_has_transparent_clock_and_no_permanent_bar():
    css = (STATIC / 'kiosk.css').read_text()
    assert 'body.kiosk-active > .playback-clock {' in css
    assert 'background: transparent !important;' in css
    assert 'border: 0 !important;' in css
    assert 'body.kiosk-active #playback-clock-time' in css
    assert 'color: var(--text);' in css
    assert 'body.kiosk-active #kiosk-logo' in css
    assert 'pointer-events: none;' in css
    assert '#kiosk-toolbar { position: fixed; bottom: 14px; right: 14px;' in css
    assert 'body.kiosk-active > .playback-clock,' not in css
