"""
Facebook Graph API Client
يتعامل مع Facebook Ads API لإنشاء الحملات الإعلانية

تحسينات:
- Fingerprint كامل (User-Agent + Headers) مع تمثيل أجهزة حقيقية
- معالجة ردود non-JSON من Facebook
- فحص صلاحيات الحساب/الصفحة قبل الإنشاء
- Rollback تلقائي عند فشل أي خطوة بعد إنشاء الحملة
- إصلاح WhatsApp objective
- إصلاح Partnership Creative
"""
from __future__ import annotations

import asyncio
import os
import random
import re
import time
from typing import Optional, Dict, Any

import httpx

from states import AdObjectives


FB_API_VERSION = 'v19.0'
FB_GRAPH_API   = 'https://graph.facebook.com'

# ─────────────────────────────────────────────────────
#  بروفايلات أجهزة كاملة (Fingerprint)
# ─────────────────────────────────────────────────────

DEVICE_PROFILES = [
    # Android - Samsung Galaxy S24
    {
        'ua': 'Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36',
        'platform': 'Android',
        'sec_ch_ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        'sec_ch_ua_mobile': '?1',
        'sec_ch_ua_platform': '"Android"',
        'accept_language': 'ar-EG,ar;q=0.9,en-US;q=0.8',
        'viewport': '412x892',
    },
    # Android - Pixel 8
    {
        'ua': 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Mobile Safari/537.36',
        'platform': 'Android',
        'sec_ch_ua': '"Google Chrome";v="123", "Not:A-Brand";v="8"',
        'sec_ch_ua_mobile': '?1',
        'sec_ch_ua_platform': '"Android"',
        'accept_language': 'en-US,en;q=0.9,ar;q=0.8',
        'viewport': '393x851',
    },
    # iPhone 15 Pro
    {
        'ua': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1',
        'platform': 'iOS',
        'sec_ch_ua': '"Safari";v="17"',
        'sec_ch_ua_mobile': '?1',
        'sec_ch_ua_platform': '"iOS"',
        'accept_language': 'ar-SA,ar;q=0.9',
        'viewport': '390x844',
    },
    # Windows Desktop - Chrome
    {
        'ua': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'platform': 'Windows',
        'sec_ch_ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        'sec_ch_ua_mobile': '?0',
        'sec_ch_ua_platform': '"Windows"',
        'accept_language': 'en-US,en;q=0.9',
        'viewport': '1920x1080',
    },
    # macOS - Safari
    {
        'ua': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15',
        'platform': 'macOS',
        'sec_ch_ua': '"Safari";v="17"',
        'sec_ch_ua_mobile': '?0',
        'sec_ch_ua_platform': '"macOS"',
        'accept_language': 'en-US,en;q=0.9',
        'viewport': '1440x900',
    },
]


def _random_profile() -> dict:
    return random.choice(DEVICE_PROFILES)


# ─────────────────────────────────────────────────────
#  مساعدات
# ─────────────────────────────────────────────────────

def parse_cookies(cookies_str: str) -> dict:
    cookies = {}
    for part in cookies_str.split(';'):
        part = part.strip()
        if '=' in part:
            key, value = part.split('=', 1)
            cookies[key.strip()] = value.strip()
    return cookies


def get_proxies(proxy: Optional[str]) -> Optional[dict]:
    if not proxy:
        return None
    if '://' not in proxy:
        proxy = f'http://{proxy}'
    return {'http://': proxy, 'https://': proxy}


# ─────────────────────────────────────────────────────
#  FacebookAPIClient
# ─────────────────────────────────────────────────────

class FacebookAPIClient:
    """عميل Facebook Graph API — يعمل بالكوكيز مع Fingerprint كامل"""

    def __init__(self, cookies: str, proxy: Optional[str] = None):
        self.cookies_str  = cookies
        self.cookies_dict = parse_cookies(cookies)
        self.proxy        = proxy
        self.proxies      = get_proxies(proxy)
        self.base_url     = f'{FB_GRAPH_API}/{FB_API_VERSION}'
        self._profile     = _random_profile()   # جهاز عشوائي لكل جلسة

    def _get_headers(self, rotate: bool = False) -> dict:
        if rotate:
            self._profile = _random_profile()
        p = self._profile
        return {
            'User-Agent':                p['ua'],
            'Accept':                    'application/json, text/plain, */*',
            'Accept-Language':           p['accept_language'],
            'Accept-Encoding':           'gzip, deflate, br',
            'Referer':                   'https://www.facebook.com/',
            'Origin':                    'https://www.facebook.com',
            'Sec-Ch-Ua':                 p.get('sec_ch_ua', ''),
            'Sec-Ch-Ua-Mobile':          p.get('sec_ch_ua_mobile', '?0'),
            'Sec-Ch-Ua-Platform':        p.get('sec_ch_ua_platform', '"Windows"'),
            'Sec-Fetch-Dest':            'empty',
            'Sec-Fetch-Mode':            'cors',
            'Sec-Fetch-Site':            'same-origin',
            'X-Requested-With':          'XMLHttpRequest',
            'Connection':                'keep-alive',
        }

    async def _request(self, method: str, endpoint: str,
                       rotate_fp: bool = False, **kwargs) -> Dict[str, Any]:
        """طلب HTTP مع معالجة ردود non-JSON وأخطاء الشبكة."""
        url     = f'{self.base_url}/{endpoint}'
        headers = self._get_headers(rotate=rotate_fp)
        try:
            async with httpx.AsyncClient(
                timeout=60,
                proxies=self.proxies,
                follow_redirects=True,
            ) as client:
                resp = await client.request(
                    method, url,
                    headers=headers,
                    cookies=self.cookies_dict,
                    **kwargs
                )

                # ── معالجة ردود non-JSON ──
                content_type = resp.headers.get('content-type', '')
                if 'application/json' not in content_type and 'text/javascript' not in content_type:
                    # Facebook أحياناً يرجع HTML لو الجلسة منتهية
                    if resp.status_code in (401, 403):
                        return {'success': False,
                                'error': 'انتهت صلاحية الكوكيز أو غير مصرح (401/403)',
                                'raw': resp.text[:200]}
                    if 'login' in resp.text.lower() or 'checkpoint' in resp.text.lower():
                        return {'success': False,
                                'error': 'فيسبوك طلب تسجيل دخول — تحقق من الكوكيز',
                                'raw': resp.text[:200]}

                try:
                    data = resp.json()
                except Exception:
                    return {
                        'success': False,
                        'error':   f'رد غير JSON من Facebook (HTTP {resp.status_code})',
                        'raw':     resp.text[:300],
                    }

                if resp.status_code != 200 or 'error' in data:
                    err = data.get('error', {})
                    msg = err.get('message') or err.get('error_user_msg') or f'HTTP {resp.status_code}'
                    code = err.get('code', '')
                    return {'success': False, 'error': f'[{code}] {msg}' if code else msg, 'raw': data}

                return {'success': True, 'data': data}

        except httpx.ProxyError as e:
            return {'success': False, 'error': f'خطأ في البروكسي: {e}', 'proxy_error': True}
        except httpx.ConnectTimeout:
            return {'success': False, 'error': 'انتهت مهلة الاتصال — جرّب بروكسي آخر'}
        except httpx.ReadTimeout:
            return {'success': False, 'error': 'انتهت مهلة القراءة من Facebook'}
        except Exception as e:
            return {'success': False, 'error': f'خطأ شبكة: {e}'}

    # ─────────────── فحص صلاحيات ───────────────

    async def check_ad_account(self, ad_account_id: str) -> Dict[str, Any]:
        """تحقق من صلاحية الوصول لحساب الإعلانات."""
        r = await self._request(
            'GET', f'act_{ad_account_id}',
            params={'fields': 'id,name,account_status,currency'}
        )
        if not r['success']:
            return r
        status = r['data'].get('account_status')
        status_map = {1: 'نشط', 2: 'معلق', 3: 'محذوف', 7: 'موقوف', 9: 'مغلق'}
        if status not in (None, 1):
            return {
                'success': False,
                'error': f"حساب الإعلانات {status_map.get(status, f'حالة {status}')} — لا يمكن إنشاء إعلان",
            }
        return {'success': True, 'data': r['data']}

    async def check_page_access(self, page_id: str) -> Dict[str, Any]:
        """تحقق من صلاحية الوصول للصفحة."""
        r = await self._request(
            'GET', page_id,
            params={'fields': 'id,name,whatsapp_number,verification_status'}
        )
        if not r['success']:
            return r
        return {
            'success':          True,
            'data':             r['data'],
            'whatsapp_number':  r['data'].get('whatsapp_number', ''),
        }

    async def resolve_post_link(self, link_or_id: str) -> Dict[str, Any]:
        """
        من رابط/معرف بوست فيسبوك، يستخرج (page_id, post_id).
        post_id المُرجَع "خام" (بدون بادئة page_id_) لأن
        create_ad_creative_post بيبنى object_story_id بالشكل page_id_post_id.

        الترتيب:
        1) صيغة pageId_postId مباشرة من المستخدم.
        2) URL فيه page_id + post_id (الأكثر موثوقية، بدون أى API).
        3) URL فيه post_id فقط، أو رقم خام → fallback على Graph API
           (قد يفشل لو البوست status أو pfbid).
        """
        text = (link_or_id or '').strip()
        if not text:
            return {'success': False, 'error': 'الرابط أو معرف البوست فارغ'}

        # 1) صيغة "pageId_postId" — رجّع post_id خام
        m = re.match(r'^(\d+)_(\w+)$', text)
        if m:
            return {'success': True, 'page_id': m.group(1), 'post_id': m.group(2)}

        # 2) محاولة استخراج page_id و post_id من URL
        page_id_from_url: Optional[str] = None
        post_id_from_url: Optional[str] = None

        m = re.search(r'(?:^|[?&])id=(\d+)', text)
        if m:
            page_id_from_url = m.group(1)
        else:
            m = re.search(r'facebook\.com/(\d{10,20})/(?:posts|videos|photos|reels)', text)
            if m:
                page_id_from_url = m.group(1)

        url_patterns = [
            r'/posts/(pfbid\w+)',
            r'/posts/(\d+)',
            r'story_fbid=(pfbid\w+|\d+)',
            r'/videos/(\d+)',
            r'/reel/(\d+)',
            r'fbid=(\d+)',
            r'/permalink/(\d+)',
            r'/(\d{10,30})/?(?:\?|$)',
        ]
        for p in url_patterns:
            m = re.search(p, text)
            if m:
                post_id_from_url = m.group(1)
                break

        # 2-أ) الاتنين موجودين فى URL → نثق بيهم بدون استعلام (الأفضل)
        # FB ad creative بيقبل page_id_pfbid... وبيقبل page_id_<numeric>
        if page_id_from_url and post_id_from_url:
            return {
                'success': True,
                'page_id': page_id_from_url,
                'post_id': post_id_from_url,
            }

        # 3) رقم خام أو URL ناقص → استعلام Graph API
        if re.match(r'^\d{6,30}$', text):
            return await self._resolve_via_api(text)
        if post_id_from_url:
            return await self._resolve_via_api(post_id_from_url)

        return {'success': False, 'error': 'تعذّر استخراج معرف البوست من الرابط'}

    async def _resolve_via_api(self, post_id: str) -> Dict[str, Any]:
        """
        يسأل Graph API عن البوست لاستخراج page_id والـ post_id الخام.
        قد يفشل بـ #12 لو البوست status — فى الحالة دى لازم URL يحتوى page_id.
        """
        r = await self._request('GET', post_id, params={'fields': 'id,from'})
        if not r['success']:
            return {
                'success': False,
                'error': f'فشل التحقق من البوست عبر Facebook: {r.get("error")}',
            }
        data = r.get('data', {}) or {}
        from_obj = data.get('from') or {}
        page_id = from_obj.get('id')
        full_post_id = data.get('id') or post_id
        if not page_id:
            return {
                'success': False,
                'error': 'لم يُعثر على معرف الصفحة (from) فى رد Facebook — تأكد أن الرابط لمنشور صفحة.',
            }
        # full_post_id = "pageId_postId" → نستخرج post_id الخام
        bare_post_id = full_post_id
        if full_post_id.startswith(f'{page_id}_'):
            bare_post_id = full_post_id.split('_', 1)[1]
        return {'success': True, 'page_id': page_id, 'post_id': bare_post_id}

    async def check_page_can_create_content(self, page_id: str) -> Dict[str, Any]:
        """
        تحقق من إن المستخدم عنده صلاحية إنشاء منشورات جديدة على الصفحة
        (Content Creator أو أعلى). ضروري لإعلانات الدارك بوست.
        المعلن فقط (ADVERTISE) مش كفاية.
        """
        r = await self._request(
            'GET', page_id,
            params={'fields': 'name,tasks'}
        )
        if not r['success']:
            return r
        tasks = r['data'].get('tasks', []) or []
        # الصلاحيات اللى تسمح بإنشاء محتوى جديد
        ALLOWED = {'CREATE_CONTENT', 'MANAGE', 'MODERATE', 'EDIT_PROFILE'}
        if not any(t in ALLOWED for t in tasks):
            tasks_label = '، '.join(tasks) if tasks else 'لا توجد'
            return {
                'success': False,
                'error': (
                    'دورك على هذه الصفحة لا يسمح بإنشاء منشورات جديدة (الدارك بوست).\n\n'
                    f'الصلاحيات الحالية: {tasks_label}\n\n'
                    'الدارك بوست يتطلّب دور <b>Content Creator</b> أو أعلى — '
                    'دور "معلن فقط" غير كافٍ.\n\n'
                    '<b>حلول مقترحة:</b>\n'
                    '• استخدم بوابة "إعلان رابط بوست" بدلاً من الدارك بوست — '
                    'لتعزيز منشور موجود مسبقاً على الصفحة (يعمل بصلاحية المعلن).\n'
                    '• اطلب من مدير الصفحة رفع صلاحيتك إلى Content Creator.'
                ),
                'tasks': tasks,
            }
        return {'success': True, 'tasks': tasks}

    # ─────────────── حملة ───────────────

    async def create_campaign(self, ad_account_id: str, name: str,
                               objective: str) -> Dict[str, Any]:
        fb_objective = AdObjectives.get_fb_objective(objective)
        result = await self._request(
            'POST', f'act_{ad_account_id}/campaigns',
            json={
                'name':                   name,
                'objective':              fb_objective,
                'special_ad_categories':  [],
                'status':                 'PAUSED',
            }
        )
        if result['success']:
            result['campaign_id'] = result['data'].get('id')
        return result

    async def delete_campaign(self, campaign_id: str) -> Dict[str, Any]:
        """حذف حملة (للـ rollback)."""
        return await self._request('DELETE', campaign_id)

    async def delete_ad_set(self, ad_set_id: str) -> Dict[str, Any]:
        """حذف Ad Set (للـ rollback)."""
        return await self._request('DELETE', ad_set_id)

    async def delete_ad(self, ad_id: str) -> Dict[str, Any]:
        """حذف إعلان (للـ rollback)."""
        return await self._request('DELETE', ad_id)

    # ─────────────── Ad Set ───────────────

    async def create_ad_set(
        self,
        ad_account_id: str,
        campaign_id:   str,
        page_id:       str,
        name:          str,
        objective:     str,
        daily_budget_usd: float,
        days:             int,
        audience_id:      Optional[str] = None,
        whatsapp_phone:   Optional[str] = None,
    ) -> Dict[str, Any]:
        optimization_goal = AdObjectives.get_optimization_goal(objective)
        targeting: dict   = {
            'geo_locations': {'countries': ['EG']},
            'age_min': 18,
            'age_max': 65,
        }
        if audience_id:
            targeting['custom_audiences'] = [{'id': audience_id}]

        promoted_object: dict = {'page_id': page_id}
        if objective == AdObjectives.MESSAGES_WHATSAPP:
            phone = (whatsapp_phone or '').strip().lstrip('+')
            if not phone:
                return {
                    'success': False,
                    'error':   'يجب إدخال رقم واتساب للهدف MESSAGES_WHATSAPP',
                }
            promoted_object['whatsapp_phone_number'] = phone

        payload = {
            'name':               name,
            'campaign_id':        campaign_id,
            'optimization_goal':  optimization_goal,
            'billing_event':      'IMPRESSIONS',
            'daily_budget':       int(daily_budget_usd * 100),
            'start_time':         int(time.time()),
            'end_time':           int(time.time()) + (days * 86400),
            'targeting':          targeting,
            'promoted_object':    promoted_object,
            'status':             'PAUSED',
        }
        result = await self._request(
            'POST', f'act_{ad_account_id}/adsets', json=payload
        )
        if result['success']:
            result['ad_set_id'] = result['data'].get('id')
        return result

    # ─────────────── Creative ───────────────

    async def upload_ad_image(self, ad_account_id: str,
                              image_path: str) -> Dict[str, Any]:
        """
        رفع صورة على مستوى الـ Ad Account (Marketing API).
        ده الـ endpoint الصحيح لإعلانات الدارك بوست — بيشتغل بكوكيز المستخدم
        وبيرجع image_hash يستخدم فى creative.photo_data.image_hash.
        """
        if not ad_account_id or not str(ad_account_id).strip().isdigit() or int(ad_account_id) <= 0:
            return {'success': False,
                    'error': 'Ad Account ID غير صالح'}

        with open(image_path, 'rb') as f:
            image_data = f.read()
        file_name = os.path.basename(image_path) or 'image.jpg'

        # Headers لرفع الصور: نتجنب Accept-Encoding: br لتفادى أخطاء فك الضغط،
        # ونزيل Content-Type لو موجود (httpx بيحدده تلقائياً للـ multipart).
        headers = self._get_headers()
        headers['Accept-Encoding'] = 'gzip, deflate'
        headers.pop('Content-Type', None)

        async with httpx.AsyncClient(
            timeout=120, proxies=self.proxies, follow_redirects=True
        ) as client:
            resp = await client.post(
                f'{self.base_url}/act_{ad_account_id}/adimages',
                headers=headers,
                cookies=self.cookies_dict,
                files={'source': (file_name, image_data, 'image/jpeg')},
            )
            raw_text = resp.text
            try:
                data = resp.json()
            except Exception:
                # نطبع الـ raw response فى لوج البوت لتسهيل التشخيص
                snippet = raw_text[:300] if raw_text else '(فارغ)'
                print(f'[upload_ad_image] non-JSON HTTP {resp.status_code}: {snippet}')
                return {'success': False,
                        'error': (
                            f'رد غير JSON من Facebook عند رفع الصورة (HTTP {resp.status_code}).\n'
                            f'مقتطف: {snippet[:200]}'
                        )}

            if resp.status_code == 200 and isinstance(data.get('images'), dict) and data['images']:
                first_key = next(iter(data['images']))
                img = data['images'][first_key]
                image_hash = img.get('hash')
                if image_hash:
                    return {'success': True, 'image_hash': image_hash, 'data': data}

            err_obj = data.get('error', {}) if isinstance(data, dict) else {}
            err_msg = err_obj.get('message') or err_obj.get('error_user_msg') or f'HTTP {resp.status_code}'
            err_code = err_obj.get('code')

            if err_code == 100 or 'does not resolve to a valid user' in str(err_msg).lower():
                return {
                    'success': False,
                    'error': (
                        'الكوكيز لا تمتلك صلاحية الرفع على هذا الحساب الإعلاني.\n'
                        '• تأكد أن Ad Account ID يخص نفس الحساب المسجَّل بالكوكيز\n'
                        '• تأكد أن الكوكيز حديثة وغير منتهية\n'
                        '• تأكد أن لديك صلاحية على الحساب الإعلاني'
                    ),
                    'fb_code': err_code,
                }
            return {'success': False, 'error': f'[{err_code}] {err_msg}' if err_code else err_msg,
                    'fb_code': err_code}

    async def upload_photo(self, page_id: str, image_path: str,
                           caption: str) -> Dict[str, Any]:
        # تحقق من صلاحية Page ID قبل إرسال الطلب
        if not page_id or not str(page_id).strip().isdigit() or int(page_id) <= 0:
            return {'success': False,
                    'error': 'Page ID غير صالح — تحقق من معرف الصفحة وأعد المحاولة'}

        with open(image_path, 'rb') as f:
            image_data = f.read()
        async with httpx.AsyncClient(
            timeout=120, proxies=self.proxies, follow_redirects=True
        ) as client:
            resp = await client.post(
                f'{self.base_url}/{page_id}/photos',
                headers=self._get_headers(),
                cookies=self.cookies_dict,
                data={'caption': caption, 'published': 'false'},
                files={'source': ('image.jpg', image_data, 'image/jpeg')}
            )
            try:
                data = resp.json()
            except Exception:
                return {'success': False,
                        'error': f'رد غير JSON عند رفع الصورة (HTTP {resp.status_code})'}
            if resp.status_code == 200 and 'id' in data:
                return {'success': True, 'photo_id': data['id'], 'data': data}

            err_obj = data.get('error', {}) if isinstance(data, dict) else {}
            err_msg = err_obj.get('message') or err_obj.get('error_user_msg') or f'HTTP {resp.status_code}'
            err_code = err_obj.get('code')

            # خطأ FB #100 — غالباً الكوكيز/الصلاحيات ليست لهذه الصفحة
            if err_code == 100 or 'does not resolve to a valid user' in str(err_msg).lower():
                return {
                    'success': False,
                    'error': (
                        'الكوكيز لا تمتلك صلاحية النشر على هذه الصفحة، '
                        'أو الـ Page ID غير صحيح. تحقق من:\n'
                        '• أن Page ID يخص صفحة تديرها بنفس الحساب\n'
                        '• أن الكوكيز حديثة وغير منتهية\n'
                        '• أن لديك صلاحية المشرف على الصفحة'
                    ),
                    'fb_code': err_code,
                }
            return {'success': False, 'error': err_msg, 'fb_code': err_code}

    async def create_ad_creative_post(
        self, ad_account_id: str, page_id: str,
        post_id: str, objective: str
    ) -> Dict[str, Any]:
        cta_type = AdObjectives.get_cta_type(objective)
        result = await self._request(
            'POST', f'act_{ad_account_id}/adcreatives',
            json={
                'name':             'Creative - Post Boost',
                'object_story_id':  f'{page_id}_{post_id}',
                'call_to_action':   {'type': cta_type},
            }
        )
        if result['success']:
            result['creative_id'] = result['data'].get('id')
        return result

    async def create_ad_creative_dark_post(
        self, ad_account_id: str, page_id: str,
        image_hash: str, caption: str, objective: str
    ) -> Dict[str, Any]:
        cta_type = AdObjectives.get_cta_type(objective)
        result = await self._request(
            'POST', f'act_{ad_account_id}/adcreatives',
            json={
                'name': 'Creative - Dark Post',
                'object_story_spec': {
                    'page_id': page_id,
                    'photo_data': {
                        'image_hash':     image_hash,
                        'message':         caption,
                        'call_to_action':  {'type': cta_type},
                    }
                }
            }
        )
        if result['success']:
            result['creative_id'] = result['data'].get('id')
        return result

    async def create_ad_creative_partner(
        self,
        ad_account_id:    str,
        page_id:          str,
        partner_page_id:  str,
        post_id:          str,
        objective:        str,
    ) -> Dict[str, Any]:
        """
        إنشاء Creative لإعلان Partnership (Branded Content).
        partner_page_id: صفحة الشريك (المنشئ)
        post_id:         معرف البوست المراد تعزيزه
        """
        cta_type = AdObjectives.get_cta_type(objective)
        result = await self._request(
            'POST', f'act_{ad_account_id}/adcreatives',
            json={
                'name': 'Creative - Partnership',
                'object_story_spec': {
                    'page_id': page_id,
                    'link_data': {
                        'link':            f'https://www.facebook.com/{partner_page_id}/posts/{post_id}',
                        'message':         f'منشور مشترك بين الصفحتين',
                        'call_to_action':  {'type': cta_type},
                    },
                },
                'branded_content_sponsor_page_id': page_id,
            }
        )
        if result['success']:
            result['creative_id'] = result['data'].get('id')
        return result

    async def create_ad(
        self, ad_account_id: str, ad_set_id: str,
        creative_id: str, name: str
    ) -> Dict[str, Any]:
        result = await self._request(
            'POST', f'act_{ad_account_id}/ads',
            json={
                'name':      name,
                'adset_id':  ad_set_id,
                'creative':  {'creative_id': creative_id},
                'status':    'ACTIVE',
            }
        )
        if result['success']:
            result['ad_id'] = result['data'].get('id')
        return result

    # ─────────────── تفعيل / إيقاف ───────────────

    async def activate_campaign(self, campaign_id: str) -> Dict[str, Any]:
        return await self._request('POST', campaign_id, json={'status': 'ACTIVE'})

    async def activate_ad_set(self, ad_set_id: str) -> Dict[str, Any]:
        return await self._request('POST', ad_set_id, json={'status': 'ACTIVE'})

    async def pause_campaign(self, campaign_id: str) -> Dict[str, Any]:
        return await self._request('POST', campaign_id, json={'status': 'PAUSED'})

    async def pause_ad_set(self, ad_set_id: str) -> Dict[str, Any]:
        return await self._request('POST', ad_set_id, json={'status': 'PAUSED'})

    async def pause_ad(self, ad_id: str) -> Dict[str, Any]:
        return await self._request('POST', ad_id, json={'status': 'PAUSED'})

    # ─────────────── بوستات الصفحة ───────────────

    async def get_page_posts(self, page_id: str, limit: int = 10) -> Dict[str, Any]:
        result = await self._request(
            'GET', f'{page_id}/posts',
            params={'fields': 'id,message,story,created_time', 'limit': limit}
        )
        if result['success']:
            result['posts'] = result['data'].get('data', [])
        return result


# ─────────────────────────────────────────────────────
#  دوال مساعدة
# ─────────────────────────────────────────────────────

async def fetch_page_posts(cookies: str, page_id: str,
                           proxy: Optional[str] = None,
                           limit: int = 10) -> Dict[str, Any]:
    client = FacebookAPIClient(cookies, proxy)
    return await client.get_page_posts(page_id, limit)


async def resolve_post_link(cookies: str, link_or_id: str,
                            proxy: Optional[str] = None) -> Dict[str, Any]:
    """مساعد على مستوى الموديول لاستخراج (page_id, post_id) من رابط بوست."""
    client = FacebookAPIClient(cookies, proxy)
    return await client.resolve_post_link(link_or_id)


async def _rollback(client: FacebookAPIClient, *,
                    campaign_id: Optional[str] = None,
                    ad_set_id:   Optional[str] = None,
                    ad_id:       Optional[str] = None):
    """حذف كل ما تم إنشاؤه عند حدوث خطأ (rollback)."""
    if ad_id:
        await client.delete_ad(ad_id)
    if ad_set_id:
        await client.delete_ad_set(ad_set_id)
    if campaign_id:
        await client.delete_campaign(campaign_id)


async def _pause_all(client: FacebookAPIClient,
                     campaign_id: str, ad_set_id: str, ad_id: str):
    """إيقاف الحملة والـ Ad Set والإعلان عبر Marketing API."""
    await asyncio.sleep(1)
    await client.pause_ad(ad_id)
    await client.pause_ad_set(ad_set_id)
    await client.pause_campaign(campaign_id)


def _is_inconclusive_error(err_text: str) -> bool:
    """
    أخطاء graph API بتظهر أحياناً لأسباب بنية (app context) مش بسبب صلاحيات
    حقيقية. الفحص الاستباقى ميقدرش يفصل بينها، فنعتبرها inconclusive
    ونكمّل — العمليات الفعلية هتطلع الخطأ الحقيقى لو فيه.
    """
    if not err_text:
        return False
    t = str(err_text).lower()
    # (#200) Provide valid app ID, (#2500) An active access token, ...
    return '#200' in t or '#2500' in t or 'provide valid app id' in t


async def _check_permissions(client: FacebookAPIClient,
                              ad_account_id: str,
                              page_id: str) -> Optional[Dict[str, Any]]:
    """
    فحص صلاحيات الحساب والصفحة قبل البدء.
    يُرجع None لو كل شيء تمام (أو الفحص inconclusive)، وdict خطأ لو في مشكلة حقيقية.
    """
    acc_check = await client.check_ad_account(ad_account_id)
    if not acc_check['success']:
        if _is_inconclusive_error(acc_check.get('error', '')):
            # الفحص الاستباقى مش قادر يحسم — نكمّل والعمليات الفعلية هتظهر
            # الخطأ الحقيقى لو فيه مشكلة
            pass
        else:
            return {'success': False,
                    'step':  'فحص حساب الإعلانات',
                    'error': acc_check['error']}

    page_check = await client.check_page_access(page_id)
    if not page_check['success']:
        if _is_inconclusive_error(page_check.get('error', '')):
            pass
        else:
            return {'success': False,
                    'step':  'فحص صلاحيات الصفحة',
                    'error': page_check['error']}

    return None   # لا توجد مشكلة


# ─────────────────────────────────────────────────────
#  النواة المشتركة لبناء الإعلان
# ─────────────────────────────────────────────────────

async def _run_ad_core(
    client:        FacebookAPIClient,
    data:          dict,
    campaign_name: str,
    page_id:       str,
    objective:     str,
    image_hash:    Optional[str] = None,
    partner_page_id: Optional[str] = None,
    partner_post_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Campaign → AdSet → Creative → Ad → تفعيل
    مع rollback كامل عند الفشل في أي خطوة.
    """
    campaign_id: Optional[str] = None
    ad_set_id:   Optional[str] = None
    ad_id:       Optional[str] = None

    # ── 1. فحص الصلاحيات ──
    perm_err = await _check_permissions(client, data['ad_account_id'], page_id)
    if perm_err:
        return perm_err

    # ── 2. الحملة ──
    step     = "إنشاء الحملة"
    campaign = await client.create_campaign(data['ad_account_id'], campaign_name, objective)
    if not campaign['success']:
        return {'success': False, 'step': step, 'error': campaign['error']}
    campaign_id = campaign['campaign_id']

    # ── 3. Ad Set ──
    step   = "إنشاء Ad Set"
    ad_set = await client.create_ad_set(
        data['ad_account_id'], campaign_id, page_id,
        f"AdSet - {campaign_name}", objective,
        data.get('daily_budget', 10.0),
        data.get('days', 7),
        data.get('audience_id'),
        data.get('whatsapp_phone'),
    )
    if not ad_set['success']:
        await _rollback(client, campaign_id=campaign_id)
        return {'success': False, 'step': step, 'error': ad_set['error']}
    ad_set_id = ad_set['ad_set_id']

    # ── 4. Creative ──
    step = "إنشاء Creative"
    if image_hash:
        creative = await client.create_ad_creative_dark_post(
            data['ad_account_id'], page_id, image_hash, data.get('caption', ''), objective)
    elif partner_page_id and partner_post_id:
        creative = await client.create_ad_creative_partner(
            data['ad_account_id'], page_id, partner_page_id, partner_post_id, objective)
    else:
        creative = await client.create_ad_creative_post(
            data['ad_account_id'], page_id, data.get('post_id', ''), objective)

    if not creative['success']:
        await _rollback(client, campaign_id=campaign_id, ad_set_id=ad_set_id)
        return {'success': False, 'step': step, 'error': creative['error']}
    creative_id = creative['creative_id']

    # ── 5. الإعلان ──
    step = "إنشاء الإعلان"
    ad   = await client.create_ad(
        data['ad_account_id'], ad_set_id, creative_id, f"Ad - {campaign_name}")
    if not ad['success']:
        await _rollback(client, campaign_id=campaign_id, ad_set_id=ad_set_id)
        return {'success': False, 'step': step, 'error': ad['error']}
    ad_id = ad['ad_id']

    # ── 6. تفعيل ──
    step = "تفعيل الحملة"
    act_campaign = await client.activate_campaign(campaign_id)
    if not act_campaign['success']:
        await _rollback(client, campaign_id=campaign_id,
                        ad_set_id=ad_set_id, ad_id=ad_id)
        return {'success': False, 'step': step, 'error': act_campaign['error']}

    step = "تفعيل Ad Set"
    act_adset = await client.activate_ad_set(ad_set_id)
    if not act_adset['success']:
        await _rollback(client, campaign_id=campaign_id,
                        ad_set_id=ad_set_id, ad_id=ad_id)
        return {'success': False, 'step': step, 'error': act_adset['error']}

    return {
        'success':     True,
        'campaign_id': campaign_id,
        'ad_set_id':   ad_set_id,
        'creative_id': creative_id,
        'ad_id':       ad_id,
    }


# ─────────────────────────────────────────────────────
#  دوال العامة لكل بوابة
# ─────────────────────────────────────────────────────

async def run_standard_ad(data: dict) -> Dict[str, Any]:
    """إعلان رابط بوست — نشر نشط"""
    client        = FacebookAPIClient(data['cookies'], data.get('proxy'))
    campaign_name = f"Boost - {data['post_id'][:8]}"
    return await _run_ad_core(
        client, data, campaign_name,
        data['page_id'], data['objective']
    )


async def run_standard_ad_then_pause(data: dict) -> Dict[str, Any]:
    """إعلان رابط بوست — نشر ثم إيقاف"""
    client        = FacebookAPIClient(data['cookies'], data.get('proxy'))
    campaign_name = f"Boost - {data['post_id'][:8]}"
    result = await _run_ad_core(
        client, data, campaign_name,
        data['page_id'], data['objective']
    )
    if result['success']:
        await _pause_all(client, result['campaign_id'],
                         result['ad_set_id'], result['ad_id'])
        result['paused'] = True
    return result


async def run_dark_post_ad(data: dict) -> Dict[str, Any]:
    """Dark Post — نشر نشط"""
    client        = FacebookAPIClient(data['cookies'], data.get('proxy'))
    page_id       = data['page_id']
    campaign_name = f"DarkPost - {page_id[:8]}"
    image_hash    = None

    # فحص استباقي: لازم يكون للمستخدم صلاحية إنشاء محتوى على الصفحة
    role_check = await client.check_page_can_create_content(page_id)
    if not role_check['success']:
        return {'success': False, 'step': 'فحص دور الصفحة', 'error': role_check['error']}

    if data.get('image_path'):
        step  = "رفع الصورة"
        upload = await client.upload_ad_image(data['ad_account_id'], data['image_path'])
        if not upload['success']:
            return {'success': False, 'step': step, 'error': upload['error']}
        image_hash = upload['image_hash']

    result = await _run_ad_core(client, data, campaign_name, page_id, data['objective'],
                                image_hash=image_hash)
    if result['success']:
        result['image_hash'] = image_hash
    return result


async def run_dark_post_ad_then_pause(data: dict) -> Dict[str, Any]:
    """Dark Post — نشر ثم إيقاف"""
    client        = FacebookAPIClient(data['cookies'], data.get('proxy'))
    page_id       = data['page_id']
    campaign_name = f"DarkPost - {page_id[:8]}"
    image_hash    = None

    # فحص استباقي: لازم يكون للمستخدم صلاحية إنشاء محتوى على الصفحة
    role_check = await client.check_page_can_create_content(page_id)
    if not role_check['success']:
        return {'success': False, 'step': 'فحص دور الصفحة', 'error': role_check['error']}

    if data.get('image_path'):
        step  = "رفع الصورة"
        upload = await client.upload_ad_image(data['ad_account_id'], data['image_path'])
        if not upload['success']:
            return {'success': False, 'step': step, 'error': upload['error']}
        image_hash = upload['image_hash']

    result = await _run_ad_core(client, data, campaign_name, page_id, data['objective'],
                                image_hash=image_hash)
    if result['success']:
        result['image_hash'] = image_hash
        await _pause_all(client, result['campaign_id'],
                         result['ad_set_id'], result['ad_id'])
        result['paused'] = True
    return result


async def run_partner_ship_ad(data: dict) -> Dict[str, Any]:
    """Partnership Ad — نشر نشط"""
    client        = FacebookAPIClient(data['cookies'], data.get('proxy'))
    campaign_name = f"PartnerShip - {data['ad_account_id'][:8]}"
    return await _run_ad_core(
        client, data, campaign_name,
        data.get('page_id', ''), data['objective'],
        partner_page_id=data.get('partner_page_id'),
        partner_post_id=data.get('partner_post_id'),
    )


async def run_partner_ship_ad_then_pause(data: dict) -> Dict[str, Any]:
    """Partnership Ad — نشر ثم إيقاف"""
    client        = FacebookAPIClient(data['cookies'], data.get('proxy'))
    campaign_name = f"PartnerShip - {data['ad_account_id'][:8]}"
    result = await _run_ad_core(
        client, data, campaign_name,
        data.get('page_id', ''), data['objective'],
        partner_page_id=data.get('partner_page_id'),
        partner_post_id=data.get('partner_post_id'),
    )
    if result['success']:
        await _pause_all(client, result['campaign_id'],
                         result['ad_set_id'], result['ad_id'])
        result['paused'] = True
    return result
