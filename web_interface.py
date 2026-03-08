#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Веб-интерфейс для менеджера загрузки файлов.
Запускает локальный веб-сервер с удобным интерфейсом.
"""

import os
import sys
import json
import threading
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify, send_from_directory

# Добавляем путь к downloader.py
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from downloader import SmartDownloader, DownloaderConfig, read_urls_from_file

app = Flask(__name__)

# Глобальный загрузчик
downloader = None
config = None
download_thread = None
download_result = None

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Менеджер загрузки файлов</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 15px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            overflow: hidden;
        }
        header {
            background: #4a5568;
            color: white;
            padding: 25px;
            text-align: center;
        }
        header h1 {
            font-size: 28px;
            margin-bottom: 10px;
        }
        .content {
            padding: 30px;
        }
        .section {
            margin-bottom: 30px;
            padding: 20px;
            border: 1px solid #e2e8f0;
            border-radius: 10px;
            background: #f7fafc;
        }
        .section h2 {
            color: #2d3748;
            margin-bottom: 15px;
            font-size: 22px;
            border-bottom: 2px solid #667eea;
            padding-bottom: 10px;
        }
        textarea {
            width: 100%;
            height: 200px;
            padding: 12px;
            border: 2px solid #cbd5e0;
            border-radius: 8px;
            font-family: 'Courier New', monospace;
            font-size: 14px;
            resize: vertical;
        }
        textarea:focus {
            outline: none;
            border-color: #667eea;
        }
        .btn {
            background: #667eea;
            color: white;
            border: none;
            padding: 12px 30px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 16px;
            margin: 5px;
            transition: background 0.3s;
        }
        .btn:hover { background: #5a67d8; }
        .btn:disabled { background: #a0aec0; cursor: not-allowed; }
        .btn-danger { background: #e53e3e; }
        .btn-danger:hover { background: #c53030; }
        .btn-success { background: #48bb78; }
        .btn-success:hover { background: #38a169; }
        .status {
            padding: 15px;
            background: #edf2f7;
            border-radius: 8px;
            margin: 15px 0;
            font-family: monospace;
            white-space: pre-wrap;
            max-height: 400px;
            overflow-y: auto;
        }
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin: 20px 0;
        }
        .stat-card {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 10px;
            text-align: center;
        }
        .stat-card .number {
            font-size: 32px;
            font-weight: bold;
            margin: 10px 0;
        }
        .stat-card .label {
            font-size: 14px;
            opacity: 0.9;
        }
        .files-list {
            max-height: 300px;
            overflow-y: auto;
            background: white;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 10px;
        }
        .file-item {
            padding: 8px;
            border-bottom: 1px solid #edf2f7;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .file-item:last-child { border-bottom: none; }
        .file-item .name {
            flex: 1;
            word-break: break-all;
        }
        .file-item .size {
            color: #718096;
            font-size: 14px;
            margin-left: 10 px;
        }
        .alert {
            padding: 15px;
            border-radius: 8px;
            margin: 15px 0;
        }
        .alert-success { background: #c6f6d5; color: #22543d; border: 1px solid #9ae6b4; }
        .alert-error { background: #fed7d7; color: #742a2a; border: 1px solid #feb2b2; }
        .alert-info { background: #bee3f8; color: #2c5282; border: 1px solid #90cdf4; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>📥 Менеджер загрузки файлов</h1>
            <p>Умный загрузчик с поддержкой возобновления и параллельных загрузок</p>
        </header>

        <div class="content">
            <!-- Статистика -->
            <div class="stats">
                <div class="stat-card">
                    <div class="label">Статус</div>
                    <div class="number" id="status">Готов</div>
                </div>
                <div class="stat-card">
                    <div class="label">Задач</div>
                    <div class="number" id="total">0</div>
                </div>
                <div class="stat-card">
                    <div class="label">Завершено</div>
                    <div class="number" id="completed">0</div>
                </div>
                <div class="stat-card">
                    <div class="label">Ошибок</div>
                    <div class="number" id="errors">0</div>
                </div>
            </div>

            <!-- URL Managing -->
            <div class="section">
                <h2>📝 Список URL для загрузки</h2>
                <div class="alert alert-info">
                    Введите по одному URL на строку. Поддерживаются HTTP/HTTPS.
                </div>
                <textarea id="urls-text" placeholder="https://example.com/file1.pdf&#10;https://example.com/file2.jpg"></textarea>
                <div style="margin-top: 15px;">
                    <button class="btn btn-success" onclick="startDownload()">🚀 Загрузить файлы</button>
                    <button class="btn" onclick="loadUrls()">📂 Загрузить из файла</button>
                    <button class="btn btn-danger" onclick="clearUrls()">🗑️ Очистить</button>
                </div>
            </div>

            <!-- Статус -->
            <div class="section">
                <h2>📊 Статус загрузки</h2>
                <div id="status-area">
                    <div class="alert alert-info">Нажмите "Загрузить файлы" для начала</div>
                </div>
                <button class="btn" onclick="refreshStatus()">🔄 Обновить</button>
                <button class="btn" onclick="downloadReport()">📄 Отчет</button>
            </div>

            <!-- Файлы -->
            <div class="section">
                <h2>📁 Загруженные файлы</h2>
                <div class="files-list" id="files-list">
                    <div style="text-align: center; color: #718096; padding: 20px;">
                        Папка загрузок пуста или не найдена
                    </div>
                </div>
                <div style="margin-top: 15px;">
                    <button class="btn" onclick="listFiles()">🔄 Обновить список</button>
                    <button class="btn" onclick="openFolder()">📂 Открыть папку</button>
                </div>
            </div>

            <!-- Настройки -->
            <div class="section">
                <h2>⚙️ Настройки</h2>
                <div class="alert alert-info">
                    Настройки редактируются в файле settings.json в папке Подкачка
                </div>
                <p><strong>Текущие настройки:</strong></p>
                <ul id="settings-list">
                    <li>Загрузок одновременно: <span id="max-concurrent">-</span></li>
                    <li>Папка загрузок: <span id="download-folder">-</span></li>
                    <li>Возобновление: <span id="resume">-</span></li>
                </ul>
                <button class="btn" onclick="loadSettings()">🔄 Загрузить настройки</button>
            </div>
        </div>
    </div>

    <script>
        const API_BASE = '';

        function startDownload() {
            const urls = document.getElementById('urls-text').value.trim();
            if (!urls) {
                alert('Введите хотя бы один URL');
                return;
            }

            fetch(API_BASE + '/download', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({urls: urls.split('\\n').filter(u => u.trim())})
            })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'started') {
                    showAlert('Загрузка начата!', 'success');
                    setTimeout(refreshStatus, 1000);
                } else {
                    showAlert('Ошибка: ' + data.error, 'error');
                }
            })
            .catch(err => {
                showAlert('Ошибка сети: ' + err, 'error');
            });
        }

        function refreshStatus() {
            fetch(API_BASE + '/status')
                .then(r => r.json())
                .then(data => {
                    updateStats(data);
                    updateStatusArea(data);
                })
                .catch(err => console.error(err));
        }

        function updateStats(data) {
            document.getElementById('status').textContent = data.status || 'Готов';
            document.getElementById('total').textContent = data.total || 0;
            document.getElementById('completed').textContent = data.completed || 0;
            document.getElementById('errors').textContent = data.errors || 0;
        }

        function updateStatusArea(data) {
            const area = document.getElementById('status-area');
            if (data.status === 'downloading') {
                area.innerHTML = `<div class="alert alert-info">Идет загрузка... Завершено: ${data.completed}/${data.total}</div>`;
            } else if (data.status === 'completed') {
                area.innerHTML = `<div class="alert alert-success">Загрузка завершена! Успешно: ${data.completed}, Ошибок: ${data.errors}</div>`;
            } else if (data.error) {
                area.innerHTML = `<div class="alert alert-error">Ошибка: ${data.error}</div>`;
            } else {
                area.innerHTML = `<div class="alert alert-info">Готов к загрузке</div>`;
            }
        }

        function listFiles() {
            fetch(API_BASE + '/files')
                .then(r => r.json())
                .then(data => {
                    const list = document.getElementById('files-list');
                    if (data.files && data.files.length > 0) {
                        list.innerHTML = data.files.map(f =>
                            `<div class="file-item">
                                <span class="name">${f.name}</span>
                                <span class="size">${f.size}</span>
                            </div>`
                        ).join('');
                    } else {
                        list.innerHTML = '<div style="text-align: center; color: #718096; padding: 20px;">Папка загрузок пуста</div>';
                    }
                })
                .catch(err => console.error(err));
        }

        function loadUrls() {
            fetch(API_BASE + '/load-urls')
                .then(r => r.json())
                .then(data => {
                    if (data.urls) {
                        document.getElementById('urls-text').value = data.urls.join('\\n');
                        showAlert('Загружено ' + data.urls.length + ' URL', 'success');
                    }
                });
        }

        function loadSettings() {
            fetch(API_BASE + '/settings')
                .then(r => r.json())
                .then(data => {
                    if (data.settings) {
                        document.getElementById('max-concurrent').textContent = data.settings.max_concurrent || 3;
                        document.getElementById('download-folder').textContent = data.settings.download_folder || 'Загрузки';
                        document.getElementById('resume').textContent = data.settings.resume ? 'Да' : 'Нет';
                    }
                });
        }

        function openFolder() {
            fetch(API_BASE + '/folder')
                .then(r => r.json())
                .then(data => {
                    if (data.folder) {
                        window.open('/files/' + encodeURIComponent(data.folder.split('/').pop()), '_blank');
                    }
                });
        }

        function downloadReport() {
            window.location.href = API_BASE + '/report';
        }

        function clearUrls() {
            document.getElementById('urls-text').value = '';
        }

        function showAlert(message, type) {
            const alertDiv = document.createElement('div');
            alertDiv.className = 'alert alert-' + type;
            alertDiv.textContent = message;
            document.querySelector('.content').insertBefore(alertDiv, document.querySelector('.section'));
            setTimeout(() => alertDiv.remove(), 5000);
        }

        // Инициализация
        document.addEventListener('DOMContentLoaded', function() {
            loadSettings();
            listFiles();
            refreshStatus();

            // Автообновление статуса каждые 2 секунды во время загрузки
            setInterval(() => {
                refreshStatus();
            }, 2000);
        });
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/download', methods=['POST'])
def api_download():
    global downloader, download_thread, download_result
    
    data = request.get_json()
    urls = data.get('urls', [])
    
    if not urls:
        return jsonify({'error': 'No URLs provided', 'status': 'error'})
    
    # Создаем загрузчик и запускаем в отдельном потоке
    def download_task():
        global download_result
        try:
            config = DownloaderConfig()
            downloader = SmartDownloader(config)
            download_result = downloader.download_batch(urls)
        except Exception as e:
            download_result = {'error': str(e)}
    
    download_thread = threading.Thread(target=download_task)
    download_thread.start()
    
    return jsonify({'status': 'started'})

@app.route('/api/status')
def api_status():
    global downloader, download_thread, download_result
    
    status = {
        'status': 'ready',
        'total': 0,
        'completed': 0,
        'errors': 0,
        'error': None
    }
    
    if download_thread and download_thread.is_alive():
        status['status'] = 'downloading'
        if downloader:
            status['total'] = len(downloader.tasks) if downloader.tasks else 0
            status['completed'] = downloader.completed
            status['errors'] = downloader.errors
    elif download_result:
        if 'error' in download_result:
            status['status'] = 'error'
            status['error'] = download_result['error']
        else:
            status['status'] = 'completed'
            status['total'] = download_result.get('total', 0)
            status['completed'] = download_result.get('completed', 0)
            status['errors'] = download_result.get('errors', 0)
    
    return jsonify(status)

@app.route('/api/files')
def api_files():
    try:
        config = DownloaderConfig()
        download_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), config.settings['download_folder'])
        
        if not os.path.exists(download_dir):
            return jsonify({'files': []})
        
        files = []
        for f in os.listdir(download_dir):
            if f.endswith('.part'):
                continue
            filepath = os.path.join(download_dir, f)
            if os.path.isfile(filepath):
                size = os.path.getsize(filepath)
                files.append({
                    'name': f,
                    'size': format_size(size),
                    'bytes': size
                })
        
        # Сортируем по дате изменения (новые сверху)
        files.sort(key=lambda x: os.path.getmtime(os.path.join(download_dir, x['name'])), reverse=True)
        
        return jsonify({'files': files, 'folder': download_dir})
    except Exception as e:
        return jsonify({'files': [], 'error': str(e)})

@app.route('/api/load-urls')
def api_load_urls():
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        urls_file = os.path.join(script_dir, 'urls.txt')
        urls = read_urls_from_file(urls_file)
        return jsonify({'urls': urls})
    except Exception as e:
        return jsonify({'urls': [], 'error': str(e)})

@app.route('/api/settings')
def api_settings():
    try:
        config = DownloaderConfig()
        return jsonify({'settings': config.settings})
    except Exception as e:
        return jsonify({'settings': {}, 'error': str(e)})

@app.route('/api/folder')
def api_folder():
    try:
        config = DownloaderConfig()
        download_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), config.settings['download_folder'])
        return jsonify({'folder': download_dir})
    except Exception as e:
        return jsonify({'folder': None, 'error': str(e)})

@app.route('/api/report')
def api_report():
    try:
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        report_path = os.path.join(desktop, 'download_report.txt')
        if os.path.exists(report_path):
            return send_from_directory(desktop, 'download_report.txt', as_attachment=True)
        else:
            return "Отчет не найден", 404
    except Exception as e:
        return str(e), 500

@app.route('/files/<path:filename>')
def serve_file(filename):
    try:
        config = DownloaderConfig()
        download_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), config.settings['download_folder'])
        return send_from_directory(download_dir, filename, as_attachment=True)
    except Exception as e:
        return str(e), 500

def format_size(bytes_size):
    """Форматирует размер в читаемый вид"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} TB"

def main():
    global downloader
    
    print("=" * 60)
    print("   ВЕБ-ИНТЕРФЕЙС МЕНЕДЖЕРА ЗАГРУЗКИ ФАЙЛОВ")
    print("=" * 60)
    
    # Инициализируем загрузчик
    config = DownloaderConfig()
    downloader = SmartDownloader(config)
    
    print(f"📁 Папка загрузок: {downloader.download_folder}")
    print(f"🌐 Веб-интерфейс будет доступен по адресу: http://localhost:5000")
    print("\nДля остановки нажмите Ctrl+C")
    print("=" * 60)
    
    # Запускаем Flask приложение
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nВеб-интерфейс остановлен")
    except Exception as e:
        print(f"Ошибка: {e}")
        import traceback
        traceback.print_exc()