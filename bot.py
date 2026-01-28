import os
import json
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import anthropic
import httpx
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Переменные окружения
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
NOTION_TOKEN = os.environ.get('NOTION_TOKEN')
NOTION_DATABASE_ID = os.environ.get('NOTION_DATABASE_ID')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
ALLOWED_USER_ID = os.environ.get('ALLOWED_USER_ID')  # Опционально: ограничить доступ

# Инициализация клиента Anthropic
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Маппинг языков для Notion
LANGUAGE_MAP = {
    'english': 'Английский 🇬🇧',
    'latin': 'Латынь 🏛️',
    'french': 'Французский 🇫🇷',
    'английский': 'Английский 🇬🇧',
    'латынь': 'Латынь 🏛️',
    'французский': 'Французский 🇫🇷',
}


async def analyze_word_with_claude(word: str) -> dict:
    """Анализирует слово с помощью Claude и возвращает всю информацию."""

    prompt = f"""Проанализируй слово или выражение: "{word}"

Определи:
1. Язык слова (english, french, или latin)
2. Перевод на русский язык
3. Транскрипцию (IPA в квадратных скобках)
4. Пример использования (предложение на языке оригинала)
5. Тематику (выбери одну или несколько из: Еда, Путешествия, Работа, Наука, Культура, Повседневное)

Ответь ТОЛЬКО в формате JSON без markdown:
{{"language": "english/french/latin", "translation": "перевод", "transcription": "[транскрипция]", "example": "пример предложения", "themes": ["тема1", "тема2"]}}"""

    message = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )

    response_text = message.content[0].text.strip()

    # Парсим JSON из ответа
    try:
        result = json.loads(response_text)
        return result
    except json.JSONDecodeError:
        # Пробуем извлечь JSON из ответа
        import re
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        raise ValueError(f"Не удалось распарсить ответ Claude: {response_text}")


async def add_to_notion(word: str, data: dict) -> str:
    """Добавляет слово в базу данных Notion."""

    url = "https://api.notion.com/v1/pages"

    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    # Маппинг языка
    language = LANGUAGE_MAP.get(data['language'].lower(), 'Английский 🇬🇧')

    # Формируем тематики для multi_select
    themes = []
    for theme in data.get('themes', ['Повседневное']):
        themes.append({"name": theme})

    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Слово": {
                "title": [{"text": {"content": word}}]
            },
            "Перевод": {
                "rich_text": [{"text": {"content": data['translation']}}]
            },
            "Язык": {
                "select": {"name": language}
            },
            "Транскрипция": {
                "rich_text": [{"text": {"content": data.get('transcription', '')}}]
            },
            "Пример": {
                "rich_text": [{"text": {"content": data.get('example', '')}}]
            },
            "Тематика": {
                "multi_select": themes
            },
            "Дата добавления": {
                "date": {"start": datetime.now().strftime("%Y-%m-%d")}
            }
        }
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)

        if response.status_code == 200:
            page_data = response.json()
            return page_data.get('url', 'Успешно добавлено!')
        else:
            error_msg = response.text
            logger.error(f"Notion API error: {error_msg}")
            raise Exception(f"Ошибка Notion API: {response.status_code}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start."""
    await update.message.reply_text(
        "👋 Привет! Я бот для твоего личного словаря.\n\n"
        "Просто отправь мне слово на английском, французском или латыни, "
        "и я добавлю его в твой Notion-словарь с переводом, транскрипцией и примером.\n\n"
        "Например: serendipity\n\n"
        "Команды:\n"
        "/start — это сообщение\n"
        "/help — помощь"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /help."""
    await update.message.reply_text(
        "📚 Как пользоваться:\n\n"
        "1. Отправь слово или выражение\n"
        "2. Я определю язык, найду перевод, транскрипцию и составлю пример\n"
        "3. Добавлю всё в твой Notion-словарь\n\n"
        "Поддерживаемые языки: английский, французский, латынь"
    )


async def process_word(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает отправленное слово."""

    # Проверка доступа (опционально)
    if ALLOWED_USER_ID:
        if str(update.effective_user.id) != ALLOWED_USER_ID:
            await update.message.reply_text("⛔ У вас нет доступа к этому боту.")
            return

    word = update.message.text.strip()

    if not word or len(word) > 100:
        await update.message.reply_text("❌ Отправь слово или короткое выражение.")
        return

    # Отправляем статус
    status_message = await update.message.reply_text(f"🔍 Анализирую слово «{word}»...")

    try:
        # Анализируем слово через Claude
        data = await analyze_word_with_claude(word)

        await status_message.edit_text(f"📝 Добавляю в словарь...")

        # Добавляем в Notion
        notion_url = await add_to_notion(word, data)

        # Формируем красивый ответ
        language_emoji = {
            'english': '🇬🇧',
            'french': '🇫🇷',
            'latin': '🏛️'
        }.get(data['language'].lower(), '🌍')

        response = (
            f"✅ Добавлено в словарь!\n\n"
            f"{language_emoji} **{word}**\n"
            f"📖 {data['translation']}\n"
            f"🔊 {data.get('transcription', '—')}\n"
            f"💬 _{data.get('example', '—')}_\n"
            f"🏷 {', '.join(data.get('themes', []))}"
        )

        await status_message.edit_text(response, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error processing word '{word}': {e}")
        await status_message.edit_text(
            f"❌ Ошибка при обработке слова.\n\nПодробности: {str(e)[:200]}"
        )


def main() -> None:
    """Запуск бота."""

    # Проверяем наличие необходимых переменных
    required_vars = ['TELEGRAM_TOKEN', 'NOTION_TOKEN', 'NOTION_DATABASE_ID', 'ANTHROPIC_API_KEY']
    missing = [var for var in required_vars if not os.environ.get(var)]

    if missing:
        logger.error(f"Missing environment variables: {', '.join(missing)}")
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    # Создаём приложение
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_word))

    # Запускаем бота
    logger.info("Bot started!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
