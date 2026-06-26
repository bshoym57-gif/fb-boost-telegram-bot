"""
bm_card_service.py
خدمة تسميع البطاقات من Business Manager
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from typing import Any, Dict, List, Optional

import httpx

BM_BASE = 'https://business.facebook.com'
GRAPHQL  = f'{BM_BASE}/api/graphql/'

DEVICE_PROFILES = [
    {
        'ua': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'lang': 'en-US,en;q=0.9',
    },
    {
        'ua': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15',
        'lang': 'en-US,en;q=0.9',
    },
]


def _parse_cookies(s: str) -> dict:
    out = {}
    for part in s.split(';'):
        part = part.strip()
        if '=' in part:
            k, v = part.split('=', 1)
            out[k.strip()] = v.strip()
    return out


def _parse_proxy(proxy_str: str) -> Optional[str]:
    """
    تحليل صيغ متعددة من البروكسي وتحويلها إلى صيغة موحدة.
    صيغ مقبولة:
    - IP:PORT
    - username:password@IP:PORT
    - hostname:port:username:password
    - hostname:port
    """
    if not proxy_str or not isinstance(proxy_str, str):
        return None
    
    proxy_str = proxy_str.strip()
    
    # إذا كان هناك @، فهو بصيغة user:pass@host:port
    if '@' in proxy_str:
        try:
            credentials, host_port = proxy_str.rsplit('@', 1)
            if ':' in host_port:
                # تحقق من أن المنفذ صحيح
                host, port_str = host_port.rsplit(':', 1)
                port = int(port_str)
                if 1 <= port <= 65535:
                    return f"http://{credentials}@{host}:{port}"
        except (ValueError, IndexError):
            pass
        return None
    
    # إذا لم يكن هناك @، نحاول تحليل الأجزاء
    parts = proxy_str.split(':')
    
    if len(parts) == 2:
        # host:port
        host, port_str = parts
        try:
            port = int(port_str)
            if 1 <= port <= 65535:
                return f"http://{host}:{port}"
        except (ValueError, IndexError):
            pass
    
    elif len(parts) == 4:
        # host:port:username:password
        host, port_str, user, password = parts
        try:
            port = int(port_str)
            if 1 <= port <= 65535:
                return f"http://{user}:{password}@{host}:{port}"
        except (ValueError, IndexError):
            pass
    
    elif len(parts) >= 3:
        # نحاول التحقق من آخر جزء، هل هو منفذ صحيح؟
        try:
            port = int(parts[-1])
            if 1 <= port <= 65535:
                # كل شيء ما عدا الآخر هو host (قد يحتوي على أكثر من :)
                host = ':'.join(parts[:-1])
                return f"http://{host}:{port}"
        except (ValueError, IndexError):
            pass
    
    return None


def _get_proxies(proxy: Optional[str]) -> Optional[dict]:
    """تحويل البروكسي إلى صيغة httpx."""
    if not proxy:
        return None
    
    parsed_proxy = _parse_proxy(proxy)
    if not parsed_proxy:
        return None
    
    return {'http://': parsed_proxy, 'https://': parsed_proxy}


def _extract_dtsg(html: str) -> Optional[str]:
    for pat in [
        r'"DTSGInitialData"[^}]*?"token":"([^"]+)"',
        r'name="fb_dtsg"\s+value="([^"]+)"',
        r'"token":"(AQ[^"]{10,})"',
        r'"fb_dtsg","([^"]+)"',
    ]:
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return None


class BMCardService:
    def __init__(self, cookies_str: str, proxy: Optional[str] = None):
        self.cookies_str  = cookies_str
        self.cookies_dict = _parse_cookies(cookies_str)
        self.proxies      = _get_proxies(proxy)
        self._dtsg: Optional[str] = None
        self._user_id: str = self.cookies_dict.get('c_user', '')
        self._profile = random.choice(DEVICE_PROFILES)

    def _headers(self) -> dict:
        return {
            'User-Agent':            self._profile['ua'],
            'Accept':                'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language':       self._profile['lang'],
            'Accept-Encoding':       'gzip, deflate, br',
            'Content-Type':          'application/x-www-form-urlencoded',
            'Origin':                BM_BASE,
            'Referer':               f'{BM_BASE}/billing/',
            'Sec-Fetch-Dest':        'document',
            'Sec-Fetch-Mode':        'navigate',
            'Sec-Fetch-Site':        'same-origin',
            'Cache-Control':         'max-age=0',
            'X-Requested-With':      'XMLHttpRequest',
            'Pragma':                'no-cache',
        }

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=30,
            proxies=self.proxies,
            follow_redirects=True,
        )

    async def fetch_dtsg(self) -> Dict[str, Any]:
        """جلب DTSG token من Business Manager بعد التحقق من صحة الجلسة."""
        try:
            async with self._client() as c:
                resp = await c.get(f'{BM_BASE}/', headers=self._headers(),
                                   cookies=self.cookies_dict)
                
                # تحقق من حالة الاستجابة
                text = resp.text
                url_str = str(resp.url).lower()
                
                # علامات تدل على انتهاء الجلسة
                if any(indicator in url_str for indicator in ['login', 'checkpoint', 'error']):
                    return {'success': False, 'error': 'الكوكيز منتهية — تحتاج تسجيل دخول من جديد'}
                
                if any(indicator in text.lower() for indicator in ['checkpoint', 'please log in', 'session expired']):
                    return {'success': False, 'error': 'الكوكيز منتهية — تحتاج تسجيل دخول من جديد'}
                
                # محاولة استخراج DTSG من HTML
                tok = _extract_dtsg(text)
                if not tok:
                    # قد يكون HTML أو JSON
                    dtsg_patterns = [
                        r'"DTSGInitialData":.*?"token":"([^"]+)"',
                        r'"DTSGInitialData":\s*\{[^}]*?"token":"([^"]+)"',
                        r'name="fb_dtsg"\s+value="([^"]+)"',
                    ]
                    for pattern in dtsg_patterns:
                        match = re.search(pattern, text)
                        if match:
                            tok = match.group(1)
                            break
                
                if not tok:
                    # إذا لم نجد DTSG، قد تكون هناك مشكلة في الكوكيز
                    error_msgs = []
                    if len(text) < 500:
                        error_msgs.append(f'رد صغير جداً ({len(text)} حرف)')
                    if 'facebook' not in text.lower():
                        error_msgs.append('الرد ليس من Facebook')
                    if 'business' not in text.lower():
                        error_msgs.append('لم يتم العثور على صفحة Business Manager')
                    
                    error_info = ' + '.join(error_msgs) if error_msgs else 'الكوكيز قد تكون غير صحيحة أو منتهية الصلاحية'
                    return {'success': False, 'error': f'لم يتم العثور على fb_dtsg — {error_info}'}
                
                self._dtsg = tok
                return {'success': True}
        except Exception as e:
            return {'success': False, 'error': f'خطأ في الاتصال: {str(e)}'}

    async def _gql(self, friendly: str, doc_id: str, variables: dict,
                   bm_id: str, ad_id: str) -> Dict[str, Any]:
        body = '&'.join([
            f'av={self._user_id}',
            f'__aaid={ad_id}',
            f'__bid={bm_id}',
            f'__user={self._user_id}',
            '__a=1',
            f'fb_dtsg={self._dtsg}',
            'fb_api_caller_class=RelayModern',
            f'fb_api_req_friendly_name={friendly}',
            f'variables={json.dumps(variables)}',
            f'doc_id={doc_id}',
        ])
        try:
            async with self._client() as c:
                resp = await c.post(GRAPHQL, headers=self._headers(),
                                    cookies=self.cookies_dict, content=body)
                
                # تحقق من حالة الاستجابة
                if resp.status_code == 401:
                    return {'success': False, 'error': 'الكوكيز منتهية - تحتاج تسجيل دخول من جديد (401)'}
                elif resp.status_code == 403:
                    return {'success': False, 'error': 'الوصول مرفوض - قد تحتاج إلى سلطات إضافية (403)'}
                elif resp.status_code >= 500:
                    return {'success': False, 'error': f'خطأ الخادم ({resp.status_code})'}
                
                try:
                    data = resp.json()
                    return {'success': True, 'data': data}
                except Exception:
                    return {'success': False, 'error': f'رد غير JSON ({resp.status_code}): {resp.text[:200]}'}
        except Exception as e:
            return {'success': False, 'error': f'خطأ شبكة: {str(e)}'}

    async def get_billing_account_id(self, bm_id: str, ad_id: str) -> Dict[str, Any]:
        r = await self._gql(
            'BillingHubPaymentMethodsViewQuery',
            '23945721255021756',
            {'businessID': bm_id},
            bm_id, ad_id,
        )
        if not r['success']:
            return r
        bm_ad_id = (r['data'].get('data', {})
                              .get('business', {})
                              .get('billing_payment_account', {})
                              .get('id'))
        if not bm_ad_id:
            return {'success': False, 'error': 'لم يتم العثور على حساب الدفع في البيزنس'}
        return {'success': True, 'bm_ad_id': bm_ad_id}

    async def get_payment_methods(self, bm_id: str, ad_id: str,
                                   bm_ad_id: str) -> Dict[str, Any]:
        r = await self._gql(
            'BillingHubPaymentMethodsBusinessSectionQuery',
            '24585166657733775',
            {
                'paymentAccountID':       bm_ad_id,
                'billable_account_types': ['FB_ADS', 'WHATSAPP'],
                'connected_asset_limit':  26,
                'connected_asset_detail_limit': 5,
            },
            bm_id, ad_id,
        )
        if not r['success']:
            return r
        try:
            methods = (r['data']['data']['payment_account']
                                        ['billing_payment_methods'])
            cards = [m['credential'] for m in methods]
            if not cards:
                return {'success': False, 'error': 'لا توجد بطاقات في الحافظة'}
            return {'success': True, 'cards': cards}
        except Exception as e:
            return {'success': False, 'error': f'خطأ في تحليل البطاقات: {e}'}

    async def make_default(self, bm_id: str, ad_id: str,
                            credential_id: str) -> Dict[str, Any]:
        def _rnd():
            return f"upl_{int(time.time()*1000)}_{random.randint(100000, 999999)}"

        r = await self._gql(
            'BillingSaveSharedBizCardStateMutation',
            '25126279877041501',
            {
                'input': {
                    'payment_legacy_account_id': ad_id,
                    'shared_biz_credential_id':  credential_id,
                    'upl_logging_data': {
                        'context':           'billingaddpm',
                        'credential_id':     credential_id,
                        'credential_type':   'CREDIT_CARD',
                        'entry_point':       'BILLING_HUB',
                        'external_flow_id':  _rnd(),
                        'target_name':       'BillingSaveSharedBizCardStateMutation',
                        'user_session_id':   _rnd(),
                        'wizard_config_name': 'SELECT_PAYMENT_METHOD',
                        'wizard_name':       'ADD_PM_PUX_EP',
                        'wizard_session_id': f'upl_wizard_{_rnd()}',
                    },
                    'actor_id':          self._user_id,
                    'client_mutation_id': str(int(time.time() * 1000)),
                },
                'includeCreateNewFromOldFragment': False,
            },
            bm_id, ad_id,
        )
        if not r['success']:
            return r
        if 'errors' in r.get('data', {}):
            msg = r['data']['errors'][0].get('message', 'خطأ غير معروف')
            return {'success': False, 'error': msg}
        return {'success': True}


async def get_bm_cards(cookies: str, bm_id: str, ad_id: str,
                       proxy: Optional[str] = None) -> Dict[str, Any]:
    svc = BMCardService(cookies, proxy)
    r = await svc.fetch_dtsg()
    if not r['success']:
        return r
    r = await svc.get_billing_account_id(bm_id, ad_id)
    if not r['success']:
        return r
    return await svc.get_payment_methods(bm_id, ad_id, r['bm_ad_id'])


async def warm_bm_cards(cookies: str, bm_id: str, ad_id: str,
                         cards: List[dict], card_ids: List[str],
                         interval_secs: int,
                         proxy: Optional[str] = None) -> Dict[str, Any]:
    svc = BMCardService(cookies, proxy)
    r = await svc.fetch_dtsg()
    if not r['success']:
        return r

    id_to_card = {c.get('credential_id', ''): c for c in cards}
    results = []
    for cid in card_ids:
        card = id_to_card.get(cid, {})
        name  = card.get('card_association_name', 'Card')
        last4 = card.get('last_four_digits', '****')
        label = f"{name} •••• {last4}"

        res = await svc.make_default(bm_id, ad_id, cid)
        results.append({
            'label':   label,
            'success': res['success'],
            'error':   res.get('error', ''),
        })
        if interval_secs > 0 and cid != card_ids[-1]:
            await asyncio.sleep(interval_secs)

    success_count = sum(1 for r in results if r['success'])
    fail_count    = len(results) - success_count
    return {
        'success':       True,
        'results':       results,
        'success_count': success_count,
        'fail_count':    fail_count,
    }
