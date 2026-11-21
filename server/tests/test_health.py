import httpx
import pytest

from ci_guard.health import probe_health,validate_health_url


def test_probe_pins_validated_dns_target_and_preserves_host(monkeypatch):
    monkeypatch.setattr('ci_guard.health.socket.getaddrinfo',lambda *args,**kwargs:[(2,1,6,'',('10.2.3.4',80))])
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200)
    real_client = httpx.Client
    monkeypatch.setattr('ci_guard.health.httpx.Client',lambda **kwargs:real_client(transport=httpx.MockTransport(handler),**kwargs))
    assert probe_health({'health_url':'http://inventory:9000/health','timeout_seconds':1}) == ('healthy','HTTP 200')
    assert seen[0].url.host == '10.2.3.4'
    assert seen[0].headers['host'] == 'inventory:9000'


def test_metadata_dns_answer_is_rejected_before_http(monkeypatch):
    monkeypatch.setattr('ci_guard.health.socket.getaddrinfo',lambda *args,**kwargs:[(2,1,6,'',('169.254.169.254',80))])
    assert probe_health({'health_url':'http://innocent-name/health','timeout_seconds':1})[0] == 'unknown'


def test_redirect_is_unknown_and_not_followed(monkeypatch):
    monkeypatch.setattr('ci_guard.health.socket.getaddrinfo',lambda *args,**kwargs:[(2,1,6,'',('10.2.3.4',80))])
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(302,headers={'Location':'http://169.254.169.254/latest'})
    real_client = httpx.Client
    monkeypatch.setattr('ci_guard.health.httpx.Client',lambda **kwargs:real_client(transport=httpx.MockTransport(handler),**kwargs))
    assert probe_health({'health_url':'http://inventory/health','timeout_seconds':1})[0] == 'unknown'
    assert len(seen) == 1


@pytest.mark.parametrize('url',['http://[fd00:ec2::254]/latest','http://[fd20:ce::254]/metadata'])
def test_ipv6_metadata_is_rejected(url):
    with pytest.raises(ValueError,match='metadata'):
        validate_health_url(url)


def test_dns_resolution_obeys_total_probe_deadline(monkeypatch):
    import time
    from threading import Event
    release = Event()
    def blocked_dns(*args,**kwargs):
        release.wait(2)
        raise OSError('DNS unavailable')
    monkeypatch.setattr('ci_guard.health.socket.getaddrinfo',blocked_dns)
    start = time.monotonic()
    try:
        status,detail = probe_health({'health_url':'http://inventory/health','timeout_seconds':0.1})
        assert time.monotonic()-start<0.5
        assert status=='unhealthy' and 'timeout' in detail
    finally:
        release.set()
