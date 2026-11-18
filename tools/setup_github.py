"""Prepares the GitHub side of publishing from your terminal: repository, Pages and secrets.

    python tools/setup_github.py

Asking before each change on GitHub, it
  1. creates the repository named by `git remote get-url origin` if it is missing (public);
  2. makes Pages build from GitHub Actions;
  3. asks for the eClass ID and password, checks them against eClass, and stores them
     as the ECLASS_USERNAME / ECLASS_PASSWORD secrets;
  4. stores DATA_PASSPHRASE: one you type, or a generated one shown once.

Secret values go from this terminal straight to GitHub, encrypted with the
repository's public key (a libsodium sealed box, as GitHub requires). It signs in
with the GitHub login that git keeps in Git Credential Manager, the one `git push`
uses. Run it again whenever the eClass password changes.

Needs `pip install pynacl` on top of requirements.txt.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
import fetch  # noqa: E402  (read_password, Client.login, MIN_PASSPHRASE)

API = "https://api.github.com"


def say(text: str = "") -> None:
    print(text, flush=True)


def ask_yes(question: str) -> bool:
    return input(f"{question} [Y/n] ").strip().lower() in ("", "y", "yes", "д", "да")


def origin_repo() -> tuple[str, str]:
    url = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"],
                         capture_output=True, text=True, check=True).stdout.strip()
    match = re.search(r"github\.com[/:]([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    if not match:
        raise SystemExit(f"origin is not a GitHub repository: {url}")
    return match[1], match[2]


def github_token() -> str:
    """The token Git Credential Manager keeps for github.com. It is never printed."""
    result = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                            capture_output=True, text=True)
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if result.returncode != 0 or not fields.get("password"):
        raise SystemExit("git has no GitHub login saved: run `git push` once, sign in, then run this again")
    return fields["password"]


def seal(public_key_b64: str, value: str) -> str:
    """GitHub's secret format: a libsodium sealed box for the repository key, base64-encoded."""
    from nacl import encoding, public

    box = public.SealedBox(public.PublicKey(public_key_b64.encode("ascii"), encoding.Base64Encoder()))
    return base64.b64encode(box.encrypt(value.encode("utf-8"))).decode("ascii")


class GitHub:
    def __init__(self, token: str) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "eclass-board-setup",
        })

    def call(self, method: str, path: str, expect: tuple = (200,), **kwargs) -> requests.Response:
        response = self.session.request(method, API + path, timeout=30, **kwargs)
        if response.status_code not in expect:
            try:
                message = response.json().get("message", "")
            except ValueError:
                message = response.text[:200]
            raise SystemExit(f"GitHub {method} {path}: HTTP {response.status_code} {message}")
        return response


def ensure_repo(github: GitHub, login: str, owner: str, name: str) -> str:
    response = github.call("GET", f"/repos/{owner}/{name}", expect=(200, 404))
    if response.status_code == 200:
        return "exists"
    if login.lower() != owner.lower():
        raise SystemExit(f"{owner}/{name} does not exist, and it is not under your account ({login})")
    if not ask_yes(f"Репозитория {owner}/{name} нет. Создать публичный?"):
        raise SystemExit("остановлено")
    github.call("POST", "/user/repos", expect=(201,), json={
        "name": name, "private": False, "has_wiki": False,
        "description": "Домашки, расписание и статистика из eClass IUT",
    })
    say(f"  репозиторий {owner}/{name} создан")
    return "created"


def ensure_pages(github: GitHub, owner: str, name: str) -> str:
    response = github.call("GET", f"/repos/{owner}/{name}/pages", expect=(200, 404))
    if response.status_code == 200 and response.json().get("build_type") == "workflow":
        say("  Pages: собирается из GitHub Actions")
        return "workflow"
    question = ("Pages выключен. Включить со сборкой из GitHub Actions?" if response.status_code == 404
                else "Pages собирается из ветки, а не из Actions. Переключить на GitHub Actions?")
    if not ask_yes(question):
        say("  Pages не тронут: без сборки из Actions сайт не опубликуется")
        return "unchanged"
    if response.status_code == 404:
        github.call("POST", f"/repos/{owner}/{name}/pages", expect=(201,), json={"build_type": "workflow"})
    else:
        github.call("PUT", f"/repos/{owner}/{name}/pages", expect=(204,), json={"build_type": "workflow"})
    say("  Pages: теперь собирается из GitHub Actions")
    return "workflow"


def ask_passphrase() -> tuple[str, bool]:
    """Returns (passphrase, generated)."""
    say()
    say("Пароль для сайта (DATA_PASSPHRASE) понадобится один раз на каждом устройстве.")
    typed = fetch.read_password(
        f"Придумай его (от {fetch.MIN_PASSPHRASE} символов) или нажми Enter, чтобы сгенерировать: ")
    if not typed:
        return secrets.token_urlsafe(18), True
    if len(typed) < fetch.MIN_PASSPHRASE:
        raise SystemExit(f"короче {fetch.MIN_PASSPHRASE} символов: такой пароль легко перебрать")
    if fetch.read_password("Ещё раз: ") != typed:
        raise SystemExit("пароли не совпали")
    return typed, False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", type=Path, help="also write a short report (no secret values) to this file")
    args = ap.parse_args()
    report: dict = {"ok": False}

    def finish(message: str) -> None:
        if args.status:
            args.status.write_text(json.dumps({**report, "message": message}, ensure_ascii=False), encoding="utf-8")

    try:
        owner, name = origin_repo()
        github = GitHub(github_token())
        login = github.call("GET", "/user").json()["login"]
        say(f"GitHub: {login}, репозиторий {owner}/{name}")
        report["repo"] = ensure_repo(github, login, owner, name)
        report["pages"] = ensure_pages(github, owner, name)

        say()
        username = input("eClass ID: ").strip()
        password = fetch.read_password("eClass password (not echoed, not stored): ")
        say(f"Проверяю вход в eClass как {username!r} (пароль: {len(password)} символов)...")
        fetch.Client().login(username, password)  # SystemExit with eClass's answer if it fails
        say("  вход в eClass работает")

        passphrase, generated = ask_passphrase()
        key = github.call("GET", f"/repos/{owner}/{name}/actions/secrets/public-key").json()
        say()
        report["secrets"] = []
        for secret_name, value in (("ECLASS_USERNAME", username), ("ECLASS_PASSWORD", password),
                                   ("DATA_PASSPHRASE", passphrase)):
            github.call("PUT", f"/repos/{owner}/{name}/actions/secrets/{secret_name}", expect=(201, 204),
                        json={"encrypted_value": seal(key["key"], value), "key_id": key["key_id"]})
            report["secrets"].append(secret_name)
            say(f"  секрет {secret_name} сохранён")
        report["passphrase"] = "generated" if generated else "typed"

        if generated:
            say()
            say("Пароль для сайта. Сохрани его (заметки, менеджер паролей): больше он нигде не покажется.")
            say(f"    {passphrase}")
            say()
            input("Нажми Enter, когда сохранишь, и экран очистится...")
            os.system("cls" if os.name == "nt" else "clear")
        report["ok"] = True
        say("Готово: репозиторий, Pages и секреты на месте.")
        finish("done")
    except SystemExit as error:
        finish(str(error))
        raise
    except KeyboardInterrupt:
        finish("interrupted")
        raise SystemExit("прервано")


if __name__ == "__main__":
    main()
