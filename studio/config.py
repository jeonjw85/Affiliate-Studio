"""Explicit hostname lists for LAN access and local model servers."""
import ipaddress
import re


def normalize_host(host):
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return host.lower()


def host_list(value, name, defaults=()):
    hosts = set(defaults)
    for host in value.split(','):
        host = normalize_host(host.strip())
        if not host:
            continue
        try:
            ipaddress.ip_address(host)
        except ValueError:
            label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
            if len(host) > 253 or not re.fullmatch(rf'{label}(?:\.{label})*', host):
                raise ValueError(f'{name}에는 쉼표로 구분한 IP 또는 호스트명 필요 (URL·포트·와일드카드 제외)') from None
        hosts.add(host)
    return hosts
