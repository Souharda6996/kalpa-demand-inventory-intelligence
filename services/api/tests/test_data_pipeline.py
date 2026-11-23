from conftest import Api
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kalpa_api.db.models import Org, SalesDaily
from kalpa_api.scripts.seed import DEMO_SLUG, seed_demo
from kalpa_ml.data.synth import SynthConfig

D = "/api/v1/data"
GOOD = b"Store Name,Item,Sale Date,Qty,Unit Price\nKoramangala,A1,2024-01-01,3,10\nKoramangala,A1,2024-01-02,0,10\nKoramangala,A1,2024-01-04,5,10\n"
BAD = b"store,sku,date,units\nS1,A1,2024-01-01,-4\nS1,A1,not-a-date,2\n"


async def _upload(api: Api, body: bytes):  # type: ignore[no-untyped-def]
    return await api.post(f"{D}/uploads", files={"file": ("x.csv", body, "text/csv")})


async def test_mapping_suggested_and_commit_loads_zero_filled_data(owner_a: Api) -> None:
    r = await _upload(owner_a, GOOD)
    assert r.status_code == 200
    body = r.json()
    assert body["validation_ok"] and body["suggested_mapping"] == {
        "location": "Store Name",
        "sku": "Item",
        "date": "Sale Date",
        "units": "Qty",
        "price": "Unit Price",
        "category": None,
    }
    c = await owner_a.post(f"{D}/uploads/{body['upload_id']}/commit", json={})
    assert c.status_code == 200 and c.json()["rows_in"] == 3 and c.json()["rows_out"] == 4  # gap zero-filled
    s = (await owner_a.get(f"{D}/stats")).json()
    assert s["series"] == 1 and s["rows"] == 4 and s["pct_zero_days"] == 50.0
    q = (await owner_a.get(f"{D}/quality-reports")).json()
    assert len(q) == 1 and q[0]["issues"]["date_gaps"] == 1


async def test_broken_csv_rejected_with_readable_report(owner_a: Api) -> None:
    up = (await _upload(owner_a, BAD)).json()
    assert not up["validation_ok"] and up["problems"]
    r = await owner_a.post(f"{D}/uploads/{up['upload_id']}/commit", json={})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_failed" and any("units" in p or "date" in p for p in body["details"])
    assert (await owner_a.get(f"{D}/stats")).json()["rows"] == 0  # nothing loaded


async def test_other_org_cannot_see_or_commit_upload(owner_a: Api, owner_b: Api) -> None:
    up = (await _upload(owner_a, GOOD)).json()
    assert (await owner_b.post(f"{D}/uploads/{up['upload_id']}/commit", json={})).status_code == 404
    assert (await owner_b.get(f"{D}/uploads/{up['upload_id']}/preview")).status_code == 404
    assert (await owner_b.get(f"{D}/stats")).json()["rows"] == 0


async def test_data_endpoints_require_login(make_api) -> None:  # type: ignore[no-untyped-def]
    anon = await make_api()
    assert (await anon.get(f"{D}/stats")).status_code == 401


async def test_seed_demo_is_idempotent(session: AsyncSession) -> None:
    cfg = SynthConfig(start="2024-01-01", end="2024-02-29", categories={"Sweets": 5, "Beverages": 5})
    first = await seed_demo(session, cfg)
    second = await seed_demo(session, cfg)
    assert first == second and first["series"] == 60  # 6 stores x 10 SKUs
    n = (await session.execute(select(func.count()).select_from(SalesDaily))).scalar_one()
    assert n == first["sales_rows"]
    orgs = (await session.execute(select(func.count()).select_from(Org).where(Org.slug == DEMO_SLUG))).scalar_one()
    assert orgs == 1


async def test_demo_loader_requires_owner_and_empty_org(owner_a: Api, make_api) -> None:  # type: ignore[no-untyped-def]
    from conftest import PASSWORD, login_as

    await owner_a.post(
        "/api/v1/org/members", json={"email": "p@a.com", "name": "P", "password": PASSWORD, "role": "planner"}
    )
    planner = await login_as(make_api, "p@a.com")
    assert (await planner.post(f"{D}/demo")).status_code == 403
    up = (await _upload(owner_a, GOOD)).json()
    await owner_a.post(f"{D}/uploads/{up['upload_id']}/commit", json={})
    r = await owner_a.post(f"{D}/demo")
    assert r.status_code == 409 and r.json()["code"] == "org_not_empty"
