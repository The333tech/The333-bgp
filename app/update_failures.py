"""Allowlisted update diagnostics; never expose captured command output to the portal."""

from pathlib import Path

from app.maintenance import read_state, write_state


FAILURES = {
    "disk_space": (
        "Недостаточно свободного места для безопасного обновления.",
        "Освободи место на диске VM, сохранив рабочие данные и резервные копии. Затем повтори обновление.",
    ),
    "disk_check": (
        "Не удалось проверить свободное место на VM.",
        "Проверь доступность диска проекта и хранилища Docker.",
    ),
    "release_download": (
        "Не удалось загрузить сведения о релизе или архив обновления.",
        "Проверь доступ VM к GitHub, DNS и интернету. Затем повтори обновление.",
    ),
    "release_integrity": (
        "Контрольная сумма архива не совпала. Установка остановлена для безопасности.",
        "Повтори загрузку. Если ошибка повторяется, сообщи разработчику; не отключай проверку архива.",
    ),
    "release_invalid": (
        "Файлы релиза не прошли проверку формата или содержимого.",
        "Проверь, что выбран официальный релиз. Если ошибка повторяется, сообщи разработчику.",
    ),
    "project_permissions": (
        "Updater не может записать файлы в каталог проекта.",
        "Проверь владельца и права каталога установки. Не выдавай доступ всем пользователям.",
    ),
    "backup_failed": (
        "Не удалось подготовить проверенную резервную копию перед обновлением.",
        "Проверь свободное место и доступность каталога резервных копий. Не удаляй единственную рабочую копию.",
    ),
    "image_prepare": (
        "Не удалось загрузить или подготовить образы контейнеров.",
        "Проверь место на диске и доступ VM к реестру образов ghcr.io. Подробности есть в журнале операции.",
    ),
    "runtime_readiness": (
        "Сервисы новой версии не прошли проверку запуска и готовности.",
        "Проверь результат восстановления предыдущей версии и журнал операции перед новой попыткой.",
    ),
    "update_timeout": (
        "Превышено допустимое время обновления.",
        "Проверь доступность VM и журнал операции. Не запускай второе обновление, пока состояние не проверено.",
    ),
}


def safe_failure(value: object) -> dict | None:
    if not isinstance(value, dict) or not isinstance(value.get("code"), str) or value["code"] not in FAILURES:
        return None
    result = {"code": value["code"]}
    if value["code"] == "disk_space":
        free, required = value.get("free_bytes"), value.get("required_bytes")
        if type(free) is int and type(required) is int and 0 <= free < required <= 2**63 - 1:
            result.update(free_bytes=free, required_bytes=required)
    return result


def record_failure(directory: Path, code: str, free_bytes: int | None = None,
                   required_bytes: int | None = None) -> None:
    failure = safe_failure({"code": code, "free_bytes": free_bytes, "required_bytes": required_bytes})
    if failure is None:
        raise ValueError("unknown update failure code")
    state = read_state(directory)
    if state.get("status") != "running":
        raise ValueError("there is no running update")
    state["failure"] = failure
    write_state(directory, state)


def public_failure(operation: dict) -> dict | None:
    if operation.get("status") not in {"failed", "rolled_back", "recovery_required"}:
        return None
    failure = safe_failure(operation.get("failure"))
    if failure is None:
        return None
    message, action = FAILURES[failure["code"]]
    if "free_bytes" in failure:
        unit = 1024 * 1024
        free = failure["free_bytes"] // unit
        required = (failure["required_bytes"] + unit - 1) // unit
        missing = (failure["required_bytes"] - failure["free_bytes"] + unit - 1) // unit
        message = f"Недостаточно места: свободно {free} МБ, требуется не менее {required} МБ."
        action = f"Освободи ещё не менее {missing} МБ. Сохрани рабочие данные и резервные копии. Затем повтори обновление."
    if operation.get("status") == "recovery_required":
        action = "Не запускай обновление повторно. Проверь VM и журнал операции; изменения заблокированы до проверки состояния сервисов."
    return {"code": failure["code"], "message": message, "action": action}
