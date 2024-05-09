import asyncio
import datetime
import logging
from aiogram import Bot, Dispatcher, types  # pip install aiogram
from aiogram.filters.command import Command
from config_reader import config
from aiogram.filters import CommandObject
import html
from stat_parser2 import parse_en_stat2


logging.basicConfig(level=logging.INFO)
bot = Bot(token=config.bot_token.get_secret_value())
dp = Dispatcher()
example = '<code>/stat https://dozorekb.en.cx/GameStat.aspx?gid=76109</code>\n<code>/stat https://dozorekb.en.cx/GameStat.aspx?gid=76109 8 15 19 25 86 89-95 99</code>'

@dp.message(Command(commands=['start', 'help']))
async def cmd_start(message: types.Message):
    print(f'{message.from_user.username} {message.from_user.first_name} {message.from_user.last_name} {message.chat.id} \n {message.text}')
    await message.answer(f'Temig stat parser\nПример:\n{example}', parse_mode='HTML')


@dp.message(Command('stat'))
async def cmd_stat(message: types.Message, command: CommandObject):
    print(f'{message.from_user.username} {message.from_user.first_name} {message.from_user.last_name} {message.chat.id} \n {message.text}')
    starttime = datetime.datetime.now()
    await message.answer('Считаю статистику, подождите...')
    try:
        level_nums = []
        for elem in command.args.split()[1:]:
            if '-' in elem:
                for i in range(int(elem.split('-')[0]), int(elem.split('-')[1])+1):
                    level_nums.append(i)
            else:
                level_nums.append(int(elem))
        result = parse_en_stat2(command.args.split()[0], level_nums)

        for entry in result:
            result_str = ''
            for line in entry:
                result_str += line + '\n'
                if len(result_str) > config.max_message_len:
                    await message.answer('<code>'+html.escape(result_str)+'</code>', parse_mode='HTML')
                    result_str = ''
            await message.answer('<code>'+html.escape(result_str)+'</code>', parse_mode='HTML')
    except:
        await message.answer(f'Ошибка, возможно неверный формат ввода или некорректная статистика.\nПример ввода:\n{example}', parse_mode='HTML')
    print(f'aiogram обработал за {datetime.datetime.now() - starttime}')


async def main():
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
