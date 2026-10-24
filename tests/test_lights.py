# -*- coding: utf-8 -*-
"""Проверяем КОНСТРУКЦИЮ запросов к API Яндекса на локальном сервере-заглушке.
Сам Яндекс так не проверить — нужен токен пользователя, — но форму запроса,
заголовки, тело и разбор ответа проверить можно полностью."""
import importlib.util
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

spec = importlib.util.spec_from_file_location(
    "a", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ssb_alarm.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

seen = []

USER_INFO = {
    "devices": [
        {"id": "dev-lamp", "name": "Лампа у кровати", "type": "devices.types.light",
         "capabilities": [
             {"type": "devices.capabilities.on_off"},
             {"type": "devices.capabilities.range",
              "parameters": {"instance": "brightness", "unit": "unit.percent"}}]},
        {"id": "dev-switch", "name": "Выключатель в комнате",
         "type": "devices.types.switch",
         "capabilities": [{"type": "devices.capabilities.on_off"}]},
    ]
}


def ok_action():
    return {"devices": [{"id": "dev-lamp", "capabilities": [
        {"type": "devices.capabilities.on_off",
         "state": {"instance": "on",
                   "action_result": {"status": "DONE"}}}]}]}


def bad_action():
    return {"devices": [{"id": "dev-lamp", "capabilities": [
        {"type": "devices.capabilities.on_off",
         "state": {"instance": "on",
                   "action_result": {"status": "ERROR",
                                     "error_code": "DEVICE_UNREACHABLE",
                                     "error_message": "устройство недоступно"}}}]}]}


MODE = {"fail": False, "http_error": 0}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        raw = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        seen.append(("GET", self.path, dict(self.headers), None))
        if MODE["http_error"]:
            self._send(MODE["http_error"], {"message": "nope"})
            return
        self._send(200, USER_INFO)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n).decode("utf-8"))
        seen.append(("POST", self.path, dict(self.headers), body))
        if MODE["http_error"]:
            self._send(MODE["http_error"], {"message": "nope"})
            return
        self._send(200, bad_action() if MODE["fail"] else ok_action())


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
m.IOT_BASE = "http://127.0.0.1:%d" % srv.server_address[1]
TOKEN = "test-token-not-real"

print("=== 1. список устройств ===")
ok, devices = m.iot_devices(TOKEN)
assert ok, devices
print("устройств получено:", len(devices))
meth, path, hdr, _ = seen[-1]
assert path == "/v1.0/user/info", path
assert hdr.get("Authorization") == "Bearer " + TOKEN, "токен не в заголовке"
assert TOKEN not in path, "токен утёк в адрес запроса"
print("путь:", path, "| заголовок Authorization: есть, в адресе токена нет")
print("умеет яркость — лампа:", m.device_dims(devices[0]),
      "| выключатель:", m.device_dims(devices[1]))
assert m.device_dims(devices[0]) and not m.device_dims(devices[1])

print()
print("=== 2. включение ===")
ok, data = m.iot_on_off(TOKEN, "dev-lamp", True)
assert ok, data
meth, path, hdr, body = seen[-1]
print("метод:", meth, "| путь:", path)
print("тело:", json.dumps(body, ensure_ascii=False))
assert path == "/v1.0/devices/actions"
assert body == {"devices": [{"id": "dev-lamp", "actions": [
    {"type": "devices.capabilities.on_off",
     "state": {"instance": "on", "value": True}}]}]}, "форма тела не та"
assert hdr.get("Content-Type") == "application/json"
assert m.action_failed(data) == []
print("форма тела совпадает с документацией, отказов нет")

print()
print("=== 3. яркость ===")
ok, data = m.iot_brightness(TOKEN, "dev-lamp", 37)
assert ok
_, _, _, body = seen[-1]
print("тело:", json.dumps(body, ensure_ascii=False))
assert body["devices"][0]["actions"][0]["state"] == {
    "instance": "brightness", "value": 37.0}

print()
print("=== 4. устройство ответило отказом при HTTP 200 ===")
MODE["fail"] = True
ok, data = m.iot_on_off(TOKEN, "dev-lamp", True)
fails = m.action_failed(data)
print("HTTP успех:", ok, "| распознанный отказ:", fails)
assert ok and fails == ["устройство недоступно"], "отказ устройства не пойман"
MODE["fail"] = False

print()
print("=== 5. протухший токен и нет прав ===")
for code, expect in ((401, "токен недействителен"), (403, "iot:control")):
    MODE["http_error"] = code
    ok, data = m.iot_on_off(TOKEN, "dev-lamp", True)
    print("HTTP %d -> %s" % (code, data["error"]))
    assert not ok and expect in data["error"]
MODE["http_error"] = 0

print()
print("=== 6. рассвет: расчёт времени включения ===")
for hh, mm, pre in ((6, 30, 30), (6, 30, 45), (0, 20, 30), (6, 0, 90)):
    print("подъём %02d:%02d, за %d мин -> свет в %02d:%02d"
          % (hh, mm, pre, *m.light_time(hh, mm, pre)))
assert m.light_time(6, 30, 30) == (6, 0)
assert m.light_time(0, 20, 30) == (23, 50), "переход через полночь сломан"

print()
print("=== 7. рассвет на устройстве с яркостью ===")
m.LIGHTS_CONF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lights_test.json")
m.save_lights({"token": TOKEN, "device_id": "dev-lamp", "device_name": "",
               "pre_minutes": 5, "dawn": True})
real_sleep = m.time.sleep
m.time.sleep = lambda s: None                 # не ждём 5 минут в тесте
seen.clear()
m.run_dawn(log=lambda s: print("   ", s))
m.time.sleep = real_sleep
brightness = [b["devices"][0]["actions"][0]["state"]["value"]
              for _, _, _, b in seen if b and b["devices"][0]["actions"][0]["type"]
              == "devices.capabilities.range"]
print("шагов яркости:", len(brightness), "| от", brightness[0], "до", brightness[-1])
assert brightness[0] <= 2 and brightness[-1] == 100
assert brightness == sorted(brightness), "яркость не растёт монотонно"

print()
print("ВСЁ ПРОВЕРЕНО (кроме реального ответа Яндекса — нужен твой токен)")
srv.shutdown()
