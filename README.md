# content-agents

Проект для автоматизации сбора и обработки контента из телеграм-каналов.

## Структура проекта

- `stage0_bezkromny_parser/` — парсер канала Бескромного: собирает ссылки из постов и считает статистику по доменам.
- `stage2_daily_agent/` — агент для ежедневной обработки контента.
- `shared/` — общий код и утилиты, используемые в разных стадиях проекта.

## Установка

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Запуск парсера

```bash
python stage0_bezkromny_parser/parse.py
```
