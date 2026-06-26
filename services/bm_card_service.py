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
import urllib.parse
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
    """
    تحليل الكوكيز من أي تنسيق:
      - semicolon-separated: a=1;b=2
      - newline-separated: a=1\nb=2
      - comma-separated: a=1, b=2
      - with Cookie: prefix
      - with URL-encoded values
      - JSON values inside cookies
    """
    if not s or not isinstance(s, str):
        return {}

    s = s.strip()
    if not s:
        return {}

    # إزالة prefix "Cookie:" لو موجود
    if s.lower().startswith('cookie:'):
        s = s[7:]

    # استبدال newlines بـ ;
    s = s.replace('\n', ';').replace('\r', '')

    # إذا كان التنسيق JSON dict مباشر
    if s.startswith('{') and s.endswith('}'):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, dict):
                return {str(k).strip(): str(v).strip() for k, v in parsed.items()}
        except (json.JSONDecodeError, ValueError):
            pass

    result = {}
    # تقسيم على ; أولاً
    parts = s.split(';')

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # تجاهل attributes زي Secure, HttpOnly, SameSite, Path, Domain, Expires
        skip_attrs = ['secure', 'httponly', 'samesite', 'path', 'domain', 'expires',
                      'max-age', 'partitioned', 'priority']
        lower_part = part.lower().split('=')[0].strip()
        if lower_part in skip_attrs:
            continue

        if '=' not in part:
            continue

        # نستخدم partition عشان نتعامل مع = داخل القيمة
        key, sep, value = part.partition('=')
        key = key.strip()
        value = value.strip()

        if not key:
            continue

        # URL decode للقيمة (ممكن تكون encoded من المتصفح)
        try:
            decoded = urllib.parse.unquote(value)
            value = decoded
        except Exception:
            pass

        # إزالة quotes حول القيمة
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]

        result[key] = value

    return result


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


def _is_business_manager_response(text: str) -> bool:
    html = text.lower()
    return 'business.facebook.com' in html or 'fb_dtsg' in html or 'dtsginitialdata' in html


def _validate_cookie_keys(cookies_dict: dict) -> dict:
    """التحقق من وجود المفاتيح الأساسية والحساسة."""
    result = {
        'has_c_user': 'c_user' in cookies_dict and bool(cookies_dict['c_user']),
        'has_xs': 'xs' in cookies_dict and bool(cookies_dict['xs']),
        'has_datr': 'datr' in cookies_dict and bool(cookies_dict['datr']),
        'has_sb': 'sb' in cookies_dict and bool(cookies_dict['sb']),
        'total_keys': len(cookies_dict),
    }
    result['looks_valid'] = result['has_c_user'] and result['has_xs']
    return result


async def verify_bm_cookies(cookies_str: str, proxy: Optional[str] = None) -> Dict[str, Any]:
    """تحقق من أن الكوكيز صالحة وأن الصفحة فعلاً من Business Manager."""
    parsed = _parse_cookies(cookies_str)

    # التحقق الأساسي من المفاتيح المهمة
    validation = _validate_cookie_keys(parsed)
    if not validation['looks_valid']:
        missing = []
        if not validation['has_c_user']:
            missing.append('c_user')
        if not validation['has_xs']:
            missing.append('xs')
        return {
            'success': False,
            'error': f'الكوكيز ناقصة - مفاتيح أساسية مفقودة: {", ".join(missing)}. '
                     f'تأكد من نسخ الكوكيز كاملة من المتصفح (Developer Tools → Network → Cookies)'
        }

    svc = BMCardService(cookies_str, proxy)
    targets = [BM_BASE, f'{BM_BASE}/billing/']
    last_error = None

    for target in targets:
        try:
            async with svc._client() as c:
                resp = await c.get(target, headers=svc._headers(), cookies=svc.cookies_dict)
                text = resp.text
                url = str(resp.url).lower()

                if any(path in url for path in ['login.php', '/login', '/checkpoint']):
                    last_error = 'تم إعادة التوجيه لصفحة تسجيل الدخول أو تحقق الأمان - الكوكيز منتهية'
                    continue
                if resp.status_code != 200:
                    last_error = f'رد HTTP غير متوقع: {resp.status_code}'
                    continue
                if 'business.facebook.com' not in url and not _is_business_manager_response(text):
                    last_error = 'الرد ليس صفحة Business Manager واضحة'
                    continue

                tok = _extract_dtsg(text)
                if tok:
                    return {'success': True, 'dtsg': tok, 'url': str(resp.url)}
                return {'success': True, 'url': str(resp.url)}
        except Exception as e:
            last_error = f'خطأ عند التحقق من الكوكيز: {str(e)}'

    return {
        'success': False,
        'error': 'فشل التحقق من الكوكيز. ' + (last_error or 'الكوكيز قد تكون منتهية أو غير صحيحة'),
    }


def _extract_dtsg(html: str) -> Optional[str]:
    """استخراج DTSG من HTML - محاولات متعددة."""
    if not html or len(html) < 100:
        return None

    patterns = [
        # الأنماط الشائعة
        r'"DTSGInitialData"[^}]*?"token":"([^"]+)"',
        r'"DTSGInitialData":\s*\{[^}]*?"token":"([^"]+)"',
        r'require\(\s*["\']DTSGInitialData["\']\s*\)\.token\s*[:=]?\s*["\']([^"\']+)["\']',
        r'DTSGInitialData\.token\s*=\s*["\']([^"\']+)["\']',
        r'name="fb_dtsg"\s+value="([^"]+)"',
        r'"fb_dtsg":"([^"]+)"',
        r'name="fb_dtsg" value="([^"]+)"',
        # أنماط إضافية
        r'fb_dtsg["\']?\s*[=:]\s*["\']?([a-zA-Z0-9_-]+)',
        r'DTSG["\']?\s*[=:]\s*["\']?([a-zA-Z0-9_-]+)',
        # في JSON
        r'"dtsg":"([^"]+)"',
        r'"token":"([a-zA-Z0-9_-]{24,})"',
    ]

    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            token = match.group(1)
            if len(token) >= 20:  # DTSG عادة يكون طويل
                return token

    return None


def _extract_js_value(html: str, patterns: List[str]) -> Optional[str]:
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if match:
            value = match.group(1)
            if value:
                return value.strip().strip('"\'')
    return None


def _extract_bm_context(html: str) -> Dict[str, Optional[str]]:
    context = {
        'dtsg': _extract_dtsg(html),
        'user_id': None,
        'business_id': None,
        'ad_account_id': None,
    }

    if not html or len(html) < 100:
        return context

    context['user_id'] = _extract_js_value(html, [
        r'"CurrentUserInitialData"\s*:\s*\{[^}]*?"ACCOUNT_ID"\s*:\s*"(\d+)"',
        r'CurrentUserInitialData\.ACCOUNT_ID\s*=\s*"(\d+)"',
        r'CurrentUserInitialData\.ACCOUNT_ID\s*=\s*(\d+)',
        r'"ACCOUNT_ID"\s*:\s*"(\d+)"',
    ])

    context['business_id'] = _extract_js_value(html, [
        r'"BusinessUnifiedNavigationContext"\s*:\s*\{[^}]*?"businessID"\s*:\s*"(\d+)"',
        r'BusinessUnifiedNavigationContext\.businessID\s*=\s*"(\d+)"',
        r'BusinessUnifiedNavigationContext\.businessID\s*=\s*(\d+)',
        r'"businessID"\s*:\s*"(\d+)"',
    ])

    context['ad_account_id'] = _extract_js_value(html, [
        r'"BusinessUnifiedNavigationContext"\s*:\s*\{[^}]*?"adAccountID"\s*:\s*"(\d+)"',
        r'BusinessUnifiedNavigationContext\.adAccountID\s*=\s*"(\d+)"',
        r'BusinessUnifiedNavigationContext\.adAccountID\s*=\s*(\d+)',
        r'"adAccountID"\s*:\s*"(\d+)"',
    ])

    return context

class BMCardService:
    def __init__(self, cookies_str: str, proxy: Optional[str] = None,
                 dtsg: Optional[str] = None, user_id: Optional[str] = None):
        self.cookies_str  = cookies_str
        self.cookies_dict = _parse_cookies(cookies_str)
        self.proxies      = _get_proxies(proxy)
        self._dtsg: Optional[str] = dtsg  # يمكن تمريره مباشرة
        self._user_id: str = user_id or self.cookies_dict.get('c_user', '')  # أو من الكوكيز
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
        endpoints = [
            f'{BM_BASE}/',
            f'{BM_BASE}/overview',
            f'{BM_BASE}/billing/',
            f'{BM_BASE}/business_locations',
            f'{BM_BASE}/business_settings',
        ]
        session_expired_url_indicators = ['login', 'checkpoint']
        session_expired_text_indicators = [
            'please log in', 'session expired', 'session has expired',
            'must be logged in', 'requires login', 'checkpoint'
        ]

        checked = []

        try:
            async with self._client() as c:
                for endpoint in endpoints:
                    resp = await c.get(endpoint, headers=self._headers(), cookies=self.cookies_dict)
                    text = resp.text
                    url_str = str(resp.url).lower()

                    checked.append({
                        'endpoint': endpoint,
                        'status': resp.status_code,
                        'final_url': url_str,
                        'has_bm': 'business.facebook.com' in url_str or 'business.facebook.com' in text.lower(),
                    })

                    if any(indicator in url_str for indicator in session_expired_url_indicators):
                        continue
                    if any(indicator in text.lower() for indicator in session_expired_text_indicators):
                        continue
                    if resp.status_code != 200:
                        continue

                    ctx = _extract_bm_context(text)
                    if ctx['dtsg']:
                        self._dtsg = ctx['dtsg']
                        if ctx['user_id']:
                            self._user_id = ctx['user_id']
                        return {
                            'success': True,
                            'endpoint': endpoint,
                            'dtsg': self._dtsg,
                            'user_id': self._user_id,
                            'business_id': ctx['business_id'],
                            'ad_account_id': ctx['ad_account_id'],
                        }

                if any(item['has_bm'] for item in checked):
                    return {
                        'success': False,
                        'error': 'لم نتمكن من استخراج DTSG من صفحات Business Manager. تأكد من أن الكوكيز تحتوي على جلسة صالحة.',
                        'details': checked,
                    }

                return {
                    'success': False,
                    'error': 'فشل الوصول إلى Business Manager باستخدام هذه الكوكيز. قد تكون الجلسة غير نشطة أو أن الكوكيز غير كاملة.',
                    'details': checked,
                }
        except Exception as e:
            error_str = str(e)
            if 'timeout' in error_str.lower():
                return {'success': False, 'error': 'انتهاء المهلة الزمنية - قد يكون البروكسي بطيئاً'}
            elif 'proxy' in error_str.lower() or 'connection' in error_str.lower():
                return {'success': False, 'error': f'مشكلة في الاتصال/البروكسي: {error_str[:100]}'}
            return {'success': False, 'error': f'خطأ في الاتصال: {error_str}'}
    async def _gql(self, friendly: str, doc_id: str, variables: dict,
                     bm_id: str, ad_id: str) -> Dict[str, Any]:
        query_params = {}
        if friendly == 'BillingHubPaymentMethodsViewQuery':
            query_params = {'_callFlowletID': '0', '_triggerFlowletID': '2596'}
        elif friendly == 'BillingHubPaymentMethodsBusinessSectionQuery':
            query_params = {'_callFlowletID': '0', '_triggerFlowletID': '1'}

        url = GRAPHQL
        if query_params:
            url = f"{GRAPHQL}?{urllib.parse.urlencode(query_params)}"

        body_dict = {
            'av': self._user_id,
            '__aaid': ad_id,
            '__bid': bm_id,
            '__user': self._user_id,
            '__a': '1',
            'fb_dtsg': self._dtsg,
            'fb_api_caller_class': 'RelayModern',
            'fb_api_req_friendly_name': friendly,
            'variables': json.dumps(variables),
            'doc_id': doc_id,
        }
        body = urllib.parse.urlencode(body_dict)
        try:
            async with self._client() as c:
                resp = await c.post(url, headers=self._headers(),
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
    """جلب البطاقات من Business Manager مع محاولة التعامل مع مشاكل البروكسي والكوكيز."""

    # المحاولة الأولى: مع البروكسي إن وجد
    svc = BMCardService(cookies, proxy)
    r = await svc.fetch_dtsg()

    if not r['success']:
        # إذا كان هناك خطأ وتم استخدام بروكسي، جرب بدون بروكسي
        if proxy:
            # الخطأ قد يكون بسبب البروكسي أو الكوكيز
            # جرب بدون بروكسي أولاً
            svc_no_proxy = BMCardService(cookies, proxy=None)
            r = await svc_no_proxy.fetch_dtsg()

            if r['success']:
                # نجحت بدون بروكسي، استمر
                r = await svc_no_proxy.get_billing_account_id(bm_id, ad_id)
                if not r['success']:
                    return r
                return await svc_no_proxy.get_payment_methods(bm_id, ad_id, r['bm_ad_id'])
            else:
                # فشلت أيضاً بدون بروكسي، فالمشكلة في الكوكيز
                return {
                    'success': False,
                    'error': f"{r['error']}\n\n💡 <b>ملاحظة:</b> حاولنا بدون بروكسي أيضاً، المشكلة في الكوكيز"
                }
        else:
            # بدون بروكسي والفشل موجود، إذاً الكوكيز مشكلة
            return r

    # نجحت مع البروكسي/بدون بروكسي، استمر
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
