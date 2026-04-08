from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx
import re
import asyncio

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DART_KEY = "7fb964ae09f610593964e76b1620eed18ef14b64"
DART_BASE = "https://opendart.fss.or.kr/api"

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/api/list")
async def get_list(bgn_de: str, end_de: str, pblntf_detail_ty: str):
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.get(f"{DART_BASE}/list.json", params={
            "crtfc_key": DART_KEY, "bgn_de": bgn_de, "end_de": end_de,
            "pblntf_detail_ty": pblntf_detail_ty, "page_count": 100,
            "sort": "date", "sort_mth": "desc"
        })
        return res.json()

@app.get("/api/detail")
async def get_detail(rcept_no: str):
    """공시 원문에서 상세 정보 파싱"""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            res = await client.get(f"{DART_BASE}/document.xml", params={
                "crtfc_key": DART_KEY, "rcept_no": rcept_no
            })
            content = res.text

        # 직위 파싱
        position = parse_field(content, ["직위", "직 위", "보고자직위"])
        # 성명 파싱
        name = parse_field(content, ["성명", "보고자성명"])
        # 매수방식 파싱
        trade_type = parse_field(content, ["거래유형", "취득방법", "취득방식", "매매유형"])
        # 매수가격 파싱
        price = parse_number_field(content, ["단가", "1주당가액", "거래단가", "취득가액"])
        # 매수수량 파싱
        quantity = parse_number_field(content, ["취득수량", "거래수량", "변동수량", "수량"])
        # 매수금액 파싱
        amount = parse_number_field(content, ["취득금액", "거래금액", "변동금액", "금액"])
        # 비고 파싱
        remark = parse_field(content, ["비고", "기타"])

        return {
            "rcept_no": rcept_no,
            "position": position,
            "name": name,
            "trade_type": trade_type,
            "price": price,
            "quantity": quantity,
            "amount": amount,
            "remark": remark
        }
    except Exception as e:
        return {"rcept_no": rcept_no, "error": str(e)}

@app.get("/api/details-batch")
async def get_details_batch(rcept_nos: str):
    """여러 공시 원문을 한번에 파싱"""
    nos = [r.strip() for r in rcept_nos.split(",") if r.strip()]
    tasks = [get_detail(no) for no in nos[:20]]  # 최대 20개
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out = {}
    for no, r in zip(nos, results):
        if isinstance(r, Exception):
            out[no] = {"error": str(r)}
        else:
            out[no] = r
    return out

@app.get("/api/search")
async def search_corp(corp_name: str, bgn_de: str, end_de: str):
    """기업명으로 검색"""
    async with httpx.AsyncClient(timeout=15) as client:
        tasks = []
        for ty in ["D002", "D001"]:
            tasks.append(client.get(f"{DART_BASE}/list.json", params={
                "crtfc_key": DART_KEY, "bgn_de": bgn_de, "end_de": end_de,
                "pblntf_detail_ty": ty, "page_count": 100,
                "sort": "date", "sort_mth": "desc"
            }))
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_items = []
    for i, res in enumerate(results):
        ty = ["D002", "D001"][i]
        if isinstance(res, Exception):
            continue
        data = res.json()
        items = data.get("list", [])
        filtered = [x for x in items if corp_name in x.get("corp_name", "")]
        all_items.extend([{**x, "_ty": ty} for x in filtered])

    return {"list": sorted(all_items, key=lambda x: x.get("rcept_dt", ""), reverse=True)}

@app.get("/api/history")
async def get_history(flr_nm: str, bgn_de: str, end_de: str):
    """동일인 3개월 매수 이력 조회"""
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.get(f"{DART_BASE}/list.json", params={
            "crtfc_key": DART_KEY, "bgn_de": bgn_de, "end_de": end_de,
            "pblntf_detail_ty": "D002", "page_count": 100,
            "sort": "date", "sort_mth": "desc"
        })
        data = res.json()

    items = data.get("list", [])
    matched = [x for x in items if x.get("flr_nm", "") == flr_nm]
    return {"list": matched}

def parse_field(content: str, keys: list) -> str:
    for key in keys:
        patterns = [
            rf'{key}[^<>]*?</[^>]+>\s*<[^>]+>([^<>]+)<',
            rf'{key}\s*[：:]\s*([^\n<>|]+)',
            rf'<td[^>]*>{key}</td>\s*<td[^>]*>([^<>]+)</td>',
        ]
        for pat in patterns:
            m = re.search(pat, content, re.IGNORECASE)
            if m:
                val = m.group(1).strip()
                if val and len(val) < 100:
                    return val
    return ""

def parse_number_field(content: str, keys: list) -> str:
    for key in keys:
        patterns = [
            rf'{key}[^<>]*?</[^>]+>\s*<[^>]+>([\d,\.]+)',
            rf'{key}\s*[：:]\s*([\d,\.]+)',
            rf'<td[^>]*>{key}</td>\s*<td[^>]*>([\d,\.]+)</td>',
        ]
        for pat in patterns:
            m = re.search(pat, content, re.IGNORECASE)
            if m:
                val = m.group(1).strip().replace(",", "")
                if val:
                    return val
    return ""
