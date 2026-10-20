# AI-assist-for-revit

AI-ассистент внутри Revit (2022+): чат-окно, команды на естественном языке,
выбор элементов, массовое редактирование параметров. Плагин на pyRevit (Python),
«мозг» — Claude через Anthropic API (tool use).

## Установка для теста

1. Установить [pyRevit](https://github.com/pyrevitlabs/pyRevit/releases) (4.8+ или 5.x).
2. Склонировать репозиторий и подключить папку как источник расширений:
   ```
   git clone https://github.com/kuskuznetsova-ux/AI-assist-for-revit.git
   git checkout claude/affectionate-wright-4f2c4k
   pyrevit extensions paths add C:\путь\к\AI-assist-for-revit
   ```
   (или pyRevit → Settings → Custom Extension Directories → Add), затем **Reload**.
3. Создать `%USERPROFILE%\.aiassist\config.json`:
   ```json
   { "api_key": "sk-ant-...", "model": "claude-sonnet-5-5" }
   ```
   Либо задать переменную окружения `ANTHROPIC_API_KEY`. Ключ в репозиторий не коммитить.
4. Открыть проект в Revit → вкладка **AIAssist** → **AI-ассистент**.

Примеры запросов: «сколько в модели стен и дверей», «выбери все стены толще 200»,
«в выбранных стенах запиши в Комментарии "проверить"», «скрой кружки осей справа».

## Структура

- `AIAssist.extension/AIAssist.tab/.../Chat.pushbutton` — окно чата (WPF).
- `lib/aiassist/agent.py` — цикл «запрос → инструменты → ответ».
- `lib/aiassist/tools.py` — инструменты Revit для модели (чтение, выбор, запись параметров, кружки осей).
- `lib/aiassist/bridge.py` — выполнение вызовов API в главном потоке (ExternalEvent).
- `lib/aiassist/llm.py` — клиент Anthropic API (.NET HttpWebRequest).
- `~/.aiassist/recipes.md` — правила, которым пользователь «обучает» ассистента (добавляются в системный промпт).

## Безопасность

Инструменты записи показывают окно подтверждения и работают в одной транзакции
(откат через Ctrl+Z). Результаты инструментов (имена категорий, значения параметров)
отправляются в Anthropic API.

## Планы

Докируемая панель, скриншот вида для модели, создание зон/помещений по контуру, больше инструментов.
