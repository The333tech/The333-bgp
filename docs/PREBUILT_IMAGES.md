# Предсобранные runtime-образы

> [!IMPORTANT]
> Режим `prebuilt` прошёл автоматическую установку с нуля на чистой Ubuntu VM, но остаётся явным opt-in до проверки с отдельным реальным MikroTik. Стандартный режим `source` сохраняется как независимый резервный путь.

The333-BGP поддерживает два явных режима доставки Docker images:

| Режим | Поведение | Когда использовать |
|---|---|---|
| `source` | GoBGP core, backend и portal собираются локально | Надёжный резервный путь, разработка и аудит собственной сборки |
| `prebuilt` | Готовые `amd64/arm64` images загружаются из GHCR по `sha256` digest | Быстрая установка с меньшим пиковым расходом диска |

Режимы не переключаются автоматически. Ошибка загрузки GHCR image завершает операцию до изменения контейнеров; установщик не начинает локальную сборку без явного выбора пользователя.

## Модель доверия

- release workflow собирает все три images из того же Git tag, что и release archive;
- каждому image назначается неизменяемая ссылка `ghcr.io/...@sha256:<digest>`;
- точные ссылки входят в `update-manifest.json` внутри проверенного SHA-256 release archive;
- images собираются для `linux/amd64` и `linux/arm64`;
- BuildKit публикует SBOM и provenance, GitHub Actions добавляет artifact attestation;
- Grype блокирует известные исправимые High/Critical уязвимости до публикации релиза;
- installer/updater принимают только три ожидаемых GHCR repository и полный lowercase SHA-256 digest.

## Обычная установка из релиза

Начиная с `v0.83b`, официальный релиз содержит раздел `images` в проверенном `update-manifest.json`. Пользователь запускает тот же интерактивный установщик одной строкой:

```bash
curl -fsSL https://raw.githubusercontent.com/The333tech/The333-bgp/main/install.sh \
  | THE333_IMAGE_MODE=prebuilt bash
```

Установщик сам загружает release archive, проверяет его SHA-256, извлекает из manifest три immutable digest-ссылки и задаёт обычные вопросы о VM, MikroTik и пароле портала. Вводить ссылки на images вручную не требуется.

## Проверка candidate-ветки

Candidate workflow предназначен для разработки до появления официального релиза. Он публикует временные теги `candidate-<commit-sha>` и сохраняет точные digest-ссылки как workflow artifacts. Только в этом сценарии ссылки передаются установщику явно:

```bash
THE333_IMAGE_MODE=prebuilt \
THE333_GOBGP_IMAGE='ghcr.io/the333tech/the333-bgp-core@sha256:...' \
THE333_BACKEND_IMAGE='ghcr.io/the333tech/the333-bgp-backend@sha256:...' \
THE333_PORTAL_IMAGE='ghcr.io/the333tech/the333-bgp-portal@sha256:...' \
bash ./install.sh
```

Для анонимной загрузки пакеты GHCR должны иметь видимость **Public**. При первом создании package GitHub может потребовать один раз открыть его настройки и выбрать `Change visibility -> Public`. Публичный GHCR image затем загружается без `docker login`.

## Возврат к локальной сборке

Для тестовой установки режим меняется только осознанно:

```bash
cd /opt/the333-bgp
./scripts/the333bgp.sh image-mode source
```

Команда создаёт backup `.env`, проверяет место, собирает source images, запускает health-check и автоматически восстанавливает предыдущий режим при ошибке. До отдельной проверки с реальным внешним MikroTik основной рабочий стенд следует оставлять в `source` mode.
