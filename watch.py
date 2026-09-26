import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.jleague.jp"
SCHEDULE = BASE + "/j1/match/search-list/"
UA = "Mozilla/5.0 (compatible; JLeagueLoanWatch/1.0)"
JST = timezone(timedelta(hours=9))

session = requests.Session()
session.headers.update({"User-Agent": UA})


def get(url):
    r = session.get(url, timeout=20)
    r.raise_for_status()
    return r.text


def match_links(html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    seen = set()
    # Match-detail links are exposed on the official schedule page.
    pat = re.compile(r"/match/(?:j1|j2|j3|levan|emperor|acl)/\d{4}/\d{6}/")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if pat.search(href):
            url = urljoin(BASE, href)
            url = url.rstrip("/") + "/"
            if url not in seen:
                seen.add(url)
                out.append(url)
    return out


def upcoming_schedule(days):
    now = datetime.now(JST)
    end = now + timedelta(days=days)
    params = {
        "period": "custom",
        "startdate": now.strftime("%Y-%m-%d"),
        "enddate": end.strftime("%Y-%m-%d"),
    }
    r = session.get(SCHEDULE, params=params, timeout=20)
    r.raise_for_status()
    return match_links(r.text)


def page_info(url):
    html = get(url)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [x.strip() for x in text.splitlines() if x.strip()]

    # A lineup page exposes this exact heading once lineups are published.
    marker = "スターティングメンバー発表"
    if marker not in lines:
        return {"url": url, "published": False}

    i = lines.index(marker)
    tail = lines[i + 1:]

    # Stop before preview/other page sections.
    stop_words = {"見どころ", "注目選手", "スタジアム", "過去対戦成績"}
    block = []
    for x in tail:
        if x in stop_words:
            break
        block.append(x)

    # Header text immediately before the lineup contains the fixture.
    # Find a line with "vs" in the page title, then parse the title.
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    m = re.search(r"公式】(.+?) vs (.+?)の試合", title)
    home = away = ""
    if m:
        home, away = m.group(1).strip(), m.group(2).strip()

    return {
        "url": url,
        "published": True,
        "home": home,
        "away": away,
        "lineup_block": block,
        "text": text,
    }


def player_status(info, player_name):
    if not info["published"]:
        return "未発表"
    block = "\n".join(info["lineup_block"])
    return "先発" if player_name in block else "先発ではない"


def discord_send(message):
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        print(message)
        return
    r = session.post(webhook, json={"content": message}, timeout=20)
    r.raise_for_status()


def main():
    with open("config.json", encoding="utf-8") as f:
        cfg = json.load(f)

    players = cfg["players"]
    links = upcoming_schedule(int(cfg.get("lookahead_days", 14)))

    results = []
    for url in links:
        info = page_info(url)
        if not info["published"]:
            continue

        for p in players:
            # We intentionally match the player name only. The team field is
            # metadata for your own config and can later be used for stricter
            # team/match filtering.
            status = player_status(info, p["name"])
            if status != "未発表":
                results.append((p["name"], status, info["home"], info["away"], url))

    if not results:
        print("スタメン発表済みの対象選手はありません。")
        return

    # Avoid duplicate notifications during repeated GitHub Actions runs.
    state_path = Path(".state.json")
    old = set()
    if state_path.exists():
        try:
            old = set(json.loads(state_path.read_text(encoding="utf-8")))
        except Exception:
            old = set()

    new_messages = []
    new_state = set(old)

    for name, status, home, away, url in results:
        key = f"{url}|{name}|{status}"
        if key in old:
            continue
        new_state.add(key)
        fixture = f"{home} vs {away}" if home and away else url
        new_messages.append(f"⚽ {name}：**{status}**\n{fixture}\n{url}")

    state_path.write_text(
        json.dumps(sorted(new_state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if new_messages:
        discord_send("\n\n".join(new_messages))
    else:
        print("新しいスタメン情報はありません。")


if __name__ == "__main__":
    main()
