import json
import os
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.jleague.jp"

SCHEDULES = [
BASE + "/j1/match/search-list/",
BASE + "/j2/match/search-list/",
BASE + "/j3/match/search-list/",
]

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

# Jリーグ公式の試合詳細ページ
pat = re.compile(
    r"/match/(?:j1|j2|j3|j2j3|leaguecup|levan|emperor|acl|acl-elite|acl-two)/\d{4}/\d{6}/"
)

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

all_links = []
seen = set()

for schedule_url in SCHEDULES:
    try:
        r = session.get(
            schedule_url,
            params=params,
            timeout=20,
        )
        r.raise_for_status()

        links = match_links(r.text)

        for url in links:
            if url not in seen:
                seen.add(url)
                all_links.append(url)

    except Exception as e:
        print(f"日程取得エラー: {schedule_url} / {e}")

return all_links

def extract_fixture(soup):
title = soup.title.get_text(" ", strip=True) if soup.title else ""

# 例：
# 【公式】秋田 vs 新潟の試合結果・データ...
m = re.search(r"公式】(.+?) vs (.+?)の試合", title)

if m:
    return m.group(1).strip(), m.group(2).strip()

return "", ""

def extract_starting_players(lines):
"""
Jリーグ公式のラインナップ部分から先発選手だけを取得する。

現在の公式ページでは、

    GK 1
    山田 元気
    GK 46
    バウマン
    DF 3
    飯泉 涼矢
    ...

のように、
    「ポジション＋背番号」
    ↓
    「選手名」

の順で並んでいる。

「見どころ」が出たところでラインナップ部分を終了する。
"""

players = []

# ラインナップ開始位置を探す。
# これは「発表済みか」の判定には使わず、
# 先発選手が並ぶ場所を特定するためだけに使う。
start = None

for i, line in enumerate(lines):
    if line == "スターティングメンバー発表":
        start = i + 1
        break

if start is None:
    return players

# 「見どころ」までをラインナップ領域とする。
end = len(lines)

for i in range(start, len(lines)):
    if lines[i] == "見どころ":
        end = i
        break

block = lines[start:end]

# 「GK 1」「DF 3」「MF 10」「FW 9」などを検出
position_pattern = re.compile(
    r"^(GK|DF|MF|FW)\s+\d+$"
)

for i in range(len(block) - 1):
    position_line = block[i]
    name_line = block[i + 1]

    if position_pattern.match(position_line):
        players.append(name_line)

return players

def page_info(url):
html = get(url)
soup = BeautifulSoup(html, "html.parser")

text = soup.get_text("\n", strip=True)
lines = [x.strip() for x in text.splitlines() if x.strip()]

home, away = extract_fixture(soup)

starting_players = extract_starting_players(lines)

if not starting_players:
    return {
        "url": url,
        "published": False,
        "home": home,
        "away": away,
        "starting_players": [],
    }

return {
    "url": url,
    "published": True,
    "home": home,
    "away": away,
    "starting_players": starting_players,
}

def player_status(info, player_name):
if not info["published"]:
return "未発表"

# 選手名を先発選手リストから完全一致で確認
if player_name in info["starting_players"]:
    return "先発"

return "先発ではない"

def discord_send(message):
webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

if not webhook:
    print(message)
    return

r = session.post(
    webhook,
    json={"content": message},
    timeout=20,
)

r.raise_for_status()

def main():
with open("config.json", encoding="utf-8") as f:
cfg = json.load(f)

players = cfg["players"]

links = upcoming_schedule(
    int(cfg.get("lookahead_days", 14))
)

print(f"試合ページを {len(links)} 件取得しました。")

results = []

for url in links:
    try:
        info = page_info(url)

    except Exception as e:
        print(f"試合ページ取得エラー: {url} / {e}")
        continue

    if not info["published"]:
        continue

    print(
        f"スタメン取得: "
        f"{info['home']} vs {info['away']} "
        f"({len(info['starting_players'])}人)"
    )

    for p in players:
        status = player_status(info, p["name"])

        if status != "未発表":
            results.append(
                (
                    p["name"],
                    status,
                    info["home"],
                    info["away"],
                    url,
                )
            )

if not results:
    print("スタメン発表済みの対象選手はありません。")
    return

# Avoid duplicate notifications during repeated GitHub Actions runs.
state_path = Path(".state.json")

old = set()

if state_path.exists():
    try:
        old = set(
            json.loads(
                state_path.read_text(
                    encoding="utf-8"
                )
            )
        )
    except Exception:
        old = set()

new_messages = []
new_state = set(old)

for name, status, home, away, url in results:
    key = f"{url}|{name}|{status}"

    if key in old:
        continue

    new_state.add(key)

    fixture = (
        f"{home} vs {away}"
        if home and away
        else url
    )

    new_messages.append(
        f"⚽ {name}：**{status}**\n"
        f"{fixture}\n"
        f"{url}"
    )

state_path.write_text(
    json.dumps(
        sorted(new_state),
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)

if new_messages:
    discord_send(
        "\n\n".join(new_messages)
    )
else:
    print("新しいスタメン情報はありません。")

if name == "main":
main()
