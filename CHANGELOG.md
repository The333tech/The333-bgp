# The333-BGP Changelog

## 0.82.1b - 2026-07-23

- Host-updater дополнительно защищён от выхода result-файлов за доверенный каталог: путь нормализуется и проверяется как непосредственный потомок фиксированного root, а временные имена больше не зависят от входного `request_id`.
- Добавлены тесты traversal, absolute path, separators, регистра/длины идентификатора и POSIX symlink replacement; внешний файл при атакующей подмене не изменяется.
- Публичный CodeQL повторно прошёл без открытых alerts; CI, secret scanning, push protection, Dependabot security updates и release attestations проверены на GitHub.
- Backend зависимости обновлены до FastAPI `0.139.2` и HTTPX2 `2.7.0` с полным hash-lock; portal обновлён до React `19.2.8`, Tabler Icons `3.45.0`, Vite `8.1.5` и Vite React plugin `6.0.4`.
- Runtime portal остаётся на Node.js 24 LTS; nginx обновлён внутри stable-линейки до `1.30.4`, а минимальный GoBGP runtime — до Alpine `3.24`, оба образа закреплены immutable multi-arch digest.
- SHA-pinned GitHub Actions, включая CodeQL, attest, checkout, setup-node, QEMU, Buildx и Docker build, обновлены до проверенных совместимых выпусков.
- Dependabot группирует minor/patch updates по экосистемам; переходы на major runtime и nginx mainline не предлагаются как обычное автоматическое обновление.
- GoBGP остаётся на актуальной проверенной версии `v4.7.0`; перезапуск host-updater на рабочей VM не прервал BGP-сессию, а global RIB и advertised routes сохранили одинаковые `16483` маршрута.

## 0.82b - 2026-07-12

- Фоновые product-update задачи получили durable-результат: перезапуск backend больше не превращает выполняющееся обновление в потерянную или фиктивно отменённую задачу.
- Обновление использует полный readiness-check backend/GoBGP/маршрутов/портала; rollback восстанавливает код, `.env`, `config` и `data` и проверяет предыдущую версию совместимым health-check.
- Перед update/repair Backend кратковременно останавливается для согласованного снимка `data/config`, затем обязательно запускается снова даже при ошибке архива; GoBGP и опубликованные маршруты при этом не перезапускаются.
- Исправлен stdin/JSON defect установщика, который приводил к `JSONDecodeError` при выборе версии из manifest.
- Fresh install и upgrade переведены на GitHub Releases API с поддержкой prerelease, SHA-256 release assets и безопасной распаковкой без path traversal, symlink и device-файлов.
- Добавлена идемпотентная миграция `.env`: пользовательские TLS/auth-настройки сохраняются, legacy manifest URL обновляется, TCP MD5 переносится в secret-файл.
- Portal, backend и GoBGP разделены по frontend/control/BGP-edge сетям; control network стала internal, portal работает без Linux capabilities под непривилегированным UID.
- GTSM/TTL security для прямого eBGP сделана явной двухсторонней opt-in настройкой: совместимый режим используется по умолчанию, а генератор MikroTik добавляет TTL-параметры только когда GTSM включена и на сервере, и на RouterOS.
- Для direct eBGP через Docker bridge введён явный `BGP_DOCKER_BRIDGE_HOPS`: MikroTik работает с `multihop=no`, а GoBGP автоматически учитывает ровно один внутренний container-to-LAN hop минимальным TTL `2`.
- Версия GoBGP core отделена от версии продукта: обычные portal/backend-релизы больше не должны пересоздавать routing-core.
- Атомарные записи состояния получили уникальные временные файлы, `fsync` файла и каталога; критические sources/catalog/state JSON теперь обрабатываются fail-closed.
- DNS-кэш Geosite обновляется пакетно вместо полного чтения и записи на каждый домен; ожидаемые DNS-сбои больше не засоряют логи stack trace.
- История маршрутов сохраняет выбранные источники, модули, итоговое количество и SHA-256 fingerprint набора; портал показывает реальное происхождение вместо «источник не указан».
- В состояние системы добавлены свободное место, порог безопасного обновления и понятное предупреждение о disk pressure.
- CI дополнен Docker runtime smoke-тестом, проверкой сетевой изоляции и Dependency Review; release workflow запрещает повторную публикацию уже существующего тега и перезапись его assets.
- Manifest и release archive загружаются во временные файлы только по HTTPS, включая redirects; добавлены лимиты 2 MiB/256 MiB и понятные ошибки без Python traceback при сетевом или JSON-сбое.
- Исправлен контракт `download_release`: вывод проверки SHA-256 больше не смешивается с путём распакованного релиза; поведение покрыто POSIX integration-тестом.
- Host-updater запускает дочерний update в минимальном системном окружении и каждый раз читает актуальный `.env`, поэтому удалённые CA/URL/secrets не остаются унаследованными от старого systemd-процесса.
- Атомарная миграция `.env` сохраняет UID/GID исходного файла и режим `0600`, поэтому обновление от root-service не лишает локального администратора доступа к CLI и Docker Compose.
- DNS-кэш модулей сервисов самовосстанавливается при повреждённом JSON и удаляет malformed, private, reserved и sinkhole-адреса до расчёта маршрутов.
- Полный update E2E через production Unix-socket updater завершён успешно: durable result сохранён, backend/portal заменены, GoBGP core не пересоздан и BGP-сессия не прерывалась.
- Автообновление маршрутов получило persistent-настройки в портале: включение, отключение и интервал в минутах применяются без рестарта и переживают обновление проекта.
- Добавлены автоматические системные бэкапы с расписанием, настраиваемым retention и проверкой fingerprint: неизменившееся состояние не дублируется новым архивом.
- Uptime трёх контейнеров передаётся через allowlist-ручку изолированного host-updater; Docker socket по-прежнему не монтируется в backend или portal.
- На странице маршрутов файл выбранного набора можно скачать, а блок «Подключения» больше не дублирует настройку автообновления.
- Community-профили получили точный генератор входного Large Community filter для RouterOS v7, выбор имени BGP connection, Safe Mode warning, проверку по реальному `session prefix-count` и команды rollback.
- Генератор Community-команд теперь проверяет, что BGP connection найден в единственном экземпляре, до изменения фильтров; интерфейс объясняет назначение защитных private/reserved reject-правил.
- Блок GoBGP в «Подключениях» расширен до технической диагностики: global, подробное состояние соседа, таймеры, capabilities, статистика сообщений, advertised routes и сверка RIB/last-good.
- Настройки автоматических бэкапов перенесены в нижнюю часть модального окна, получили нейтральное оформление, явную кнопку включения и компактные числовые поля.
- Атомарный мигратор `.env` сохраняет строгие POSIX-права на Linux и корректно проходит release-тесты на Windows, где `fchmod` недоступен.
- Исправлены scrollbar блоков Dashboard и статусы страницы обновлений: установленная актуальная версия теперь явно показывает «Обновление не требуется».
- Успешный служебный код `75` host-updater объявлен в systemd как `SuccessExitStatus`, поэтому штатный self-restart больше не выглядит как отказ сервиса.
- Backend/security suite обнаруживает **104 теста** в актуальном Python 3.14 image; 9 POSIX update/installer сценариев, пропускаемых в Alpine без Bash, отдельно проходят на Linux host. Frontend production build, MikroTik logic tests и npm audit также проходят без ошибок.
- Документация GitHub дополнена правилами для contributors и приватным процессом отправки отчётов об уязвимостях без публикации чувствительных деталей в Issues.

## 0.78 beta - 2026-07-09

- Beta-релиз перед первым stable-релизом: проект находится в активной разработке, рабочий стенд больше месяца публикует маршруты в MikroTik.
- GoBGP обновлён до `v4.7.0`; core/backend images собраны и проверены на этой версии.
- Backend runtime обновлён до Python 3.14 с hash-locked dependencies; FastAPI, Uvicorn, Pydantic и HTTPX2 обновлены.
- Portal runtime обновлён до Node 24/nginx 1.30; frontend dependencies закреплены exact versions.
- Добавлены и проверены session auth, CSRF, rate limiting, TLS overlay, безопасный reset password flow.
- Updater вынесен в host-side systemd service через Unix socket; Docker socket не монтируется в containers.
- Применение маршрутов стало транзакционным: добавление до удаления, rollback при ошибке, сериализация операций и восстановление last-good.
- Внешние источники защищены HTTPS-only, SSRF/private address validation, redirect validation, response size limit и verified cache fallback.
- Встроенный каталог сервисов отделён от пользовательского каталога, чтобы обновления не перетирали выбор пользователя.
- Добавлены production checks: digest-pinned base images, pinned GitHub Actions, SBOM, attestations, CVE gates и release metadata tests.
- Подготовлен отдельный experimental `docker-awg` candidate на `amneziawg-go v0.2.19` и `amneziawg-tools v1.0.20260618-2`.
- Обновлён digest Go builder image; CI/Release теперь блокируют исправимые Critical CVE, а scanner выводит таблицу findings для triage.
- Логи auth proxy больше не выводят некорректные значения `AUTH_TRUSTED_PROXY_CIDRS`, чтобы не раскрывать содержимое переменных окружения.
- Проверено: backend/security suite **62/62** внутри Python 3.14 image, installer upgrade-flow на Linux host, `release-check.sh`, portal build/test/audit и GoBGP `4.7.0`.

## 0.1 - 2026-07-03

- Первый публичный релиз The333-BGP.
- Портал управления источниками маршрутов, модулями сервисов, Community-профилями, диагностикой, историей и резервными копиями.
- Раздельные контейнеры `the333-gobgp-core`, `the333-bgp-backend` и `the333-portal`; updater вынесен в изолированный systemd service без Docker socket внутри контейнеров.
- Интерактивная установка с определением IP VM, проверкой BGP-параметров, Docker и занятых портов.
- Обновление через портал и CLI с pre-update backup, SHA-256 release-архивов, проверкой готовности и автоматическим rollback файлов.
- Авторизация одним паролем: PBKDF2-хеш на сервере, rate limiting и отсутствие пароля в `localStorage` браузера.
- Защита файловых операций бэкапа allowlist-проверками и безопасные публичные сообщения об ошибках.
- Workflows CI и CodeQL, dependency audit и unit-тесты перед публикацией релиза.
