# The333-BGP Changelog

## 0.78 beta - 2026-07-09

- Beta-релиз перед первым stable-релизом: проект находится в активной разработке, рабочий стенд больше месяца публикует маршруты в MikroTik.
- GoBGP обновлён до `v4.7.0`; core/backend images собраны и проверены на этой версии.
- Backend runtime обновлён до Python 3.14 с hash-locked dependencies; FastAPI/Uvicorn/Pydantic HTTPX2 обновлены.
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
