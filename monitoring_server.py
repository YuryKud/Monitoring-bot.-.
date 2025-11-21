import os
import time
import threading
import paramiko
from flask import Flask, jsonify
from dotenv import load_dotenv, find_dotenv
import logging
import codecs
import sys
import locale
locale.setlocale(locale.LC_ALL, "ru_RU.UTF-8")


# Логирование загрузки .env
print(f"Загружаем .env из: {find_dotenv()}")

# Загрузка переменных окружения
load_dotenv()

# Переменные окружения
MONITOR_SERVER_HOST = os.getenv("MONITOR_SERVER_HOST", "127.0.0.1")
MONITOR_SERVER_PORT = int(os.getenv("MONITOR_SERVER_PORT", 5000))
REMOTE_HOST = os.getenv("REMOTE_HOST")
USERNAME = os.getenv("USERNAME_1")  # Обновленная переменная
PASSWORD = os.getenv("PASSWORD")
BUY_BOT_SERVICE = os.getenv("BUY_BOT_SERVICE", "buy_bot")
SELL_BOT_SERVICE = os.getenv("SELL_BOT_SERVICE", "sell_bot")
BUY_BOT_PING_FILE = os.getenv("BUY_BOT_PING_FILE")
SELL_BOT_PING_FILE = os.getenv("SELL_BOT_PING_FILE")
PING_TIMEOUT = int(os.getenv("PING_TIMEOUT", 120))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 1800))

# Настройка логирования
log_file = "bot_monitor.log"
file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
file_handler.setFormatter(formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler]
)
print("заебал")  # Отладочный вывод


# Проверка доступности лог-файла
try:
    with open(log_file, "a") as f:
        f.write("\n--- Log Initialized ---\n")
except Exception as e:
    print(f"Ошибка при создании лог-файла: {e}")
    sys.exit(1)

# Flask-приложение
app = Flask(__name__)

# Глобальная переменная для хранения состояния ботов
bot_status = {
    "buy_bot": "Unknown",
    "sell_bot": "Unknown"
}


@app.route("/", methods=["GET"])
def home():
    return "Bot Monitoring Server is running. Use /status to check bot statuses."


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/status", methods=["GET"])
def status():
    """
    Возвращает текущее состояние ботов.
    """
    return jsonify(bot_status)


def restart_service(ssh, service_name):
    """
    Перезапускает указанный сервис через systemctl.
    """
    try:
        logging.info(f"Перезапуск {service_name}...")
        ssh.exec_command(f"systemctl restart {service_name}")
        logging.info(f"{service_name} успешно перезапущен.")
    except Exception as e:
        logging.error(f"Ошибка при перезапуске {service_name}: {e}")


def start_service(ssh, service_name):
    """
    Запускает указанный сервис через systemctl.
    """
    try:
        logging.info(f"Запуск {service_name}...")
        ssh.exec_command(f"systemctl start {service_name}")

        logging.info(f"{service_name} успешно запущен.")
    except Exception as e:
        logging.error(f"Ошибка при запуске {service_name}: {e}")


def check_bot_ping(ssh, ping_file_path, service_name):
    """
    Проверяет файл ping на удаленном сервере. Если бот не обновляет файл, перезапускает или запускает сервис.
    """
    global bot_status
    try:
        stdin, stdout, stderr = ssh.exec_command(f"cat {ping_file_path}")
        output = stdout.read().decode().strip()

        if output:
            last_ping = float(output)
            current_time = time.time()

            # Проверяем, обновлялся ли файл менее чем PING_TIMEOUT секунд назад
            if current_time - last_ping > PING_TIMEOUT:
                bot_status[service_name] = "Not Running"
                logging.warning(f"{service_name} не работает. Перезапуск...")
                restart_service(ssh, service_name)
            else:
                bot_status[service_name] = "Running"
                logging.info(f"{service_name} работает нормально.")
        else:
            bot_status[service_name] = "File Not Found"

            start_service(ssh, service_name)
    except Exception as e:
        bot_status[service_name] = f"Error: {e}"
        logging.error(f"Ошибка при проверке {service_name}: {e}")


def monitor_bots(stop_event):
    """
    Циклическая проверка состояния ботов через SSH.
    """
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        ssh.connect(hostname=REMOTE_HOST, username=USERNAME, password=PASSWORD)
        ssh.get_transport().set_keepalive(30)  # Удержание сессии активной
        logging.info("Успешное подключение к серверу.")

        while not stop_event.is_set():
            logging.info("Начало проверки состояния ботов...")
            check_bot_ping(ssh, BUY_BOT_PING_FILE, BUY_BOT_SERVICE)
            check_bot_ping(ssh, SELL_BOT_PING_FILE, SELL_BOT_SERVICE)
            logging.info("Проверка завершена. Ожидание следующей проверки...")
            stop_event.wait(CHECK_INTERVAL)
    except Exception as e:
        logging.error(f"Ошибка SSH: {e}")
    finally:
        ssh.close()
        logging.info("SSH-соединение закрыто.")


def run_flask_server(stop_event):
    """
    Запускает Flask-сервер.
    """
    logging.info("Запуск Flask-сервера...")
    try:
        app.run(host=MONITOR_SERVER_HOST, port=MONITOR_SERVER_PORT,
                debug=False, use_reloader=False)
    except Exception as e:
        logging.error(f"Ошибка запуска Flask-сервера: {e}")
        stop_event.set()


if __name__ == "__main__":
    stop_event = threading.Event()

    try:
        # Запуск Flask-сервера в отдельном потоке
        flask_thread = threading.Thread(
            target=run_flask_server, args=(stop_event,), daemon=True)
        flask_thread.start()

        logging.info("Flask-сервер запущен в отдельном потоке.")

        # Запуск мониторинга ботов в основном потоке
        monitor_bots(stop_event)
    except KeyboardInterrupt:
        logging.info("Завершение работы по сигналу KeyboardInterrupt.")
        stop_event.set()
    except Exception as e:
        logging.error(f"Необработанная ошибка: {e}")
        stop_event.set()
    finally:
        logging.info("Программа завершена.")
        sys.exit(0)
