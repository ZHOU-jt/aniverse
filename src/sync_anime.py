import argparse
import json
from pathlib import Path

import html
import re
import time
from datetime import datetime, timedelta

import requests

from app import get_db, init_db, SITE_TIMEZONE


ANILIST_URL = "https://graphql.anilist.co"


def clean_description(text):
    if not text:
        return "暂无简介"

    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def current_season():
    month = datetime.now(SITE_TIMEZONE).month
    year = datetime.now(SITE_TIMEZONE).year

    if month <= 3:
        season = "WINTER"
    elif month <= 6:
        season = "SPRING"
    elif month <= 9:
        season = "SUMMER"
    else:
        season = "FALL"

    return season, year


def request_anilist(query, variables):
    for attempt in range(1, 4):
        try:
            response = requests.post(
                ANILIST_URL,
                json={"query": query, "variables": variables},
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()

            if "errors" in result:
                raise RuntimeError(result["errors"])

            return result["data"]

        except Exception as exc:
            print(
                f"[AniVerse] AniList 请求失败 "
                f"({attempt}/3): {exc}"
            )

            if attempt < 3:
                wait_time = attempt * 2
                print(f"[AniVerse] {wait_time} 秒后重试...")
                time.sleep(wait_time)
            else:
                raise


ANIME_QUERY = """
query (
    $page: Int,
    $perPage: Int,
    $season: MediaSeason,
    $seasonYear: Int
) {
    Page(page: $page, perPage: $perPage) {
        pageInfo {
            hasNextPage
        }

        media(
            type: ANIME,
            season: $season,
            seasonYear: $seasonYear,
            isAdult: false,
            sort: [POPULARITY_DESC, SCORE_DESC]
        ) {
            id
            idMal

            title {
                romaji
                english
                native
            }

            description

            coverImage {
                medium
                large
                extraLarge
            }

            startDate {
                year
                month
                day
            }

            averageScore
            season
            seasonYear
            genres
            siteUrl
            status
        }
    }
}
"""


UPCOMING_QUERY = """
query (
    $page: Int,
    $perPage: Int,
    $startDateGreater: FuzzyDateInt,
    $startDateLesser: FuzzyDateInt
) {
    Page(page: $page, perPage: $perPage) {
        pageInfo {
            hasNextPage
        }

        media(
            type: ANIME,
            status: NOT_YET_RELEASED,
            startDate_greater: $startDateGreater,
            startDate_lesser: $startDateLesser,
            isAdult: false,
            sort: [START_DATE, POPULARITY_DESC]
        ) {
            id
            idMal

            title {
                romaji
                english
                native
            }

            description

            coverImage {
                medium
                large
                extraLarge
            }

            startDate {
                year
                month
                day
            }

            averageScore
            season
            seasonYear
            genres
            siteUrl
            status
        }
    }
}
"""


def fetch_current_anime(total=100, max_pages=2):
    season, year = current_season()

    print(f"[AniVerse] 当前季度：{season} {year}")
    print("[AniVerse] 正在获取本季度全部番剧（含播出中/已完结/即将播出）...")

    all_anime = []
    seen_ids = set()

    for page in range(1, max_pages + 1):
        print(f"[AniVerse] 本季番剧第 {page}/{max_pages} 页...")

        result = request_anilist(
            ANIME_QUERY,
            {
                "page": page,
                "perPage": 50,
                "season": season,
                "seasonYear": year,
            },
        )

        page_data = result["Page"]
        media_list = page_data["media"]

        if not media_list:
            break

        for anime in media_list:
            anime_id = anime.get("id")
            if anime_id in seen_ids:
                continue

            seen_ids.add(anime_id)
            all_anime.append(anime)

            if len(all_anime) >= total:
                break

        if len(all_anime) >= total:
            break

        if not page_data["pageInfo"]["hasNextPage"]:
            break

        time.sleep(1)

    print(f"[AniVerse] 本季度共获取 {len(all_anime)} 部番剧")
    return all_anime


def fetch_upcoming_anime(total=60, max_pages=3):
    print("[AniVerse] 正在获取未来一年 Upcoming 番剧...")

    all_anime = []
    seen_ids = set()
    today = datetime.now(SITE_TIMEZONE)
    one_year_later = today + timedelta(days=365)

    start_date_greater = int(
        (today - timedelta(days=1)).strftime("%Y%m%d")
    )
    start_date_lesser = int(
        one_year_later.strftime("%Y%m%d")
    )

    for page in range(1, max_pages + 1):
        print(f"[AniVerse] Upcoming 第 {page}/{max_pages} 页...")

        result = request_anilist(
            UPCOMING_QUERY,
            {
                "page": page,
                "perPage": 50,
                "startDateGreater": start_date_greater,
                "startDateLesser": start_date_lesser,
            },
        )

        page_data = result["Page"]
        media_list = page_data["media"]

        if not media_list:
            break

        for anime in media_list:
            anime_id = anime.get("id")
            if anime_id in seen_ids:
                continue

            start_date = anime.get("startDate") or {}
            if not start_date.get("year") or not start_date.get("month"):
                continue

            seen_ids.add(anime_id)
            all_anime.append(anime)

            if len(all_anime) >= total:
                break

        if len(all_anime) >= total:
            break

        if not page_data["pageInfo"]["hasNextPage"]:
            break

        time.sleep(1)

    print(f"[AniVerse] 最终获取 {len(all_anime)} 部未来番剧")
    return all_anime


def map_status(anilist_status):
    mapping = {
        "RELEASING": "airing",
        "FINISHED": "finished",
        "NOT_YET_RELEASED": "upcoming",
        "HIATUS": "other",
        "CANCELLED": "other",
    }
    return mapping.get(anilist_status, "other")


def save_anime(anime_list, classic=False):
    names_path = Path(__file__).with_name("title_names.json")
    names = json.loads(names_path.read_text(encoding="utf-8")).get("names", {}) if names_path.exists() else {}
    db = get_db()
    saved = 0

    for anime in anime_list:
        # 沿用 MAL ID 作为唯一键，以兼容现有数据库和评论引用。
        mal_id = anime.get("idMal")
        if not mal_id:
            continue

        title_data = anime.get("title") or {}
        title = (
            title_data.get("english")
            or title_data.get("romaji")
            or title_data.get("native")
            or "Unknown"
        )
        title_japanese = title_data.get("native") or ""

        cover_data = anime.get("coverImage") or {}
        cover = (
            cover_data.get("extraLarge")
            or cover_data.get("large")
            or cover_data.get("medium")
            or ""
        )
        cover_small = (
            cover_data.get("large")
            or cover_data.get("extraLarge")
            or cover_data.get("medium")
            or cover
        )

        start_date = anime.get("startDate") or {}
        start_year = start_date.get("year")
        start_month = start_date.get("month")
        start_day = start_date.get("day")

        genres = ", ".join(anime.get("genres") or [])

        average_score = anime.get("averageScore")
        score = (
            round(average_score / 10, 1)
            if average_score is not None
            else None
        )

        description = clean_description(anime.get("description"))
        status = map_status(anime.get("status"))
        season = anime.get("season")

        db.execute(
            """
            INSERT INTO anime (
                mal_id,
                title,
                title_japanese,
                description,
                cover,
                cover_small,
                status,
                mal_score,
                year,
                season,
                genres,
                source_url,
                start_year,
                start_month,
                start_day,
                title_zh,
                is_classic,
                popularity
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mal_id)
            DO UPDATE SET
                title = excluded.title,
                title_japanese = excluded.title_japanese,
                description = excluded.description,
                cover = excluded.cover,
                cover_small = excluded.cover_small,
                status = excluded.status,
                mal_score = excluded.mal_score,
                year = excluded.year,
                season = excluded.season,
                genres = excluded.genres,
                source_url = excluded.source_url,
                start_year = excluded.start_year,
                start_month = excluded.start_month,
                start_day = excluded.start_day,
                title_zh = CASE WHEN anime.title_zh IS NOT NULL AND anime.title_zh != '' THEN anime.title_zh ELSE excluded.title_zh END,
                is_classic = MAX(anime.is_classic, excluded.is_classic),
                popularity = CASE WHEN excluded.popularity > 0 THEN excluded.popularity ELSE anime.popularity END
            """,
            (
                mal_id,
                title,
                title_japanese,
                description,
                cover,
                cover_small,
                status,
                score,
                anime.get("seasonYear"),
                season,
                genres,
                anime.get("siteUrl"),
                start_year,
                start_month,
                start_day,
                names.get(str(mal_id)),
                int(classic),
                anime.get("popularity") or 0,
            ),
        )

        saved += 1
        print(f"  ✓ [{status}] {title}")

    db.commit()
    db.close()

    print(f"[AniVerse] 已写入/更新 {saved} 部番剧")



CLASSICS_QUERY = """
query ($page: Int) {
    Page(page: $page, perPage: 50) {
        pageInfo { hasNextPage }
        media(type: ANIME, isAdult: false, status: FINISHED,
              startDate_lesser: 20260101, sort: POPULARITY_DESC) {
            id idMal title { romaji english native } description
            coverImage { medium large extraLarge }
            startDate { year month day }
            averageScore season seasonYear genres siteUrl status popularity
        }
    }
}
"""


def fetch_classic_anime(total=200):
    items, seen = [], set()
    for page in range(1, (total + 49) // 50 + 1):
        result = request_anilist(CLASSICS_QUERY, {"page": page})["Page"]
        for item in result["media"]:
            if item.get("idMal") and item["idMal"] not in seen:
                items.append(item)
                seen.add(item["idMal"])
        if len(items) >= total or not result["pageInfo"]["hasNextPage"]:
            break
        time.sleep(1)
    return items[:total]


def main():
    parser = argparse.ArgumentParser(description="Sync AniVerse metadata without replacing user reviews.")
    parser.add_argument("--classics", action="store_true", help="Fetch a separate popular back-catalogue.")
    parser.add_argument("--import-classics", type=Path, help="Import an already fetched AniList JSON file.")
    args = parser.parse_args()
    init_db()
    if args.classics or args.import_classics:
        items = json.loads(args.import_classics.read_text(encoding="utf-8")) if args.import_classics else fetch_classic_anime()
        save_anime(items, classic=True)
        return

    current = fetch_current_anime(total=100, max_pages=2)
    save_anime(current)

    time.sleep(1)

    upcoming = fetch_upcoming_anime(total=60, max_pages=3)
    save_anime(upcoming)

    print()
    print("[AniVerse] AniList 数据同步完成。")


if __name__ == "__main__":
    main()
