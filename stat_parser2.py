import re
import requests
# from string import Formatter
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta

# https://stackoverflow.com/a/49986645/1656677
regex_pattern = re.compile(pattern="["
                           u"\U0001F600-\U0001F64F"  # emoticons
                           u"\U0001F300-\U0001F5FF"  # symbols & pictographs
                           u"\U0001F680-\U0001F6FF"  # transport & map symbols
                           u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
                           "]+", flags=re.UNICODE)

'''
def strfdelta(tdelta: timedelta, fmt='{D:02}d {H:02}h {M:02}m {S:02}s'):
    remainder = int(tdelta.total_seconds())
    f = Formatter()
    desired_fields = [field_tuple[1] for field_tuple in f.parse(fmt)]
    possible_fields = ('W', 'D', 'H', 'M', 'S')
    constants = {'W': 604800, 'D': 86400, 'H': 3600, 'M': 60, 'S': 1}
    values = {}
    for field in possible_fields:
        if field in desired_fields and field in constants:
            Quotient, remainder = divmod(remainder, constants[field])
            values[field] = int(Quotient)
    values['mS'] = int(tdelta.microseconds / 1000)
    return f.format(fmt, **values)
'''


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
    api_url = f'https://{url.hostname}/gamestatistics/full/{gid}?json=1'
    return requests.get(api_url, headers={"User-Agent": "dummy"}).json()


def deEmojify(text):
    return regex_pattern.sub(r'?', text)


def parse_en_stat2(my_url, levels_list=[], level_text=''):
    result_dict = {}
    stat_list = []  # Список с кортежами всей статы (с временами во сколько апнуты уровни и номерами уровней)
    new_stat_list = []  # Отсеянный список только с нужными номерами уровней и вычисленным временем уровня
    json = get_json(my_url)

    if level_text != '':
        for level in json['Levels']:
            if level_text.lower() in level['LevelName'].lower():
                levels_list.append(level['LevelNumber'])

    def datetime_from_seconds(milliseconds_from_zero_year):
        # в движке все расчеты идут по секундам (по словам музыканта)
        # но все равно возьмем время с миллисекундами для более точных результатов
        return datetime(1, 1, 1) + timedelta(milliseconds=round(milliseconds_from_zero_year))

    def get_stat_item(x):
        finished_at = datetime_from_seconds(x['ActionTime']['Value'])
        bonus_time = -x['Corrections']['CorrectionValue']['TotalSeconds'] if x['Corrections'] else 0
        return deEmojify(x['TeamName'] or x['UserName']), x['LevelNum'], finished_at, bonus_time, x['LevelOrder']

    date_start = datetime_from_seconds(json['Game']['StartDateTime']['Value'])
    for level in json['StatItems']:
        stat_list.extend(get_stat_item(x) for x in level)
    dismissed_levels = set(x['LevelNumber'] for x in json['Levels'] if x['Dismissed'])

    for a in stat_list:
        if (a[1] in levels_list) or len(levels_list) == 0:  # если уровни не заданы - считаем по всем
            if a[4] == 1:  # если первый уровень, то нужно вычитать из времени начала игры
                new_stat_list.append([a[0], a[1], a[2] - date_start, a[3]])

            for b in stat_list:
                if b[0] == a[0] and b[4] == a[4] - 1:  # если одинаковая команда и порядок выдачи перед текущим, то считаем время уровня и обрываем цикл
                    new_stat_list.append([a[0], a[1], a[2] - b[2], a[3]])
                    break

    for a in new_stat_list:
        if a[0] in result_dict:
            result_dict[a[0]] = [result_dict[a[0]][0] + a[2], result_dict[a[0]][1] + 1, result_dict[a[0]][2] + a[2] + timedelta(seconds=a[3])]
        else:
            result_dict[a[0]] = [a[2], 1, a[2] + timedelta(seconds=a[3])]

    result_list = [(a, result_dict[a][0], result_dict[a][2], result_dict[a][1]) for a in result_dict]

    # посчитаем максимальную ширину столбцов "место" и "команда" для выравнивания
    team_width = max(len(a[0]) for a in result_list)
    pos_width = len(str(len(result_list)))

    def format_line(i, row, with_bonus):
        pos = str(i + 1).ljust(pos_width)
        team = row[0].ljust(team_width)
        # time = strfdelta(row[2] if with_bonus else row[1], '{H:02}:{M:02}:{S:02}.{mS:03}')
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

    return header, bonus_output_list, nobonus_output_list


def generate_csv(my_url, with_bonuses: bool):
    json = get_json(my_url)
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
            stat_d.setdefault(statitem['TeamName'], {})[statitem['LevelNum']] = f"{sign}{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"

    file_text = ''
    for i in range(0, json['Game']['LevelNumber']+1):
        file_text += str(i)+';'
    for team, values in stat_d.items():
        file_text += '\n' + team + ';' + ';'.join(values.values())+';'

    return file_text
