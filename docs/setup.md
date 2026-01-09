# Настройка RCord

## 1) Установка Python и зависимостей

### Требования
- Windows 10+ для клиента.
- Windows Server 2025 для сервера.
- Python 3.10+ (рекомендуется 3.11).

### Установка Python
1. Скачайте Python с официального сайта: <https://www.python.org/downloads/>.
2. Во время установки отметьте опцию **Add Python to PATH**.
3. Проверьте установку:

```powershell
python --version
```

### Зависимости
Проект работает на стандартной библиотеке Python. Для дополнительных функций можно установить:
- `sounddevice` — голосовой чат.
- `Pillow` — захват изображений.

Установка (необязательно):

```powershell
python -m pip install --upgrade pip
python -m pip install sounddevice pillow
```

## 2) Запуск сервера (Windows Server 2025)

### Размещение файлов
1. Скопируйте репозиторий на сервер, например в `C:\RCord`.
2. Перейдите в папку сервера:

```powershell
cd C:\RCord\server
```

### Настройка порта и firewall
Сервер использует два порта:
- `RCORD_PORT` (по умолчанию `8765`) — основной.
- `RCORD_MEDIA_PORT` (по умолчанию `8766`) — медиа.

Откройте порты в Windows Firewall (PowerShell от имени администратора):

```powershell
New-NetFirewallRule -DisplayName "RCord TCP 8765" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow
New-NetFirewallRule -DisplayName "RCord TCP 8766" -Direction Inbound -Protocol TCP -LocalPort 8766 -Action Allow
```

Если используете другие порты, откройте именно их.

### Размещение `DB.dat`
По умолчанию база создаётся в текущей директории сервера. Чтобы хранить её отдельно, задайте переменную `RCORD_DB_PATH`:

```powershell
$env:RCORD_DB_PATH = "C:\RCord\data\DB.dat"
```

Убедитесь, что папка существует и у процесса есть права записи:

```powershell
New-Item -ItemType Directory -Path "C:\RCord\data" -Force
```

### Запуск сервера
Можно задать хост и порты через переменные окружения:

```powershell
$env:RCORD_HOST = "0.0.0.0"
$env:RCORD_PORT = "8765"
$env:RCORD_MEDIA_PORT = "8766"
python .\main.py
```

После старта сервер создаст `DB.dat`, если его нет.

## 3) Запуск клиента (Windows 10+)

### Указание адреса сервера
Клиент берёт адрес и порты из переменных окружения:
- `RCORD_HOST` (например, IP сервера).
- `RCORD_PORT` (по умолчанию `8765`).
- `RCORD_MEDIA_PORT` (по умолчанию `8766`).

Пример (PowerShell):

```powershell
$env:RCORD_HOST = "192.168.1.10"
$env:RCORD_PORT = "8765"
$env:RCORD_MEDIA_PORT = "8766"
```

### Запуск

```powershell
cd C:\RCord\client
python .\main.py
```

### Первый запуск и регистрация
1. Откроется окно входа.
2. Введите имя пользователя и пароль.
3. Нажмите **Register** для создания аккаунта.
4. После регистрации используйте **Login** для входа.

Если сервер недоступен, проверьте:
- IP/порт в переменных окружения.
- Открыты ли порты на сервере.
- Запущен ли процесс сервера.
