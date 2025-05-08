import Adafruit_DHT
import asyncio
import aiohttp
from aiohttp import web
import requests
import json
from datetime import datetime, timedelta
from telegram.ext import Updater, CommandHandler
import logging
from logging.handlers import RotatingFileHandler

# Налаштування датчика DHT22
DHT_SENSOR = Adafruit_DHT.DHT22
DHT_PIN = 4  # GPIO4 - пін для підключення датчика

# Налаштування API погоди
WEATHER_API_KEY = "YOUR_OPENWEATHERMAP_API_KEY"
CITY = "YOUR_CITY"
WEATHER_URL = f"http://api.openweathermap.org/data/2.5/weather?q={CITY}&appid={WEATHER_API_KEY}&units=metric&lang=en"  # URL для отримання погоди

# Налаштування Telegram бота
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"

# Налаштування логування
LOG_FILE = "logs/climateguard.log"
LOG_MAX_BYTES = 1024 * 1024  # Максимальний розмір лог-файлу: 1 MB
LOG_BACKUP_COUNT = 5  # Кількість бекапів логів
logger = logging.getLogger(__name__)  # Ініціалізація логера
logger.setLevel(logging.DEBUG)  # Встановлення рівня деталізації логів
handler = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)  # Налаштування ротації логів
formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] [%(name)s] - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')  # Форматування записів
handler.setFormatter(formatter)
logger.addHandler(handler)

# Глобальні змінні для зберігання даних
esp_data = {"temperature": None, "humidity": None, "last_update": None}  # Дані від ESP8266
rpi_data = {"temperature": None, "humidity": None, "last_update": None}  # Дані від Raspberry Pi (DHT22)
avg_data = {"temperature": None, "humidity": None, "temp_error": None, "hum_error": None, "last_update": None}  # Середні значення та похибки
weather_data = "N/A"  # Дані погоди, за замовчуванням "N/A"
weather_last_update = None  # Час останнього оновлення погоди
last_esp_check = datetime.now()  # Час останньої перевірки ESP8266
first_data_access = True  # Прапорець для першого доступу до даних

# Похибки датчиків
DHT11_TEMP_ERROR = 2.0  # ±2°C для DHT11
DHT11_HUM_ERROR = 5.0   # ±5% для DHT11
DHT22_TEMP_ERROR = 0.5  # ±0.5°C для DHT22
DHT22_HUM_ERROR = 2.0   # ±2% для DHT22

# Читання даних із датчика DHT22
async def read_dht22():
    # Спроба зчитати температуру та вологість із датчика
    humidity, temperature = Adafruit_DHT.read_retry(DHT_SENSOR, DHT_PIN)
    if temperature is not None and humidity is not None:
        rpi_data["temperature"] = temperature  # Збереження температури
        rpi_data["humidity"] = humidity  # Збереження вологості
        rpi_data["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # Оновлення часу
        logger.info(f"DHT22 sensor data retrieved successfully: temperature={temperature:.1f}°C, humidity={humidity:.1f}%")
    else:
        rpi_data["temperature"] = None  # Скидання даних при помилці
        rpi_data["humidity"] = None
        rpi_data["last_update"] = None
        logger.warning("Failed to retrieve data from DHT22 sensor")

# Отримання даних погоди з OpenWeatherMap
async def fetch_weather():
    global weather_data, weather_last_update
    try:
        response = requests.get(WEATHER_URL)  # Запит до API погоди
        response.raise_for_status()  # Перевірка на помилки HTTP
        data = response.json()
        if data["cod"] == 200:
            description = data["weather"][0]["description"]
            temp = data["main"]["temp"]
            weather_data = f"{description} {temp:.1f}°C"  # Форматування даних погоди
            weather_last_update = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"Weather data retrieved from OpenWeatherMap: {weather_data}")
        else:
            weather_data = "Weather unavailable"
            weather_last_update = None
            logger.warning(f"Weather API request failed with status code {data['cod']}: {data.get('message', 'No message')}")
    except Exception as e:
        weather_data = "Weather unavailable"
        weather_last_update = None
        logger.error(f"Error fetching weather data from OpenWeatherMap: {str(e)}")

# Обчислення середніх значень та похибок
async def calculate_averages():
    if esp_data["temperature"] is None or rpi_data["temperature"] is None:
        avg_data["temperature"] = None  # Скидання середнього при відсутності даних
        avg_data["humidity"] = None
        avg_data["temp_error"] = None
        avg_data["hum_error"] = None
        avg_data["last_update"] = None
        logger.warning("Cannot calculate averages due to missing data from ESP8266 or DHT22")
        if esp_data["temperature"] is None:
            logger.warning("ESP8266 data unavailable, possible disconnection")
        return

    # Обчислення середнього значення температури та вологості
    avg_data["temperature"] = (esp_data["temperature"] + rpi_data["temperature"]) / 2
    avg_data["humidity"] = (esp_data["humidity"] + rpi_data["humidity"]) / 2

    # Обчислення комбінованої похибки (RMS)
    avg_data["temp_error"] = ((DHT11_TEMP_ERROR**2 + DHT22_TEMP_ERROR**2) ** 0.5) / 2
    avg_data["hum_error"] = ((DHT11_HUM_ERROR**2 + DHT22_HUM_ERROR**2) ** 0.5) / 2
    avg_data["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"Averages calculated: temperature={avg_data['temperature']:.1f}±{avg_data['temp_error']:.1f}°C, humidity={avg_data['humidity']:.1f}±{avg_data['hum_error']:.1f}%")

# Обробка даних від ESP8266
async def handle_esp_update(request):
    global esp_data, last_esp_check
    try:
        data = await request.json()  # Отримання JSON-даних від ESP8266
        esp_data["temperature"] = data["temperature"]  # Оновлення температури
        esp_data["humidity"] = data["humidity"]  # Оновлення вологості
        esp_data["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # Оновлення часу
        last_esp_check = datetime.now()
        logger.info(f"ESP8266 data received: temperature={data['temperature']:.1f}°C, humidity={data['humidity']:.1f}%")

        # Оновлення середніх значень
        await calculate_averages()

        # Відправка відповіді ESP8266
        response = {
            "avgTemperature": avg_data["temperature"],
            "avgHumidity": avg_data["humidity"],
            "weather": weather_data
        }
        return web.json_response(response)
    except Exception as e:
        logger.error(f"Error processing ESP8266 update: {str(e)}")
        return web.Response(status=500)

# Обробка запиту на favicon
async def handle_favicon(request):
    logger.debug("Favicon request received")
    return web.Response(status=204)  # Повернення порожньої відповіді

# API-ендпоінт для отримання даних
async def get_data(request):
    global first_data_access
    if first_data_access:
        logger.info("Data endpoint accessed for the first time")
        first_data_access = False
    return web.json_response({
        "weather": weather_data,
        "weather_last_update": weather_last_update,
        "esp_temp": esp_data["temperature"],
        "esp_hum": esp_data["humidity"],
        "esp_last_update": esp_data["last_update"],
        "rpi_temp": rpi_data["temperature"],
        "rpi_hum": rpi_data["humidity"],
        "rpi_last_update": rpi_data["last_update"],
        "avg_temp": avg_data["temperature"],
        "avg_hum": avg_data["humidity"],
        "temp_error": avg_data["temp_error"],
        "hum_error": avg_data["hum_error"],
        "avg_last_update": avg_data["last_update"]
    })

# Обробка запиту на styles.css
async def handle_styles(request):
    logger.debug("Styles.css file requested")
    with open("styles.css", "r") as f:
        return web.Response(text=f.read(), content_type="text/css")

# Обробка запиту на веб-інтерфейс
async def handle_web(request):
    logger.debug("Web interface requested")
    with open("index.html", "r") as f:
        return web.Response(text=f.read(), content_type="text/html")

# Обробка команди /start Telegram бота
def start(update, context):
    logger.info("Telegram bot received /start command")
    update.message.reply_text(
        "Welcome to ClimateGuard Bot!\n"
        "Your friendly companion for real-time climate monitoring!\n"
        "Stay updated with temperature, humidity, and weather forecasts from Chernihiv!\n"
        "Click the buttons below to explore the environment around you!\n\n"
        "Available Commands:\n"
        "/weather - Check the current weather\n"
        "/average - View averaged sensor data\n"
        "/esp - Get ESP8266 (DHT11) readings\n"
        "/rpi - Get Raspberry Pi (DHT22) readings"
    )

# Обробка команди /weather Telegram бота
def weather(update, context):
    logger.info("Telegram bot received /weather command")
    update.message.reply_text(
        f"Weather in Chernihiv:\n"
        f"{weather_data}\n"
        f"Last updated: {weather_last_update or 'N/A'}"
    )

# Обробка команди /average Telegram бота
def average(update, context):
    logger.info("Telegram bot received /average command")
    if avg_data["temperature"] is None:
        update.message.reply_text("Average data unavailable - ESP8266 might be offline!")
    else:
        update.message.reply_text(
            f"Average Climate Data:\n"
            f"Temperature: {avg_data['temperature']:.1f} ± {avg_data['temp_error']:.1f} °C\n"
            f"Humidity: {avg_data['humidity']:.1f} ± {avg_data['hum_error']:.1f} %\n"
            f"Last updated: {avg_data['last_update']}"
        )

# Обробка команди /esp Telegram бота
def esp(update, context):
    logger.info("Telegram bot received /esp command")
    if esp_data["temperature"] is None:
        update.message.reply_text("ESP8266 offline - Check the connection!")
    else:
        update.message.reply_text(
            f"ESP8266 (DHT11) Data:\n"
            f"Temperature: {esp_data['temperature']:.1f} °C\n"
            f"Humidity: {esp_data['humidity']:.1f} %\n"
            f"Last updated: {esp_data['last_update']}"
        )

# Обробка команди /rpi Telegram бота
def rpi(update, context):
    logger.info("Telegram bot received /rpi command")
    update.message.reply_text(
        f"Raspberry Pi (DHT22) Data:\n"
        f"Temperature: {rpi_data['temperature']:.1f} °C\n"
        f"Humidity: {rpi_data['humidity']:.1f} %\n"
        f"Last updated: {rpi_data['last_update'] or 'N/A'}"
    )

# Регулярна перевірка статусу ESP8266
async def check_esp_status(updater):
    global esp_data, last_esp_check
    while True:
        await asyncio.sleep(60)  # Перевірка кожні 60 секунд
        current_time = datetime.now()
        if esp_data["last_update"] is None or (current_time - datetime.strptime(esp_data["last_update"], "%Y-%m-%d %H:%M:%S")) > timedelta(minutes=1):
            logger.warning("ESP8266 not responding, resetting data to None")
            esp_data["temperature"] = None  # Скидання температури при відсутності оновлень
            esp_data["humidity"] = None  # Скидання вологості
            esp_data["last_update"] = None  # Скидання часу оновлення
            updater.bot.send_message(chat_id=CHAT_ID, text="ClimateGuard Alert: ESP8266 not responding! Check the connection!")
            last_esp_check = current_time
        else:
            logger.debug("ESP8266 status check successful")

# Основний цикл програми
async def main():
    logger.info("Starting ClimateGuard application")
    # Налаштування веб-сервера
    app = web.Application()
    app.router.add_post('/update', handle_esp_update)
    app.router.add_get('/', handle_web)
    app.router.add_get('/data', get_data)
    app.router.add_get('/styles.css', handle_styles)
    app.router.add_get('/favicon.ico', handle_favicon)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 80)
    await site.start()
    logger.info("Web server started on 0.0.0.0:80")

    # Налаштування Telegram бота
    logger.info("Starting ClimateGuard Telegram Bot")
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("weather", weather))
    dp.add_handler(CommandHandler("average", average))
    dp.add_handler(CommandHandler("esp", esp))
    dp.add_handler(CommandHandler("rpi", rpi))
    updater.start_polling()
    logger.info("ClimateGuard Telegram Bot polling started")

    # Запуск перевірки статусу ESP8266
    asyncio.create_task(check_esp_status(updater))

    # Головний цикл оновлення
    while True:
        await read_dht22()  # Зчитування даних із DHT22
        await fetch_weather()  # Отримання даних погоди
        await calculate_averages()  # Обчислення середніх значень
        await asyncio.sleep(10)  # Оновлення кожні 10 секунд

if __name__ == "__main__":
    asyncio.run(main())  # Запуск асинхронного циклу