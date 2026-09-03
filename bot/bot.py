"""Telegram bot (pyTelegramBotAPI) that spins up GenisysPro / LiteCore 1.1.5
Minecraft PE servers in Docker with a single button.

The bot takes care of everything by itself:
  * picks a free UDP port,
  * creates the container + persistent volume,
  * opens the port on the router (UPnP -> NAT-PMP -> playit.gg -> direct),
  * shows a real, working, temporary address and keeps the lease alive,
  * stops the server and closes the port when the lease/TTL expires.
"""

from __future__ import annotations

import html
import logging
import os
import threading
import time
from typing import Any, Optional

import telebot
from telebot import types

from config import Config
from db import Database
from netinfo import bedrock_deeplink
from provisioner import ProvisionError, Provisioner
from publisher import HUMAN_KIND

LOG = logging.getLogger("bot")

ALIVE_FILE = "/tmp/bot.alive"
CORE_LABEL = "GenisysPro · LiteCore 1.1.5 (MCPE 1.1.5)"

STATUS_ICON = {
    "running": "🟢",
    "stopped": "⚪️",
    "creating": "⏳",
    "error": "🔴",
    "deleted": "🗑",
}
STATUS_TEXT = {
    "running": "работает",
    "stopped": "остановлен",
    "creating": "создаётся",
    "error": "ошибка",
    "deleted": "удалён",
}


# --------------------------------------------------------------------- format


def human_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч"
    return f"{minutes} мин"


def server_card(row: dict[str, Any], status: Optional[dict[str, Any]] = None) -> str:
    icon = STATUS_ICON.get(row["status"], "❓")
    state = STATUS_TEXT.get(row["status"], row["status"])
    name = html.escape(str(row["name"]))
    address = str(row["address"] or "—")
    lines = [
        f"{icon} <b>Сервер #{row['id']} · {name}</b>",
        f"Ядро: {CORE_LABEL}",
        f"Адрес: <code>{html.escape(address)}</code>",
        f"Порт: <code>{row['port']}/UDP</code> — {HUMAN_KIND.get(row['publish_kind'], row['publish_kind'])}",
        f"Состояние: {state}",
    ]

    if status:
        parts = []
        if status.get("cpu") is not None:
            parts.append(f"CPU {status['cpu']}%")
        if status.get("memory") is not None:
            limit = status.get("memory_limit")
            parts.append(
                f"RAM {status['memory']} МБ" + (f" / {limit:.0f} МБ" if limit else "")
            )
        if parts:
            lines.append("Ресурсы: " + " · ".join(parts))

    if row["status"] == "running" and row.get("expires_at"):
        left = float(row["expires_at"]) - time.time()
        lines.append(f"Автостоп: через {human_duration(left)}")

    note = (row.get("publish_meta") or {}).get("note")
    if note:
        lines.append(f"ℹ️ {html.escape(str(note))}")

    if row["status"] == "running" and ":" in address:
        link = bedrock_deeplink(str(row["name"]), address)
        lines.append(
            "\nВ игре: <b>Игра → Серверы → Добавить сервер</b>, "
            f"вставь адрес и порт.\n"
            f'<a href="{html.escape(link, quote=True)}">➕ Добавить в Minecraft одним касанием</a>'
        )
    return "\n".join(lines)


# ------------------------------------------------------------------ keyboards


def main_keyboard(has_servers: bool) -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("🚀 Создать сервер", callback_data="menu:create"))
    if has_servers:
        keyboard.add(types.InlineKeyboardButton("📋 Мои серверы", callback_data="menu:list"))
    return keyboard


def server_keyboard(row: dict[str, Any]) -> types.InlineKeyboardMarkup:
    server_id = int(row["id"])
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    if row["status"] == "running":
        keyboard.add(
            types.InlineKeyboardButton("⏹ Остановить", callback_data=f"srv:{server_id}:stop"),
            types.InlineKeyboardButton("🔄 Рестарт", callback_data=f"srv:{server_id}:restart"),
        )
    else:
        keyboard.add(
            types.InlineKeyboardButton("▶️ Запустить", callback_data=f"srv:{server_id}:start"),
            types.InlineKeyboardButton("🔄 Рестарт", callback_data=f"srv:{server_id}:restart"),
        )
    keyboard.add(
        types.InlineKeyboardButton("📊 Статус", callback_data=f"srv:{server_id}:status"),
        types.InlineKeyboardButton("📜 Логи", callback_data=f"srv:{server_id}:logs"),
    )
    keyboard.add(
        types.InlineKeyboardButton("🖥 Консоль", callback_data=f"srv:{server_id}:cmd"),
        types.InlineKeyboardButton("🌐 Обновить адрес", callback_data=f"srv:{server_id}:addr"),
    )
    keyboard.add(types.InlineKeyboardButton("🗑 Удалить", callback_data=f"srv:{server_id}:del"))
    keyboard.add(types.InlineKeyboardButton("⬅️ Меню", callback_data="menu:home"))
    return keyboard


def confirm_keyboard(server_id: int) -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("✅ Да, удалить", callback_data=f"srv:{server_id}:delyes"),
        types.InlineKeyboardButton("❌ Отмена", callback_data=f"srv:{server_id}:status"),
    )
    return keyboard


# ---------------------------------------------------------------------- build


def build_bot(cfg: Config, database: Database, provisioner: Provisioner) -> Any:
    bot = telebot.TeleBot(
        cfg.bot_token,
        parse_mode="HTML",
        threaded=True,
        num_threads=max(2, cfg.worker_threads),
    )
    awaiting_command: dict[tuple[int, int], int] = {}
    lock = threading.Lock()

    # ---------------------------------------------------------------- helpers

    def allowed(user_id: int) -> bool:
        return cfg.is_allowed(user_id)

    def deny(chat_id: int, user_id: int) -> None:
        bot.send_message(
            chat_id,
            "⛔️ Доступ закрыт.\n\n"
            f"Твой Telegram ID: <code>{user_id}</code>\n"
            "Добавь его в <code>ADMIN_IDS</code> в файле .env и перезапусти бота "
            "(<code>docker compose restart bot</code>).",
        )

    def owned_row(server_id: int, user_id: int) -> Optional[dict[str, Any]]:
        row = database.get(server_id)
        if not row or row["status"] == "deleted":
            return None
        if row["owner_id"] != user_id and not cfg.is_admin(user_id):
            return None
        return row

    def show_menu(chat_id: int, user_id: int, message_id: Optional[int] = None) -> None:
        servers = database.list_by_owner(user_id)
        text = (
            "🎮 <b>Minecraft PE серверы</b>\n"
            f"Ядро: {CORE_LABEL}\n\n"
            "Нажми кнопку — бот сам выберет порт, поднимет контейнер, "
            "откроет порт на роутере и выдаст рабочий адрес."
        )
        if servers:
            text += f"\n\nСейчас у тебя серверов: {len(servers)}"
        keyboard = main_keyboard(bool(servers))
        if message_id:
            try:
                bot.edit_message_text(
                    text, chat_id, message_id, reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
                return
            except Exception:  # noqa: BLE001 - message unchanged / too old
                pass
        bot.send_message(chat_id, text, reply_markup=keyboard, disable_web_page_preview=True)

    def send_card(chat_id: int, row: dict[str, Any], message_id: Optional[int] = None,
                  status: Optional[dict[str, Any]] = None) -> None:
        text = server_card(row, status)
        keyboard = server_keyboard(row)
        if message_id:
            try:
                bot.edit_message_text(
                    text, chat_id, message_id, reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
                return
            except Exception:  # noqa: BLE001
                pass
        bot.send_message(chat_id, text, reply_markup=keyboard, disable_web_page_preview=True)

    # --------------------------------------------------------------- commands

    @bot.message_handler(commands=["start", "menu"])
    def handle_start(message: Any) -> None:
        if not allowed(message.from_user.id):
            deny(message.chat.id, message.from_user.id)
            return
        show_menu(message.chat.id, message.from_user.id)

    @bot.message_handler(commands=["help"])
    def handle_help(message: Any) -> None:
        bot.send_message(
            message.chat.id,
            "<b>Команды</b>\n"
            "/start — главное меню с кнопкой «Создать сервер»\n"
            "/servers — список моих серверов\n"
            "/id — мой Telegram ID\n"
            "/diag — проверка Docker и образа\n\n"
            "В карточке сервера есть старт/стоп, рестарт, логи, консоль и кнопка "
            "обновления адреса.",
        )

    @bot.message_handler(commands=["id"])
    def handle_id(message: Any) -> None:
        bot.send_message(message.chat.id, f"Твой ID: <code>{message.from_user.id}</code>")

    @bot.message_handler(commands=["diag"])
    def handle_diag(message: Any) -> None:
        if not allowed(message.from_user.id):
            deny(message.chat.id, message.from_user.id)
            return
        problems = provisioner.check_environment()
        if problems:
            bot.send_message(
                message.chat.id,
                "⚠️ Проблемы:\n" + "\n".join(f"• {html.escape(p)}" for p in problems),
            )
        else:
            bot.send_message(
                message.chat.id,
                "✅ Docker доступен, образ сервера собран.\n"
                f"Режим адреса: <code>{cfg.address_mode}</code>\n"
                f"Пул портов: <code>{cfg.port_start}-{cfg.port_end}</code>",
            )

    @bot.message_handler(commands=["servers"])
    def handle_servers(message: Any) -> None:
        if not allowed(message.from_user.id):
            deny(message.chat.id, message.from_user.id)
            return
        rows = database.list_by_owner(message.from_user.id)
        if not rows:
            show_menu(message.chat.id, message.from_user.id)
            return
        for row in rows:
            send_card(message.chat.id, row)

    # ------------------------------------------------------- console messages

    @bot.message_handler(func=lambda m: (m.chat.id, m.from_user.id) in awaiting_command,
                         content_types=["text"])
    def handle_console_input(message: Any) -> None:
        key = (message.chat.id, message.from_user.id)
        with lock:
            server_id = awaiting_command.pop(key, None)
        if server_id is None:
            return
        row = owned_row(server_id, message.from_user.id)
        if not row:
            bot.send_message(message.chat.id, "Сервер не найден.")
            return
        command = message.text.strip()
        if not command or command.startswith("/"):
            bot.send_message(message.chat.id, "Отменено.")
            return
        try:
            provisioner.console(row, command)
        except ProvisionError as exc:
            bot.send_message(message.chat.id, f"❌ {html.escape(str(exc))}")
            return
        time.sleep(1.5)
        try:
            tail = provisioner.logs(row, tail=12)
        except ProvisionError:
            tail = ""
        text = f"✅ Отправлено в консоль: <code>{html.escape(command)}</code>"
        if tail:
            text += f"\n\n<pre>{html.escape(tail[-2500:])}</pre>"
        bot.send_message(message.chat.id, text)

    # -------------------------------------------------------------- callbacks

    @bot.callback_query_handler(func=lambda call: call.data.startswith("menu:"))
    def handle_menu(call: Any) -> None:
        if not allowed(call.from_user.id):
            bot.answer_callback_query(call.id, "Доступ закрыт", show_alert=True)
            return
        action = call.data.split(":", 1)[1]

        if action == "home":
            bot.answer_callback_query(call.id)
            show_menu(call.message.chat.id, call.from_user.id, call.message.message_id)
            return

        if action == "list":
            bot.answer_callback_query(call.id)
            rows = database.list_by_owner(call.from_user.id)
            if not rows:
                show_menu(call.message.chat.id, call.from_user.id, call.message.message_id)
                return
            for row in rows:
                send_card(call.message.chat.id, row)
            return

        if action == "create":
            bot.answer_callback_query(call.id, "Создаю…")
            progress = bot.send_message(
                call.message.chat.id,
                "⏳ <b>Создаю сервер…</b>\n"
                "1/4 выбираю свободный UDP-порт\n"
                "2/4 поднимаю контейнер GenisysPro\n"
                "3/4 открываю порт (UPnP/NAT-PMP/туннель)\n"
                "4/4 проверяю внешний адрес",
            )
            try:
                row = provisioner.create_server(
                    owner_id=call.from_user.id, chat_id=call.message.chat.id
                )
            except ProvisionError as exc:
                bot.edit_message_text(
                    f"❌ Не получилось:\n{html.escape(str(exc))}",
                    call.message.chat.id,
                    progress.message_id,
                )
                return
            except Exception as exc:  # noqa: BLE001
                LOG.exception("create failed")
                bot.edit_message_text(
                    f"❌ Внутренняя ошибка: {html.escape(str(exc))}",
                    call.message.chat.id,
                    progress.message_id,
                )
                return
            send_card(call.message.chat.id, row, progress.message_id)
            return

        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("srv:"))
    def handle_server_action(call: Any) -> None:
        if not allowed(call.from_user.id):
            bot.answer_callback_query(call.id, "Доступ закрыт", show_alert=True)
            return
        _, raw_id, action = call.data.split(":", 2)
        row = owned_row(int(raw_id), call.from_user.id)
        if not row:
            bot.answer_callback_query(call.id, "Сервер не найден", show_alert=True)
            return
        chat_id = call.message.chat.id
        message_id = call.message.message_id

        try:
            if action == "status":
                bot.answer_callback_query(call.id, "Обновляю…")
                status = provisioner.status(row)
                send_card(chat_id, database.get(int(raw_id)) or row, message_id, status)

            elif action == "start":
                bot.answer_callback_query(call.id, "Запускаю…")
                row = provisioner.start(row)
                send_card(chat_id, row, message_id)

            elif action == "stop":
                bot.answer_callback_query(call.id, "Останавливаю…")
                row = provisioner.stop(row)
                send_card(chat_id, row, message_id)

            elif action == "restart":
                bot.answer_callback_query(call.id, "Перезапуск…")
                row = provisioner.restart(row)
                send_card(chat_id, row, message_id)

            elif action == "addr":
                bot.answer_callback_query(call.id, "Обновляю адрес…")
                row = provisioner.refresh_address(row)
                send_card(chat_id, row, message_id)

            elif action == "logs":
                bot.answer_callback_query(call.id)
                tail = provisioner.logs(row, tail=40)
                bot.send_message(
                    chat_id,
                    f"📜 <b>Логи #{row['id']}</b>\n<pre>{html.escape(tail[-3500:])}</pre>",
                )

            elif action == "cmd":
                bot.answer_callback_query(call.id)
                with lock:
                    awaiting_command[(chat_id, call.from_user.id)] = int(raw_id)
                bot.send_message(
                    chat_id,
                    f"🖥 Отправь команду для сервера #{row['id']} одним сообщением.\n"
                    "Например: <code>op Steve</code>, <code>gamemode 1 Steve</code>, "
                    "<code>save-all</code>, <code>tps</code>.",
                )

            elif action == "del":
                bot.answer_callback_query(call.id)
                bot.edit_message_text(
                    f"🗑 Удалить сервер #{row['id']} вместе с мирами и плагинами?",
                    chat_id,
                    message_id,
                    reply_markup=confirm_keyboard(int(raw_id)),
                )

            elif action == "delyes":
                bot.answer_callback_query(call.id, "Удаляю…")
                provisioner.delete(row)
                bot.edit_message_text(f"🗑 Сервер #{row['id']} удалён.", chat_id, message_id)
                show_menu(chat_id, call.from_user.id)

            else:
                bot.answer_callback_query(call.id)

        except ProvisionError as exc:
            bot.send_message(chat_id, f"❌ {html.escape(str(exc))}")
        except Exception as exc:  # noqa: BLE001
            LOG.exception("action %s failed", action)
            bot.send_message(chat_id, f"❌ Ошибка: {html.escape(str(exc))}")

    return bot


# ----------------------------------------------------------------- background


def maintenance_loop(cfg: Config, database: Database, provisioner: Provisioner, bot: Any) -> None:
    """Renew port leases, stop expired servers, keep the healthcheck fresh."""
    while True:
        try:
            with open(ALIVE_FILE, "w", encoding="ascii") as handle:
                handle.write(str(int(time.time())))
        except OSError:
            pass

        try:
            provisioner.renew_addresses()
        except Exception as exc:  # noqa: BLE001
            LOG.warning("lease renewal failed: %s", exc)

        try:
            for row in provisioner.expired_servers():
                LOG.info("server #%s expired -> stopping", row["id"])
                try:
                    provisioner.stop(row)
                    bot.send_message(
                        int(row["chat_id"]),
                        f"⏰ Сервер #{row['id']} остановлен — истёк срок аренды "
                        f"({cfg.server_ttl_minutes} мин), порт закрыт.\n"
                        "Миры сохранены — можно запустить снова кнопкой «▶️ Запустить».",
                    )
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("cannot stop expired #%s: %s", row["id"], exc)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("expiry check failed: %s", exc)

        try:
            provisioner.reconcile()
        except Exception as exc:  # noqa: BLE001
            LOG.debug("reconcile failed: %s", exc)

        time.sleep(60)


def main() -> None:
    cfg = Config.load()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    if cfg.timezone:
        os.environ.setdefault("TZ", cfg.timezone)

    database = Database(cfg.db_path)
    provisioner = Provisioner(cfg, database)
    bot = build_bot(cfg, database, provisioner)

    for problem in provisioner.check_environment():
        LOG.warning("startup check: %s", problem)
    try:
        provisioner.reconcile()
    except Exception as exc:  # noqa: BLE001
        LOG.warning("reconcile on startup failed: %s", exc)

    threading.Thread(
        target=maintenance_loop,
        args=(cfg, database, provisioner, bot),
        name="maintenance",
        daemon=True,
    ).start()

    try:
        bot.set_my_commands(
            [
                types.BotCommand("start", "Меню и кнопка создания сервера"),
                types.BotCommand("servers", "Мои серверы"),
                types.BotCommand("diag", "Диагностика"),
                types.BotCommand("id", "Мой Telegram ID"),
                types.BotCommand("help", "Помощь"),
            ]
        )
    except Exception as exc:  # noqa: BLE001
        LOG.debug("set_my_commands failed: %s", exc)

    LOG.info("bot started (address mode: %s)", cfg.address_mode)
    bot.infinity_polling(timeout=30, long_polling_timeout=30, skip_pending=True)


if __name__ == "__main__":
    main()
