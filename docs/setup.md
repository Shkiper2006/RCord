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

### Клиент

#### Требования
- Windows 10+.
- Python 3.10+ (рекомендуется 3.11).
- Tkinter (обычно идёт вместе с Windows-версией Python; при установке убедитесь, что выбран Tcl/Tk).

#### Рекомендуемые зависимости
Проект работает на стандартной библиотеке Python, но для дополнительных возможностей клиента стоит установить:
- `sounddevice` и `numpy` — голосовой чат.
- `Pillow` — захват экрана/изображений.

Установка (необязательно):

```powershell
python -m pip install --upgrade pip
python -m pip install sounddevice numpy pillow
```

#### Указание адреса сервера
Клиент берёт адрес и порты из:
1. GUI-экрана **Server Settings** (окно логина).
2. `settings.json` рядом с `client/main.py`.
3. Переменных окружения (если `settings.json` ещё не создан):
   - `RCORD_HOST` (например, IP сервера).
   - `RCORD_PORT` (по умолчанию `8765`).
   - `RCORD_MEDIA_PORT` (по умолчанию `8766`).

Пример (PowerShell):

```powershell
$env:RCORD_HOST = "192.168.1.10"
$env:RCORD_PORT = "8765"
$env:RCORD_MEDIA_PORT = "8766"
```

#### Запуск

```powershell
cd C:\RCord\client
python .\main.py
```

#### Walkthrough (первый запуск)
1. Откроется окно входа с блоком **Server Settings**.
2. Укажите IP/порт сервера (если не заданы через переменные окружения).
3. Введите имя пользователя и пароль.
4. Нажмите **Register** → регистрация аккаунта.
5. После регистрации нажмите **Login** → автологин в дальнейшем (клиент запоминает последнюю конфигурацию подключения).
6. В главном окне создайте комнату.
7. Отправьте приглашения другим пользователям.
8. Общайтесь в текстовом чате и подключайтесь к голосовому (если установлены `sounddevice` и `numpy`).
9. По завершении нажмите выход/закройте приложение.

#### Требования к сети и типичные ошибки подключения
- Откройте `RCORD_PORT` и `RCORD_MEDIA_PORT` на сервере (Firewall/антивирус).
- Убедитесь, что сервер слушает нужный интерфейс (`RCORD_HOST=0.0.0.0` для внешнего доступа).
- Проверьте, что у клиента указан внешний IP/домен сервера, а не `127.0.0.1`.
- Если подключение не устанавливается:
  - **Connection refused** — сервер не запущен или порт закрыт.
  - **Timed out** — неверный IP/порт, проблемы маршрутизации, блокировка фаерволом.
  - **Нет медиа** — `RCORD_MEDIA_PORT` не проброшен или не совпадает на клиенте/сервере.
