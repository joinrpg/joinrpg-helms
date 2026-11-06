#!/usr/bin/env python3
"""Синхронизация дашборда с API Coroot 1.27.4. По умолчанию — только план."""

import argparse
import getpass
import http.cookiejar
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request


class SyncError(Exception):
    """Ошибка с безопасным для вывода сообщением."""


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise SyncError("Некорректный идентификатор проекта или дашборда.")
    return value


def validate_url(value):
    try:
        url = urllib.parse.urlsplit(value)
        _ = url.port
    except ValueError:
        raise SyncError("Некорректный URL Coroot.") from None
    if (not url.hostname or url.username is not None or url.password is not None
            or url.query or url.fragment or any(c.isspace() for c in value)
            or ".." in url.path or "%" in url.path):
        raise SyncError("URL не должен содержать учётные данные, query или fragment.")
    if url.scheme != "https" and not (
        url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost", "::1")
    ):
        raise SyncError("Нужен HTTPS, HTTP разрешён только для локального port-forward.")
    return value.rstrip("/")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SyncError("Перенаправление API запрещено. Проверь URL Coroot.")


class Client:
    def __init__(self, url, token=None, timeout=30):
        self.url = validate_url(url)
        if token and (not token.isascii() or any(c.isspace() or ord(c) < 32 for c in token)):
            raise SyncError("Пользовательский API-ключ содержит недопустимые символы.")
        self.token = token
        self.timeout = timeout
        self.opener = urllib.request.build_opener(
            NoRedirect(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def request(self, method, path, payload=None):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.url + path, data=body,
                                         headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                # Ответ ограничен, тело ошибок и заголовки с секретами не выводятся.
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise SyncError("Ответ API больше 8 MiB.")
                text = raw.decode("utf-8")
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            raise SyncError(f"API вернул HTTP {status}. Проверь авторизацию и права.") from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            raise SyncError("Сетевая ошибка или таймаут API Coroot.") from None
        except UnicodeError:
            raise SyncError("Ответ API не является UTF-8.") from None
        if method == "GET":
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                raise SyncError("Ожидался JSON от API. Проверь адрес и авторизацию.") from None
        return text


def data(response):
    if not isinstance(response, dict) or "data" not in response:
        raise SyncError("Неожиданный формат ответа API Coroot 1.27.4.")
    return response["data"]


def load_dashboard(path, machine_id):
    if not re.fullmatch(r"[a-fA-F0-9]{32}", machine_id):
        raise SyncError("machine-id должен содержать 32 шестнадцатеричных символа.")
    try:
        text = path.read_text(encoding="utf-8")
        document = json.loads(text.replace("__MACHINE_ID__", machine_id))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SyncError("Не удалось прочитать JSON дашборда.") from None
    if (not isinstance(document, dict)
            or not isinstance(document.get("name"), str) or not document["name"].strip()
            or not isinstance(document.get("description"), str)
            or not isinstance(document.get("config"), dict)
            or not isinstance(document["config"].get("groups"), list)):
        raise SyncError("Нужны поля name, description и config.groups.")
    return {key: document[key] for key in ("name", "description", "config")}


def backup_dashboard(directory, project, dashboard_id, document):
    directory.mkdir(parents=True, exist_ok=True)
    # Уникальный файл не перезаписывает предыдущую резервную копию.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json",
                                     prefix=f"{project}-{dashboard_id}-",
                                     dir=directory, delete=False) as saved:
        os.chmod(saved.name, 0o600)
        json.dump(document, saved, ensure_ascii=False, indent=2)
        saved.write("\n")
        return Path(saved.name)


def synchronize(client, project, document, *, apply=False, backup_dir=None):
    project = identifier(project)
    route = f"/api/project/{project}/dashboards"
    entries = data(client.request("GET", route))
    if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
        raise SyncError("API не вернул список дашбордов. Проект может быть недоступен.")
    matches = [entry for entry in entries if entry.get("name") == document["name"]]
    if len(matches) > 1:
        raise SyncError("Найдено несколько дашбордов с таким именем. Изменения отменены.")
    dashboard_id = None
    current = None
    if matches:
        dashboard_id = identifier(matches[0].get("id"))
        current = data(client.request("GET", route + "/" + dashboard_id))
        if not isinstance(current, dict) or not isinstance(current.get("config"), dict):
            raise SyncError("Не удалось прочитать существующий дашборд.")
        if (current["config"] == document["config"]
                and matches[0].get("description", "") == document["description"]):
            return dashboard_id, "Изменений нет"
    action = "Обновление" if dashboard_id else "Создание"
    if not apply:
        return dashboard_id, action + " (план, без записи)"
    if current is not None:
        # Данные описания берём из списка: GetDashboard в 1.27.4 его не читает.
        old = {"name": matches[0]["name"],
               "description": matches[0].get("description", ""),
               "config": current["config"]}
        if backup_dir is None:
            raise SyncError("Для обновления нужен каталог резервных копий.")
        backup_dashboard(backup_dir, project, dashboard_id, old)
    if dashboard_id is None:
        result = client.request("POST", route, {
            "action": "create", "name": document["name"],
            "description": document["description"],
        })
        if not isinstance(result, str):
            raise SyncError("API не вернул идентификатор созданного дашборда.")
        dashboard_id = identifier(result.strip())
    elif matches[0].get("description", "") != document["description"]:
        client.request("POST", route + "/" + dashboard_id, {
            "action": "update", "name": document["name"],
            "description": document["description"],
        })
    client.request("POST", route + "/" + dashboard_id, {"action": "save", **document})
    actual = data(client.request("GET", route + "/" + dashboard_id))
    if not isinstance(actual, dict) or actual.get("config") != document["config"]:
        raise SyncError("Проверка сохранения не прошла. Повторно прочитай дашборд перед применением.")
    return dashboard_id, action + " выполнено, конфигурация проверена"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=Path(__file__).parents[1] / "dashboards/mssql.json")
    parser.add_argument("--machine-id", required=True)
    parser.add_argument("--url", help="URL Coroot, HTTPS или локальный port-forward")
    parser.add_argument("--project", help="ID проекта из URL интерфейса, не его имя")
    parser.add_argument("--user", default=os.environ.get("COROOT_USER") or "admin",
                        help="Логин: COROOT_USER или admin")
    parser.add_argument("--apply", action="store_true", help="Применить изменения")
    parser.add_argument("--render", action="store_true", help="Вывести JSON без сети и авторизации")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--backup-dir", type=Path,
                        default=Path(__file__).resolve().parents[3] / "docs/coroot-dashboard-backups")
    args = parser.parse_args(argv)
    try:
        document = load_dashboard(args.file, args.machine_id)
        if args.render:
            if args.apply:
                raise SyncError("--render нельзя совмещать с --apply.")
            print(json.dumps(document, ensure_ascii=False, indent=2))
            return 0
        if not args.url or not args.project:
            raise SyncError("Укажи --url и --project либо используй --render.")
        identifier(args.project)
        if not 0 < args.timeout <= 300:
            raise SyncError("Таймаут должен быть больше 0 и не больше 300 секунд.")
        token = os.environ.get("COROOT_USER_API_KEY")
        client = Client(args.url, token=token, timeout=args.timeout)
        if not token:
            password = os.environ.get("COROOT_PASSWORD") or getpass.getpass("Пароль пользователя Coroot: ")
            if not password:
                raise SyncError("Пустой пароль.")
            client.request("POST", "/api/login", {"email": args.user, "password": password})
            del password
        dashboard_id, message = synchronize(client, args.project, document,
                                             apply=args.apply, backup_dir=args.backup_dir)
        print(message)
        if dashboard_id:
            print(f"{client.url}/p/{args.project}/dashboards/{dashboard_id}")
        return 0
    except (SyncError, OSError) as error:
        # OSError может содержать произвольный путь/текст: не печатаем его целиком.
        print(str(error) if isinstance(error, SyncError) else "Ошибка чтения/записи локального файла.", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("Ввод отменён.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
