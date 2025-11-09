import asyncio
import aiohttp
import html
import logging
import sys
import io

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandObject
from aiogram.filters.command import Command

from config_reader import config
from stat_parser2 import generate_csv, get_rates, parse_en_stat2, parse_html_stat, get_json
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)])
bot = Bot(token=config.bot_token.get_secret_value())
dp = Dispatcher()

example = f'''<code>/stat https://dozorekb.en.cx/GameStat.aspx?gid=76109</code>
<code>/stat https://dozorekb.en.cx/GameStat.aspx?gid=76109 8 15 19 25 86 89-95 99</code>
<code>/stat https://dozorekb.en.cx/GameStat.aspx?gid=76109 1-103 -22 -35 -52 -68 -78 -79 -80</code> (уровни 1-103, за исключением 22 35 52 68 78 79 80)
<code>/textstat https://dozorekb.en.cx/GameStat.aspx?gid=76109 доезд QRV</code> (только доезды и QRV)
<code>/textstat https://dozorekb.en.cx/GameStat.aspx?gid=76109 -доезд -QRV</code> (исключить доезды и QRV)
<code>/csv https://dozorekb.en.cx/GameStat.aspx?gid=76109</code>
'''
example2 = '<code>/rates https://dozorekb.en.cx/GameDetails.aspx?gid=76109</code>'
example3 = f'''<code>/hstat https://dozorekb.en.cx/GameStat.aspx?gid=76109 ваш_id_в_энке 1-103 -22 -35 -52 -68 -78 -79 -80</code>
Для ее просмотра добавьте бота https://world.en.cx/UserDetails.aspx?uid={config.bot_en_id} в авторы игры, дайте ему права автора, и установите у себя в профиле tg, с которого общаетесь с ботом. Бот проверит, что Вы в составе авторов игры и запросит статистику'''


def command_info(message: types.Message):
    return f'@{message.from_user.username} ({message.from_user.full_name or 'N/A'}) {message.chat.id}\n{message.text}'


@dp.message(Command(commands=['start', 'help']))
async def cmd_start(message: types.Message):
    logging.info(command_info(message))
    await message.answer(f'''Temig stat parser
Пример:
{example}
Имейте в виду, бот не учитывает вручную начисленные бонусы, у которых не проставлен номер уровня.
Также, некорректно считать штурмовую последовательность.

Узнать все оценки за игру:
{example2}

Скрытая статистика:
{example3}

Также, можете загрузить html файл с сохраненной страницей статистики (только отсортируйте по времени на уровне, если вдруг у вас последовательность указанная и переключите домен на русский язык, если он на другом языке (можно добавить &lang=ru в конец ссылки)), добавив в подпись номера уровней, по которым надо посчитать стату (аналочно команде stat)''', parse_mode='HTML')


@dp.message(Command('stat'))
async def cmd_stat(message: types.Message, command: CommandObject):
    logging.info(command_info(message))
    await message.answer('Считаю статистику, подождите...')
    input_args = command.args.split(maxsplit=1)

    if not input_args:
        await message.answer(f'Пример ввода:\n{example}', parse_mode='HTML')
        return

    my_url = input_args[0]
    levels_text = input_args[1] if len(input_args) > 1 else ''

    try:
        levels_list = parse_level_nums(levels_text)
    except:
        await message.answer('Ошибка списка уровней')
        return

    try:
        json_data = await get_json(my_url)

        result = parse_en_stat2(json_data, levels_list)
        await send_result(message.chat.id, result)
    except Exception as ex:
        logging.error(ex)
        await message.answer(f'Ошибка, возможно неверный формат ввода или некорректная статистика.\nПример ввода:\n{example}', parse_mode='HTML')


@dp.message(Command('textstat'))
async def cmd_textstat(message: types.Message, command: CommandObject):
    logging.info(command_info(message))
    await message.answer('Считаю статистику, подождите...')
    input_args = command.args.split()
    if len(input_args) < 2:
        await message.answer(f'Пример ввода:\n{example}', parse_mode='HTML')
        return
    my_url = input_args[0]
    levels_text = input_args[1:]

    try:
        json_data = await get_json(my_url)
    except Exception as e:
        logging.error(f'Ошибка получения json {e}')
        await message.answer('Ошибка получения json')
        return

    level_count = json_data['Game']['LevelNumber']
    parsed_level_data = []

    if not json_data['Game']['HideLevelsNames']:  # названия уровней есть в json, если не стоит галка "скрыть названия уровней до конца игры"
        for level in json_data['Levels']:
            parsed_level_data.append((int(level['LevelNumber']), level['LevelName'].lower()))
    else:
        url_obj = urlparse(my_url)
        gid = parse_qs(url_obj.query)['gid'][0]
        stat_url = f'https://{url_obj.hostname}/GameStat.aspx?gid={gid}&sortfield=SpentSeconds&lang=ru'

        async with aiohttp.get(stat_url, headers={"User-Agent": config.user_agent}) as rs:
            rs.raise_for_status()
            soup = BeautifulSoup(await rs.text(), 'lxml')
            parse_levels = soup.find('tr', class_='levelsRow').find_all('td')
            for td in parse_levels[1:-3]:
                for span in td.find_all('span', class_='dismissed'):
                    span.decompose()
                text = td.get_text(strip=True).lower()
                level_number, level_name = text.split(':', maxsplit=1)
                parsed_level_data.append((int(level_number), level_name))

    levels_list = []
    if levels_text[0][0] == '-':  # Если мы минусуем какие-то уровни из общей статы
        excluded_filters = {elem[1:].lower() for elem in levels_text}
        excluded_level_num_set = set()
        for level_num, level_name in parsed_level_data:
            for exclude_pattern in excluded_filters:
                if exclude_pattern in level_name:
                    excluded_level_num_set.add(level_num)
                    break

        for level_num in range(1, level_count + 1):
            if level_num not in excluded_level_num_set:
                levels_list.append(level_num)

    else:  # Если отбираем только конкретные уровни
        included_filters = {elem.lower() for elem in levels_text}
        for level_num, level_name in parsed_level_data:
            for include_pattern in included_filters:
                if include_pattern in level_name:
                    levels_list.append(level_num)
                    break

    try:
        result = parse_en_stat2(json_data, levels_list)
        await send_result(message.chat.id, result)
    except Exception as ex:
        logging.error(ex)
        await message.answer(f'Ошибка, возможно неверный формат ввода или некорректная статистика.\nПример ввода:\n{example}', parse_mode='HTML')


@dp.message(Command('csv'))
async def cmd_csv(message: types.Message, command: CommandObject):
    logging.info(command_info(message))
    await message.answer('Генерирую файл, подождите...')
    try:
        my_url = command.args.split()[0]
        json_data = await get_json(my_url)

        file_text = generate_csv(json_data, True)
        buf_file = types.BufferedInputFile(bytes(file_text, 'utf-8-sig'), filename='with_bonuses.csv')
        await message.answer_document(buf_file)

        file_text = generate_csv(json_data, False)
        buf_file = types.BufferedInputFile(bytes(file_text, 'utf-8-sig'), filename='without_bonuses.csv')
        await message.answer_document(buf_file)
    except Exception as ex:
        logging.error(ex)
        await message.answer(f'Ошибка, возможно неверный формат ввода или некорректная статистика.\nПример ввода:\n{example}', parse_mode='HTML')


@dp.message(Command('rates'))
async def cmd_rates(message: types.Message, command: CommandObject):
    logging.info(command_info(message))
    await message.answer('Получаю оценки...')
    try:

        marks = await get_rates(command.args.split()[0])

        if len(marks):
            await message.answer('<code>' + html.escape('\n'.join(marks)) + '</code>', parse_mode='HTML')
        else:
            await message.answer('Оценок нет')
    except Exception as ex:
        logging.error(ex)
        await message.answer('Ошибка :(', parse_mode='HTML')


@dp.message(Command('chat_id'))
async def cmd_chat_id(message: types.Message, command: CommandObject):
    await message.answer(f'{message.chat.id}')


@dp.message(Command('hstat'))
async def cmd_hstat(message: types.Message, command: CommandObject):
    logging.info(command_info(message))
    parts = command.args.split(maxsplit=2) if command.args else []
    if len(parts) != 3:
        await message.answer(f'Неверный формат команды, пример {example3}', parse_mode='HTML')
        return

    my_url, author_id, levels_text = parts
    url_obj = urlparse(my_url)
    my_domain = url_obj.hostname
    if not my_domain:
        await message.answer('Некорректный домен')
        return
    my_game_id = parse_qs(url_obj.query)['gid'][0]
    if not my_game_id:
        await message.answer('Некорректный id игры')
        return

    async with aiohttp.ClientSession(headers={"User-Agent": config.user_agent}) as my_session:
        auth_url = f'https://{my_domain}/Login.aspx'
        data = {'Login': config.en_username, 'Password': config.en_password.get_secret_value()}
        try:
            async with my_session.post(auth_url, data=data) as rs:
                rs.raise_for_status()
        except Exception as e:
            logging.error(f'Ошибка авторизации {e}')
            await message.answer('Ошибка авторизации')
            return

        # Проверка, что id находится в списке авторов игры
        try:
            async with my_session.get(f'https://{my_domain}/GameDetails.aspx?gid={my_game_id}') as rs:
                rs.raise_for_status()
                soup = BeautifulSoup(await rs.text(), 'lxml')
                authors_links = soup.select('a[id^="GameDetail_AuthorsRepeater"]')
                authors_list = []
                for a in authors_links:
                    href = a.get('href', '')
                    if href:
                        url_obj = urlparse(href)
                        user_id = parse_qs(url_obj.query)['uid'][0]
                        if user_id:
                            authors_list.append(user_id)
                if author_id not in authors_list:
                    await message.answer('Ваш id не находится в списке авторов игры')
                    return
                if config.bot_en_id not in authors_list:
                    await message.answer(f'enstatbot (https://world.en.cx/UserDetails.aspx?uid={config.bot_en_id}) не находится в списке авторов игры')
                    return
        except Exception as e:
            logging.error(f'Ошибка проверки списка авторов игры {e}')
            await message.answer('Ошибка проверки списка авторов игры')
            return


        # Проверка, что телеграм в профиле соответствует тому, кто обращается
        try:
            async with my_session.get(f'https://{my_domain}/UserDetails.aspx?uid={author_id}') as rs:
                rs.raise_for_status()
                soup = BeautifulSoup(await rs.text(), 'lxml')
                tg_span_tag = soup.find('span', id='EnTabContainer1_content_ctl00_panelLineContacts_contactsBlock_JabberValue')
                if tg_span_tag:
                    tg_contact = tg_span_tag.get_text()
                else:
                    await message.answer('У данного id не указан Telegram')
                    return
                if tg_contact.lower() != message.from_user.username.lower():
                    await message.answer('Ваше имя в tg не соответствует tg указанного автора')
                    return
        except Exception as e:
            logging.error(f'Ошибка проверки телеграма в en-профиле {e}')
            await message.answer('Ошибка проверки телеграма в en-профиле')
            return

        try:
            levels_list = parse_level_nums(levels_text)
        except Exception as e:
            logging.error(f'Ошибка списка уровней {e}')
            await message.answer(f'Ошибка списка уровней')
            return

        info_str = f'Пользователь tg @{message.chat.username} с en id https://world.en.cx/UserDetails.aspx?uid={author_id} считает закрытую статистику игры {my_url}'
        logging.info(info_str)
        await bot.send_message(config.admin_chat_id, info_str)
        #try:
        async with my_session.get(f'https://{my_domain}/GameStat.aspx?gid={my_game_id}&sortfield=SpentSeconds&lang=ru') as rs:
            rs.raise_for_status()
            html_source = await rs.text()
            result = parse_html_stat(html_source, levels_list)
            await send_result(message.chat.id, result)
        #except Exception as e:
        #    logging.error(f'Ошибка парсера статистики {e}')
        #    await message.answer('Ошибка парсера статистики')


async def send_result(chat_id, result):
    for entry in result:
        result_str = ''
        for line in entry:
            result_str += line + '\n'
            if len(result_str) > config.max_message_len:
                await bot.send_message(chat_id, '<code>' + html.escape(result_str) + '</code>', parse_mode='HTML')
                result_str = ''
        await bot.send_message(chat_id, '<code>' + html.escape(result_str) + '</code>', parse_mode='HTML')


# Парсит строку с уровнями (например, "1 3-5 -2") в отсортированный список уникальных int.
def parse_level_nums(levels_text: str) -> list[int]:
    parsed_levels = []
    for elem in levels_text.split():
        if elem.startswith('-'):
            parsed_levels.remove(int(elem[1:]))
        elif '-' in elem:
            start_str, end_str = elem.split('-')
            start = int(start_str)
            end = int(end_str)
            for i in range(start, end+1):
                parsed_levels.append(i)
        else:
            parsed_levels.append(int(elem))
    return sorted(parsed_levels)


@dp.message(F.document)
async def cmd_hstat_file(message: types.Message):
    MAX_FILE_SIZE_BYTES = 7 * 1024 * 1024
    document = message.document
    caption = message.caption

    if not caption:
        await message.reply("Подпись к файлу с номерами уровней не найдена")
        return

    try:
        levels_list = parse_level_nums(caption)
    except:
        await message.answer('Ошибка списка уровней')
        return

    if document.mime_type != 'text/html' and not document.file_name.endswith(('.html', '.htm')):
        await message.reply("Пожалуйста, загрузите действительный HTML файл.")
        return

    if document.file_size > MAX_FILE_SIZE_BYTES:
        await message.reply('Слишком большой файл')
        return

    await message.reply("Получаю Ваш HTML файл, пожалуйста, подождите...")

    try:
        html_buffer = io.BytesIO()
        file_info = await bot.get_file(document.file_id)
        logging.info(f'user {message.from_user.username} is loading stat file {document.file_name}')
        await bot.download_file(file_info.file_path, destination=html_buffer)
        html_buffer.seek(0)
        html_content_str = html_buffer.read().decode('utf-8')
        result = parse_html_stat(html_content_str, levels_list)
        await send_result(message.chat.id, result)
    except:
        await message.reply("Произошла ошибка")


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
