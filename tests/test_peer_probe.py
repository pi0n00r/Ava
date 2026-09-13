import importlib.util
import ipaddress
import socket
import sys
from pathlib import Path

import pytest


def _link_local(suffix):
    return str(ipaddress.IPv6Address((0xFE80 << 112) | suffix))


def _load_probe():
    root = Path(__file__).resolve().parents[1]
    path = root / "probe-ava-peer.py"
    if not path.exists():
        path = root / ".fleet-overlay" / "deployment-kit" / "probe-ava-peer.py"
    spec = importlib.util.spec_from_file_location("probe_ava_peer", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_interface_table_parses_only_link_local_ipv6():
    probe = _load_probe()
    table = "\n".join(
        [
            "fe800000000000000000000000000123 02 40 20 80 enp2s0",
            "20010db8000000000000000000000123 03 40 00 80 enp3s0",
            "not-an-address 04 40 20 80 bad0",
        ]
    )
    assert probe.parse_interface_table(table) == [(2, "enp2s0")]


def test_resolution_expands_unscoped_dns_result_across_local_link_interfaces():
    probe = _load_probe()

    def fake_getaddrinfo(*_args):
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (_link_local(0x123), 3003, 0, 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::123", 3003, 0, 0)),
        ]

    targets = probe.resolve_link_local_targets(
        "peer.future.example",
        3003,
        getaddrinfo=fake_getaddrinfo,
        interfaces=lambda: [(2, "enp2s0"), (4, "enp4s0")],
    )
    assert targets == [
        probe.LinkLocalTarget(_link_local(0x123), 2, "enp2s0"),
        probe.LinkLocalTarget(_link_local(0x123), 4, "enp4s0"),
    ]


def test_resolution_preserves_dns_scope_and_renders_bare_qualification_url():
    probe = _load_probe()

    def fake_getaddrinfo(*_args):
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (_link_local(0x456), 3003, 0, 7))]

    targets = probe.resolve_link_local_targets(
        "peer.future.example",
        3003,
        getaddrinfo=fake_getaddrinfo,
        if_indextoname=lambda index: {7: "enp7s0"}[index],
    )
    assert targets == [probe.LinkLocalTarget(_link_local(0x456), 7, "enp7s0")]
    assert probe.render_admin_url(targets[0].address, 3003) == f"http://[{_link_local(0x456)}]:3003/"
    assert "%" not in probe.render_admin_url(targets[0].address, 3003)


@pytest.mark.parametrize(
    "payload",
    [
        b"HTTP/1.1 503 Service Unavailable\r\n\r\nAsterisk AI Agent Admin",
        b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\nwrong application",
        b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n",
    ],
)
def test_admin_content_validation_rejects_unhelpful_responses(payload):
    probe = _load_probe()
    with pytest.raises(probe.ProbeError):
        probe.validate_admin_response(payload, "Asterisk AI Agent Admin")


def test_admin_content_validation_accepts_useful_page():
    probe = _load_probe()
    probe.validate_admin_response(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n"
        b"<title>Asterisk AI Agent Admin</title>",
        "Asterisk AI Agent Admin",
    )


def test_qualification_success_uses_same_address_and_scope_for_both_probes():
    probe = _load_probe()
    target = probe.LinkLocalTarget(_link_local(0x789), 3, "enp3s0")
    calls = []

    def admin(candidate, port, timeout, marker):
        calls.append(("admin", candidate, port, timeout, marker))

    def audio(candidate, port, timeout):
        calls.append(("audio", candidate, port, timeout))

    result = probe.qualify_peer(
        "peer.future.example",
        3003,
        8090,
        2.0,
        "Admin marker",
        resolver=lambda _host, _port: [target],
        admin_probe=admin,
        audiosocket_probe=audio,
    )
    assert result == target
    assert calls == [
        ("admin", target, 3003, 2.0, "Admin marker"),
        ("audio", target, 8090, 2.0),
    ]


def test_qualification_failure_does_not_report_partial_admin_success():
    probe = _load_probe()
    target = probe.LinkLocalTarget(_link_local(0xABC), 5, "enp5s0")

    def audio_failure(_candidate, _port, _timeout):
        raise OSError("connection refused")

    with pytest.raises(probe.ProbeError, match="no link-local candidate passed"):
        probe.qualify_peer(
            "peer.future.example",
            3003,
            8090,
            2.0,
            "Admin marker",
            resolver=lambda _host, _port: [target],
            admin_probe=lambda *_args: None,
            audiosocket_probe=audio_failure,
        )
