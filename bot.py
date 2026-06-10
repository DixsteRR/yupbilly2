import os
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from openai import AsyncOpenAI
import database as db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Конфиг ───────────────────────────────────────────────────────────────────
BOT_TOKEN    = os.environ["BOT_TOKEN"]
OPENAI_KEY   = os.environ["OPENAI_API_KEY"]
ADMIN_IDS    = list(map(int, os.environ.get("ADMIN_IDS", "").split(","))) if os.environ.get("ADMIN_IDS") else []

FREE_LIMIT   = 5          # бесплатных сообщений
TRIAL_DAYS   = 3          # дней пробного периода
PRICE_MONTH  = 299        # Telegram Stars за месяц
PRICE_YEAR   = 2490       # Telegram Stars за год

openai_client = AsyncOpenAI(api_key=OPENAI_KEY)

# ─── Системный промпт ─────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Ты умный и полезный AI-ассистент в Telegram-боте.
Отвечай по-русски, если пользователь пишет по-русски, и по-английски если по-английски.
Будь краток, конкретен и полезен. Используй эмодзи умеренно.
Если не знаешь ответ — честно скажи об этом."""

# ─── Хелперы ──────────────────────────────────────────────────────────────────
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def check_access(user_id: int) -> tuple[bool, str]:
    """Возвращает (имеет_доступ, причина_отказа)"""
    user = db.get_user(user_id)
    if not user:
        return False, "not_found"

    # Активная подписка
    if user["subscription_until"] and datetime.fromisoformat(user["subscription_until"]) > datetime.now():
        return True, "subscribed"

    # Пробный период
    if user["trial_until"] and datetime.fromisoformat(user["trial_until"]) > datetime.now():
        return True, "trial"

    # Бесплатные сообщения
    if user["free_messages_used"] < FREE_LIMIT:
        remaining = FREE_LIMIT - user["free_messages_used"]
        return True, f"free:{remaining}"

    return False, "limit_reached"

def subscription_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ Месяц — 299 Stars", callback_data="sub_month")],
        [InlineKeyboardButton("⭐ Год — 2490 Stars (−30%)", callback_data="sub_year")],
        [InlineKeyboardButton("🎁 Пробный период 3 дня", callback_data="trial")],
    ])

def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💬 Новый чат", callback_data="new_chat"),
            InlineKeyboardButton("📊 Мой аккаунт", callback_data="account"),
        ],
        [InlineKeyboardButton("💳 Подписка", callback_data="show_plans")],
    ])

# ─── /start ───────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.create_user_if_not_exists(user.id, user.username or "", user.first_name or "")

    text = (
        f"👋 Привет, *{user.first_name}*!\n\n"
        f"Я — AI-ассистент на базе GPT-4. Могу помочь с:\n"
        f"• 📝 Текстами, письмами, постами\n"
        f"• 💡 Идеями и брейнштормингом\n"
        f"• 📊 Анализом и объяснениями\n"
        f"• 🌍 Переводами и рерайтом\n"
        f"• И любыми другими вопросами!\n\n"
        f"У тебя есть *{FREE_LIMIT} бесплатных сообщений*. Попробуй прямо сейчас!"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard())

# ─── /account ─────────────────────────────────────────────────────────────────
async def account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    if not user:
        await update.message.reply_text("Напиши /start для начала.")
        return

    has_access, reason = await check_access(user_id)

    if "subscribed" in reason:
        until = datetime.fromisoformat(user["subscription_until"]).strftime("%d.%m.%Y")
        status = f"✅ Подписка активна до *{until}*"
    elif "trial" in reason:
        until = datetime.fromisoformat(user["trial_until"]).strftime("%d.%m.%Y")
        status = f"🎁 Пробный период до *{until}*"
    else:
        used = user["free_messages_used"]
        left = max(0, FREE_LIMIT - used)
        status = f"🆓 Бесплатных сообщений: *{left}/{FREE_LIMIT}*"

    text = (
        f"📊 *Твой аккаунт*\n\n"
        f"👤 {user['first_name']}\n"
        f"🆔 `{user_id}`\n"
        f"📅 Зарегистрирован: {user['created_at'][:10]}\n"
        f"💬 Всего сообщений: {user['total_messages']}\n\n"
        f"{status}"
    )
    await update.message.reply_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💳 Оформить подписку", callback_data="show_plans")]])
    )

# ─── Обработка AI-сообщений ───────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # Создаём юзера если нет
    user = update.effective_user
    db.create_user_if_not_exists(user_id, user.username or "", user.first_name or "")

    has_access, reason = await check_access(user_id)

    if not has_access:
        await update.message.reply_text(
            "⛔ *Лимит исчерпан*\n\n"
            "Ты использовал все бесплатные сообщения.\n"
            "Оформи подписку чтобы продолжить:",
            parse_mode="Markdown",
            reply_markup=subscription_keyboard()
        )
        return

    # Показываем typing
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # История чата из контекста
    history = context.user_data.get("history", [])
    history.append({"role": "user", "content": text})

    # Ограничиваем историю до 10 сообщений
    if len(history) > 10:
        history = history[-10:]

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history,
            max_tokens=1000,
            temperature=0.7,
        )
        reply = response.choices[0].message.content
        history.append({"role": "assistant", "content": reply})
        context.user_data["history"] = history

        # Обновляем счётчики в БД
        db.increment_message_count(user_id, is_free=(reason.startswith("free")))

        # Показываем остаток бесплатных если нужно
        suffix = ""
        if reason.startswith("free:"):
            left = int(reason.split(":")[1]) - 1
            if left <= 2:
                suffix = f"\n\n_💡 Осталось бесплатных: {left}. [Оформить подписку]_"

        await update.message.reply_text(reply + suffix, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        await update.message.reply_text("⚠️ Ошибка при обращении к AI. Попробуй ещё раз.")

# ─── Callback кнопки ──────────────────────────────────────────────────────────
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "new_chat":
        context.user_data["history"] = []
        await query.edit_message_text("🔄 *Новый чат начат!*\n\nЗадай мне любой вопрос.", parse_mode="Markdown")

    elif data == "account":
        user = db.get_user(user_id)
        has_access, reason = await check_access(user_id)
        if "subscribed" in reason:
            until = datetime.fromisoformat(user["subscription_until"]).strftime("%d.%m.%Y")
            status = f"✅ Подписка до *{until}*"
        elif "trial" in reason:
            until = datetime.fromisoformat(user["trial_until"]).strftime("%d.%m.%Y")
            status = f"🎁 Пробный период до *{until}*"
        else:
            left = max(0, FREE_LIMIT - user["free_messages_used"])
            status = f"🆓 Бесплатных осталось: *{left}*"
        await query.edit_message_text(
            f"📊 *Аккаунт*\n\n{status}\n💬 Сообщений всего: {user['total_messages']}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_main")]])
        )

    elif data == "show_plans":
        await query.edit_message_text(
            "💳 *Выбери план подписки:*\n\n"
            "⭐ *Месяц* — 299 Stars (~$3.3)\n"
            "Неограниченные сообщения на 30 дней\n\n"
            "⭐ *Год* — 2490 Stars (~$27, экономия 30%)\n"
            "Неограниченные сообщения на 365 дней\n\n"
            "🎁 *Пробный период* — 3 дня бесплатно\n"
            "_(только для новых пользователей)_",
            parse_mode="Markdown",
            reply_markup=subscription_keyboard()
        )

    elif data == "trial":
        user = db.get_user(user_id)
        if user and user["trial_used"]:
            await query.edit_message_text(
                "❌ Пробный период уже был использован.\nОформи подписку:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⭐ Месяц — 299 Stars", callback_data="sub_month")],
                    [InlineKeyboardButton("⭐ Год — 2490 Stars", callback_data="sub_year")],
                ])
            )
        else:
            trial_until = datetime.now() + timedelta(days=TRIAL_DAYS)
            db.activate_trial(user_id, trial_until.isoformat())
            await query.edit_message_text(
                f"🎉 *Пробный период активирован!*\n\n"
                f"У тебя есть полный доступ до *{trial_until.strftime('%d.%m.%Y')}*.\n"
                f"Задавай любые вопросы!",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Начать", callback_data="new_chat")]])
            )

    elif data in ("sub_month", "sub_year"):
        stars = PRICE_MONTH if data == "sub_month" else PRICE_YEAR
        label = "1 месяц" if data == "sub_month" else "1 год"
        await context.bot.send_invoice(
            chat_id=user_id,
            title=f"Подписка AI-ассистент — {label}",
            description=f"Неограниченный доступ к AI-ассистенту на {label}",
            payload=data,
            currency="XTR",           # Telegram Stars
            prices=[{"label": label, "amount": stars}],
        )

    elif data == "back_main":
        await query.edit_message_text(
            "Выбери действие:", reply_markup=main_keyboard()
        )

# ─── Оплата ───────────────────────────────────────────────────────────────────
async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def payment_success(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    payload = update.message.successful_payment.invoice_payload

    if payload == "sub_month":
        until = datetime.now() + timedelta(days=30)
    else:
        until = datetime.now() + timedelta(days=365)

    db.activate_subscription(user_id, until.isoformat())

    label = "1 месяц" if payload == "sub_month" else "1 год"
    await update.message.reply_text(
        f"✅ *Оплата прошла! Подписка активна.*\n\n"
        f"📅 Доступ до: *{until.strftime('%d.%m.%Y')}*\n"
        f"💬 Неограниченные сообщения на {label}!\n\n"
        f"Задавай любой вопрос прямо сейчас 👇",
        parse_mode="Markdown"
    )

# ─── Админ-команды ────────────────────────────────────────────────────────────
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    stats = db.get_stats()
    await update.message.reply_text(
        f"📊 *Статистика бота*\n\n"
        f"👥 Всего пользователей: *{stats['total_users']}*\n"
        f"✅ Активных подписок: *{stats['active_subs']}*\n"
        f"🎁 Пробных периодов: *{stats['active_trials']}*\n"
        f"💬 Сообщений сегодня: *{stats['messages_today']}*\n"
        f"💰 Новых за 7 дней: *{stats['new_users_week']}*",
        parse_mode="Markdown"
    )

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Использование: /broadcast <текст>")
        return
    text = " ".join(context.args)
    users = db.get_all_user_ids()
    sent = 0
    for uid in users:
        try:
            await context.bot.send_message(uid, text)
            sent += 1
        except Exception:
            pass
    await update.message.reply_text(f"✅ Отправлено {sent}/{len(users)} пользователям.")

# ─── /help ────────────────────────────────────────────────────────────────────
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Команды бота:*\n\n"
        "/start — главное меню\n"
        "/account — твой аккаунт и подписка\n"
        "/new — начать новый чат\n"
        "/help — эта справка\n\n"
        "Просто напиши любой вопрос — я отвечу!",
        parse_mode="Markdown"
    )

async def new_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["history"] = []
    await update.message.reply_text("🔄 Новый чат начат! Задавай вопрос.")

# ─── Запуск ───────────────────────────────────────────────────────────────────
def main():
    db.init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("account", account))
    app.add_handler(CommandHandler("new", new_chat))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CommandHandler("broadcast", admin_broadcast))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, payment_success))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.ALL, pre_checkout))

    # Pre-checkout отдельно
    from telegram.ext import PreCheckoutQueryHandler
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))

    logger.info("Бот запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
