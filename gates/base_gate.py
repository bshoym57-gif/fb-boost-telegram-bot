import os
import re
from pathlib import Path
from abc import ABC, abstractmethod
from typing import Optional, Tuple
from aiogram.types import Message, CallbackQuery, PhotoSize
from aiogram.fsm.context import FSMContext
from states import AdObjectives, GateConstants, AdGateStates
from services.proxy_manager import ProxyManager

TEMP_DIR = Path('data/temp')
TEMP_DIR.mkdir(parents=True, exist_ok=True)


class BaseGate(ABC):
    def __init__(self, gate_id: str, gate_name: str, gate_type: str):
        self.gate_id = gate_id
        self.gate_name = gate_name
        self.gate_type = gate_type

    @abstractmethod
    async def enter(self, call: CallbackQuery, state: FSMContext, config: dict):
        pass

    def validate_proxy(self, proxy: str) -> Tuple[bool, Optional[str]]:
        if not proxy or proxy.strip().lower() == 'skip':
            return True, None
        proxy = proxy.strip()
        patterns = [
            r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{2,5}$',
            r'^[a-zA-Z0-9._-]+:\d{2,5}$',
            r'^[a-zA-Z0-9._-]+:[a-zA-Z0-9@._-]+@[\d.a-zA-Z-]+:\d{2,5}$',
        ]
        for pattern in patterns:
            if re.match(pattern, proxy):
                return True, None
        return False, "❌ صيغة البروكسي غير صحيحة\nمثال: 1.2.3.4:8080 أو user:pass@1.2.3.4:8080"

    def validate_cookies(self, cookies: str) -> Tuple[bool, Optional[str]]:
        if not cookies or len(cookies.strip()) < 50:
            return False, "❌ الكوكيز مطلوب أو قصير جداً"
        required_keys = ['c_user', 'xs', 'datr']
        found = [k for k in required_keys if k in cookies]
        if not found:
            return False, "❌ الكوكيز لا يحتوي على بيانات فيسبوك صحيحة\n(يجب أن يحتوي على c_user أو xs أو datr)"
        return True, None

    def validate_ad_account_id(self, account_id: str) -> Tuple[bool, Optional[str]]:
        if not account_id:
            return False, "❌ معرف الحساب الإعلاني مطلوب"
        account_id = account_id.strip().lstrip('act_')
        if not re.match(r'^\d{10,20}$', account_id):
            return False, "❌ معرف الحساب غير صحيح (10-20 رقم)\nمثال: 1234567890123"
        return True, account_id

    def validate_page_id(self, page_id: str) -> Tuple[bool, Optional[str]]:
        if not page_id:
            return False, "❌ معرف الصفحة مطلوب"
        page_id = page_id.strip()
        if not re.match(r'^\d{10,20}$', page_id):
            return False, "❌ معرف الصفحة غير صحيح (10-20 رقم)"
        return True, page_id

    def validate_post_link(self, link: str) -> Tuple[bool, Optional[str]]:
        if not link:
            return False, "❌ رابط أو معرف البوست مطلوب"
        link = link.strip()
        if 'facebook.com' in link or 'fb.watch' in link or re.match(r'^\d{10,30}$', link):
            return True, None
        return False, "❌ الرابط ليس رابط فيسبوك صحيح"

    def extract_post_id(self, text: str) -> Optional[str]:
        text = text.strip()
        if re.match(r'^\d{10,30}$', text):
            return text
        patterns = [
            r'/posts/(\d+)',
            r'story_fbid=(\d+)',
            r'pfbid(\w+)',
            r'/(\d{15,25})/?$',
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                return m.group(1)
        parts = text.rstrip('/').split('/')
        for part in reversed(parts):
            part = part.split('?')[0]
            if re.match(r'^\d{10,30}$', part):
                return part
        return text.split('/')[-1].split('?')[0] or None

    def validate_budget(self, budget: str) -> Tuple[bool, any]:
        if not budget or budget.strip() in ['', 'skip']:
            return True, GateConstants.DEFAULT_BUDGET
        try:
            amount = float(budget.strip())
            if amount < GateConstants.MIN_BUDGET:
                return False, f"❌ الميزانية يجب أن تكون {GateConstants.MIN_BUDGET}$ على الأقل"
            if amount > GateConstants.MAX_BUDGET:
                return False, f"❌ الميزانية كبيرة جداً"
            return True, round(amount, 2)
        except ValueError:
            return False, "❌ الميزانية يجب أن تكون رقم مثل: 10 أو 15.5"

    def validate_days(self, days: str) -> Tuple[bool, any]:
        if not days or days.strip() in ['', 'skip']:
            return True, GateConstants.DEFAULT_DAYS
        try:
            amount = int(days.strip())
            if amount < GateConstants.MIN_DAYS:
                return False, f"❌ عدد الأيام يجب أن يكون {GateConstants.MIN_DAYS} على الأقل"
            if amount > GateConstants.MAX_DAYS:
                return False, f"❌ عدد الأيام كبير جداً"
            return True, amount
        except ValueError:
            return False, "❌ عدد الأيام يجب أن يكون رقم صحيح مثل: 7"

    def validate_audience_id(self, audience_id: str) -> Tuple[bool, Optional[str]]:
        if not audience_id or audience_id.strip().lower() in ['skip', '']:
            return True, None
        audience_id = audience_id.strip()
        if not re.match(r'^\d{10,30}$', audience_id):
            return False, "❌ معرف الأوديانس غير صحيح (أو اكتب skip)"
        return True, audience_id

    def validate_caption(self, caption: str) -> Tuple[bool, Optional[str]]:
        if not caption:
            return False, "❌ نص الإعلان مطلوب"
        caption = caption.strip()
        if len(caption) < GateConstants.MIN_CAPTION_LENGTH:
            return False, f"❌ النص قصير جداً ({GateConstants.MIN_CAPTION_LENGTH} حرف على الأقل)"
        if len(caption) > GateConstants.MAX_CAPTION_LENGTH:
            return False, f"❌ النص طويل جداً (الحد الأقصى {GateConstants.MAX_CAPTION_LENGTH} حرف)"
        return True, None

    def validate_ad_code(self, ad_code: str) -> Tuple[bool, Optional[str]]:
        if not ad_code or len(ad_code.strip()) < 5:
            return False, "❌ Ad Code مطلوب (5 أحرف على الأقل)"
        return True, None

    def validate_image_size(self, file_path: str) -> Tuple[bool, Optional[str]]:
        if not os.path.exists(file_path):
            return False, "❌ الملف غير موجود"
        ext = Path(file_path).suffix.lower()
        if ext not in GateConstants.IMAGE_EXTENSIONS:
            return False, f"❌ امتداد غير مدعوم"
        file_size = os.path.getsize(file_path)
        if file_size > GateConstants.MAX_IMAGE_SIZE:
            return False, f"❌ حجم الصورة كبير جداً (الحد 10MB)"
        return True, None

    async def save_image(self, message: Message) -> Tuple[bool, str, Optional[str]]:
        if not message.photo:
            return False, "❌ أرسل صورة وليس نص أو ملف آخر", None
        photo: PhotoSize = message.photo[-1]
        file_name = f"{self.gate_id}_{message.from_user.id}_{message.message_id}.jpg"
        file_path = TEMP_DIR / file_name
        try:
            # aiogram v3: download() expects the file/file_id as first positional arg
            await message.bot.download(photo.file_id, destination=file_path)
        except Exception as e:
            return False, f"❌ فشل تحميل الصورة: {str(e)}", None
        is_valid, error = self.validate_image_size(str(file_path))
        if not is_valid:
            if file_path.exists():
                file_path.unlink()
            return False, error, None
        return True, "✅ تم تحميل الصورة", str(file_path)

    def cleanup_temp_files(self, file_path: Optional[str]):
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass

    def format_summary(self, data: dict) -> str:
        lines = [
            "📋 <b>مراجعة البيانات</b>\n",
            "━━━━━━━━━━━━━━━━━━━━",
            f"🎯 <b>البوابة:</b> {self.gate_name}",
            "",
            f"🌐 <b>البروكسي:</b> {ProxyManager.mask(data.get('proxy'))}",
            f"🍪 <b>الكوكيز:</b> {'✅ موجود' if data.get('cookies') else '❌ غير موجود'}",
            f"🔢 <b>Account ID:</b> {data.get('ad_account_id', 'غير محدد')}",
        ]
        if self.gate_type == 'standard_ad':
            lines += [
                f"📄 <b>Page ID:</b> {data.get('page_id', 'غير محدد')}",
                f"🔗 <b>Post ID:</b> {data.get('post_id', 'غير محدد')}",
            ]
        elif self.gate_type == 'dark_post':
            lines += [
                f"📄 <b>Page ID:</b> {data.get('page_id', 'غير محدد')}",
                f"🖼 <b>الصورة:</b> {'✅ موجودة' if data.get('image_path') else '❌ بدون صورة'}",
                f"📝 <b>النص:</b> {str(data.get('caption', ''))[:80]}...",
            ]
        elif self.gate_type == 'partner_ship':
            lines += [
                f"📦 <b>Ad Set ID:</b> {data.get('ad_set_id', 'غير محدد')}",
                f"📋 <b>Ad Code:</b> {data.get('ad_code', 'غير محدد')}",
            ]

        lines += [
            "",
            f"🎯 <b>الهدف:</b> {AdObjectives.get_display_name(data.get('objective', ''))}",
            f"👥 <b>Audience ID:</b> {data.get('audience_id') or 'افتراضي'}",
            f"💰 <b>الميزانية اليومية:</b> {data.get('daily_budget', GateConstants.DEFAULT_BUDGET)}$",
            f"📅 <b>عدد الأيام:</b> {data.get('days', GateConstants.DEFAULT_DAYS)}",
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "\n⚠️ هل تريد تأكيد التشغيل؟",
        ]
        return "\n".join(lines)
