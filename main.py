import asyncio
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.context import FSMContext

from database import DB, is_subscribed
from keyboards import (
    main_menu, admin_panel, back_home, back_admin,
    proxy_selection_keyboard, objective_selection_keyboard,
    confirm_keyboard, activate_or_back_keyboard, image_received_keyboard,
    admin_users_keyboard, user_action_keyboard, settings_keyboard,
    post_selection_keyboard,
    tools_menu, ad_tools_menu, link_tools_menu,
    bm_card_select_keyboard, bm_proxy_keyboard,
)
from states import UserFlow, AdminFlow, AdGateStates, BMToolStates
from services.bm_card_service import get_bm_cards, warm_bm_cards
from services.redeem import generate_code
from services.proxy_manager import ProxyManager
from gates.standard_ad_gate import StandardAdGate
from gates.dark_post_gate import DarkPostGate
from gates.partner_ship_gate import PartnerShipGate

TOKEN        = os.environ.get('TELEGRAM_BOT_TOKEN', '')
ADMIN_PASS   = 'Nemo@1986'
ADMIN_CMD    = 'beshoy'
BOT_NAME     = os.environ.get('BOT_NAME',    '⚡ FB Boost Bot')
SUPPORT_URL  = os.environ.get('SUPPORT_URL', 'https://t.me/')

GATE_NAMES = {
    'standard_ad':  '🟢 إعلان رابط بوست',
    'dark_post':    '🔵 إعلان دارك بوست',
    'partner_ship': '🟣 إعلان بارتنر شيب',
}

db            = DB('data/bot.db')
proxy_manager = ProxyManager('proxies.txt')

standard_ad_gate  = StandardAdGate()
dark_post_gate    = DarkPostGate()
partner_ship_gate = PartnerShipGate()

GATES = {
    'standard_ad':  standard_ad_gate,
    'dark_post':    dark_post_gate,
    'partner_ship': partner_ship_gate,
}

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
redis_url = os.environ.get('REDIS_URL', '')
storage = RedisStorage.from_url(redis_url) if redis_url else MemoryStorage()
dp  = Dispatcher(storage=storage)

# ─── جدول الجلسات النشطة: user_id → session_token ───
_sessions: dict[int, str] = {}


# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────

def _mask_proxy(proxy: str) -> str:
    """إخفاء كلمة مرور البروكسي: user:pass@host:port → user:****@host:port"""
    if '@' in proxy:
        creds, rest = proxy.split('@', 1)
        if ':' in creds:
            user = creds.split(':', 1)[0]
            return f"{user}:****@{rest}"
    return proxy


def _new_session(user_id: int) -> str:
    """ينشئ session token جديد للمستخدم ويحفظه في الجدول."""
    tok = uuid.uuid4().hex[:10]
    _sessions[user_id] = tok
    return tok


async def _check_session(call: CallbackQuery, state: FSMContext) -> bool:
    """
    يتحقق أن الـ callback جاي من الجلسة النشطة الحالية.
    للـ proxy callbacks: يقارن message_id لمنع ردود رسائل قديمة.
    للـ mid-flow callbacks: الـ state filter كافي.
    """
    data       = await state.get_data()
    active_mid = data.get('active_msg_id')
    if active_mid and call.message.message_id != active_mid:
        await call.answer('⚠️ هذه الرسالة قديمة، ابدأ من جديد.', show_alert=True)
        return False
    return True

def menu_for(user_id: int):
    return main_menu(GATE_NAMES, is_subscribed(db.get_user(user_id)), SUPPORT_URL)


async def send_home(msg_or_call, user_id: int):
    row       = db.get_user(user_id)
    subscribed = is_subscribed(row)
    name      = (row['custom_name'] or row['first_name'] or 'مستخدم') if row else 'مستخدم'
    sub_until  = row['subscription_until'] if row else None

    if subscribed and sub_until:
        try:
            dt      = datetime.fromisoformat(sub_until)
            days    = (dt - datetime.utcnow()).days
            sub_txt = f"✅ مشترك — ينتهي بعد <b>{days}</b> يوم"
        except Exception:
            sub_txt = "✅ مشترك"
    else:
        sub_txt = "❌ غير مشترك — فعّل كودك أولاً"

    txt = (
        f"<b>{BOT_NAME}</b>\n\n"
        f"مرحبًا <b>{name}</b> 👋\n"
        f"الحالة: {sub_txt}\n\n"
        "اختر بوابة الإعلان من الأزرار أدناه 👇"
    )
    kb = menu_for(user_id)
    if isinstance(msg_or_call, CallbackQuery):
        await msg_or_call.message.edit_text(txt, reply_markup=kb)
    else:
        await msg_or_call.answer(txt, reply_markup=kb)


# ──────────────────────────────────────────────
#  /start
# ──────────────────────────────────────────────

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    db.add_user(
        message.from_user.id,
        message.from_user.username or '',
        message.from_user.first_name or ''
    )
    await send_home(message, message.from_user.id)


@dp.callback_query(F.data == 'home')
async def home(call: CallbackQuery, state: FSMContext):
    await state.clear()
    _sessions.pop(call.from_user.id, None)
    await send_home(call, call.from_user.id)
    await call.answer()


# ──────────────────────────────────────────────
#  إحصائيات المستخدم
# ──────────────────────────────────────────────

@dp.callback_query(F.data == 'my_stats')
async def my_stats(call: CallbackQuery):
    row = db.get_user(call.from_user.id)
    name = (row['custom_name'] or row['first_name'] or 'مستخدم') if row else 'مستخدم'
    sub = row['subscription_until'] if row else None
    joined = row['joined_at'] if row else '—'
    sub_text = sub if sub else '❌ بدون اشتراك'
    await call.message.edit_text(
        f"📊 <b>إحصائياتك</b>\n\n"
        f"👤 الاسم: <b>{name}</b>\n"
        f"🆔 ID: <code>{call.from_user.id}</code>\n"
        f"📅 انضممت: {joined}\n"
        f"⏳ الاشتراك حتى: {sub_text}",
        reply_markup=back_home()
    )
    await call.answer()


# ──────────────────────────────────────────────
#  Redeem
# ──────────────────────────────────────────────

@dp.callback_query(F.data == 'redeem')
async def redeem_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(UserFlow.waiting_redeem)
    await call.message.edit_text(
        "🎟️ <b>تفعيل كود Redeem</b>\n\nأرسل كود التفعيل الآن:",
        reply_markup=back_home()
    )
    await call.answer()


@dp.message(UserFlow.waiting_redeem)
async def redeem_code(message: Message, state: FSMContext):
    code  = message.text.strip()
    hours = db.use_code(code, message.from_user.id)
    if not hours:
        await message.answer(
            "❌ <b>الكود غير صالح أو تم استخدامه من قبل.</b>\n\nتأكد من الكود وحاول مرة أخرى.",
            reply_markup=back_home()
        )
        return
    until = db.set_subscription_hours(message.from_user.id, hours)
    await state.clear()
    await message.answer(
        f"🎉 <b>تم تفعيل الاشتراك بنجاح!</b>\n\n"
        f"⏳ صالح حتى (UTC):\n<code>{until.isoformat(timespec='seconds')}</code>\n\n"
        f"مدة الاشتراك: <b>{hours} ساعة</b>",
        reply_markup=menu_for(message.from_user.id)
    )


# ──────────────────────────────────────────────
#  البوابات
# ──────────────────────────────────────────────

@dp.callback_query(F.data.startswith('gate:'))
async def enter_gate(call: CallbackQuery, state: FSMContext):
    row = db.get_user(call.from_user.id)
    if not is_subscribed(row):
        await call.answer('❌ اشترك أولاً بكود Redeem.', show_alert=True)
        return
    db.inc('requests')
    gate_id = call.data.split(':', 1)[1]
    gate    = GATES.get(gate_id)
    if not gate:
        await call.answer('البوابة غير موجودة.', show_alert=True)
        return
    await state.clear()
    _new_session(call.from_user.id)
    await gate.enter(call, state, {'gate_names': GATE_NAMES})
    # active_msg_id يُستخدم للتحقق من proxy buttons فقط
    await state.update_data(active_msg_id=call.message.message_id)
    await call.answer()


# ──────────────────────────────────────────────
#  Proxy handlers
# ──────────────────────────────────────────────

@dp.callback_query(F.data == 'proxy:auto', AdGateStates.waiting_proxy)
async def proxy_auto(call: CallbackQuery, state: FSMContext):
    if not await _check_session(call, state): return
    data = await state.get_data()
    gate = GATES.get(data.get('gate_type'))
    if gate:
        await gate.handle_proxy_auto(call, state, proxy_manager.choose())
    await call.answer()


@dp.callback_query(F.data == 'proxy:skip', AdGateStates.waiting_proxy)
async def proxy_skip(call: CallbackQuery, state: FSMContext):
    if not await _check_session(call, state): return
    data = await state.get_data()
    gate = GATES.get(data.get('gate_type'))
    if gate:
        await gate.handle_proxy_skip(call, state)
    await call.answer()


@dp.callback_query(F.data == 'proxy:custom', AdGateStates.waiting_proxy)
async def proxy_custom_prompt(call: CallbackQuery, state: FSMContext):
    if not await _check_session(call, state): return
    await call.message.edit_text(
        '✏️ <b>أدخل البروكسي يدوياً:</b>\n\n'
        'الصيغة:\n'
        '<code>IP:PORT</code>\n'
        '<code>user:pass@IP:PORT</code>',
        reply_markup=back_home()
    )
    await call.answer()


@dp.callback_query(F.data == 'proxy:back', AdGateStates.waiting_cookies)
async def proxy_back(call: CallbackQuery, state: FSMContext):
    if not await _check_session(call, state): return
    data = await state.get_data()
    gate = GATES.get(data.get('gate_type'))
    if gate:
        await gate.handle_proxy_back(call, state)
    await call.answer()


@dp.message(AdGateStates.waiting_proxy)
async def proxy_custom_input(message: Message, state: FSMContext):
    data = await state.get_data()
    gate = GATES.get(data.get('gate_type'))
    if gate:
        await gate.handle_proxy_custom(message, state)


# ──────────────────────────────────────────────
#  Gate step handlers (shared dispatcher)
# ──────────────────────────────────────────────

async def _dispatch(state, attr, *args):
    data = await state.get_data()
    gate = GATES.get(data.get('gate_type'))
    if gate and hasattr(gate, attr):
        await getattr(gate, attr)(*args, state)


@dp.message(AdGateStates.waiting_cookies)
async def cookies_input(m: Message, state: FSMContext):
    await _dispatch(state, 'handle_cookies', m)

@dp.message(AdGateStates.waiting_ad_account_id)
async def ad_account_id_input(m: Message, state: FSMContext):
    await _dispatch(state, 'handle_ad_account_id', m)

@dp.message(AdGateStates.waiting_page_id)
async def page_id_input(m: Message, state: FSMContext):
    await _dispatch(state, 'handle_page_id', m)

@dp.message(AdGateStates.waiting_post_link)
async def post_link_input(m: Message, state: FSMContext):
    await _dispatch(state, 'handle_post_link', m)

@dp.message(AdGateStates.waiting_ad_set_id)
async def ad_set_id_input(m: Message, state: FSMContext):
    await _dispatch(state, 'handle_ad_set_id', m)

@dp.message(AdGateStates.waiting_post_id)
async def post_id_input(m: Message, state: FSMContext):
    await _dispatch(state, 'handle_ad_code', m)

@dp.message(AdGateStates.waiting_image)
async def image_input(m: Message, state: FSMContext):
    await _dispatch(state, 'handle_image', m)

@dp.message(AdGateStates.waiting_caption)
async def caption_input(m: Message, state: FSMContext):
    await _dispatch(state, 'handle_caption', m)

@dp.message(AdGateStates.waiting_audience_id)
async def audience_id_input(m: Message, state: FSMContext):
    await _dispatch(state, 'handle_audience_id', m)

@dp.message(AdGateStates.waiting_daily_budget)
async def budget_input(m: Message, state: FSMContext):
    await _dispatch(state, 'handle_daily_budget', m)

@dp.message(AdGateStates.waiting_days)
async def days_input(m: Message, state: FSMContext):
    await _dispatch(state, 'handle_days', m)


@dp.callback_query(F.data == 'image:skip')
async def image_skip(call: CallbackQuery, state: FSMContext):
    if not await _check_session(call, state):
        return
    await _dispatch(state, 'handle_image_skip', call)
    await call.answer()

@dp.callback_query(F.data == 'image:change')
async def image_change(call: CallbackQuery, state: FSMContext):
    if not await _check_session(call, state):
        return
    await _dispatch(state, 'handle_image_back', call)
    await call.answer()

@dp.callback_query(F.data.startswith('objective:'), AdGateStates.waiting_objective)
async def objective_select(call: CallbackQuery, state: FSMContext):
    if not await _check_session(call, state):
        return
    await _dispatch(state, 'handle_objective', call)
    await call.answer()

@dp.callback_query(F.data.in_(['confirm:yes', 'confirm:no']), AdGateStates.waiting_confirm)
async def confirm_action(call: CallbackQuery, state: FSMContext):
    if not await _check_session(call, state):
        return
    await _dispatch(state, 'handle_confirm', call)
    await call.answer()

@dp.callback_query(
    F.data.in_(['activate:run', 'activate:run_pause', 'activate:back']),
    AdGateStates.waiting_activate
)
async def activate_action(call: CallbackQuery, state: FSMContext):
    if not await _check_session(call, state):
        return
    await _dispatch(state, 'handle_activate', call)
    await call.answer()

# ── اختيار البوست من القائمة ──

@dp.callback_query(F.data.startswith('post:'), AdGateStates.waiting_post_select)
async def post_select(call: CallbackQuery, state: FSMContext):
    if not await _check_session(call, state):
        return
    data = await state.get_data()
    gate = GATES.get(data.get('gate_type'))
    if gate and hasattr(gate, 'handle_post_select'):
        await gate.handle_post_select(call, state)
    else:
        await call.answer()

# ── رابط بوست يدوي عند الإدخال اليدوي من post_select ──

@dp.message(AdGateStates.waiting_post_select)
async def post_select_manual_msg(message: Message, state: FSMContext):
    data = await state.get_data()
    gate = GATES.get(data.get('gate_type'))
    if gate and hasattr(gate, 'handle_post_link'):
        await gate.handle_post_link(message, state)


# ══════════════════════════════════════════════
#  قائمة الأدوات
# ══════════════════════════════════════════════

@dp.callback_query(F.data == 'tools:menu')
async def tools_menu_cb(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text(
        "🛠️ <b>الأدوات</b>\n\n"
        "اختر القسم:",
        reply_markup=tools_menu()
    )
    await call.answer()


@dp.callback_query(F.data == 'tools:ads')
async def tools_ads_cb(call: CallbackQuery):
    row = db.get_user(call.from_user.id)
    sub = is_subscribed(row)
    await call.message.edit_text(
        "📢 <b>أدوات الإعلانات</b>\n\n"
        + ("اختر نوع الإعلان:" if sub else "❌ هذه الأدوات للمشتركين فقط.\nفعّل كودك أولاً."),
        reply_markup=ad_tools_menu(GATE_NAMES, sub)
    )
    await call.answer()


@dp.callback_query(F.data == 'tools:link')
async def tools_link_cb(call: CallbackQuery):
    row = db.get_user(call.from_user.id)
    sub = is_subscribed(row)
    await call.message.edit_text(
        "🔗 <b>أدوات ربط و تسميع</b>\n\n"
        + ("اختر الأداة:" if sub else "❌ هذه الأدوات للمشتركين فقط. فعّل كودك أولاً."),
        reply_markup=link_tools_menu(sub)
    )
    await call.answer()


# ══════════════════════════════════════════════
#  أداة تسميع البطاقات BM
# ══════════════════════════════════════════════

@dp.callback_query(F.data == 'tool:bm_cards')
async def bm_cards_start(call: CallbackQuery, state: FSMContext):
    row = db.get_user(call.from_user.id)
    if not is_subscribed(row):
        await call.answer('❌ هذه الأداة للمشتركين فقط. فعّل كود Redeem أولاً.', show_alert=True)
        return
    await state.clear()
    tok = _new_session(call.from_user.id)
    await state.set_state(BMToolStates.waiting_proxy)
    await state.update_data(_session_tok=tok, active_msg_id=call.message.message_id)
    await call.message.edit_text(
        "💳 <b>تسميع البطاقات BM</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📋 <b>خطوات العمل:</b>\n"
        "1️⃣ البروكسي\n"
        "2️⃣ الكوكيز (فيسبوك/انستاجرام BM)\n"
        "3️⃣ BM ID (Business Manager)\n"
        "4️⃣ Ad Account ID\n"
        "5️⃣ اختيار البطاقات\n"
        "6️⃣ الفاصل الزمني بالثواني\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔽 <b>الخطوة 1:</b> اختر البروكسي",
        reply_markup=bm_proxy_keyboard()
    )
    await call.answer()


@dp.callback_query(F.data == 'bm:proxy:auto', BMToolStates.waiting_proxy)
async def bm_proxy_auto(call: CallbackQuery, state: FSMContext):
    if not await _check_session(call, state):
        return
    proxy = proxy_manager.choose()
    if not proxy:
        await call.answer("⚠️ لا توجد بروكسيات متاحة في القائمة", show_alert=True)
        return
    await state.update_data(bm_proxy=proxy)
    await state.set_state(BMToolStates.waiting_cookies)
    await call.message.edit_text(
        "✅ <b>البروكسي:</b> تم اختيار بروكسي من البوت تلقائياً\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔽 <b>الخطوة 2:</b> أرسل كوكيز Business Manager\n\n"
        "📌 افتح <b>business.facebook.com</b> أو <b>instagram.com</b> في المتصفح\n"
        "وانسخ الكوكيز من Developer Tools → Network → Cookie",
        reply_markup=back_home()
    )
    await call.answer()


@dp.callback_query(F.data == 'bm:proxy:skip', BMToolStates.waiting_proxy)
async def bm_proxy_skip(call: CallbackQuery, state: FSMContext):
    if not await _check_session(call, state):
        return
    await state.update_data(bm_proxy=None)
    await state.set_state(BMToolStates.waiting_cookies)
    await call.message.edit_text(
        "✅ <b>تم تخطي البروكسي</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔽 <b>الخطوة 2:</b> أرسل كوكيز Business Manager",
        reply_markup=back_home()
    )
    await call.answer()


@dp.callback_query(F.data == 'bm:proxy:custom', BMToolStates.waiting_proxy)
async def bm_proxy_custom_prompt(call: CallbackQuery, state: FSMContext):
    if not await _check_session(call, state):
        return
    await state.set_state(BMToolStates.waiting_proxy)
    await call.message.edit_text(
        "✏️ <b>أدخل البروكسي يدوياً:</b>\n\n"
        "الصيغة:\n"
        "<code>IP:PORT</code>\n"
        "<code>user:pass@IP:PORT</code>",
        reply_markup=back_home()
    )
    await call.answer()


@dp.message(BMToolStates.waiting_proxy)
async def bm_proxy_custom_input(message: Message, state: FSMContext):
    txt = message.text.strip()
    if txt.lower() == 'skip' or not txt:
        proxy = None
    else:
        proxy = txt
        # استيراد دالة التحليل من الخدمة
        from services.bm_card_service import _parse_proxy
        
        # تحقق من صيغة البروكسي
        parsed = _parse_proxy(proxy)
        if not parsed:
            await message.answer(
                "❌ <b>صيغة البروكسي خاطئة</b>\n\n"
                "صيغ مقبولة:\n"
                "📌 <code>1.2.3.4:8080</code>\n"
                "📌 <code>user:pass@1.2.3.4:8080</code>\n"
                "📌 <code>proxy.host.com:80:username:password</code>\n"
                "📌 <code>proxy.host.com:80</code>",
                reply_markup=back_home()
            )
            return
    await state.update_data(bm_proxy=proxy)
    await state.set_state(BMToolStates.waiting_cookies)
    await message.answer(
        "✅ <b>البروكسي:</b> تم حفظ البروكسي\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔽 <b>الخطوة 2:</b> أرسل كوكيز Business Manager",
        reply_markup=back_home()
    )


@dp.message(BMToolStates.waiting_cookies)
async def bm_cookies_input(message: Message, state: FSMContext):
    cookies = message.text.strip()
    if len(cookies) < 20 or '=' not in cookies:
        await message.answer(
            "❌ <b>الكوكيز غير صالحة</b>\n\n"
            "تأكد من نسخها بالكامل من المتصفح.",
            reply_markup=back_home()
        )
        return
    await state.update_data(bm_cookies=cookies)
    await state.set_state(BMToolStates.waiting_bm_id)
    await message.answer(
        "✅ <b>تم حفظ الكوكيز</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔽 <b>الخطوة 3:</b> أرسل Business Manager ID\n\n"
        "📌 تجده في رابط: business.facebook.com/overview\n"
        "مثال: <code>123456789012345</code>",
        reply_markup=back_home()
    )


@dp.message(BMToolStates.waiting_bm_id)
async def bm_id_input(message: Message, state: FSMContext):
    bm_id = message.text.strip().replace(' ', '')
    if not bm_id.isdigit():
        await message.answer(
            "❌ <b>BM ID يجب أن يكون أرقاماً فقط</b>\n\nمثال: <code>123456789012345</code>",
            reply_markup=back_home()
        )
        return
    await state.update_data(bm_id=bm_id)
    await state.set_state(BMToolStates.waiting_ad_id)
    await message.answer(
        "✅ <b>تم حفظ BM ID</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔽 <b>الخطوة 4:</b> أرسل Ad Account ID\n\n"
        "📌 بدون <code>act_</code> — فقط الأرقام\n"
        "مثال: <code>987654321098765</code>",
        reply_markup=back_home()
    )


@dp.message(BMToolStates.waiting_ad_id)
async def bm_ad_id_input(message: Message, state: FSMContext):
    ad_id = message.text.strip().replace('act_', '').replace(' ', '')
    if not ad_id.isdigit():
        await message.answer(
            "❌ <b>Ad Account ID يجب أن يكون أرقاماً فقط</b>\n\nمثال: <code>987654321098765</code>",
            reply_markup=back_home()
        )
        return
    await state.update_data(bm_ad_id=ad_id)

    data = await state.get_data()
    wait_msg = await message.answer("⏳ <b>جارٍ جلب البطاقات من BM...</b>")

    result = await get_bm_cards(
        cookies=data['bm_cookies'],
        bm_id=data['bm_id'],
        ad_id=ad_id,
        proxy=data.get('bm_proxy'),
    )

    if not result['success']:
        error_msg = result['error']
        suggestions = ""
        
        # أضف اقتراحات بناءً على نوع الخطأ
        if 'منتهية' in error_msg or 'login' in error_msg.lower():
            suggestions = "\n\n💡 <b>الحل:</b>\n- تأكد من أن الجلسة لا تزال نشطة\n- حاول تحديث الكوكيز من جديد\n- تسجيل الدخول في متصفح جديد وإعادة نسخ الكوكيز"
        elif 'dtsg' in error_msg.lower():
            suggestions = "\n\n💡 <b>الحل:</b>\n- تأكد من أن الكوكيز صحيحة\n- حاول نسخ الكوكيز مرة أخرى من متصفح جديد\n- تأكد من أن لديك صلاحيات في Business Manager"
        elif 'شبكة' in error_msg or 'network' in error_msg.lower():
            suggestions = "\n\n💡 <b>الحل:</b>\n- تحقق من اتصال الإنترنت\n- تحقق من صحة البروكسي إن وجد\n- حاول بدون بروكسي"
        
        await wait_msg.edit_text(
            f"❌ <b>فشل جلب البطاقات</b>\n\n"
            f"<b>السبب:</b> {error_msg}"
            f"{suggestions}",
            reply_markup=back_home()
        )
        return

    cards = result['cards']
    await state.update_data(bm_cards=cards, bm_selected=[])
    await state.set_state(BMToolStates.waiting_card_sel)

    card_list = "\n".join(
        f"  {i+1}. {c.get('card_association_name','Card')} •••• {c.get('last_four_digits','****')}"
        for i, c in enumerate(cards)
    )
    await wait_msg.edit_text(
        f"✅ <b>تم جلب {len(cards)} بطاقة</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{card_list}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔽 <b>الخطوة 5:</b> اختر البطاقات المراد تسميعها",
        reply_markup=bm_card_select_keyboard(cards, [])
    )


@dp.callback_query(F.data.startswith('bm:card:'), BMToolStates.waiting_card_sel)
async def bm_card_toggle(call: CallbackQuery, state: FSMContext):
    if not await _check_session(call, state):
        return
    idx = int(call.data.split(':')[2])
    data = await state.get_data()
    cards    = data.get('bm_cards', [])
    selected = list(data.get('bm_selected', []))

    if idx >= len(cards):
        await call.answer()
        return

    cid = cards[idx].get('credential_id', '')
    if cid in selected:
        selected.remove(cid)
    else:
        selected.append(cid)

    await state.update_data(bm_selected=selected)
    await call.message.edit_reply_markup(
        reply_markup=bm_card_select_keyboard(cards, selected)
    )
    await call.answer(f"{'✅ تم التحديد' if cid in selected else '⬜ تم الإلغاء'}")


@dp.callback_query(F.data == 'bm:select_all', BMToolStates.waiting_card_sel)
async def bm_select_all(call: CallbackQuery, state: FSMContext):
    if not await _check_session(call, state):
        return
    data = await state.get_data()
    cards    = data.get('bm_cards', [])
    selected = [c.get('credential_id', '') for c in cards]
    await state.update_data(bm_selected=selected)
    await call.message.edit_reply_markup(
        reply_markup=bm_card_select_keyboard(cards, selected)
    )
    await call.answer(f"☑️ تم تحديد الكل ({len(cards)} بطاقة)")


@dp.callback_query(F.data == 'bm:confirm_cards', BMToolStates.waiting_card_sel)
async def bm_confirm_cards(call: CallbackQuery, state: FSMContext):
    if not await _check_session(call, state):
        return
    data = await state.get_data()
    selected = data.get('bm_selected', [])
    if not selected:
        await call.answer("⚠️ لم تختر أي بطاقة!", show_alert=True)
        return
    await state.set_state(BMToolStates.waiting_interval)
    await call.message.edit_text(
        f"✅ <b>تم تحديد {len(selected)} بطاقة</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔽 <b>الخطوة 6:</b> أدخل الفاصل الزمني بين كل بطاقة\n\n"
        "📌 أرسل رقماً بالثواني (مثال: <code>3</code>)\n"
        "أو أرسل <code>0</code> بدون فاصل",
        reply_markup=back_home()
    )
    await call.answer()


@dp.message(BMToolStates.waiting_interval)
async def bm_interval_input(message: Message, state: FSMContext):
    try:
        interval = int(message.text.strip())
        if interval < 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "❌ أدخل رقم صحيح بالثواني (مثال: <code>3</code>)",
            reply_markup=back_home()
        )
        return

    data = await state.get_data()
    cards    = data.get('bm_cards', [])
    selected = data.get('bm_selected', [])
    bm_id    = data.get('bm_id', '')
    ad_id    = data.get('bm_ad_id', '')
    cookies  = data.get('bm_cookies', '')
    proxy    = data.get('bm_proxy')

    await state.clear()

    wait_msg = await message.answer(
        f"⏳ <b>جارٍ تسميع {len(selected)} بطاقة...</b>\n\n"
        f"الفاصل الزمني: <b>{interval} ثانية</b>"
    )

    result = await warm_bm_cards(
        cookies=cookies,
        bm_id=bm_id,
        ad_id=ad_id,
        cards=cards,
        card_ids=selected,
        interval_secs=interval,
        proxy=proxy,
    )

    if not result.get('success'):
        await wait_msg.edit_text(
            f"❌ <b>فشل التسميع</b>\n\nالسبب: {result.get('error', 'خطأ غير معروف')}",
            reply_markup=back_home()
        )
        return

    lines = [
        f"📊 <b>نتيجة التسميع</b>\n",
        f"━━━━━━━━━━━━━━━━━━━━",
    ]
    for r in result['results']:
        icon = "✅" if r['success'] else "❌"
        line = f"{icon} {r['label']}"
        if not r['success']:
            line += f"\n    ↳ {r['error']}"
        lines.append(line)

    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━")
    lines.append(
        f"✅ نجح: <b>{result['success_count']}</b>   "
        f"❌ فشل: <b>{result['fail_count']}</b>"
    )

    await wait_msg.edit_text(
        "\n".join(lines),
        reply_markup=back_home()
    )


# ── ربط بايبال (placeholder) ──

@dp.callback_query(F.data == 'tool:paypal')
async def tool_paypal(call: CallbackQuery):
    row = db.get_user(call.from_user.id)
    if not is_subscribed(row):
        await call.answer('❌ هذه الأداة للمشتركين فقط. فعّل كود Redeem أولاً.', show_alert=True)
        return
    await call.message.edit_text(
        "🔗 <b>ربط بايبال</b>\n\n"
        "⏳ هذه الأداة قيد التطوير وستكون متاحة قريباً.",
        reply_markup=link_tools_menu(True)
    )
    await call.answer()


# ──────────────────────────────────────────────
#  لوحة تحكم الأدمن — /beshoy (مخفي)
# ──────────────────────────────────────────────

@dp.message(Command(ADMIN_CMD))
async def admin_login(message: Message, state: FSMContext):
    await state.set_state(AdminFlow.waiting_password)
    await message.answer(
        "🔐 <b>لوحة تحكم خاصة</b>\n\nأدخل كلمة المرور:"
    )


@dp.message(AdminFlow.waiting_password)
async def admin_password(message: Message, state: FSMContext):
    if message.text.strip() != ADMIN_PASS:
        await message.answer("❌ كلمة المرور خاطئة.")
        return
    await state.clear()
    await message.answer(
        f"✅ <b>مرحباً بك في لوحة التحكم</b>\n\n"
        f"📊 {db.counts()['users']} مشترك نشط | "
        f"📈 {db.counts()['requests']} طلب",
        reply_markup=admin_panel()
    )


# ── رجوع للوحة ──

@dp.callback_query(F.data == 'admin:panel')
async def admin_panel_cb(call: CallbackQuery):
    c = db.counts()
    await call.message.edit_text(
        f"⚙️ <b>لوحة التحكم</b>\n\n"
        f"👥 مشتركون: <b>{c['users']}</b> | 📈 طلبات: <b>{c['requests']}</b>",
        reply_markup=admin_panel()
    )
    await call.answer()


# ── إحصائيات ──

@dp.callback_query(F.data == 'admin:stats')
async def admin_stats(call: CallbackQuery):
    c = db.counts()
    proxies = len(proxy_manager.all())
    await call.message.edit_text(
        f"📊 <b>إحصائيات البوت</b>\n\n"
        f"┌ 👥 المشتركون النشطون:  <b>{c['users']}</b>\n"
        f"├ 🗑️  المحذوفون:          <b>{c['removed']}</b>\n"
        f"├ 📈 إجمالي الطلبات:     <b>{c['requests']}</b>\n"
        f"├ 🎟️  أكواد Redeem:       <b>{c['codes']}</b>\n"
        f"├ 🆕 أكواد غير مستخدمة: <b>{c['unused']}</b>\n"
        f"└ 🌐 البروكسيات:         <b>{proxies}</b>",
        reply_markup=back_admin()
    )
    await call.answer()


# ── توليد كود ──

@dp.callback_query(F.data == 'admin:gen_code')
async def admin_gen_code(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminFlow.waiting_code_duration)
    await call.message.edit_text(
        "🎟️ <b>توليد كود Redeem</b>\n\n"
        "أدخل مدة الاشتراك بالساعات:\n"
        "<code>24</code> = يوم\n"
        "<code>168</code> = أسبوع\n"
        "<code>720</code> = شهر",
        reply_markup=back_admin()
    )
    await call.answer()


@dp.message(AdminFlow.waiting_code_duration)
async def admin_code_duration(message: Message, state: FSMContext):
    try:
        hours = int(message.text.strip())
        assert hours > 0
    except Exception:
        await message.answer("❌ أدخل رقم ساعات صحيح.")
        return
    code = generate_code()
    db.create_code(code, hours, f'{hours}h')
    await state.clear()
    days = hours // 24
    period = f"{days} يوم" if days >= 1 else f"{hours} ساعة"
    await message.answer(
        f"🎟️ <b>تم توليد كود جديد</b>\n\n"
        f"الكود:\n<code>{code}</code>\n\n"
        f"المدة: <b>{period}</b>\n\n"
        f"<i>اضغط على الكود لنسخه</i>",
        reply_markup=admin_panel()
    )


# ── عرض المشتركين ──

@dp.callback_query(F.data == 'admin:list_users')
async def admin_list_users(call: CallbackQuery):
    users = [dict(r) for r in db.all_active_users()]
    if not users:
        await call.message.edit_text("لا يوجد مشتركون.", reply_markup=back_admin())
        await call.answer()
        return
    await call.message.edit_text(
        f"👥 <b>المشتركون ({len(users)})</b>",
        reply_markup=admin_users_keyboard(users)
    )
    await call.answer()


@dp.callback_query(F.data.startswith('admin:user_info:'))
async def admin_user_info(call: CallbackQuery):
    uid = int(call.data.split(':')[2])
    row = db.get_user(uid)
    if not row:
        await call.answer("المستخدم غير موجود", show_alert=True)
        return
    name    = row['custom_name'] or row['first_name'] or f'user_{uid}'
    sub     = row['subscription_until'] or '—'
    active  = '✅ نشط' if is_subscribed(row) else '❌ منتهي'
    await call.message.edit_text(
        f"👤 <b>{name}</b>\n\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📛 يوزرنيم: @{row['username'] or '—'}\n"
        f"📅 انضم: {row['joined_at']}\n"
        f"⏳ اشتراك حتى: {sub}\n"
        f"الحالة: {active}",
        reply_markup=user_action_keyboard(uid)
    )
    await call.answer()


@dp.callback_query(F.data.startswith('admin:extend:'))
async def admin_extend_user(call: CallbackQuery, state: FSMContext):
    uid = int(call.data.split(':')[2])
    await state.update_data(extend_uid=uid)
    await state.set_state(AdminFlow.waiting_code_duration)
    await call.message.edit_text(
        f"⏳ تمديد اشتراك <code>{uid}</code>\n\nأدخل عدد الساعات:",
        reply_markup=back_admin()
    )
    await call.answer()


@dp.callback_query(F.data.startswith('admin:del:'))
async def admin_del_user(call: CallbackQuery):
    uid = int(call.data.split(':')[2])
    db.remove_user(uid)
    await call.message.edit_text("✅ تم حذف المستخدم.", reply_markup=back_admin())
    await call.answer()


# ── إضافة/تمديد مشترك ──

@dp.callback_query(F.data == 'admin:set_user')
async def admin_set_user(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminFlow.waiting_user_manage)
    await call.message.edit_text(
        "👤 <b>إضافة / تمديد مشترك</b>\n\n"
        "أرسل بالصيغة:\n"
        "<code>user_id | الاسم | عدد الساعات</code>\n\n"
        "مثال:\n<code>123456789 | VIP | 720</code>",
        reply_markup=back_admin()
    )
    await call.answer()


@dp.message(AdminFlow.waiting_user_manage)
async def admin_manage_user(message: Message, state: FSMContext):
    parts = [p.strip() for p in message.text.split('|')]
    if len(parts) != 3:
        await message.answer("❌ الصيغة خاطئة.\nاستخدم: user_id | الاسم | عدد الساعات")
        return
    try:
        user_id = int(parts[0]); name = parts[1]; hours = int(parts[2])
    except Exception:
        await message.answer("❌ user_id والساعات يجب أن تكون أرقام.")
        return
    db.add_user(user_id)
    if name:
        db.set_custom_name(user_id, name)
    until = db.set_subscription_hours(user_id, hours)
    await state.clear()
    days = hours // 24
    await message.answer(
        f"✅ <b>تم تعيين المشترك</b>\n\n"
        f"👤 {name} (<code>{user_id}</code>)\n"
        f"⏳ صالح حتى: <code>{until.isoformat(timespec='seconds')}</code>\n"
        f"المدة: <b>{days} يوم</b>",
        reply_markup=admin_panel()
    )


# ── حذف مشترك ──

@dp.callback_query(F.data == 'admin:remove_user')
async def admin_remove_user_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminFlow.waiting_remove_user)
    await call.message.edit_text(
        "🗑️ <b>حذف مشترك</b>\n\nأرسل user_id:",
        reply_markup=back_admin()
    )
    await call.answer()


@dp.message(AdminFlow.waiting_remove_user)
async def admin_remove_user(message: Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
    except Exception:
        await message.answer("❌ أدخل user_id صحيح (رقم).")
        return
    db.remove_user(user_id)
    await state.clear()
    await message.answer(f"✅ تم حذف المستخدم <code>{user_id}</code>.", reply_markup=admin_panel())


# ── عرض الأكواد ──

@dp.callback_query(F.data == 'admin:list_codes')
async def admin_list_codes(call: CallbackQuery):
    rows = db.conn.execute(
        'SELECT * FROM redeem_codes ORDER BY created_at DESC LIMIT 10'
    ).fetchall()
    if not rows:
        await call.message.edit_text("لا توجد أكواد.", reply_markup=back_admin())
        await call.answer()
        return
    lines = ["🎟️ <b>آخر الأكواد:</b>\n"]
    for r in rows:
        status = "✅ مستخدم" if r['used_by'] else "🆕 متاح"
        lines.append(f"<code>{r['code']}</code> — {r['duration_hours']}h — {status}")
    await call.message.edit_text("\n".join(lines), reply_markup=back_admin())
    await call.answer()


# ── البروكسيات ──

@dp.callback_query(F.data == 'admin:add_proxies')
async def admin_add_proxies_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminFlow.waiting_proxies)
    count = len(proxy_manager.all())
    await call.message.edit_text(
        f"🌐 <b>إضافة بروكسيات</b>\n\n"
        f"البروكسيات الحالية: <b>{count}</b>\n\n"
        "أرسل البروكسيات — كل بروكسي في سطر:\n"
        "<code>1.2.3.4:8080\n"
        "user:pass@5.6.7.8:3128</code>",
        reply_markup=back_admin()
    )
    await call.answer()


@dp.message(AdminFlow.waiting_proxies)
async def admin_add_proxies(message: Message, state: FSMContext):
    count = proxy_manager.add_many(message.text)
    total = len(proxy_manager.all())
    await state.clear()
    await message.answer(
        f"✅ تمت إضافة <b>{count}</b> بروكسي.\n"
        f"الإجمالي الآن: <b>{total}</b>",
        reply_markup=admin_panel()
    )


@dp.callback_query(F.data == 'admin:list_proxies')
async def admin_list_proxies(call: CallbackQuery):
    proxies = proxy_manager.all()
    if not proxies:
        await call.message.edit_text("لا توجد بروكسيات.", reply_markup=back_admin())
        await call.answer()
        return
    sample = proxies[:15]
    more   = len(proxies) - 15
    lines  = [f"🌐 <b>البروكسيات ({len(proxies)}):</b>\n"]
    lines += [f"<code>{_mask_proxy(p)}</code>" for p in sample]
    if more > 0:
        lines.append(f"\n<i>...و {more} بروكسي أخرى</i>")
    await call.message.edit_text("\n".join(lines), reply_markup=back_admin())
    await call.answer()


# ── رسالة جماعية ──

@dp.callback_query(F.data == 'admin:broadcast')
async def admin_broadcast_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminFlow.waiting_broadcast)
    count = len(list(db.all_active_users()))
    await call.message.edit_text(
        f"📢 <b>رسالة جماعية</b>\n\n"
        f"سيتم الإرسال إلى <b>{count}</b> مستخدم.\n\n"
        "اكتب الرسالة (تدعم HTML):",
        reply_markup=back_admin()
    )
    await call.answer()


@dp.message(AdminFlow.waiting_broadcast)
async def admin_broadcast(message: Message, state: FSMContext):
    sent = failed = 0
    for u in db.all_active_users():
        try:
            await bot.send_message(u['user_id'], message.text, parse_mode='HTML')
            sent += 1
        except Exception:
            failed += 1
    await state.clear()
    await message.answer(
        f"📢 <b>تم الإرسال</b>\n\n"
        f"✅ نجح: <b>{sent}</b>\n"
        f"❌ فشل: <b>{failed}</b>",
        reply_markup=admin_panel()
    )


# ── الإعدادات ──

@dp.callback_query(F.data == 'admin:settings')
async def admin_settings(call: CallbackQuery):
    dashboard_port = os.environ.get('PORT', '5000')
    await call.message.edit_text(
        f"⚙️ <b>إعدادات البوت</b>\n\n"
        f"🤖 اسم البوت: <b>{BOT_NAME}</b>\n"
        f"🔑 الأمر السري: <code>/{ADMIN_CMD}</code>\n"
        f"🌐 لوحة الويب: <code>port {dashboard_port}</code>\n"
        f"🔗 رابط الدعم: {SUPPORT_URL}",
        reply_markup=settings_keyboard()
    )
    await call.answer()


# ──────────────────────────────────────────────
#  Main entry
# ──────────────────────────────────────────────

async def main():
    if not TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN غير موجود!")
        sys.exit(1)

    Path('data').mkdir(exist_ok=True)
    Path('data/temp').mkdir(exist_ok=True)
    Path('proxies.txt').touch(exist_ok=True)

    print(f"🚀 {BOT_NAME} يعمل الآن...")
    print(f"🔑 الأمر السري: /{ADMIN_CMD}  |  كلمة المرور: {ADMIN_PASS}")
    print(f"📌 البوابات: {', '.join(GATE_NAMES.keys())}")

    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == '__main__':
    asyncio.run(main())
