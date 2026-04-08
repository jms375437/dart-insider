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

# ── 1. 공시 목록 조회 ──
@app.get("/api/list")
async def get_list(bgn_de: str, end_de: str, pblntf_detail_ty: str):
    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.get(f"{DART_BASE}/list.json", params={
            "crtfc_key": DART_KEY,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "pblntf_detail_ty": pblntf_detail_ty,
            "page_count": 100,
            "sort": "date",
            "sort_mth": "desc"
        })
        return res.json()

# ── 2. 상세 정보 배치 조회 ──
@app.get("/api/detail-batch")
async def get_detail_batch(items: str):
    """
    items: "rcept_no:corp_code:ty,..." 형태
    직위 → elestock API
    단가/금액 → 공시 원문 HTML 파싱
    """
    pairs = [i.strip() for i in items.split(",") if i.strip()][:15]

    async def fetch_one(pair):
        try:
            parts = pair.split(":")
            if len(parts) < 3:
                return pair, {}
            rcept_no, corp_code, ty = parts[0], parts[1], parts[2]

            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                if ty == "D002":
                    # 직위: elestock API
                    r1 = await client.get(f"{DART_BASE}/elestock.json", params={
                        "crtfc_key": DART_KEY,
                        "rcept_no": rcept_no,
                        "corp_code": corp_code
                    })
                    edata = r1.json()
                    elist = edata.get("list", [])
                    matched = next((x for x in elist if x.get("rcept_no") == rcept_no), None)
                    ofcps = matched.get("isu_exctv_ofcps", "") if matched else ""
                    rgist_at = matched.get("isu_exctv_rgist_at", "") if matched else ""
                    qty_change = matched.get("sp_stock_lmp_irds_cnt", "") if matched else ""

                    # 단가/금액/방법: 공시 원문 HTML 파싱
                    r2 = await client.get(
                        f"https://dart.fss.or.kr/report/viewer.do",
                        params={"rcpNo": rcept_no, "dcmNo": "", "eleId": "", "offset": "", "length": "", "dtd": ""},
                        headers={"Referer": "https://dart.fss.or.kr/"}
                    )
                    trd_prc, trd_amount, trd_tp, rmk = parse_d002_html(r2.text, rcept_no)

                    return rcept_no, {
                        "ofcps": ofcps,
                        "rgist_at": rgist_at,
                        "qty_change": qty_change,
                        "trd_tp": trd_tp,
                        "trd_prc": trd_prc,
                        "trd_amount": trd_amount,
                        "rmk": rmk
                    }
                else:
                    # D001 대량보유: majorstock API
                    r1 = await client.get(f"{DART_BASE}/majorstock.json", params={
                        "crtfc_key": DART_KEY,
                        "rcept_no": rcept_no,
                        "corp_code": corp_code
                    })
                    mdata = r1.json()
                    mlist = mdata.get("list", [])
                    matched = next((x for x in mlist if x.get("rcept_no") == rcept_no), None)
                    if matched:
                        return rcept_no, {
                            "ofcps": "",
                            "rgist_at": "",
                            "qty_change": matched.get("stkqy_irds", ""),
                            "trd_tp": matched.get("report_resn", ""),
                            "trd_prc": "",
                            "trd_amount": "",
                            "rmk": matched.get("report_resn", "")
                        }
                    return rcept_no, {}
        except Exception as e:
            return pair.split(":")[0], {"error": str(e)}

    results = await asyncio.gather(*[fetch_one(p) for p in pairs])
    return {rcept_no: data for rcept_no, data in results}

def parse_d002_html(html: str, rcept_no: str) -> tuple:
    """공시 원문 HTML에서 취득/처분 단가, 금액, 방법, 비고 파싱"""
    trd_prc = ""
    trd_amount = ""
    trd_tp = ""
    rmk = ""

    try:
        # 세부변동내역 테이블에서 파싱
        # 취득/처분 방법
        m = re.search(r'취득[/／]?처분\s*방법[^<]*</th>\s*<td[^>]*>([^<]+)</td>', html, re.IGNORECASE)
        if not m:
            m = re.search(r'(장내매수|장외매수|장내매도|장외매도|유상신주취득|무상신주취득|상속|증여|전환사채|신주인수권)', html)
        if m:
            trd_tp = m.group(1).strip()

        # 취득/처분 단가
        m = re.search(r'취득[/／]?처분\s*단가[^<]*</th>\s*<td[^>]*>([\d,]+)', html, re.IGNORECASE)
        if not m:
            m = re.search(r'단\s*가\*{0,2}\s*</t[dh]>\s*<td[^>]*>([\d,]+)', html, re.IGNORECASE)
        if m:
            trd_prc = m.group(1).strip().replace(",", "")

        # 취득 금액 (단가 * 수량 또는 직접 기재)
        m = re.search(r'취득[/／]?처분\s*금액[^<]*</th>\s*<td[^>]*>([\d,]+)', html, re.IGNORECASE)
        if not m:
            m = re.search(r'자기자금\s*\(H\)[^<]*</th>\s*<td[^>]*>([\d,]+)', html, re.IGNORECASE)
        if m:
            trd_amount = m.group(1).strip().replace(",", "")

        # 비고
        m = re.search(r'<td[^>]*>\s*비\s*고\s*</td>\s*<td[^>]*>([^<]{1,100})</td>', html, re.IGNORECASE)
        if m:
            rmk = m.group(1).strip()

    except Exception:
        pass

    return trd_prc, trd_amount, trd_tp, rmk

# ── 3. 공시 원문에서 직접 파싱 (단일) ──
@app.get("/api/parse-detail")
async def parse_detail(rcept_no: str):
    """공시 원문 HTML 파싱 테스트용"""
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            # 공시 뷰어 URL 조회
            r = await client.get(
                f"https://dart.fss.or.kr/dsaf001/main.do",
                params={"rcpNo": rcept_no},
                headers={"User-Agent": "Mozilla/5.0"}
            )
            html = r.text

            # dcmNo 추출
            dcm_match = re.search(r'dcmNo["\s:=]+(\d+)', html)
            dcm_no = dcm_match.group(1) if dcm_match else ""

            if dcm_no:
                r2 = await client.get(
                    f"https://dart.fss.or.kr/report/viewer.do",
                    params={"rcpNo": rcept_no, "dcmNo": dcm_no},
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://dart.fss.or.kr/"}
                )
                content = r2.text
            else:
                content = html

            trd_prc, trd_amount, trd_tp, rmk = parse_d002_html(content, rcept_no)

            return {
                "rcept_no": rcept_no,
                "dcm_no": dcm_no,
                "trd_tp": trd_tp,
                "trd_prc": trd_prc,
                "trd_amount": trd_amount,
                "rmk": rmk,
                "html_length": len(content),
                "html_sample": content[:500]
            }
    except Exception as e:
        return {"error": str(e)}

# ── 4. 종목코드로 전체 내부자거래 검색 ──
@app.get("/api/search")
async def search_corp(corp_name: str, bgn_de: str, end_de: str):
    # 최근 데이터에서 종목코드로 corp_code 조회
    corp_code = None
    async with httpx.AsyncClient(timeout=20) as client:
        for ty in ["D002", "D001"]:
            r = await client.get(f"{DART_BASE}/list.json", params={
                "crtfc_key": DART_KEY,
                "bgn_de": "20240101",
                "end_de": end_de,
                "pblntf_detail_ty": ty,
                "page_count": 100,
            })
            data = r.json()
            for item in data.get("list", []):
                if item.get("stock_code") == corp_name:
                    corp_code = item.get("corp_code")
                    break
            if corp_code:
                break

    if not corp_code:
        return {"list": [], "error": "종목코드를 찾을 수 없습니다. 6자리 종목코드로 검색해주세요."}

    # corp_code로 전체 기간 조회
    all_items = []
    async with httpx.AsyncClient(timeout=60) as client:
        for ty in ["D002", "D001"]:
            page = 1
            while True:
                r = await client.get(f"{DART_BASE}/list.json", params={
                    "crtfc_key": DART_KEY,
                    "corp_code": corp_code,
                    "bgn_de": bgn_de,
                    "end_de": end_de,
                    "pblntf_detail_ty": ty,
                    "page_count": 100,
                    "page_no": page,
                    "sort": "date",
                    "sort_mth": "desc"
                })
                data = r.json()
                items = data.get("list", [])
                if not items:
                    break
                all_items.extend([{**x, "_ty": ty} for x in items])
                if page >= data.get("total_page", 1):
                    break
                page += 1
                if page > 20:
                    break

    return {"list": sorted(all_items, key=lambda x: x.get("rcept_dt", ""), reverse=True)}
