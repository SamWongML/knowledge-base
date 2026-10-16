async def test_openapi(client):
    resp = await client.get("/api/openapi.json")
    assert resp.status_code == 200
    assert resp.json()["info"]["title"] == "Knowledge Base"


async def test_migrations_created_tables(app):
    from kb.db import connection

    async with connection() as conn:
        cur = await conn.execute("select count(*) as n from collections where slug = 'default'")
        assert (await cur.fetchone())["n"] == 1
