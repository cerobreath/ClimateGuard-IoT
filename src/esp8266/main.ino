#include <ESP8266HTTPClient.h>    // Бібліотека для HTTP-запитів до Raspberry Pi
#include <ESPAsyncWebServer.h>    // Асинхронний веб-сервер для резервного інтерфейсу
#include <DHT.h>                  // Бібліотека для роботи з датчиком DHT11
#include <Adafruit_SSD1306.h>     // Бібліотека для керування OLED-дисплеєм
#include <ArduinoJson.h>          // Бібліотека для обробки JSON-даних

// Налаштування Wi-Fi
const char* ssid = "YOUR_SSID";
const char* password = "YOUR_PASSWORD";

// Статична IP-конфігурація для стабільності
IPAddress staticIP(192, 168, 0, 51);
IPAddress gateway(192, 168, 0, 1);
IPAddress subnet(255, 255, 255, 0);

// Налаштування сервера Raspberry Pi
const char* raspberryPiHost = "192.168.0.50";  // IP-адреса Raspberry Pi
const int raspberryPiPort = 80;                // Порт для HTTP-з'єднання

// Налаштування датчика DHT11
#define DHTPIN 5           // Пін для підключення DHT11 (D1)
#define DHTTYPE DHT11      // Тип датчика (DHT11)
DHT dht(DHTPIN, DHTTYPE);  // Ініціалізація датчика

// Налаштування OLED-дисплея
#define SCREEN_WIDTH 128   // Ширина екрана в пікселях
#define SCREEN_HEIGHT 64   // Висота екрана в пікселях
#define OLED_RESET -1
#define SCREEN_ADDRESS 0x3C // I2C-адреса OLED
#define OLED_SDA 14
#define OLED_SCL 12

// Ініціалізація OLED
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

float t = 0.0;                    // Температура
float h = 0.0;                    // Вологість
float avgT = 0.0;                 // Середня температура
float avgH = 0.0;                 // Середня вологість
String weather = "N/A";           // Погода
bool serverActive = false;        // Статус зв’язку з Raspberry Pi
unsigned long previousMillis = 0; // Час попереднього оновлення
const long interval = 10000;      // Інтервал оновлення (10 секунд)

AsyncWebServer server(80);        // Ініціалізація веб-сервера на порту 80

String localIP;                   // Зберігання локальної IP-адреси

// HTML-код для резервного веб-інтерфейсу
const char index_html[] PROGMEM = R"rawliteral(
<!DOCTYPE HTML><html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    html { font-family: Arial, sans-serif; background: #111; color: #eee; text-align: center; }
    h2 { font-size: 2rem; margin-top: 20px; }
    p { font-size: 1.5rem; margin: 10px; }
    .label { font-weight: bold; }
    .units { font-size: 1.2rem; color: #ccc; }
    .data { font-size: 2.2rem; color: #00ffcc; }
    .icon { font-size: 2rem; margin-right: 10px; }
    .footer { font-size: 0.9rem; color: #888; margin-top: 20px; }
  </style>
</head>
<body>
  <h2>ESP8266 DHT11 Monitor</h2>
  <h3>Minimal backup interface</h3>
  <p><span class="icon">🌡️</span><span class="label">Temperature:</span>
    <span id="temperature" class="data">%TEMPERATURE%</span>
    <span class="units">°C</span>
  </p>
  <p><span class="icon">💧</span><span class="label">Humidity:</span>
    <span id="humidity" class="data">%HUMIDITY%</span>
    <span class="units">%</span>
  </p>
<script>
setInterval(() => {
  fetch("/temperature").then(res => res.text()).then(data => {
    document.getElementById("temperature").textContent = data;
  });
  fetch("/humidity").then(res => res.text()).then(data => {
    document.getElementById("humidity").textContent = data;
  });
}, 10000);
</script>
</body>
</html>)rawliteral";

// Функція для обробки змінних у HTML
String processor(const String& var) {
  if (var == "TEMPERATURE") return String(t, 1); // Повертає температуру з 1 знаком після коми
  if (var == "HUMIDITY") return String(h, 1);    // Повертає вологість з 1 знаком після коми
  return String();
}

// Функція ініціалізації
void setup() {
  Serial.begin(115200);           // Ініціалізація серійного порту для налагодження
  dht.begin();                    // Ініціалізація датчика DHT11
  Wire.begin(OLED_SDA, OLED_SCL); // Ініціалізація I2C для OLED

  // Ініціалізація OLED
  if (!display.begin(SSD1306_SWITCHCAPVCC, SCREEN_ADDRESS)) {
    Serial.println("OLED initialization failed!"); // Повідомлення про помилку ініціалізації
    while (true);                                  // Блокування, якщо OLED не ініціалізовано
  }
  display.clearDisplay();              // Очищення екрана
  display.setTextColor(SSD1306_WHITE); // Встановлення білого кольору тексту
  display.setTextSize(1);              // Встановлення розміру тексту
  display.setCursor(0, 0);             // Встановлення курсору в початкову позицію
  display.println("Starting...");
  display.display();                   // Оновлення екрана
  delay(1000);                         // Затримка 1 секунда

  // Налаштування Wi-Fi зі статичною IP-адресою
  WiFi.config(staticIP, gateway, subnet); // Встановлення статичної IP
  WiFi.begin(ssid, password);             // Підключення до Wi-Fi
  Serial.print("Connecting");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);        // Затримка 0.5 секунди
    Serial.print(".");
  }
  localIP = WiFi.localIP().toString();           // Отримання і збереження IP-адреси
  Serial.println("\nConnected! IP: " + localIP); // Повідомлення про успішне підключення

  // Налаштування веб-шляхів
  server.on("/", HTTP_GET, [](AsyncWebServerRequest *req) {
    req->send_P(200, "text/html", index_html, processor); // Відправка HTML-інтерфейсу
  });
  server.on("/temperature", HTTP_GET, [](AsyncWebServerRequest *req) {
    String temp = String(t, 1);         // Форматування температури
    req->send(200, "text/plain", temp); // Відправка температури
  });
  server.on("/humidity", HTTP_GET, [](AsyncWebServerRequest *req) {
    String hum = String(h, 1);         // Форматування вологості
    req->send(200, "text/plain", hum); // Відправка вологості
  });
  server.begin(); // Запуск веб-сервера
}

// Функція відправки даних на Raspberry Pi
void sendDataToRaspberryPi() {
  WiFiClient client;             // Створення клієнта для HTTP
  HTTPClient http;               // Ініціалізація HTTP-клієнта

  String url = "http://" + String(raspberryPiHost) + ":" + String(raspberryPiPort) + "/update"; // URL для POST-запиту
  http.begin(client, url); // Початок з’єднання
  http.addHeader("Content-Type", "application/json"); // Встановлення заголовка JSON

  StaticJsonDocument<200> doc; // Створення JSON-документа
  doc["temperature"] = t;      // Додавання температури
  doc["humidity"] = h;         // Додавання вологості
  String payload;              // Змінна для серіалізації
  serializeJson(doc, payload); // Серіалізація JSON у рядок

  int httpCode = http.POST(payload);        // Відправка POST-запиту
  if (httpCode == HTTP_CODE_OK) {           // Перевірка успішного відправлення
    String response = http.getString();     // Отримання відповіді
    StaticJsonDocument<200> responseDoc;
    deserializeJson(responseDoc, response); // Десеріалізація відповіді

    avgT = responseDoc["avgTemperature"];          // Отримання середньої температури
    avgH = responseDoc["avgHumidity"];             // Отримання середньої вологості
    weather = responseDoc["weather"].as<String>(); // Отримання погоди
    serverActive = true;
  } else {
    serverActive = false;
    Serial.println("Failed to connect to Raspberry Pi: " + String(httpCode));
  }
  http.end(); // Закриття з’єднання
}

// Головний цикл
void loop() {
  unsigned long currentMillis = millis();           // Поточний час у мілісекундах
  if (currentMillis - previousMillis >= interval) { // Перевірка інтервалу
    previousMillis = currentMillis;                 // Оновлення попереднього часу

    // Читання даних з датчика DHT11
    float newT = dht.readTemperature(); // Читання температури
    float newH = dht.readHumidity();    // Читання вологості

    if (!isnan(newT)) t = newT; // Оновлення температури, якщо дані валідні
    if (!isnan(newH)) h = newH; // Оновлення вологості, якщо дані валідні

    // Відправка даних на Raspberry Pi
    if (WiFi.status() == WL_CONNECTED) { // Перевірка підключення до Wi-Fi
      sendDataToRaspberryPi();           // Виклик функції відправки
    } else {
      serverActive = false;
    }

    // Оновлення OLED-дисплея
    display.clearDisplay();  // Очищення екрана
    display.setTextSize(1);  // Встановлення розміру тексту
    display.setCursor(0, 0); // Встановлення курсору

    if (isnan(t) || isnan(h)) {
      display.println("Sensor Error");
    } else if (!serverActive) {       // Режим офлайн при відсутності зв’язку
      display.println("RPi Offline"); // Повідомлення про офлайн Raspberry Pi
      display.print("T:");
      display.print(t, 1);
      display.println("C");
      display.print("H:");
      display.print(h, 1);
      display.println("%");
    } else { // Режим онлайн
      display.print("Avg T:");
      display.print(avgT, 1);
      display.println("C");
      display.print("Avg H:");
      display.print(avgH, 1);
      display.println("%");
      display.println("Weather:");
      display.println(weather);
    }

    // Виведення IP-адреси внизу екрана
    display.setTextSize(1);
    display.setCursor(0, SCREEN_HEIGHT - 8); // Встановлення курсору внизу
    display.print("IP:");
    display.print(localIP);

    display.display(); // Оновлення екрана
    Serial.printf("Temp: %.1f C | Hum: %.1f %% | Avg T: %.1f | Avg H: %.1f | Weather: %s\n",
                  t, h, avgT, avgH, weather.c_str()); // Виведення даних у Serial
  }
}