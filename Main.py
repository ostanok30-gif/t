import asyncio
import logging
import random
import sqlite3
import threading
import json
import uuid
import re
import os
import zipfile
from typing import Optional, Dict, List, Tuple

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, LabeledPrice,
    PreCheckoutQuery, BotCommand, ContentType
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from cryptography.fernet import Fernet
from pyrogram import Client
from pyrogram.errors import (
    FloodWait, SessionPasswordNeeded, SessionExpired,
    PhoneNumberBanned, PhoneNumberInvalid
)
import aiohttp

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('bot')

BOT_TOKEN = "8960090061:AAHxZ5MxJsL4pQNDvRXucwsAA5CmF_HvY6U"
ADMIN_IDS = [608502324]
API_ID = 25874957
API_HASH = "c89ef6fd9ba5c8a479abb1f4d2de248d"
ENCRYPTION_KEY = b"CoLS2rBzrpf4g7NV8ACSTxicsFNeFj1RqhYoBrxABW8="

MAX_EXECUTORS = 5
FAIL_PAUSE = 45
MAX_FAILS = 5
CHECK_CONCURRENT = 10
BATCH_SIZE = 100
SESSIONS_FOLDER = "sessions"

fernet = Fernet(ENCRYPTION_KEY)

os.makedirs(SESSIONS_FOLDER, exist_ok=True)

def mask_data(data: str) -> str:
    data = str(data).strip()
    if len(data) > 7:
        return f"{data[:4]}***{data[-2:]}"
    return "***"

class DB:
    def __init__(self):
        self._local = threading.local()
        self._init()
    
    def _conn(self):
        if not hasattr(self._local, 'c') or self._local.c is None:
            self._local.c = sqlite3.connect("autoferma.db", check_same_thread=False)
            self._local.c.row_factory = sqlite3.Row
            self._local.c.execute("PRAGMA journal_mode=WAL")
            self._local.c.execute("PRAGMA synchronous=OFF")
            self._local.c.execute("PRAGMA busy_timeout=5000")
        return self._local.c
    
    def _init(self):
        c = self._conn().cursor()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, username TEXT DEFAULT '', first_name TEXT DEFAULT '',
                role TEXT DEFAULT 'user', balance REAL DEFAULT 0.0, lolz_token TEXT DEFAULT '',
                total_spent REAL DEFAULT 0.0, total_loaded INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS bots (
                bot_id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER,
                bot_username TEXT NOT NULL, bot_link TEXT NOT NULL,
                price_per_user REAL DEFAULT 1.5, delay_between INTEGER DEFAULT 5,
                is_active INTEGER DEFAULT 1, custom_steps TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS accounts (
                account_id INTEGER PRIMARY KEY AUTOINCREMENT,
                login TEXT NOT NULL UNIQUE, password TEXT DEFAULT '',
                session_path TEXT DEFAULT '',
                status TEXT DEFAULT 'free', source TEXT DEFAULT 'manual',
                lolz_order_id INTEGER DEFAULT 0, added_by INTEGER,
                checked INTEGER DEFAULT 0, valid INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')), used_at TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                bot_id INTEGER, account_id INTEGER, status TEXT DEFAULT 'pending',
                error_reason TEXT DEFAULT '', amount_charged REAL DEFAULT 0.0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS payments (
                payment_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                amount_rub REAL DEFAULT 0.0, stars REAL DEFAULT 0.0,
                status TEXT DEFAULT 'pending', payment_method TEXT DEFAULT 'stars',
                payload TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now'))
            );
            INSERT OR IGNORE INTO settings VALUES ('global_price', '1.5');
            INSERT OR IGNORE INTO settings VALUES ('global_cooldown', '30');
            CREATE INDEX IF NOT EXISTS idx_acc_status ON accounts(status);
            CREATE INDEX IF NOT EXISTS idx_log_user ON sessions_log(user_id);
        """)
        try:
            c.execute("ALTER TABLE accounts ADD COLUMN session_path TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        self._conn().commit()
    
    def close(self):
        if hasattr(self._local, 'c') and self._local.c:
            self._local.c.close()
            self._local.c = None
    
    def one(self, q, *p):
        return dict(r) if (r := self._conn().cursor().execute(q, p).fetchone()) else None
    
    def all(self, q, *p):
        return [dict(r) for r in self._conn().cursor().execute(q, p).fetchall()]
    
    def val(self, q, *p):
        r = self._conn().cursor().execute(q, p).fetchone()
        return r[0] if r else None
    
    def run(self, q, *p):
        c = self._conn().cursor()
        c.execute(q, p)
        self._conn().commit()
        return c.lastrowid

db = DB()

class SessionLoader:
    @staticmethod
    async def extract_zip(zip_path: str, extract_to: str = SESSIONS_FOLDER) -> int:
        count = 0
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                for file_info in zf.infolist():
                    if file_info.filename.endswith('.session'):
                        zf.extract(file_info, extract_to)
                        count += 1
                        logger.info(f"Извлечён: {file_info.filename}")
            logger.info(f"Распаковано {count} сессий из {zip_path}")
        except Exception as e:
            logger.error(f"Ошибка распаковки zip: {e}")
        return count
    
    @staticmethod
    async def load_from_folder(folder_path: str = SESSIONS_FOLDER) -> Tuple[int, int]:
        added = 0
        skipped = 0
        
        if not os.path.exists(folder_path):
            logger.warning(f"Папка {folder_path} не существует")
            return 0, 0
        
        for filename in os.listdir(folder_path):
            if not filename.endswith('.session'):
                continue
            
            session_path = os.path.join(folder_path, filename)
            session_name = filename.replace('.session', '')
            
            existing = db.one("SELECT 1 FROM accounts WHERE session_path=?", session_path)
            if existing:
                skipped += 1
                continue
            
            try:
                cl = Client(
                    session_path,
                    api_id=API_ID,
                    api_hash=API_HASH,
                    in_memory=True,
                    no_updates=True
                )
                await cl.connect()
                
                try:
                    me = await cl.get_me()
                    phone = me.phone_number or session_name
                    await cl.disconnect()
                    
                    db.run(
                        "INSERT OR IGNORE INTO accounts (login, password, session_path, status, source, checked, valid) VALUES (?, 'session', ?, 'free', 'session', 1, 1)",
                        phone, session_path
                    )
                    added += 1
                    logger.info(f"Сессия загружена: {phone} ({filename})")
                except Exception as e:
                    await cl.disconnect()
                    raise e
                
            except FloodWait as e:
                logger.warning(f"FloodWait {e.value}с для {filename}")
                await asyncio.sleep(e.value)
            except Exception as e:
                logger.error(f"Ошибка загрузки сессии {filename}: {e}")
                db.run(
                    "INSERT OR IGNORE INTO accounts (login, password, session_path, status, source, checked, valid) VALUES (?, 'session', ?, 'free', 'session', 0, 1)",
                    session_name, session_path
                )
                added += 1
                logger.info(f"Сессия добавлена без проверки: {session_name}")
        
        return added, skipped

session_loader = SessionLoader()

class AccountChecker:
    def __init__(self):
        self.sem = asyncio.Semaphore(CHECK_CONCURRENT)
    
    async def check_one(self, login: str, password: str, session_path: str = '') -> Tuple[str, bool, str]:
        async with self.sem:
            cl = None
            try:
                if session_path and os.path.exists(session_path):
                    cl = Client(
                        session_path,
                        api_id=API_ID,
                        api_hash=API_HASH,
                        in_memory=True,
                        no_updates=True
                    )
                    await cl.connect()
                    me = await cl.get_me()
                    phone = me.phone_number or login
                    await cl.disconnect()
                    return phone, True, "Сессия живая"
                else:
                    cl = Client(
                        name=f"chk_{uuid.uuid4().hex[:8]}",
                        api_id=API_ID,
                        api_hash=API_HASH,
                        in_memory=True,
                        no_updates=True
                    )
                    await cl.connect()
                    
                    phone = str(login).strip().lstrip('@')
                    if not phone.startswith('+'):
                        phone = '+' + phone
                    
                    try:
                        await cl.send_code(phone)
                        return login, True, "Активен"
                    except PhoneNumberBanned:
                        return login, False, "Забанен"
                    except PhoneNumberInvalid:
                        return login, False, "Неверный формат"
                    except FloodWait as e:
                        return login, False, f"Флуд {e.value}с"
                    except Exception as e:
                        msg = str(e).lower()
                        if 'banned' in msg or 'ban' in msg:
                            return login, False, "Забанен"
                        return login, False, "Ошибка"
                
            except FloodWait as e:
                await asyncio.sleep(e.value)
                return login, False, f"Флуд {e.value}с"
            except Exception as e:
                return login, False, f"Сбой"
            finally:
                if cl:
                    try:
                        if cl.is_connected:
                            await cl.disconnect()
                    except:
                        pass
    
    async def check_batch(self, accs: List[Dict], progress_callback=None) -> Dict:
        results = {'valid': 0, 'invalid': 0, 'total': len(accs)}
        tasks = [asyncio.create_task(self.check_one(
            a['login'], 
            a.get('password', ''), 
            a.get('session_path', '')
        )) for a in accs]
        
        done = 0
        for coro in asyncio.as_completed(tasks):
            login, valid, error = await coro
            done += 1
            
            if valid:
                results['valid'] += 1
                db.run("UPDATE accounts SET checked=1, valid=1, status='free' WHERE login=?", login)
            else:
                results['invalid'] += 1
                db.run("UPDATE accounts SET checked=1, valid=0, status='banned' WHERE login=?", login)
            
            if progress_callback:
                await progress_callback(done, len(accs), login, valid, error)
            
            await asyncio.sleep(0.3)
        
        return results

checker = AccountChecker()

class Executor:
    def __init__(self):
        self.sem = asyncio.Semaphore(MAX_EXECUTORS)
        self.fails = 0
        self.lock = asyncio.Lock()
    
    async def run_one(self, acc, bot_un, acc_id, steps=None):
        async with self.sem:
            cl = None
            try:
                session_path = acc.get('session_path', '')
                
                if session_path and os.path.exists(session_path):
                    cl = Client(
                        session_path,
                        api_id=API_ID,
                        api_hash=API_HASH,
                        in_memory=True,
                        no_updates=True
                    )
                    await cl.connect()
                    await cl.get_me()
                else:
                    cl = Client(
                        name=f"ex_{uuid.uuid4().hex[:8]}",
                        api_id=API_ID,
                        api_hash=API_HASH,
                        in_memory=True,
                        no_updates=True
                    )
                    await cl.connect()
                    
                    phone = str(acc['login']).strip().lstrip('@')
                    if not phone.startswith('+'):
                        phone = '+' + phone
                    
                    code = await cl.send_code(phone)
                    await asyncio.sleep(random.uniform(1, 3))
                    
                    pw = acc.get('password', '')
                    try:
                        await cl.sign_in(phone, code.phone_code_hash, '00000')
                    except SessionPasswordNeeded:
                        if pw:
                            await cl.check_password(pw)
                        else:
                            raise Exception("Требуется облачный пароль")
                    except Exception as e:
                        raise Exception(f"Ошибка входа: {e}")
                
                await asyncio.sleep(random.uniform(0.5, 1.5))
                await cl.send_message(bot_un, "/start")
                await asyncio.sleep(random.uniform(2, 4))
                
                if steps:
                    for step in steps:
                        await asyncio.sleep(random.uniform(0.5, 2))
                        await cl.send_message(bot_un, step)
                    return True, "Успешно"
                
                async for msg in cl.get_chat_history(bot_un, limit=1):
                    if msg.from_user and msg.from_user.is_self:
                        continue
                    mk = msg.reply_markup
                    if not mk:
                        return True, "Успешно"
                    
                    chk, chk_data, urls = None, None, []
                    
                    if hasattr(mk, 'inline_keyboard') and mk.inline_keyboard:
                        for row in mk.inline_keyboard:
                            for btn in row:
                                if btn.url:
                                    urls.append(btn.url)
                                elif btn.text and any(w in btn.text.lower() for w in ['проверить','check','подтвердить','verify']):
                                    chk = btn.text
                                    chk_data = getattr(btn, 'callback_data', None)
                    
                    for url in urls:
                        try:
                            if 't.me/' in url:
                                target_chat = url.split('t.me/')[-1].split('/')[0]
                                await cl.join_chat(target_chat)
                            await asyncio.sleep(random.uniform(0.5, 1.5))
                        except FloodWait as e:
                            await asyncio.sleep(e.value)
                        except:
                            pass
                    
                    if chk:
                        if chk_data:
                            try:
                                await cl.request_callback_answer(bot_un, msg.id, chk_data)
                            except:
                                await cl.send_message(bot_un, chk)
                        else:
                            await cl.send_message(bot_un, chk)
                        await asyncio.sleep(1)
                
                return True, "Успешно"
            
            except FloodWait as e:
                await asyncio.sleep(e.value)
                return False, "Таймаут"
            except SessionExpired:
                return False, "Сессия истекла"
            except Exception as e:
                return False, str(e)[:30]
            finally:
                if cl:
                    try:
                        if cl.is_connected:
                            await cl.disconnect()
                    except:
                        pass
    
    async def batch(self, uid, bid, bun, qty, price, delay, steps=None):
        ok = fail = 0
        charged = 0.0
        accs = db.all("SELECT * FROM accounts WHERE status='free' AND valid=1 ORDER BY RANDOM() LIMIT ?", qty)
        
        for a in accs:
            db.run("UPDATE accounts SET status='in_use' WHERE account_id=?", a['account_id'])
            log_id = db.run("INSERT INTO sessions_log (user_id,bot_id,account_id,status,amount_charged) VALUES (?,?,?,'pending',?)",
                        uid, bid, a['account_id'], price)
            
            success, err = await self.run_one(a, bun, a['account_id'], steps)
            
            if success:
                db.run("UPDATE accounts SET status='used',used_at=datetime('now') WHERE account_id=?", a['account_id'])
                db.run("UPDATE sessions_log SET status='success',error_reason='' WHERE log_id=?", log_id)
                db.run("UPDATE users SET balance=balance-?,total_spent=total_spent+?,total_loaded=total_loaded+1 WHERE user_id=?",
                      price, price, uid)
                ok += 1
                charged += price
                async with self.lock:
                    self.fails = 0
            else:
                db.run("UPDATE accounts SET status='free',used_at=NULL WHERE account_id=?", a['account_id'])
                db.run("UPDATE sessions_log SET status='fail',error_reason=? WHERE log_id=?", err, log_id)
                fail += 1
                async with self.lock:
                    self.fails += 1
                    if self.fails >= MAX_FAILS:
                        logger.warning(f"Превышено количество ошибок. Пауза {FAIL_PAUSE} секунд.")
                        self.fails = 0
                        await asyncio.sleep(FAIL_PAUSE)
            
            await asyncio.sleep(delay)
        
        return {'ok': ok, 'fail': fail, 'spent': charged}

executor = Executor()

class LolzAPI:
    def __init__(self):
        self.endpoints = [
            "https://api.zelenka.guru/market/user/orders/download",
            "https://api.lolz.live/market/user/orders/download",
        ]
        
    async def fetch_accounts(self, token: str) -> List[Dict]:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        accounts = []
        
        async with aiohttp.ClientSession() as session:
            for url in self.endpoints:
                try:
                    async with session.get(url, headers=headers, timeout=15) as resp:
                        if resp.status == 200:
                            raw_data = await resp.text()
                            accounts = await self._process_response(session, raw_data)
                            if accounts:
                                break
                except Exception as e:
                    logger.error(f"Ошибка загрузки лолз: {e}")
                    
        return accounts

    async def _process_response(self, session: aiohttp.ClientSession, text: str) -> List[Dict]:
        try:
            data = json.loads(text)
            link = data.get('url') or data.get('download_url') or data.get('link')
            if link:
                async with session.get(link, timeout=30) as file_resp:
                    if file_resp.status == 200:
                        file_content = await file_resp.text()
                        return self._parse_raw_text(file_content)
        except json.JSONDecodeError:
            pass
            
        return self._parse_raw_text(text)

    def _parse_raw_text(self, text: str) -> List[Dict]:
        accs = []
        seen = set()
        pattern = re.compile(r'[:;,|\t]')
        
        for line in text.strip().splitlines():
            line = line.strip()
            if not line:
                continue
                
            parts = pattern.split(line, maxsplit=1)
            login = parts[0].strip().lstrip('@').replace(' ', '')
            password = parts[1].strip() if len(parts) > 1 else ''
            
            if login and login not in seen and any(char.isdigit() for char in login):
                seen.add(login)
                accs.append({'login': login, 'password': password, 'order_id': 0})
                
        return accs

lolz_api = LolzAPI()

class States(StatesGroup):
    bot_username = State()
    bot_price = State()
    bot_delay = State()
    bot_steps = State()
    target_qty = State()
    lolz_token = State()
    manual_accounts = State()
    topup_amount = State()
    sys_price = State()
    sys_cooldown = State()
    upload_zip = State()

def get_sys_price():
    return float(db.val("SELECT value FROM settings WHERE key='global_price'") or 1.5)

def get_sys_cd():
    return int(db.val("SELECT value FROM settings WHERE key='global_cooldown'") or 30)

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

class GUI:
    @staticmethod
    def main_menu(uid):
        kb = [
            [KeyboardButton(text="Залить юзеров")],
            [KeyboardButton(text="Мои боты"), KeyboardButton(text="Отслеживание")],
            [KeyboardButton(text="Профиль"), KeyboardButton(text="Пополнить")]
        ]
        if is_admin(uid):
            kb.append([KeyboardButton(text="Админка")])
        return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    
    @staticmethod
    def admin_menu():
        return ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="Аккаунты")],
            [KeyboardButton(text="Лолз"), KeyboardButton(text="Настройки")],
            [KeyboardButton(text="Юзеры"), KeyboardButton(text="Стата")],
            [KeyboardButton(text="Баланс"), KeyboardButton(text="В меню")]
        ], resize_keyboard=True)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

async def setup_routers(dp: Dispatcher):
    
    @dp.message(Command("start"))
    async def cmd_start(msg: Message):
        uid = msg.from_user.id
        if not db.one("SELECT 1 FROM users WHERE user_id=?", uid):
            db.run("INSERT INTO users (user_id,username,first_name,role) VALUES (?,?,?,?)",
                  uid, msg.from_user.username or '', msg.from_user.first_name or '',
                  'admin' if is_admin(uid) else 'user')
        
        await msg.answer(
            f"Привет, {msg.from_user.first_name}.\n\n"
            f"Здесь можно настроить автоматизацию подписок в Telegram-ботах.\n"
            f"Добавь своего бота, пополни баланс и запусти процесс.",
            reply_markup=GUI.main_menu(uid)
        )

    @dp.message(F.text == "В меню")
    async def cmd_back(msg: Message):
        await msg.answer("Главное меню.", reply_markup=GUI.main_menu(msg.from_user.id))

    @dp.message(F.text == "Залить юзеров")
    async def traffic_start(msg: Message):
        bots = db.all("SELECT * FROM bots WHERE owner_id=? AND is_active=1", msg.from_user.id)
        if not bots:
            await msg.answer("У вас нет активных ботов. Добавьте их в разделе «Мои боты».", reply_markup=GUI.main_menu(msg.from_user.id))
            return
        
        builder = InlineKeyboardBuilder()
        for b in bots:
            mark = " [шаблон]" if b['custom_steps'] else ""
            builder.row(InlineKeyboardButton(text=f"@{b['bot_username']} - {b['price_per_user']} зв{mark}", callback_data=f"trf_{b['bot_id']}"))
        builder.row(InlineKeyboardButton(text="Отмена", callback_data="cancel_action"))
        
        await msg.answer("Выберите бота для залива юзеров:", reply_markup=builder.as_markup())

    @dp.callback_query(F.data.startswith("trf_"))
    async def traffic_select(cb: CallbackQuery, state: FSMContext):
        bot_id = int(cb.data.split("_")[1])
        proj = db.one("SELECT * FROM bots WHERE bot_id=?", bot_id)
        if not proj:
            await cb.answer("Проект не найден.", show_alert=True)
            return
            
        free_accs = db.val("SELECT COUNT(*) FROM accounts WHERE status='free' AND valid=1")
        user = db.one("SELECT * FROM users WHERE user_id=?", cb.from_user.id)
        
        max_possible = min(free_accs, int(user['balance']/proj['price_per_user']) if proj['price_per_user'] > 0 else free_accs)
        steps = [s.strip() for s in proj['custom_steps'].split(',')] if proj['custom_steps'] else None
        
        await state.update_data(
            bid=bot_id, bun=proj['bot_username'], pr=proj['price_per_user'],
            dl=proj['delay_between'], st=steps, mx=max_possible
        )
        await state.set_state(States.target_qty)
        
        await cb.message.edit_text(
            f"Запуск в бота: @{proj['bot_username']}\n\n"
            f"Цена за юзера: {proj['price_per_user']} зв\n"
            f"Доступно аккаунтов: {free_accs}\n"
            f"Ваш баланс: {user['balance']:.1f} зв\n"
            f"Максимальное количество: <b>{max_possible}</b> шт.\n\n"
            f"Введите количество:"
        )
        await cb.answer()

    @dp.message(States.target_qty)
    async def traffic_execute(msg: Message, state: FSMContext):
        if not msg.text.isdigit() or int(msg.text) <= 0:
            await msg.answer("Пожалуйста, введите положительное целое число.")
            return
            
        qty = int(msg.text)
        data = await state.get_data()
        
        if qty > data['mx']:
            await msg.answer(f"Превышен лимит. Доступно максимум: {data['mx']}.", reply_markup=GUI.main_menu(msg.from_user.id))
            await state.clear()
            return
            
        await state.clear()
        status_msg = await msg.answer(f"Запускаю процесс. Выбрано: {qty} юзеров для @{data['bun']}...", reply_markup=GUI.main_menu(msg.from_user.id))
        
        res = await executor.batch(msg.from_user.id, data['bid'], data['bun'], qty, data['pr'], data['dl'], data.get('st'))
        
        u = db.one("SELECT balance FROM users WHERE user_id=?", msg.from_user.id)
        await status_msg.edit_text(
            f"Готово.\n\n"
            f"Успешных входов: <b>{res['ok']}</b>\n"
            f"Ошибок (отвал): <b>{res['fail']}</b>\n"
            f"Списано с баланса: <b>{res['spent']:.1f}</b> зв\n"
            f"Остаток на балансе: <b>{u['balance']:.1f}</b> зв"
        )

    @dp.callback_query(F.data == "cancel_action")
    async def cancel_action(cb: CallbackQuery, state: FSMContext):
        await state.clear()
        await cb.message.delete()
        await cb.answer("Действие отменено.")

    @dp.message(F.text == "Мои боты")
    async def my_projects(msg: Message):
        bots = db.all("SELECT * FROM bots WHERE owner_id=? AND is_active=1 ORDER BY created_at DESC", msg.from_user.id)
        
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="Добавить", callback_data="proj_add"))
        
        if not bots:
            await msg.answer("У вас пока нет добавленных ботов.", reply_markup=builder.as_markup())
            return
            
        text = f"Ваши боты ({len(bots)}):\n\n"
        for b in bots:
            text += f"@{b['bot_username']} - {b['price_per_user']} зв\n"
            builder.row(
                InlineKeyboardButton(text=f"Удалить @{b['bot_username']}", callback_data=f"proj_del_{b['bot_id']}")
            )
            
        await msg.answer(text, reply_markup=builder.as_markup())

    @dp.callback_query(F.data == "proj_add")
    async def proj_add(cb: CallbackQuery, state: FSMContext):
        await state.set_state(States.bot_username)
        await cb.message.edit_text("Введите username вашего бота (без @):")
        await cb.answer()

    @dp.message(States.bot_username)
    async def proj_price(msg: Message, state: FSMContext):
        un = msg.text.strip().replace('@', '')
        await state.update_data(un=un)
        await state.set_state(States.bot_price)
        await msg.answer(f"Укажите цену за одного юзера в звездах.\nГлобальная цена в системе: {get_sys_price()}")

    @dp.message(States.bot_price)
    async def proj_delay(msg: Message, state: FSMContext):
        try:
            price = float(msg.text.strip())
            if price < 0: raise ValueError
        except:
            await msg.answer("Введите корректное положительное число.")
            return
            
        await state.update_data(pr=price)
        await state.set_state(States.bot_delay)
        await msg.answer(f"Укажите задержку между входами (в секундах).\nСтандартное значение: {get_sys_cd()}")

    @dp.message(States.bot_delay)
    async def proj_steps(msg: Message, state: FSMContext):
        try:
            dl = int(msg.text.strip())
            if dl < 1: raise ValueError
        except:
            await msg.answer("Введите целое число больше нуля.")
            return
            
        await state.update_data(dl=dl)
        await state.set_state(States.bot_steps)
        await msg.answer("Перечислите шаги через запятую или отправьте '-', если шаги не нужны.\nПример: подписаться, подтвердить")

    @dp.message(States.bot_steps)
    async def proj_save(msg: Message, state: FSMContext):
        raw = msg.text.strip()
        steps = '' if raw == '-' else ','.join(s.strip() for s in raw.split(','))
        data = await state.get_data()
        
        db.run(
            "INSERT INTO bots (owner_id,bot_username,bot_link,price_per_user,delay_between,custom_steps) VALUES (?,?,?,?,?,?)",
            msg.from_user.id, data['un'], f"https://t.me/{data['un']}", data['pr'], data['dl'], steps
        )
        await state.clear()
        await msg.answer(f"Бот @{data['un']} успешно добавлен.", reply_markup=GUI.main_menu(msg.from_user.id))

    @dp.callback_query(F.data.startswith("proj_del_"))
    async def proj_del(cb: CallbackQuery):
        bid = int(cb.data.split("_")[2])
        db.run("DELETE FROM bots WHERE bot_id=? AND owner_id=?", bid, cb.from_user.id)
        await cb.answer("Бот удален из списка.")
        await my_projects(cb.message)

    @dp.message(F.text == "Отслеживание")
    async def analytics(msg: Message):
        uid = msg.from_user.id
        stats = db.one("""
            SELECT 
                COUNT(*) as t,
                SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as s,
                SUM(CASE WHEN status='fail' THEN 1 ELSE 0 END) as f,
                COALESCE(SUM(CASE WHEN status='success' THEN amount_charged ELSE 0 END), 0) as sp
            FROM sessions_log WHERE user_id=?
        """, uid)
        
        last = db.all(
            "SELECT sl.status, sl.created_at, b.bot_username FROM sessions_log sl "
            "JOIN bots b ON sl.bot_id=b.bot_id WHERE sl.user_id=? ORDER BY sl.created_at DESC LIMIT 5", uid
        )
        
        text = (
            f"Статистика заходов:\n\n"
            f"Всего попыток: {stats['t']}\n"
            f"Успешных: {stats['s']}\n"
            f"Ошибок: {stats['f']}\n"
            f"Потрачено: {stats['sp']:.1f} зв\n"
        )
        
        if last:
            text += "\nПоследние операции:\n"
            for r in last:
                status_text = "Успешно" if r['status'] == 'success' else "Ошибка"
                text += f"- @{r['bot_username']} | {status_text} | {r['created_at'][11:16]}\n"
                
        await msg.answer(text, reply_markup=GUI.main_menu(uid))

    @dp.message(F.text == "Профиль")
    async def profile(msg: Message):
        u = db.one("SELECT * FROM users WHERE user_id=?", msg.from_user.id)
        if not u: return
        
        b_count = db.val("SELECT COUNT(*) FROM bots WHERE owner_id=?", u['user_id'])
        await msg.answer(
            f"Ваш профиль\n\n"
            f"ID: <code>{u['user_id']}</code>\n"
            f"Баланс: <b>{u['balance']:.1f}</b> зв\n"
            f"Количество ботов: {b_count}",
            reply_markup=GUI.main_menu(msg.from_user.id)
        )

    @dp.message(F.text == "Пополнить")
    async def balance_topup(msg: Message):
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="100 зв", callback_data="pay_100"), InlineKeyboardButton(text="500 зв", callback_data="pay_500"))
        builder.row(InlineKeyboardButton(text="1000 зв", callback_data="pay_1000"), InlineKeyboardButton(text="Своя сумма", callback_data="pay_custom"))
        await msg.answer("Выберите сумму для пополнения:", reply_markup=builder.as_markup())

    @dp.callback_query(F.data.startswith("pay_"))
    async def process_pay(cb: CallbackQuery, state: FSMContext):
        d = cb.data.split("_")[1]
        if d == "custom":
            await state.set_state(States.topup_amount)
            await cb.message.edit_text("Введите желаемую сумму пополнения (целое число):")
            return
            
        await send_invoice(cb.message, int(d), cb.from_user.id)
        await cb.answer()

    @dp.message(States.topup_amount)
    async def custom_pay(msg: Message, state: FSMContext):
        if not msg.text.isdigit() or int(msg.text) < 1:
            await msg.answer("Пожалуйста, введите корректное число.")
            return
        await state.clear()
        await send_invoice(msg, int(msg.text), msg.from_user.id)

    async def send_invoice(msg, amt, uid):
        try:
            await bot.send_invoice(
                chat_id=uid,
                title="Пополнение баланса",
                description=f"Пакет на {amt} звезд",
                payload=f"topup_{amt}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(label=f"{amt} Stars", amount=amt)]
            )
        except Exception as e:
            await msg.answer(f"Ошибка платежной системы: {e}")

    @dp.pre_checkout_query()
    async def pre_checkout(q: PreCheckoutQuery):
        await q.answer(ok=True)

    @dp.message(F.content_type == ContentType.SUCCESSFUL_PAYMENT)
    async def successful_payment(msg: Message):
        p = msg.successful_payment
        if p.invoice_payload.startswith("topup_"):
            amt = int(p.invoice_payload.split("_")[1])
            db.run("UPDATE users SET balance=balance+? WHERE user_id=?", float(amt), msg.from_user.id)
            db.run("INSERT INTO payments (user_id,stars,status) VALUES (?,?,'completed')", msg.from_user.id, amt)
            await msg.answer(f"Баланс успешно пополнен на {amt} звезд.", reply_markup=GUI.main_menu(msg.from_user.id))

    # ==========================================
    # ПАНЕЛЬ АДМИНИСТРАТОРА
    # ==========================================
    @dp.message(F.text == "Админка")
    async def admin_root(msg: Message):
        if is_admin(msg.from_user.id):
            await msg.answer("Панель администратора открыта.", reply_markup=GUI.admin_menu())

    @dp.message(F.text == "Аккаунты")
    async def db_pool(msg: Message):
        if not is_admin(msg.from_user.id): return
        
        stats = db.one("""
            SELECT 
                COUNT(*) as t,
                SUM(CASE WHEN status='free' AND valid=1 THEN 1 ELSE 0 END) as f,
                SUM(CASE WHEN valid=0 THEN 1 ELSE 0 END) as ban,
                SUM(CASE WHEN checked=0 THEN 1 ELSE 0 END) as unch,
                SUM(CASE WHEN session_path != '' THEN 1 ELSE 0 END) as sess
            FROM accounts
        """)
        
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="Добавить вручную", callback_data="adm_load_txt"))
        builder.row(InlineKeyboardButton(text="Загрузить сессии (zip)", callback_data="adm_load_zip"))
        builder.row(InlineKeyboardButton(text="Загрузить из папки sessions/", callback_data="adm_load_folder"))
        builder.row(InlineKeyboardButton(text="Очистить забаненные", callback_data="adm_clear_banned"))
        builder.row(InlineKeyboardButton(text="Сбросить статус проверок", callback_data="adm_reset_chk"))
        builder.row(InlineKeyboardButton(text="Запустить чекер (все)", callback_data="adm_start_chk"))
        
        await msg.answer(
            f"База аккаунтов\n\n"
            f"Всего добавлено: {stats['t']}\n"
            f"Свободных (живых): {stats['f'] or 0}\n"
            f"Сессий загружено: {stats['sess'] or 0}\n"
            f"Забаненных: {stats['ban'] or 0}\n"
            f"Непроверенных: {stats['unch'] or 0}",
            reply_markup=builder.as_markup()
        )

    @dp.callback_query(F.data == "adm_load_zip")
    async def adm_load_zip(cb: CallbackQuery, state: FSMContext):
        await state.set_state(States.upload_zip)
        await cb.message.edit_text("Отправьте файл sessions.zip с .session файлами внутри:")
        await cb.answer()

    @dp.message(States.upload_zip, F.document)
    async def process_zip(msg: Message, state: FSMContext):
        if not msg.document.file_name.endswith('.zip'):
            await msg.answer("Нужен .zip файл.")
            return
        
        await msg.answer("Скачиваю и распаковываю архив...")
        
        file_id = msg.document.file_id
        file = await bot.get_file(file_id)
        zip_path = f"{SESSIONS_FOLDER}/uploaded.zip"
        await bot.download_file(file.file_path, zip_path)
        
        count = await session_loader.extract_zip(zip_path)
        
        if count > 0:
            added, skipped = await session_loader.load_from_folder()
            await msg.answer(
                f"Архив обработан.\n"
                f"Распаковано сессий: {count}\n"
                f"Загружено в базу: {added}\n"
                f"Пропущено (дубли): {skipped}",
                reply_markup=GUI.admin_menu()
            )
        else:
            await msg.answer("В архиве не найдено .session файлов.", reply_markup=GUI.admin_menu())
        
        await state.clear()

    @dp.callback_query(F.data == "adm_load_folder")
    async def adm_load_folder(cb: CallbackQuery):
        await cb.answer("Сканирую папку sessions/...")
        added, skipped = await session_loader.load_from_folder()
        await cb.message.edit_text(
            f"Сканирование завершено.\n"
            f"Загружено новых сессий: {added}\n"
            f"Пропущено (уже в базе): {skipped}"
        )

    @dp.callback_query(F.data == "adm_start_chk")
    async def admin_check_all(cb: CallbackQuery):
        total = db.val("SELECT COUNT(*) FROM accounts WHERE checked=0")
        if total == 0:
            await cb.answer("Все аккаунты уже проверены.", show_alert=True)
            return
        
        await cb.answer(f"Запущена проверка всех {total} аккаунтов...")
        st = await cb.message.edit_text(f"Статус: проверка...\nПрогресс: 0/{total}")
        
        valid_total = 0
        invalid_total = 0
        offset = 0
        
        while offset < total:
            batch = [{'login': r['login'], 'password': r['password'], 'session_path': r['session_path']} 
                     for r in db.all("SELECT login, password, session_path FROM accounts WHERE checked=0 LIMIT ? OFFSET ?", BATCH_SIZE, offset)]
            
            if not batch:
                break
            
            async def updater(i, t, login, v, error):
                if i % 20 == 0 or i == t:
                    try:
                        await st.edit_text(
                            f"Статус: выполнение\n"
                            f"Проверено всего: {offset + i}/{total}\n"
                            f"Последний: {mask_data(login)} | {'Валид' if v else 'Бан'}"
                        )
                    except:
                        pass
            
            res = await checker.check_batch(batch, progress_callback=updater)
            valid_total += res['valid']
            invalid_total += res['invalid']
            offset += len(batch)
            
            await asyncio.sleep(1)
        
        await st.edit_text(
            f"Проверка завершена.\n\n"
            f"Всего проверено: {total}\n"
            f"Валидных: {valid_total}\n"
            f"Забаненных: {invalid_total}"
        )

    @dp.callback_query(F.data == "adm_load_txt")
    async def adm_load(cb: CallbackQuery, state: FSMContext):
        await state.set_state(States.manual_accounts)
        await cb.message.edit_text("Отправьте аккаунты в формате login:pass (каждый с новой строки):")
        await cb.answer()

    @dp.message(States.manual_accounts)
    async def process_manual_accs(msg: Message, state: FSMContext):
        added = 0
        for line in msg.text.splitlines():
            if ':' not in line: continue
            l, p = line.split(':', 1)
            l = l.strip().replace(' ', '')
            if not l or db.one("SELECT 1 FROM accounts WHERE login=?", l): continue
            db.run("INSERT INTO accounts (login,password,added_by) VALUES (?,?,?)", l, p.strip(), msg.from_user.id)
            added += 1
            
        await state.clear()
        await msg.answer(f"Добавлено новых аккаунтов: {added}", reply_markup=GUI.admin_menu())

    @dp.callback_query(F.data == "adm_clear_banned")
    async def clear_banned(cb: CallbackQuery):
        db.run("DELETE FROM accounts WHERE valid=0 AND session_path=''")
        await cb.answer("Нерабочие аккаунты удалены (сессии сохранены).")
        await db_pool(cb.message)

    @dp.callback_query(F.data == "adm_reset_chk")
    async def reset_checks(cb: CallbackQuery):
        db.run("UPDATE accounts SET checked=0, valid=1, status='free' WHERE valid=0")
        await cb.answer("Статус аккаунтов сброшен, они снова будут проверены.")
        await db_pool(cb.message)

    @dp.message(F.text == "Лолз")
    async def zelenka_panel(msg: Message):
        if not is_admin(msg.from_user.id): return
        
        u = db.one("SELECT lolz_token FROM users WHERE user_id=?", msg.from_user.id)
        has_token = bool(u and u['lolz_token'])
        
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="Обновить токен", callback_data="zl_key"))
        builder.row(InlineKeyboardButton(text="Скачать аккаунты", callback_data="zl_pull"))
        
        await msg.answer(f"Интеграция с Лолз\nСтатус токена: {'Привязан' if has_token else 'Отсутствует'}", reply_markup=builder.as_markup())

    @dp.callback_query(F.data == "zl_key")
    async def zl_key(cb: CallbackQuery, state: FSMContext):
        await state.set_state(States.lolz_token)
        await cb.message.edit_text("Отправьте API токен лолза:")
        await cb.answer()

    @dp.message(States.lolz_token)
    async def save_zl_key(msg: Message, state: FSMContext):
        enc = fernet.encrypt(msg.text.strip().encode()).decode()
        db.run("UPDATE users SET lolz_token=? WHERE user_id=?", enc, msg.from_user.id)
        await state.clear()
        await msg.answer("API токен успешно сохранен.", reply_markup=GUI.admin_menu())

    @dp.callback_query(F.data == "zl_pull")
    async def zl_pull(cb: CallbackQuery):
        u = db.one("SELECT lolz_token FROM users WHERE user_id=?", cb.from_user.id)
        if not u or not u['lolz_token']:
            await cb.answer("Сначала привяжите токен.", show_alert=True)
            return
            
        try:
            token = fernet.decrypt(u['lolz_token'].encode()).decode()
        except:
            await cb.answer("Ошибка чтения токена, привяжите его заново.", show_alert=True)
            return
            
        status = await cb.message.edit_text("Выполняю запрос к API для скачивания...")
        accs = await lolz_api.fetch_accounts(token)
        
        if not accs:
            await status.edit_text("Не удалось найти или скачать файл с аккаунтами. Проверьте актуальность токена.")
            return
            
        added = 0
        for a in accs:
            if not db.one("SELECT 1 FROM accounts WHERE login=?", a['login']):
                db.run("INSERT INTO accounts (login,password,source,lolz_order_id,added_by) VALUES (?,?,'lolz',?,?)",
                      a['login'], a['password'], a.get('order_id', 0), cb.from_user.id)
                added += 1
                
        await status.edit_text(f"Скачивание завершено.\nВсего в файле найдено: {len(accs)}\nУникальных загружено в базу: {added}")

    @dp.message(F.text.in_({"Настройки", "Юзеры", "Стата", "Баланс"}))
    async def adm_placeholders(msg: Message):
        if not is_admin(msg.from_user.id): return
        if msg.text == "Настройки":
            await msg.answer(f"Глобальная цена: {get_sys_price()} зв\nЗадержка по умолчанию: {get_sys_cd()} сек\n\nДля изменения воспользуйтесь базой данных.")
        elif msg.text == "Стата":
            users = db.val("SELECT COUNT(*) FROM users")
            await msg.answer(f"Всего пользователей в боте: {users}")
        elif msg.text == "Баланс":
            rub = db.val("SELECT COALESCE(SUM(stars),0) FROM payments")
            await msg.answer(f"Общая сумма пополнений: {rub} звезд")
        else:
            await msg.answer("Раздел в разработке.")

    return dp

async def main():
    dp = Dispatcher(storage=MemoryStorage())
    await setup_routers(dp)
    
    @dp.startup()
    async def on_start():
        await bot.set_my_commands([BotCommand(command="start", description="Запуск")])
        os.makedirs(SESSIONS_FOLDER, exist_ok=True)
        logger.info("Бот запущен и готов к работе")
    
    @dp.shutdown()
    async def on_stop():
        db.close()
        logger.info("Бот остановлен")
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass