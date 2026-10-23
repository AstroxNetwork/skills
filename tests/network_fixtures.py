"""Deterministic DNS for fixture hosts; production URL validation still runs."""
import ipaddress
import socket


def getaddrinfo(host, port, *args, **kwargs):
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not host.endswith('.example'):
            raise AssertionError('Unexpected DNS fixture host: ' + host)
        address = ipaddress.ip_address('93.184.216.34')
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (str(address), port))]
