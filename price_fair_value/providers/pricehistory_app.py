from __future__ import annotations

import base64
from datetime import datetime, timedelta
import json
import re
from typing import Any

from scrapling.fetchers import Fetcher

from price_fair_value.models import PricePoint, ProductMetadata


def extract_listing_slug(url_or_code: str) -> str:
    """
    Extracts lowercase 8-char suffix from ...-1n4hLvyp (e.g. '1n4hlvyp'),
    or slugifies the string if no 8-char suffix is matched.
    """
    cleaned = url_or_code.strip().split("?")[0].split("#")[0].rstrip("/")
    # Match an 8-character alphanumeric suffix after a hyphen (e.g. ...-1n4hLvyp)
    match = re.search(r"-([a-zA-Z0-9]{8})$", cleaned)
    if match:
        return match.group(1).lower()

    # If the entire input is an 8-char alphanumeric string
    if len(cleaned) == 8 and cleaned.isalnum():
        return cleaned.lower()

    # Fallback slugification
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", cleaned).strip("-").lower()
    return slug or "unknown-product"


def decrypt_embedded_dataset(script_text: str) -> dict | None:
    """
    Implements base64 + XOR decryption for PriceHistory.app embedded scripts:
    PagePriceHistoryDataSet = "<base64_xor_encrypted>"
    CachedKey = '<key>'
    """
    m_data = re.search(r'PagePriceHistoryDataSet\s*=\s*"([^"]+)"', script_text)
    m_key = re.search(r"CachedKey\s*=\s*'([^']+)'", script_text)
    if not m_data or not m_key:
        return None

    try:
        raw = base64.b64decode(m_data.group(1)).decode("latin1")
        key = m_key.group(1)
        decrypted = "".join(chr(ord(c) ^ ord(key[i % len(key)])) for i, c in enumerate(raw))
        return json.loads(decrypted)
    except Exception:
        return None


def resolve_to_pricehistory_url(url_or_query: str) -> str:
    """
    Resolves input URL or query to a PriceHistory.app product URL.
    If already a pricehistory.app URL, returns it.
    Otherwise queries https://pricehistory.app/api/search via Fetcher.post.
    """
    cleaned = url_or_query.strip()
    if cleaned.startswith("https://pricehistory.app/p/") or cleaned.startswith("http://pricehistory.app/p/"):
        return cleaned

    resp = Fetcher.post(
        "https://pricehistory.app/api/search",
        json={"url": cleaned},
        timeout=15,
    )
    if resp.status != 200:
        raise ValueError(f"PriceHistory search API returned status {resp.status} for {cleaned}")

    body_str = (
        resp.body.decode("utf-8", "replace")
        if isinstance(resp.body, (bytes, bytearray))
        else str(resp.body)
    )

    try:
        data = json.loads(body_str)
    except Exception as e:
        raise ValueError(f"Failed to parse search API response as JSON: {e}") from e

    if data.get("status") and data.get("code"):
        return f"https://pricehistory.app/p/{data['code']}"

    raise ValueError(f"Search API could not find match for {cleaned}: {data}")


def parse_meta_description(
    meta_content: str,
    retailer: str = "amazon.in",
) -> tuple[float, float | None, list[PricePoint]]:
    """
    Parses summary pricing from meta description string:
    Lowest Price: ₹... | Average Price: ₹... | Highest Price: ₹... | MRP: ₹... | Price in India on ...: ₹...
    """
    def _clean_price(raw: str) -> float:
        return float(raw.replace(",", "").strip())

    m_low = re.search(r"Lowest\s*Price:\s*₹?\s*([0-9,]+(?:\.[0-9]+)?)", meta_content, re.IGNORECASE)
    m_avg = re.search(r"Average\s*Price:\s*₹?\s*([0-9,]+(?:\.[0-9]+)?)", meta_content, re.IGNORECASE)
    m_high = re.search(r"Highest\s*Price:\s*₹?\s*([0-9,]+(?:\.[0-9]+)?)", meta_content, re.IGNORECASE)
    m_mrp = re.search(r"MRP:\s*₹?\s*([0-9,]+(?:\.[0-9]+)?)", meta_content, re.IGNORECASE)
    m_curr = re.search(
        r"Price in India(?:\s+on\s+[^:]+)?\s*(?:is\s*)?:?\s*₹?\s*([0-9,]+(?:\.[0-9]+)?)",
        meta_content,
        re.IGNORECASE,
    )

    low_p = _clean_price(m_low.group(1)) if m_low else None
    avg_p = _clean_price(m_avg.group(1)) if m_avg else None
    high_p = _clean_price(m_high.group(1)) if m_high else None
    mrp = _clean_price(m_mrp.group(1)) if m_mrp else None
    curr_p = _clean_price(m_curr.group(1)) if m_curr else None

    current_price = curr_p if curr_p is not None else (low_p or avg_p or (mrp if mrp else 0.0))

    now = datetime.utcnow()
    points: list[PricePoint] = []
    if low_p is not None:
        points.append(
            PricePoint(
                price=low_p,
                recorded_at=(now - timedelta(days=90)).strftime("%Y-%m-%d"),
                currency="INR",
                retailer=retailer,
                source="pricehistory.app",
            )
        )
    if avg_p is not None:
        points.append(
            PricePoint(
                price=avg_p,
                recorded_at=(now - timedelta(days=60)).strftime("%Y-%m-%d"),
                currency="INR",
                retailer=retailer,
                source="pricehistory.app",
            )
        )
    if high_p is not None:
        points.append(
            PricePoint(
                price=high_p,
                recorded_at=(now - timedelta(days=30)).strftime("%Y-%m-%d"),
                currency="INR",
                retailer=retailer,
                source="pricehistory.app",
            )
        )
    if curr_p is not None and curr_p != low_p:
        points.append(
            PricePoint(
                price=curr_p,
                recorded_at=now.strftime("%Y-%m-%d"),
                currency="INR",
                retailer=retailer,
                source="pricehistory.app",
            )
        )

    return current_price, mrp, points


def fetch_and_parse(url_or_query: str) -> tuple[ProductMetadata, float, list[PricePoint]]:
    """
    1. Resolves URL to pricehistory.app URL via resolve_to_pricehistory_url.
    2. Fetches page via Fetcher.get.
    3. Parses title, retailer, and pricing.
    4. Tries decrypt_embedded_dataset on scripts first; falls back to meta description.
    """
    ph_url = resolve_to_pricehistory_url(url_or_query)
    page = Fetcher.get(ph_url, timeout=15)

    if page.status != 200:
        raise ValueError(f"Failed to fetch {ph_url}, status code: {page.status}")

    slug = extract_listing_slug(ph_url)

    # Extract body text
    html_text = ""
    if hasattr(page, "body") and page.body:
        html_text = (
            page.body.decode("utf-8", "replace")
            if isinstance(page.body, (bytes, bytearray))
            else str(page.body)
        )
    elif hasattr(page, "html_content") and page.html_content:
        html_text = str(page.html_content)

    # Extract script contents
    scripts: list[str] = []
    if hasattr(page, "css"):
        try:
            for s in page.css("script"):
                if hasattr(s, "html_content") and s.html_content:
                    scripts.append(str(s.html_content))
                elif hasattr(s, "text") and s.text:
                    scripts.append(str(s.text))
                elif isinstance(s, str):
                    scripts.append(s)
                else:
                    scripts.append(str(s))
        except Exception:
            pass

    if html_text:
        regex_scripts = re.findall(r"<script[^>]*>(.*?)</script>", html_text, re.DOTALL | re.IGNORECASE)
        for sc in regex_scripts:
            if sc and sc not in scripts:
                scripts.append(sc)

    # Extract retailer from text
    retailer = "amazon.in"
    m_ret = re.search(r"Buy\s*@\s*([A-Za-z0-9.-]+)", html_text, re.IGNORECASE)
    if m_ret:
        raw_ret = m_ret.group(1).strip().lower()
        if "flipkart" in raw_ret:
            retailer = "flipkart.com"
        elif "amazon" in raw_ret:
            retailer = "amazon.in"
        else:
            retailer = raw_ret

    # Extract title
    title = ""
    if hasattr(page, "css"):
        try:
            for el in page.css("h1"):
                t = (el.text if hasattr(el, "text") else str(el)).strip()
                clean_t = re.sub(r"<[^>]+>", "", t).strip()
                if clean_t:
                    title = clean_t
                    break
        except Exception:
            pass

    if not title and html_text:
        m_h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html_text, re.DOTALL | re.IGNORECASE)
        if m_h1:
            title = re.sub(r"<[^>]+>", "", m_h1.group(1)).strip()
        else:
            m_title = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.DOTALL | re.IGNORECASE)
            if m_title:
                title = re.sub(r"<[^>]+>", "", m_title.group(1)).strip()

    # Attempt embedded dataset decryption first
    dataset: dict | None = None
    for s_text in scripts:
        dataset = decrypt_embedded_dataset(s_text)
        if dataset:
            break

    points: list[PricePoint] = []
    current_price = 0.0
    mrp: float | None = None

    if dataset:
        price_obj = dataset.get("Price", {})
        if isinstance(price_obj, dict):
            current_price = float(price_obj.get("Price", 0.0))
            raw_mrp = price_obj.get("MRP")
            mrp = float(raw_mrp) if raw_mrp is not None and raw_mrp != "" else None
        elif isinstance(price_obj, (int, float)):
            current_price = float(price_obj)

        history_obj = dataset.get("History", {})
        raw_history = history_obj.get("Price", []) if isinstance(history_obj, dict) else []
        for item in raw_history:
            if isinstance(item, dict) and "y" in item:
                points.append(
                    PricePoint(
                        price=float(item["y"]),
                        recorded_at=str(item.get("x", "")),
                        currency="INR",
                        retailer=retailer,
                        source="pricehistory.app",
                    )
                )

        if not current_price and points:
            current_price = points[-1].price

        if not title and (dataset.get("name") or dataset.get("Name")):
            title = str(dataset.get("name") or dataset.get("Name"))
    else:
        # Fallback to meta description parsing
        m_desc = re.search(
            r'<meta[^>]+(?:name=["\']description["\']|property=["\']og:description["\'])[^>]+content=["\']([^"\']+)["\']',
            html_text,
            re.IGNORECASE,
        )
        if not m_desc:
            m_desc = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name=["\']description["\']|property=["\']og:description["\'])',
                html_text,
                re.IGNORECASE,
            )

        meta_content = m_desc.group(1) if m_desc else ""
        current_price, mrp, points = parse_meta_description(meta_content, retailer=retailer)

    if not title:
        title = slug

    metadata = ProductMetadata(
        product_slug=slug,
        canonical_url=ph_url,
        title=title,
        retailer=retailer,
        currency="INR",
        mrp=mrp,
        last_checked=datetime.utcnow().isoformat(),
    )

    return metadata, current_price, points
