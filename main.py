import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext

from database import DB, is_subscribed
from keyboards import (
    main_menu, admin_panel, back_home, back_admin,
    proxy_selection_keyboard, objective_selection_keyboard,
    confirm_keyboard, activate_or_back_keyboard, image_received_keyboard,
    admin_users_keyboard, user_action_keyboard, settings_keyboard,
    post_selection_keyboard
)
from states import UserFlow, AdminFlow, AdGateStates
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

# قفل لكل مستخدم (Single-flight): يضمن عدم تشغيل أكتر من فلو إعلان فى نفس الوقت
# المفتاح = user_id, القيمة = gate_id الحالى
_active_jobs: dict[int, str] = {}


def _job_active_for(user_id: int) -> bool:
    return user_id in _active_jobs


def _set_active_job(user_id: int, gate_id: str):
    _active_jobs[user_id] = gate_id


def _clear_active_job(user_id: int):
    _active_jobs.pop(user_id, None)


bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
dp  = Dispatcher(storage=MemoryStorage())


# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────

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
    _clear_active_job(message.from_user.id)
    db.add_user(
        message.from_user.id,
        message.from_user.username or '',
        message.from_user.first_name or ''
    )
    await send_home(message, message.from_user.id)


@dp.callback_query(F.data == 'home')
async def home(call: CallbackQuery, state: FSMContext):
    await state.clear()
    _clear_active_job(call.from_user.id)
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

    user_id = call.from_user.id
    gate_id = call.data.split(':', 1)[1]
    gate    = GATES.get(gate_id)
    if not gate:
        await call.answer('البوابة غير موجودة.', show_alert=True)
        return

    # منع تشغيل أكتر من فلو إعلان فى نفس الوقت لنفس المستخدم
    if _job_active_for(user_id):
        active_gate_id = _active_jobs.get(user_id)
        active_name = GATE_NAMES.get(active_gate_id, active_gate_id)
        await call.answer(
            f"⛔ لديك إعلان قيد التنفيذ بالفعل: {active_name}\n"
            "أنهِ الإعلان الحالى أو ارجع للقائمة الرئيسية أولاً.",
            show_alert=True,
        )
        return

    db.inc('requests')
    _set_active_job(user_id, gate_id)
    await gate.enter(call, state, {'gate_names': GATE_NAMES})
    await call.answer()


# ──────────────────────────────────────────────
#  Proxy handlers
# ──────────────────────────────────────────────

@dp.callback_query(F.data == 'proxy:auto', AdGateStates.waiting_proxy)
async def proxy_auto(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    gate = GATES.get(data.get('gate_type'))
    if gate:
        await gate.handle_proxy_auto(call, state, proxy_manager.choose())
    await call.answer()


@dp.callback_query(F.data == 'proxy:skip', AdGateStates.waiting_proxy)
async def proxy_skip(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    gate = GATES.get(data.get('gate_type'))
    if gate:
        await gate.handle_proxy_skip(call, state)
    await call.answer()


@dp.callback_query(F.data == 'proxy:custom', AdGateStates.waiting_proxy)
async def proxy_custom_prompt(call: CallbackQuery, state: FSMContext):
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
    # تحرير قفل المستخدم تلقائياً لما الـ FSM يكون اتمسح (تم إنهاء/إلغاء الفلو)
    try:
        if (await state.get_state()) is None:
            event = args[0] if args else None
            user_id = getattr(getattr(event, 'from_user', None), 'id', None)
            if user_id is not None:
                _clear_active_job(user_id)
    except Exception:
        pass


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
    await _dispatch(state, 'handle_image_skip', call)
    await call.answer()

@dp.callback_query(F.data == 'image:change')
async def image_change(call: CallbackQuery, state: FSMContext):
    await _dispatch(state, 'handle_image_back', call)
    await call.answer()

@dp.callback_query(F.data.startswith('objective:'), AdGateStates.waiting_objective)
async def objective_select(call: CallbackQuery, state: FSMContext):
    await _dispatch(state, 'handle_objective', call)
    await call.answer()

@dp.callback_query(F.data.in_(['confirm:yes', 'confirm:no']), AdGateStates.waiting_confirm)
async def confirm_action(call: CallbackQuery, state: FSMContext):
    await _dispatch(state, 'handle_confirm', call)
    await call.answer()

@dp.callback_query(
    F.data.in_(['activate:run', 'activate:run_pause', 'activate:back']),
    AdGateStates.waiting_activate
)
async def activate_action(call: CallbackQuery, state: FSMContext):
    await _dispatch(state, 'handle_activate', call)
    await call.answer()

# ── اختيار البوست من القائمة ──

@dp.callback_query(F.data.startswith('post:'), AdGateStates.waiting_post_select)
async def post_select(call: CallbackQuery, state: FSMContext):
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
    lines += [f"<code>{p}</code>" for p in sample]
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
    dashboard_port = os.environ.get('DASHBOARD_PORT', '8080')
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
