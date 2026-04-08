from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx
import asyncio
import re

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DART_KEY = "7fb964ae09f610593964e76b1620eed18ef14b64"
DART_BASE = "https://opendart.fss.or.kr/api"

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/api/list")
async def get_list(bgn_de: str, end_de: str, pblntf_detail_ty: str):
    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.get(f"{DART_BASE}/list.json", params={
            "crtfc_key": DART_KEY, "bgn_de": bgn_de, "end_de": end_de,
            "pblntf_detail_ty": pblntf_detail_ty, "page_count": 100,
            "sort": "date", "sort_mth": "desc"
        })
        return res.json()

@app.get("/api/majorstock-batch")
async def get_majorstock_batch(rcept_nos: str):
    nos = [r.strip() for r in rcept_nos.split(",") if r.strip()][:20]

    async def fetch_one(no):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                res = await client.get(f"{DART_BASE}/majorstock.json", params={
                    "crtfc_key": DART_KEY, "rcept_no": no
                })
                data = res.json()
                items = data.get("list", [])
                if items:
                    return no, items[0]
                # majorstock 없으면 XML 파싱
                xml_res = await client.get(f"{DART_BASE}/document.xml", params={
                    "crtfc_key": DART_KEY, "rcept_no": no
                })
                return no, parse_xml(xml_res.text)
        except Exception as e:
            return no, {"error": str(e)}

    results = await asyncio.gather(*[fetch_one(no) for no in nos])
    return {no: data for no, data in results}

@app.get("/api/search")
async def search_corp(corp_name: str, bgn_de: str, end_de: str):
    async with httpx.AsyncClient(timeout=20) as client:
        tasks = [
            client.get(f"{DART_BASE}/list.json", params={
                "crtfc_key": DART_KEY, "bgn_de": bgn_de, "end_de": end_de,
                "pblntf_detail_ty": ty, "page_count": 100,
                "sort": "date", "sort_mth": "desc"
            }) for ty in ["D002", "D001"]
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_items = []
    for i, res in enumerate(results):
        ty = ["D002", "D001"][i]
        if isinstance(res, Exception):
            continue
        data = res.json()
        items = data.get("list", [])
        filtered = [x for x in items if corp_name in x.get("corp_name", "") or corp_name == x.get("stock_code", "")]
        all_items.extend([{**x, "_ty": ty} for x in filtered])

    return {"list": sorted(all_items, key=lambda x: x.get("rcept_dt", ""), reverse=True)}

def parse_xml(content: str) -> dict:
    def find(keys):
        for key in keys:
            for pat in [
                rf'<{key}[^>]*>([^<]+)</{key}>',
                rf'{key}\s*[：:]\s*([^\n<>|]+)',
            ]:
                m = re.search(pat, content, re.IGNORECASE)
                if m:
                    v = m.group(1).strip()
                    if v and len(v) < 100:
                        return v
        return ""

    def find_num(keys):
        for key in keys:
            m = re.search(rf'<{key}[^>]*>([\d,]+)</{key}>', content, re.IGNORECASE)
            if m:
                return m.group(1).replace(",", "")
        return ""

    return {
        "ofcps": find(["ofcps"]),
        "trd_tp": find(["trd_tp"]),
        "trd_prc": find_num(["trd_prc"]),
        "trd_qty": find_num(["trd_qty"]),
        "trd_amount": find_num(["trd_amount"]),
        "rmk": find(["rmk"]),
    }
