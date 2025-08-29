import subprocess
import datetime
import os
import shutil
import argparse

def load_env_file(filepath):
    """Liest eine .env-Datei und gibt die Variablen als Dictionary zurück."""
    env_vars = {}
    try:
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key.strip()] = value.strip()
    except FileNotFoundError:
        print(f"Fehler: .env-Datei nicht gefunden unter {filepath}")
        raise
    return env_vars

def run_docker_command(container_name, command_args, capture_output=False, use_n8n_prefix=True, env_vars=None):
    """Führt einen Befehl im Container aus und injiziert dabei Umgebungsvariablen."""
    if isinstance(command_args, str):
        command_args = command_args.split()

    cmd = ["docker", "exec"]
    if env_vars:
        for key, value in env_vars.items():
            cmd.extend(["-e", f"{key}={value}"])

    cmd.append(container_name)

    if use_n8n_prefix:
        cmd.append("n8n")

    cmd.extend(command_args)

    print("Führe Befehl im Container aus (Umgebungsvariablen werden übergeben)...")
    try:
        result = subprocess.run(cmd, check=True, capture_output=capture_output, text=True, encoding='utf-8')
        return result.stdout.strip() if capture_output else None
    except subprocess.CalledProcessError as e:
        print(f"Fehler beim Ausführen des Befehls im Container: {e}")
        stderr_output = e.stderr.strip() if e.stderr else '[Keine Ausgabe]'
        print(f"Stderr: {stderr_output}")
        raise

def run_host_command(command, shell=True):
    print(f"Führe auf Host aus: {command}")
    try:
        subprocess.run(command, shell=shell, check=True, text=True, encoding='utf-8', capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"Fehler beim Ausführen des Host-Befehls: {e}")
        print(f"Stderr: {e.stderr.strip() if e.stderr else '[Keine Ausgabe]'}")
        raise

def export_n8n_data(container_name, host_backup_base_path, n8n_env_vars, backup_description=None):
    """Exportiert n8n-Daten mit sauberen, getrennten temporären Verzeichnissen für jede Operation."""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    sanitized_description = "".join(c for c in backup_description if c.isalnum() or c in (' ', '_')).replace(' ', '_') if backup_description else ""
    backup_dir_name = f"backup_{timestamp}_{sanitized_description}" if sanitized_description else f"backup_{timestamp}"
    host_destination_path = os.path.join(host_backup_base_path, backup_dir_name)

    host_workflows_path = os.path.join(host_destination_path, "workflows")
    host_credentials_path = os.path.join(host_destination_path, "credentials")
    os.makedirs(host_workflows_path, exist_ok=True)
    os.makedirs(host_credentials_path, exist_ok=True)

    print(f"Erstelle Backup in {host_destination_path}")

    for data_type in ["workflow", "credentials"]:
        print(f"\n--- Exportiere {data_type.capitalize()}s ---")
        container_temp_dir = run_docker_command(container_name, "mktemp -d", capture_output=True, use_n8n_prefix=False)
        try:
            run_docker_command(container_name, f"export:{data_type} --backup --output={container_temp_dir}", env_vars=n8n_env_vars)
            host_target_path = host_workflows_path if data_type == "workflow" else host_credentials_path
            run_host_command(f"docker cp '{container_name}:{container_temp_dir}/.' '{host_target_path}/'")
            print(f"{data_type.capitalize()}s erfolgreich nach '{host_target_path}' exportiert.")
        finally:
            print(f"Lösche temporäres Verzeichnis im Container: {container_temp_dir}")
            run_docker_command(container_name, f"rm -rf {container_temp_dir}", use_n8n_prefix=False)

    print("\nBackup erfolgreich und sauber erstellt.")
    return host_destination_path

def import_n8n_data(container_name, host_backup_base_path, backup_folder_name, n8n_env_vars):
    """Importiert n8n-Daten und stellt eine saubere Trennung zwischen den Schritten sicher."""
    host_source_path = os.path.join(host_backup_base_path, backup_folder_name)
    host_workflows_path = os.path.join(host_source_path, "workflows")
    host_credentials_path = os.path.join(host_source_path, "credentials")

    if not os.path.isdir(host_workflows_path) or not os.path.isdir(host_credentials_path):
        print(f"Fehler: Backup-Verzeichnis '{host_source_path}' ist ungültig oder unvollständig.")
        return

    container_temp_dir = run_docker_command(container_name, "mktemp -d", capture_output=True, use_n8n_prefix=False)
    try:
        # Schritt 1: Workflows importieren
        print("\n--- Importiere Workflows ---")
        host_abs_workflows = os.path.abspath(host_workflows_path)
        if os.listdir(host_abs_workflows):
            pipe_cmd = f"tar -c -C '{host_abs_workflows}' . | docker exec -i {container_name} tar -x -C {container_temp_dir}"
            run_host_command(pipe_cmd)
            run_docker_command(container_name, f"import:workflow --separate --input={container_temp_dir}", env_vars=n8n_env_vars)
        else:
            print("Keine Workflow-Dateien gefunden, überspringe.")

        # Schritt 2: Anmeldeinformationen importieren
        print("\n--- Importiere Anmeldeinformationen ---")

        print(f"Leere temporäres Verzeichnis: {container_temp_dir}")
        run_docker_command(container_name, ['sh', '-c', f'rm -rf {container_temp_dir}/*'], use_n8n_prefix=False)

        host_abs_creds = os.path.abspath(host_credentials_path)
        if os.listdir(host_abs_creds):
            pipe_cmd = f"tar -c -C '{host_abs_creds}' . | docker exec -i {container_name} tar -x -C {container_temp_dir}"
            run_host_command(pipe_cmd)
            run_docker_command(container_name, f"import:credentials --separate --input={container_temp_dir}", env_vars=n8n_env_vars)
        else:
            print("Keine Credential-Dateien gefunden, überspringe.")
    finally:
        print(f"\nLösche temporäres Verzeichnis im Container: {container_temp_dir}")
        run_docker_command(container_name, f"rm -rf {container_temp_dir}", use_n8n_prefix=False)
    print("\nImport-Prozess erfolgreich abgeschlossen.")

def delete_old_backups(backup_base_path, hours=24):
    """Löscht Backup-Verzeichnisse, die älter als eine bestimmte Stundenzahl sind."""
    print(f"\n--- Starte Bereinigung alter Backups (älter als {hours} Stunden) ---")
    now = datetime.datetime.now()
    cutoff = now - datetime.timedelta(hours=hours)

    for folder_name in os.listdir(backup_base_path):
        folder_path = os.path.join(backup_base_path, folder_name)
        if os.path.isdir(folder_path) and folder_name.startswith("backup_"):
            try:
                # Extrahiere den Zeitstempel aus dem Ordnernamen, z.B. backup_20231027_103000_daily
                parts = folder_name.split('_')
                if len(parts) >= 3:
                    timestamp_str = f"{parts[1]}_{parts[2]}"
                    backup_time = datetime.datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")

                    if backup_time < cutoff:
                        print(f"Lösche altes Backup: {folder_name}")
                        shutil.rmtree(folder_path)
                else:
                    print(f"Ordnername '{folder_name}' hat nicht das erwartete Format, überspringe.")
            except (IndexError, ValueError) as e:
                print(f"Konnte Zeitstempel für '{folder_name}' nicht verarbeiten, überspringe. Fehler: {e}")
    print("Bereinigung abgeschlossen.")


def main():
    parser = argparse.ArgumentParser(description="Python-Skript zur Verwaltung von n8n-Daten-Backups mit Docker.", formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--host-path", default="./n8n/user_backup", help="Basispfad für Backups.")
    parser.add_argument("--env-file", required=True, help="Pfad zur .env-Datei, die die n8n-Variablen enthält.")
    parser.add_argument("container_name", help="Der Name des n8n Docker-Containers.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export", help="Exportiert n8n-Daten.")
    export_parser.add_argument("-d", "--description", help="Optionale Beschreibung für das Backup.")

    import_parser = subparsers.add_parser("import", help="Importiert n8n-Daten.")
    import_parser.add_argument("backup_folder_name", help="Name des Backup-Ordners.")

    args = parser.parse_args()

    env_vars = load_env_file(args.env_file)
    required_keys = ["POSTGRES_PASSWORD", "N8N_ENCRYPTION_KEY"]
    n8n_env_vars = {key: env_vars[key] for key in required_keys if key in env_vars}
    if "POSTGRES_PASSWORD" in n8n_env_vars:
        n8n_env_vars["DB_POSTGRESDB_PASSWORD"] = n8n_env_vars.pop("POSTGRES_PASSWORD")

    missing = set(required_keys) - set(n8n_env_vars.keys()) - ({"POSTGRES_PASSWORD"} if "DB_POSTGRESDB_PASSWORD" in n8n_env_vars else set())
    if missing:
        print(f"Fehler: Folgende Variablen fehlen in der .env-Datei: {', '.join(missing)}")
        return

    n8n_env_vars["DB_TYPE"] = "postgresdb"
    n8n_env_vars["DB_POSTGRESDB_HOST"] = "db"
    n8n_env_vars["DB_POSTGRESDB_USER"] = "postgres"
    n8n_env_vars["DB_POSTGRESDB_DATABASE"] = "postgres"

    try:
        if args.command == "export":
            export_n8n_data(args.container_name, args.host_path, n8n_env_vars, args.description)
            delete_old_backups(args.host_path, 24)
        elif args.command == "import":
            import_n8n_data(args.container_name, args.host_path, args.backup_folder_name, n8n_env_vars)
    except Exception as e:
        print(f"\nSkriptausführung fehlgeschlagen: {e}")

if __name__ == "__main__":
    main()
