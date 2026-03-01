import asyncio
import httpx
import logging

logging.basicConfig(level=logging.DEBUG)

async def test():
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://epqs.nationalmap.gov/v1/json",
            params={
                "x": -122.41940,
                "y": 37.77490,
                "wkid": 4326,
                "units": "Meters",
                "includeDate": "false",
            }
        )
        print(response.status_code)
        print(response.text)

asyncio.run(test())
