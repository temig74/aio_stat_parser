from bs4 import BeautifulSoup #pip install beautifulsoup4
import re
from urllib.request import urlopen
from operator import itemgetter
from datetime import datetime, timedelta

def parse_en_stat2(my_url, levels_list):
    result_dict = {}
    my_url += '&sortfield=SpentSeconds&lang=ru'  # Сортируем по времени на уровне, на случай указанной последовательности
    stat_list = []  # Список с кортежами всей статы распаршеной (с временами во сколько апнуты уровни и номерами уровней
    new_stat_list = []  # Отсеянный список только с нужными номерами уровней и вычисленным временем уровня

    rs = urlopen(my_url)
    html = BeautifulSoup(rs, 'html.parser')
    rs.close()

    '''
        # получаем дату и время начала игры (старый способ, ниже новый из слайдера)
        gamelink = html.find('a', id='lnkDomain').get('href')[:-1] + html.find('a', id='lnkGameName').get('href')
        rs = urlopen(gamelink + '&lang=ru')
        html2 = BeautifulSoup(rs, 'html.parser')
        rs.close()
        span_set = html2.find('table', class_='gameInfo').find_all('span')
        for elem in span_set:
            if elem.text == 'Начало игры':
                date_start_str = re.search(r'\d\d\.\d\d\.\d\d\d\d \d*:\d\d:\d\d', elem.find_next().text).group(0)
                date_start = datetime.strptime(date_start_str, '%d.%m.%Y %H:%M:%S')
                break
        '''

    # window.sliderStartTime = '23.09.2023 08:00:00.000';
    date_start = datetime.strptime(re.search(r"(?<=sliderStartTime = ').*(?=\';)", str(html))[0], '%d.%m.%Y %H:%M:%S.%f')

    parse_data = html.find('table', id='GameStatObject_DataTable')
    if not parse_data:
        parse_data = html.find('table', id='GameStatObject2_DataTable')
    #parse_set = html.find('table', id='GameStatObject_DataTable').find_all('tr')[1:-1]
    parse_set = parse_data.find_all('tr')[1:-1]
    for a in parse_set:
        level = 0
        b = a.find_all('div', class_='dataCell')
        for elem in b:
            level += 1
            level_order = int(elem.find('div', class_='n').text) if (elem.find('div', class_='n') is not None) else level
            team = elem.find('a').text
            up_date = re.findall(r'\d\d\.\d\d\.\d\d\d\d', str(elem))  # 12.11.2022
            up_time = re.findall(r'\d\d:\d\d:\d\d\.\d\d\d', str(elem))  # 12:11:11.000
            up_datetime = datetime.strptime((up_date[0] + ' ' + up_time[0]), '%d.%m.%Y %H:%M:%S.%f')
            bonus = re.findall(r'<br/>бонус[^<]*<', str(elem))
            if len(bonus) == 1:
                sec_bonus = eval(bonus[0][11:-1].replace(' ', '').replace('М', '*2592000+').replace('дн', '*86400+').replace('ч', '*3600+').replace('м', '*60+').replace('с', '*1+')[:-1])
            else:
                sec_bonus = 0
            penalty = re.findall(r'<br/>штраф[^<]*<', str(elem))
            if len(penalty) == 1:
                sec_penalty = eval(
                    penalty[0][11:-1].replace(' ', '').replace('М', '*2592000+').replace('дн', '*86400+').replace('ч', '*3600+').replace('м', '*60+').replace('с', '*1+')[:-1])
            else:
                sec_penalty = 0
            sec_total = sec_penalty - sec_bonus
            stat_list.append((team, level, up_datetime, sec_total, level_order))

    for a in stat_list:
        if a[1] in levels_list:
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

    maxlen = max(len(a[0]) for a in result_list)

    sorted_bonus_list = sorted(sorted(result_list, key=itemgetter(2)), key=itemgetter(3), reverse=True)
    bonus_output_list = ['С бонусами:'] + [f'{i+1} {a[0]} {" " * (maxlen - len(a[0])-len(str(i+1))+2)} {str(a[2]).split(".")[0] if a[2] >= timedelta(0) else "-" + str(timedelta() - a[2]).split(".")[0]} {a[3]}' for i, a in enumerate(sorted_bonus_list)]

    sorted_nobonus_list = sorted(sorted(result_list, key=itemgetter(1)), key=itemgetter(3), reverse=True)
    #nobonus_output_list = ['Без бонусов:'] + [f'{i+1} {a[0].split(".")[0]} {" " * (maxlen - len(a[0])-len(str(i+1))+2)} {str(a[1])[:-3]} {a[3]}' for i, a in enumerate(sorted_nobonus_list)]
    nobonus_output_list = ['Без бонусов:'] + [f'{i + 1} {a[0]} {" " * (maxlen - len(a[0]) - len(str(i + 1)) + 2)} {str(a[1])[:-3]} {a[3]}' for i, a in enumerate(sorted_nobonus_list)]

    return bonus_output_list, nobonus_output_list