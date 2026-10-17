"""The port the Epilykos dashboard listens on (Settings -> System).

Kept on DATA so it survives OS updates and slot switches:

  /data/epilykos/port   one line, the port number; absent = 3000

80 (so http://epilykos.local works with no port) or 1024-65535, except the
ports systemd-resolved itself listens on. epilykos-run reads the same file
when it starts the container, with the same rules.
"""
import errno
import os
import socket

PORT_FILE = os.environ.get('EPILYKOS_PORT_FILE', '/data/epilykos/port')
DEFAULT_PORT = 3000
RESERVED = {5353: 'mDNS', 5355: 'LLMNR'}


class PortError(ValueError):
    """An unusable port; the message says why."""


def validate(value):
    if isinstance(value, bool):
        raise PortError('the port must be a number')
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if not isinstance(value, int):
        raise PortError('the port must be a number')
    if value != 80 and not 1024 <= value <= 65535:
        raise PortError('the port must be 80 or between 1024 and 65535')
    if value in RESERVED:
        raise PortError(f'port {value} is used by the system ({RESERVED[value]})')
    return value


def read(path=None):
    """The configured port, or 3000 when unset or unreadable."""
    try:
        with open(path or PORT_FILE) as f:
            return validate(f.readline().strip())
    except (OSError, PortError):
        return DEFAULT_PORT


def write(port, path=None):
    path = path or PORT_FILE
    port = validate(port)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        f.write(f'{port}\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return port


def in_use(port):
    """True if something on the box already listens on this TCP port."""
    for family, addr in ((socket.AF_INET, '0.0.0.0'), (socket.AF_INET6, '::')):
        try:
            s = socket.socket(family, socket.SOCK_STREAM)
        except OSError:
            continue  # no IPv6
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            s.bind((addr, port))
        except OSError as e:
            if e.errno == errno.EADDRINUSE:
                return True
        finally:
            s.close()
    return False


def dashboard_url(host, port=None):
    port = read() if port is None else port
    return f'http://{host}/' if port == 80 else f'http://{host}:{port}/'
