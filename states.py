from aiogram.fsm.state import StatesGroup, State


class AdGateStates(StatesGroup):
    waiting_proxy = State()
    waiting_cookies = State()
    waiting_ad_account_id = State()
    waiting_page_id = State()
    waiting_post_select = State()   # اختيار البوست من القائمة
    waiting_post_link = State()     # كتابة رابط/ID يدوي
    waiting_post_id = State()
    waiting_image = State()
    waiting_caption = State()
    waiting_objective = State()
    waiting_audience_id = State()
    waiting_daily_budget = State()
    waiting_days = State()
    waiting_confirm = State()
    waiting_activate = State()
    waiting_ad_set_id = State()


class UserFlow(StatesGroup):
    waiting_redeem = State()


class AdminFlow(StatesGroup):
    waiting_password = State()
    waiting_code_duration = State()
    waiting_user_manage = State()
    waiting_remove_user = State()
    waiting_broadcast = State()
    waiting_proxies = State()


class AdObjectives:
    CONVERSATIONS = "CONVERSATIONS"
    MESSAGES_MESSENGER = "MESSAGES_MESSENGER"
    MESSAGES_WHATSAPP = "MESSAGES_WHATSAPP"
    LINK_CLICKS = "LINK_CLICKS"
    POST_ENGAGEMENT = "POST_ENGAGEMENT"
    VIDEO_VIEWS = "VIDEO_VIEWS"

    @classmethod
    def all(cls):
        return [
            cls.CONVERSATIONS,
            cls.MESSAGES_MESSENGER,
            cls.MESSAGES_WHATSAPP,
            cls.LINK_CLICKS,
            cls.POST_ENGAGEMENT,
            cls.VIDEO_VIEWS,
        ]

    @classmethod
    def get_display_name(cls, objective: str) -> str:
        names = {
            cls.CONVERSATIONS: "💬 محادثات (Conversations)",
            cls.MESSAGES_MESSENGER: "📨 رسائل ماسنجر",
            cls.MESSAGES_WHATSAPP: "📱 رسائل واتساب",
            cls.LINK_CLICKS: "🔗 نقرات على الرابط",
            cls.POST_ENGAGEMENT: "📈 تفاعل مع البوست",
            cls.VIDEO_VIEWS: "🎬 مشاهدات فيديو",
        }
        return names.get(objective, objective)

    @classmethod
    def get_optimization_goal(cls, objective: str) -> str:
        goal_map = {
            cls.CONVERSATIONS: "CONVERSATIONS",
            cls.MESSAGES_MESSENGER: "CONVERSATIONS",
            cls.MESSAGES_WHATSAPP: "CONVERSATIONS",
            cls.LINK_CLICKS: "LINK_CLICKS",
            cls.POST_ENGAGEMENT: "POST_ENGAGEMENT",
            cls.VIDEO_VIEWS: "THRUPLAY",
        }
        return goal_map.get(objective, "LINK_CLICKS")

    @classmethod
    def get_cta_type(cls, objective: str) -> str:
        cta_map = {
            cls.CONVERSATIONS: "MESSAGE_PAGE",
            cls.MESSAGES_MESSENGER: "MESSAGE_PAGE",
            cls.MESSAGES_WHATSAPP: "WHATSAPP_MESSAGE",
            cls.LINK_CLICKS: "LEARN_MORE",
            cls.POST_ENGAGEMENT: "LIKE_PAGE",
            cls.VIDEO_VIEWS: "WATCH_MORE",
        }
        return cta_map.get(objective, "LEARN_MORE")

    @classmethod
    def get_fb_objective(cls, objective: str) -> str:
        obj_map = {
            cls.CONVERSATIONS: "OUTCOME_ENGAGEMENT",
            cls.MESSAGES_MESSENGER: "OUTCOME_ENGAGEMENT",
            cls.MESSAGES_WHATSAPP: "OUTCOME_ENGAGEMENT",
            cls.LINK_CLICKS: "OUTCOME_TRAFFIC",
            cls.POST_ENGAGEMENT: "OUTCOME_ENGAGEMENT",
            cls.VIDEO_VIEWS: "OUTCOME_ENGAGEMENT",
        }
        return obj_map.get(objective, "OUTCOME_ENGAGEMENT")


class BMToolStates(StatesGroup):
    waiting_proxy      = State()
    waiting_cookies    = State()
    waiting_bm_id      = State()
    waiting_ad_id      = State()
    waiting_dtsg       = State()  # اختياري: إدخال DTSG يدوي
    waiting_user_id    = State()  # اختياري: إدخال User ID يدوي
    waiting_card_sel   = State()
    waiting_interval   = State()


class GateConstants:
    MIN_BUDGET = 1.0
    MAX_BUDGET = 10000.0
    DEFAULT_BUDGET = 10.0
    MIN_DAYS = 1
    MAX_DAYS = 365
    DEFAULT_DAYS = 7
    MAX_IMAGE_SIZE = 10 * 1024 * 1024
    IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.gif', '.webp']
    MIN_CAPTION_LENGTH = 10
    MAX_CAPTION_LENGTH = 2000
