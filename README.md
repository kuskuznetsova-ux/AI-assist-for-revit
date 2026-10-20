# AI-assist-for-revit

AI-ассистент внутри Revit (2022+): чат-окно, команды на естественном языке,
выбор элементов, массовое редактирование параметров. Плагин на pyRevit (Python),
«мозг» — Gemini (бесплатный тариф Google AI Studio) или Claude (Anthropic API), через tool use.

## Установка для теста

1. Установить [pyRevit](https://github.com/pyrevitlabs/pyRevit/releases) (4.8+ или 5.x).
2. Склонировать репозиторий и подключить папку как источник расширений:
   ```
   git clone https://github.com/kuskuznetsova-ux/AI-assist-for-revit.git
   git checkout claude/affectionate-wright-4f2c4k
   pyrevit extensions paths add C:\путь\к\AI-assist-for-revit
   ```
   (или pyRevit → Settings → Custom Extension Directories → Add), затем **Reload**.
3. Получить ключ Gemini на https://aistudio.google.com (Get API key) и создать
   `%USERPROFILE%\.aiassist\config.json`:
   ```json
   { "provider": "gemini", "api_key": "ВАШ-КЛЮЧ" }
   ```
   Модель по умолчанию — `gemini-flash-latest`, меняется полем `"model"`.
   Для Claude: `{ "provider": "anthropic", "api_key": "sk-ant-..." }`.
   Ключ также можно задать переменной окружения `GEMINI_API_KEY` / `ANTHROPIC_API_KEY`.
   Ключ в репозиторий не коммитить.
4. Открыть проект в Revit → вкладка **AIAssist** → **AI-ассистент**.

Примеры запросов: «сколько в модели стен и дверей», «выбери все стены толще 200»,
«в выбранных стенах запиши в Комментарии "проверить"», «скрой кружки осей справа».

## Структура

- `AIAssist.extension/AIAssist.tab/.../Chat.pushbutton` — окно чата (WPF).
- `lib/aiassist/agent.py` — цикл «запрос → инструменты → ответ».
- `lib/aiassist/tools.py` — инструменты Revit для модели (чтение, выбор, запись параметров, кружки осей).
- `lib/aiassist/bridge.py` — выполнение вызовов API в главном потоке (ExternalEvent).
- `lib/aiassist/llm.py` — провайдеры Gemini и Anthropic с общим интерфейсом; `http.py` — HTTP через .NET.
- `~/.aiassist/recipes.md` — правила, которым пользователь «обучает» ассистента (добавляются в системный промпт).

## Безопасность

Инструменты записи показывают окно подтверждения и работают в одной транзакции
(откат через Ctrl+Z). Результаты инструментов (имена категорий, значения параметров)
отправляются выбранному провайдеру (Google или Anthropic).

## Планы

Докируемая панель, скриншот вида для модели, создание зон/помещений по контуру, больше инструментов.
