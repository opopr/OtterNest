from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = ROOT / "deploy" / "systemd"


def test_systemd_units_use_dedicated_service_identity_and_environment():
    names = (
        "otternest-api.service",
        "otternest-migrate.service",
        "otternest-scheduler.service",
        "otternest-workflows.service",
        "otternest-webhooks.service",
    )
    for name in names:
        content = (SYSTEMD / name).read_text(encoding="utf-8")
        assert "User=otternest" in content
        assert "Group=otternest" in content
        assert "EnvironmentFile=/etc/otternest/otternest.env" in content
        assert "NoNewPrivileges=true" in content
        assert "ProtectSystem=strict" in content
        assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in content


def test_api_only_binds_loopback_and_uses_one_worker():
    content = (SYSTEMD / "otternest-api.service").read_text(encoding="utf-8")
    assert "--host 127.0.0.1" in content
    assert "--workers 1" in content
    assert "--host 0.0.0.0" not in content


def test_nginx_template_proxies_to_loopback_and_uses_private_listener():
    content = (ROOT / "deploy" / "nginx" / "otternest.conf.example").read_text(encoding="utf-8")
    assert "listen 100.64.0.10:443 ssl http2;" in content
    assert "proxy_pass http://127.0.0.1:8000;" in content
    assert "proxy_set_header Upgrade $http_upgrade;" in content
    assert "proxy_buffering off;" in content
    assert "listen 0.0.0.0:" not in content


def test_server_environment_template_does_not_put_secrets_in_frontend_variables():
    content = (ROOT / "deploy" / "otternest.env.example").read_text(encoding="utf-8")
    assert "OTTERNEST_OWNER_TOKEN=REPLACE_" in content
    assert "OTTERNEST_DATABASE_URL=postgresql+psycopg:///otternest" in content
    assert "VITE_OWNER_TOKEN" not in content
    assert "VITE_DATABASE_URL" not in content
    assert "VITE_" not in content


def test_systemd_installer_has_valid_shell_syntax():
    bash = shutil.which("bash")
    if bash is None:
        return
    script = ROOT / "deploy" / "install-systemd.sh"
    subprocess.run([bash, "-n", str(script)], check=True)
