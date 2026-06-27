import asyncio
import logging
import os
import random
import time
from typing import Optional, Dict, List, Tuple

import asyncpg
import redis.asyncio as aioredis
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.redis import RedisStorage, DefaultKeyBuilder
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, LabeledPrice,
    PreCheckoutQuery, BotCommand
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from pyrogram import Client
from pyrogram.errors import (
    FloodWait, SessionPasswordNeeded,
    UserAlreadyParticipant, InviteHashExpired
)
from pyrogram.raw.functions.messages import GetBotCallbackAnswer
from aiohttp import web
import aiohttp

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('autoferma_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('AutoFerma')

class Config:
    BOT_TOKEN: str = os.getenv('BOT_TOKEN', '8960090061:AAHmGZ0WM7OTUTIdrWOYLaqETuWp_l65r14')
    ADMIN_IDS: List[int] = [int(x) for x in os.getenv('ADMIN_IDS', '').split(',') if x]
    API_ID: int = int(os.getenv('API_ID', '25874957'))
    API_HASH: str = os.getenv('API_HASH', 'c89ef6fd9ba5c8a479abb1f4d2de248d')
    
    POSTGRES_HOST: str = os.getenv('POSTGRES_HOST', 'localhost')
    POSTGRES_PORT: int = int(os.getenv('POSTGRES_PORT', '5432'))
    POSTGRES_USER: str = os.getenv('POSTGRES_USER', 'autoferma')
    POSTGRES_PASSWORD: str = os.getenv('POSTGRES_PASSWORD', '')
    POSTGRES_DB: str = os.getenv('POSTGRES_DB', 'autoferma')
    
    REDIS_HOST: str = os.getenv('REDIS_HOST', 'localhost')
    REDIS_PORT: int = int(os.getenv('REDIS_PORT', '6379'))
    REDIS_PASSWORD: str = os.getenv('REDIS_PASSWORD', '')
    REDIS_DB: int = int(os.getenv('REDIS_DB', '0'))
    
    ENCRYPTION_KEY: str = os.getenv('ENCRYPTION_KEY', Fernet.generate_key().decode())
    
    DEFAULT_DELAY_BETWEEN: int = int(os.getenv('DEFAULT_DELAY_BETWEEN', '5'))
    MIN_DELAY_ACTIONS: float = float(os.getenv('MIN_DELAY_ACTIONS', '0.5'))
    MAX_DELAY_ACTIONS: float = float(os.getenv('MAX_DELAY_ACTIONS', '2.5'))
    MAX_RETRIES: int = int(os.getenv('MAX_RETRIES', '3'))
    RATE_LIMIT_PER_MINUTE: int = int(os.getenv('RATE_LIMIT_PER_MINUTE', '10'))
    WEB_PORT: int = int(os.getenv('WEB_PORT', '8080'))
    MAX_CONSECUTIVE_FAILS: int = int(os.getenv('MAX_CONSECUTIVE_FAILS', '5'))
    FAIL_PAUSE_SECONDS: int = int(os.getenv('FAIL_PAUSE_SECONDS', '60'))

config = Config()
fernet = Fernet(config.ENCRYPTION_KEY.encode())

class Database:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None
    
    async def connect(self):
        self.pool = await asyncpg.create_pool(
            host=config.POSTGRES_HOST,
            port=config.POSTGRES_PORT,
            user=config.POSTGRES_USER,
            password=config.POSTGRES_PASSWORD,
            database=config.POSTGRES_DB,
            min_size=5,
            max_size=20
        )
        await self._init_tables()
        logger.info("PostgreSQL подключён")
    
    async def _init_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT DEFAULT '',
                    first_name TEXT DEFAULT '',
                    role TEXT DEFAULT 'user',
                    balance DOUBLE PRECISION DEFAULT 0.0,
                    lolz_token TEXT DEFAULT '',
                    total_spent DOUBLE PRECISION DEFAULT 0.0,
                    total_users_loaded INT DEFAULT 0,
                    is_banned BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                
                CREATE TABLE IF NOT EXISTS bots (
                    bot_id SERIAL PRIMARY KEY,
                    owner_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                    bot_username TEXT NOT NULL,
                    bot_link TEXT NOT NULL,
                    price_per_user DOUBLE PRECISION DEFAULT 1.5,
                    delay_between INT DEFAULT 5,
                    is_active BOOLEAN DEFAULT TRUE,
                    custom_steps TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT NOW()
                );
                
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id SERIAL PRIMARY KEY,
                    login TEXT NOT NULL,
                    password TEXT DEFAULT '',
                    status TEXT DEFAULT 'free',
                    source TEXT DEFAULT 'manual',
                    lolz_order_id INT DEFAULT NULL,
                    added_by BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
                    created_at TIMESTAMP DEFAULT NOW(),
                    used_at TIMESTAMP DEFAULT NULL
                );
                
                CREATE TABLE IF NOT EXISTS proxies (
                    proxy_id SERIAL PRIMARY KEY,
                    proxy_type TEXT DEFAULT 'socks5',
                    host TEXT NOT NULL,
                    port INT NOT NULL,
                    username TEXT DEFAULT '',
                    password TEXT DEFAULT '',
                    is_active BOOLEAN DEFAULT TRUE,
                    usage_count INT DEFAULT 0,
                    last_used TIMESTAMP DEFAULT NULL
                );
                
                CREATE TABLE IF NOT EXISTS sessions_log (
                    log_id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                    bot_id INT REFERENCES bots(bot_id) ON DELETE SET NULL,
                    account_id INT REFERENCES accounts(account_id) ON DELETE SET NULL,
                    status TEXT DEFAULT 'pending',
                    error_reason TEXT DEFAULT '',
                    amount_charged DOUBLE PRECISION DEFAULT 0.0,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                
                CREATE TABLE IF NOT EXISTS payments (
                    payment_id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                    amount_rub DOUBLE PRECISION DEFAULT 0.0,
                    stars DOUBLE PRECISION DEFAULT 0.0,
                    status TEXT DEFAULT 'pending',
                    payment_method TEXT DEFAULT 'stars',
                    payload TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT NOW()
                );
                
                CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(status);
                CREATE INDEX IF NOT EXISTS idx_sessions_log_user ON sessions_log(user_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_log_created ON sessions_log(created_at);
                CREATE INDEX IF NOT EXISTS idx_bots_owner ON bots(owner_id);
                CREATE INDEX IF NOT EXISTS idx_proxies_active ON proxies(is_active);
                
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name='bots' AND column_name='custom_steps'
                    ) THEN
                        ALTER TABLE bots ADD COLUMN custom_steps TEXT DEFAULT '';
                    END IF;
                END $$;
            """)
            logger.info("Таблицы созданы/проверены")
    
    async def close(self):
        if self.pool:
            await self.pool.close()
            logger.info("PostgreSQL отключён")

db = Database()

class RedisClient:
    def __init__(self):
        self.client: Optional[aioredis.Redis] = None
    
    async def connect(self):
        self.client = aioredis.from_url(
            f"redis://:{config.REDIS_PASSWORD}@{config.REDIS_HOST}:{config.REDIS_PORT}/{config.REDIS_DB}",
            encoding="utf-8",
            decode_responses=True
        )
        await self.client.ping()
        logger.info("Redis подключён")
    
    async def close(self):
        if self.client:
            await self.client.close()
            logger.info("Redis отключён")

redis_client = RedisClient()

async def log_step(account_id: int, step: str, status: str = 'ok'):
    """Запись шага выполнения в Redis для отладки"""
    try:
        key = f"debug:acc:{account_id}:steps"
        await redis_client.client.lpush(key, f"{int(time.time())}|{step}|{status}")
        await redis_client.client.expire(key, 3600)
    except Exception as e:
        logger.warning(f"Ошибка записи в Redis: {e}")

async def human_delay(min_s: float = None, max_s: float = None):
    """Эмуляция человеческой задержки между действиями"""
    if min_s is None:
        min_s = config.MIN_DELAY_ACTIONS
    if max_s is None:
        max_s = config.MAX_DELAY_ACTIONS
    await asyncio.sleep(random.uniform(min_s, max_s))

async def get_next_proxy() -> Optional[Dict]:
    """Получение следующего прокси по round-robin"""
    async with db.pool.acquire() as conn:
        proxy = await conn.fetchrow(
            "SELECT * FROM proxies WHERE is_active = TRUE ORDER BY usage_count ASC, last_used ASC NULLS FIRST LIMIT 1"
        )
        if proxy:
            await conn.execute(
                "UPDATE proxies SET usage_count = usage_count + 1, last_used = NOW() WHERE proxy_id = $1",
                proxy['proxy_id']
            )
            return dict(proxy)
    return None

class LolzteamAPI:
    BASE_URL = "https://api.lolz.guru"
    
    @staticmethod
    async def fetch_accounts(api_token: str) -> List[Dict[str, str]]:
        headers = {"Authorization": f"Bearer {api_token}"}
        accounts = []
        
        async with aiohttp.ClientSession() as session:
            page = 1
            while True:
                try:
                    async with session.get(
                        f"{LolzteamAPI.BASE_URL}/market/orders",
                        headers=headers,
                        params={
                            "category": "telegram",
                            "status": "paid",
                            "page": page,
                            "per_page": 50
                        },
                        timeout=30
                    ) as response:
                        if response.status != 200:
                            logger.error(f"Lolzteam API error: {response.status}")
                            break
                        
                        data = await response.json()
                        orders = data.get('orders', [])
                        
                        if not orders:
                            break
                        
                        for order in orders:
                            acc_data = order.get('account_data', [])
                            for acc in acc_data:
                                if isinstance(acc, dict):
                                    accounts.append({
                                        'login': acc.get('login', ''),
                                        'password': acc.get('password', ''),
                                        'order_id': order.get('id', 0)
                                    })
                                elif isinstance(acc, str):
                                    login_pw = acc.split(':')
                                    if len(login_pw) >= 2:
                                        accounts.append({
                                            'login': login_pw[0],
                                            'password': login_pw[1],
                                            'order_id': order.get('id', 0)
                                        })
                        
                        page += 1
                        await asyncio.sleep(0.5)
                        
                except Exception as e:
                    logger.error(f"Lolzteam fetch error: {e}")
                    break
        
        return accounts

lolz_api = LolzteamAPI()

class AccountExecutor:
    def __init__(self):
        self.semaphore = asyncio.Semaphore(3)
    
    async def execute_single_account(
        self,
        account: Dict,
        bot_username: str,
        account_id: int,
        custom_steps: Optional[List[str]] = None
    ) -> Tuple[bool, str]:
        async with self.semaphore:
            client = None
            try:
                client_kwargs = {
                    'name': f"acc_{account_id}_{random.randint(1000, 9999)}",
                    'api_id': config.API_ID,
                    'api_hash': config.API_HASH,
                    'in_memory': True
                }
                
                proxy = await get_next_proxy()
                if proxy:
                    client_kwargs['proxy'] = {
                        'scheme': proxy['proxy_type'],
                        'hostname': proxy['host'],
                        'port': proxy['port']
                    }
                    if proxy['username']:
                        client_kwargs['proxy']['username'] = proxy['username']
                        client_kwargs['proxy']['password'] = proxy['password']
                    await log_step(account_id, 'proxy_assigned', f"{proxy['host']}:{proxy['port']}")
                
                client = Client(**client_kwargs)
                
                try:
                    await client.start()
                    await log_step(account_id, 'client_started', 'ok')
                except Exception as start_error:
                    logger.error(f"Ошибка client.start(): {start_error}")
                    await log_step(account_id, 'client_started', str(start_error))
                    return False, f"Ошибка запуска клиента: {str(start_error)}"
                
                phone_number = account['login']
                if not phone_number.startswith('+'):
                    phone_number = '+' + phone_number
                
                try:
                    sent_code = await client.send_code(phone_number)
                    await human_delay(1.0, 3.0)
                    await log_step(account_id, 'code_sent', 'ok')
                    
                    if account.get('password'):
                        try:
                            await client.sign_in(
                                phone_number=phone_number,
                                phone_code_hash=sent_code.phone_code_hash,
                                phone_code='00000'
                            )
                        except SessionPasswordNeeded:
                            await client.check_password(account['password'])
                        await log_step(account_id, 'logged_in', 'ok')
                    else:
                        raise Exception("Нет пароля для входа")
                    
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    return False, f"FloodWait при входе: {e.value}с"
                except Exception as login_error:
                    await log_step(account_id, 'login_failed', str(login_error))
                    return False, f"Ошибка входа: {str(login_error)}"
                
                await human_delay(1.0, 2.0)
                
                try:
                    await client.send_message(bot_username, "/start")
                    await log_step(account_id, 'start_sent', 'ok')
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    return False, f"FloodWait при /start: {e.value}с"
                except Exception as e:
                    await log_step(account_id, 'start_sent', str(e))
                    return False, f"Ошибка /start: {str(e)}"
                
                await human_delay(2.0, 4.0)
                
                if custom_steps:
                    for step_text in custom_steps:
                        await human_delay(1.0, 3.0)
                        try:
                            await client.send_message(bot_username, step_text)
                            await log_step(account_id, f'custom_step:{step_text}', 'ok')
                        except Exception as e:
                            await log_step(account_id, f'custom_step:{step_text}', str(e))
                            return False, f"Ошибка кастомного шага '{step_text}': {str(e)}"
                    
                    await human_delay(2.0, 4.0)
                    success = False
                    async for response in client.get_chat_history(bot_username, limit=3):
                        if response.from_user and not response.from_user.is_self:
                            text = (response.text or response.caption or "").lower()
                            if any(word in text for word in [
                                'успешно', 'success', 'готово', 'done',
                                'выполнено', 'completed'
                            ]):
                                success = True
                                break
                    
                    if success:
                        return True, "Успешно (кастомные шаги)"
                    else:
                        return True, "Кастомные шаги выполнены (без явного подтверждения)"
                
                response_found = False
                async for message in client.get_chat_history(bot_username, limit=1):
                    if message.from_user and message.from_user.is_self:
                        continue
                    
                    response_found = True
                    await log_step(account_id, 'got_response', 'ok')
                    reply_markup = message.reply_markup
                    
                    if reply_markup:
                        sponsor_urls = []
                        check_button = None
                        check_button_data = None
                        
                        if hasattr(reply_markup, 'inline_keyboard') and reply_markup.inline_keyboard:
                            for row in reply_markup.inline_keyboard:
                                for button in row:
                                    if button.url:
                                        sponsor_urls.append(button.url)
                                    elif button.text and any(
                                        word in button.text.lower() 
                                        for word in ['проверить', 'check', 'подтвердить', 'verify', 'подписался']
                                    ):
                                        check_button = button.text
                                        check_button_data = button.callback_data if hasattr(button, 'callback_data') else None
                        
                        elif hasattr(reply_markup, 'keyboard') and reply_markup.keyboard:
                            for row in reply_markup.keyboard:
                                for button in row:
                                    button_text = getattr(button, 'text', '') or str(button)
                                    if any(
                                        word in button_text.lower()
                                        for word in ['проверить', 'check', 'подтвердить', 'verify', 'подписался']
                                    ):
                                        check_button = button_text
                        
                        for url in sponsor_urls:
                            try:
                                if 't.me/' in url:
                                    chat_identifier = url.split('t.me/')[-1].split('/')[0]
                                    try:
                                        if chat_identifier.startswith('+'):
                                            await client.join_chat(chat_identifier)
                                        else:
                                            await client.join_chat(chat_identifier)
                                        await log_step(account_id, f'subscribed:{chat_identifier}', 'ok')
                                    except UserAlreadyParticipant:
                                        await log_step(account_id, f'already_joined:{chat_identifier}', 'ok')
                                    except InviteHashExpired:
                                        await log_step(account_id, f'invite_expired:{chat_identifier}', 'fail')
                                await human_delay(1.0, 2.0)
                            except FloodWait as e:
                                await asyncio.sleep(e.value)
                            except Exception as e:
                                logger.warning(f"Ошибка подписки на {url}: {e}")
                                await log_step(account_id, f'sub_error:{url}', str(e))
                        
                        if sponsor_urls:
                            await human_delay(1.0, 3.0)
                            async for msg in client.get_chat_history(bot_username, limit=3):
                                if msg.reply_markup and hasattr(msg.reply_markup, 'inline_keyboard'):
                                    for row in msg.reply_markup.inline_keyboard:
                                        for btn in row:
                                            if btn.text and any(w in btn.text.lower() for w in ['подписался', 'subscribed', 'продолжить', 'continue']):
                                                try:
                                                    if hasattr(btn, 'callback_data') and btn.callback_data:
                                                        peer = await client.resolve_peer(bot_username)
                                                        await client.invoke(GetBotCallbackAnswer(
                                                            peer=peer,
                                                            msg_id=msg.id,
                                                            data=btn.callback_data.encode()
                                                        ))
                                                    else:
                                                        await client.send_message(bot_username, btn.text)
                                                    await log_step(account_id, 'clicked_subscribed', 'ok')
                                                except Exception as e:
                                                    await log_step(account_id, 'clicked_subscribed', str(e))
                                                await human_delay(2.0, 3.0)
                                                break
                        
                        if check_button:
                            try:
                                if check_button_data:
                                    peer = await client.resolve_peer(bot_username)
                                    await client.invoke(GetBotCallbackAnswer(
                                        peer=peer,
                                        msg_id=message.id,
                                        data=check_button_data.encode()
                                    ))
                                else:
                                    await client.send_message(bot_username, check_button)
                                await log_step(account_id, 'clicked_check', 'ok')
                                
                                await human_delay(2.0, 4.0)
                                
                                second_check_found = False
                                async for response in client.get_chat_history(bot_username, limit=3):
                                    if response.from_user and not response.from_user.is_self:
                                        reply_markup2 = response.reply_markup
                                        if reply_markup2 and hasattr(reply_markup2, 'inline_keyboard') and reply_markup2.inline_keyboard:
                                            for row in reply_markup2.inline_keyboard:
                                                for button in row:
                                                    if button.text and any(
                                                        word in button.text.lower()
                                                        for word in ['выполнил', 'условия', 'готово', 'done', 'completed', 'продолжить']
                                                    ):
                                                        if hasattr(button, 'callback_data') and button.callback_data:
                                                            peer = await client.resolve_peer(bot_username)
                                                            await client.invoke(GetBotCallbackAnswer(
                                                                peer=peer,
                                                                msg_id=response.id,
                                                                data=button.callback_data.encode()
                                                            ))
                                                        else:
                                                            await client.send_message(bot_username, button.text)
                                                        second_check_found = True
                                                        await log_step(account_id, 'second_check_clicked', 'ok')
                                                        await human_delay(2.0, 3.0)
                                                        break
                                                if second_check_found:
                                                    break
                                        if second_check_found:
                                            break
                                
                                await human_delay(2.0, 4.0)
                                
                                success = False
                                async for response in client.get_chat_history(bot_username, limit=3):
                                    if response.from_user and not response.from_user.is_self:
                                        text = (response.text or response.caption or "").lower()
                                        if any(word in text for word in [
                                            'успешно', 'success', 'подписка подтверждена',
                                            'subscription confirmed', 'готово', 'done',
                                            'выполнено', 'completed', 'задание выполнено'
                                        ]):
                                            success = True
                                            break
                                
                                if success:
                                    await log_step(account_id, 'success_confirmed', 'ok')
                                else:
                                    await log_step(account_id, 'success_not_confirmed', 'maybe_ok')
                                
                                return True, "Успешно" if success else "Шаги выполнены (без явного подтверждения)"
                                    
                            except FloodWait as e:
                                await asyncio.sleep(e.value)
                                return False, f"FloodWait при проверке: {e.value}с"
                            except Exception as e:
                                await log_step(account_id, 'check_error', str(e))
                                return False, f"Ошибка проверки: {str(e)}"
                        else:
                            return False, "Не найдена кнопка проверки"
                        break
                
                if not response_found:
                    await log_step(account_id, 'no_response', 'fail')
                    return False, "Не получен ответ от бота"
                
                return True, "Успешно"
                
            except FloodWait as e:
                await asyncio.sleep(e.value)
                return False, f"FloodWait: {e.value}с"
            except Exception as e:
                await log_step(account_id, 'critical_error', str(e))
                return False, f"Критическая ошибка: {str(e)}"
            finally:
                if client:
                    try:
                        await client.stop()
                    except Exception:
                        pass
    
    async def run_nakrutka(
        self,
        user_id: int,
        bot_id: int,
        bot_username: str,
        bot_link: str,
        quantity: int,
        price_per_user: float,
        delay_between: int,
        custom_steps: Optional[List[str]] = None
    ) -> Dict[str, int]:
        success_count = 0
        fail_count = 0
        total_charged = 0.0
        consecutive_fails = 0
        
        async with db.pool.acquire() as conn:
            free_accounts = await conn.fetch(
                "SELECT * FROM accounts WHERE status = 'free' ORDER BY RANDOM() LIMIT $1",
                quantity
            )
            
            if len(free_accounts) < quantity:
                quantity = len(free_accounts)
            
            for account in free_accounts:
                account_dict = dict(account)
                
                await conn.execute(
                    "UPDATE accounts SET status = 'in_use' WHERE account_id = $1",
                    account_dict['account_id']
                )
                
                log_id = await conn.fetchval(
                    """INSERT INTO sessions_log (user_id, bot_id, account_id, status, amount_charged)
                       VALUES ($1, $2, $3, 'pending', $4) RETURNING log_id""",
                    user_id, bot_id, account_dict['account_id'], price_per_user
                )
                
                success, error_reason = await self.execute_single_account(
                    account_dict, bot_username, account_dict['account_id'], custom_steps
                )
                
                if success:
                    await conn.execute(
                        "UPDATE accounts SET status = 'used', used_at = NOW() WHERE account_id = $1",
                        account_dict['account_id']
                    )
                    await conn.execute(
                        "UPDATE sessions_log SET status = 'success', error_reason = '' WHERE log_id = $1",
                        log_id
                    )
                    await conn.execute(
                        "UPDATE users SET balance = balance - $1, total_spent = total_spent + $1, total_users_loaded = total_users_loaded + 1 WHERE user_id = $2",
                        price_per_user, user_id
                    )
                    success_count += 1
                    total_charged += price_per_user
                    consecutive_fails = 0
                else:
                    await conn.execute(
                        "UPDATE accounts SET status = 'free', used_at = NULL WHERE account_id = $1",
                        account_dict['account_id']
                    )
                    await conn.execute(
                        "UPDATE sessions_log SET status = 'fail', error_reason = $1 WHERE log_id = $2",
                        error_reason, log_id
                    )
                    fail_count += 1
                    consecutive_fails += 1
                    
                    if consecutive_fails >= config.MAX_CONSECUTIVE_FAILS:
                        logger.warning(f"{config.MAX_CONSECUTIVE_FAILS} ошибок подряд, пауза {config.FAIL_PAUSE_SECONDS} сек...")
                        await asyncio.sleep(config.FAIL_PAUSE_SECONDS)
                        consecutive_fails = 0
                
                await asyncio.sleep(delay_between)
        
        return {
            'success': success_count,
            'fail': fail_count,
            'total_charged': total_charged
        }

executor = AccountExecutor()

class UserStates(StatesGroup):
    adding_bot_username = State()
    adding_bot_price = State()
    adding_bot_delay = State()
    adding_bot_custom_steps = State()
    editing_bot = State()
    entering_quantity = State()
    entering_lolz_token = State()
    manual_add_accounts = State()
    entering_payment_amount = State()
    adding_proxy = State()

class Keyboards:
    @staticmethod
    def main_menu(user_id: int) -> ReplyKeyboardMarkup:
        kb = [
            [KeyboardButton(text="📥 Залить юзеров")],
            [KeyboardButton(text="🤖 Мои боты")],
            [KeyboardButton(text="📊 Отслеживание заходов")],
            [KeyboardButton(text="👤 Профиль")],
            [KeyboardButton(text="💳 Пополнить баланс")]
        ]
        if user_id in config.ADMIN_IDS:
            kb.append([KeyboardButton(text="⚙️ Админ-панель")])
        return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    
    @staticmethod
    def admin_menu() -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📦 Пул аккаунтов")],
                [KeyboardButton(text="🌐 Прокси")],
                [KeyboardButton(text="🔄 Lolzteam")],
                [KeyboardButton(text="👥 Пользователи")],
                [KeyboardButton(text="📊 Общая статистика")],
                [KeyboardButton(text="💰 Финансы")],
                [KeyboardButton(text="🔙 Главное меню")]
            ],
            resize_keyboard=True
        )

async def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS

async def get_pool_stats_message() -> Tuple[str, InlineKeyboardMarkup]:
    async with db.pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM accounts")
        free = await conn.fetchval("SELECT COUNT(*) FROM accounts WHERE status = 'free'")
        in_use = await conn.fetchval("SELECT COUNT(*) FROM accounts WHERE status = 'in_use'")
        used = await conn.fetchval("SELECT COUNT(*) FROM accounts WHERE status = 'used'")
        banned = await conn.fetchval("SELECT COUNT(*) FROM accounts WHERE status = 'banned'")
    
    text = (
        f"📦 <b>Пул аккаунтов:</b>\n\n"
        f"Всего: <b>{total}</b>\n"
        f"Свободно: <b>{free}</b>\n"
        f"В работе: <b>{in_use}</b>\n"
        f"Использовано: <b>{used}</b>\n"
        f"Забанено: <b>{banned}</b>"
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ Добавить вручную", callback_data="manual_add_accounts"))
    builder.row(InlineKeyboardButton(text="📋 Список аккаунтов", callback_data="list_accounts"))
    builder.row(InlineKeyboardButton(text="🗑 Очистить использованные", callback_data="clear_used_accounts"))
    
    return text, builder.as_markup()

bot = Bot(token=config.BOT_TOKEN)

async def setup_handlers(dp: Dispatcher):
    @dp.message(Command("start"))
    async def cmd_start(message: Message):
        user_id = message.from_user.id
        async with db.pool.acquire() as conn:
            existing = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
            if not existing:
                await conn.execute(
                    """INSERT INTO users (user_id, username, first_name, role)
                       VALUES ($1, $2, $3, $4)
                       ON CONFLICT (user_id) DO UPDATE SET
                       username = COALESCE($2, username),
                       first_name = COALESCE($3, first_name)""",
                    user_id,
                    message.from_user.username or '',
                    message.from_user.first_name or '',
                    'admin' if await is_admin(user_id) else 'user'
                )
        
        role_text = "👑 Администратор" if await is_admin(user_id) else "👤 Пользователь"
        await message.answer(
            f"🤖 <b>АвтоФерма v3.0</b>\n\n"
            f"Роль: {role_text}\n"
            f"Накрутка подписок в Telegram ботах\n\n"
            f"📌 Используйте меню для навигации:",
            reply_markup=Keyboards.main_menu(user_id)
        )

    @dp.message(F.text == "🔙 Главное меню")
    async def back_to_main(message: Message):
        await message.answer("📱 Главное меню:", reply_markup=Keyboards.main_menu(message.from_user.id))

    @dp.message(F.text == "❌ Отмена")
    async def cancel_action(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("❌ Действие отменено", reply_markup=Keyboards.main_menu(message.from_user.id))

    @dp.message(F.text == "📥 Залить юзеров")
    async def zalit_users_start(message: Message):
        user_id = message.from_user.id
        async with db.pool.acquire() as conn:
            bots = await conn.fetch(
                "SELECT * FROM bots WHERE owner_id = $1 AND is_active = TRUE",
                user_id
            )
        
        if not bots:
            await message.answer(
                "❌ У вас нет добавленных ботов.\n"
                "Сначала добавьте бота в разделе «🤖 Мои боты».",
                reply_markup=Keyboards.main_menu(user_id)
            )
            return
        
        builder = InlineKeyboardBuilder()
        for bot_item in bots:
            has_custom = "🔧" if bot_item['custom_steps'] else ""
            builder.row(InlineKeyboardButton(
                text=f"{has_custom} @{bot_item['bot_username']} — {bot_item['price_per_user']} ⭐ / юзер",
                callback_data=f"select_bot_{bot_item['bot_id']}"
            ))
        builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_zaliv"))
        
        await message.answer(
            "🤖 <b>Выберите бота для накрутки:</b>\n\n"
            "🔧 — есть кастомные шаги\n"
            "Боты-исполнители зайдут в выбранного бота,\n"
            "напишут /start и выполнят задания.",
            reply_markup=builder.as_markup()
        )

    @dp.callback_query(F.data.startswith("select_bot_"))
    async def select_bot_for_zaliv(callback: CallbackQuery, state: FSMContext):
        bot_id = int(callback.data.split("_")[2])
        
        async with db.pool.acquire() as conn:
            bot_data = await conn.fetchrow("SELECT * FROM bots WHERE bot_id = $1", bot_id)
            if not bot_data:
                await callback.answer("❌ Бот не найден", show_alert=True)
                return
            
            free_count = await conn.fetchval(
                "SELECT COUNT(*) FROM accounts WHERE status = 'free'"
            )
            
            user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", callback.from_user.id)
        
        max_available = min(
            free_count,
            int(user['balance'] / bot_data['price_per_user']) if bot_data['price_per_user'] > 0 else free_count
        )
        
        custom_steps = bot_data['custom_steps'].split(',') if bot_data['custom_steps'] else None
        if custom_steps:
            custom_steps = [s.strip() for s in custom_steps if s.strip()]
        
        await state.update_data(
            selected_bot_id=bot_id,
            bot_username=bot_data['bot_username'],
            bot_link=bot_data['bot_link'],
            price_per_user=bot_data['price_per_user'],
            delay_between=bot_data['delay_between'],
            custom_steps=custom_steps,
            max_available=max_available
        )
        
        await state.set_state(UserStates.entering_quantity)
        
        await callback.message.edit_text(
            f"📥 <b>Сколько юзеров загнать в @{bot_data['bot_username']}?</b>\n\n"
            f"💰 Цена за юзера: {bot_data['price_per_user']} ⭐\n"
            f"📦 Свободно аккаунтов: {free_count}\n"
            f"💳 Ваш баланс: {user['balance']} ⭐\n"
            f"📊 Максимум доступно: <b>{max_available} акк.</b>\n"
            f"{'🔧 Кастомные шаги: ' + ', '.join(custom_steps) if custom_steps else ''}\n\n"
            f"Введите количество:",
            reply_markup=None
        )
        await callback.answer()

    @dp.message(UserStates.entering_quantity)
    async def process_quantity(message: Message, state: FSMContext):
        try:
            quantity = int(message.text.strip())
            if quantity <= 0:
                raise ValueError
        except ValueError:
            await message.answer("❌ Введите целое положительное число.")
            return
        
        data = await state.get_data()
        max_available = data['max_available']
        
        if quantity > max_available:
            await message.answer(
                f"❌ Недостаточно ресурсов.\n"
                f"Максимум доступно: {max_available}\n"
                f"Пополните баланс или дождитесь загрузки аккаунтов.",
                reply_markup=Keyboards.main_menu(message.from_user.id)
            )
            await state.clear()
            return
        
        await state.clear()
        
        status_msg = await message.answer(
            f"⏳ <b>Запускаю накрутку...</b>\n"
            f"Бот: @{data['bot_username']}\n"
            f"Количество: {quantity}\n"
            f"Цена за юзера: {data['price_per_user']} ⭐\n\n"
            f"Ожидайте, процесс может занять время...",
            reply_markup=Keyboards.main_menu(message.from_user.id)
        )
        
        result = await executor.run_nakrutka(
            user_id=message.from_user.id,
            bot_id=data['selected_bot_id'],
            bot_username=data['bot_username'],
            bot_link=data['bot_link'],
            quantity=quantity,
            price_per_user=data['price_per_user'],
            delay_between=data['delay_between'],
            custom_steps=data.get('custom_steps')
        )
        
        async with db.pool.acquire() as conn:
            user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", message.from_user.id)
        
        await status_msg.edit_text(
            f"✅ <b>Готово!</b>\n\n"
            f"✅ Успешно: <b>{result['success']}</b>\n"
            f"❌ Провалено: <b>{result['fail']}</b>\n"
            f"💰 Списано: <b>{result['total_charged']:.1f} ⭐</b>\n"
            f"💳 Остаток на балансе: <b>{user['balance']:.1f} ⭐</b>"
        )

    @dp.callback_query(F.data == "cancel_zaliv")
    async def cancel_zaliv(callback: CallbackQuery):
        await callback.message.delete()
        await callback.answer("Отменено")

    @dp.message(F.text == "🤖 Мои боты")
    async def my_bots(message: Message):
        user_id = message.from_user.id
        async with db.pool.acquire() as conn:
            bots = await conn.fetch(
                "SELECT * FROM bots WHERE owner_id = $1 AND is_active = TRUE ORDER BY created_at DESC",
                user_id
            )
        
        if not bots:
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="➕ Добавить бота", callback_data="add_bot"))
            await message.answer(
                "🤖 У вас пока нет ботов.\nДобавьте первого бота для накрутки:",
                reply_markup=builder.as_markup()
            )
            return
        
        text = f"🤖 <b>Ваши боты ({len(bots)}):</b>\n\n"
        builder = InlineKeyboardBuilder()
        
        for i, bot_item in enumerate(bots, 1):
            has_custom = " 🔧" if bot_item['custom_steps'] else ""
            text += f"{i}. @{bot_item['bot_username']} — {bot_item['price_per_user']} ⭐{has_custom}\n"
            builder.row(
                InlineKeyboardButton(
                    text=f"✏️ @{bot_item['bot_username']}",
                    callback_data=f"edit_bot_{bot_item['bot_id']}"
                ),
                InlineKeyboardButton(
                    text="🗑",
                    callback_data=f"delete_bot_{bot_item['bot_id']}"
                )
            )
        
        builder.row(InlineKeyboardButton(text="➕ Добавить бота", callback_data="add_bot"))
        await message.answer(text, reply_markup=builder.as_markup())

    @dp.callback_query(F.data == "add_bot")
    async def add_bot_start(callback: CallbackQuery, state: FSMContext):
        await state.set_state(UserStates.adding_bot_username)
        await callback.message.edit_text(
            "➕ <b>Добавление бота</b>\n\n"
            "Введите юзернейм бота (без @):\n"
            "<i>Например: testbot</i>",
            reply_markup=None
        )
        await callback.answer()

    @dp.message(UserStates.adding_bot_username)
    async def process_bot_username(message: Message, state: FSMContext):
        username = message.text.strip().replace('@', '')
        
        if not username or ' ' in username:
            await message.answer("❌ Некорректный юзернейм. Попробуйте ещё раз.")
            return
        
        await state.update_data(bot_username=username)
        await state.set_state(UserStates.adding_bot_price)
        
        await message.answer(
            f"💰 Введите цену за 1 юзера (в звёздах):\n"
            f"<i>Например: 1.5</i>"
        )

    @dp.message(UserStates.adding_bot_price)
    async def process_bot_price(message: Message, state: FSMContext):
        try:
            price = float(message.text.strip())
            if price <= 0:
                raise ValueError
        except ValueError:
            await message.answer("❌ Введите положительное число.")
            return
        
        await state.update_data(price_per_user=price)
        await state.set_state(UserStates.adding_bot_delay)
        
        await message.answer(
            f"⏱ Введите задержку между заходами (в секундах):\n"
            f"<i>Например: 5</i>"
        )

    @dp.message(UserStates.adding_bot_delay)
    async def process_bot_delay(message: Message, state: FSMContext):
        try:
            delay = int(message.text.strip())
            if delay < 1:
                raise ValueError
        except ValueError:
            await message.answer("❌ Введите целое положительное число.")
            return
        
        await state.update_data(delay_between=delay)
        await state.set_state(UserStates.adding_bot_custom_steps)
        
        await message.answer(
            f"🔧 <b>Кастомные шаги (опционально)</b>\n\n"
            f"Введите тексты команд через запятую, которые бот-исполнитель отправит после /start.\n"
            f"Оставьте пустым для автоматического режима.\n\n"
            f"<i>Пример: подписался, проверить, готово</i>",
            reply_markup=Keyboards.cancel_kb()
        )

    @dp.message(UserStates.adding_bot_custom_steps)
    async def process_bot_custom_steps(message: Message, state: FSMContext):
        custom_steps_text = message.text.strip()
        custom_steps = None
        
        if custom_steps_text:
            custom_steps = ','.join(s.strip() for s in custom_steps_text.split(',') if s.strip())
        
        data = await state.get_data()
        username = data['bot_username']
        price = data['price_per_user']
        delay = data['delay_between']
        
        async with db.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO bots (owner_id, bot_username, bot_link, price_per_user, delay_between, custom_steps)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                message.from_user.id, username, f"https://t.me/{username}", price, delay,
                custom_steps or ''
            )
        
        await state.clear()
        
        result_text = f"✅ <b>Бот @{username} добавлен!</b>\n💰 Цена: {price} ⭐\n⏱ Задержка: {delay} сек"
        if custom_steps:
            result_text += f"\n🔧 Кастомные шаги: {custom_steps.replace(',', ', ')}"
        
        await message.answer(result_text, reply_markup=Keyboards.main_menu(message.from_user.id))

    @dp.callback_query(F.data.startswith("delete_bot_"))
    async def delete_bot(callback: CallbackQuery):
        bot_id = int(callback.data.split("_")[2])
        
        async with db.pool.acquire() as conn:
            bot_data = await conn.fetchrow(
                "SELECT * FROM bots WHERE bot_id = $1 AND owner_id = $2",
                bot_id, callback.from_user.id
            )
            if not bot_data:
                await callback.answer("❌ Бот не найден", show_alert=True)
                return
            
            await conn.execute("DELETE FROM bots WHERE bot_id = $1", bot_id)
        
        await callback.answer(f"Бот @{bot_data['bot_username']} удалён")
        await my_bots(callback.message)

    @dp.callback_query(F.data.startswith("edit_bot_"))
    async def edit_bot_menu(callback: CallbackQuery, state: FSMContext):
        bot_id = int(callback.data.split("_")[2])
        
        async with db.pool.acquire() as conn:
            bot_data = await conn.fetchrow("SELECT * FROM bots WHERE bot_id = $1", bot_id)
        
        if not bot_data:
            await callback.answer("❌ Бот не найден", show_alert=True)
            return
        
        await state.update_data(editing_bot_id=bot_id)
        await state.set_state(UserStates.editing_bot)
        
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(
            text=f"💰 Изменить цену (сейчас {bot_data['price_per_user']})",
            callback_data=f"edit_price_{bot_id}"
        ))
        builder.row(InlineKeyboardButton(
            text=f"⏱ Изменить задержку (сейчас {bot_data['delay_between']}с)",
            callback_data=f"edit_delay_{bot_id}"
        ))
        builder.row(InlineKeyboardButton(
            text=f"🔧 Изменить кастомные шаги",
            callback_data=f"edit_steps_{bot_id}"
        ))
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_bots"))
        
        await callback.message.edit_text(
            f"✏️ <b>Редактирование @{bot_data['bot_username']}</b>\n\n"
            f"Текущая цена: {bot_data['price_per_user']} ⭐\n"
            f"Текущая задержка: {bot_data['delay_between']} сек\n"
            f"Кастомные шаги: {bot_data['custom_steps'] or 'авторежим'}",
            reply_markup=builder.as_markup()
        )
        await callback.answer()

    @dp.callback_query(F.data == "back_to_bots")
    async def back_to_bots_list(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await callback.message.delete()
        await my_bots(callback.message)

    @dp.message(F.text == "📊 Отслеживание заходов")
    async def tracking_menu(message: Message):
        user_id = message.from_user.id
        
        async with db.pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM sessions_log WHERE user_id = $1", user_id)
            success = await conn.fetchval("SELECT COUNT(*) FROM sessions_log WHERE user_id = $1 AND status = 'success'", user_id)
            fail = await conn.fetchval("SELECT COUNT(*) FROM sessions_log WHERE user_id = $1 AND status = 'fail'", user_id)
            total_spent = await conn.fetchval("SELECT COALESCE(SUM(amount_charged), 0) FROM sessions_log WHERE user_id = $1 AND status = 'success'", user_id)
            
            by_bot = await conn.fetch(
                """SELECT b.bot_username, COUNT(*) as total,
                          SUM(CASE WHEN sl.status = 'success' THEN 1 ELSE 0 END) as suc,
                          SUM(CASE WHEN sl.status = 'fail' THEN 1 ELSE 0 END) as fl
                   FROM sessions_log sl
                   JOIN bots b ON sl.bot_id = b.bot_id
                   WHERE sl.user_id = $1
                   GROUP BY b.bot_username""",
                user_id
            )
            
            last_20 = await conn.fetch(
                """SELECT sl.*, b.bot_username
                   FROM sessions_log sl
                   JOIN bots b ON sl.bot_id = b.bot_id
                   WHERE sl.user_id = $1
                   ORDER BY sl.created_at DESC
                   LIMIT 20""",
                user_id
            )
        
        text = (
            f"📊 <b>Статистика за всё время:</b>\n"
            f"Всего заходов: <b>{total}</b>\n"
            f"✅ Успешно: <b>{success}</b>\n"
            f"❌ Провалено: <b>{fail}</b>\n"
            f"💰 Всего потрачено: <b>{total_spent:.1f} ⭐</b>\n"
        )
        
        if by_bot:
            text += "\n📋 <b>По ботам:</b>\n"
            for row in by_bot:
                text += f"@{row['bot_username']} — {row['total']} заходов ({row['suc']}/{row['fl']})\n"
        
        if last_20:
            text += "\n🕐 <b>Последние заходы:</b>\n"
            for i, row in enumerate(last_20[:10], 1):
                emoji = "✅" if row['status'] == 'success' else "❌"
                time_str = row['created_at'].strftime('%H:%M:%S')
                text += f"{i}. @{row['bot_username']} — {emoji} — {time_str}"
                if row['error_reason']:
                    text += f" ({row['error_reason'][:30]})"
                text += "\n"
        
        await message.answer(text, reply_markup=Keyboards.main_menu(user_id))

    @dp.message(F.text == "👤 Профиль")
    async def profile(message: Message):
        user_id = message.from_user.id
        
        async with db.pool.acquire() as conn:
            user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
            bots_count = await conn.fetchval("SELECT COUNT(*) FROM bots WHERE owner_id = $1", user_id)
            total_loaded = await conn.fetchval("SELECT COUNT(*) FROM sessions_log WHERE user_id = $1 AND status = 'success'", user_id)
        
        if not user:
            await message.answer("❌ Профиль не найден")
            return
        
        role_emoji = "👑 Администратор" if await is_admin(user_id) else "👤 Пользователь"
        
        await message.answer(
            f"👤 <b>Профиль</b>\n\n"
            f"🆔 ID: <code>{user['user_id']}</code>\n"
            f"👋 Имя: {user['first_name']}\n"
            f"📛 Username: @{user['username'] or 'нет'}\n"
            f"💳 Баланс: <b>{user['balance']:.1f} ⭐</b>\n"
            f"👑 Роль: {role_emoji}\n"
            f"💰 Потрачено всего: <b>{user['total_spent']:.1f} ⭐</b>\n"
            f"📊 Загружено юзеров: <b>{total_loaded}</b>\n"
            f"🤖 Ботов: <b>{bots_count}</b>\n"
            f"📅 Дата регистрации: {user['created_at'].strftime('%d.%m.%Y')}",
            reply_markup=Keyboards.main_menu(user_id)
        )

    @dp.message(F.text == "💳 Пополнить баланс")
    async def top_up_balance(message: Message):
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="100 ⭐", callback_data="topup_100"),
            InlineKeyboardButton(text="500 ⭐", callback_data="topup_500")
        )
        builder.row(
            InlineKeyboardButton(text="1000 ⭐", callback_data="topup_1000"),
            InlineKeyboardButton(text="2500 ⭐", callback_data="topup_2500")
        )
        builder.row(InlineKeyboardButton(text="💎 Своя сумма", callback_data="topup_custom"))
        
        await message.answer(
            "💳 <b>Пополнение баланса</b>\n\n"
            "Выберите сумму пополнения.\n"
            "Оплата через Telegram Stars.",
            reply_markup=builder.as_markup()
        )

    @dp.callback_query(F.data.startswith("topup_"))
    async def process_topup(callback: CallbackQuery, state: FSMContext):
        data = callback.data
        
        if data == "topup_custom":
            await state.set_state(UserStates.entering_payment_amount)
            await callback.message.edit_text("💎 Введите сумму пополнения (в звёздах):")
            await callback.answer()
            return
        
        amount = int(data.split("_")[1])
        await process_stars_payment(callback.message, amount, callback.from_user.id)
        await callback.answer()

    @dp.message(UserStates.entering_payment_amount)
    async def custom_topup(message: Message, state: FSMContext):
        try:
            amount = int(message.text.strip())
            if amount < 1:
                raise ValueError
        except ValueError:
            await message.answer("❌ Введите целое положительное число.")
            return
        
        await state.clear()
        await process_stars_payment(message, amount, message.from_user.id)

    async def process_stars_payment(message: Message, amount: int, user_id: int):
        try:
            await bot.send_invoice(
                chat_id=user_id,
                title="Пополнение баланса АвтоФерма",
                description=f"Пополнение баланса на {amount} ⭐",
                payload=f"autoferma_topup_{amount}",
                currency="XTR",
                prices=[LabeledPrice(label=f"{amount} Stars", amount=amount)]
            )
        except Exception as e:
            await message.answer(f"❌ Ошибка создания платежа: {e}")

    @dp.pre_checkout_query()
    async def process_pre_checkout(query: PreCheckoutQuery):
        await query.answer(ok=True)

    @dp.message(F.successful_payment)
    async def process_successful_payment(message: Message):
        payment = message.successful_payment
        payload = payment.invoice_payload
        
        if payload.startswith("autoferma_topup_"):
            amount = int(payload.split("_")[-1])
            
            async with db.pool.acquire() as conn:
                await conn.execute(
                    "UPDATE users SET balance = balance + $1 WHERE user_id = $2",
                    float(amount), message.from_user.id
                )
                await conn.execute(
                    """INSERT INTO payments (user_id, amount_rub, stars, status, payment_method, payload)
                       VALUES ($1, $2, $3, 'completed', 'stars', $4)""",
                    message.from_user.id, amount, amount, payload
                )
                
                user = await conn.fetchrow("SELECT balance FROM users WHERE user_id = $1", message.from_user.id)
            
            await message.answer(
                f"✅ <b>Баланс пополнен!</b>\n"
                f"💰 Сумма: {amount} ⭐\n"
                f"💳 Текущий баланс: <b>{user['balance']:.1f} ⭐</b>",
                reply_markup=Keyboards.main_menu(message.from_user.id)
            )

    @dp.message(F.text == "⚙️ Админ-панель")
    async def admin_panel(message: Message):
        if not await is_admin(message.from_user.id):
            await message.answer("❌ Доступ запрещён")
            return
        
        await message.answer("⚙️ <b>Админ-панель</b>", reply_markup=Keyboards.admin_menu())

    @dp.message(F.text == "📦 Пул аккаунтов")
    async def admin_pool_handler(message: Message):
        if not await is_admin(message.from_user.id):
            return
        
        text, markup = await get_pool_stats_message()
        await message.answer(text, reply_markup=markup)

    @dp.message(F.text == "🌐 Прокси")
    async def admin_proxies(message: Message):
        if not await is_admin(message.from_user.id):
            return
        
        async with db.pool.acquire() as conn:
            proxies = await conn.fetch("SELECT * FROM proxies ORDER BY proxy_id")
        
        text = f"🌐 <b>Прокси ({len(proxies)}):</b>\n\n"
        for p in proxies:
            status = "🟢" if p['is_active'] else "🔴"
            text += f"{status} {p['proxy_type']}://{p['host']}:{p['port']} (использован: {p['usage_count']} раз)\n"
        
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="➕ Добавить прокси", callback_data="add_proxy"))
        builder.row(InlineKeyboardButton(text="🗑 Удалить все", callback_data="clear_proxies"))
        
        await message.answer(text, reply_markup=builder.as_markup())

    @dp.callback_query(F.data == "add_proxy")
    async def add_proxy_start(callback: CallbackQuery, state: FSMContext):
        if not await is_admin(callback.from_user.id):
            return
        
        await state.set_state(UserStates.adding_proxy)
        await callback.message.edit_text(
            "🌐 <b>Добавление прокси</b>\n\n"
            "Отправьте данные в формате:\n"
            "<code>type host port username password</code>\n\n"
            "Пример:\n"
            "<code>socks5 192.168.1.1 1080 user pass</code>"
        )
        await callback.answer()

    @dp.message(UserStates.adding_proxy)
    async def process_add_proxy(message: Message, state: FSMContext):
        if not await is_admin(message.from_user.id):
            return
        
        parts = message.text.strip().split()
        if len(parts) < 3:
            await message.answer("❌ Формат: type host port [username] [password]")
            return
        
        proxy_type = parts[0]
        host = parts[1]
        try:
            port = int(parts[2])
        except ValueError:
            await message.answer("❌ Порт должен быть числом")
            return
        
        username = parts[3] if len(parts) > 3 else ''
        password = parts[4] if len(parts) > 4 else ''
        
        async with db.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO proxies (proxy_type, host, port, username, password)
                   VALUES ($1, $2, $3, $4, $5)""",
                proxy_type, host, port, username, password
            )
        
        await state.clear()
        await message.answer(f"✅ Прокси {proxy_type}://{host}:{port} добавлен", reply_markup=Keyboards.admin_menu())

    @dp.callback_query(F.data == "clear_proxies")
    async def clear_proxies(callback: CallbackQuery):
        if not await is_admin(callback.from_user.id):
            return
        
        async with db.pool.acquire() as conn:
            await conn.execute("DELETE FROM proxies")
        
        await callback.answer("Все прокси удалены")
        await callback.message.delete()
        await admin_proxies(callback.message)

    @dp.callback_query(F.data == "manual_add_accounts")
    async def manual_add_start(callback: CallbackQuery, state: FSMContext):
        await state.set_state(UserStates.manual_add_accounts)
        await callback.message.edit_text(
            "📋 <b>Добавление аккаунтов вручную</b>\n\n"
            "Отправьте список аккаунтов в формате:\n"
            "<code>login:password</code>\n"
            "По одному на строку.\n\n"
            "Пример:\n"
            "<code>+79123456789:pass123\n"
            "+79123456790:pass456</code>"
        )
        await callback.answer()

    @dp.message(UserStates.manual_add_accounts)
    async def process_manual_accounts(message: Message, state: FSMContext):
        lines = message.text.strip().split('\n')
        added = 0
        
        async with db.pool.acquire() as conn:
            for line in lines:
                line = line.strip()
                if not line or ':' not in line:
                    continue
                
                parts = line.split(':', 1)
                login = parts[0].strip()
                password = parts[1].strip() if len(parts) > 1 else ''
                
                if not login:
                    continue
                
                existing = await conn.fetchval(
                    "SELECT account_id FROM accounts WHERE login = $1", login
                )
                if not existing:
                    await conn.execute(
                        """INSERT INTO accounts (login, password, status, source, added_by)
                           VALUES ($1, $2, 'free', 'manual', $3)""",
                        login, password, message.from_user.id
                    )
                    added += 1
        
        await state.clear()
        await message.answer(
            f"✅ Добавлено аккаунтов: <b>{added}</b>",
            reply_markup=Keyboards.admin_menu()
        )

    @dp.callback_query(F.data == "clear_used_accounts")
    async def clear_used_accounts(callback: CallbackQuery):
        async with db.pool.acquire() as conn:
            deleted = await conn.fetchval(
                "WITH deleted AS (DELETE FROM accounts WHERE status = 'used' RETURNING account_id) SELECT COUNT(*) FROM deleted"
            )
        
        await callback.answer(f"Удалено {deleted} использованных аккаунтов")
        await callback.message.delete()
        
        text, markup = await get_pool_stats_message()
        await callback.message.answer(text, reply_markup=markup)

    @dp.message(F.text == "🔄 Lolzteam")
    async def admin_lolzteam(message: Message):
        if not await is_admin(message.from_user.id):
            return
        
        async with db.pool.acquire() as conn:
            user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", message.from_user.id)
            has_token = bool(user['lolz_token'])
        
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(
            text="🔑 Привязать API-токен" if not has_token else "🔑 Обновить API-токен",
            callback_data="set_lolz_token"
        ))
        builder.row(InlineKeyboardButton(
            text="📥 Загрузить аккаунты с Lolz",
            callback_data="fetch_lolz_accounts"
        ))
        
        await message.answer(
            f"🔄 <b>Lolzteam интеграция</b>\n\n"
            f"Статус: {'✅ Токен привязан' if has_token else '❌ Токен не привязан'}\n"
            f"Аккаунты с лолза загружаются в пул для накрутки.",
            reply_markup=builder.as_markup()
        )

    @dp.callback_query(F.data == "set_lolz_token")
    async def set_lolz_token_start(callback: CallbackQuery, state: FSMContext):
        await state.set_state(UserStates.entering_lolz_token)
        await callback.message.edit_text(
            "🔑 <b>Привязка API-токена Lolzteam</b>\n\n"
            "Введите ваш API-токен из личного кабинета Lolzteam:\n"
            "<i>Токен будет зашифрован перед сохранением</i>"
        )
        await callback.answer()

    @dp.message(UserStates.entering_lolz_token)
    async def process_lolz_token(message: Message, state: FSMContext):
        token = message.text.strip()
        encrypted_token = fernet.encrypt(token.encode()).decode()
        
        async with db.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET lolz_token = $1 WHERE user_id = $2",
                encrypted_token, message.from_user.id
            )
        
        await state.clear()
        await message.answer(
            "✅ <b>API-токен сохранён!</b>\n"
            "Теперь вы можете загружать аккаунты с Lolzteam.",
            reply_markup=Keyboards.admin_menu()
        )

    @dp.callback_query(F.data == "fetch_lolz_accounts")
    async def fetch_lolz_accounts(callback: CallbackQuery):
        async with db.pool.acquire() as conn:
            user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", callback.from_user.id)
            if not user['lolz_token']:
                await callback.answer("❌ Сначала привяжите API-токен", show_alert=True)
                return
            token = fernet.decrypt(user['lolz_token'].encode()).decode()
        
        await callback.answer("⏳ Загружаю аккаунты с Lolzteam...")
        status_msg = await callback.message.edit_text("⏳ Загрузка аккаунтов с Lolzteam...")
        
        accounts = await lolz_api.fetch_accounts(token)
        
        added = 0
        async with db.pool.acquire() as conn:
            for acc in accounts:
                existing = await conn.fetchval("SELECT account_id FROM accounts WHERE login = $1", acc['login'])
                if not existing:
                    await conn.execute(
                        """INSERT INTO accounts (login, password, status, source, lolz_order_id, added_by)
                           VALUES ($1, $2, 'free', 'lolzteam', $3, $4)""",
                        acc['login'], acc['password'], acc.get('order_id', 0), callback.from_user.id
                    )
                    added += 1
        
        await status_msg.edit_text(
            f"✅ <b>Загрузка завершена!</b>\n\n"
            f"📥 Загружено новых аккаунтов: <b>{added}</b>\n"
            f"📦 Всего обработано: {len(accounts)}"
        )

    @dp.message(F.text == "👥 Пользователи")
    async def admin_users(message: Message):
        if not await is_admin(message.from_user.id):
            return
        
        async with db.pool.acquire() as conn:
            users = await conn.fetch(
                """SELECT u.*, COUNT(sl.log_id) as total_sessions
                   FROM users u
                   LEFT JOIN sessions_log sl ON u.user_id = sl.user_id
                   GROUP BY u.user_id
                   ORDER BY u.created_at DESC
                   LIMIT 20"""
            )
        
        text = f"👥 <b>Пользователи ({len(users)}):</b>\n\n"
        for u in users:
            ban = "🚫" if u['is_banned'] else ""
            text += (
                f"{ban} {u['first_name']} @{u['username'] or 'нет'}\n"
                f"   Баланс: {u['balance']} ⭐ | Заходов: {u['total_sessions'] or 0}\n"
            )
        
        await message.answer(text)

    @dp.message(F.text == "📊 Общая статистика")
    async def admin_global_stats(message: Message):
        if not await is_admin(message.from_user.id):
            return
        
        async with db.pool.acquire() as conn:
            users_count = await conn.fetchval("SELECT COUNT(*) FROM users")
            accounts_count = await conn.fetchval("SELECT COUNT(*) FROM accounts")
            free_accounts = await conn.fetchval("SELECT COUNT(*) FROM accounts WHERE status = 'free'")
            proxies_count = await conn.fetchval("SELECT COUNT(*) FROM proxies WHERE is_active = TRUE")
            total_sessions = await conn.fetchval("SELECT COUNT(*) FROM sessions_log")
            success_sessions = await conn.fetchval("SELECT COUNT(*) FROM sessions_log WHERE status = 'success'")
            total_earned = await conn.fetchval("SELECT COALESCE(SUM(total_spent), 0) FROM users")
        
        await message.answer(
            f"📊 <b>Общая статистика:</b>\n\n"
            f"👥 Пользователей: <b>{users_count}</b>\n"
            f"📦 Аккаунтов в пуле: <b>{accounts_count}</b> (свободно: {free_accounts})\n"
            f"🌐 Активных прокси: <b>{proxies_count}</b>\n"
            f"📊 Всего заходов: <b>{total_sessions}</b>\n"
            f"✅ Успешно: <b>{success_sessions}</b>\n"
            f"💰 Заработано всего: <b>{total_earned:.1f} ⭐</b>",
            reply_markup=Keyboards.admin_menu()
        )

    @dp.message(F.text == "💰 Финансы")
    async def admin_finances(message: Message):
        if not await is_admin(message.from_user.id):
            return
        
        async with db.pool.acquire() as conn:
            payments = await conn.fetch(
                "SELECT * FROM payments WHERE status = 'completed' ORDER BY created_at DESC LIMIT 20"
            )
            total_revenue = await conn.fetchval(
                "SELECT COALESCE(SUM(stars), 0) FROM payments WHERE status = 'completed'"
            )
            users_balance = await conn.fetchval("SELECT COALESCE(SUM(balance), 0) FROM users")
        
        text = (
            f"💰 <b>Финансы:</b>\n\n"
            f"💵 Выручка всего: <b>{total_revenue} ⭐</b>\n"
            f"💳 Баланс пользователей: <b>{users_balance:.1f} ⭐</b>\n\n"
            f"📋 <b>Последние платежи:</b>\n"
        )
        
        for p in payments:
            text += f"• {p['stars']} ⭐ — {p['created_at'].strftime('%d.%m.%Y %H:%M')}\n"
        
        await message.answer(text, reply_markup=Keyboards.admin_menu())

    return dp

async def setup_web_dashboard():
    """Веб-дашборд для статистики"""
    async def dashboard(request):
        async with db.pool.acquire() as conn:
            stats = await conn.fetchrow("""
                SELECT 
                    (SELECT COUNT(*) FROM users) as users,
                    (SELECT COUNT(*) FROM accounts WHERE status='free') as free_accs,
                    (SELECT COUNT(*) FROM accounts WHERE status='in_use') as busy_accs,
                    (SELECT COUNT(*) FROM accounts WHERE status='used') as used_accs,
                    (SELECT COUNT(*) FROM sessions_log WHERE status='success') as success,
                    (SELECT COUNT(*) FROM sessions_log WHERE status='fail') as fail,
                    (SELECT COALESCE(SUM(total_spent),0) FROM users) as earned,
                    (SELECT COUNT(*) FROM proxies WHERE is_active=TRUE) as proxies
            """)
        
        return web.json_response({
            'users': stats['users'],
            'accounts': {
                'free': stats['free_accs'],
                'busy': stats['busy_accs'],
                'used': stats['used_accs']
            },
            'sessions': {
                'success': stats['success'],
                'fail': stats['fail']
            },
            'earned_stars': float(stats['earned']),
            'active_proxies': stats['proxies']
        })
    
    async def health(request):
        return web.json_response({'status': 'ok', 'timestamp': int(time.time())})
    
    app = web.Application()
    app.router.add_get('/api/stats', dashboard)
    app.router.add_get('/api/health', health)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', config.WEB_PORT)
    await site.start()
    logger.info(f"Веб-дашборд запущен на порту {config.WEB_PORT}")

async def set_commands(bot_instance: Bot):
    commands = [
        BotCommand(command="start", description="Запуск бота"),
        BotCommand(command="admin", description="Админ-панель (для администраторов)")
    ]
    await bot_instance.set_my_commands(commands)

async def main():
    await db.connect()
    await redis_client.connect()
    
    storage = RedisStorage(
        redis=redis_client.client,
        key_builder=DefaultKeyBuilder(with_destiny=True)
    )
    dp = Dispatcher(storage=storage)
    
    await setup_handlers(dp)
    
    @dp.startup()
    async def on_startup():
        await set_commands(bot)
        asyncio.create_task(setup_web_dashboard())
        logger.info("✅ АвтоФерма v3.0 бот запущен")
    
    @dp.shutdown()
    async def on_shutdown():
        await db.close()
        await redis_client.close()
        logger.info("⛔ Бот остановлен")
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())