Парсер статы для encounter. Пример использования:

`/stat https://dozorekb.en.cx/GameStat.aspx?gid=76109`

`/stat https://dozorekb.en.cx/GameStat.aspx?gid=76109 8 15 19 25 86 89-95 99`

`/textstat https://dozorekb.en.cx/GameStat.aspx?gid=76109 доезд`

`/csv https://dozorekb.en.cx/GameStat.aspx?gid=76109`

Для запуска нужно создать файл `.env`:
```
BOT_TOKEN = ВАШ_ТОКЕН_ПОЛУЧЕННЫЙ_У_BOTFATHER
MAX_MESSAGE_LEN = 3500
```

и запустить `main.py`