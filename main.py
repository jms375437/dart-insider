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

@app.get("/api/detail-batch")
async def get_detail_batch(items: str):
    """
    items: "rcept_no:corp_code,rcept_no:corp_code,..." 형태
    D002는 elestock API, D001은 majorstock API 사용
    """
    pairs = [i.strip() for i in items.split(",") if i.strip()][:20]

    async def fetch_one(pair):
        try:
            parts = pair.split(":")
            if len(parts) < 3:
                return pair, {"error": "invalid format"}
            rcept_no, corp_code, ty = parts[0], parts[1], parts[2]

            async with httpx.AsyncClient(timeout=15) as client:
                if ty == "D002":
                    # 임원·주요주주 소유상황 API
                    res = await client.get(f"{DART_BASE}/elestock.json", params={
                        "crtfc_key": DART_KEY,
                        "rcept_no": rcept_no,
                        "corp_code": corp_code
                    })
                    data = res.json()
                    items_list = data.get("list", [])
                    # rcept_no 일치하는 항목 찾기
                    matched = next((x for x in items_list if x.get("rcept_no") == rcept_no), None)
                    if matched:
                        return rcept_no, {
                            "ofcps": matched.get("isu_exctv_ofcps", ""),
                            "rgist_at": matched.get("isu_exctv_rgist_at", ""),
                            "qty_change": matched.get("sp_stock_lmp_irds_cnt", ""),
                            "qty_total": matched.get("sp_stock_lmp_cnt", ""),
                            "trd_prc": "",
                            "trd_amount": "",
                            "rmk": ""
                        }
                    return rcept_no, {"ofcps": "", "rgist_at": "", "qty_change": "", "qty_total": "", "trd_prc": "", "trd_amount": "", "rmk": ""}
                else:
                    # 대량보유 API
                    res = await client.get(f"{DART_BASE}/majorstock.json", params={
                        "crtfc_key": DART_KEY,
                        "rcept_no": rcept_no,
                        "corp_code": corp_code
                    })
                    data = res.json()
                    items_list = data.get("list", [])
                    matched = next((x for x in items_list if x.get("rcept_no") == rcept_no), None)
                    if matched:
                        return rcept_no, {
                            "ofcps": "",
                            "rgist_at": "",
                            "qty_change": matched.get("stkqy_irds", ""),
                            "qty_total": matched.get("stkqy", ""),
                            "trd_prc": "",
                            "trd_amount": "",
                            "rmk": matched.get("report_resn", "")
                        }
                    return rcept_no, {"ofcps": "", "rgist_at": "", "qty_change": "", "qty_total": "", "trd_prc": "", "trd_amount": "", "rmk": ""}
        except Exception as e:
            return pair.split(":")[0], {"error": str(e)}

    results = await asyncio.gather(*[fetch_one(p) for p in pairs])
    return {rcept_no: data for rcept_no, data in results}

@app.get("/api/search")
async def search_corp(corp_name: str, bgn_de: str, end_de: str):
    # 먼저 corp_code 조회 (전체기간 검색을 위해 필요)
    corp_code = None
    async with httpx.AsyncClient(timeout=20) as client:
        # 회사명으로 corp_code 조회
        r = await client.get(f"{DART_BASE}/company.json", params={
            "crtfc_key": DART_KEY, "corp_name": corp_name
        })
        corp_data = r.json()
        corp_list = corp_data.get("list", [])
        # 종목코드 일치 우선, 없으면 이름 포함 첫번째
        for c in corp_list:
            if c.get("stock_code") == corp_name:
                corp_code = c.get("corp_code")
                break
        if not corp_code:
            for c in corp_list:
                if corp_name in c.get("corp_name", ""):
                    corp_code = c.get("corp_code")
                    break

    all_items = []
    async with httpx.AsyncClient(timeout=30) as client:
        if corp_code:
            # corp_code 있으면 전체기간 조회 가능
            tasks = [
                client.get(f"{DART_BASE}/list.json", params={
                    "crtfc_key": DART_KEY,
                    "corp_code": corp_code,
                    "bgn_de": bgn_de, "end_de": end_de,
                    "pblntf_detail_ty": ty, "page_count": 100,
                    "sort": "date", "sort_mth": "desc"
                }) for ty in ["D002", "D001"]
            ]
        else:
            # corp_code 없으면 3개월 제한
            tasks = [
                client.get(f"{DART_BASE}/list.json", params={
                    "crtfc_key": DART_KEY,
                    "bgn_de": bgn_de, "end_de": end_de,
                    "pblntf_detail_ty": ty, "page_count": 100,
                    "sort": "date", "sort_mth": "desc"
                }) for ty in ["D002", "D001"]
            ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, res in enumerate(results):
        ty = ["D002", "D001"][i]
        if isinstance(res, Exception):
            continue
        data = res.json()
        items = data.get("list", [])
        if corp_code:
            all_items.extend([{**x, "_ty": ty} for x in items])
        else:
            filtered = [x for x in items if corp_name in x.get("corp_name", "") or corp_name == x.get("stock_code", "")]
            all_items.extend([{**x, "_ty": ty} for x in filtered])

    return {"list": sorted(all_items, key=lambda x: x.get("rcept_dt", ""), reverse=True)}
