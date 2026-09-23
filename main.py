import asyncio
import html
import io
import logging
import sys
from urllib.parse import parse_qs, urlparse

import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandObject
from aiogram.filters.command import Command
from bs4 import BeautifulSoup

from config_reader import config
from stat_parser2 import (
    HTTP_TIMEOUT,
    fetch_stat_pages,
    filter_levels_by_pattern,
    generate_csv,
    generate_csv_from_html,
    get_json,
    get_level_names_json,
    get_rates,
    parse_game_url,
    parse_html_stat,
    parse_en_stat2,
    parse_level_nums,
    parse_levels_row_from_html,
)

logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)])
bot = Bot(token=config.bot_token.get_secret_value())
dp = Dispatcher()
RATES_LOCK = asyncio.Lock()

# 'json' — статистика берётся через JSON API игры (сейчас может быть отключено на сайте);
# 'html' — статистика парсится из HTML-страницы GameStat.aspx.
# Задаётся в конфиге (config.stat_source), по умолчанию 'html'.
STAT_SOURCE = getattr(config, 'stat_source', 'html')
if STAT_SOURCE not in ('json', 'html'):
    logging.warning(f'Неизвестное значение stat_source={STAT_SOURCE!r}, использую "html"')
    STAT_SOURCE = 'html'

example = f'''<code>/stat https://dozorekb.en.cx/GameStat.aspx?gid=76109 8 15 19 25 86 89-95 99</code>
<code>/stat https://dozorekb.en.cx/GameStat.aspx?gid=76109 1-103 -22 -35 -52 -68 -78 -79 -80</code> (уровни 1-103, за исключением 22 35 52 68 78 79 80 (бонусы и штрафы на них не сохраняются))
<code>/stat https://dozorekb.en.cx/GameStat.aspx?gid=76109 1-103 !96</code> (уровни 1-103, со снятием уровня 96 (бонусы и штрафы на нем сохраняются, как при снятии в движке). Не используйте, если уровень уже снят в движке
<code>/textstat https://dozorekb.en.cx/GameStat.aspx?gid=76109 доезд QRV</code> (только доезды и QRV)
<code>/textstat https://dozorekb.en.cx/GameStat.aspx?gid=76109 -доезд -QRV</code> (исключить доезды и QRV)
<code>/csv https://dozorekb.en.cx/GameStat.aspx?gid=76109</code>
'''
example2 = '<code>/rates https://dozorekb.en.cx/GameDetails.aspx?gid=76109</code>'
example3 = f'''<code>/hstat https://dozorekb.en.cx/GameStat.aspx?gid=76109 ваш_id_в_энке 1-103 -22 -35 -52 -68 -78 -79 -80</code>
Для ее просмотра добавьте бота https://world.en.cx/UserDetails.aspx?uid={config.bot_en_id} в авторы игры, дайте ему права автора, и установите у себя в профиле tg, с которого общаетесь с ботом. Бот проверит, что Вы в составе авторов игры и запросит статистику'''


def command_info(message: types.Message) -> str:
    username = message.from_user.username or '—'
    return f'@{username} ({message.from_user.full_name or "N/A"}) {message.chat.id}\n{message.text}'


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

    input_args = command.args.split(maxsplit=1) if command.args else []
    if not input_args:
        await message.answer(f'Пример ввода:\n{example}', parse_mode='HTML')
        return

    my_url = input_args[0]
    game_ref = parse_game_url(my_url)
    if not game_ref:
        await message.answer('Некорректная ссылка на игру')
        return
    my_domain, my_game_id = game_ref

    levels_text = input_args[1] if len(input_args) > 1 else ''
    try:
        levels_list, dismissed_levels_list = parse_level_nums(levels_text)
    except ValueError as e:
        await message.answer(f'Ошибка списка уровней: {e}')
        return

    await message.answer('Считаю статистику, подождите...')

    try:
        if STAT_SOURCE == 'json':
            json_data = await get_json(my_url)
            result = parse_en_stat2(json_data, levels_list, dismissed_levels_list)
        else:
            async with aiohttp.ClientSession(headers={"User-Agent": config.user_agent}, timeout=HTTP_TIMEOUT) as session:
                pages = await fetch_stat_pages(session, my_domain, my_game_id)
            result = parse_html_stat(pages, levels_list, dismissed_levels_list)
        await send_result(message.chat.id, result)
    except Exception as e:
        logging.exception(f'Ошибка парсера статистики: {e}')
        await message.answer('Ошибка парсера статистики. Проверьте корректность ссылки и списка уровней.')


@dp.message(Command('textstat'))
async def cmd_textstat(message: types.Message, command: CommandObject):
    logging.info(command_info(message))

    input_args = command.args.split() if command.args else []
    if len(input_args) < 2:
        await message.answer(f'Пример ввода:\n{example}', parse_mode='HTML')
        return

    my_url = input_args[0]
    levels_text = input_args[1:]

    game_ref = parse_game_url(my_url)
    if not game_ref:
        await message.answer('Некорректная ссылка на игру')
        return
    my_domain, my_game_id = game_ref

    await message.answer('Считаю статистику, подождите...')

    try:
        if STAT_SOURCE == 'json':
            json_data = await get_json(my_url)
            parsed_level_data, level_count = await get_level_names_json(json_data, my_url)
            levels_list = filter_levels_by_pattern(parsed_level_data, level_count, levels_text)
            result = parse_en_stat2(json_data, levels_list)
        else:
            async with aiohttp.ClientSession(headers={"User-Agent": config.user_agent}, timeout=HTTP_TIMEOUT) as session:
                pages = await fetch_stat_pages(session, my_domain, my_game_id)
            parsed_level_data = parse_levels_row_from_html(pages[0])
            levels_list = filter_levels_by_pattern(parsed_level_data, len(parsed_level_data), levels_text)
            result = parse_html_stat(pages, levels_list, ())

        if not levels_list:
            await message.answer('По заданным паттернам не найдено ни одного уровня')
            return
        await send_result(message.chat.id, result)
    except Exception as e:
        logging.exception(f'Ошибка парсера статистики: {e}')
        await message.answer('Ошибка парсера статистики. Проверьте корректность ссылки и списка уровней.')


@dp.message(Command('csv'))
async def cmd_csv(message: types.Message, command: CommandObject):
    logging.info(command_info(message))

    input_args = command.args.split() if command.args else []
    if not input_args:
        await message.answer(f'Пример ввода:\n{example}', parse_mode='HTML')
        return
    my_url = input_args[0]

    await message.answer('Генерирую файл, подождите...')
    try:
        if STAT_SOURCE == 'json':
            json_data = await get_json(my_url)
            with_bonuses_text = generate_csv(json_data, True)
            without_bonuses_text = generate_csv(json_data, False)
        else:
            game_ref = parse_game_url(my_url)
            if not game_ref:
                await message.answer('Некорректная ссылка на игру')
                return
            my_domain, my_game_id = game_ref
            async with aiohttp.ClientSession(headers={"User-Agent": config.user_agent}, timeout=HTTP_TIMEOUT) as session:
                pages = await fetch_stat_pages(session, my_domain, my_game_id)
            with_bonuses_text = generate_csv_from_html(pages, True)
            without_bonuses_text = generate_csv_from_html(pages, False)

        buf_file = types.BufferedInputFile(bytes(with_bonuses_text, 'utf-8-sig'), filename='with_bonuses.csv')
        await message.answer_document(buf_file)

        buf_file = types.BufferedInputFile(bytes(without_bonuses_text, 'utf-8-sig'), filename='without_bonuses.csv')
        await message.answer_document(buf_file)
    except Exception as e:
        logging.exception(f'Ошибка генерации csv: {e}')
        await message.answer(f'Ошибка, возможно неверный формат ввода или некорректная статистика.\nПример ввода:\n{example}', parse_mode='HTML')


@dp.message(Command('rates'))
async def cmd_rates(message: types.Message, command: CommandObject):
    if RATES_LOCK.locked():
        await message.answer('Повторите Ваш запрос позже')
        return
    async with RATES_LOCK:
        logging.info(command_info(message))
        if not command.args:
            await message.answer(f'Пример: {example2}', parse_mode='HTML')
            return
        try:
            await message.answer('Получаю оценки...')
            marks = await get_rates(command.args.split()[0])
            if marks:
                await message.answer('<code>' + html.escape('\n'.join(marks)) + '</code>', parse_mode='HTML')
            else:
                await message.answer('Оценок нет')
        except Exception as e:
            logging.exception(f'Ошибка получения оценок: {e}')
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
    game_ref = parse_game_url(my_url)
    if not game_ref:
        await message.answer('Некорректная ссылка на игру')
        return
    my_domain, my_game_id = game_ref

    username = message.from_user.username

    async with aiohttp.ClientSession(headers={"User-Agent": config.user_agent}, timeout=HTTP_TIMEOUT) as my_session:
        auth_url = f'https://{my_domain}/Login.aspx'
        data = {'Login': config.en_username, 'Password': config.en_password.get_secret_value()}
        try:
            async with my_session.post(auth_url, data=data) as rs:
                rs.raise_for_status()
        except Exception as e:
            logging.exception(f'Ошибка авторизации: {e}')
            await message.answer('Ошибка авторизации')
            return

        if username not in config.admins:
            if not username:
                await message.answer(
                    'В вашем профиле Telegram не задан username — без него нельзя проверить '
                    'соответствие с профилем автора на сайте игры. Задайте username в настройках Telegram.'
                )
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
                        if not href:
                            continue
                        user_id_values = parse_qs(urlparse(href).query).get('uid')
                        if user_id_values:
                            authors_list.append(user_id_values[0])

                    if author_id not in authors_list:
                        await message.answer('Ваш id не находится в списке авторов игры')
                        return
                    if config.bot_en_id not in authors_list:
                        await message.answer(
                            f'enstatbot (https://world.en.cx/UserDetails.aspx?uid={config.bot_en_id}) '
                            f'не находится в списке авторов игры'
                        )
                        return
            except Exception as e:
                logging.exception(f'Ошибка проверки списка авторов игры: {e}')
                await message.answer('Ошибка проверки списка авторов игры')
                return

            # Проверка, что телеграм в профиле соответствует тому, кто обращается
            try:
                async with my_session.get(f'https://{my_domain}/UserDetails.aspx?uid={author_id}') as rs:
                    rs.raise_for_status()
                    soup = BeautifulSoup(await rs.text(), 'lxml')
                    tg_span_tag = soup.find(
                        'span', id='EnTabContainer1_content_ctl00_panelLineContacts_contactsBlock_JabberValue'
                    )
                    if not tg_span_tag:
                        await message.answer('У данного id не указан Telegram')
                        return
                    tg_contact = tg_span_tag.get_text()
                    if tg_contact.lower().replace('@', '') != username.lower():
                        await message.answer('Ваше имя в tg не соответствует tg указанного автора')
                        return
            except Exception as e:
                logging.exception(f'Ошибка проверки телеграма в en-профиле: {e}')
                await message.answer('Ошибка проверки телеграма в en-профиле')
                return

        try:
            levels_list, dismissed_levels_list = parse_level_nums(levels_text)
        except ValueError as e:
            await message.answer(f'Ошибка списка уровней: {e}')
            return

        info_str = (
            f'Пользователь tg @{username} с en id https://world.en.cx/UserDetails.aspx?uid={author_id} '
            f'считает закрытую статистику игры {my_url}'
        )
        logging.info(info_str)
        await bot.send_message(config.admin_chat_id, info_str)

        try:
            pages = await fetch_stat_pages(my_session, my_domain, my_game_id)
            result = parse_html_stat(pages, levels_list, dismissed_levels_list)
            await send_result(message.chat.id, result)
        except Exception as e:
            logging.exception(f'Ошибка парсера статистики: {e}')
            await message.answer('Ошибка парсера статистики')


@dp.message(F.document)
async def cmd_hstat_file(message: types.Message):
    MAX_FILE_SIZE_BYTES = 7 * 1024 * 1024
    document = message.document
    caption = message.caption

    if not caption:
        await message.reply("Подпись к файлу с номерами уровней не найдена")
        return

    try:
        levels_list, dismissed_levels_list = parse_level_nums(caption)
    except ValueError as e:
        await message.answer(f'Ошибка списка уровней: {e}')
        return

    if document.mime_type != 'text/html' and not document.file_name.endswith(('.html', '.htm')):
        await message.reply("Пожалуйста, загрузите действительный HTML файл.")
        return

    if document.file_size and document.file_size > MAX_FILE_SIZE_BYTES:
        await message.reply('Слишком большой файл')
        return

    await message.reply("Получаю Ваш HTML файл, пожалуйста, подождите...")

    try:
        html_buffer = io.BytesIO()
        file_info = await bot.get_file(document.file_id)
        logging.info(f'user {message.from_user.username} is loading stat file {document.file_name}')
        await bot.download_file(file_info.file_path, destination=html_buffer)
        html_buffer.seek(0)
        html_content_str = html_buffer.read().decode('utf-8', errors='replace')
        result = parse_html_stat([html_content_str], levels_list, dismissed_levels_list)
        await send_result(message.chat.id, result)
    except Exception as e:
        logging.exception(f'Ошибка обработки загруженного файла: {e}')
        await message.reply("Произошла ошибка")


async def send_result(chat_id: int, result) -> None:
    for entry in result:
        result_str = ''
        for line in entry:
            result_str += line + '\n'
            if len(result_str) > config.max_message_len:
                await bot.send_message(chat_id, '<code>' + html.escape(result_str) + '</code>', parse_mode='HTML')
                result_str = ''
        if result_str:
            await bot.send_message(chat_id, '<code>' + html.escape(result_str) + '</code>', parse_mode='HTML')


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())