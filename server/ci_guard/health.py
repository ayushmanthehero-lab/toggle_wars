"""Administrator-registered, bounded health probes without redirects."""
import ipaddress
import socket
from queue import Empty, Queue
from threading import BoundedSemaphore, Thread
from urllib.parse import urlsplit

import httpx


METADATA_HOSTS = {'metadata.google.internal', 'metadata', 'instance-data', 'metadata.aws.internal'}
METADATA_ADDRESSES = {'100.100.100.200','fd00:ec2::254','fd20:ce::254'}
PROBE_WORKERS = BoundedSemaphore(8)


def forbidden_address(address):
    try:
        ip = ipaddress.ip_address(address.split('%', 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_link_local or ip.is_unspecified or ip.is_multicast or str(ip) in METADATA_ADDRESSES


def validate_health_url(url):
    if any(ord(char) < 33 or ord(char) == 127 for char in url):
        raise ValueError('Health URL must not contain whitespace or control characters')
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ValueError('Health URL must use http or https and contain a hostname')
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError('Health URL must not contain credentials or a fragment')
    try:
        parsed.port
    except ValueError as error:
        raise ValueError('Invalid health URL port') from error
    hostname = parsed.hostname.lower().rstrip('.')
    if hostname in METADATA_HOSTS or hostname.endswith('.metadata.google.internal') or forbidden_address(hostname):
        raise ValueError('Cloud metadata and link-local health targets are forbidden')
    return url


def _probe_health(dependency):
    """Unknown transport failures do not establish an outage by themselves."""
    try:
        url = validate_health_url(dependency['health_url'])
        parsed = urlsplit(url)
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM)
        if not addresses or any(forbidden_address(item[4][0]) for item in addresses):
            return 'unknown', 'Health target resolved to a forbidden address'
        # Private service addresses are intentionally permitted for this self-hosted application.
        # No redirects and no environment proxy prevent redirect/proxy metadata bypasses.
        target = httpx.URL(url).copy_with(host=addresses[0][4][0])
        with httpx.Client(timeout=dependency['timeout_seconds'], follow_redirects=False, trust_env=False) as client:
            with client.stream('GET', target, headers={'User-Agent':'CI-Guard/1.0','Host':parsed.netloc},
                               extensions={'sni_hostname':parsed.hostname}) as response:
                status = response.status_code
        if 200 <= status < 300:
            return 'healthy', f'HTTP {status}'
        if status >= 500:
            return 'unhealthy', f'HTTP {status}'
        return 'unknown', f'HTTP {status}; expected a successful health response'
    except (httpx.TimeoutException, httpx.ConnectError):
        return 'unhealthy', 'Health endpoint timed out or refused the connection'
    except (ValueError, OSError, httpx.HTTPError):
        return 'unknown', 'Health endpoint could not be verified'


def probe_health(dependency):
    """Bound the whole check, including libc DNS and slowly streamed headers.

    A small daemon worker pool prevents stuck DNS from blocking coordinator
    threads or process shutdown. Timed-out workers cannot write state, and at
    most eight network checks can remain outstanding across dependencies.
    """
    if not PROBE_WORKERS.acquire(blocking=False):
        return 'unknown','Health probe capacity is busy; retry on the next eligible run'
    results = Queue(maxsize=1)
    def work():
        try:
            results.put(_probe_health(dependency))
        except Exception:
            results.put(('unknown','Health endpoint could not be verified'))
        finally:
            PROBE_WORKERS.release()
    Thread(target=work,daemon=True,name='ci-guard-health-probe').start()
    try:
        return results.get(timeout=dependency['timeout_seconds'])
    except Empty:
        return 'unhealthy','Health check exceeded its configured total timeout'
