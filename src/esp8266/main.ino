#include <Arduino.h>
#include <Wire.h>
#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <WiFiClient.h>
#include <Hash.h>
#include <ESPAsyncTCP.h>
#include <ESPAsyncWebServer.h>
#include <Adafruit_Sensor.h>
#include <DHT.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <ArduinoJson.h>

// Wi-Fi credentials
const char* ssid = "YOUR_SSID";
const char* password = "YOUR_PASSWORD";

// Static IP configuration
IPAddress staticIP(192, 168, 0, 51);
IPAddress gateway(192, 168, 0, 1);
IPAddress subnet(255, 255, 255, 0);

// Raspberry Pi server
const char* raspberryPiHost = "192.168.0.50";
const int raspberryPiPort = 80;

// DHT settings
#define DHTPIN 5
#define DHTTYPE DHT11
DHT dht(DHTPIN, DHTTYPE);

// OLED settings
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET    -1
#define SCREEN_ADDRESS 0x3C
#define OLED_SDA 14  // D5
#define OLED_SCL 12  // D6

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

float t = 0.0;
float h = 0.0;
float avgT = 0.0;
float avgH = 0.0;
String weather = "N/A";
bool serverActive = false;
unsigned long previousMillis = 0;
const long interval = 10000;

AsyncWebServer server(80);

String localIP;

// Web interface
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

String processor(const String& var){
  if (var == "TEMPERATURE") return String(t, 1);
  if (var == "HUMIDITY") return String(h, 1);
  if (var == "IP") return WiFi.localIP().toString();
  return String();
}

void setup() {
  Serial.begin(115200);
  dht.begin();
  Wire.begin(OLED_SDA, OLED_SCL);

  // OLED
  if (!display.begin(SSD1306_SWITCHCAPVCC, SCREEN_ADDRESS)) {
    Serial.println("OLED initialization failed!");
    while (true);
  }
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println("Starting...");
  display.display();
  delay(1000);

  // WiFi with static IP
  WiFi.config(staticIP, gateway, subnet);
  WiFi.begin(ssid, password);
  Serial.print("Connecting");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  localIP = WiFi.localIP().toString();
  Serial.println("\nConnected! IP: " + localIP);

  // Web routes
  server.on("/", HTTP_GET, [](AsyncWebServerRequest *req){
    req->send_P(200, "text/html", index_html, processor);
  });
  server.on("/temperature", HTTP_GET, [](AsyncWebServerRequest *req){
    String temp = String(t, 1);
    req->send(200, "text/plain", temp);
  });
  server.on("/humidity", HTTP_GET, [](AsyncWebServerRequest *req){
    String hum = String(h, 1);
    req->send(200, "text/plain", hum);
  });
  server.begin();
}

void sendDataToRaspberryPi() {
  WiFiClient client;
  HTTPClient http;

  String url = "http://" + String(raspberryPiHost) + ":" + String(raspberryPiPort) + "/update";
  http.begin(client, url);
  http.addHeader("Content-Type", "application/json");

  StaticJsonDocument<200> doc;
  doc["temperature"] = t;
  doc["humidity"] = h;
  String payload;
  serializeJson(doc, payload);

  int httpCode = http.POST(payload);
  if (httpCode == HTTP_CODE_OK) {
    String response = http.getString();
    StaticJsonDocument<200> responseDoc;
    deserializeJson(responseDoc, response);

    avgT = responseDoc["avgTemperature"];
    avgH = responseDoc["avgHumidity"];
    weather = responseDoc["weather"].as<String>();
    serverActive = true;
  } else {
    serverActive = false;
    Serial.println("Failed to connect to Raspberry Pi: " + String(httpCode));
  }
  http.end();
}

void loop() {
  unsigned long currentMillis = millis();
  if (currentMillis - previousMillis >= interval) {
    previousMillis = currentMillis;

    // Read DHT11 sensor
    float newT = dht.readTemperature();
    float newH = dht.readHumidity();

    if (!isnan(newT)) t = newT;
    if (!isnan(newH)) h = newH;

    // Send data to Raspberry Pi and get average/weather
    if (WiFi.status() == WL_CONNECTED) {
      sendDataToRaspberryPi();
    } else {
      serverActive = false;
    }

    // OLED update
    display.clearDisplay();
    display.setTextSize(1);
    display.setCursor(0, 0);

    if (isnan(t) || isnan(h)) {
      display.println("Sensor Error");
    } else if (!serverActive) {
      display.println("RPi Offline");
      display.print("T:");
      display.print(t, 1);
      display.print((char)247);
      display.println("C");
      display.print("H:");
      display.print(h, 1);
      display.println("%");
    } else {
      display.print("Avg T:");
      display.print(avgT, 1);
      display.print((char)247);
      display.println("C");
      display.print("Avg H:");
      display.print(avgH, 1);
      display.println("%");
      display.println("Weather:");
      display.println(weather);
    }

    // IP at the bottom
    display.setTextSize(1);
    display.setCursor(0, SCREEN_HEIGHT - 8);
    display.print("IP:");
    display.print(localIP);

    display.display();
    Serial.printf("Temp: %.1f C | Hum: %.1f %% | Avg T: %.1f | Avg H: %.1f | Weather: %s\n", t, h, avgT, avgH, weather.c_str());
  }
}