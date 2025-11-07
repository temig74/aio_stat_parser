Парсер статы для encounter. Пример использования:

`/stat https://dozorekb.en.cx/GameStat.aspx?gid=76109`

`/stat https://dozorekb.en.cx/GameStat.aspx?gid=76109 8 15 19 25 86 89-95 99`

`/textstat https://dozorekb.en.cx/GameStat.aspx?gid=76109 доезд`

`/csv https://dozorekb.en.cx/GameStat.aspx?gid=76109`

`/rates https://dozorekb.en.cx/GameDetails.aspx?gid=76109`

`/hstat https://dozorekb.en.cx/GameStat.aspx?gid=76109 ваш_id_в_энке 1-103 -22 -35 -52 -68 -78 -79 -80`

- Также, можете загрузить html файл с сохраненной страницей статистики, добавив в подпись номера уровней, по которым надо посчитать стату (аналочно команде stat)''', parse_mode='HTML')

Имейте в виду, бот не учитывает вручную начисленные бонусы, у которых не проставлен номер уровня. Также, некорректно считать штурмовую последовательность.
Для запуска нужно создать файл `.env`:
```
BOT_TOKEN = ВАШ_ТОКЕН_ПОЛУЧЕННЫЙ_У_BOTFATHER
MAX_MESSAGE_LEN = 3500
EN_USERNAME = логин_бота_для_закрытой_статы
EN_PASSWORD = пароль_бота
USER_AGENT = Temig Stat Parser
ADMIN_CHAT_ID = id_чата_для_уведомлений
BOT_EN_ID = en_id_бота
```

и запустить `main.py`