import Adafruit_DHT
import asyncio
import aiohttp
from aiohttp import web
import requests
import json
from datetime import datetime, timedelta
from telegram.ext import Updater, CommandHandler
import logging

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

# Global variables
esp_data = {"temperature": None, "humidity": None, "last_update": None}
rpi_data = {"temperature": None, "humidity": None}
avg_data = {"temperature": None, "humidity": None, "temp_error": None, "hum_error": None}
weather_data = "N/A"
last_esp_check = datetime.now()

# Error margins for sensors
DHT11_TEMP_ERROR = 2.0  # ±2°C for DHT11
DHT11_HUM_ERROR = 5.0   # ±5% for DHT11
DHT22_TEMP_ERROR = 0.5  # ±0.5°C for DHT22
DHT22_HUM_ERROR = 2.0   # ±2% for DHT22

# Setup logging for Telegram bot
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Read DHT22 sensor
async def read_dht22():
    humidity, temperature = Adafruit_DHT.read_retry(DHT_SENSOR, DHT_PIN)
    if temperature is not None and humidity is not None:
        rpi_data["temperature"] = temperature
        rpi_data["humidity"] = humidity
    else:
        rpi_data["temperature"] = None
        rpi_data["humidity"] = None

# Fetch weather data
async def fetch_weather():
    global weather_data
    try:
        response = requests.get(WEATHER_URL)
        data = response.json()
        weather_data = f"{data['weather'][0]['description'].capitalize()}, {data['main']['temp']}°C"
    except Exception as e:
        weather_data = "Weather unavailable"
        logger.error(f"Weather fetch error: {e}")

# Calculate averages and errors
async def calculate_averages():
    if esp_data["temperature"] is None or rpi_data["temperature"] is None:
        avg_data["temperature"] = None
        avg_data["humidity"] = None
        avg_data["temp_error"] = None
        avg_data["hum_error"] = None
        return

    # Average temperature and humidity
    avg_data["temperature"] = (esp_data["temperature"] + rpi_data["temperature"]) / 2
    avg_data["humidity"] = (esp_data["humidity"] + rpi_data["humidity"]) / 2

    # Combined error (root mean square of individual errors)
    avg_data["temp_error"] = ((DHT11_TEMP_ERROR**2 + DHT22_TEMP_ERROR**2) ** 0.5) / 2
    avg_data["hum_error"] = ((DHT11_HUM_ERROR**2 + DHT22_HUM_ERROR**2) ** 0.5) / 2

# Handle ESP8266 data
async def handle_esp_update(request):
    global esp_data, last_esp_check
    try:
        data = await request.json()
        esp_data["temperature"] = data["temperature"]
        esp_data["humidity"] = data["humidity"]
        esp_data["last_update"] = datetime.now()
        last_esp_check = datetime.now()

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
        logger.error(f"ESP update error: {e}")
        return web.Response(status=500)

# Handle favicon request
async def handle_favicon(request):
    return web.Response(status=204)  # No content

# API endpoint for data
async def get_data(request):
    return web.json_response({
        "weather": weather_data,
        "esp_temp": esp_data["temperature"],
        "esp_hum": esp_data["humidity"],
        "rpi_temp": rpi_data["temperature"],
        "rpi_hum": rpi_data["humidity"],
        "avg_temp": avg_data["temperature"],
        "avg_hum": avg_data["humidity"],
        "temp_error": avg_data["temp_error"],
        "hum_error": avg_data["hum_error"]
    })

# Serve styles.css
async def handle_styles(request):
    with open("styles.css", "r") as f:
        return web.Response(text=f.read(), content_type="text/css")

# Web interface
async def handle_web(request):
    with open("index.html", "r") as f:
        return web.Response(text=f.read(), content_type="text/html")

# Telegram bot handlers
def start(update, context):
    update.message.reply_text("Welcome to ClimateGuardianBot! Use commands to get data:\n/weather\n/average\n/esp\n/rpi")

def weather(update, context):
    update.message.reply_text(f"Weather in Chernihiv: {weather_data}")

def average(update, context):
    if avg_data["temperature"] is None:
        update.message.reply_text("Average data not available (ESP8266 offline)")
    else:
        update.message.reply_text(
            f"Average Temperature: {avg_data['temperature']:.1f} ± {avg_data['temp_error']:.1f} °C\n"
            f"Average Humidity: {avg_data['humidity']:.1f} ± {avg_data['hum_error']:.1f} %"
        )

def esp(update, context):
    if esp_data["temperature"] is None:
        update.message.reply_text("ESP8266 offline")
    else:
        update.message.reply_text(
            f"ESP8266 (DHT11):\n"
            f"Temperature: {esp_data['temperature']:.1f} °C\n"
            f"Humidity: {esp_data['humidity']:.1f} %"
        )

def rpi(update, context):
    update.message.reply_text(
        f"Raspberry Pi (DHT22):\n"
        f"Temperature: {rpi_data['temperature']:.1f} °C\n"
        f"Humidity: {rpi_data['humidity']:.1f} %"
    )

# Check ESP8266 status periodically
async def check_esp_status(updater):
    global last_esp_check
    while True:
        await asyncio.sleep(600)  # Every 10 minutes
        if esp_data["last_update"] is None or (datetime.now() - esp_data["last_update"]) > timedelta(minutes=1):
            updater.bot.send_message(chat_id=CHAT_ID, text="⚠️ Warning: ESP8266 not responding")
            last_esp_check = datetime.now()

# Main loop
async def main():
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

    # Setup Telegram bot
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("weather", weather))
    dp.add_handler(CommandHandler("average", average))
    dp.add_handler(CommandHandler("esp", esp))
    dp.add_handler(CommandHandler("rpi", rpi))
    updater.start_polling()

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