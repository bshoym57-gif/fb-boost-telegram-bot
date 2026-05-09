from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from keyboards import (
    back_home, proxy_selection_keyboard, back_to_proxy,
    objective_selection_keyboard, confirm_keyboard,
    activate_or_back_keyboard, post_selection_keyboard
)
from states import AdObjectives, GateConstants, AdGateStates
from gates.base_gate import BaseGate
from services.facebook_api import (
    run_standard_ad, run_standard_ad_then_pause, fetch_page_posts
)
from services.proxy_manager import ProxyManager


def _result_text(result: dict, gate_name: str) -> str:
    paused = result.get('paused', False)
    status = "⏸ <b>تم النشر ثم الإيقاف بنجاح!</b>" if paused else "🟢 <b>الإعلان يعمل الآن!</b>"
    return (
        f"✅ <b>تم تشغيل {gate_name} بنجاح!</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Campaign ID:</b> <code>{result.get('campaign_id', 'N/A')}</code>\n"
        f"📦 <b>Ad Set ID:</b>  <code>{result.get('ad_set_id', 'N/A')}</code>\n"
        f"🎨 <b>Creative ID:</b> <code>{result.get('creative_id', 'N/A')}</code>\n"
        f"📌 <b>Ad ID:</b>      <code>{result.get('ad_id', 'N/A')}</code>\n\n"
        f"{status}"
    )


class StandardAdGate(BaseGate):
    def __init__(self):
        super().__init__('standard_ad', '🟢 إعلان رابط بوست', 'standard_ad')

    # ────── دخول البوابة ──────

    async def enter(self, call: CallbackQuery, state: FSMContext, config: dict):
        await state.update_data(gate_id=self.gate_id, gate_type=self.gate_type, gate_name=self.gate_name)
        await state.set_state(AdGateStates.waiting_proxy)
        await call.message.edit_text(
            f"🚪 <b>{self.gate_name}</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📋 <b>خطوات العمل:</b>\n"
            "1️⃣ البروكسي\n2️⃣ الكوكيز\n3️⃣ Account ID\n"
            "4️⃣ Page ID\n5️⃣ اختيار البوست\n"
            "6️⃣ الهدف + الأوديانس\n7️⃣ الميزانية والأيام\n8️⃣ مراجعة وتشغيل\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔽 <b>الخطوة 1:</b> اختر البروكسي",
            reply_markup=proxy_selection_keyboard()
        )

    # ────── البروكسي ──────

    async def handle_proxy_auto(self, call: CallbackQuery, state: FSMContext, proxy: str = None):
        if not proxy:
            await call.answer("⚠️ لا توجد بروكسيات متاحة في القائمة", show_alert=True)
            return
        await state.update_data(proxy=proxy)
        await state.set_state(AdGateStates.waiting_cookies)
        await call.message.edit_text(
            f"✅ <b>البروكسي:</b> {ProxyManager.mask(proxy)}\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔽 <b>الخطوة 2:</b> أرسل كوكيز فيسبوك\n\n"
            "📌 افتح فيسبوك في المتصفح وانسخ الكوكيز من Developer Tools",
            reply_markup=back_to_proxy()
        )

    async def handle_proxy_skip(self, call: CallbackQuery, state: FSMContext):
        await state.update_data(proxy=None)
        await state.set_state(AdGateStates.waiting_cookies)
        await call.message.edit_text(
            "✅ <b>تم تخطي البروكسي</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔽 <b>الخطوة 2:</b> أرسل كوكيز فيسبوك",
            reply_markup=back_to_proxy()
        )

    async def handle_proxy_custom(self, message: Message, state: FSMContext):
        is_valid, error = self.validate_proxy(message.text)
        if not is_valid:
            await message.answer(error, reply_markup=back_home())
            return
        proxy = message.text.strip() if message.text.strip().lower() != 'skip' else None
        # مسح رسالة المستخدم التي تحتوي البروكسي صريحاً
        try:
            await message.delete()
        except Exception:
            pass
        await state.update_data(proxy=proxy)
        await state.set_state(AdGateStates.waiting_cookies)
        await message.answer(
            f"✅ <b>البروكسي:</b> {ProxyManager.mask(proxy)}\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔽 <b>الخطوة 2:</b> أرسل كوكيز فيسبوك",
            reply_markup=back_to_proxy()
        )

    async def handle_proxy_back(self, call: CallbackQuery, state: FSMContext):
        await state.set_state(AdGateStates.waiting_proxy)
        await call.message.edit_text(
            "🔽 <b>اختر البروكسي</b>",
            reply_markup=proxy_selection_keyboard()
        )

    # ────── الكوكيز ──────

    async def handle_cookies(self, message: Message, state: FSMContext):
        is_valid, error = self.validate_cookies(message.text)
        if not is_valid:
            await message.answer(error, reply_markup=back_home())
            return
        await state.update_data(cookies=message.text.strip())
        await state.set_state(AdGateStates.waiting_ad_account_id)
        await message.answer(
            "✅ <b>تم حفظ الكوكيز</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔽 <b>الخطوة 3:</b> أدخل Ad Account ID\n"
            "(أرقام فقط - مثال: 1234567890123)",
            reply_markup=back_home()
        )

    # ────── Account ID ──────

    async def handle_ad_account_id(self, message: Message, state: FSMContext):
        is_valid, result = self.validate_ad_account_id(message.text)
        if not is_valid:
            await message.answer(result, reply_markup=back_home())
            return
        await state.update_data(ad_account_id=result)
        await state.set_state(AdGateStates.waiting_page_id)
        await message.answer(
            f"✅ <b>Account ID:</b> {result}\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔽 <b>الخطوة 4:</b> أدخل Page ID\n"
            "(معرف صفحتك على فيسبوك - أرقام فقط)",
            reply_markup=back_home()
        )

    # ────── Page ID → جلب بوستات الصفحة ──────

    async def handle_page_id(self, message: Message, state: FSMContext):
        is_valid, result = self.validate_page_id(message.text)
        if not is_valid:
            await message.answer(result, reply_markup=back_home())
            return
        await state.update_data(page_id=result)
        await state.set_state(AdGateStates.waiting_post_select)

        # نجيب بوستات الصفحة تلقائياً
        fetching = await message.answer(
            f"✅ <b>Page ID:</b> {result}\n\n"
            "⏳ جاري جلب بوستات الصفحة..."
        )
        try:
            data = await state.get_data()
            resp  = await fetch_page_posts(data['cookies'], result, data.get('proxy'))
            posts = resp.get('posts', [])
            if posts:
                await fetching.edit_text(
                    f"✅ <b>Page ID:</b> {result}\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔽 <b>الخطوة 5:</b> اختر البوست ({len(posts)} بوست)",
                    reply_markup=post_selection_keyboard(posts)
                )
                await state.update_data(fetched_posts=posts)
            else:
                await fetching.edit_text(
                    f"✅ <b>Page ID:</b> {result}\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "⚠️ لم يُعثر على بوستات — أدخل رابط البوست أو معرفه يدوياً:",
                    reply_markup=back_home()
                )
                await state.set_state(AdGateStates.waiting_post_link)
        except Exception as e:
            await fetching.edit_text(
                f"✅ <b>Page ID:</b> {result}\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "🔽 <b>الخطوة 5:</b> أرسل رابط البوست أو معرفه (Post ID)\n\n"
                "📌 مثال: https://facebook.com/page/posts/123456\n"
                "📌 أو: 123456789012345",
                reply_markup=back_home()
            )
            await state.set_state(AdGateStates.waiting_post_link)

    # ────── اختيار بوست من القائمة ──────

    async def handle_post_select(self, call: CallbackQuery, state: FSMContext):
        """المستخدم اختار بوست من القائمة"""
        post_id = call.data.split(':', 1)[1]
        # إدخال يدوي
        if post_id == 'manual':
            await state.set_state(AdGateStates.waiting_post_link)
            await call.message.edit_text(
                "✏️ <b>إدخال يدوي</b>\n\n"
                "🔽 أرسل رابط البوست أو معرفه (Post ID):\n\n"
                "📌 مثال: https://facebook.com/page/posts/123456\n"
                "📌 أو: 123456789012345",
                reply_markup=back_home()
            )
            await call.answer()
            return

        # اختار بوست من القائمة
        data = await state.get_data()
        posts = data.get('fetched_posts', [])
        chosen = next((p for p in posts if p.get('id') == post_id), None)
        preview = ''
        if chosen:
            txt = chosen.get('message') or chosen.get('story') or ''
            preview = f"\n📝 {txt[:80]}..." if txt else ''

        await state.update_data(post_id=post_id, post_link=f"https://facebook.com/posts/{post_id}")
        await state.set_state(AdGateStates.waiting_objective)
        await call.message.edit_text(
            f"✅ <b>Post ID:</b> <code>{post_id}</code>{preview}\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔽 <b>الخطوة 6:</b> اختر هدف الإعلان",
            reply_markup=objective_selection_keyboard()
        )
        await call.answer()

    # ────── رابط بوست يدوي ──────

    async def handle_post_link(self, message: Message, state: FSMContext):
        text = message.text.strip()
        is_valid, error = self.validate_post_link(text)
        if not is_valid:
            await message.answer(error, reply_markup=back_home())
            return
        post_id = self.extract_post_id(text)
        link = text if 'facebook.com' in text else f"https://facebook.com/posts/{post_id}"
        await state.update_data(post_link=link, post_id=post_id)
        await state.set_state(AdGateStates.waiting_objective)
        await message.answer(
            f"✅ <b>Post ID:</b> <code>{post_id}</code>\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔽 <b>الخطوة 6:</b> اختر هدف الإعلان",
            reply_markup=objective_selection_keyboard()
        )

    # ────── الهدف ──────

    async def handle_objective(self, call: CallbackQuery, state: FSMContext):
        objective = call.data.split(':', 1)[1]
        await state.update_data(objective=objective)
        await state.set_state(AdGateStates.waiting_audience_id)
        await call.message.edit_text(
            f"✅ <b>الهدف:</b> {AdObjectives.get_display_name(objective)}\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔽 <b>الخطوة 7:</b> أدخل Audience ID\n"
            "(اختياري — اكتب skip للتخطي)",
            reply_markup=back_home()
        )
        await call.answer()

    # ────── Audience ──────

    async def handle_audience_id(self, message: Message, state: FSMContext):
        is_valid, result = self.validate_audience_id(message.text)
        if not is_valid:
            await message.answer(result, reply_markup=back_home())
            return
        await state.update_data(audience_id=result)
        await state.set_state(AdGateStates.waiting_daily_budget)
        await message.answer(
            f"✅ <b>Audience ID:</b> {result or 'افتراضي'}\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🔽 <b>الخطوة 8:</b> أدخل الميزانية اليومية (USD)\n"
            f"(الافتراضي: {GateConstants.DEFAULT_BUDGET}$ — اكتب skip للتخطي)",
            reply_markup=back_home()
        )

    # ────── الميزانية ──────

    async def handle_daily_budget(self, message: Message, state: FSMContext):
        is_valid, result = self.validate_budget(message.text)
        if not is_valid:
            await message.answer(result, reply_markup=back_home())
            return
        await state.update_data(daily_budget=result)
        await state.set_state(AdGateStates.waiting_days)
        await message.answer(
            f"✅ <b>الميزانية:</b> {result}$\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🔽 <b>الخطوة 9:</b> أدخل عدد الأيام\n"
            f"(الافتراضي: {GateConstants.DEFAULT_DAYS} — اكتب skip للتخطي)",
            reply_markup=back_home()
        )

    # ────── الأيام ──────

    async def handle_days(self, message: Message, state: FSMContext):
        is_valid, result = self.validate_days(message.text)
        if not is_valid:
            await message.answer(result, reply_markup=back_home())
            return
        await state.update_data(days=result)
        await state.set_state(AdGateStates.waiting_confirm)
        data = await state.get_data()
        await message.answer(self.format_summary(data), reply_markup=confirm_keyboard())

    # ────── تأكيد ──────

    async def handle_confirm(self, call: CallbackQuery, state: FSMContext):
        if call.data == 'confirm:yes':
            await state.set_state(AdGateStates.waiting_activate)
            await call.message.edit_text(
                "🚀 <b>جاهز للتشغيل!</b>\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "اختر طريقة النشر:",
                reply_markup=activate_or_back_keyboard()
            )
        else:
            await state.clear()
            await call.message.edit_text("❌ <b>تم إلغاء العملية</b>", reply_markup=back_home())
        await call.answer()

    # ────── تفعيل (نشر / نشر ثم إيقاف) ──────

    async def handle_activate(self, call: CallbackQuery, state: FSMContext):
        data   = await state.get_data()
        action = call.data  # activate:run | activate:run_pause

        if action not in ('activate:run', 'activate:run_pause'):
            await state.clear()
            await call.message.edit_text("🏠 <b>تم الإلغاء</b>", reply_markup=back_home())
            await call.answer()
            return

        pause = action == 'activate:run_pause'
        label = "⏸ نشر ثم إيقاف" if pause else "🟢 نشر نشط"

        await call.message.edit_text(
            f"⏳ <b>جاري إنشاء الإعلان... ({label})</b>\n\n"
            "🔄 الخطوة 1/4: إنشاء الحملة\nيرجى الانتظار..."
        )
        try:
            fn     = run_standard_ad_then_pause if pause else run_standard_ad
            result = await fn(data)
            if result['success']:
                await call.message.edit_text(
                    _result_text(result, self.gate_name),
                    reply_markup=back_home()
                )
            else:
                await call.message.edit_text(
                    f"❌ <b>فشل في خطوة: {result.get('step', '?')}</b>\n\n"
                    f"🔴 {result.get('error', 'خطأ غير معروف')}",
                    reply_markup=back_home()
                )
        except Exception as e:
            await call.message.edit_text(f"❌ <b>خطأ:</b>\n{e}", reply_markup=back_home())
        # إنهاء الفلو لتحرير قفل المستخدم تلقائياً
        await state.clear()
        await call.answer()
