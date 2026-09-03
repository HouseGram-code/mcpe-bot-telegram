#!/usr/bin/env python3
"""Offline self-test: checks the bot logic without Docker, Telegram or network.

Run:  python3 tests/selftest.py
"""

from __future__ import annotations

import os
import pathlib
import re
import socket
import struct
import sys
import threading
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))


# ------------------------------------------------------------- fake docker sdk


class DockerException(Exception):
    pass


class APIError(DockerException):
    pass


class NotFound(DockerException):
    pass


class ImageNotFound(NotFound):
    pass


class FakeStdin:
    def __init__(self) -> None:
        self.data = b""

    def sendall(self, payload: bytes) -> None:
        self.data += payload

    def close(self) -> None:
        pass


class FakeContainer:
    def __init__(self, name, kwargs=None):
        self.name = name
        self.kwargs = kwargs or {}
        self.status = "running"
        self.removed = False
        self.stdin = FakeStdin()

    def start(self):
        self.status = "running"

    def stop(self, timeout=10):
        self.status = "exited"

    def reload(self):
        pass

    def remove(self, force=False):
        self.removed = True
        self.status = "removed"

    def logs(self, tail=100):
        return b'[Server thread/INFO]: Done! For help, type "help"\n'

    def stats(self, stream=False):
        return {
            "cpu_stats": {
                "cpu_usage": {"total_usage": 2_000_000},
                "system_cpu_usage": 100_000_000,
                "online_cpus": 2,
            },
            "precpu_stats": {
                "cpu_usage": {"total_usage": 1_000_000},
                "system_cpu_usage": 50_000_000,
            },
            "memory_stats": {
                "usage": 220 * 1024 * 1024,
                "limit": 1024 * 1024 * 1024,
                "stats": {"cache": 20 * 1024 * 1024},
            },
        }

    def attach_socket(self, params=None):
        return self.stdin


class FakeContainers:
    def __init__(self):
        self.items = {}

    def get(self, name):
        if name not in self.items:
            raise NotFound(name)
        return self.items[name]

    def run(self, **kwargs):
        container = FakeContainer(kwargs["name"], kwargs)
        self.items[container.name] = container
        return container

    def list(self, **kwargs):
        return list(self.items.values())


class FakeVolume:
    def __init__(self, name):
        self.name = name
        self.removed = False

    def remove(self, force=False):
        self.removed = True


class FakeVolumes:
    def __init__(self):
        self.items = {}

    def create(self, name, labels=None):
        volume = FakeVolume(name)
        self.items[name] = volume
        return volume

    def get(self, name):
        if name not in self.items:
            raise NotFound(name)
        return self.items[name]


class FakeImages:
    def __init__(self, present=True):
        self.present = present

    def get(self, name):
        if not self.present:
            raise ImageNotFound(name)
        return {"Id": "sha256:fake"}


class FakeDocker:
    def __init__(self, image_present=True):
        self.containers = FakeContainers()
        self.volumes = FakeVolumes()
        self.images = FakeImages(image_present)

    def ping(self):
        return True


docker_module = types.ModuleType("docker")
errors_module = types.ModuleType("docker.errors")
errors_module.DockerException = DockerException
errors_module.APIError = APIError
errors_module.NotFound = NotFound
errors_module.ImageNotFound = ImageNotFound
docker_module.errors = errors_module
docker_module.from_env = lambda **kwargs: FakeDocker()
sys.modules["docker"] = docker_module
sys.modules["docker.errors"] = errors_module


# ----------------------------------------------------------------- fake telebot


class InlineKeyboardButton:
    def __init__(self, text, callback_data=None, url=None):
        self.text = text
        self.callback_data = callback_data
        self.url = url


class InlineKeyboardMarkup:
    def __init__(self, row_width=3):
        self.row_width = row_width
        self.keyboard = []

    def add(self, *buttons, row_width=None):
        self.keyboard.append(list(buttons))
        return self


class BotCommand:
    def __init__(self, command, description):
        self.command = command
        self.description = description


class FakeTeleBot:
    def __init__(self, token, **kwargs):
        self.token = token
        self.options = kwargs
        self.message_handlers = []
        self.callback_handlers = []
        self.sent = []

    def message_handler(self, *args, **kwargs):
        def decorator(function):
            self.message_handlers.append(function)
            return function

        return decorator

    def callback_query_handler(self, *args, **kwargs):
        def decorator(function):
            self.callback_handlers.append(function)
            return function

        return decorator

    def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return types.SimpleNamespace(message_id=len(self.sent))

    def edit_message_text(self, text, chat_id, message_id, **kwargs):
        self.sent.append((chat_id, text))
        return True

    def answer_callback_query(self, call_id, text=None, show_alert=False):
        return True

    def set_my_commands(self, commands):
        return True


telebot_module = types.ModuleType("telebot")
telebot_types = types.ModuleType("telebot.types")
telebot_types.InlineKeyboardButton = InlineKeyboardButton
telebot_types.InlineKeyboardMarkup = InlineKeyboardMarkup
telebot_types.BotCommand = BotCommand
telebot_module.types = telebot_types
telebot_module.TeleBot = FakeTeleBot
sys.modules["telebot"] = telebot_module
sys.modules["telebot.types"] = telebot_types


# ------------------------------------------------------------------- imports

os.environ.update(
    {
        "BOT_TOKEN": "123456:TEST-TOKEN",
        "ADMIN_IDS": "111, 222",
        "PORT_RANGE_START": "29132",
        "PORT_RANGE_END": "29140",
        "SERVER_TTL_MINUTES": "120",
        "ADDRESS_MODE": "auto",
        "DB_PATH": "/tmp/mcpe-selftest.sqlite3",
        "MAX_SERVERS_PER_USER": "5",
    }
)

import bot as bot_module  # noqa: E402
import natpmp  # noqa: E402
import netinfo  # noqa: E402
import upnp  # noqa: E402
from config import Config  # noqa: E402
from db import Database  # noqa: E402
from provisioner import Provisioner, _read_stats  # noqa: E402
from publisher import Published, Publisher  # noqa: E402


IGD_XML = b"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <device>
    <deviceType>urn:schemas-upnp-org:device:InternetGatewayDevice:1</deviceType>
    <deviceList><device><deviceList><device>
      <serviceList>
        <service>
          <serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType>
          <controlURL>/ctl/IPConn</controlURL>
        </service>
      </serviceList>
    </device></deviceList></device></deviceList>
  </device>
</root>"""


def reset_db(path):
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass
    return path


class StubPublisher:
    """Pretends the router accepted the UPnP mapping."""

    def __init__(self):
        self.released = []
        self.renewed = []

    def publish(self, port, label="mcpe"):
        return Published(address=f"203.0.113.7:{port}", kind="upnp", note="stub")

    def renew(self, published, port, label="mcpe"):
        self.renewed.append(port)
        return True

    def release(self, published, port):
        self.released.append(port)


def make_stack(db_path):
    cfg = Config.load()
    database = Database(reset_db(db_path))
    fake_docker = FakeDocker()
    prov = Provisioner(cfg, database, docker_client=fake_docker, publisher=StubPublisher())
    return cfg, database, fake_docker, prov


class ConfigTests(unittest.TestCase):
    def test_env_parsing(self):
        cfg = Config.load()
        self.assertEqual(cfg.bot_token, "123456:TEST-TOKEN")
        self.assertEqual(cfg.admin_ids, frozenset({111, 222}))
        self.assertEqual(list(cfg.port_pool)[:2], [29132, 29133])
        self.assertEqual(cfg.ttl_seconds, 120 * 60)
        self.assertTrue(cfg.is_allowed(111))
        self.assertFalse(cfg.is_allowed(999))


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(reset_db("/tmp/mcpe-db-test.sqlite3"))

    def tearDown(self):
        self.db.close()

    def test_crud_and_port_bookkeeping(self):
        server_id = self.db.create_server(
            owner_id=1, chat_id=2, name="test", container="c", volume="v", port=29132
        )
        self.assertEqual(self.db.used_ports(), {29132})
        self.db.update(
            server_id, status="running", address="1.2.3.4:29132", publish_meta={"note": "hi"}
        )
        row = self.db.get(server_id)
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["publish_meta"]["note"], "hi")
        self.assertEqual(self.db.count_by_owner(1), 1)
        self.db.mark_deleted(server_id)
        self.assertEqual(self.db.count_by_owner(1), 0)
        self.assertEqual(self.db.used_ports(), set())


class UpnpTests(unittest.TestCase):
    def test_control_url_is_resolved(self):
        parsed = upnp.parse_device_description(IGD_XML, "http://192.168.1.1:5000/rootDesc.xml")
        self.assertIsNotNone(parsed)
        control_url, service_type = parsed
        self.assertEqual(control_url, "http://192.168.1.1:5000/ctl/IPConn")
        self.assertEqual(service_type, "urn:schemas-upnp-org:service:WANIPConnection:1")

    def test_broken_xml_is_ignored(self):
        self.assertIsNone(upnp.parse_device_description(b"<not-xml", "http://x/"))


class NatPmpTests(unittest.TestCase):
    """Talks to a local fake NAT-PMP router, so no real network is touched."""

    def test_add_mapping_round_trip(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]
        original_port = natpmp.NATPMP_PORT
        natpmp.NATPMP_PORT = port

        def responder():
            data, addr = server.recvfrom(64)
            version, opcode = struct.unpack("!BB", data[:2])
            internal, suggested, lifetime = struct.unpack("!HHI", data[4:12])
            reply = struct.pack(
                "!BBHIHHI", version, opcode + 128, 0, 42, internal, suggested, lifetime
            )
            server.sendto(reply, addr)

        threading.Thread(target=responder, daemon=True).start()
        try:
            mapping = natpmp.add_mapping(
                internal_port=29132, external_port=29132, lifetime=3600, gateway="127.0.0.1"
            )
        finally:
            natpmp.NATPMP_PORT = original_port
            server.close()
        self.assertEqual(mapping.internal_port, 29132)
        self.assertEqual(mapping.external_port, 29132)
        self.assertEqual(mapping.lifetime, 3600)


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.original = (
            upnp.discover,
            natpmp.default_gateway,
            netinfo.public_ip,
            netinfo.local_ip,
        )
        upnp.discover = lambda timeout=3.0: None
        natpmp.default_gateway = lambda: None
        netinfo.local_ip = lambda: "192.168.1.50"

    def tearDown(self):
        (
            upnp.discover,
            natpmp.default_gateway,
            netinfo.public_ip,
            netinfo.local_ip,
        ) = self.original

    def test_falls_back_to_direct_public_ip(self):
        netinfo.public_ip = lambda timeout=6.0: "203.0.113.7"
        result = Publisher(Config.load(), docker_client=None).publish(29132, label="test")
        self.assertEqual(result.kind, "direct")
        self.assertEqual(result.address, "203.0.113.7:29132")

    def test_local_address_is_last_resort(self):
        netinfo.public_ip = lambda timeout=6.0: None
        result = Publisher(Config.load(), docker_client=None).publish(29133)
        self.assertEqual(result.kind, "local")
        self.assertTrue(result.address.endswith(":29133"))


class ProvisionerTests(unittest.TestCase):
    def setUp(self):
        self.cfg, self.db, self.docker, self.prov = make_stack("/tmp/mcpe-prov-test.sqlite3")

    def tearDown(self):
        self.db.close()

    def test_create_start_stop_delete(self):
        row = self.prov.create_server(owner_id=111, chat_id=555)
        self.assertEqual(row["status"], "running")
        self.assertTrue(row["address"].startswith("203.0.113.7:"))
        self.assertIn(row["port"], list(self.cfg.port_pool))

        container = self.docker.containers.get(row["container"])
        self.assertEqual(container.kwargs["network_mode"], "host")
        self.assertEqual(container.kwargs["environment"]["SERVER_PORT"], str(row["port"]))
        self.assertEqual(container.kwargs["image"], self.cfg.server_image)
        self.assertEqual(container.kwargs["labels"]["mcpe-bot.owner"], "111")
        self.assertTrue(container.kwargs["stdin_open"])

        status = self.prov.status(row)
        self.assertEqual(status["state"], "running")
        self.assertIsNotNone(status["cpu"])

        self.prov.console(row, "say hello")
        self.assertIn(b"say hello\n", container.stdin.data)

        stopped = self.prov.stop(row)
        self.assertEqual(stopped["status"], "stopped")

        started = self.prov.start(stopped)
        self.assertEqual(started["status"], "running")

        self.prov.delete(started)
        self.assertTrue(container.removed)
        self.assertEqual(self.db.count_by_owner(111), 0)

    def test_each_server_gets_its_own_port(self):
        first = self.prov.create_server(owner_id=111, chat_id=1)
        second = self.prov.create_server(owner_id=111, chat_id=1)
        self.assertNotEqual(first["port"], second["port"])

    def test_missing_image_is_reported(self):
        self.docker.images.present = False
        problems = self.prov.check_environment()
        self.assertTrue(problems)

    def test_stats_parsing(self):
        parsed = _read_stats(FakeContainer("x").stats())
        self.assertAlmostEqual(parsed["cpu"], 4.0, places=1)
        self.assertAlmostEqual(parsed["memory"], 200.0, places=1)


class BotTests(unittest.TestCase):
    def setUp(self):
        self.cfg, self.db, self.docker, self.prov = make_stack("/tmp/mcpe-bot-test.sqlite3")

    def tearDown(self):
        self.db.close()

    def test_single_create_button(self):
        keyboard = bot_module.main_keyboard(has_servers=False)
        self.assertEqual(len(keyboard.keyboard), 1)
        button = keyboard.keyboard[0][0]
        self.assertIn("Создать сервер", button.text)
        self.assertEqual(button.callback_data, "menu:create")

    def test_card_shows_real_address_and_deeplink(self):
        row = self.prov.create_server(owner_id=111, chat_id=555)
        card = bot_module.server_card(row, {"state": "running", "cpu": 3.2, "memory": 210.0})
        self.assertIn(row["address"], card)
        self.assertIn("LiteCore 1.1.5", card)
        self.assertIn("minecraft://?addExternalServer", card)

    def test_server_keyboard_actions(self):
        row = self.prov.create_server(owner_id=111, chat_id=555)
        keyboard = bot_module.server_keyboard(row)
        actions = {
            button.callback_data
            for line in keyboard.keyboard
            for button in line
            if button.callback_data
        }
        for action in ("status", "stop", "restart", "addr", "logs", "cmd", "del"):
            self.assertIn(f"srv:{row['id']}:{action}", actions)

    def test_handlers_are_registered(self):
        bot = bot_module.build_bot(self.cfg, self.db, self.prov)
        self.assertGreaterEqual(len(bot.message_handlers), 5)
        self.assertGreaterEqual(len(bot.callback_handlers), 2)

    def test_port_probe_detects_busy_port(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", 0))
        busy = sock.getsockname()[1]
        try:
            self.assertFalse(netinfo.udp_port_free(busy))
            self.assertTrue(netinfo.udp_port_free(busy + 1))
        finally:
            sock.close()


class SecurityTests(unittest.TestCase):
    """Guards that keep the project safe to push to GitHub."""

    TOKEN_RE = re.compile(r"[0-9]{6,12}:[A-Za-z0-9_-]{35}")
    SKIP_DIRS = {".git", "__pycache__", "data", "secrets", ".venv"}

    def test_no_token_like_string_in_tracked_files(self):
        offenders = []
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(ROOT)
            if relative.name == ".env" or relative.suffix in {".phar", ".zip", ".pyc"}:
                continue
            if set(relative.parts) & self.SKIP_DIRS:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if self.TOKEN_RE.search(text):
                offenders.append(str(relative))
        self.assertEqual(offenders, [], f"token-like strings found in: {offenders}")

    def test_gitignore_covers_secrets(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for entry in (".env", "secrets/*", "data/", "*.sqlite3"):
            self.assertIn(entry, text)

    def test_env_example_has_no_real_token(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("BOT_TOKEN=\n", text)
        self.assertIsNone(self.TOKEN_RE.search(text))

    def test_token_can_live_in_a_file_outside_the_repo(self):
        secret_path = pathlib.Path("/tmp/mcpe-token-selftest")
        secret_value = "123456:" + "F" * 32
        secret_path.write_text(secret_value + "\n", encoding="utf-8")
        os.environ.pop("BOT_TOKEN", None)
        os.environ["BOT_TOKEN_FILE"] = str(secret_path)
        try:
            self.assertEqual(Config.load().bot_token, secret_value)
        finally:
            os.environ["BOT_TOKEN"] = "123456:TEST-TOKEN"
            os.environ.pop("BOT_TOKEN_FILE", None)
            secret_path.unlink(missing_ok=True)

    def test_missing_token_stops_the_bot(self):
        os.environ.pop("BOT_TOKEN", None)
        try:
            with self.assertRaises(SystemExit):
                Config.load()
        finally:
            os.environ["BOT_TOKEN"] = "123456:TEST-TOKEN"


if __name__ == "__main__":
    unittest.main(verbosity=2)
