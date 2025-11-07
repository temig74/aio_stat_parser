import re
from datetime import datetime, timedelta
from itertools import groupby
from time import sleep
from urllib.parse import parse_qs, urlparse
import requests
from bs4 import BeautifulSoup
from emoji import replace_emoji
from config_reader import config


def format_timedelta(tdelta: timedelta):
    seconds = tdelta.total_seconds()
    sign = "-" if seconds < 0 else " "
    seconds = abs(seconds)
    milliseconds = int(round((seconds - int(seconds))*1000))
    seconds = int(seconds)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{sign}{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}:{milliseconds:03d}"


def get_json(my_url):
    url = urlparse(my_url)
    gid = parse_qs(url.query)['gid'][0]

    json = None
    MAX_PAGES = 10
    with requests.Session() as session:
        for i in range(1, MAX_PAGES):
            api_url = f'https://{url.hostname}/gamestatistics/full/{gid}?json=1&page={i}'
            response = session.get(api_url, headers={"User-Agent": config.user_agent}).json()
            if i == 1:
                json = response

            # do not proceed with next page if no stat items for this page
            if not response['StatItems'][0]:
                break

            # merge StatItems into first page
            if i > 1:
                for i in range(0, len(response['StatItems'])):
                    json['StatItems'][i].extend(response['StatItems'][i])
    return json


def parse_en_stat2(json, levels_list):
    start_time = datetime.now()
    if json['Game']['LevelsSequenceId'] == 3:
        return ['Ошибка: не применимо в штурмовой последовательности'], []

    if not levels_list:
        levels_list = [i for i in range(1, json['Game']['LevelNumber']+1)]

    def datetime_from_seconds(milliseconds_from_zero_year):
        # в движке все расчеты идут по секундам (по словам музыканта)
        # но все равно возьмем время с миллисекундами для более точных результатов
        return datetime(1, 1, 1) + timedelta(milliseconds=round(milliseconds_from_zero_year))

    def get_stat_item(x):
        finished_at = datetime_from_seconds(x['ActionTime']['Value'])
        bonus_time = -x['Corrections']['CorrectionValue']['TotalSeconds'] if x['Corrections'] else 0
        # return replace_emoji(x['TeamName'] or x['UserName'], replace=''), x['LevelNum'], finished_at, bonus_time, x['LevelOrder']
        return {'team': replace_emoji(x['TeamName'] or x['UserName'], ''), 'level_num': x['LevelNum'], 'up_datetime': finished_at, 'bonus_sec_total': bonus_time, 'level_order': x['LevelOrder']}

    date_start = datetime_from_seconds(json['Game']['StartDateTime']['Value'])
    stat_list = []  # (участник0, номер_ур1, время_завершения2, бонус3, порядок выдачи4)

    for level in json['StatItems']:
        stat_list.extend(get_stat_item(x) for x in level)
    dismissed_levels = set(x['LevelNumber'] for x in json['Levels'] if x['Dismissed'])

    '''####################
    new_stat_list = []  # Отсеянный список только с нужными номерами уровней: [участник0, номер_ур1, время_ур2, бонус3]
    for team1, level_num1, finished_at1, bonus_time1, level_order1 in stat_list:
        if (level_num1 in levels_list) or len(levels_list) == 0:  # если уровень в списке, или уровни не заданы
            if level_order1 == 1:  # если первый уровень, то нужно вычитать из времени начала игры
                new_stat_list.append([team1, level_num1, finished_at1 - date_start, bonus_time1])
            else:
                for team2, level_num2, finished_at2, bonus_time2, level_order2 in stat_list:
                    if team2 == team1 and level_order2 == level_order1 - 1:  # если одинаковая команда и порядок выдачи перед текущим, то считаем время уровня и обрываем цикл
                        new_stat_list.append([team1, level_num1, finished_at1 - finished_at2, bonus_time1])
                        break

    result_dict = {}  # {команда: [общее время, кол-во пройденных уровней, время с бонусами]}
    for team, level_num, level_time, bonus_time in new_stat_list:
        if team in result_dict:
            result_dict[team] = [result_dict[team][0] + level_time, result_dict[team][1] + 1, result_dict[team][2] + level_time + timedelta(seconds=bonus_time)]
        else:
            result_dict[team] = [level_time, 1, level_time + timedelta(seconds=bonus_time)]

    result_list = [[a, result_dict[a][0], result_dict[a][2], result_dict[a][1]] for a in result_dict]
    ##################'''
    result_list = get_final_results_from_stat(stat_list, levels_list, date_start)

    # посчитаем максимальную ширину столбцов "место" и "команда" для выравнивания
    team_width = max(len(a[0]) for a in result_list)
    pos_width = len(str(len(result_list)))

    def format_line(i, row, with_bonus):
        pos = str(i + 1).ljust(pos_width)
        team = row[0].ljust(team_width)
        time = format_timedelta(row[2] if with_bonus else row[1])
        return f'{pos} {team} {time} {row[3]}'
    header = [
        f'Статистика по уровням: {'все' if len(levels_list) == 0 else sorted(levels_list)}',
        f'Снятые уровни: {'нету' if len(dismissed_levels) == 0 else sorted(dismissed_levels)}'
    ]

    sorted_bonus_list = sorted(result_list, key=lambda x: (-x[3], x[2]))
    bonus_output_list = ['С бонусами:'] + [format_line(i, a, True) for i, a in enumerate(sorted_bonus_list)]

    sorted_nobonus_list = sorted(result_list, key=lambda x: (-x[3], x[1]))
    nobonus_output_list = ['Без бонусов:'] + [format_line(i, a, False) for i, a in enumerate(sorted_nobonus_list)]

    print(f'parse_en_stat2 отработала за {datetime.now()-start_time}')
    return header, bonus_output_list, nobonus_output_list


def generate_csv(json, with_bonuses: bool):
    stat_d = {}
    for level in json['StatItems']:
        for statitem in level:
            total_seconds = statitem['SpentLevelTime']['TotalSeconds']
            if with_bonuses and statitem.get('Corrections'):
                total_seconds -= statitem['Corrections']['CorrectionValue']['TotalSeconds']

            sign = "-" if total_seconds < 0 else ''
            total_seconds = abs(total_seconds)
            minutes, seconds = divmod(total_seconds, 60)
            hours, minutes = divmod(minutes, 60)
            stat_d.setdefault(statitem['TeamName'] or statitem['UserName'], {})[statitem['LevelNum']] = f"{sign}{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"

    file_text = ''
    for i in range(0, json['Game']['LevelNumber']+1):
        file_text += str(i)+';'
    for team, values in stat_d.items():
        file_text += '\n' + team + ';' + ';'.join(values.values())+';'

    return file_text


def get_rates(my_url: str):
    gid = parse_qs(urlparse(my_url).query)['gid'][0]
    with requests.Session() as session:
        all_teams = session.get(f'https://world.en.cx/ALoader/GameLoader.aspx?gid={gid}&item=3', headers={'User-Agent': config.user_agent})
        html = BeautifulSoup(all_teams.text, 'lxml')
        rates = []
        for team in html.find_all(id='lnkPlayerInfo'):
            team_name = team.get_text(strip=True)
            href = urlparse(team['href'])
            tid = parse_qs(href.query)['tid'][0]
            rates_url = f'https://world.en.cx/ALoader/FormulaDetails.aspx?gid={gid}&tid={tid}&mode=0'
            team_players = session.get(rates_url, headers={'User-Agent': config.user_agent})
            rates_doc = BeautifulSoup(team_players.text, 'lxml')
            for p in rates_doc.find_all(class_='toWinnerItem'):
                player_node = p.find('a', href=re.compile(r'uid=\d+'))
                player_rate = p.find('td', class_='pink').get_text(strip=True)
                player_name = player_node.get_text(strip=True)
                player_weight = p.find('td', class_='yellow_lihgt').get_text(strip=True)
                rates.append((int(player_rate), team_name, player_name, float(player_weight)))
            sleep(0.2)
        if not len(rates):
            return []

        result = []
        for key, group in groupby(rates, lambda x: x[1]):  # group by team
            for entry in group:
                result.append(f'{str(entry[0]).ljust(2)} {entry[1]} {entry[2]}')
            result.append('')  # separate teams by line break
        # unweighted rate
        result.append('ИТОГ: ' + str(round(sum([x[0] for x in rates]) / len(rates), 2)))
        # weighted rate
        result.append('ИТОГ(+вес): ' + str(round(sum([x[0] * x[3] for x in rates]) / sum([x[3] for x in rates]), 2)))
        return result


TIME_UNITS = {'М': 2592000, 'дн': 86400, 'ч': 3600, 'м': 60, 'с': 1}
MAIN_PATTERN = re.compile(r'<br/>(бонус|штраф)([^<]*)<')
COMPONENT_PATTERN = re.compile(r'(\d+)(М|дн|ч|м|с)')
UP_DATETIME_PATTERN = re.compile(r'(\d{2}\.\d{2}\.\d{4}).*?(\d{2}:\d{2}:\d{2}\.\d{3})')


def parse_bonus_time(bonus_text):
    text_to_parse = str(bonus_text)
    match = MAIN_PATTERN.search(text_to_parse)
    if not match:
        return 0
    bonus_penalty_type = match.group(1)
    duration_str = match.group(2).strip().replace(' ', '')
    k = -1 if bonus_penalty_type == 'бонус' else 1
    total_sec = 0
    components = COMPONENT_PATTERN.findall(duration_str)
    for value_str, unit in components:
        value = int(value_str)
        total_sec += value * TIME_UNITS[unit]
    return total_sec * k


def parse_html_stat(html_source, levels_list):
    date_start = datetime.strptime(re.search(r"(?<=sliderStartTime = ').*(?=\';)", str(html_source))[0], '%d.%m.%Y %H:%M:%S.%f')
    soup = BeautifulSoup(html_source, 'lxml')

    parse_data = soup.find('table', id='GameStatObject_DataTable')
    if not parse_data:
        parse_data = soup.find('table', id='GameStatObject2_DataTable')
    stat_list = []
    for tr_set in parse_data.find_all('tr')[1:-1]:
        cells = tr_set.find_all('div', class_='dataCell')
        for level_num, elem in enumerate(cells, 1):
            team = elem.find('a').text
            up_datetime_match = UP_DATETIME_PATTERN.search(str(elem))
            up_datetime = datetime.strptime(f"{up_datetime_match.group(1)} {up_datetime_match.group(2)}", '%d.%m.%Y %H:%M:%S.%f')
            level_order_tag = elem.find('div', class_='n')
            level_order = int(level_order_tag.text) if level_order_tag else level_num
            bonus_sec_total = parse_bonus_time(elem)

            stat_list.append({
                'team': team,
                'level_num': level_num,
                'up_datetime': up_datetime,
                'bonus_sec_total': bonus_sec_total,
                'level_order': level_order
            })

    final_results = get_final_results_from_stat(stat_list, levels_list, date_start)

    maxlen = 0
    if final_results:
        maxlen = max(len(a[0]) for a in final_results)

    sorted_bonus_list = sorted(final_results, key=lambda x: (-x[3], x[2]))

    bonus_output_list = ['С бонусами:']
    for i, a in enumerate(sorted_bonus_list):
        team_name = a[0]
        formatted_time = format_timedelta(a[2])
        levels_count = a[3]
        bonus_output_list.append(f'{i + 1} {team_name.ljust(maxlen)} {formatted_time} {levels_count}')

    sorted_nobonus_list = sorted(final_results, key=lambda x: (-x[3], x[1]))

    nobonus_output_list = ['Без бонусов:']
    for i, a in enumerate(sorted_nobonus_list):
        team_name = a[0]
        formatted_time = format_timedelta(a[1])  # Используем время без бонусов
        levels_count = a[3]
        nobonus_output_list.append(f'{i + 1} {team_name.ljust(maxlen)} {formatted_time} {levels_count}')

    return bonus_output_list, nobonus_output_list


def get_final_results_from_stat(stat_list, levels_list, date_start):
    team_level_order_lookup = {}
    result_dict = {}
    for entry in stat_list:
        team_level_order_lookup.setdefault(entry['team'], {})[entry['level_order']] = entry['up_datetime']
    calculated_level_durations = []

    for entry in stat_list:
        team = entry['team']
        level_num = entry['level_num']
        up_datetime = entry['up_datetime']
        bonus_sec_total = entry['bonus_sec_total']
        level_order = entry['level_order']

        # Ранняя фильтрация по levels_list
        if level_num not in levels_list:
            continue

        level_duration = None
        if level_order == 1:
            level_duration = up_datetime - date_start
        else:
            prev_up_datetime = team_level_order_lookup.get(team, {}).get(level_order - 1)
            if prev_up_datetime:
                level_duration = up_datetime - prev_up_datetime

        if level_duration is not None:
            calculated_level_durations.append({'team': team, 'duration': level_duration, 'bonus_sec_total': bonus_sec_total})
    for entry in calculated_level_durations:
        team = entry['team']
        duration = entry['duration']
        bonus_sec_total = entry['bonus_sec_total']

        if team not in result_dict:
            result_dict[team] = [timedelta(0), 0, timedelta(0)]

        result_dict[team][0] += duration
        result_dict[team][1] += 1
        result_dict[team][2] += duration + timedelta(seconds=bonus_sec_total)
    final_results = []
    for team, data in result_dict.items():
        final_results.append((team, data[0], data[2], data[1]))

    return final_results
