import importlib.util
import socket
from pathlib import Path


def _load_launcher():
    root = Path(__file__).resolve().parents[1]
    path = root / ".fleet-overlay" / "runtime" / "uvicorn_dual_stack.py"
    if not path.exists():
        path = root / "runtime" / "uvicorn_dual_stack.py"
    spec = importlib.util.spec_from_file_location("uvicorn_dual_stack", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_admin_listener_is_one_socket_accepting_ipv4_and_ipv6():
    launcher = _load_launcher()
    listener = launcher.create_listener("::", 0)
    port = listener.getsockname()[1]
    assert listener.family == socket.AF_INET6
    assert listener.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY) == 0
    accepted_listener_fds = []
    try:
        for host, family in (("127.0.0.1", socket.AF_INET), ("::1", socket.AF_INET6)):
            with socket.socket(family, socket.SOCK_STREAM) as client:
                client.settimeout(2)
                client.connect((host, port))
                server, _ = listener.accept()
                accepted_listener_fds.append(listener.fileno())
                server.close()
    finally:
        listener.close()
    assert len(set(accepted_listener_fds)) == 1


def test_launcher_keeps_stock_admin_import_root():
    launcher = _load_launcher()
    source = Path(launcher.__file__).read_text(encoding="utf-8")
    assert 'sys.path.insert(0, "/app")' in source


def test_compose_keeps_stock_entrypoint_and_appuser_drop():
    root = Path(__file__).resolve().parents[1]
    override = (root / "docker-compose.override.yml").read_text(encoding="utf-8")
    dockerfile = (root / "admin_ui" / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (root / "admin_ui" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "entrypoint:" not in override
    assert 'ENTRYPOINT ["/app/entrypoint.sh"]' in dockerfile
    assert 'exec gosu appuser "$@"' in entrypoint
