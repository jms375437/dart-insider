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
    from datetime import datetime, timedelta

    # 날짜를 3개월씩 나눠서 조회 후 종목명/종목코드로 필터링
    all_items = []

    try:
        start = datetime.strptime(bgn_de, "%Y%m%d")
        end = datetime.strptime(end_de, "%Y%m%d")
    except:
        start = datetime(2020, 1, 1)
        end = datetime.now()

    # 3개월 단위로 나눔 (최근 2년치만 - 너무 많으면 느림)
    two_years_ago = end - timedelta(days=730)
    if start < two_years_ago:
        start = two_years_ago

    periods = []
    cur = start
    while cur < end:
        period_end = min(cur + timedelta(days=89), end)
        periods.append((cur.strftime("%Y%m%d"), period_end.strftime("%Y%m%d")))
        cur = period_end + timedelta(days=1)

    async with httpx.AsyncClient(timeout=30) as client:
        for bgn, end_p in periods:
            for ty in ["D002", "D001"]:
                try:
                    r = await client.get(f"{DART_BASE}/list.json", params={
                        "crtfc_key": DART_KEY,
                        "bgn_de": bgn, "end_de": end_p,
                        "pblntf_detail_ty": ty,
                        "page_count": 100,
                        "sort": "date", "sort_mth": "desc"
                    })
                    data = r.json()
                    items = data.get("list", [])
                    filtered = [
                        {**x, "_ty": ty} for x in items
                        if corp_name in x.get("corp_name", "") or corp_name == x.get("stock_code", "")
                    ]
                    all_items.extend(filtered)
                except:
                    continue

    # 중복 제거
    seen = set()
    unique = []
    for item in all_items:
        if item["rcept_no"] not in seen:
            seen.add(item["rcept_no"])
            unique.append(item)

    return {"list": sorted(unique, key=lambda x: x.get("rcept_dt", ""), reverse=True)}
