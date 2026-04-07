from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DART_KEY = "7fb964ae09f610593964e76b1620eed18ef14b64"
DART_BASE = "https://opendart.fss.or.kr/api"

@app.get("/api/list")
async def get_list(bgn_de: str, end_de: str, pblntf_detail_ty: str):
    url = f"{DART_BASE}/list.json"
    params = {
        "crtfc_key": DART_KEY,
        "bgn_de": bgn_de,
        "end_de": end_de,
        "pblntf_detail_ty": pblntf_detail_ty,
        "page_count": 100,
        "sort": "date",
        "sort_mth": "desc"
    }
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.get(url, params=params)
        return res.json()

@app.get("/health")
async def health():
    return {"status": "ok"}
