"""Startup inputs stay compatible without opening accounts or starting services."""

from pathlib import Path
from types import SimpleNamespace

import pytest

import schema_path
from law_app.bootstrap.config import staff_arguments
from law_app.bootstrap.composition import build_staff_app


def test_defaults_follow_installation_not_working_directory(tmp_path, monkeypatch):
    repo = tmp_path / "fictional-installation"
    monkeypatch.chdir(tmp_path)
    args = staff_arguments([], repo=repo, environ={})
    assert args.data == repo / "data" / "clients"
    assert args.deployment == repo / "deployment.json"
    assert args.portal == repo / "data" / "portal"
    assert args.users == repo / "data" / "review_users.json"
    assert args.field_map == schema_path.path("field_map", "i485", schema_path.schemas_in(repo))
    assert args.template == schema_path.path("template", "i485", schema_path.schemas_in(repo))
    assert args.policy == schema_path.path("law", "policy_sijs", schema_path.schemas_in(repo))
    assert (args.host, args.port) == ("127.0.0.1", 8485)
    assert not args.secure_cookies and not args.behind_tls_proxy
    assert args.hostname == args.trusted_proxy == []
    assert not repo.exists(), "Parsing must not initialize an installation."


@pytest.mark.parametrize("portal", ["relative-portal", ""])
def test_portal_environment_is_read_at_call_time_and_cli_wins(tmp_path, monkeypatch, portal):
    monkeypatch.setenv("PORTAL_DATA", portal)
    assert staff_arguments([], repo=tmp_path).portal == Path(portal)
    assert staff_arguments(["--portal", "cli-portal"], repo=tmp_path).portal == Path("cli-portal")


def test_explicit_paths_and_security_options_survive_parsing(tmp_path):
    args = staff_arguments([
        "--data", "case-data", "--field-map", "fields.json", "--template", "form.pdf",
        "--policy", "policy.json", "--users", "accounts.json", "--port", "8499",
        "--host", "0.0.0.0", "--hostname", "first.example.test", "--hostname", "second.example.test",
        "--secure-cookies", "--behind-tls-proxy", "--trusted-proxy", "127.0.0.1", "--trusted-proxy", "::1",
    ], repo=tmp_path, environ={})
    assert [args.data, args.field_map, args.template, args.policy, args.users] == [
        Path(p) for p in ("case-data", "fields.json", "form.pdf", "policy.json", "accounts.json")]
    assert (args.host, args.port) == ("0.0.0.0", 8499)
    assert args.hostname == ["first.example.test", "second.example.test"]
    assert args.trusted_proxy == ["127.0.0.1", "::1"]
    assert args.secure_cookies and args.behind_tls_proxy


@pytest.mark.parametrize("has_accounts", [False, True])
def test_composition_preserves_constructor_identity_count_and_inputs(tmp_path, has_accounts):
    args = staff_arguments(["--secure-cookies", "--behind-tls-proxy", "--trusted-proxy", "::1"],
                           repo=tmp_path, environ={})
    if has_accounts:
        args.users.parent.mkdir()
        args.users.write_text("{}", encoding="utf-8")
    account, app, calls = object(), object(), []

    def accounts_factory(path):
        calls.append(("accounts", path))
        return account

    def app_factory(*values, **options):
        calls.append(("app", values, options))
        return app

    result = build_staff_app(args, accounts_factory=accounts_factory,
                             app_factory=app_factory, local_setup=False)
    expected_account = account if has_accounts else None
    assert result == (app, expected_account)
    assert [c[0] for c in calls] == (["accounts", "app"] if has_accounts else ["app"])
    if has_accounts:
        assert calls[0][1] is args.users
    assert calls[-1][1] == (args.data, args.field_map, args.template, args.policy,
                            args.portal, expected_account, True)
    assert calls[-1][2] == {"behind_tls": True, "trusted_proxies": ("::1",), "local_setup": False,
                            "deployment_path": args.deployment}
    assert args.users.exists() is has_accounts


@pytest.mark.parametrize("failing", ["accounts", "app"])
def test_composition_propagates_constructor_failure_without_retry(tmp_path, failing):
    args = staff_arguments([], repo=tmp_path, environ={})
    args.users.parent.mkdir()
    args.users.write_text("{}", encoding="utf-8")
    error, calls = RuntimeError("synthetic construction failure"), []

    def accounts_factory(path):
        calls.append("accounts")
        if failing == "accounts":
            raise error
        return object()

    def app_factory(*values, **options):
        calls.append("app")
        raise error

    with pytest.raises(RuntimeError) as caught:
        build_staff_app(args, accounts_factory=accounts_factory,
                        app_factory=app_factory, local_setup=True)
    assert caught.value is error
    assert calls == (["accounts"] if failing == "accounts" else ["accounts", "app"])


def test_main_retains_launch_order_after_composition(tmp_path, monkeypatch, capsys):
    from review import server as srv

    calls = []
    args = staff_arguments([], repo=tmp_path, environ={})
    args.users.parent.mkdir()
    args.users.write_text("{}", encoding="utf-8")
    accounts = SimpleNamespace(needs_setup=lambda: True, users=lambda: {})
    def setup_code():
        calls.append("setup-code")
        return "fictional-code"

    accounts.console_setup_code = setup_code
    app = SimpleNamespace(warm_up=lambda: None,
                          clio_resume=lambda: calls.append("clio"),
                          start_posture=lambda: calls.append("posture"))

    def accounts_factory(path):
        calls.append("accounts")
        return accounts

    def app_factory(*values, **options):
        calls.append("app")
        assert values[5] is accounts and options["local_setup"] is True
        return app

    def serve(*values):
        calls.append("serve")
        assert values[0] is app
        return SimpleNamespace(serve_forever=lambda: calls.append("serving"))

    monkeypatch.setattr(srv, "staff_arguments", lambda *a, **k: args)
    monkeypatch.setattr(srv, "Accounts", accounts_factory)
    monkeypatch.setattr(srv, "ReviewApp", app_factory)
    monkeypatch.setattr(srv, "serve", serve)
    monkeypatch.setattr(srv.jobs, "sweep", lambda *a: calls.append("sweep"))
    monkeypatch.setattr(srv.jobs, "ensure_worker", lambda *a: calls.append("worker"))
    monkeypatch.setattr("classify.translate.warm_up", lambda: None)
    monkeypatch.setattr(srv.threading, "Thread", lambda **k: SimpleNamespace(
        start=lambda: calls.append(k["name"])))
    srv.main([])
    assert calls == ["accounts", "app", "translator-warm-up", "serve", "sweep", "worker",
                     "warm-up", "clio", "posture", "setup-code", "serving"]
    assert "No staff account yet" in capsys.readouterr().out


@pytest.mark.parametrize("configured", ["", "relative-deployment.json"])
def test_selected_deployment_environment_is_call_time_and_cli_wins(tmp_path, monkeypatch, configured):
    monkeypatch.setenv("I485_DEPLOYMENT", configured)
    assert staff_arguments([], repo=tmp_path).deployment == (
        Path(configured) if configured else tmp_path / "deployment.json")
    assert staff_arguments(["--deployment", "selected.json"], repo=tmp_path).deployment == Path("selected.json")
    assert not (tmp_path / "deployment.json").exists()


@pytest.mark.parametrize("selected,fails", [(False, False), (True, False), (True, True)])
def test_review_constructor_calls_migration_once_before_indexes_with_paired_inputs(tmp_path, monkeypatch, capsys, selected, fails):
    import firmsecrets
    from review import server as srv

    clients = tmp_path / "fictional" / "data" / "clients"
    record = tmp_path / "fictional" / "deployment.json" if selected else None
    calls = []
    stop = RuntimeError("stop before index construction")

    def migration(**kwargs):
        calls.append(("migration", kwargs))
        if fails:
            raise ValueError("fictional private value must not be printed")
        return ["fictional migration completed"]

    def index(*_):
        calls.append(("index", None))
        raise stop

    monkeypatch.setattr(firmsecrets, "migrate_deployment", migration)
    monkeypatch.setattr(srv.oversight, "Views", index)
    options = {"deployment_path": record} if selected else {}
    with pytest.raises(RuntimeError) as caught:
        srv.ReviewApp(clients, tmp_path / "unused-map", tmp_path / "unused-template", None, **options)
    assert caught.value is stop
    assert calls == [("migration", {"data_root": clients.resolve().parent, "deployment_path": record}),
                     ("index", None)]
    stderr = capsys.readouterr().err
    assert ("could not be moved" in stderr) is fails
    assert "fictional private value" not in stderr
    assert not clients.parent.exists()
