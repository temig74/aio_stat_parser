import asyncio
import logging
import re
from datetime import datetime, timedelta
from itertools import groupby
from urllib.parse import parse_qs, urlparse

import aiohttp
from bs4 import BeautifulSoup
from emoji import replace_emoji

from config_reader import config

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30)


def parse_game_url(url: str) -> tuple[str, str] | None:
    """Извлекает (домен, gid) из ссылки на игру/статистику. None, если ссылка некорректна."""
    url_obj = urlparse(url)
    domain = url_obj.hostname
    if not domain:
        return None
    gid_values = parse_qs(url_obj.query).get('gid')
    if not gid_values or not gid_values[0]:
        return None
    return domain, gid_values[0]


def format_timedelta(tdelta: timedelta) -> str:
    seconds = tdelta.total_seconds()
    sign = "-" if seconds < 0 else " "
    seconds = abs(seconds)
    milliseconds = int(round((seconds - int(seconds)) * 1000))
    seconds = int(seconds)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{sign}{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}:{milliseconds:03d}"


# ---------------------------------------------------------------------------
# Получение исходных данных статистики: JSON API либо HTML-страницы
# ---------------------------------------------------------------------------

async def get_json(my_url: str) -> dict | None:
    """Загружает статистику через JSON API игры (может быть отключён на сайте)."""
    start_time = datetime.now()
    game_ref = parse_game_url(my_url)
    if not game_ref:
        raise ValueError('Некорректная ссылка на игру')
    domain, gid = game_ref

    json_data = None
    MAX_PAGES = 10
    headers = {"User-Agent": config.user_agent}
    async with aiohttp.ClientSession(headers=headers, timeout=HTTP_TIMEOUT) as session:
        for i in range(1, MAX_PAGES):
            api_url = f'https://{domain}/gamestatistics/full/{gid}?json=1&page={i}'
            async with session.get(api_url) as response:
                if response.status != 200:
                    logging.error(f"Ошибка при загрузке страницы {api_url}: статус {response.status}")
                    break
                response_data = await response.json()

                if i == 1:
                    json_data = response_data
                if not response_data.get('StatItems') or not response_data['StatItems'][0]:
                    break

                if i > 1:
                    for j in range(len(response_data['StatItems'])):
                        json_data['StatItems'][j].extend(response_data['StatItems'][j])

    logging.info(f'json загружен за {datetime.now() - start_time}')
    return json_data


async def fetch_stat_pages(session: aiohttp.ClientSession, domain: str, gid: str) -> list[str]:
    """Загружает HTML-страницы GameStat.aspx (обычно 1, максимум 2 страницы)."""
    base_url = f'https://{domain}/GameStat.aspx?gid={gid}&sortfield=SpentSeconds&lang=ru'
    pages = []
    async with session.get(base_url) as rs:
        rs.raise_for_status()
        html_source = await rs.text()
        pages.append(html_source)
    if '&page=2' in html_source:
        async with session.get(f'{base_url}&page=2') as rs:
            rs.raise_for_status()
            pages.append(await rs.text())
    return pages


async def fetch_stat_pages_standalone(domain: str, gid: str) -> list[str]:
    """То же самое, но сама открывает сессию (для мест без уже открытой сессии)."""
    async with aiohttp.ClientSession(headers={"User-Agent": config.user_agent}, timeout=HTTP_TIMEOUT) as session:
        return await fetch_stat_pages(session, domain, gid)


# ---------------------------------------------------------------------------
# Список уровней: парсинг ввода пользователя и фильтрация по названиям
# ---------------------------------------------------------------------------

def parse_level_nums(levels_text: str) -> tuple[list[int], list[int]]:
    """
    Парсит строку с уровнями (например, "1 3-5 -2 !6") в отсортированные списки
    (включённые уровни, снятые уровни). Токены обрабатываются по порядку слева направо,
    поэтому исключение уровня ("-N") должно идти после его добавления (например, через диапазон).
    """
    parsed_levels: list[int] = []
    dismissed_levels: list[int] = []
    for elem in levels_text.split():
        if elem.startswith('!'):
            dismissed_levels.append(int(elem[1:]))
        elif elem.startswith('-'):
            level_to_remove = int(elem[1:])
            if level_to_remove not in parsed_levels:
                raise ValueError(
                    f'нельзя исключить уровень {level_to_remove} — он не был указан ранее '
                    f'(проверьте порядок аргументов, например "1-103 -{level_to_remove}")'
                )
            parsed_levels.remove(level_to_remove)
        elif '-' in elem:
            start_str, end_str = elem.split('-')
            parsed_levels.extend(range(int(start_str), int(end_str) + 1))
        else:
            parsed_levels.append(int(elem))
    return sorted(parsed_levels), sorted(dismissed_levels)


def parse_levels_row_from_html(html_source: str) -> list[tuple[int, str]]:
    """Извлекает (номер_уровня, название_уровня) из шапки таблицы статистики GameStat.aspx."""
    soup = BeautifulSoup(html_source, 'lxml')
    levels_row = soup.find('tr', class_='levelsRow')
    if not levels_row:
        return []
    parsed = []
    for td in levels_row.find_all('td')[1:-3]:
        for span in td.find_all('span', class_='dismissed'):
            span.decompose()
        text = td.get_text(strip=True).lower()
        if ':' not in text:
            continue
        level_number_str, level_name = text.split(':', maxsplit=1)
        parsed.append((int(level_number_str), level_name.strip()))
    return parsed


async def get_level_names_json(json_data: dict, my_url: str) -> tuple[list[tuple[int, str]], int]:
    """
    Возвращает [(номер, название)] и общее число уровней по данным JSON.
    Если названия скрыты (HideLevelsNames), дополнительно подтягивает их из HTML-страницы статистики.
    """
    level_count = json_data['Game']['LevelNumber']

    if not json_data['Game']['HideLevelsNames']:
        parsed = [
            (int(level['LevelNumber']), level['LevelName'].lower())
            for level in json_data['Levels']
        ]
        return parsed, level_count

    game_ref = parse_game_url(my_url)
    if not game_ref:
        return [], level_count
    domain, gid = game_ref
    stat_url = f'https://{domain}/GameStat.aspx?gid={gid}&sortfield=SpentSeconds&lang=ru'
    async with aiohttp.ClientSession(headers={"User-Agent": config.user_agent}, timeout=HTTP_TIMEOUT) as session:
        async with session.get(stat_url) as rs:
            rs.raise_for_status()
            html_source = await rs.text()
    return parse_levels_row_from_html(html_source), level_count


def filter_levels_by_pattern(
    parsed_level_data: list[tuple[int, str]],
    level_count: int,
    levels_text: list[str],
) -> list[int]:
    """
    Отбирает номера уровней по текстовым паттернам их названий (команда /textstat).
    Если первый паттерн начинается с '-', все паттерны трактуются как исключающие
    (из полного диапазона 1..level_count убираются уровни с совпавшим названием).
    Иначе паттерны трактуются как включающие (берутся только совпавшие уровни).
    """
    if not levels_text:
        return []

    exclude_mode = levels_text[0].startswith('-')

    if exclude_mode:
        excluded_patterns = {elem[1:].lower() for elem in levels_text}
        excluded_level_nums = {
            level_num
            for level_num, level_name in parsed_level_data
            if any(pattern in level_name for pattern in excluded_patterns)
        }
        return [n for n in range(1, level_count + 1) if n not in excluded_level_nums]

    included_patterns = {elem.lower() for elem in levels_text}
    return [
        level_num
        for level_num, level_name in parsed_level_data
        if any(pattern in level_name for pattern in included_patterns)
    ]


# ---------------------------------------------------------------------------
# Подсчёт результатов
# ---------------------------------------------------------------------------

def compute_level_durations(
    stat_list: list[dict],
    date_start: datetime,
    dismissed_levels_list: tuple = (),
) -> list[dict]:
    """
    Для каждой "сырой" записи о прохождении считает длительность конкретного уровня
    (разницу со временем взятия предыдущего по порядку выдачи уровня той же командой)
    и итоговый bonus_sec_total с поправкой на снятые уровни.
    Используется и для сводных итогов по игре, и для CSV-экспорта по уровням.
    """
    team_level_order_lookup: dict[str, dict[int, datetime]] = {}
    for entry in stat_list:
        team_level_order_lookup.setdefault(entry['team'], {})[entry['level_order']] = entry['up_datetime']

    calculated_level_durations = []
    for entry in stat_list:
        team = entry['team']
        level_num = entry['level_num']
        up_datetime = entry['up_datetime']
        bonus_sec_total = entry['bonus_sec_total']
        level_order = entry['level_order']

        if level_order == 1:
            level_duration = up_datetime - date_start
        else:
            prev_up_datetime = team_level_order_lookup.get(team, {}).get(level_order - 1)
            level_duration = up_datetime - prev_up_datetime if prev_up_datetime else None

        if level_duration is None:
            continue

        # снятый уровень: "начисляем" бонус в размере его длительности (сохраняя реальные бонусы/штрафы)
        if level_num in dismissed_levels_list:
            bonus_sec_total -= level_duration.total_seconds()

        calculated_level_durations.append({
            'team': team,
            'level_num': level_num,
            'duration': level_duration,
            'bonus_sec_total': bonus_sec_total,
        })

    return calculated_level_durations


def get_final_results_from_stat(
    stat_list: list[dict],
    levels_list: list[int],
    date_start: datetime,
    dismissed_levels_list: tuple = (),
) -> list[tuple[str, timedelta, timedelta, int]]:
    """Сводит "сырые" записи о прохождении уровней в итог по командам (сумма по выбранным уровням)."""
    result_dict: dict[str, list] = {}
    for entry in compute_level_durations(stat_list, date_start, dismissed_levels_list):
        if entry['level_num'] not in levels_list:
            continue

        team = entry['team']
        if team not in result_dict:
            result_dict[team] = [timedelta(0), 0, timedelta(0)]
        result_dict[team][0] += entry['duration']
        result_dict[team][1] += 1
        result_dict[team][2] += entry['duration'] + timedelta(seconds=entry['bonus_sec_total'])

    return [(team, data[0], data[2], data[1]) for team, data in result_dict.items()]


def _format_results(header: list[str], final_results: list[tuple]) -> tuple[list[str], list[str], list[str]]:
    maxlen = max((len(a[0]) for a in final_results), default=0)

    def format_line(i: int, row: tuple, with_bonus: bool) -> str:
        team = row[0].ljust(maxlen)
        time_str = format_timedelta(row[2] if with_bonus else row[1])
        return f'{i + 1} {team} {time_str} {row[3]}'

    sorted_bonus_list = sorted(final_results, key=lambda x: (-x[3], x[2]))
    bonus_output = ['С бонусами:'] + [format_line(i, a, True) for i, a in enumerate(sorted_bonus_list)]

    sorted_nobonus_list = sorted(final_results, key=lambda x: (-x[3], x[1]))
    nobonus_output = ['Без бонусов:'] + [format_line(i, a, False) for i, a in enumerate(sorted_nobonus_list)]

    return header, bonus_output, nobonus_output


def parse_en_stat2(
    json: dict,
    levels_list: list[int],
    dismissed_levels_list: tuple = (),
) -> tuple[list[str], list[str], list[str]] | list[str]:
    start_time = datetime.now()
    if json['Game']['LevelsSequenceId'] == 3:
        return ['Ошибка: не применимо в штурмовой последовательности']

    if not levels_list:
        levels_list = list(range(1, json['Game']['LevelNumber'] + 1))

    def datetime_from_seconds(milliseconds_from_zero_year: float) -> datetime:
        return datetime(1, 1, 1) + timedelta(milliseconds=round(milliseconds_from_zero_year))

    def get_stat_item(x: dict) -> dict:
        return {
            'team': replace_emoji(x['TeamName'] or x['UserName'], ''),
            'level_num': x['LevelNum'],
            'up_datetime': datetime_from_seconds(x['ActionTime']['Value']),
            'bonus_sec_total': -x['Corrections']['CorrectionValue']['TotalSeconds'] if x['Corrections'] else 0,
            'level_order': x['LevelOrder'],
        }

    date_start = datetime_from_seconds(json['Game']['StartDateTime']['Value'])
    stat_list = [get_stat_item(x) for level in json['StatItems'] for x in level]
    dismissed_levels = sorted(x['LevelNumber'] for x in json['Levels'] if x['Dismissed'])

    final_results = get_final_results_from_stat(stat_list, levels_list, date_start, dismissed_levels_list)

    header = [
        f"Статистика по уровням: {'все' if not levels_list else sorted(levels_list)}",
        f"Снятые уровни: {'нету' if not dismissed_levels else dismissed_levels}",
    ]

    logging.info(f'parse_en_stat2 отработала за {datetime.now() - start_time}')
    return _format_results(header, final_results)


TIME_UNITS = {'М': 2592000, 'дн': 86400, 'ч': 3600, 'м': 60, 'с': 1}
MAIN_PATTERN = re.compile(r'<br/>(бонус|штраф)([^<]*)<')
COMPONENT_PATTERN = re.compile(r'(\d+)(М|дн|ч|м|с)')
UP_DATETIME_PATTERN = re.compile(r'(\d{2}\.\d{2}\.\d{4}).*?(\d{2}:\d{2}:\d{2}\.\d{3})')


def parse_bonus_time(bonus_text) -> float:
    text_to_parse = str(bonus_text)
    match = MAIN_PATTERN.search(text_to_parse)
    if not match:
        return 0

    bonus_penalty_type = match.group(1)
    duration_str = match.group(2).strip().replace(' ', '')
    sign = -1 if bonus_penalty_type == 'бонус' else 1

    total_sec = sum(
        int(value_str) * TIME_UNITS[unit]
        for value_str, unit in COMPONENT_PATTERN.findall(duration_str)
    )
    return total_sec * sign


def extract_html_stat_list(pages: list[str]) -> tuple[list[dict], datetime]:
    """Парсит страницы GameStat.aspx в список "сырых" записей о прохождении и дату старта игры."""
    date_start_match = re.search(r"(?<=sliderStartTime = ').*(?=\';)", str(pages[0]))
    if not date_start_match:
        raise ValueError('Не удалось найти дату начала игры на странице статистики')
    date_start = datetime.strptime(date_start_match[0], '%d.%m.%Y %H:%M:%S.%f')

    stat_list = []
    for html_page in pages:
        soup = BeautifulSoup(html_page, 'lxml')
        parse_data = soup.find(
            lambda tag: tag.name == 'table'
            and tag.get('id') in ['GameStatObject_DataTable', 'GameStatObject2_DataTable',
                                   'GameStatObject3_DataTable', 'GameStatObject4_DataTable']
        )
        if not parse_data:
            continue
        for tr_set in parse_data.find_all('tr')[1:-1]:
            cells = tr_set.find_all('div', class_='dataCell')
            for level_num, elem in enumerate(cells, 1):
                team_tag = elem.find('a')
                if not team_tag:
                    continue
                team = team_tag.text
                up_datetime_match = UP_DATETIME_PATTERN.search(str(elem))
                if not up_datetime_match:
                    continue
                up_datetime = datetime.strptime(
                    f"{up_datetime_match.group(1)} {up_datetime_match.group(2)}", '%d.%m.%Y %H:%M:%S.%f'
                )
                level_order_tag = elem.find('div', class_='n')
                level_order = int(level_order_tag.text) if level_order_tag else level_num

                stat_list.append({
                    'team': team,
                    'level_num': level_num,
                    'up_datetime': up_datetime,
                    'bonus_sec_total': parse_bonus_time(elem),
                    'level_order': level_order,
                })

    return stat_list, date_start


def parse_html_stat(
    pages: list[str],
    levels_list: list[int],
    dismissed_levels_list: tuple = (),
) -> tuple[list[str], list[str]]:
    stat_list, date_start = extract_html_stat_list(pages)
    final_results = get_final_results_from_stat(stat_list, levels_list, date_start, dismissed_levels_list)
    _, bonus_output, nobonus_output = _format_results([], final_results)
    return bonus_output, nobonus_output


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def _seconds_to_hms(total_seconds: float) -> str:
    sign = "-" if total_seconds < 0 else ''
    total_seconds = abs(total_seconds)
    minutes, seconds = divmod(int(total_seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"


def _build_csv(level_durations: list[dict], level_number: int, with_bonuses: bool) -> str:
    """
    Собирает CSV-матрицу team x level из списка записей {'team', 'level_num', 'duration', 'bonus_sec_total'}.
    'duration' — timedelta длительности уровня, 'bonus_sec_total' — знаковая поправка в секундах
    (отрицательная для бонуса, положительная для штрафа), так же, как используется в сводных итогах.
    """
    stat_d: dict[str, dict[int, str]] = {}
    for entry in level_durations:
        total_seconds = entry['duration'].total_seconds()
        if with_bonuses:
            total_seconds += entry['bonus_sec_total']
        stat_d.setdefault(entry['team'], {})[entry['level_num']] = _seconds_to_hms(total_seconds)

    header = ';'.join(str(i) for i in range(level_number + 1)) + ';'
    lines = [header]
    for team, values in stat_d.items():
        # по каждому уровню явно берём значение из словаря (или пустую строку),
        # чтобы непройденные уровни не сдвигали остальные колонки относительно шапки
        times = [values.get(lvl, '') for lvl in range(1, level_number + 1)]
        lines.append(team + ';' + ';'.join(times) + ';')

    return '\n'.join(lines)


def generate_csv(json_data: dict, with_bonuses: bool) -> str:
    """CSV из данных JSON API статистики."""
    level_number = json_data['Game']['LevelNumber']
    level_durations = []
    for level in json_data['StatItems']:
        for statitem in level:
            bonus_sec_total = (
                -statitem['Corrections']['CorrectionValue']['TotalSeconds'] if statitem.get('Corrections') else 0
            )
            level_durations.append({
                'team': statitem['TeamName'] or statitem['UserName'],
                'level_num': statitem['LevelNum'],
                'duration': timedelta(seconds=statitem['SpentLevelTime']['TotalSeconds']),
                'bonus_sec_total': bonus_sec_total,
            })

    return _build_csv(level_durations, level_number, with_bonuses)


def generate_csv_from_html(pages: list[str], with_bonuses: bool) -> str:
    """CSV из HTML-страниц GameStat.aspx (используется, когда JSON API недоступен)."""
    stat_list, date_start = extract_html_stat_list(pages)
    level_durations = compute_level_durations(stat_list, date_start)
    level_number = max((entry['level_num'] for entry in level_durations), default=0)
    return _build_csv(level_durations, level_number, with_bonuses)


# ---------------------------------------------------------------------------
# Оценки (/rates)
# ---------------------------------------------------------------------------

async def get_rates(my_url: str) -> list[str]:
    game_ref = parse_game_url(my_url)
    if not game_ref:
        raise ValueError('Некорректная ссылка на игру')
    _, gid = game_ref

    async with aiohttp.ClientSession(headers={'User-Agent': config.user_agent}, timeout=HTTP_TIMEOUT) as session:
        async with session.get(f'https://world.en.cx/ALoader/GameLoader.aspx?gid={gid}&item=3') as rs:
            rs.raise_for_status()
            all_teams = await rs.text()

        soup = BeautifulSoup(all_teams, 'lxml')
        rates = []
        for team in soup.find_all(id='lnkPlayerInfo'):
            team_name = team.get_text(strip=True)
            href = urlparse(team['href'])
            tid_values = parse_qs(href.query).get('tid')
            if not tid_values:
                continue
            tid = tid_values[0]
            rates_url = f'https://world.en.cx/ALoader/FormulaDetails.aspx?gid={gid}&tid={tid}&mode=0'

            async with session.get(rates_url) as rs:
                rs.raise_for_status()
                team_players = await rs.text()

            rates_doc = BeautifulSoup(team_players, 'lxml')
            for p in rates_doc.find_all(class_='toWinnerItem'):
                player_node = p.find('a', href=re.compile(r'uid=\d+'))
                rate_tag = p.find('td', class_='pink')
                weight_tag = p.find('td', class_='yellow_lihgt')
                if not (player_node and rate_tag and weight_tag):
                    continue
                rates.append((
                    int(rate_tag.get_text(strip=True)),
                    team_name,
                    player_node.get_text(strip=True),
                    float(weight_tag.get_text(strip=True)),
                ))

            await asyncio.sleep(1.5)  # не долбим сайт запросами слишком часто

        if not rates:
            return []

        result = []
        for _, group in groupby(rates, key=lambda x: x[1]):  # группировка по команде
            for entry in group:
                result.append(f'{str(entry[0]).ljust(2)} {entry[1]} {entry[2]}')
            result.append('')  # разделитель между командами

        total_weight = sum(x[3] for x in rates)
        result.append(f'ИТОГ: {round(sum(x[0] for x in rates) / len(rates), 2)}')
        if total_weight:
            result.append(f'ИТОГ(+вес): {round(sum(x[0] * x[3] for x in rates) / total_weight, 2)}')

        return result