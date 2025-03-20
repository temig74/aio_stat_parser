import asyncio
import html
import logging
import sys

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandObject
from aiogram.filters.command import Command

from config_reader import config
from stat_parser2 import generate_csv, get_rate, parse_en_stat2

logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)])
bot = Bot(token=config.bot_token.get_secret_value())
dp = Dispatcher()
example = '''<code>/stat https://dozorekb.en.cx/GameStat.aspx?gid=76109</code>
<code>/stat https://dozorekb.en.cx/GameStat.aspx?gid=76109 8 15 19 25 86 89-95 99</code>
<code>/stat https://dozorekb.en.cx/GameStat.aspx?gid=76109 1-103 -22 -35 -52 -68 -78 -79 -80</code> (уровни 1-103, за исключением 22 35 52 68 78 79 80)
<code>/textstat https://dozorekb.en.cx/GameStat.aspx?gid=76109 доезд QRV</code> (только доезды и QRV)
<code>/textstat https://dozorekb.en.cx/GameStat.aspx?gid=76109 -доезд -QRV</code> (исключить доезды и QRV)
<code>/csv https://dozorekb.en.cx/GameStat.aspx?gid=76109</code>'''

example2 = '<code>/rates https://dozorekb.en.cx/GameDetails.aspx?gid=76109</code>'

def command_info(message: types.Message):
    return f'@{message.from_user.username} ({message.from_user.full_name or 'N/A'}) {message.chat.id}\n{message.text}'


@dp.message(Command(commands=['start', 'help']))
async def cmd_start(message: types.Message):
    logging.info(command_info(message))
    await message.answer(f'Temig stat parser\nПример:\n{example}\nИмейте в виду, бот не учитывает вручную начисленные бонусы, у которых не проставлен номер уровня. Также, некорректно считать штурмовую последовательность.\n\nУзнать все оценки за игру:\n{example2}', parse_mode='HTML')


@dp.message(Command(commands=['stat', 'textstat']))
async def cmd_stat(message: types.Message, command: CommandObject):
    logging.info(command_info(message))
    await message.answer('Считаю статистику, подождите...')
    try:
        if command.command == 'stat':
            result = parse_en_stat2(command.args.split()[0], command.args.split()[1:], 'by_nums')
        elif command.command == 'textstat':
            result = parse_en_stat2(command.args.split()[0], command.args.lower().split()[1:], 'by_text')
        for entry in result:
            result_str = ''
            for line in entry:
                result_str += line + '\n'
                if len(result_str) > config.max_message_len:
                    await message.answer('<code>'+html.escape(result_str)+'</code>', parse_mode='HTML')
                    result_str = ''
            await message.answer('<code>'+html.escape(result_str)+'</code>', parse_mode='HTML')
    except Exception as ex:
        logging.error(ex)
        await message.answer(f'Ошибка, возможно неверный формат ввода или некорректная статистика.\nПример ввода:\n{example}', parse_mode='HTML')


@dp.message(Command('csv'))
async def cmd_csv(message: types.Message, command: CommandObject):
    logging.info(command_info(message))
    await message.answer('Генерирую файл, подождите...')
    try:
        file_text = generate_csv(command.args.split()[0], True)
        buf_file = types.BufferedInputFile(bytes(file_text, 'utf-8-sig'), filename='with_bonuses.csv')
        await message.answer_document(buf_file)
        file_text = generate_csv(command.args.split()[0], False)
        buf_file = types.BufferedInputFile(bytes(file_text, 'utf-8-sig'), filename='without_bonuses.csv')
        await message.answer_document(buf_file)
    except Exception as ex:
        logging.error(ex)
        await message.answer(f'Ошибка, возможно неверный формат ввода или некорректная статистика.\nПример ввода:\n{example}', parse_mode='HTML')

@dp.message(Command('rates'))
async def cmd_marks(message: types.Message, command: CommandObject):
    logging.info(command_info(message))
    await message.answer('Получаю оценки...')
    try:
        marks = get_rate(command.args.split()[0])
        await message.answer('<code>' + html.escape('\n'.join(marks)) + '</code>', parse_mode='HTML')
    except Exception as ex:
        logging.error(ex)
        await message.answer('Ошибка :(', parse_mode='HTML')


async def main():
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
