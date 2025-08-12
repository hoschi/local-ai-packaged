import subprocess
import datetime
import os
import shutil
import argparse

"""
" Usage:
" python3 n8n/n8n_backup.py --host-path /Users/hoschi/repos/local-ai-packaged/n8n/user_backup n8n export --description 'main-instance'
" python3 n8n/n8n_backup.py --host-path /Users/hoschi/repos/local-ai-packaged/n8n/user_backup n8n import backup_20250812_100748_main_instance
" https://aistudio.google.com/prompts/1J7fFba4vXBjYd5eF_KvUgXkjB5c-1ukH
"""

def run_docker_command(container_name, command_args, capture_output=False, use_n8n_prefix=True, user=None):
    if isinstance(command_args, str):
        command_args = command_args.split()
    base_cmd = ["docker", "exec"]
    if user:
        base_cmd.extend(["-u", user])
    base_cmd.append(container_name)
    if use_n8n_prefix:
        cmd = base_cmd + ["n8n"] + command_args
        print(f"Führe im Container (n8n CLI, als Benutzer '{user or 'default'}') aus: {' '.join(cmd)}")
    else:
        cmd = base_cmd + command_args
        print(f"Führe im Container (Shell, als Benutzer '{user or 'default'}') aus: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd, check=True, capture_output=capture_output, text=True, encoding='utf-8'
        )
        if capture_output:
            return result.stdout.strip()
        return None
    except subprocess.CalledProcessError as e:
        print(f"Fehler beim Ausführen des Befehls im Container: {e}")
        stdout_msg = e.stdout.strip() if e.stdout else "[Keine Standardausgabe]"
        stderr_msg = e.stderr.strip() if e.stderr else "[Keine Standardfehlerausgabe]"
        print(f"Stdout (falls vorhanden): {stdout_msg}")
        print(f"Stderr: {stderr_msg}")
        raise
    except Exception as e:
        print(f"Ein unerwarteter Fehler ist aufgetreten: {e}")
        raise

def run_host_command(command, capture_output=False, shell=True):
    print(f"Führe auf Host aus: {command}")
    try:
        result = subprocess.run(
            command, shell=shell, check=True, capture_output=capture_output, text=True, encoding='utf-8'
        )
        if capture_output:
            return result.stdout.strip()
        return None
    except subprocess.CalledProcessError as e:
        print(f"Fehler beim Ausführen des Host-Befehls: {e}")
        stdout_msg = e.stdout.strip() if e.stdout else "[Keine Standardausgabe]"
        stderr_msg = e.stderr.strip() if e.stderr else "[Keine Standardfehlerausgabe]"
        print(f"Stdout (falls vorhanden): {stdout_msg}")
        print(f"Stderr: {stderr_msg}")
        raise
    except Exception as e:
        print(f"Ein unerwarteter Fehler ist aufgetreten: {e}")
        raise

def export_n8n_data(container_name, host_backup_base_path, backup_description=None):
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir_name = f"backup_{timestamp}"
    if backup_description:
        sanitized_description = "".join(c if c.isalnum() or c in (' ', '_') else '_' for c in backup_description).replace(' ', '_')
        backup_dir_name += f"_{sanitized_description}"
    host_destination_path = os.path.join(host_backup_base_path, backup_dir_name)
    try:
        container_temp_dir = run_docker_command(container_name, "mktemp -d", capture_output=True, use_n8n_prefix=False)
    except Exception as e:
        print(f"Konnte temporäres Verzeichnis im Container nicht erstellen: {e}")
        return
    print(f"Temporäres Export-Verzeichnis im Container: {container_temp_dir}")
    print("Exportiere Workflows im Container...")
    run_docker_command(container_name, f"export:workflow --backup --output={container_temp_dir} --pretty")
    print("Exportiere Anmeldeinformationen im Container...")
    run_docker_command(container_name, f"export:credentials --backup --output={container_temp_dir} --pretty")
    os.makedirs(host_destination_path, exist_ok=True)
    print(f"Kopiere Backup-Daten von Container '{container_name}:{container_temp_dir}' nach Host '{host_destination_path}'...")
    run_host_command(f"docker cp {container_name}:{container_temp_dir}/. '{host_destination_path}'")
    print(f"Lösche temporäres Verzeichnis im Container: {container_temp_dir}")
    run_docker_command(container_name, f"rm -rf {container_temp_dir}", use_n8n_prefix=False, user='root')
    print(f"Backup erfolgreich erstellt unter {host_destination_path} auf dem Host.")
    return host_destination_path

def import_n8n_data(container_name, host_backup_base_path, backup_folder_name):
    """
    Importiert n8n-Daten mit einer tar-Pipe, um Berechtigungsprobleme zu umgehen.
    """
    host_source_path = os.path.join(host_backup_base_path, backup_folder_name)
    if not os.path.isdir(host_source_path):
        print(f"Fehler: Backup-Verzeichnis '{host_source_path}' auf dem Host nicht gefunden.")
        return

    print("Erstelle temporäres Import-Verzeichnis im Container als Benutzer 'node'...")
    try:
        # Da der Standardbenutzer 'node' ist, wird das Verzeichnis mit den richtigen Berechtigungen erstellt.
        container_temp_dir = run_docker_command(
            container_name,
            "mktemp -d",
            capture_output=True,
            use_n8n_prefix=False
        )
    except Exception as e:
        print(f"Konnte temporäres Verzeichnis im Container nicht erstellen: {e}")
        return

    print(f"Temporäres Import-Verzeichnis im Container: {container_temp_dir}")

    try:
        # Absoluten Pfad für Zuverlässigkeit verwenden
        host_abs_path = os.path.abspath(host_source_path)

        # Diese Pipe erstellt einen tar-Stream auf dem Host und leitet ihn an einen tar-Befehl im Container weiter.
        # Der Befehl im Container läuft als 'node'-Benutzer und erstellt die Dateien mit der korrekten Eigentümerschaft.
        pipe_command = f"tar -c -C '{host_abs_path}' . | docker exec -i {container_name} tar -x -C {container_temp_dir}"

        print("Übertrage Backup-Daten via tar-Stream...")
        run_host_command(pipe_command)
        print("Datenübertragung erfolgreich.")

        print("Importiere Workflows im Container...")
        run_docker_command(
            container_name,
            f"import:workflow --separate --input={container_temp_dir}"
        )

        print("Importiere Anmeldeinformationen im Container...")
        run_docker_command(
            container_name,
            f"import:credentials --separate --input={container_temp_dir}"
        )

    finally:
        # Stellt sicher, dass das temporäre Verzeichnis auch bei einem Fehler gelöscht wird.
        print(f"Lösche temporäres Verzeichnis im Container: {container_temp_dir}")
        run_docker_command(container_name, f"rm -rf {container_temp_dir}", use_n8n_prefix=False)

    print(f"\nDaten erfolgreich importiert aus {host_source_path} auf dem Host.")

def cleanup_old_backups(host_backup_base_path, retention_hours=24):
    print(f"Bereinige alte Backups auf dem Host, die älter als {retention_hours} Stunden sind...")
    if not os.path.isdir(host_backup_base_path):
        return
    now = datetime.datetime.now()
    deleted_count = 0
    for dir_name in os.listdir(host_backup_base_path):
        full_path = os.path.join(host_backup_base_path, dir_name)
        if not os.path.isdir(full_path) or not dir_name.startswith("backup_"):
            continue
        try:
            parts = dir_name.split('_')
            if len(parts) >= 3 and parts[0] == "backup":
                timestamp_str = f"{parts[1]}_{parts[2]}"
                backup_time = datetime.datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                if (now - backup_time).total_seconds() > retention_hours * 3600:
                    shutil.rmtree(full_path)
                    deleted_count += 1
        except (ValueError, IndexError) as e:
            continue
    print(f"{deleted_count} alte Backups auf dem Host bereinigt.")

def main():
    parser = argparse.ArgumentParser(
        description="Python-Skript zur Verwaltung von n8n-Daten-Backups mit Docker.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--host-path", default="./n8n_backups", help="Basispfad für Backups (Standard: './n8n_backups').")
    parser.add_argument("container_name", help="Der Name des n8n Docker-Containers.")
    subparsers = parser.add_subparsers(dest="command", required=True, help="Verfügbare Befehle")
    export_parser = subparsers.add_parser("export", help="Exportiert n8n-Daten auf den Host.")
    export_parser.add_argument("-d", "--description", help="Optionale Beschreibung für das Backup.")
    export_parser.add_argument("-r", "--retention-hours", type=int, default=24, help="Aufbewahrungsdauer für Backups in Stunden (Standard: 24).")
    import_parser = subparsers.add_parser("import", help="Importiert n8n-Daten vom Host.")
    import_parser.add_argument("backup_folder_name", help="Name des Backup-Ordners innerhalb des --host-path.")
    args = parser.parse_args()
    try:
        if args.command == "export":
            exported_path = export_n8n_data(args.container_name, args.host_path, args.description)
            if exported_path:
                cleanup_old_backups(args.host_path, args.retention_hours)
                print("Backup-Prozess abgeschlossen.")
        elif args.command == "import":
            import_n8n_data(args.container_name, args.host_path, args.backup_folder_name)
            print("Import-Prozess abgeschlossen.")
    except Exception as e:
        print(f"\nSkriptausführung fehlgeschlagen: {e}")

if __name__ == "__main__":
    main()
