"""
proxy_manager.py
إدارة احترافية للبروكسيات مع فحص صحة تلقائي وحذف التالف
"""
from __future__ import annotations
import asyncio
import random
from pathlib import Path
from typing import Optional

import httpx

HEALTH_CHECK_URL = 'https://graph.facebook.com/'
HEALTH_TIMEOUT   = 8   # ثوانٍ


class ProxyManager:
    """يدير قائمة البروكسيات ويحذف التالف تلقائياً."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.touch(exist_ok=True)
        self._bad: set[str] = set()   # مؤقت في الذاكرة (يُنظَّف عند الإعادة)

    # ──────────────── CRUD ────────────────

    def add_many(self, text: str) -> int:
        """أضف بروكسيات من نص (سطر لكل بروكسي)."""
        lines = [
            x.strip() for x in text.splitlines()
            if x.strip() and not x.strip().startswith('#')
        ]
        if not lines:
            return 0
        existing = set(self.all())
        new_lines = [l for l in lines if l not in existing]
        if new_lines:
            with self.path.open('a', encoding='utf-8') as f:
                for line in new_lines:
                    f.write(line + '\n')
        return len(new_lines)

    def all(self) -> list[str]:
        """كل البروكسيات من الملف."""
        try:
            return [
                x.strip()
                for x in self.path.read_text(encoding='utf-8').splitlines()
                if x.strip() and not x.strip().startswith('#')
            ]
        except Exception:
            return []

    def remove(self, proxy: str):
        """احذف بروكسي من الملف."""
        items = [x for x in self.all() if x != proxy]
        self.path.write_text(
            '\n'.join(items) + ('\n' if items else ''),
            encoding='utf-8'
        )

    def clear(self):
        """احذف كل البروكسيات."""
        self.path.write_text('', encoding='utf-8')
        self._bad.clear()

    def count(self) -> int:
        return len(self.all())

    # ──────────────── فحص صحة البروكسي ────────────────

    @staticmethod
    def _normalize(proxy: str) -> str:
        return proxy if '://' in proxy else f'http://{proxy}'

    @staticmethod
    def mask(proxy: Optional[str]) -> str:
        """عرض البروكسي بشكل مقنّع للمستخدم (لا يكشف IP/Port/Credentials)."""
        if not proxy:
            return 'بدون'
        return '✅ بروكسي مفعّل'

    async def health_check(self, proxy: str) -> bool:
        """
        فحص البروكسي عن طريق الاتصال بـ Facebook Graph API.
        يُرجع True لو البروكسي يعمل.
        """
        proxy_url = self._normalize(proxy)
        try:
            async with httpx.AsyncClient(
                proxies={'https://': proxy_url, 'http://': proxy_url},
                timeout=HEALTH_TIMEOUT,
                follow_redirects=True,
            ) as client:
                r = await client.get(HEALTH_CHECK_URL)
                return r.status_code < 500
        except Exception:
            return False

    async def check_all_and_remove_bad(self, concurrency: int = 5) -> dict:
        """
        يفحص كل البروكسيات بشكل متوازٍ ويحذف الفاشلة.
        يُرجع {'checked': N, 'removed': M, 'remaining': K}
        """
        proxies = self.all()
        if not proxies:
            return {'checked': 0, 'removed': 0, 'remaining': 0}

        sem = asyncio.Semaphore(concurrency)

        async def _check(p: str) -> tuple[str, bool]:
            async with sem:
                ok = await self.health_check(p)
                return p, ok

        results = await asyncio.gather(*[_check(p) for p in proxies])
        removed = 0
        for proxy, ok in results:
            if not ok:
                self.remove(proxy)
                self._bad.add(proxy)
                removed += 1

        return {
            'checked':   len(proxies),
            'removed':   removed,
            'remaining': len(self.all()),
        }

    # ──────────────── اختيار بروكسي ────────────────

    def choose(self) -> Optional[str]:
        """اختر بروكسي عشوائي من القائمة (بدون فحص)."""
        items = [p for p in self.all() if p not in self._bad]
        return random.choice(items) if items else None

    async def choose_healthy(self, max_tries: int = 5) -> Optional[str]:
        """
        اختر بروكسي صالح مع فحص صحة فعلي.
        يحذف الفاشلين تلقائياً.
        """
        candidates = [p for p in self.all() if p not in self._bad]
        random.shuffle(candidates)
        for proxy in candidates[:max_tries]:
            if await self.health_check(proxy):
                return proxy
            else:
                self.mark_bad(proxy)
        return None

    def mark_bad(self, proxy: str):
        """
        سجّل البروكسي كفاشل (يُحذف من الملف فوراً).
        يُستخدم عند فشل طلب FB API.
        """
        self._bad.add(proxy)
        self.remove(proxy)
