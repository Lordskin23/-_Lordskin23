#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Улучшенный менеджер загрузки файлов.
Поддерживает:
- Параллельные загрузки (до 5 одновременно)
- Возобновление прерванных загрузок
- Прогресс-бар (tqdm)
- Проверку целостности (размеры)
- Логирование
- Конфигурацию через settings.json
"""

import os
import sys
import json
import time
import threading
import queue
import requests
from urllib.parse import urlparse
from datetime import datetime
from pathlib import Path

# Проверяем наличие tqdm
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

class DownloaderConfig:
    """Конфигурация загрузчика"""
    def __init__(self, config_path=None):
        if config_path is None:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.join(script_dir, 'settings.json')
        
        self.config_path = config_path
        self.settings = self.load()
    
    def load(self):
        """Загружает настройки"""
        defaults = {
            'download_folder': 'Загрузки',
            'max_concurrent': 3,
            'timeout': 30,
            'chunk_size': 8192,
            'resume': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'retry_count': 3,
            'retry_delay': 2,
            'auto_create_urls': True
        }
        
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    user_settings = json.load(f)
                    defaults.update(user_settings)
        except Exception:
            pass
        
        return defaults
    
    def save(self):
        """Сохраняет настройки"""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

class DownloadTask:
    """Задача на загрузку одного файла"""
    def __init__(self, url, filename=None, folder=None):
        self.url = url
        self.filename = filename
        self.folder = folder
        self.status = 'pending'  # pending, downloading, completed, error
        self.error = None
        self.downloaded = 0
        self.total = 0
        self.filepath = None
        self.temp_filepath = None
        
    def get_temp_path(self):
        """Возвращает путь к временному файлу"""
        if self.filepath:
            return self.filepath + '.part'
        return None

class SmartDownloader:
    """Умный загрузчик с поддержкой возобновления"""
    def __init__(self, config=None):
        self.config = config or DownloaderConfig()
        self.download_folder = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            self.config.settings['download_folder']
        )
        os.makedirs(self.download_folder, exist_ok=True)
        
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': self.config.settings['user_agent']})
        
        self.tasks = []
        self.completed = 0
        self.errors = 0
        self.lock = threading.Lock()
        
    def sanitize_filename(self, filename):
        """Очищает имя файла"""
        invalid = '<>:"/\\|?*'
        for ch in invalid:
            filename = filename.replace(ch, '_')
        return filename[:200]
    
    def get_filename(self, url, response):
        """Определяет имя файла"""
        # Пробуем из Content-Disposition
        if 'Content-Disposition' in response.headers:
            import re
            match = re.search(r'filename=["\']?([^"\';]+)["\']?', response.headers['Content-Disposition'])
            if match:
                return match.group(1)
        
        # Из URL
        path = urlparse(url).path
        filename = os.path.basename(path)
        if filename and '.' in filename:
            return filename
        
        # По Content-Type
        ct = response.headers.get('Content-Type', '')
        if 'text/html' in ct:
            return 'index.html'
        elif 'application/pdf' in ct:
            return 'document.pdf'
        elif 'image/' in ct:
            ext = ct.split('/')[-1].split(';')[0]
            return f'image.{ext}'
        
        return 'downloaded_file'
    
    def get_file_size(self, url):
        """Получает размер файла без загрузки"""
        try:
            resp = self.session.head(url, timeout=10, allow_redirects=True)
            if 'Content-Length' in resp.headers:
                return int(resp.headers['Content-Length'])
        except Exception:
            pass
        return 0
    
    def download_single(self, task):
        """Загружает один файл с поддержкой возобновления"""
        try:
            task.status = 'downloading'
            
            # Определяем имя файла
            if task.filename:
                filename = self.sanitize_filename(task.filename)
            else:
                # Пробуем получить имя из HEAD-запроса
                try:
                    head = self.session.head(task.url, timeout=10, allow_redirects=True)
                    filename = self.get_filename(task.url, head)
                    filename = self.sanitize_filename(filename)
                except:
                    filename = 'unknown_file'
            
            # Папка для файла
            folder = task.folder or self.download_folder
            os.makedirs(folder, exist_ok=True)
            filepath = os.path.join(folder, filename)
            
            # Обработка дубликатов
            counter = 1
            original_filepath = filepath
            while os.path.exists(filepath) and os.path.exists(filepath + '.part'):
                name, ext = os.path.splitext(original_filepath)
                filepath = f"{name}_{counter}{ext}"
                counter += 1
            
            temp_path = filepath + '.part'
            
            # Возобновление загрузки
            start_byte = 0
            if self.config.settings['resume'] and os.path.exists(temp_path):
                start_byte = os.path.getsize(temp_path)
            
            # Получаем общий размер
            total_size = self.get_file_size(task.url)
            if total_size > 0 and start_byte >= total_size:
                # Уже загружен полностью
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                task.status = 'completed'
                task.filepath = filepath
                return True
            
            # Заголовки для возобновления
            headers = {}
            if start_byte > 0:
                headers['Range'] = f'bytes={start_byte}-'
            
            # Загрузка
            with self.session.get(task.url, headers=headers, stream=True, timeout=self.config.settings['timeout']) as r:
                r.raise_for_status()
                
                # Определяем общий размер
                if 'Content-Range' in r.headers:
                    total_size = int(r.headers['Content-Range'].split('/')[-1])
                elif total_size == 0 and 'Content-Length' in r.headers:
                    total_size = int(r.headers['Content-Length'])
                    if start_byte > 0:
                        total_size += start_byte
                
                task.total = total_size
                task.filepath = filepath
                
                mode = 'ab' if start_byte > 0 else 'wb'
                with open(temp_path, mode) as f:
                    if HAS_TQDM:
                        pbar = tqdm(
                            total=total_size,
                            initial=start_byte,
                            unit='B',
                            unit_scale=True,
                            unit_divisor=1024,
                            desc=os.path.basename(filename)[:30],
                            leave=False
                        )
                    else:
                        pbar = None
                    
                    for chunk in r.iter_content(chunk_size=self.config.settings['chunk_size']):
                        if chunk:
                            f.write(chunk)
                            task.downloaded += len(chunk)
                            if pbar:
                                pbar.update(len(chunk))
                    
                    if pbar:
                        pbar.close()
            
            # Переименовываем временный файл
            os.replace(temp_path, filepath)
            task.status = 'completed'
            return True
            
        except Exception as e:
            task.status = 'error'
            task.error = str(e)
            return False
    
    def download_batch(self, urls, folder=None, callback=None):
        """Загружает список URL"""
        self.tasks = []
        for url in urls:
            task = DownloadTask(url, folder=folder)
            self.tasks.append(task)
        
        # Очередь задач
        task_queue = queue.Queue()
        for task in self.tasks:
            task_queue.put(task)
        
        # Рабочие потоки
        workers = []
        for i in range(min(self.config.settings['max_concurrent'], len(urls))):
            t = threading.Thread(target=self._worker, args=(task_queue, callback))
            t.daemon = True
            t.start()
            workers.append(t)
        
        # Ждем завершения
        task_queue.join()
        
        # Собираем статистику
        self.completed = sum(1 for t in self.tasks if t.status == 'completed')
        self.errors = sum(1 for t in self.tasks if t.status == 'error')
        
        return {
            'total': len(self.tasks),
            'completed': self.completed,
            'errors': self.errors,
            'tasks': self.tasks
        }
    
    def _worker(self, task_queue, callback):
        """Воркер для параллельных загрузок"""
        while True:
            try:
                task = task_queue.get(timeout=1)
            except queue.Empty:
                break
            
            try:
                success = self.download_single(task)
                if callback:
                    callback(task)
            finally:
                task_queue.task_done()
    
    def save_state(self, state_file='download_state.json'):
        """Сохраняет состояние загрузок для возобновления"""
        state = {
            'timestamp': datetime.now().isoformat(),
            'tasks': [
                {
                    'url': t.url,
                    'filepath': t.filepath,
                    'downloaded': t.downloaded,
                    'total': t.total,
                    'status': t.status
                }
                for t in self.tasks if t.status == 'downloading'
            ]
        }
        
        state_path = os.path.join(self.download_folder, state_file)
        try:
            with open(state_path, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

def read_urls_from_file(filepath):
    """Читает URLs из файла"""
    if not os.path.exists(filepath):
        return []
    
    with open(filepath, 'r', encoding='utf-8') as f:
        urls = []
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                urls.append(line)
        return urls

def main():
    """Основная функция"""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config = DownloaderConfig()
        
        # Файл с URLs
        urls_file = os.path.join(script_dir, 'urls.txt')
        urls = read_urls_from_file(urls_file)
        
        if not urls:
            # Создаем пример файла если его нет и настройка auto_create_urls
            if config.settings.get('auto_create_urls', True) and not os.path.exists(urls_file):
                with open(urls_file, 'w', encoding='utf-8') as f:
                    f.write('# Список URL для скачивания\n')
                    f.write('https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf\n')
                    f.write('https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/png/dummy.png\n')
            
            report = "Нет URL для загрузки. Добавьте их в urls.txt"
            write_report(report)
            print("NO_URLS")
            return
        
        # Загрузчик
        downloader = SmartDownloader(config)
        
        # Колбэк для прогресса
        def progress_callback(task):
            if task.total > 0:
                percent = (task.downloaded / task.total) * 100
                # Логируем прогресс
                log_file = os.path.join(downloader.download_folder, 'progress.log')
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(f"{datetime.now().strftime('%H:%M:%S')} - {os.path.basename(task.filepath or 'unknown')}: {percent:.1f}%\n")
        
        # Запускаем загрузку
        result = downloader.download_batch(urls, callback=progress_callback)
        
        # Отчет
        report_lines = [
            "=" * 60,
            "      ОТЧЕТ О ЗАГРУЗКЕ ФАЙЛОВ",
            "=" * 60,
            f"Начало: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
            f"Обработано: {result['total']}",
            f"Успешно: {result['completed']}",
            f"Ошибок: {result['errors']}",
            f"Папка загрузок: {downloader.download_folder}",
            "",
            "ДЕТАЛИ:"
        ]
        
        for task in result['tasks']:
            if task.status == 'completed':
                size = os.path.getsize(task.filepath) if task.filepath and os.path.exists(task.filepath) else 0
                report_lines.append(f"  Успешно: {task.url}")
                report_lines.append(f"     Файл: {os.path.basename(task.filepath)}")
                report_lines.append(f"     Размер: {size:,} байт")
            else:
                report_lines.append(f"  Ошибка: {task.url}")
                report_lines.append(f"     {task.error}")
        
        report_lines.append("=" * 60)
        report = "\n".join(report_lines)
        
        # Сохраняем отчеты
        write_report(report)
        
        # Сохраняем JSON отчет
        json_report = {
            'timestamp': datetime.now().isoformat(),
            'total': result['total'],
            'completed': result['completed'],
            'errors': result['errors'],
            'download_folder': downloader.download_folder,
            'tasks': [
                {
                    'url': t.url,
                    'filepath': t.filepath,
                    'status': t.status,
                    'error': t.error,
                    'downloaded': t.downloaded,
                    'total': t.total
                }
                for t in result['tasks']
            ]
        }
        
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        json_report_path = os.path.join(desktop, 'download_detailed_report.json')
        try:
            with open(json_report_path, 'w', encoding='utf-8') as f:
                json.dump(json_report, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
        
        print(f"COMPLETE:{result['completed']}/{result['total']}")
        
    except Exception as e:
        error_msg = f"Критическая ошибка: {str(e)}"
        write_report(error_msg)
        print(f"ERROR:{str(e)}")

def write_report(text):
    """Записывает отчет на рабочий стол"""
    try:
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        report_path = os.path.join(desktop, 'download_report.txt')
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(text)
    except Exception:
        pass

if __name__ == "__main__":
    main()