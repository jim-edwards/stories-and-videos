import re
import time

import pytest

from app import auth
from conftest import ADMIN, MOM, csrf_of, login


def test_password_hash_roundtrip_and_salting():
    a, b = auth.hash_password("sunny garden walk"), auth.hash_password("sunny garden walk")
    assert a != b
    assert auth.verify_password("sunny garden walk", a)
    assert not auth.verify_password("Sunny garden walk", a)
    assert not auth.verify_password("x", "garbage")
    assert not auth.verify_password("x", "bcrypt$1$2$3$4$5")


def test_create_user_rules(db):
    with pytest.raises(auth.UserError, match="at least 8"):
        auth.create_user(db, "dad", "short")
    with pytest.raises(auth.UserError, match="type a name"):
        auth.create_user(db, "   ", "long enough pw")
    auth.create_user(db, "Dad", "long enough pw")
    with pytest.raises(auth.UserError, match="already using"):
        auth.create_user(db, "dad", "another long pw")  # names aren't case sensitive
    assert auth.find_user(db, "  DAD ")["display_name"] == "Dad"


def test_first_start_creates_admin_from_settings(db):
    row = auth.find_user(db, ADMIN[0])
    assert row["is_admin"] == 1 and auth.verify_password(ADMIN[1], row["password_hash"])


def test_first_start_without_password_generates_one(tmp_path, cfg, caplog):
    from dataclasses import replace
    from app import create_app
    cfg2 = replace(cfg, data_dir=tmp_path / "fresh", admin_password=None)
    with caplog.at_level("WARNING"):
        app = create_app(cfg2, start_scanner=False)
    logged = " ".join(r.getMessage() for r in caplog.records)
    password = re.search(r"Password: (\S+)", logged).group(1)
    c = app.test_client()
    assert login(c, (cfg2.admin_username, password)).status_code == 302


def test_pages_require_sign_in(client):
    for path in ("/", "/watch/1", "/media/1", "/admin"):
        r = client.get(path)
        assert r.status_code == 302 and "/login" in r.headers["Location"], path
    assert client.post("/api/progress/1", json={}).status_code == 401
    assert client.get("/healthz").data == b"ok"


def test_login_success_sets_long_lived_httponly_cookie(client):
    r = login(client, ADMIN)
    assert r.status_code == 302 and r.headers["Location"] == "/"
    cookie = r.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Max-Age=31536000" in cookie
    assert client.get("/").status_code == 200


def test_login_is_friendly_and_case_insensitive_on_name(client):
    r = login(client, ("BOSS", ADMIN[1]))
    assert r.status_code == 302
    c2 = client.application.test_client()
    r = login(c2, (ADMIN[0], "wrong password"))
    assert r.status_code == 200
    assert "didn&#39;t work" in r.get_data(as_text=True)
    assert "Set-Cookie" not in r.headers


def test_unknown_user_gets_the_same_message(client):
    r = login(client, ("nobody", "whatever1234"))
    assert r.status_code == 200 and "didn&#39;t work" in r.get_data(as_text=True)


def test_repeated_failures_are_throttled(client):
    for _ in range(auth.LoginThrottle.PER_NAME):
        assert login(client, (ADMIN[0], "nope nope nope")).status_code == 200
    r = login(client, ADMIN)  # even the right password is refused for a while
    assert r.status_code == 429 and "Too many tries" in r.get_data(as_text=True)


def test_next_redirect_only_stays_on_this_site(client):
    for bad in ("https://evil.example/", "//evil.example", "/\\evil.example", "javascript:alert(1)"):
        c = client.application.test_client()
        r = c.post("/login", data={"username": ADMIN[0], "password": ADMIN[1], "next": bad})
        assert r.headers["Location"] == "/", bad
    c = client.application.test_client()
    r = c.post("/login", data={"username": ADMIN[0], "password": ADMIN[1], "next": "/watch/3"})
    assert r.headers["Location"] == "/watch/3"


def test_logout_ends_the_session(admin_client):
    token = csrf_of(admin_client)
    assert admin_client.post("/logout", data={"csrf_token": token}).status_code == 302
    assert admin_client.get("/").status_code == 302


def test_post_without_csrf_token_is_rejected(admin_client):
    assert admin_client.post("/logout").status_code == 400
    assert admin_client.post("/logout", data={"csrf_token": "wrong"}).status_code == 400
    assert admin_client.get("/").status_code == 200  # still signed in


def test_sessions_expire_when_unused(client, db):
    login(client, ADMIN)
    db.execute("UPDATE sessions SET last_seen = ?", (time.time() - 366 * 86400,))
    assert client.get("/").status_code == 302
    assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_active_sessions_slide_their_cookie_forward(client, db):
    login(client, ADMIN)
    db.execute("UPDATE sessions SET last_seen = ?", (time.time() - 2 * 86400,))
    r = client.get("/")
    assert r.status_code == 200 and "sv_session=" in r.headers.get("Set-Cookie", "")


def test_a_forged_cookie_is_never_accepted_or_echoed_back(client):
    for forged in ("not-a-real-token", "x; Path=/; Domain=evil.example", "a" * 500):
        r = client.get("/", headers={"Cookie": f"sv_session={forged}"})
        assert r.status_code == 302 and "/login" in r.headers["Location"]
        assert forged not in r.headers.get("Set-Cookie", "")


def test_only_admins_can_manage(mom_client):
    assert mom_client.get("/admin").status_code == 403
    token = csrf_of(mom_client)
    r = mom_client.post("/admin/users", data={"csrf_token": token, "username": "x", "password": "long enough pw"})
    assert r.status_code == 403
    assert "Manage" not in mom_client.get("/").get_data(as_text=True)


def test_admin_adds_resets_and_removes_people(admin_client, client, db):
    token = csrf_of(admin_client)
    r = admin_client.post("/admin/users", data={
        "csrf_token": token, "username": "dad", "display_name": "Dad", "password": "dads password"})
    assert r.status_code == 302
    dad = auth.find_user(db, "dad")
    assert dad["display_name"] == "Dad" and not dad["is_admin"]

    dad_client = client.application.test_client()
    assert login(dad_client, ("dad", "dads password")).status_code == 302

    r = admin_client.post(f"/admin/users/{dad['id']}/password", data={"csrf_token": token, "password": "brand new pw 1"})
    assert r.status_code == 302
    assert dad_client.get("/").status_code == 302  # signed out everywhere after a reset
    assert login(dad_client, ("dad", "brand new pw 1")).status_code == 302

    r = admin_client.post(f"/admin/users/{dad['id']}/delete", data={"csrf_token": token})
    assert r.status_code == 302 and auth.find_user(db, "dad") is None


def test_admin_cannot_delete_self_and_bad_input_is_explained(admin_client, db):
    token = csrf_of(admin_client)
    me = auth.find_user(db, ADMIN[0])["id"]
    admin_client.post(f"/admin/users/{me}/delete", data={"csrf_token": token})
    assert auth.find_user(db, ADMIN[0]) is not None
    admin_client.post("/admin/users", data={"csrf_token": token, "username": "z", "password": "short"})
    assert "at least 8" in admin_client.get("/admin").get_data(as_text=True)


def test_admin_resetting_own_password_keeps_them_signed_in(admin_client, db):
    token = csrf_of(admin_client)
    me = auth.find_user(db, ADMIN[0])["id"]
    admin_client.post(f"/admin/users/{me}/password", data={"csrf_token": token, "password": "new admin pw 1"})
    assert admin_client.get("/admin").status_code == 200


def test_abandoned_sessions_are_cleared_at_next_sign_in(client, db, mom):
    db.execute("INSERT INTO sessions VALUES ('stale', ?, 'x', 0, ?)", (mom, time.time() - 400 * 86400))
    login(client, ADMIN)
    assert db.execute("SELECT COUNT(*) FROM sessions WHERE token_hash = 'stale'").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
