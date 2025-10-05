from flask import Flask, render_template, request, jsonify
import os
import json
from datetime import datetime
import asyncio
import aiofiles
from werkzeug.utils import secure_filename
import logging
from pathlib import Path
import aiofiles.os as aos
import concurrent.futures
import threading

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

application = Flask(__name__)
application.config['UPLOAD_FOLDER'] = 'static/uploads/'
application.config['DATA_FOLDER'] = 'data/'
application.config['MAX_CONTENT_LENGTH'] = 160 * 1024 * 1024

# Создаем директории если не существуют
Path(application.config['UPLOAD_FOLDER']).mkdir(parents=True, exist_ok=True)
Path(application.config['DATA_FOLDER']).mkdir(parents=True, exist_ok=True)

# Кэш для частых операций
user_folders_cache = set()

# Пул потоков для асинхронных операций
thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)


def run_async(coro):
    """Запуск асинхронной функции в отдельном event loop"""

    def run_in_thread():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro)
        except Exception as e:
            logger.error(f"Ошибка в асинхронной задаче: {e}")
            raise
        finally:
            loop.close()

    return thread_pool.submit(run_in_thread).result()


async def delete_files_async(folder_path):
    """Асинхронное удаление файлов"""
    try:
        if await aos.path.exists(folder_path):
            for filename in await aos.listdir(folder_path):
                file_path = os.path.join(folder_path, filename)
                if await aos.path.isfile(file_path):
                    await aos.remove(file_path)
                    logger.info(f"Удален файл: {file_path}")
    except Exception as e:
        logger.error(f'Ошибка при удалении файлов: {e}')


async def save_data_async(filepath, data):
    """Асинхронное сохранение данных в файл"""
    try:
        # Создаем директорию если не существует
        directory = os.path.dirname(filepath)
        await aos.makedirs(directory, exist_ok=True)

        async with aiofiles.open(filepath, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(data, ensure_ascii=False, indent=2))
        logger.info(f"Данные сохранены в: {filepath}")
    except Exception as e:
        logger.error(f'Ошибка при сохранении файла {filepath}: {e}')
        raise


def process_file_upload(file, user_upload_folder, user_id):
    """Синхронная обработка загрузки одного файла"""
    if file and file.filename:
        filename = secure_filename(file.filename)
        filepath = os.path.join(user_upload_folder, filename)

        # Сохраняем файл
        file.save(filepath)

        return f"/{user_id}/{filename}"
    return None


@application.route('/')
def index():
    return render_template('index.html')


@application.route('/upload', methods=['POST'])
def upload_files():
    """Загрузка файлов"""
    try:
        user_id = request.remote_addr.replace('.', '_')
        uploaded_files = request.files.getlist('photos')
        filenames = []

        # Создаем папку пользователя если её нет
        user_upload_folder = os.path.join(application.config['UPLOAD_FOLDER'], user_id)
        if user_id not in user_folders_cache:
            os.makedirs(user_upload_folder, exist_ok=True)
            user_folders_cache.add(user_id)

        # Обрабатываем файлы синхронно
        for file in uploaded_files:
            filename = process_file_upload(file, user_upload_folder, user_id)
            if filename:
                filenames.append(filename)

        logger.info(f"Пользователь {user_id} загрузил {len(filenames)} файлов")
        return jsonify(filenames)

    except Exception as e:
        logger.error(f'Ошибка при загрузке файлов: {e}')
        return jsonify({
            'status': 'error',
            'message': f'Ошибка при загрузке файлов: {str(e)}'
        }), 500


@application.route('/save_info', methods=['POST'])
def save_info():
    """Сохранение информации с асинхронной обработкой в фоне"""
    try:
        data = request.get_json()
        user_id = request.remote_addr.replace('.', '_')

        if not data:
            return jsonify({
                'status': 'error',
                'message': 'Нет данных для сохранения'
            }), 400

        # Добавляем временную метку
        data['saved_at'] = datetime.now().isoformat()

        # Генерируем имя файла
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"photo_data_{timestamp}.json"

        user_data_folder = os.path.join(application.config['DATA_FOLDER'], user_id)
        filepath = os.path.join(user_data_folder, filename)

        # Запускаем асинхронное сохранение в отдельном потоке
        def background_save():
            try:
                # Создаем асинхронную задачу для сохранения
                async def save_all():
                    # Сохраняем данные
                    await save_data_async(filepath, data)

                    # Удаляем загруженные файлы
                    user_upload_folder = os.path.join(application.config['UPLOAD_FOLDER'], user_id)
                    await delete_files_async(user_upload_folder)
                    logger.info(f"Фоновая обработка для пользователя {user_id} завершена")

                # Запускаем асинхронную задачу
                run_async(save_all())
            except Exception as e:
                logger.error(f"Ошибка в фоновой задаче: {e}")

        # Запускаем в фоне без ожидания завершения
        thread = threading.Thread(target=background_save)
        thread.daemon = True
        thread.start()

        return jsonify({
            'status': 'success',
            'message': 'Данные сохраняются в фоне',
            'filename': filename
        })

    except Exception as e:
        logger.error(f'Ошибка при сохранении: {e}')
        return jsonify({
            'status': 'error',
            'message': f'Ошибка при сохранении: {str(e)}'
        }), 500


async def periodic_cache_cleanup():
    """Периодическая очистка кэша папок пользователей"""
    while True:
        await asyncio.sleep(3600)  # Каждый час
        try:
            # Очищаем кэш от несуществующих папок
            to_remove = set()
            for user_id in user_folders_cache.copy():
                user_folder = os.path.join(application.config['UPLOAD_FOLDER'], user_id)
                if not os.path.exists(user_folder):
                    to_remove.add(user_id)

            user_folders_cache.difference_update(to_remove)
            if to_remove:
                logger.info(f"Очистка кэша: удалено {len(to_remove)} записей")
        except Exception as e:
            logger.error(f"Ошибка при очистке кэша: {e}")


def run_cleanup_loop():
    """Запуск цикла очистки в отдельном потоке"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(periodic_cache_cleanup())
    except Exception as e:
        logger.error(f"Ошибка в цикле очистки: {e}")
    finally:
        loop.close()


@application.before_request
def startup():
    """Запуск фоновых задач при старте приложения"""
    try:
        cleanup_thread = threading.Thread(target=run_cleanup_loop)
        cleanup_thread.daemon = True
        cleanup_thread.start()
        logger.info("Фоновая задача очистки кэша запущена")
    except Exception as e:
        logger.error(f"Ошибка при запуске фоновой задачи: {e}")


# Альтернативная версия для современных версий Flask
@application.route('/upload_async', methods=['POST'])
def upload_files_async():
    """Альтернативная версия загрузки с асинхронной обработкой"""
    try:
        user_id = request.remote_addr.replace('.', '_')
        uploaded_files = request.files.getlist('photos')

        # Создаем папку пользователя если её нет
        user_upload_folder = os.path.join(application.config['UPLOAD_FOLDER'], user_id)
        os.makedirs(user_upload_folder, exist_ok=True)

        # Синхронно сохраняем файлы
        filenames = []
        for file in uploaded_files:
            if file and file.filename:
                filename = secure_filename(file.filename)
                filepath = os.path.join(user_upload_folder, filename)
                file.save(filepath)
                filenames.append(f"/{user_id}/{filename}")

        logger.info(f"Пользователь {user_id} загрузил {len(filenames)} файлов (async)")
        return jsonify(filenames)

    except Exception as e:
        logger.error(f'Ошибка при асинхронной загрузке файлов: {e}')
        return jsonify({
            'status': 'error',
            'message': f'Ошибка при загрузке файлов: {str(e)}'
        }), 500


if __name__ == '__main__':
    application.run(debug=False, host="0.0.0.0")