import asyncio
import time
import os
import logging
import signal
import aiosqlite
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ChatMemberHandler, ConversationHandler, ContextTypes, filters
)
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.error import TelegramError, RetryAfter, Forbidden

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)



REQUIRED_SUBS = [
    {"id": -1004488956454, "link": "https://t.me/vocmaq"}
]

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8771920917:AAEPtIOoFhi1GDoQEtYlkyGQC0HCGvRkiPQ")
OWNER_IDS = [7830598141, 906824039]
SUPER_ADMIN = 906824039
PROTECTED_CHATS = [-1004488956454]
PHOTO_PATH = "image.jpg"
DB_PATH = "leakspam.db"
BOT_USERNAME = "FakeCleaner_Bot"

E_CONNECT = "6028171274939797252"
E_HOW = "6030848053177486888"
E_DISCONNECT = "6030864215139422409"
E_ADMIN = "6037249452824072506"
E_BAN = "6039420807900303010"
E_UNBAN = "6043874504302661409"
E_STATS_BINDS = "6039630677182254664"
E_STATS_NAKRUTKA = "6021788373717358680"
E_BROADCAST = "5938537205847822613"
E_BACK = "5938537205847822613"
E_ADD_BOT = "5807642502634674850"
E_OK = "5836907383292436018"
E_SUCCESS_CH = "5774022692642492953"
E_WARN_ICO = "5339086687609829159"
E_CLEAN = "6019606216798378032"
E_TRASH = "6019606216798378032"
E_SLOTS = "6028171274939797252"

E_WELCOME = "5823537588186647980"
E_HOW_TITLE = "5893290369629556374"
E_CONN_TITLE = "5893185207355315979"
E_DOT = "5339113303522161846"
E_CHANNELS = "5336780655244095683"
E_SUCCESS = "5334789577125147626"
E_CHECK = "5841359499146825803"
E_FAIL = "5843952899184398024"
E_NOTIFY = "5226739117764655849"
E_STATS = "5336780655244095683"
E_BROAD = "5841243255856960314"
E_NAKRUTKA = "5893072412924187198"
E_CLOCK = "6021451978993834164"
E_HEART = "5841243255856960314"
E_NEW_USER = "4918354603281482671"
E_WARNING = "5339086687609829159"

WAIT_FORWARD, WAIT_BROADCAST, WAIT_BAN, WAIT_UNBAN, WAIT_ADD_SLOTS, WAIT_CLEAN_CHANNEL = range(6)
joins_cache = {}
nakrutka_messages = {}
nakrutka_last_reset = {}
nakrutka_last_notify = {}
banned_cache = set()
chat_links_cache = {}
waiting_for_forward = {}
admin_cache = {}
MAX_CHANNELS_DEFAULT = 2
MAX_CACHE_SIZE = 1000
NOTIFY_COOLDOWN = 30
MAX_NAKRUTKA_MESSAGES = 500
MAX_JOINS_PER_CHANNEL = 200

BAN_POOL_SMALL = (5, 10)
BAN_POOL_MEDIUM = (8, 20)
BAN_POOL_LARGE = (12, 35)
BAN_SUSTAINED_THRESHOLD = 15
BAN_SUSTAINED_WINDOW = 180
BURST_WINDOW = 2
BURST_ID_GAP = 1000
BURST_MIN_USERS = 2
RATIO_WINDOW = 120
RATIO_THRESHOLD = 0.03

def check_photo():
    if not os.path.exists(PHOTO_PATH):
        try:
            from PIL import Image
            img = Image.new('RGB', (800, 400), color='#1a1a1a')
            img.save(PHOTO_PATH)
            logger.info(f"Создан {PHOTO_PATH}")
        except Exception:
            with open(PHOTO_PATH, 'wb') as f:
                f.write(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x03 \x00\x00\x01\x90\x08\x02\x00\x00\x00')
            logger.info(f"Создан заглушка {PHOTO_PATH}")

async def get_chat_link(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> str:
    if chat_id in chat_links_cache:
        return chat_links_cache[chat_id]
    try:
        chat = await context.bot.get_chat(chat_id)
        if chat and chat.username:
            link = f"@{chat.username}"
        else:
            link = f"t.me/c/{str(chat_id).replace('-100', '')}"
        chat_links_cache[chat_id] = link
        return link
    except Exception:
        link = f"t.me/c/{str(chat_id).replace('-100', '')}"
        chat_links_cache[chat_id] = link
        return link

async def init_banned_cache():
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA busy_timeout=5000")
            async with db.execute("SELECT user_id FROM users WHERE banned=1") as cur:
                rows = await cur.fetchall()
                for row in rows:
                    banned_cache.add(row[0])
    except Exception as e:
        logger.error(f"init_banned_cache error: {e}")

async def get_chat_admins(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> set:
    if chat_id in admin_cache:
        return admin_cache[chat_id]
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        admin_ids = {admin.user.id for admin in admins}
        admin_cache[chat_id] = admin_ids
        return admin_ids
    except Exception:
        return set()

async def cleanup_cache():
    while True:
        try:
            await asyncio.sleep(1800)
            now = time.time()
            for chat_id in list(joins_cache.keys()):
                joins_cache[chat_id] = {u: t for u, t in joins_cache[chat_id].items() if now - t < max(BAN_SUSTAINED_WINDOW, RATIO_WINDOW)}
                if not joins_cache[chat_id]:
                    del joins_cache[chat_id]
            for chat_id in list(nakrutka_last_reset.keys()):
                if now - nakrutka_last_reset[chat_id] > 3600:
                    del nakrutka_last_reset[chat_id]
                    if chat_id in nakrutka_messages:
                        del nakrutka_messages[chat_id]
                    if chat_id in nakrutka_last_notify:
                        del nakrutka_last_notify[chat_id]
            if len(joins_cache) > MAX_CACHE_SIZE:
                oldest = sorted(joins_cache.keys(), key=lambda x: min(joins_cache[x].values()) if joins_cache[x] else now)[:len(joins_cache)-MAX_CACHE_SIZE]
                for k in oldest:
                    del joins_cache[k]
            if len(nakrutka_messages) > MAX_NAKRUTKA_MESSAGES:
                oldest = sorted(nakrutka_messages.keys(), key=lambda x: nakrutka_last_reset.get(x, now))[:len(nakrutka_messages)-MAX_NAKRUTKA_MESSAGES]
                for k in oldest:
                    del nakrutka_messages[k]
                    if k in nakrutka_last_reset:
                        del nakrutka_last_reset[k]
                    if k in nakrutka_last_notify:
                        del nakrutka_last_notify[k]
            banned_cache.clear()
            await init_banned_cache()
            chat_links_cache.clear()
            admin_cache.clear()
            for uid in list(waiting_for_forward.keys()):
                if now - waiting_for_forward[uid] > 300:
                    del waiting_for_forward[uid]
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"cleanup error: {e}")

def init_db():
    import sqlite3
    try:
        with sqlite3.connect(DB_PATH) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=5000")
            db.execute('''CREATE TABLE IF NOT EXISTS channels (channel_id INTEGER PRIMARY KEY, owner_id INTEGER, title TEXT, nakrutka_count INTEGER DEFAULT 0)''')
            db.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, banned INTEGER DEFAULT 0, max_channels INTEGER DEFAULT 2)''')
            db.execute('''CREATE TABLE IF NOT EXISTS clean_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, chat_id INTEGER, chat_title TEXT, cleaned_count INTEGER, timestamp REAL)''')
            db.execute("CREATE INDEX IF NOT EXISTS idx_channels_owner ON channels(owner_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_users_user ON users(user_id)")
            try:
                db.execute("ALTER TABLE users ADD COLUMN max_channels INTEGER DEFAULT 2")
            except Exception:
                pass
            db.commit()
    except Exception as e:
        logger.error(f"init_db error: {e}")
        raise

async def is_banned(uid: int) -> bool:
    if uid in banned_cache:
        return True
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA busy_timeout=5000")
            async with db.execute("SELECT banned FROM users WHERE user_id=?", (uid,)) as cur:
                row = await cur.fetchone()
                banned = row is not None and row[0] == 1
                if banned:
                    banned_cache.add(uid)
                return banned
    except Exception:
        return False

async def get_max_channels(uid: int) -> int:
    if uid in OWNER_IDS:
        return 999
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA busy_timeout=5000")
            async with db.execute("SELECT max_channels FROM users WHERE user_id=?", (uid,)) as cur:
                row = await cur.fetchone()
                return row[0] if row else MAX_CHANNELS_DEFAULT
    except Exception:
        return MAX_CHANNELS_DEFAULT

async def get_user_channels_count(uid: int) -> int:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA busy_timeout=5000")
            async with db.execute("SELECT COUNT(*) FROM channels WHERE owner_id=?", (uid,)) as cur:
                row = await cur.fetchone()
                return row[0] if row else 0
    except Exception:
        return 0

async def check_all_subs(uid: int, context: ContextTypes.DEFAULT_TYPE) -> tuple:
    subs_status = []
    for sub in REQUIRED_SUBS:
        try:
            member = await context.bot.get_chat_member(sub["id"], uid)
            is_sub = member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
            subs_status.append({**sub, "subscribed": is_sub})
        except Exception:
            subs_status.append({**sub, "subscribed": False})
    all_subscribed = all(s["subscribed"] for s in subs_status)
    return all_subscribed, subs_status

async def get_chat_member_count(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        return await context.bot.get_chat_member_count(chat_id)
    except Exception:
        return 500

def pe(eid: str, fb: str) -> str:
    return f'<tg-emoji emoji-id="{eid}">{fb}</tg-emoji>'

def is_admin(uid: int) -> bool:
    return uid in OWNER_IDS

def kb(text: str, data: str = None, url: str = None, icon_id: str = None) -> InlineKeyboardButton:
    if url:
        return InlineKeyboardButton(text=text, url=url, icon_custom_emoji_id=icon_id) if icon_id else InlineKeyboardButton(text=text, url=url)
    return InlineKeyboardButton(text=text, callback_data=data, icon_custom_emoji_id=icon_id) if icon_id else InlineKeyboardButton(text=text, callback_data=data)

def main_kb(uid: int) -> InlineKeyboardMarkup:
    btns = [
        [kb("Подключить канал", data="menu:connect", icon_id=E_CONNECT)],
        [
            kb("Как подключить", data="menu:how_to", icon_id=E_HOW),
            kb("Отключить канал", data="menu:disconnect", icon_id=E_DISCONNECT)
        ]
    ]
    if is_admin(uid):
        btns.append([kb("Админ панель", data="menu:admin", icon_id=E_ADMIN)])
    return InlineKeyboardMarkup(btns)

def not_subscribed_kb(missing_subs: list) -> InlineKeyboardMarkup:
    btns = []
    for sub in missing_subs:
        btns.append([InlineKeyboardButton(f"Подписаться на {sub['link'].split('/')[-1]}", url=sub["link"])])
    btns.append([InlineKeyboardButton("Проверить подписку", callback_data="check_sub")])
    return InlineKeyboardMarkup(btns)

def get_channel_id_from_message(message) -> tuple:
    """Извлекает ID канала и название из пересланного сообщения"""
    ch_id = None
    chat_title = None
    
    # Способ 1: forward_origin (новый API PTB v20+)
    if hasattr(message, 'forward_origin') and message.forward_origin:
        if hasattr(message.forward_origin, 'chat') and message.forward_origin.chat:
            ch_id = message.forward_origin.chat.id
            chat_title = message.forward_origin.chat.title or message.forward_origin.chat.username or "Без названия"
            logger.info(f"Получен канал из forward_origin: {ch_id} ({chat_title})")
            return ch_id, chat_title
    
    # Способ 2: forward_from_chat в api_kwargs (для обратной совместимости)
    if hasattr(message, 'api_kwargs') and message.api_kwargs:
        if 'forward_from_chat' in message.api_kwargs:
            fwd = message.api_kwargs['forward_from_chat']
            ch_id = fwd['id']
            chat_title = fwd.get('title') or fwd.get('username') or "Без названия"
            logger.info(f"Получен канал из api_kwargs: {ch_id} ({chat_title})")
            return ch_id, chat_title
    
    return None, None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if await is_banned(uid):
        return
    all_subscribed, subs_status = await check_all_subs(uid, context)
    if not all_subscribed:
        missing = [s for s in subs_status if not s["subscribed"]]
        channels_text = "\n".join([f"{pe(E_DOT, '🔸')} {s['link']}" for s in missing])
        caption = f"{pe(E_FAIL, '❌')} <b>Вы не подписаны на каналы!</b>\n\nПодпишитесь чтобы пользоваться ботом:\n{channels_text}"
        with open(PHOTO_PATH, 'rb') as f:
            await update.message.reply_photo(f, caption=caption, reply_markup=not_subscribed_kb(missing), parse_mode=ParseMode.HTML)
        return
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("INSERT OR IGNORE INTO users (user_id,banned,max_channels) VALUES (?,0,?)", (uid, MAX_CHANNELS_DEFAULT))
            await db.commit()
    except Exception as e:
        logger.error(f"start db error: {e}")
    caption = f"{pe(E_NEW_USER, '⭐')} <b>Добро пожаловать в FakeCleaner,</b>\nFakeCleaner - лучшая вещь от накрутки! {pe(E_WELCOME, '🛡')}"
    with open(PHOTO_PATH, 'rb') as f:
        await update.message.reply_photo(f, caption=caption, reply_markup=main_kb(uid), parse_mode=ParseMode.HTML)

async def check_sub_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    all_subscribed, subs_status = await check_all_subs(uid, context)
    if all_subscribed:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("PRAGMA busy_timeout=5000")
                await db.execute("INSERT OR IGNORE INTO users (user_id,banned,max_channels) VALUES (?,0,?)", (uid, MAX_CHANNELS_DEFAULT))
                await db.commit()
        except Exception:
            pass
        caption = f"{pe(E_NEW_USER, '⭐')} <b>Добро пожаловать в FakeCleaner,</b>\nFakeCleaner - лучшая вещь от накрутки! {pe(E_WELCOME, '🛡')}"
        await q.message.edit_caption(caption=caption, reply_markup=main_kb(uid), parse_mode=ParseMode.HTML)
    else:
        missing = [s for s in subs_status if not s["subscribed"]]
        channels_text = "\n".join([f"{pe(E_DOT, '🔸')} {s['link']}" for s in missing])
        caption = f"{pe(E_FAIL, '❌')} <b>Вы всё ещё не подписаны!</b>\n\nПодпишитесь:\n{channels_text}"
        await q.message.edit_caption(caption=caption, reply_markup=not_subscribed_kb(missing), parse_mode=ParseMode.HTML)

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    act = q.data.split(":")[1]
    uid = update.effective_user.id
    if act != "check_sub":
        all_subscribed, subs_status = await check_all_subs(uid, context)
        if not all_subscribed:
            missing = [s for s in subs_status if not s["subscribed"]]
            channels_text = "\n".join([f"{pe(E_DOT, '🔸')} {s['link']}" for s in missing])
            caption = f"{pe(E_FAIL, '❌')} <b>Вы не подписаны на каналы!</b>\n\nПодпишитесь:\n{channels_text}"
            await q.message.edit_caption(caption=caption, reply_markup=not_subscribed_kb(missing), parse_mode=ParseMode.HTML)
            return ConversationHandler.END
    if act == "main":
        waiting_for_forward.pop(uid, None)
        caption = f"{pe(E_WELCOME, '👋')} <b>Добро пожаловать в FakeCleaner,</b>\nFakeCleaner - лучшая вещь от накрутки! {pe(E_WELCOME, '🛡')}"
        await q.message.edit_caption(caption=caption, reply_markup=main_kb(uid), parse_mode=ParseMode.HTML)
    elif act == "how_to":
        waiting_for_forward.pop(uid, None)
        caption = f"{pe(E_HOW_TITLE, '❓')} <b>Как подключить бота?</b>\n\n{pe(E_CONN_TITLE, '⚙️')} <b>Что нужно:</b>\n<blockquote>{pe(E_DOT, '🔸')} Добавь бота в канал как админа\n{pe(E_DOT, '🔸')} Выдай права: блокировка и добавление\n{pe(E_DOT, '🔸')} Перешли сообщение из канала</blockquote>"
        kb_m = InlineKeyboardMarkup([
            [kb("Добавить бота в канал", url=f"https://t.me/{BOT_USERNAME}?startchannel&admin=invite_users", icon_id=E_ADD_BOT)],
            [kb("Обратно в меню", data="menu:main", icon_id=E_BACK)]
        ])
        await q.message.edit_caption(caption=caption, reply_markup=kb_m, parse_mode=ParseMode.HTML)
    elif act == "connect":
        max_ch = await get_max_channels(uid)
        current_ch = await get_user_channels_count(uid)
        if current_ch >= max_ch:
            waiting_for_forward.pop(uid, None)
            caption = f"{pe(E_FAIL, '❌')} <b>Достигнут лимит каналов!</b>\n\nУ вас: {current_ch}/{max_ch}"
            kb_m = InlineKeyboardMarkup([[kb("Обратно в меню", data="menu:main", icon_id=E_BACK)]])
            await q.message.edit_caption(caption=caption, reply_markup=kb_m, parse_mode=ParseMode.HTML)
            return ConversationHandler.END
        waiting_for_forward[uid] = time.time()
        caption = (
            f"{pe(E_HEART, '❤')} <b>Добавление канала</b>\n\n"
            f"<blockquote>"
            f"1. Нажмите кнопку ниже.\n"
            f"2. Добавьте бота в администраторы канала,\n"
            f"нужные права: <b>Блокировка пользователей</b> и <b>Пригласительные ссылки</b>\n"
            f"3. Перешлите сообщение от канала."
            f"</blockquote>\n"
            f"Лимит: {current_ch}/{max_ch}"
        )
        kb_m = InlineKeyboardMarkup([
            [kb("Добавить бота в канал", url=f"https://t.me/{BOT_USERNAME}?startchannel&admin=invite_users", icon_id=E_ADD_BOT)],
            [kb("Обратно в меню", data="menu:main", icon_id=E_BACK)]
        ])
        await q.message.edit_caption(caption=caption, reply_markup=kb_m, parse_mode=ParseMode.HTML)
        return WAIT_FORWARD
    elif act == "disconnect":
        waiting_for_forward.pop(uid, None)
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("PRAGMA busy_timeout=5000")
                async with db.execute("SELECT channel_id,title FROM channels WHERE owner_id=?", (uid,)) as cur:
                    channels = await cur.fetchall()
        except Exception:
            channels = []
        caption = f"{pe(E_CHANNELS, '📋')} <b>Ваши каналы:</b>"
        btns = []
        for ch_id, title in channels:
            link = await get_chat_link(context, ch_id)
            btns.append([kb(f"{title} | {link}", data=f"ask_disc:{ch_id}", icon_id=E_SUCCESS_CH)])
        btns.append([kb("Назад", data="menu:main", icon_id=E_BACK)])
        await q.message.edit_caption(caption=caption, reply_markup=InlineKeyboardMarkup(btns), parse_mode=ParseMode.HTML)
    elif act == "admin" and is_admin(uid):
        waiting_for_forward.pop(uid, None)
        caption = "<b>Админ панель</b>"
        admin_btns = [
            [kb("Бан", data="admin:ban", icon_id=E_BAN), kb("Разбан", data="admin:unban", icon_id=E_UNBAN)],
            [kb("Статистика привязок", data="admin:stats_binds", icon_id=E_STATS_BINDS)],
            [kb("Статистика накруток", data="admin:stats_nakrutka", icon_id=E_STATS_NAKRUTKA)],
            [kb("Рассылка", data="admin:broadcast", icon_id=E_BROADCAST)],
            [kb("Выдать слоты", data="admin:add_slots", icon_id=E_SLOTS)]
        ]
        admin_btns.append([kb("Назад", data="menu:main", icon_id=E_BACK)])
        kb_m = InlineKeyboardMarkup(admin_btns)
        await q.message.edit_caption(caption=caption, reply_markup=kb_m, parse_mode=ParseMode.HTML)
    return ConversationHandler.END

async def ask_disc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ch_id = q.data.split(":")[1]
    caption = f"{pe(E_DOT, '⚠️')} <b>Отключить канал?</b>"
    kb_m = InlineKeyboardMarkup([
        [kb("Отключить", data=f"do_disc:{ch_id}", icon_id=E_WARN_ICO)],
        [kb("Назад", data="menu:disconnect", icon_id=E_BACK)]
    ])
    await q.message.edit_caption(caption=caption, reply_markup=kb_m, parse_mode=ParseMode.HTML)

async def do_disc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ch_id = int(q.data.split(":")[1])
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("DELETE FROM channels WHERE channel_id=? AND owner_id=?", (ch_id, update.effective_user.id))
            await db.commit()
    except Exception:
        pass
    caption = f"{pe(E_SUCCESS, '✅')} <b>Канал отключён</b>"
    kb_m = InlineKeyboardMarkup([[kb("Назад", data="menu:disconnect", icon_id=E_BACK)]])
    await q.message.edit_caption(caption=caption, reply_markup=kb_m, parse_mode=ParseMode.HTML)

async def process_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ВСЕХ пересланных сообщений"""
    uid = update.effective_user.id
    msg = update.message
    
    if not msg:
        return
    
    logger.info(f"🔄 Обработчик сообщения от {uid}")
    
    # Получаем ID канала новым способом
    ch_id, chat_title = get_channel_id_from_message(msg)
    
    if not ch_id:
        # Если это не пересланное сообщение из канала - игнорируем
        if uid in waiting_for_forward:
            # Юзер в режиме ожидания, но прислал не то
            caption = f"{pe(E_FAIL, '❌')} <b>Это не пересланное сообщение из канала!</b>\n\nПерешлите именно сообщение из канала."
            kb_m = InlineKeyboardMarkup([[kb("Назад", data="menu:main", icon_id=E_BACK)]])
            with open(PHOTO_PATH, 'rb') as f:
                await msg.reply_photo(f, caption=caption, reply_markup=kb_m, parse_mode=ParseMode.HTML)
        return
    
    logger.info(f"✅ Обнаружен канал: {ch_id} ({chat_title})")
    
    # Проверяем подписку
    all_subscribed, subs_status = await check_all_subs(uid, context)
    if not all_subscribed:
        missing = [s for s in subs_status if not s["subscribed"]]
        channels_text = "\n".join([f"{pe(E_DOT, '🔸')} {s['link']}" for s in missing])
        caption = f"{pe(E_FAIL, '❌')} <b>Вы не подписаны на каналы!</b>\n\nПодпишитесь:\n{channels_text}"
        with open(PHOTO_PATH, 'rb') as f:
            await msg.reply_photo(f, caption=caption, reply_markup=not_subscribed_kb(missing), parse_mode=ParseMode.HTML)
        return
    
    # Проверяем лимит
    max_ch = await get_max_channels(uid)
    current_ch = await get_user_channels_count(uid)
    if current_ch >= max_ch:
        caption = f"{pe(E_FAIL, '❌')} <b>Достигнут лимит каналов!</b>\n\nУ вас: {current_ch}/{max_ch}"
        kb_m = InlineKeyboardMarkup([[kb("Назад", data="menu:main", icon_id=E_BACK)]])
        with open(PHOTO_PATH, 'rb') as f:
            await msg.reply_photo(f, caption=caption, reply_markup=kb_m, parse_mode=ParseMode.HTML)
        return
    
    # Проверяем, не привязан ли уже канал
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA busy_timeout=5000")
            async with db.execute("SELECT owner_id, title FROM channels WHERE channel_id=?", (ch_id,)) as cur:
                row = await cur.fetchone()
    except Exception:
        row = None
    
    if row:
        owner_id, title = row
        if owner_id == uid:
            caption = f"{pe(E_SUCCESS, '✅')} <b>Этот канал уже привязан к вам!</b>\n\nНазвание: {title}\nID: <code>{ch_id}</code>"
        else:
            caption = f"{pe(E_WARNING, '⚠️')} <b>Этот канал уже привязан!</b>\n\nНазвание: {title}\nВладелец: {owner_id}\nID: <code>{ch_id}</code>"
        kb_m = InlineKeyboardMarkup([[kb("В меню", data="menu:main", icon_id=E_BACK)]])
        with open(PHOTO_PATH, 'rb') as f:
            await msg.reply_photo(f, caption=caption, reply_markup=kb_m, parse_mode=ParseMode.HTML)
        waiting_for_forward.pop(uid, None)
        return
    
    # Проверяем права бота
    with open(PHOTO_PATH, 'rb') as f:
        status_msg = await msg.reply_photo(f, caption=f"{pe(E_CHECK, '⏳')} <b>Проверяю права бота в канале...</b>", parse_mode=ParseMode.HTML)
    
    await asyncio.sleep(1)
    
    try:
        bot_member = await context.bot.get_chat_member(ch_id, context.bot.id)
        logger.info(f"Статус бота в канале {ch_id}: {bot_member.status}")
        
        is_admin = bot_member.status == ChatMemberStatus.ADMINISTRATOR
        can_restrict = getattr(bot_member, 'can_restrict_members', False)
        can_invite = getattr(bot_member, 'can_invite_users', False)
        
        if not is_admin or not can_restrict or not can_invite:
            raise Exception("Нет прав")
        
        # Добавляем канал в базу
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("PRAGMA busy_timeout=5000")
                await db.execute("INSERT OR REPLACE INTO channels (channel_id,owner_id,title,nakrutka_count) VALUES (?,?,?,0)", (ch_id, uid, chat_title or "Без названия"))
                await db.commit()
        except Exception as e:
            logger.error(f"DB error: {e}")
        
        caption = f"{pe(E_SUCCESS, '✅')} <b>Канал успешно привязан!</b>\n\nНазвание: {chat_title}\nID: <code>{ch_id}</code>\n\n{pe(E_NOTIFY, '🔔')} <b>Уведомления о накрутках будут приходить в этот чат</b>"
        kb_m = InlineKeyboardMarkup([[kb("Спасибо, хорошо", data="menu:main", icon_id=E_OK)]])
        await status_msg.edit_caption(caption=caption, reply_markup=kb_m, parse_mode=ParseMode.HTML)
        waiting_for_forward.pop(uid, None)
        
    except Exception as e:
        logger.error(f"Ошибка проверки прав: {e}")
        caption = f"{pe(E_FAIL, '❌')} <b>Бот не админ или нет прав!</b>\n\nУбедитесь, что бот добавлен в канал с правами:\n• Блокировка пользователей\n• Пригласительные ссылки\n\nЗатем перешлите сообщение из канала снова."
        kb_m = InlineKeyboardMarkup([
            [kb("Добавить бота в канал", url=f"https://t.me/{BOT_USERNAME}?startchannel&admin=invite_users", icon_id=E_ADD_BOT)],
            [kb("Назад", data="menu:main", icon_id=E_BACK)]
        ])
        await status_msg.edit_caption(caption=caption, reply_markup=kb_m, parse_mode=ParseMode.HTML)

async def on_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        ev = update.chat_member
        if not ev or not ev.new_chat_member:
            return

        if ev.chat.type not in ['channel', 'supergroup']:
            return

        old_status = ev.old_chat_member.status if ev.old_chat_member else None
        new_status = ev.new_chat_member.status

        if new_status == ChatMemberStatus.MEMBER and old_status == ChatMemberStatus.LEFT:
            pass
        elif new_status != ChatMemberStatus.MEMBER:
            return

        chat_id = ev.chat.id

        if chat_id in PROTECTED_CHATS:
            return

        if chat_id >= 0:
            return

        user = ev.new_chat_member.user

        if user.is_bot:
            return

        if user.id == context.bot.id:
            return

        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("PRAGMA busy_timeout=5000")
                async with db.execute("SELECT owner_id, title FROM channels WHERE channel_id=?", (chat_id,)) as cur:
                    row = await cur.fetchone()
                    if not row:
                        return
                    owner_id, chat_title = row
                    if owner_id is None:
                        return
        except Exception as e:
            logger.error(f"on_join db error: {e}")
            return

        now = time.time()

        if chat_id not in joins_cache:
            joins_cache[chat_id] = {}

        prune_window = max(BAN_SUSTAINED_WINDOW, RATIO_WINDOW)
        joins_cache[chat_id] = {u: t for u, t in joins_cache[chat_id].items() if now - t <= prune_window}
        nakrutka_last_reset[chat_id] = now

        if len(joins_cache[chat_id]) >= MAX_JOINS_PER_CHANNEL:
            oldest = sorted(joins_cache[chat_id].items(), key=lambda x: x[1])
            keep = oldest[len(oldest) - MAX_JOINS_PER_CHANNEL + 1:]
            joins_cache[chat_id] = dict(keep)

        if user.id in joins_cache[chat_id]:
            return

        joins_cache[chat_id][user.id] = now

        total_joined = len(joins_cache[chat_id])
        should_ban_all = False

        member_count = await get_chat_member_count(chat_id, context)

        if member_count < 500:
            threshold, window = BAN_POOL_SMALL
        elif member_count < 5000:
            threshold, window = BAN_POOL_MEDIUM
        else:
            threshold, window = BAN_POOL_LARGE

        if total_joined >= threshold:
            recent = [t for t in joins_cache[chat_id].values() if now - t <= window]
            if len(recent) >= threshold:
                should_ban_all = True

        if not should_ban_all and total_joined >= BAN_SUSTAINED_THRESHOLD:
            times = sorted(joins_cache[chat_id].values())
            first_join_time = times[0]
            elapsed = now - first_join_time
            if elapsed <= BAN_SUSTAINED_WINDOW:
                should_ban_all = True

        if not should_ban_all and total_joined >= max(3, int(member_count * RATIO_THRESHOLD)):
            ratio_recent = [t for t in joins_cache[chat_id].values() if now - t <= RATIO_WINDOW]
            if len(ratio_recent) >= max(3, int(member_count * RATIO_THRESHOLD)):
                should_ban_all = True

        if not should_ban_all and total_joined >= BURST_MIN_USERS:
            burst_recent = [(uid, t) for uid, t in joins_cache[chat_id].items() if now - t <= BURST_WINDOW]
            if len(burst_recent) >= BURST_MIN_USERS:
                ids_in_burst = [uid for uid, _ in burst_recent]
                for i in range(len(ids_in_burst)):
                    for j in range(i + 1, len(ids_in_burst)):
                        if abs(ids_in_burst[i] - ids_in_burst[j]) <= BURST_ID_GAP:
                            should_ban_all = True
                            break
                    if should_ban_all:
                        break

        if should_ban_all:
            to_ban = list(joins_cache[chat_id].keys())
            joins_cache[chat_id] = {}

            admin_ids = await get_chat_admins(context, chat_id)

            async def ban_one(target_uid: int, retries: int = 3) -> bool:
                """Бан + разбан ОДНОГО юзера строго последовательно, с ретраями на RetryAfter."""
                for attempt in range(retries):
                    try:
                        await context.bot.ban_chat_member(chat_id, target_uid)
                        break
                    except RetryAfter as e:
                        await asyncio.sleep(e.retry_after + 0.1)
                        continue
                    except Forbidden:
                        raise
                    except TelegramError as e:
                        logger.warning(f"ban_chat_member failed for {target_uid} in {chat_id}: {e}")
                        return False
                else:
                    return False

                for attempt in range(retries):
                    try:
                        await context.bot.unban_chat_member(chat_id, target_uid)
                        return True
                    except RetryAfter as e:
                        await asyncio.sleep(e.retry_after + 0.1)
                        continue
                    except Forbidden:
                        raise
                    except TelegramError as e:
                        logger.warning(f"unban_chat_member failed for {target_uid} in {chat_id}: {e}")
                        return True
                return True

            banned_count = 0
            bot_kicked = False
            for target_uid in to_ban:
                if target_uid in admin_ids:
                    continue
                try:
                    ok = await ban_one(target_uid)
                    if ok:
                        banned_count += 1
                except Forbidden:
                    logger.warning(f"Bot removed from channel {chat_id}")
                    bot_kicked = True
                    break
                except Exception as e:
                    logger.error(f"ban_one unexpected error for {target_uid}: {e}")
                # небольшая пауза между юзерами, чтобы не словить flood control Telegram
                await asyncio.sleep(0.1)

            if bot_kicked:
                try:
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("PRAGMA busy_timeout=5000")
                        await db.execute("DELETE FROM channels WHERE channel_id=?", (chat_id,))
                        await db.commit()
                except Exception:
                    pass
                return

            if banned_count == 0:
                return

            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("UPDATE channels SET nakrutka_count=nakrutka_count+? WHERE channel_id=?", (banned_count, chat_id))
                    async with db.execute("SELECT nakrutka_count FROM channels WHERE channel_id=?", (chat_id,)) as cur:
                        row = await cur.fetchone()
                        total_banned = row[0] if row else banned_count
                    await db.commit()
            except Exception as e:
                logger.error(f"on_join update db error: {e}")
                total_banned = banned_count

            if chat_id in nakrutka_last_notify and now - nakrutka_last_notify[chat_id] < NOTIFY_COOLDOWN:
                return

            nakrutka_last_notify[chat_id] = now

            chat_link = await get_chat_link(context, chat_id)

            tz = time.strftime("%H:%M:%S", time.localtime(now))
            alert = (
                f"{pe(E_NAKRUTKA, '⭐')} <b>Обнаружена подозрительная активность</b> {pe(E_NAKRUTKA, '⭐')}\n\n"
                f"{pe(E_NAKRUTKA, '⭐')} {chat_title}\n"
                f"{pe(E_NAKRUTKA, '⭐')} {chat_link}\n"
                f"{pe(E_CLOCK, '🎧')} Время: <b>{tz} МСК</b>\n\n"
                f"{pe(E_TRASH, '➡️')} Заблокировано: <b>{banned_count}</b>\n"
                f"{pe(E_TRASH, '➡️')} Всего удалено: <b>{total_banned}</b>"
            )

            try:
                if chat_id in nakrutka_messages:
                    msg_id = nakrutka_messages[chat_id]
                    if msg_id:
                        try:
                            await context.bot.edit_message_caption(chat_id=owner_id, message_id=msg_id, caption=alert, parse_mode=ParseMode.HTML)
                        except Exception:
                            try:
                                await context.bot.delete_message(owner_id, msg_id)
                            except Exception:
                                pass
                            with open(PHOTO_PATH, 'rb') as f:
                                msg = await context.bot.send_photo(owner_id, f, caption=alert, parse_mode=ParseMode.HTML)
                            nakrutka_messages[chat_id] = msg.message_id
                else:
                    with open(PHOTO_PATH, 'rb') as f:
                        msg = await context.bot.send_photo(owner_id, f, caption=alert, parse_mode=ParseMode.HTML)
                    nakrutka_messages[chat_id] = msg.message_id
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after)
                try:
                    with open(PHOTO_PATH, 'rb') as f:
                        msg = await context.bot.send_photo(owner_id, f, caption=alert, parse_mode=ParseMode.HTML)
                    nakrutka_messages[chat_id] = msg.message_id
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"Failed to notify owner {owner_id}: {e}")

    except Exception as e:
        logger.error(f"on_join error: {e}")

async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(update.effective_user.id):
        return
    act = q.data.split(":")[1]

    if act == "stats_binds":
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("PRAGMA busy_timeout=5000")
                async with db.execute("SELECT title,channel_id FROM channels") as cur:
                    chs = await cur.fetchall()
        except Exception:
            chs = []
        txt = f"{pe(E_STATS, '📊')} <b>Привязки:</b>\n\n"
        if not chs:
            txt += "Нет."
        else:
            for t, cid in chs:
                link = await get_chat_link(context, cid)
                line = f"{t} | {link}\n"
                if len(txt) + len(line) > 3500:
                    txt += "..."
                    break
                txt += line
        kb_m = InlineKeyboardMarkup([[kb("Назад", data="menu:admin", icon_id=E_BACK)]])
        await q.message.edit_caption(caption=txt, reply_markup=kb_m, parse_mode=ParseMode.HTML)

    elif act == "stats_nakrutka":
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("PRAGMA busy_timeout=5000")
                async with db.execute("SELECT title,channel_id,nakrutka_count FROM channels WHERE nakrutka_count>0 ORDER BY nakrutka_count DESC") as cur:
                    chs = await cur.fetchall()
        except Exception:
            chs = []
        txt = f"{pe(E_BROAD, '📈')} <b>Накрутки:</b>\n\n"
        if not chs:
            txt += "Нет."
        else:
            for t, cid, cnt in chs:
                link = await get_chat_link(context, cid)
                line = f"{t} | {link} | Всего удалено: {cnt}\n"
                if len(txt) + len(line) > 3500:
                    txt += "..."
                    break
                txt += line
        kb_m = InlineKeyboardMarkup([[kb("Назад", data="menu:admin", icon_id=E_BACK)]])
        await q.message.edit_caption(caption=txt, reply_markup=kb_m, parse_mode=ParseMode.HTML)

    elif act == "ban":
        await q.message.edit_caption(caption="Отправьте ID для бана:")
        return WAIT_BAN
    elif act == "unban":
        await q.message.edit_caption(caption="Отправьте ID для разбана:")
        return WAIT_UNBAN
    elif act == "broadcast":
        await q.message.edit_caption(caption="Отправьте текст для рассылки:")
        return WAIT_BROADCAST
    elif act == "add_slots":
        await q.message.edit_caption(caption="Отправьте ID пользователя и количество слотов (через пробел):\nПример: 123456789 5")
        return WAIT_ADD_SLOTS
    return ConversationHandler.END

async def proc_add_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.delete()
    try:
        parts = update.message.text.split()
        target_uid = int(parts[0])
        slots = int(parts[1])
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("INSERT OR IGNORE INTO users (user_id,banned,max_channels) VALUES (?,0,?)", (target_uid, slots))
            await db.execute("UPDATE users SET max_channels=? WHERE user_id=?", (slots, target_uid))
            await db.commit()
        await update.message.reply_text(f"Пользователю <code>{target_uid}</code> выдано {slots} слотов.", reply_markup=main_kb(update.effective_user.id), parse_mode=ParseMode.HTML)
    except Exception:
        await update.message.reply_text("Ошибка. Формат: ID количество", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

async def proc_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.delete()
    try:
        tid = int(update.message.text)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("INSERT OR IGNORE INTO users (user_id,banned,max_channels) VALUES (?,1,?)", (tid, MAX_CHANNELS_DEFAULT))
            await db.execute("UPDATE users SET banned=1 WHERE user_id=?", (tid,))
            await db.commit()
        banned_cache.add(tid)
        await update.message.reply_text(f"<code>{tid}</code> забанен.", reply_markup=main_kb(update.effective_user.id), parse_mode=ParseMode.HTML)
    except Exception:
        await update.message.reply_text("Ошибка.", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

async def proc_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.delete()
    try:
        tid = int(update.message.text)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("INSERT OR IGNORE INTO users (user_id,banned,max_channels) VALUES (?,0,?)", (tid, MAX_CHANNELS_DEFAULT))
            await db.execute("UPDATE users SET banned=0 WHERE user_id=?", (tid,))
            await db.commit()
        banned_cache.discard(tid)
        await update.message.reply_text(f"<code>{tid}</code> разбанен.", reply_markup=main_kb(update.effective_user.id), parse_mode=ParseMode.HTML)
    except Exception:
        await update.message.reply_text("Ошибка.", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

async def proc_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.delete()
    if not update.message.text:
        await update.message.reply_text("Текст не может быть пустым.", reply_markup=main_kb(update.effective_user.id))
        return ConversationHandler.END
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA busy_timeout=5000")
            async with db.execute("SELECT user_id FROM users WHERE banned=0") as cur:
                users = await cur.fetchall()
    except Exception:
        await update.message.reply_text("Ошибка базы данных.", reply_markup=main_kb(update.effective_user.id))
        return ConversationHandler.END
    cnt = 0
    for (uid,) in users:
        try:
            with open(PHOTO_PATH, 'rb') as f:
                await context.bot.send_photo(uid, f, caption=update.message.text, parse_mode=ParseMode.HTML)
            cnt += 1
            await asyncio.sleep(0.05)
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                with open(PHOTO_PATH, 'rb') as f:
                    await context.bot.send_photo(uid, f, caption=update.message.text, parse_mode=ParseMode.HTML)
                cnt += 1
            except Exception:
                pass
        except Exception:
            pass
    await update.message.reply_text(f"Доставлено: {cnt}.", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

async def clean_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != SUPER_ADMIN:
        return
    if not context.args:
        await update.message.reply_text("Использование: /clean ID_канала")
        return
    try:
        ch_id = int(context.args[0])
    except Exception:
        await update.message.reply_text("Неверный ID канала")
        return
    if ch_id in PROTECTED_CHATS:
        await update.message.reply_text(f"{pe(E_WARNING, '🚫')} Этот канал защищён от очистки!")
        return
    try:
        chat = await context.bot.get_chat(ch_id)
        chat_title = chat.title or "Без названия"
    except Exception:
        await update.message.reply_text("Ошибка доступа к каналу.")
        return
    try:
        bot_member = await context.bot.get_chat_member(ch_id, context.bot.id)
        if bot_member.status not in ["administrator", "creator"] or not bot_member.can_restrict_members:
            await update.message.reply_text(f"{pe(E_FAIL, '❌')} У бота нет прав администратора.")
            return
    except Exception:
        await update.message.reply_text(f"{pe(E_FAIL, '❌')} Ошибка проверки прав.")
        return
    status_msg = await update.message.reply_text(f"{pe(E_CHECK, '⏳')} Начинаю очистку канала {chat_title}...\n\nУдалено: 0")
    admin_ids = set()
    try:
        admins = await context.bot.get_chat_administrators(ch_id)
        admin_ids = {admin.user.id for admin in admins}
    except Exception:
        pass
    try:
        member_count = await context.bot.get_chat_member_count(ch_id)
    except Exception:
        member_count = 0
    if member_count == 0:
        await status_msg.edit_text("⚠️ Не удалось получить количество участников.")
        return
    kicked_count = 0
    known_ids = set()
    if ch_id in joins_cache:
        for user_id in joins_cache[ch_id]:
            if user_id not in admin_ids:
                known_ids.add(user_id)
    for cache_ch_id, cache_users in joins_cache.items():
        if cache_ch_id != ch_id:
            for user_id in cache_users:
                known_ids.add(user_id)
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA busy_timeout=5000")
            async with db.execute("SELECT user_id FROM users") as cur:
                rows = await cur.fetchall()
            for row in rows:
                known_ids.add(row[0])
    except Exception:
        pass
    for user_id in list(known_ids):
        if user_id in admin_ids:
            continue
        try:
            member = await context.bot.get_chat_member(ch_id, user_id)
            if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                await context.bot.ban_chat_member(ch_id, user_id)
                await context.bot.unban_chat_member(ch_id, user_id)
                kicked_count += 1
                if kicked_count % 10 == 0:
                    try:
                        await status_msg.edit_text(f"{pe(E_CHECK, '⏳')} Очистка канала {chat_title}...\n\nУдалено: {kicked_count}")
                    except Exception:
                        pass
                await asyncio.sleep(0.05)
        except Exception:
            pass
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("INSERT INTO clean_logs (admin_id, chat_id, chat_title, cleaned_count, timestamp) VALUES (?,?,?,?,?)", (uid, ch_id, chat_title, kicked_count, time.time()))
            await db.commit()
    except Exception:
        pass
    await status_msg.edit_text(f"{pe(E_SUCCESS, '✅')} Канал успешно очищен!\n\nУдалено пользователей: {kicked_count}")
    for owner_id in OWNER_IDS:
        try:
            await context.bot.send_message(owner_id, f"{pe(E_TRASH, '🗑')} Очистка канала\n\nКанал: {chat_title} ({ch_id})\nУдалено: {kicked_count}\nАдминистратор: {update.effective_user.id}")
        except Exception:
            pass

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA busy_timeout=5000")
            async with db.execute("SELECT title, nakrutka_count FROM channels WHERE owner_id=?", (uid,)) as cur:
                rows = await cur.fetchall()
    except Exception:
        rows = []
    if not rows:
        await update.message.reply_text("У вас нет привязанных каналов.")
        return
    txt = f"{pe(E_STATS, '📊')} <b>Ваша статистика:</b>\n\n"
    for title, cnt in rows:
        txt += f"• {title}: <b>{cnt}</b> накруток удалено\n"
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Команды:\n/start - Главное меню\n/stats - Статистика\n/clean ID - Очистка канала (админ)\n/help - Помощь")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Update {update} caused error {context.error}")

def main():
    check_photo()
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    async def post_init(app: Application):
        await init_banned_cache()
        asyncio.create_task(cleanup_cache())

    app.post_init = post_init

    adm_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_handler, pattern="^admin:ban$"),
            CallbackQueryHandler(admin_handler, pattern="^admin:unban$"),
            CallbackQueryHandler(admin_handler, pattern="^admin:broadcast$"),
            CallbackQueryHandler(admin_handler, pattern="^admin:add_slots$")
        ],
        states={
            WAIT_BAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, proc_ban)],
            WAIT_UNBAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, proc_unban)],
            WAIT_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, proc_broadcast)],
            WAIT_ADD_SLOTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, proc_add_slots)]
        },
        fallbacks=[],
        name="admin_conversation",
        conversation_timeout=300
    )

    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clean", clean_channel_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(check_sub_handler, pattern="^check_sub$"))
    app.add_handler(adm_conv)
    app.add_handler(CallbackQueryHandler(ask_disc, pattern="^ask_disc:"))
    app.add_handler(CallbackQueryHandler(do_disc, pattern="^do_disc:"))
    app.add_handler(CallbackQueryHandler(admin_handler, pattern="^admin:stats_binds$"))
    app.add_handler(CallbackQueryHandler(admin_handler, pattern="^admin:stats_nakrutka$"))
    app.add_handler(CallbackQueryHandler(menu_handler, pattern="^menu:"))
    app.add_handler(ChatMemberHandler(on_join, ChatMemberHandler.CHAT_MEMBER))
    
    # ЕДИНЫЙ ОБРАБОТЧИК ВСЕХ СООБЩЕНИЙ
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, process_forward))

    print("✅ Бот запущен! Пересылайте сообщения из каналов!")

    def signal_handler(sig, frame):
        print("Остановка бота...")
        app.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    except KeyboardInterrupt:
        print("Бот остановлен.")

if __name__ == "__main__":
    main()