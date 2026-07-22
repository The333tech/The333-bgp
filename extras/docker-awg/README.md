# docker-awg candidate

Это отдельная воспроизводимая сборка AmneziaWG-клиента для RouterOS Containers.

**Статус:** experimental candidate для лабораторной проверки. Основной installer The333-BGP его не устанавливает и рабочий MikroTik автоматически не изменяет.

## Закреплённые компоненты

- `amneziawg-go v0.2.19`, commit `1cc94272ca8e9e223a5fe76382f5880f09d3c12d`;
- `amneziawg-tools v1.0.20260618-2`, commit `61e741780e8465a67a7d7fb6cffe14a8a15d624a`;
- `golang:1.26.5-alpine3.24` и `alpine:3.24` по OCI digest;
- direct Alpine packages по точным версиям.

Поддерживаемые build targets: `linux/arm/v7`, `linux/arm64` и `linux/amd64`.

## Что делает image

1. Читает один или несколько файлов `/etc/amnezia/amneziawg/*.conf`.
2. Поднимает каждый интерфейс через `awg-quick`.
3. Добавляет только собственные IPv4 masquerade rules, не очищая весь firewall namespace.
   IPv6 NAT выключен по умолчанию и включается только через `AWG_ENABLE_IPV6_NAT=true`.
4. Корректно опускает интерфейсы и удаляет свои NAT rules по `SIGTERM`.
5. Никогда не печатает содержимое конфигурации или private keys.

Конфигурация передаётся только внешним RouterOS mount. Она не входит в image, GitHub artifact, SBOM или логи сборки.

## Сборка

Ручной workflow `Build docker-awg candidate` создаёт отдельные RouterOS-compatible tar archives, SPDX SBOM и SHA256SUMS. Для public workflow дополнительно создаются provenance/SBOM attestations.

Артефакт нельзя считать production-ready до проверки на отдельном MikroTik, полного backup/rollback теста и подтверждения BGP + AWG traffic path.

## Лицензии

- `amneziawg-go`: MIT;
- `amneziawg-tools`: GPL-2.0;
- Alpine packages: собственные лицензии пакетов.

Тексты upstream-лицензий включаются в image. Код неизвестного лицензирования из `catesin/AmneziaWG-MikroTik` не копируется.
