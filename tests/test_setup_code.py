# ruff: noqa: F811  (the fixtures imported from the other test files are used as arguments)
"""The first attorney's setup takes a one-time code always (brief J2, the finding carried from wave G): the computer that runs the app included,
since a TCP forwarder on that computer (netsh interface portproxy, socat, ssh -R with GatewayPorts) hands the app a loopback connection a stranger
controls. The code is the installer's, or the one the review app prints each time it starts while nobody has an account (the
product's own second factor for the first sign-in). The rest of the setup page is tests/test_first_attorney.py. Everyone here is made up."""

from __future__ import annotations

import threading

from review.auth import Accounts
from test_first_attorney import GOOD, PASSWORD, _call, _start
from test_review import client  # noqa: F401 -- the review app's test client


def test_through_a_tcp_forwarder_on_the_same_computer_the_code_is_still_needed(client, tmp_path):  # noqa: F811
    """The finding carried from wave G (brief J2): a forwarder below the web level (netsh portproxy, socat) hands the app a loopback connection
    with the Host the stranger typed and no forwarding header. It used to set the first attorney up with no code; now it gets the code box at most."""
    import socket as sock

    users = Accounts(tmp_path / "users.json")
    code = users.new_setup_code()
    httpd, base, port = _start(client, users)  # an app on 127.0.0.1 with no name: what main() builds on a laptop
    lst = sock.socket()
    lst.bind(("127.0.0.1", 0))
    lst.listen(5)

    def pipe(a, b):
        try:
            while True:
                data = a.recv(65536)
                if not data:
                    break
                b.sendall(data)
        except OSError:
            pass
        finally:
            try:
                b.shutdown(sock.SHUT_WR)
            except OSError:
                pass

    def forward():
        while True:
            try:
                conn, _ = lst.accept()
            except OSError:
                return
            up = sock.create_connection(("127.0.0.1", port))
            threading.Thread(target=pipe, args=(conn, up), daemon=True).start()
            threading.Thread(target=pipe, args=(up, conn), daemon=True).start()

    threading.Thread(target=forward, daemon=True).start()
    through = f"http://127.0.0.1:{lst.getsockname()[1]}"
    try:
        host = {"Host": f"127.0.0.1:{port}"}  # the stranger types the app's own loopback name
        status, out, _ = _call(through + "/api/setup", {"name": "Eve Intruder", "email": "eve@x.example", "password": PASSWORD}, headers=host)
        assert status == 404 and out == {"error": "not found"} and users.needs_setup()
        assert _call(through + "/api/setup", GOOD | {"code": code}, headers=host)[0] == 200  # whoever holds the printed code
    finally:
        httpd.shutdown()
        lst.close()


def test_the_app_prints_a_code_of_its_own_at_start_and_the_installers_still_works(tmp_path):
    users = Accounts(tmp_path / "users.json")
    installer = users.new_setup_code()
    first, second = users.console_setup_code(), users.console_setup_code()
    assert first != second and not users.setup_code_ok(first) and users.setup_code_ok(second)  # each start replaces the app's own code ...
    assert users.setup_code_ok(installer)  # ... never the installer's
    again = users.new_setup_code()
    assert users.setup_code_ok(second) and users.setup_code_ok(again) and not users.setup_code_ok(installer)  # a new installer code keeps the app's
    users.create_first("sam@firm.example", "Sam Exemplo", PASSWORD, second)
    assert users.console_setup_code() is None and not users.setup_open()  # used up, and none once an account exists


def test_main_prints_the_code_when_nobody_has_an_account(tmp_path, monkeypatch, capsys):
    from review import server as srv

    users = tmp_path / "data" / "review_users.json"
    Accounts(users).new_setup_code()
    monkeypatch.setattr(srv, "serve", lambda *a, **k: type("S", (), {"serve_forever": lambda self: None})())
    monkeypatch.setattr(srv.jobs, "sweep", lambda *a, **k: None)
    monkeypatch.setattr(srv.jobs, "ensure_worker", lambda *a, **k: None)
    monkeypatch.setattr(srv.ReviewApp, "warm_up", lambda self: None)
    monkeypatch.setattr("classify.translate.warm_up", lambda: None)
    (tmp_path / "data" / "clients").mkdir(parents=True)
    srv.main(["--data", str(tmp_path / "data" / "clients"), "--users", str(users), "--portal", str(tmp_path / "data" / "portal"), "--port", "8499"])
    out = capsys.readouterr().out
    code = out.split("?setup=")[1].split(" ")[0]
    assert "No staff account yet" in out and Accounts(users).setup_code_ok(code)
