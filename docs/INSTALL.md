# Установка The333-BGP

Версия: **v0.1 stable**

## 1. Что делает The333-BGP

The333-BGP публикует выбранные IPv4-маршруты через BGP на MikroTik. Управление идёт через веб-портал:

- источники готовых списков маршрутов;
- модули сервисов по конкретным сервисам;
- ручные записи;
- Community-профили;
- история обновлений и событий;
- диагностика BGP;
- резервные копии;
- обновления проекта.

## 2. Архитектура

```text
MikroTik  <---BGP--->  VM с The333-BGP
                         |
                         +-- the333-gobgp-core     : GoBGP speaker
                         +-- the333-bgp-backend    : API и расчёт маршрутов
                         +-- the333-host-updater   : обновление проекта
                         +-- the333-portal         : веб-портал
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

## 4. Что спросит установщик

Установщик интерактивный и проверяет типовые ошибки.

| Параметр | Что значит |
|---|---|
| IP VM | IP, на котором портал и BGP будут доступны в сети |
| ASN сервиса | Local AS The333-BGP |
| Router ID | Обычно IP VM |
| BGP nexthop | Обычно IP VM |
| IP MikroTik/BGP peer | IP MikroTik |
| ASN MikroTik/BGP peer | ASN MikroTik |
| BGP community по умолчанию | Метка маршрутов по умолчанию |
| Пароль портала | Логин фиксированный `admin`; минимум 8 символов; можно оставить пустым |

Если пароль оставить пустым, установщик сгенерирует его автоматически.

## 5. Автоматическое определение IP

Скрипт определяет IP VM командой:

```bash
ip route get 1.1.1.1
```

Это берёт IP интерфейса, через который VM выходит в сеть. Значение можно заменить вручную во время установки.

## 6. Проверки перед запуском

Установщик проверяет:

- валидность IPv4;
- валидность ASN;
- наличие Docker и Docker Compose plugin;
- занятость портов `179`, `8088`, `8090`;
- существующую установку в `/opt/the333-bgp`;
- наличие старого `.env`.

Если Docker уже установлен, установщик пропускает этот этап.

Если проект уже установлен, скрипт предложит:

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
backups/   резервные копии обновлятора
```

В поставке уже есть стартовые данные:

- `config/service_catalog.json` — каталог модулей сервисов;
- `config/service_candidates.seed.json` — найденные сервисы из Geosite;
- `config/default_sources.json` — стартовые источники маршрутов.

## 8. Портал

После установки открой:

```text
http://IP_VM:8090
```

Логин:

```text
admin
```

Пароль — тот, который был задан или сгенерирован установщиком.

## 9. MikroTik

В портале есть вкладка **Для MikroTik**. Она генерирует команды под текущие параметры установки.

Базовая логика:

```text
remote.address = IP VM
remote.as      = ASN сервиса
local.as       = ASN MikroTik
```

После настройки BGP MikroTik должен получить маршруты от The333-BGP.

## 10. Управление на VM

```bash
cd /opt/the333-bgp

./scripts/the333bgp.sh status
./scripts/the333bgp.sh backup
./scripts/the333bgp.sh check-update
./scripts/the333bgp.sh update
./scripts/the333bgp.sh set-password
```

## 11. Проверка состояния

```bash
cd /opt/the333-bgp
./scripts/the333bgp.sh status
```

Ожидаемый смысл результата:

```text
the333-gobgp-core      healthy
the333-bgp-backend     healthy
the333-host-updater    healthy
the333-portal          up
ready=true
gobgp_ready=true
```

## 12. Обновления

Обновления доступны в портале на странице **Обновления**.

Портал:

1. проверяет доступные версии;
2. показывает changelog;
3. создаёт backup перед обновлением;
4. загружает новую версию;
5. сохраняет пользовательские данные;
6. пересобирает контейнеры;
7. проверяет готовность сервиса.

Ручное обновление на VM:

```bash
cd /opt/the333-bgp
./scripts/the333bgp.sh update
```

Проверка доступной версии:

```bash
./scripts/the333bgp.sh check-update
```

Выбор канала:

```bash
./scripts/the333bgp.sh check-update --channel stable
./scripts/the333bgp.sh check-update --channel beta
```

## 13. Что сохраняется при обновлении

Сохраняются:

- `.env`;
- `data/`;
- пользовательские источники;
- состояние модулей сервисов;
- Community-профили;
- DNS cache;
- история обновлений;
- last-good маршруты.

## 14. Backup и восстановление

В портале есть кнопка **Бэкап**. Через неё можно создать backup, скачать архив, удалить лишний архив или восстановиться.

CLI backup:

```bash
cd /opt/the333-bgp
./scripts/the333bgp.sh backup
```

Архивы обновлятора хранятся в:

```text
/opt/the333-bgp/backups
```

Системные backup-архивы портала хранятся в:

```text
/opt/the333-bgp/data/system_backups
```

## 15. Смена пароля

```bash
cd /opt/the333-bgp
./scripts/the333bgp.sh set-password
```

Команда создаёт backup `.env`, записывает новый hash пароля и перезапускает только backend.

## 16. Диагностика

Проверка контейнеров:

```bash
cd /opt/the333-bgp
docker compose -f docker-compose.yml -f docker-compose.portal.yml ps
```

Логи:

```bash
docker logs the333-bgp-backend --tail 100
docker logs the333-gobgp-core --tail 100
docker logs the333-host-updater --tail 100
docker logs the333-portal --tail 100
```

## 17. Безопасность

The333-BGP рассчитан на локальную сеть рядом с MikroTik.

**Не открывай `8090`, `8088` и `179` напрямую в Интернет.**

Для удалённого доступа используй VPN, firewall allowlist или reverse proxy с TLS.
