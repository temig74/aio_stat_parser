import re
import requests
# from string import Formatter
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from emoji import replace_emoji


def format_timedelta(tdelta: timedelta):
    seconds = tdelta.total_seconds()
    sign = "-" if seconds < 0 else " "
    seconds = abs(seconds)
    milliseconds = int(round((seconds - int(seconds))*1000))
    seconds = int(seconds)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{sign}{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}:{milliseconds:03d}"


def get_json(my_url, page_num):
    url = urlparse(my_url)
    gid = parse_qs(url.query)['gid'][0]
    api_url = f'https://{url.hostname}/gamestatistics/full/{gid}?json=1&page={page_num}'
    return requests.get(api_url, headers={"User-Agent": "dummy"}).json()


'''
def deEmojify(text):
    # https://stackoverflow.com/a/49986645/1656677
    regex_pattern = re.compile(pattern="["
                                       u"\U0001F600-\U0001F64F"  # emoticons
                                       u"\U0001F300-\U0001F5FF"  # symbols & pictographs
                                       u"\U0001F680-\U0001F6FF"  # transport & map symbols
                                       u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
                                       "]+", flags=re.UNICODE)
    return regex_pattern.sub(r'?', text)
'''


def parse_en_stat2(my_url, levels_text, search_type):
    json_list = []
    MAX_PAGES = 10
    for i in range(1, MAX_PAGES):
        json_elem = get_json(my_url, i)
        if json_elem['StatItems'][0]:
            json_list.append(json_elem)
        else:
            break
    json = json_list[0]
    if json['Game']['LevelsSequenceId'] == 3:
        return ['Ошибка: не применимо в штурмовой последовательности'], []
    levels_list = []  # Список уровней, по которым считать стату
    level_count = json['Game']['LevelNumber']  # количество уровней в игре

    if search_type == 'by_nums':  # исключение и добавление уровней по номеру (для команды /stat)
        for elem in levels_text:
            if elem[0] == '-':
                levels_list.remove(int(elem[1:]))
            elif '-' in elem:
                for i in range(int(elem.split('-')[0]), int(elem.split('-')[1]) + 1):
                    if level_count < i:  # если задали номер уровня больше, чем есть
                        break
                    levels_list.append(i)
            elif int(elem) <= level_count:
                levels_list.append(int(elem))

    elif search_type == 'by_text':  # исключение и добавление уровней по тексту (для команды /textstat)
        if not json['Game']['HideLevelsNames']:  # названия уровней есть в json, если не стоит галка "скрыть названия уровней до конца игры"
            levelnames = [f"{level['LevelNumber']}: {level['LevelName'].lower()}" for level in json['Levels']]
        else:
            url = urlparse(my_url)
            gid = parse_qs(url.query)['gid'][0]
            stat_url = f'https://{url.hostname}/GameStat.aspx?gid={gid}&sortfield=SpentSeconds'
            rs = requests.get(stat_url, headers={"User-Agent": "dummy"})
            html = BeautifulSoup(rs.text, 'html.parser')
            parse_levels = html.find('tr', class_='levelsRow').find_all('td')
            levelnames = []
            for td in parse_levels[1:-3]:
                for span in td.find_all('span', class_='dismissed'):
                    span.decompose()
                levelnames.append(td.get_text(strip=True).lower())

        if levels_text[0][0] == '-':
            levels_list = list(range(1, level_count+1))
            for levelname in levelnames:
                for elem in levels_text:
                    if elem[1:] in levelname:
                        levels_list.remove(int(levelname.split(':')[0]))
        else:
            for levelname in levelnames:
                for elem in levels_text:
                    if elem in levelname:
                        levels_list.append(int(levelname.split(':')[0]))

    def datetime_from_seconds(milliseconds_from_zero_year):
        # в движке все расчеты идут по секундам (по словам музыканта)
        # но все равно возьмем время с миллисекундами для более точных результатов
        return datetime(1, 1, 1) + timedelta(milliseconds=round(milliseconds_from_zero_year))

    def get_stat_item(x):
        finished_at = datetime_from_seconds(x['ActionTime']['Value'])
        bonus_time = -x['Corrections']['CorrectionValue']['TotalSeconds'] if x['Corrections'] else 0
        # return deEmojify(x['TeamName'] or x['UserName']), x['LevelNum'], finished_at, bonus_time, x['LevelOrder']
        return replace_emoji(x['TeamName'] or x['UserName'], replace=''), x['LevelNum'], finished_at, bonus_time, x['LevelOrder']

    date_start = datetime_from_seconds(json['Game']['StartDateTime']['Value'])
    stat_list = []  # (участник0, номер_ур1, время_завершения2, бонус3, порядок выдачи4)

    for json in json_list:
        for level in json['StatItems']:
            stat_list.extend(get_stat_item(x) for x in level)
    dismissed_levels = set(x['LevelNumber'] for x in json['Levels'] if x['Dismissed'])

    new_stat_list = []  # Отсеянный список только с нужными номерами уровней: [участник0, номер_ур1, время_ур2, бонус3]

    for a in stat_list:
        if (a[1] in levels_list) or len(levels_list) == 0:  # если уровень в списке, или уровни не заданы
            if a[4] == 1:  # если первый уровень, то нужно вычитать из времени начала игры
                new_stat_list.append([a[0], a[1], a[2] - date_start, a[3]])
            else:
                for b in stat_list:
                    if b[0] == a[0] and b[4] == a[4] - 1:  # если одинаковая команда и порядок выдачи перед текущим, то считаем время уровня и обрываем цикл
                        new_stat_list.append([a[0], a[1], a[2] - b[2], a[3]])
                        break

    result_dict = {}  # {команда: [общее время, кол-во пройденных уровней, время с бонусами]}
    for a in new_stat_list:
        if a[0] in result_dict:
            result_dict[a[0]] = [result_dict[a[0]][0] + a[2], result_dict[a[0]][1] + 1, result_dict[a[0]][2] + a[2] + timedelta(seconds=a[3])]
        else:
            result_dict[a[0]] = [a[2], 1, a[2] + timedelta(seconds=a[3])]

    result_list = [[a, result_dict[a][0], result_dict[a][2], result_dict[a][1]] for a in result_dict]

    '''
    cut_num = 13
    for elem in result_list:
        if len(elem[0]) > cut_num:
            elem[0] = elem[0][:cut_num]
    '''

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
            stat_d.setdefault(statitem['TeamName'] or statitem['UserName'], {})[statitem['LevelNum']] = f"{sign}{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"

    file_text = ''
    for i in range(0, json['Game']['LevelNumber']+1):
        file_text += str(i)+';'
    for team, values in stat_d.items():
        file_text += '\n' + team + ';' + ';'.join(values.values())+';'

    return file_text
