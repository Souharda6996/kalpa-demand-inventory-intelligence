from datetime import UTC, datetime, timedelta

from conftest import PASSWORD, Api, login_as
from sqlalchemy import select

from kalpa_api.db.models import Invite

API = "/api/v1"


async def _invite(owner: Api, email: str = "new@a.com", role: str = "planner") -> dict:  # type: ignore[type-arg]
    r = await owner.post(f"{API}/org/invites", json={"email": email, "role": role})
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_new_user_accepts_invite_and_gets_the_role(owner_a: Api, make_api) -> None:  # type: ignore[no-untyped-def]
    inv = await _invite(owner_a)
    anon = await make_api()
    info = (await anon.get(f"{API}/auth/invite-info", params={"token": inv["token"]})).json()
    assert info == {"email": "new@a.com", "role": "planner", "org_name": "Org A", "existing_user": False}
    r = await anon.post(
        f"{API}/auth/accept-invite", json={"token": inv["token"], "password": PASSWORD, "name": "New Person"}
    )
    assert r.status_code == 204
    me = (await anon.get(f"{API}/auth/me")).json()  # accepting also signs the user in
    assert me["role"] == "planner" and me["email"] == "new@a.com"
    assert (await login_as(make_api, "new@a.com")).c is not None  # can log in with the chosen password


async def test_token_is_single_use_and_only_its_hash_is_stored(owner_a: Api, make_api, session) -> None:  # type: ignore[no-untyped-def]
    inv = await _invite(owner_a)
    stored = (await session.execute(select(Invite))).scalar_one()
    assert inv["token"] not in stored.token_hash and len(stored.token_hash) == 64
    await session.rollback()  # release the SQLite write lock held by this read transaction
    anon = await make_api()
    body = {"token": inv["token"], "password": PASSWORD, "name": "X"}
    assert (await anon.post(f"{API}/auth/accept-invite", json=body)).status_code == 204
    again = await (await make_api()).post(f"{API}/auth/accept-invite", json=body)
    assert again.status_code == 404 and again.json()["code"] == "invite_invalid"


async def test_expired_and_bogus_tokens_rejected(owner_a: Api, make_api, session) -> None:  # type: ignore[no-untyped-def]
    inv = await _invite(owner_a)
    stored = (await session.execute(select(Invite))).scalar_one()
    stored.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await session.commit()
    anon = await make_api()
    assert (await anon.get(f"{API}/auth/invite-info", params={"token": inv["token"]})).status_code == 404
    bogus = await anon.post(f"{API}/auth/accept-invite", json={"token": "x" * 43, "password": PASSWORD, "name": "X"})
    assert bogus.status_code == 404


async def test_existing_user_must_prove_their_password(owner_a: Api, owner_b: Api, make_api) -> None:  # type: ignore[no-untyped-def]
    inv = await _invite(owner_a, email="owner@b.com", role="viewer")  # owner B already has an account
    anon = await make_api()
    assert (await anon.get(f"{API}/auth/invite-info", params={"token": inv["token"]})).json()["existing_user"] is True
    wrong = await anon.post(f"{API}/auth/accept-invite", json={"token": inv["token"], "password": "wrong-password-1"})
    assert wrong.status_code == 401
    ok = await anon.post(f"{API}/auth/accept-invite", json={"token": inv["token"], "password": PASSWORD})
    assert ok.status_code == 204
    me = (await anon.get(f"{API}/auth/me")).json()
    assert {o["name"] for o in me["orgs"]} == {"Org A", "Org B"}  # now in both organisations


async def test_only_owners_manage_invites_and_listing_is_isolated(owner_a: Api, owner_b: Api, make_api) -> None:  # type: ignore[no-untyped-def]
    inv = await _invite(owner_a, email="v@a.com", role="viewer")
    anon = await make_api()
    await anon.post(f"{API}/auth/accept-invite", json={"token": inv["token"], "password": PASSWORD, "name": "V"})
    viewer = await login_as(make_api, "v@a.com")
    assert (await viewer.post(f"{API}/org/invites", json={"email": "z@a.com", "role": "viewer"})).status_code == 403
    assert (await viewer.get(f"{API}/org/invites")).status_code == 403
    assert (await owner_b.get(f"{API}/org/invites")).json() == []  # org B sees none of org A's invites
    listed = (await owner_a.get(f"{API}/org/invites")).json()
    assert listed[0]["accepted"] is True and "token" not in listed[0]
    pending = await _invite(owner_a, email="p@a.com")
    assert (await owner_b.delete(f"{API}/org/invites/{pending['id']}")).status_code == 404
    assert (await owner_a.delete(f"{API}/org/invites/{pending['id']}")).status_code == 204


async def test_invite_scope_must_reference_own_locations(owner_a: Api, owner_b: Api) -> None:
    loc_b = (await owner_b.post(f"{API}/locations", json={"name": "B Store", "type": "store"})).json()["id"]
    r = await owner_a.post(
        f"{API}/org/invites", json={"email": "s@a.com", "role": "store_manager", "location_ids": [loc_b]}
    )
    assert r.status_code == 422 and r.json()["code"] == "invalid_reference"
