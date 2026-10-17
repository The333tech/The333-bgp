#!/usr/bin/env python3
"""Inspect storage or retire only this installation's recorded update snapshots."""

import argparse
import json
import os
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.storage_retention import Docker, Retention, StorageError, operation_lock, storage_report


def show_plan(plan: dict) -> None:
    print(f"Сохранить проверенных комплектов отката: {plan['keep']}")
    for name in plan["protected"]:
        print(f"  Оставить: {name}")
    for item in plan["files"]:
        print(f"  Удалить архив: {item['name']} ({item['bytes']} байт)")
    print(f"Объём архивов в списке удаления: {plan['archive_bytes']} байт")
    for image in plan["images"]:
        print(f"  Удалить свободный образ: {', '.join(image['references'])} (логический размер {image['logical_bytes']} байт)")
    print(plan["image_space_note"])
    for message in [*plan["blockers"], *plan["skipped"]]:
        print(f"  Оставлено без изменений: {message}")
    if plan["unmanaged"]:
        print(f"Неучтённые/ручные файлы: {len(plan['unmanaged'])}; автоматически не удаляются.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("report", "cleanup", "record", "complete"))
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--target-version")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--docker-sudo", action="store_true")
    args = parser.parse_args()
    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run are mutually exclusive")
    project = Path(os.environ.get("THE333_PROJECT_DIR", "/opt/the333-bgp"))
    try:
        retention = Retention(project, Docker(["sudo", "-n", "docker"] if args.docker_sudo else None),
                              int(os.environ.get("UPDATE_BACKUP_RETENTION", "2")))
        if args.command == "report":
            report = storage_report(retention)
            if args.json:
                print(json.dumps(report, ensure_ascii=False))
            else:
                for label, key in (("Файлы проекта без архивов", "project_files"), ("Архивы обновлятора", "update_backups"),
                                   ("ZIP-бэкапы портала", "portal_backups"), ("Файловая система проекта", "project_filesystem"),
                                   ("Файловая система Docker", "docker_filesystem")):
                    print(f"{label}: {json.dumps(report[key], ensure_ascii=False)}")
                print(f"Docker Root Dir: {report['docker_root']}")
                for image in report["tracked_images"]:
                    print(f"Образ: {', '.join(image['references'])}; логический размер {image['logical_bytes']} байт")
                show_plan(report["cleanup"])
            return 0
        if args.command in {"record", "complete"}:
            if args.archive is None or (args.command == "record" and not args.target_version):
                parser.error("internal snapshot operation requires --archive and, for record, --target-version")
            with operation_lock(project, internal=True):
                if args.command == "record":
                    retention.capture(args.archive, args.target_version)
                else:
                    retention.complete(args.archive)
                    plan = retention.plan()
                    show_plan(plan)
                    if not plan["blockers"]:
                        print(json.dumps(retention.apply(plan), ensure_ascii=False))
            return 0
        plan = retention.plan()
        if not args.apply:
            print(json.dumps(plan, ensure_ascii=False)) if args.json else show_plan(plan)
            return 0
        if not args.non_interactive:
            if args.json or not sys.stdin.isatty():
                parser.error("interactive cleanup requires a terminal; use --apply --non-interactive explicitly")
            show_plan(plan)
            if input("Применить именно этот список? Введи YES: ") != "YES":
                print("Отменено. Файлы и образы не удалялись.")
                return 0
        with operation_lock(project):
            result = retention.apply(plan)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, StorageError) as exc:
        print(f"Обслуживание хранилища не выполнено: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
