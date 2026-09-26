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

    pat = re.compile(
        r"/match/(?:j1|j2|j3)/\d{4}/\d{6}/"
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
            print(f"日程取得エラー: {schedule_url}")
            print(e)

    return all_links


def extract_fixture(soup):
    title = soup.title.get_text(" ", strip=True) if soup.title else ""

    m = re.search(
        r"公式】(.+?) vs (.+?)の試合",
        title,
    )

    if m:
        return (
            m.group(1).strip(),
            m.group(2).strip(),
        )

    return "", ""


def extract_lineup_players(soup):
    """
    Jリーグ公式のHTMLから
    スタメンとベンチの選手名を取得する。
    """

    # スターティングメンバー
    starting_section = soup.select_one(
        "section.p-game-details-lineup-tab__starting-members"
    )

    starting_names = []

    if starting_section:
        starting_names = [
            x.get_text(strip=True)
            for x in starting_section.select(
                ".m-lineup-list-item__name"
            )
        ]

    # 控えメンバー
    reserve_section = soup.select_one(
        "section.p-game-details-lineup-tab__reserve-members"
    )

    reserve_names = []

    if reserve_section:
        reserve_names = [
            x.get_text(strip=True)
            for x in reserve_section.select(
                ".m-lineup-list-item__name"
            )
        ]

    return starting_names, reserve_names


def page_info(url):
    html = get(url)
    soup = BeautifulSoup(html, "html.parser")

    home, away = extract_fixture(soup)

    starting_players, reserve_players = extract_lineup_players(
        soup
    )

    # スタメンもベンチもまだ取得できない場合は
    # スタメン発表前と判断する
    if not starting_players and not reserve_players:
        return {
            "url": url,
            "published": False,
            "home": home,
            "away": away,
            "starting_players": [],
            "reserve_players": [],
        }

    return {
        "url": url,
        "published": True,
        "home": home,
        "away": away,
        "starting_players": starting_players,
        "reserve_players": reserve_players,
    }


def normalize_name(name):
    return re.sub(r"\s+", "", name)


def player_status(info, player_name):
    """
    対象選手が
    先発 / ベンチ / メンバー外
    のどれなのか判定する。
    """

    if not info["published"]:
        return "未発表"

    target = normalize_name(player_name)

    # 先発
    for name in info["starting_players"]:
        if normalize_name(name) == target:
            return "先発"

    # ベンチ
    for name in info["reserve_players"]:
        if normalize_name(name) == target:
            return "ベンチ"

    # スタメンにもベンチにもいない
    return "メンバー外"


def discord_send(message):
    webhook = os.getenv(
        "DISCORD_WEBHOOK_URL",
        "",
    ).strip()

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

    print(
        f"試合ページを {len(links)} 件取得しました。"
    )

    results = []

    for url in links:
        try:
            info = page_info(url)

        except Exception as e:
            print(
                f"試合ページ取得エラー: {url}"
            )
            print(e)
            continue

        if not info["published"]:
            continue

        print(
            f"スタメン取得: "
            f"{info['home']} vs {info['away']} "
            f"(先発 {len(info['starting_players'])}人 / "
            f"ベンチ {len(info['reserve_players'])}人)"
        )

        for p in players:
            team = p.get("team", "")

            # チームが指定されている場合、
            # そのチームの試合だけ対象にする
            if team:
                if (
                    team != info["home"]
                    and team != info["away"]
                ):
                    continue

            status = player_status(
                info,
                p["name"],
            )

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
        print(
            "対象チームのスタメン発表済み試合はありません。"
        )
        return

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

        if home and away:
            fixture = f"{home} vs {away}"
        else:
            fixture = url

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
        print(
            "新しいスタメン情報はありません。"
        )


if __name__ == "__main__":
    main()
