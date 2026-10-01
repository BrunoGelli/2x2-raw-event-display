"""No sockets: audit-client errors must distinguish old server from disabled audit."""
import importlib.util
import io
import json
from pathlib import Path
import urllib.error

import pytest


def load_client():
    path = Path(__file__).parents[1] / 'tools' / 'inspect_timing_audit.py'
    spec = importlib.util.spec_from_file_location('audit_client_errors', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_404_reports_serving_process_mismatch(monkeypatch):
    client = load_client()
    def request(url, timeout):
        assert url == 'http://127.0.0.1:8765/api/timing-audit'
        raise urllib.error.HTTPError(url, 404, 'Not Found', {}, io.BytesIO(b''))
    monkeypatch.setattr(client.urllib.request, 'urlopen', request)
    with pytest.raises(ValueError) as raised:
        client.fetch('http://127.0.0.1:8765')
    message = str(raised.value)
    assert 'old running process' in message
    assert '--time-basis asic' in message
    assert 'even when capture is disabled' in message
    assert 'additional PACMAN subscriber' in message


def test_other_http_errors_are_preserved(monkeypatch):
    client = load_client()
    def request(url, timeout):
        raise urllib.error.HTTPError(url, 503, 'Unavailable', {}, io.BytesIO(b''))
    monkeypatch.setattr(client.urllib.request, 'urlopen', request)
    with pytest.raises(urllib.error.HTTPError) as raised:
        client.fetch('http://127.0.0.1:8765')
    assert raised.value.code == 503


@pytest.mark.parametrize('origin', ['http://example.org:8765', 'https://127.0.0.1',
                                  'http://127.0.0.1:8765/api', 'http://user@127.0.0.1'])
def test_invalid_origins_fail_before_request(monkeypatch, origin):
    client = load_client()
    def forbidden(*args, **kwargs):
        raise AssertionError('no request allowed')
    monkeypatch.setattr(client.urllib.request, 'urlopen', forbidden)
    with pytest.raises(ValueError, match='loopback origin'):
        client.fetch(origin)


@pytest.mark.parametrize('age,enabled', [(0.1, [6]), (0.1, [])])
def test_valid_responses_keep_enabled_state(monkeypatch, age, enabled):
    client = load_client()
    payload = dict(session_id='test-session', capture_age_s=age,
                   enabled_iogs=enabled, audits=[])
    monkeypatch.setattr(client.urllib.request, 'urlopen',
                        lambda *a, **k: io.BytesIO(json.dumps(payload).encode()))
    assert client.fetch('http://127.0.0.1:8765/') == payload


def test_stale_response_is_not_a_missing_route(monkeypatch):
    client = load_client()
    payload = dict(session_id='test-session', capture_age_s=6.0)
    monkeypatch.setattr(client.urllib.request, 'urlopen',
                        lambda *a, **k: io.BytesIO(json.dumps(payload).encode()))
    with pytest.raises(ValueError, match='No fresh audit snapshot'):
        client.fetch('http://127.0.0.1:8765')
