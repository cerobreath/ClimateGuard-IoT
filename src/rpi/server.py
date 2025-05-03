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

# DHT22 settings
DHT_SENSOR = Adafruit_DHT.DHT22
DHT_PIN = 4  # GPIO4

# Weather API settings
WEATHER_API_KEY = "YOUR_OPENWEATHERMAP_API_KEY"
CITY = "YOUR_CITY"
WEATHER_URL = f"http://api.openweathermap.org/data/2.5/weather?q={CITY}&appid={WEATHER_API_KEY}&units=metric&lang=en"

# Telegram bot settings
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"

# Logging setup
LOG_FILE = "logs/climateguard.log"
LOG_MAX_BYTES = 1024 * 1024  # 1 MB
LOG_BACKUP_COUNT = 5  # Keep 5 backup files
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
handler = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] [%(name)s] - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
handler.setFormatter(formatter)
logger.addHandler(handler)

# Global variables
esp_data = {"temperature": None, "humidity": None, "last_update": None}
rpi_data = {"temperature": None, "humidity": None, "last_update": None}
avg_data = {"temperature": None, "humidity": None, "temp_error": None, "hum_error": None, "last_update": None}
weather_data = "N/A"
weather_last_update = None
last_esp_check = datetime.now()
first_data_access = True

# Error margins for sensors
DHT11_TEMP_ERROR = 2.0  # ±2°C for DHT11
DHT11_HUM_ERROR = 5.0   # ±5% for DHT11
DHT22_TEMP_ERROR = 0.5  # ±0.5°C for DHT22
DHT22_HUM_ERROR = 2.0   # ±2% for DHT22

# Read DHT22 sensor
async def read_dht22():
    humidity, temperature = Adafruit_DHT.read_retry(DHT_SENSOR, DHT_PIN)
    if temperature is not None and humidity is not None:
        rpi_data["temperature"] = temperature
        rpi_data["humidity"] = humidity
        rpi_data["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"🌡️ DHT22 read successfully - Temp: {temperature:.1f}°C, Hum: {humidity:.1f}%")
    else:
        rpi_data["temperature"] = None
        rpi_data["humidity"] = None
        rpi_data["last_update"] = None
        logger.warning("🌡️ Failed to read DHT22 sensor data")

# Fetch weather data
async def fetch_weather():
    global weather_data, weather_last_update
    try:
        response = requests.get(WEATHER_URL)
        response.raise_for_status()  # Raise an error for bad status codes
        data = response.json()
        if data["cod"] == 200:
            description = data["weather"][0]["description"]
            temp = data["main"]["temp"]
            weather_data = f"{description} {temp:.1f}°C"
            weather_last_update = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"🌦️ Weather fetched - {weather_data}")
        else:
            weather_data = "Weather unavailable"
            weather_last_update = None
            logger.warning(f"🌦️ Weather API returned code {data['cod']} - {data.get('message', 'No message')}")
    except Exception as e:
        weather_data = "Weather unavailable"
        weather_last_update = None
        logger.error(f"🌦️ Weather fetch error: {e}")

# Calculate averages and errors
async def calculate_averages():
    if esp_data["temperature"] is None or rpi_data["temperature"] is None:
        avg_data["temperature"] = None
        avg_data["humidity"] = None
        avg_data["temp_error"] = None
        avg_data["hum_error"] = None
        avg_data["last_update"] = None
        logger.warning("📊 Cannot calculate averages - Missing data from ESP8266 or DHT22")
        if esp_data["temperature"] is None:
            logger.warning("📡 ESP8266 data is unavailable - Possible disconnection")
        return

    # Average temperature and humidity
    avg_data["temperature"] = (esp_data["temperature"] + rpi_data["temperature"]) / 2
    avg_data["humidity"] = (esp_data["humidity"] + rpi_data["humidity"]) / 2

    # Combined error (root mean square of individual errors)
    avg_data["temp_error"] = ((DHT11_TEMP_ERROR**2 + DHT22_TEMP_ERROR**2) ** 0.5) / 2
    avg_data["hum_error"] = ((DHT11_HUM_ERROR**2 + DHT22_HUM_ERROR**2) ** 0.5) / 2
    avg_data["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"📊 Averages calculated - Temp: {avg_data['temperature']:.1f}±{avg_data['temp_error']:.1f}°C, Hum: {avg_data['humidity']:.1f}±{avg_data['hum_error']:.1f}%")

# Handle ESP8266 data
async def handle_esp_update(request):
    global esp_data, last_esp_check
    try:
        data = await request.json()
        esp_data["temperature"] = data["temperature"]
        esp_data["humidity"] = data["humidity"]
        esp_data["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        last_esp_check = datetime.now()
        logger.info(f"📡 ESP8266 data received - Temp: {data['temperature']:.1f}°C, Hum: {data['humidity']:.1f}%")

        # Update averages
        await calculate_averages()

        # Response to ESP8266
        response = {
            "avgTemperature": avg_data["temperature"],
            "avgHumidity": avg_data["humidity"],
            "weather": weather_data
        }
        return web.json_response(response)
    except Exception as e:
        logger.error(f"📡 ESP update error: {e}")
        return web.Response(status=500)

# Handle favicon request
async def handle_favicon(request):
    logger.debug("🔍 Favicon request received")
    return web.Response(status=204)  # No content

# API endpoint for data
async def get_data(request):
    global first_data_access
    if first_data_access:
        logger.info("📋 Data endpoint accessed for the first time")
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

# Serve styles.css
async def handle_styles(request):
    logger.debug("🎨 Styles.css requested")
    with open("styles.css", "r") as f:
        return web.Response(text=f.read(), content_type="text/css")

# Web interface
async def handle_web(request):
    logger.debug("🌐 Web interface requested")
    with open("index.html", "r") as f:
        return web.Response(text=f.read(), content_type="text/html")

# Telegram bot handlers
def start(update, context):
    logger.info("🤖 Telegram bot /start command received")
    update.message.reply_text(
        "🌱 Welcome to ClimateGuard Bot! 🌡️\n"
        "Your friendly companion for real-time climate monitoring! 🌦️\n"
        "Stay updated with temperature, humidity, and weather forecasts from Chernihiv! 🤖\n"
        "Click the buttons below to explore the environment around you! 🌍\n\n"
        "📌 Available Commands:\n"
        "🌡️ /weather - Check the current weather\n"
        "📊 /average - View averaged sensor data\n"
        "📡 /esp - Get ESP8266 (DHT11) readings\n"
        "🌐 /rpi - Get Raspberry Pi (DHT22) readings"
    )

def weather(update, context):
    logger.info("🌦️ Telegram bot /weather command received")
    update.message.reply_text(
        f"🌤️ Weather in Chernihiv:\n"
        f"🌡️ {weather_data}\n"
        f"⏰ Last updated: {weather_last_update or 'N/A'}"
    )

def average(update, context):
    logger.info("📊 Telegram bot /average command received")
    if avg_data["temperature"] is None:
        update.message.reply_text("📡 Average data unavailable - ESP8266 might be offline! ⚠️")
    else:
        update.message.reply_text(
            f"📈 Average Climate Data:\n"
            f"🌡️ Temperature: {avg_data['temperature']:.1f} ± {avg_data['temp_error']:.1f} °C\n"
            f"💧 Humidity: {avg_data['humidity']:.1f} ± {avg_data['hum_error']:.1f} %\n"
            f"⏰ Last updated: {avg_data['last_update']}"
        )

def esp(update, context):
    logger.info("📡 Telegram bot /esp command received")
    if esp_data["temperature"] is None:
        update.message.reply_text("📡 ESP8266 offline - Check the connection! ⚠️")
    else:
        update.message.reply_text(
            f"📡 ESP8266 (DHT11) Data:\n"
            f"🌡️ Temperature: {esp_data['temperature']:.1f} °C\n"
            f"💧 Humidity: {esp_data['humidity']:.1f} %\n"
            f"⏰ Last updated: {esp_data['last_update']}"
        )

def rpi(update, context):
    logger.info("🌐 Telegram bot /rpi command received")
    update.message.reply_text(
        f"🌐 Raspberry Pi (DHT22) Data:\n"
        f"🌡️ Temperature: {rpi_data['temperature']:.1f} °C\n"
        f"💧 Humidity: {rpi_data['humidity']:.1f} %\n"
        f"⏰ Last updated: {rpi_data['last_update'] or 'N/A'}"
    )

# Check ESP8266 status periodically
async def check_esp_status(updater):
    global last_esp_check
    while True:
        await asyncio.sleep(600)  # Every 10 minutes
        if esp_data["last_update"] is None or (datetime.now() - datetime.strptime(esp_data["last_update"], "%Y-%m-%d %H:%M:%S")) > timedelta(minutes=1):
            logger.warning("📡 ESP8266 not responding")
            updater.bot.send_message(chat_id=CHAT_ID, text="⚠️ ClimateGuard Alert: 📡 ESP8266 not responding! Check the connection!")
            last_esp_check = datetime.now()
        else:
            logger.debug("📡 ESP8266 status check - OK")

# Main loop
async def main():
    logger.info("🌱 Starting ClimateGuard application")
    # Setup web server
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
    logger.info("🌐 Web server started on 0.0.0.0:80")

    # Setup Telegram bot
    logger.info("🤖 Starting ClimateGuard Bot")
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("weather", weather))
    dp.add_handler(CommandHandler("average", average))
    dp.add_handler(CommandHandler("esp", esp))
    dp.add_handler(CommandHandler("rpi", rpi))
    updater.start_polling()
    logger.info("🤖 ClimateGuard Bot polling started")

    # Start ESP status check
    asyncio.create_task(check_esp_status(updater))

    # Main loop
    while True:
        await read_dht22()
        await fetch_weather()
        await calculate_averages()
        await asyncio.sleep(10)  # Update every 10 seconds

if __name__ == "__main__":
    asyncio.run(main())