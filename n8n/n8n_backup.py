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

def run_docker_command(container_name, command_args, capture_output=False, use_n8n_prefix=True):
    if isinstance(command_args, str):
        command_args = command_args.split()
    cmd = ["docker", "exec", container_name]
    if use_n8n_prefix:
        cmd.append("n8n")
    cmd.extend(command_args)
    print(f"Führe im Container aus: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, check=True, capture_output=capture_output, text=True, encoding='utf-8')
        return result.stdout.strip() if capture_output else None
    except subprocess.CalledProcessError as e:
        print(f"Fehler beim Ausführen des Befehls im Container: {e}")
        print(f"Stderr: {e.stderr.strip() if e.stderr else '[Keine Ausgabe]'}")
        raise

def run_host_command(command, shell=True):
    print(f"Führe auf Host aus: {command}")
    try:
        subprocess.run(command, shell=shell, check=True, text=True, encoding='utf-8')
    except subprocess.CalledProcessError as e:
        print(f"Fehler beim Ausführen des Host-Befehls: {e}")
        print(f"Stderr: {e.stderr.strip() if e.stderr else '[Keine Ausgabe]'}")
        raise

def export_n8n_data(container_name, host_backup_base_path, backup_description=None):
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    sanitized_description = "".join(c for c in backup_description if c.isalnum() or c in (' ', '_')).replace(' ', '_') if backup_description else ""
    backup_dir_name = f"backup_{timestamp}_{sanitized_description}" if sanitized_description else f"backup_{timestamp}"
    host_destination_path = os.path.join(host_backup_base_path, backup_dir_name)

    # Unterverzeichnisse für saubere Trennung erstellen
    host_workflows_path = os.path.join(host_destination_path, "workflows")
    host_credentials_path = os.path.join(host_destination_path, "credentials")
    os.makedirs(host_workflows_path, exist_ok=True)
    os.makedirs(host_credentials_path, exist_ok=True)

    print(f"Erstelle Backup in {host_destination_path}")

    for data_type in ["workflow", "credentials"]:
        print(f"Exportiere {data_type}s...")
        container_temp_dir = run_docker_command(container_name, "mktemp -d", capture_output=True, use_n8n_prefix=False)
        try:
            run_docker_command(container_name, f"export:{data_type} --backup --output={container_temp_dir} --pretty")

            host_target_path = host_workflows_path if data_type == "workflow" else host_credentials_path

            # Die Anführungszeichen sind wichtig für Pfade mit Leerzeichen
            run_host_command(f"docker cp '{container_name}:{container_temp_dir}/.' '{host_target_path}/'")
            print(f"{data_type.capitalize()}s erfolgreich nach '{host_target_path}' exportiert.")
        finally:
            run_docker_command(container_name, f"rm -rf {container_temp_dir}", use_n8n_prefix=False)

    print("\nBackup erfolgreich erstellt.")
    return host_destination_path

def import_n8n_data(container_name, host_backup_base_path, backup_folder_name):
    host_source_path = os.path.join(host_backup_base_path, backup_folder_name)
    host_workflows_path = os.path.join(host_source_path, "workflows")
    host_credentials_path = os.path.join(host_source_path, "credentials")

    if not os.path.isdir(host_workflows_path) or not os.path.isdir(host_credentials_path):
        print(f"Fehler: Backup-Verzeichnis '{host_source_path}' ist ungültig. 'workflows' und/oder 'credentials' Unterordner fehlen.")
        return

    container_temp_dir = run_docker_command(container_name, "mktemp -d", capture_output=True, use_n8n_prefix=False)
    print(f"Temporäres Import-Verzeichnis im Container erstellt: {container_temp_dir}")

    try:
        # Schritt 1: Workflows importieren
        print("\n--- Importiere Workflows ---")
        host_abs_workflows = os.path.abspath(host_workflows_path)
        # Überprüfen, ob überhaupt Workflow-Dateien vorhanden sind
        if os.listdir(host_abs_workflows):
            pipe_command_wf = f"tar -c -C '{host_abs_workflows}' . | docker exec -i {container_name} tar -x -C {container_temp_dir}"
            run_host_command(pipe_command_wf)
            run_docker_command(container_name, f"import:workflow --separate --input={container_temp_dir}")
        else:
            print("Keine Workflow-Dateien im Backup gefunden, überspringe.")

        # Schritt 2: Anmeldeinformationen importieren
        print("\n--- Importiere Anmeldeinformationen ---")
        # Temporäres Verzeichnis leeren
        run_docker_command(container_name, f"rm -f {container_temp_dir}/*", use_n8n_prefix=False)

        host_abs_creds = os.path.abspath(host_credentials_path)
        # Überprüfen, ob überhaupt Credential-Dateien vorhanden sind
        if os.listdir(host_abs_creds):
            pipe_command_cr = f"tar -c -C '{host_abs_creds}' . | docker exec -i {container_name} tar -x -C {container_temp_dir}"
            run_host_command(pipe_command_cr)
            run_docker_command(container_name, f"import:credentials --separate --input={container_temp_dir}")
        else:
            print("Keine Credential-Dateien im Backup gefunden, überspringe.")

    finally:
        print(f"\nLösche temporäres Verzeichnis im Container: {container_temp_dir}")
        run_docker_command(container_name, f"rm -rf {container_temp_dir}", use_n8n_prefix=False)

    print("\nImport-Prozess abgeschlossen.")

def cleanup_old_backups(host_backup_base_path, retention_hours=24):
    # ... (Diese Funktion bleibt unverändert und sollte korrekt sein)
    print(f"Bereinige alte Backups auf dem Host, die älter als {retention_hours} Stunden sind...")
    if not os.path.isdir(host_backup_base_path): return
    now = datetime.datetime.now()
    for dir_name in os.listdir(host_backup_base_path):
        full_path = os.path.join(host_backup_base_path, dir_name)
        if not os.path.isdir(full_path) or not dir_name.startswith("backup_"): continue
        try:
            # Flexible Zeitstempel-Extraktion
            timestamp_str = '_'.join(dir_name.split('_')[1:3])
            backup_time = datetime.datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
            if (now - backup_time).total_seconds() > retention_hours * 3600:
                print(f"Lösche altes Backup: {full_path}")
                shutil.rmtree(full_path)
        except (ValueError, IndexError):
            continue

def main():
    parser = argparse.ArgumentParser(description="Python-Skript zur Verwaltung von n8n-Daten-Backups mit Docker.", formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--host-path", default="./n8n_backups", help="Basispfad für Backups (Standard: './n8n_backups').")
    parser.add_argument("container_name", help="Der Name des n8n Docker-Containers.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser("export", help="Exportiert n8n-Daten auf den Host.")
    export_parser.add_argument("-d", "--description", help="Optionale Beschreibung für das Backup.")
    export_parser.add_argument("-r", "--retention-hours", type=int, default=24, help="Aufbewahrungsdauer für Backups (Std).")
    import_parser = subparsers.add_parser("import", help="Importiert n8n-Daten vom Host.")
    import_parser.add_argument("backup_folder_name", help="Name des Backup-Ordners.")
    args = parser.parse_args()

    try:
        if args.command == "export":
            path = export_n8n_data(args.container_name, args.host_path, args.description)
            if path: cleanup_old_backups(args.host_path, args.retention_hours)
        elif args.command == "import":
            import_n8n_data(args.container_name, args.host_path, args.backup_folder_name)
    except Exception as e:
        print(f"\nSkriptausführung fehlgeschlagen: {e}")

if __name__ == "__main__":
    main()
