# Полная инструкция по установке The333-BGP

Версия: **0.1**

## 1. Назначение проекта

The333-BGP публикует выбранные IPv4-маршруты через BGP на MikroTik. Пользователь управляет маршрутами через портал:

- источники готовых списков маршрутов;
- сервисные модули по конкретным сервисам;
- ручные записи;
- Community-профили;
- история обновлений;
- диагностика BGP;
- подготовленный механизм обновлений.

## 2. Архитектура

```text
MikroTik  <---BGP--->  VM с The333-BGP
                         |
                         +-- the333-gobgp-core   : GoBGP speaker
                         +-- the333-bgp-backend  : API и расчёт маршрутов
                         +-- the333-portal       : веб-портал
```

Порты по умолчанию:

```text
179/tcp   BGP
8088/tcp  backend
8090/tcp  portal
```

## 3. Быстрая установка

```bash
curl -fsSL https://raw.githubusercontent.com/The333tech/The333-bgp/main/install.sh | bash
```

Если репозиторий называется иначе:

```bash
curl -fsSL https://raw.githubusercontent.com/The333tech/ИМЯ_РЕПОЗИТОРИЯ/main/install.sh \
  | THE333_REPO_SLUG=The333tech/ИМЯ_РЕПОЗИТОРИЯ bash
```

## 4. Что спросит установщик

Установщик интерактивный и защищён от типовых ошибок.

Он спросит:

| Параметр | Что значит |
|---|---|
| IP VM | IP, на котором портал и BGP будут доступны в сети |
| ASN сервиса | Local AS The333-BGP |
| Router ID | Обычно IP VM |
| BGP nexthop | Обычно IP VM |
| IP MikroTik/BGP peer | IP MikroTik |
| ASN MikroTik/BGP peer | ASN MikroTik |
| BGP community по умолчанию | Метка маршрутов по умолчанию |
| Пароль портала | Логин фиксированный `admin`; минимум 8 символов; можно оставить пустым, будет сгенерирован |
| GitHub update manifest URL | Можно оставить пустым до публикации GitHub |

## 5. Автоматическое определение IP

Скрипт определяет IP VM командой:

```bash
ip route get 1.1.1.1
```

Это берёт IP интерфейса, через который VM выходит в сеть. Значение можно заменить вручную во время установки.

## 6. Проверки защиты от ошибок

Установщик проверяет:

- валидность IPv4;
- валидность ASN;
- наличие Docker и Docker Compose plugin;
- занятость портов `179`, `8088`, `8090`;
- существующую установку в `/opt/the333-bgp`;
- наличие старого `.env`.

Если проект уже установлен, скрипт не затирает его молча, а предлагает:

```text
update / backup / status / quit
```

## 7. Файлы проекта

По умолчанию проект ставится в:

```text
/opt/the333-bgp
```

Основные директории:

```text
app/       backend
portal/    frontend
config/    поставляемые конфиги
data/      пользовательское состояние и runtime-данные
scripts/   служебные команды
backups/   резервные копии
```

В поставке уже есть базовые данные:

- `config/service_catalog.json` — стартовый каталог сервисных модулей;
- `config/service_candidates.seed.json` — найденные сервисы из Geosite для первого запуска;
- `config/default_sources.json` — стартовые источники маршрутов.

## 8. `.env`

Пример создаётся на основе `.env.example`.

Ключевые параметры:

```env
THE333_BIND_IP=192.168.1.111

LOCAL_AS=64500
ROUTER_ID=192.168.1.111
BGP_NEXTHOP=192.168.1.111
PEER_ADDRESS=192.168.1.1
PEER_AS=65455
BGP_COMMUNITY=65432:500

WEB_USER=admin
WEB_PASSWORD=
WEB_PASSWORD_HASH=pbkdf2_sha256:...
```

Не публикуй `.env` в GitHub.

## 9. Пароль портала

Портал использует одного администратора:

```text
admin
```

Пользователь вводит только пароль. Новые установки хранят hash пароля в `.env`:

```env
WEB_PASSWORD=
WEB_PASSWORD_HASH=pbkdf2_sha256:...
```

Минимальная длина пароля — **8 символов**. Если при установке оставить пароль пустым, installer сгенерирует сильный пароль автоматически.

Смена или восстановление пароля на VM:

```bash
cd /opt/the333-bgp
./scripts/the333bgp.sh set-password
```

Команда создаёт backup `.env`, записывает новый hash пароля и перезапускает только backend. GoBGP core при этом не перезапускается.

## 10. Запуск и проверка

```bash
cd /opt/the333-bgp
./scripts/the333bgp.sh status
```

Ожидаемый смысл результата:

```text
the333-gobgp-core    healthy
the333-bgp-backend   healthy
the333-portal        up
ready=true
gobgp_ready=true
```

Портал:

```text
http://IP_VM:8090
```

## 11. MikroTik

В портале есть вкладка **Для MikroTik**. Она генерирует команды под текущие параметры установки.

Базовая логика:

```text
remote.address = IP VM
remote.as      = ASN сервиса
local.as       = ASN MikroTik
```

После настройки BGP MikroTik должен получить маршруты от The333-BGP.

## 12. Обновление

Ручная безопасная команда:

```bash
cd /opt/the333-bgp
./scripts/the333bgp.sh update
```

Проверить доступную версию:

```bash
./scripts/the333bgp.sh check-update
```

Указать канал:

```bash
./scripts/the333bgp.sh check-update --channel stable
./scripts/the333bgp.sh check-update --channel beta
```

## 13. Как работает обновление

Обновлятор:

1. Загружает GitHub manifest.
2. Выбирает версию по каналу `stable` или `beta`.
3. Делает backup.
4. Загружает release archive.
5. Проверяет `sha256`, если он указан.
6. Обновляет код проекта.
7. Сохраняет пользовательские данные.
8. Собирает контейнеры.
9. Перезапускает сервисы.
10. Проверяет `ready`.

## 14. Что не перетирается при обновлении

Сохраняются:

- `.env`;
- `data/`;
- пользовательские источники;
- состояние сервисных модулей;
- Community-профили;
- DNS cache;
- история обновлений;
- last-good маршруты.

## 15. Обновление через портал

Страница **Обновления** уже умеет:

- показывать текущую версию;
- читать backend manifest API;
- показывать stable/beta версии;
- показывать changelog;
- объяснять, что host-updater пока выключен.

Включение запуска обновления из портала требует отдельного host-updater слоя.

В `.env`:

```env
PRODUCT_UPDATE_ENABLED=false
PRODUCT_UPDATE_COMMAND=/opt/the333-bgp/scripts/the333bgp.sh update --non-interactive
```

Для версии `0.1` запуск обновления из портала намеренно выключен, чтобы backend-контейнер не получал лишних прав на Docker-хост.

## 16. Backup

```bash
cd /opt/the333-bgp
./scripts/the333bgp.sh backup
```

Архив появится в:

```text
/opt/the333-bgp/backups
```

## 17. Восстановление

Пример:

```bash
cd /opt/the333-bgp
docker compose -f docker-compose.yml -f docker-compose.portal.yml down
tar -xzf backups/ИМЯ_BACKUP.tar.gz -C /opt/the333-bgp
docker compose -f docker-compose.yml -f docker-compose.portal.yml up -d --build
```

## 18. Подготовка GitHub-релиза

Для релиза нужны:

```text
VERSION
CHANGELOG.md
update-manifest.json
release archive
sha256 архива
```

Manifest должен быть доступен по raw URL:

```text
https://raw.githubusercontent.com/The333tech/The333-bgp/main/update-manifest.json
```

## 19. Диагностика

Проверка контейнеров:

```bash
cd /opt/the333-bgp
docker compose -f docker-compose.yml -f docker-compose.portal.yml ps
```

Проверка ready:

```bash
source .env
THE333_PORTAL_PASSWORD="пароль_портала" \
  curl -u "$WEB_USER:$THE333_PORTAL_PASSWORD" "http://$THE333_BIND_IP:8090/backend/ready"
```

Логи:

```bash
docker logs the333-bgp-backend --tail 100
docker logs the333-gobgp-core --tail 100
docker logs the333-portal --tail 100
```
