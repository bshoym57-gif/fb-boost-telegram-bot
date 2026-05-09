"""
partner_ship_gate.py
بوابة Partnership Ads — مع دعم:
- partner_page_id + partner_post_id بدل ad_code
- whatsapp_phone لو الهدف MESSAGES_WHATSAPP
- نشر نشط / نشر ثم إيقاف
"""
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from keyboards import (
    back_home, proxy_selection_keyboard, back_to_proxy,
    objective_selection_keyboard, confirm_keyboard, activate_or_back_keyboard
)
from states import AdObjectives, GateConstants, AdGateStates
from gates.base_gate import BaseGate
from services.facebook_api import run_partner_ship_ad, run_partner_ship_ad_then_pause
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


class PartnerShipGate(BaseGate):
    def __init__(self):
        super().__init__('partner_ship', '🟣 إعلان بارتنر شيب', 'partner_ship')

    async def enter(self, call: CallbackQuery, state: FSMContext, config: dict):
        await state.update_data(gate_id=self.gate_id, gate_type=self.gate_type, gate_name=self.gate_name)
        await state.set_state(AdGateStates.waiting_proxy)
        await call.message.edit_text(
            f"🚪 <b>{self.gate_name}</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📋 <b>خطوات العمل:</b>\n"
            "1️⃣ البروكسي\n2️⃣ الكوكيز\n3️⃣ Account ID\n4️⃣ Page ID (صفحتك)\n"
            "5️⃣ Partner Page ID (صفحة الشريك)\n6️⃣ Partner Post ID\n"
            "7️⃣ الهدف\n8️⃣ الميزانية والأيام\n9️⃣ مراجعة وتشغيل\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔽 <b>الخطوة 1:</b> اختر البروكسي",
            reply_markup=proxy_selection_keyboard()
        )

    # ── البروكسي ──

    async def handle_proxy_auto(self, call: CallbackQuery, state: FSMContext, proxy: str = None):
        if not proxy:
            await call.answer("⚠️ لا توجد بروكسيات متاحة", show_alert=True)
            return
        await state.update_data(proxy=proxy)
        await state.set_state(AdGateStates.waiting_cookies)
        await call.message.edit_text(
            f"✅ <b>البروكسي:</b> {ProxyManager.mask(proxy)}\n\n"
            "🔽 <b>الخطوة 2:</b> أرسل كوكيز فيسبوك",
            reply_markup=back_to_proxy()
        )

    async def handle_proxy_skip(self, call: CallbackQuery, state: FSMContext):
        await state.update_data(proxy=None)
        await state.set_state(AdGateStates.waiting_cookies)
        await call.message.edit_text(
            "✅ <b>تخطي البروكسي</b>\n\n"
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
            "🔽 <b>الخطوة 2:</b> أرسل كوكيز فيسبوك",
            reply_markup=back_to_proxy()
        )

    async def handle_proxy_back(self, call: CallbackQuery, state: FSMContext):
        await state.set_state(AdGateStates.waiting_proxy)
        await call.message.edit_text("🔽 <b>اختر البروكسي</b>", reply_markup=proxy_selection_keyboard())

    # ── الكوكيز ──

    async def handle_cookies(self, message: Message, state: FSMContext):
        is_valid, error = self.validate_cookies(message.text)
        if not is_valid:
            await message.answer(error, reply_markup=back_home())
            return
        await state.update_data(cookies=message.text.strip())
        await state.set_state(AdGateStates.waiting_ad_account_id)
        await message.answer(
            "✅ <b>تم حفظ الكوكيز</b>\n\n"
            "🔽 <b>الخطوة 3:</b> أدخل Ad Account ID",
            reply_markup=back_home()
        )

    # ── Account ID ──

    async def handle_ad_account_id(self, message: Message, state: FSMContext):
        is_valid, result = self.validate_ad_account_id(message.text)
        if not is_valid:
            await message.answer(result, reply_markup=back_home())
            return
        await state.update_data(ad_account_id=result)
        await state.set_state(AdGateStates.waiting_page_id)
        await message.answer(
            f"✅ <b>Account ID:</b> {result}\n\n"
            "🔽 <b>الخطوة 4:</b> أدخل <b>Page ID (صفحتك)</b>\n"
            "(الصفحة المعلنة — أرقام فقط)",
            reply_markup=back_home()
        )

    # ── Page ID (صفحتك) ──

    async def handle_page_id(self, message: Message, state: FSMContext):
        is_valid, result = self.validate_page_id(message.text)
        if not is_valid:
            await message.answer(result, reply_markup=back_home())
            return
        await state.update_data(page_id=result)
        await state.set_state(AdGateStates.waiting_ad_set_id)
        await message.answer(
            f"✅ <b>Page ID (صفحتك):</b> {result}\n\n"
            "🔽 <b>الخطوة 5:</b> أدخل <b>Partner Page ID</b>\n"
            "(معرف صفحة الشريك/المنشئ — أرقام فقط)",
            reply_markup=back_home()
        )

    # ── Partner Page ID ──

    async def handle_ad_set_id(self, message: Message, state: FSMContext):
        """نُعيد استخدام waiting_ad_set_id لتخزين partner_page_id"""
        pid = message.text.strip()
        if not pid.isdigit() or len(pid) < 5:
            await message.answer("❌ Partner Page ID غير صحيح (أرقام فقط)", reply_markup=back_home())
            return
        await state.update_data(partner_page_id=pid)
        await state.set_state(AdGateStates.waiting_post_id)
        await message.answer(
            f"✅ <b>Partner Page ID:</b> {pid}\n\n"
            "🔽 <b>الخطوة 6:</b> أدخل <b>Partner Post ID</b>\n"
            "(معرف البوست المراد تعزيزه من صفحة الشريك — أرقام فقط)",
            reply_markup=back_home()
        )

    # ── Partner Post ID ──

    async def handle_ad_code(self, message: Message, state: FSMContext):
        """نُعيد استخدام handle_ad_code لتخزين partner_post_id"""
        post_id = message.text.strip()
        if not post_id or len(post_id) < 5:
            await message.answer("❌ Partner Post ID غير صحيح", reply_markup=back_home())
            return
        await state.update_data(partner_post_id=post_id)
        await state.set_state(AdGateStates.waiting_objective)
        await message.answer(
            f"✅ <b>Partner Post ID:</b> {post_id}\n\n"
            "🔽 <b>الخطوة 7:</b> اختر هدف الإعلان",
            reply_markup=objective_selection_keyboard()
        )

    # ── الهدف ──

    async def handle_objective(self, call: CallbackQuery, state: FSMContext):
        objective = call.data.split(':', 1)[1]
        await state.update_data(objective=objective)

        if objective == AdObjectives.MESSAGES_WHATSAPP:
            await state.set_state(AdGateStates.waiting_audience_id)
            await call.message.edit_text(
                f"✅ <b>الهدف:</b> {AdObjectives.get_display_name(objective)}\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "📱 <b>الخطوة 7.5:</b> أدخل رقم واتساب الصفحة\n"
                "(مثال: 201012345678 — بدون +)",
                reply_markup=back_home()
            )
        else:
            await state.set_state(AdGateStates.waiting_daily_budget)
            await call.message.edit_text(
                f"✅ <b>الهدف:</b> {AdObjectives.get_display_name(objective)}\n\n"
                f"🔽 <b>الخطوة 8:</b> أدخل الميزانية اليومية (USD)\n"
                f"(الافتراضي: {GateConstants.DEFAULT_BUDGET}$)",
                reply_markup=back_home()
            )
        await call.answer()

    # ── رقم واتساب (اختياري للـ MESSAGES_WHATSAPP) ──

    async def handle_audience_id(self, message: Message, state: FSMContext):
        """نُعيد استخدام waiting_audience_id لتخزين whatsapp_phone"""
        phone = message.text.strip().lstrip('+')
        if not phone.isdigit() or len(phone) < 7:
            await message.answer(
                "❌ رقم واتساب غير صحيح — أدخل الرقم بدون + (مثال: 201012345678)",
                reply_markup=back_home()
            )
            return
        await state.update_data(whatsapp_phone=phone)
        await state.set_state(AdGateStates.waiting_daily_budget)
        await message.answer(
            f"✅ <b>رقم واتساب:</b> +{phone}\n\n"
            f"🔽 <b>الخطوة 8:</b> أدخل الميزانية اليومية (USD)\n"
            f"(الافتراضي: {GateConstants.DEFAULT_BUDGET}$)",
            reply_markup=back_home()
        )

    # ── الميزانية ──

    async def handle_daily_budget(self, message: Message, state: FSMContext):
        is_valid, result = self.validate_budget(message.text)
        if not is_valid:
            await message.answer(result, reply_markup=back_home())
            return
        await state.update_data(daily_budget=result)
        await state.set_state(AdGateStates.waiting_days)
        await message.answer(
            f"✅ <b>الميزانية:</b> {result}$\n\n"
            f"🔽 <b>الخطوة 9:</b> أدخل عدد الأيام\n"
            f"(الافتراضي: {GateConstants.DEFAULT_DAYS})",
            reply_markup=back_home()
        )

    # ── الأيام ──

    async def handle_days(self, message: Message, state: FSMContext):
        is_valid, result = self.validate_days(message.text)
        if not is_valid:
            await message.answer(result, reply_markup=back_home())
            return
        await state.update_data(days=result)
        await state.set_state(AdGateStates.waiting_confirm)
        data = await state.get_data()
        await message.answer(self.format_summary(data), reply_markup=confirm_keyboard())

    # ── تأكيد ──

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
            await call.message.edit_text("❌ <b>تم الإلغاء</b>", reply_markup=back_home())
        await call.answer()

    # ── تفعيل ──

    async def handle_activate(self, call: CallbackQuery, state: FSMContext):
        data   = await state.get_data()
        action = call.data

        if action not in ('activate:run', 'activate:run_pause'):
            await state.clear()
            await call.message.edit_text("🏠 <b>تم الإلغاء</b>", reply_markup=back_home())
            await call.answer()
            return

        pause = action == 'activate:run_pause'
        label = "⏸ نشر ثم إيقاف" if pause else "🟢 نشر نشط"

        await call.message.edit_text(
            f"⏳ <b>جاري إنشاء إعلان البارتنر شيب... ({label})</b>\n\n"
            "🔗 ربط الصفحات...\nيرجى الانتظار"
        )
        try:
            fn     = run_partner_ship_ad_then_pause if pause else run_partner_ship_ad
            result = await fn(data)
            if result['success']:
                await call.message.edit_text(_result_text(result, self.gate_name), reply_markup=back_home())
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
