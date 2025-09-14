#!/usr/bin/env python3
# main.py
import json
import mimetypes
import pathlib
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse
from multiprocessing import Process
import socket
from datetime import datetime
import os
import threading
import time

# --- Налаштування ---
HTTP_PORT = int(os.environ.get("HTTP_PORT", "3000"))
SOCKET_HOST = os.environ.get("SOCKET_HOST", "127.0.0.1")
SOCKET_PORT = int(os.environ.get("SOCKET_PORT", "5001"))
MONGO_HOST = os.environ.get("MONGO_HOST", "mongo")
MONGO_PORT = int(os.environ.get("MONGO_PORT", "27017"))
MONGO_DB = os.environ.get("MONGO_DB", "messages_db")
MONGO_COLLECTION = os.environ.get("MONGO_COLLECTION", "messages")

# ---------------- Socket-server (приймає JSON по TCP і зберігає в MongoDB") ----------------


def socket_server_tcp(host='0.0.0.0', port=5001):
    """
    TCP Socket сервер: приймає JSON-рядок від клієнта
    (весь потік), перетворює в dict, додає дату, зберігає в MongoDB.
    """
    # ленивый импорт pymongo, чтобы main.py можно было запускать без mongo зависимости если нужно
    try:
        from pymongo import MongoClient
    except Exception as e:
        print("Помилка імпорту pymongo:", e)
        return

    mongo_uri = f"mongodb://{MONGO_HOST}:{MONGO_PORT}/"
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5001)
    try:
        # Перевіримо з’єднання (може запуститися трохи пізніше)
        client.server_info()
    except Exception as e:
        print("Не вдалося підключитися до MongoDB:", e)
        # Будемо намагатися повторно підключитися в циклі
    db = client[MONGO_DB]
    coll = db[MONGO_COLLECTION]

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(5)
    print(
        f"[socket_server] TCP server listening on {host}:{port}, will write to {MONGO_HOST}:{MONGO_PORT}/{MONGO_DB}.{MONGO_COLLECTION}")

    while True:
        try:
            conn, addr = srv.accept()
            data_chunks = []
            # Читаємо, поки клієнт не закриє з’єднання
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data_chunks.append(chunk)
            raw = b''.join(data_chunks)
            if not raw:
                conn.close()
                continue
            try:
                text = raw.decode('utf-8')
                obj = json.loads(text)
                # Очікуємо поля username та message (якщо їх немає — ігноруємо)
                username = obj.get("username", "")
                message = obj.get("message", "")
                record = {
                    "date": datetime.now().isoformat(sep=' '),
                    "username": username,
                    "message": message
                }
                coll.insert_one(record)
                print(f"[socket_server] saved message from {username}")
                conn.sendall(b'OK')
            except Exception as e:
                print("[socket_server] error processing data:", e)
                try:
                    conn.sendall(b'ERROR')
                except:
                    pass
            conn.close()
        except KeyboardInterrupt:
            print("[socket_server] shutting down")
            srv.close()
            break
        except Exception as e:
            print("[socket_server] accept loop error:", e)
            time.sleep(1)
            continue

# ---------------- HTTP Сервер (в одному процесі) -------------------------------------------


class HttpHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        pr_url = urllib.parse.urlparse(self.path)
        path = pr_url.path
        if path == '/' or path == '/index.html':
            self.send_html_file('index.html')
        elif path == '/message.html' or path == '/message':
            self.send_html_file('message.html')
        else:
            # Статичні файли (css, png тощо) — шлях повинен існувати у файловій системі
            fs_path = pathlib.Path('.').joinpath(path[1:])
            if fs_path.exists() and fs_path.is_file():
                self.send_static(path)
            else:
                self.send_html_file('error.html', status=404)

    def do_POST(self):
        pr_url = urllib.parse.urlparse(self.path)
        if pr_url.path == '/message':
            # Читаємо тіло (form-urlencoded)
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8')
            # Очікуємо application/x-www-form-urlencoded
            data = urllib.parse.parse_qs(body)
            username = data.get('username', [''])[0]
            message = data.get('message', [''])[0]

            # Сформуємо JSON і відправимо по TCP на socket-сервер
            payload = {"username": username, "message": message}
            try:
                with socket.create_connection((SOCKET_HOST, SOCKET_PORT), timeout=5) as s:
                    s.sendall(json.dumps(payload).encode('utf-8'))
                    # Опційно отримати відповідь
                    try:
                        resp = s.recv(1024)
                        # Ігноруємо тіло відповіді
                    except:
                        pass
                # Після успішної відправки — зробимо редирект на сторінку / (або відобразимо повідомлення)
                self.send_response(303)
                self.send_header('Location', '/')
                self.end_headers()
            except Exception as e:
                print("Помилка відправки на socket-сервер:", e)
                # отвечаем ошибкой 500
                self.send_html_file('error.html', status=500)
        else:
            self.send_html_file('error.html', status=404)

    def send_html_file(self, filename, status=200):
        try:
            self.send_response(status)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            with open(filename, 'rb') as fd:
                self.wfile.write(fd.read())
        except FileNotFoundError:
            # Якщо сам файл не знайдено — повернемо просту сторінку 500/404
            self.send_response(404)
            self.send_header('Content-type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'Not Found')

    def send_static(self, path):
        # path — рядок виду '/style.css' або '/images/logo.png'
        self.send_response(200)
        mt = mimetypes.guess_type(path)
        if mt and mt[0]:
            self.send_header("Content-type", mt[0])
        else:
            self.send_header("Content-type", 'application/octet-stream')
        self.end_headers()
        with open(f".{path}", 'rb') as file:
            self.wfile.write(file.read())


def run_http_server():
    server_address = ('', HTTP_PORT)
    httpd = HTTPServer(server_address, HttpHandler)
    print(f"[http] Listening on 0.0.0.0:{HTTP_PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()


# ---------------- main: Запускаємо socket-сервер в окремому процесі, а потім HTTP-сервер ----------------
if __name__ == '__main__':
    # Запускати socket-сервер в окремому процесі (щоб виконувалася вимога "в різних процесах")
    socket_proc = Process(target=socket_server_tcp, args=(
        '0.0.0.0', SOCKET_PORT), daemon=True)
    socket_proc.start()
    # Чекаємо трохи, щоб socket-сервер запустився
    time.sleep(0.5)
    try:
        run_http_server()
    finally:
        if socket_proc.is_alive():
            socket_proc.terminate()
            socket_proc.join(timeout=2)
