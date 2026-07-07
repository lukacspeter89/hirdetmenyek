#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rss.py — az RSS mint "jelzőcsengő"
----------------------------------
A feedből csak a hirdetmény-ID-k kellenek (a link végén lévő szám);
az adatokat az ID-bejáró a JSON API-ról kéri le. Így az RSS 20 elemes
ablaka nem korlát.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import requests

RSS_URL = "https://hirdetmenyek.gov.hu/rss"
REQUEST_TIMEOUT = 30
ID_RE = re.compile(r"/reszletezo/(\d+)")


def fetch_rss_ids(session: requests.Session) -> list:
    """A feedben szereplő hirdetmény-ID-k (csökkenő sorrendben).
    Hiba esetén üres lista — a hívó ilyenkor előre-szondázásra vált."""
    try:
        resp = session.get(
            RSS_URL, timeout=REQUEST_TIMEOUT,
            headers={"Accept": "application/rss+xml, application/xml, text/xml"},
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        except Exception:
        return []

    ids = []
    for item in root.iter("item"):
        link = item.findtext("link", default="") or ""
        m = ID_RE.search(link)
        if m:
            ids.append(int(m.group(1)))
    return sorted(set(ids), reverse=True)
