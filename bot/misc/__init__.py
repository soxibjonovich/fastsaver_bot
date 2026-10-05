import httpx2

from bot.misc import schemas
from config import settings


async def fetch_url_info(url: str) -> schemas.FetchResponse | None:
    async with httpx2.AsyncClient(
        headers={"X-Api-Key": settings.API_KEY.get_secret_value()},
        base_url=settings.API_URL,
    ) as client:
        params = {"url": url}
        response = await client.get("/fetch", params=params)
        print(response.json())
        try:
            return schemas.FetchResponse.model_validate(response.json())

        except Exception as e:  # noqa: BLE001
            print(e)
        return None
