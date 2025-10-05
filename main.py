from flask import Flask, render_template, request, jsonify
import os
import json
from datetime import datetime
import asyncio
import aiofiles
from werkzeug.utils import secure_filename
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

application = Flask(__name__)
application.config['UPLOAD_FOLDER'] = 'static/uploads/'
application.config['DATA_FOLDER'] = 'data/'
application.config['MAX_CONTENT_LENGTH'] = 160 * 1024 * 1024

# Создаем директории если не существуют
os.makedirs(application.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(application.config['DATA_FOLDER'], exist_ok=True)

# Кэш для частых операций
user_folders_cache = set()


async def delete_files_async(folder_path):
    """Асинхронное удаление файлов"""
    try:
        for filename in os.listdir(folder_path):
            file_path = os.path.join(folder_path, filename)
            if os.path.isfile(file_path):
                os.unlink(file_path)  # Быстрее чем os.remove
    except Exception as e:
        logger.error(f'Ошибка при удалении файлов: {e}')


@application.route('/')
def index():
    # Кэшируем шаблон если он не меняется
    return render_template('index.html')


@application.route('/upload', methods=['POST'])
def upload_files():
    user_id = request.remote_addr.replace('.', '_')  # Безопасное имя папки
    uploaded_files = request.files.getlist('photos')
    filenames = []

    # Создаем папку пользователя если её нет
    user_upload_folder = os.path.join(application.config['UPLOAD_FOLDER'], user_id)
    if user_id not in user_folders_cache:
        os.makedirs(user_upload_folder, exist_ok=True)
        user_folders_cache.add(user_id)

    for file in uploaded_files:
        if file and file.filename:
            filename = secure_filename(file.filename)
            filepath = os.path.join(user_upload_folder, filename)
            file.save(filepath)
            filenames.append(f"/{user_id}/" + filename)

    return jsonify(filenames)


@application.route('/save_info', methods=['POST'])
def save_info():
    try:
        data = request.get_json()
        user_id = request.remote_addr.replace('.', '_')

        # Добавляем временную метку
        data['saved_at'] = datetime.now().isoformat()

        # Генерируем имя файла
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"photo_data_{timestamp}.json"

        user_data_folder = os.path.join(application.config['DATA_FOLDER'], user_id)
        os.makedirs(user_data_folder, exist_ok=True)
        filepath = os.path.join(user_data_folder, filename)

        # Асинхронное сохранение
        import threading
        def save_data():
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

                # Асинхронное удаление файлов
                user_upload_folder = os.path.join(application.config['UPLOAD_FOLDER'], user_id)
                if os.path.exists(user_upload_folder):
                    asyncio.run(delete_files_async(user_upload_folder))
            except Exception as e:
                logger.error(f'Ошибка в фоновой задаче: {e}')

        # Запускаем в отдельном потоке
        thread = threading.Thread(target=save_data)
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


if __name__ == '__main__':
    # Отключаем debug в продакшене
    application.run(debug=False, host="0.0.0.0")