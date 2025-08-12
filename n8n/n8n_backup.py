import subprocess
import datetime
import os
import shutil
import argparse

"""
" Usage:
" python3 n8n/n8n_backup.py --host-path /Users/hoschi/repos/local-ai-packaged/n8n/user_backup n8n export --description 'main-instance'
" https://aistudio.google.com/prompts/1J7fFba4vXBjYd5eF_KvUgXkjB5c-1ukH
"""


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
        print(f"Warnung: .env-Datei nicht gefunden unter {filepath}")
    return env_vars

def run_docker_command(container_name, command_args, capture_output=False, use_n8n_prefix=True, env_vars=None):
    """Führt einen Befehl im Container aus und injiziert dabei Umgebungsvariablen."""
    if isinstance(command_args, str):
        command_args = command_args.split()

    cmd = ["docker", "exec"]
    if env_vars:
        for key, value in env_vars.items():
            # Stelle sicher, dass die Werte korrekt escaped sind, falls sie Sonderzeichen enthalten
            cmd.extend(["-e", f"{key}={value}"])

    cmd.append(container_name)

    if use_n8n_prefix:
        cmd.append("n8n")

    cmd.extend(command_args)

    # Aus Sicherheitsgründen geben wir die Werte der Variablen nicht im Log aus
    print(f"Führe Befehl im Container aus (Umgebungsvariablen werden übergeben)...")
    try:
        result = subprocess.run(cmd, check=True, capture_output=capture_output, text=True, encoding='utf-8')
        return result.stdout.strip() if capture_output else None
    except subprocess.CalledProcessError as e:
        print(f"Fehler beim Ausführen des Befehls im Container: {e}")
        stderr_output = e.stderr.strip() if e.stderr else '[Keine Ausgabe]'
        # Nützliche Fehlermeldung für das häufigste Problem hinzufügen
        if "connect ECONNREFUSED" in stderr_output:
            print("\nFEHLERHINWEIS: 'Connection refused' deutet darauf hin, dass die DB-Verbindungsvariablen falsch sind oder der DB-Container nicht erreichbar ist.\n")
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

def import_n8n_data(container_name, host_backup_base_path, backup_folder_name, n8n_env_vars):
    """Importiert n8n-Daten mit den korrekten Umgebungsvariablen für DB und Verschlüsselung."""
    host_source_path = os.path.join(host_backup_base_path, backup_folder_name)
    host_workflows_path = os.path.join(host_source_path, "workflows")
    host_credentials_path = os.path.join(host_source_path, "credentials")

    if not os.path.isdir(host_workflows_path) or not os.path.isdir(host_credentials_path):
        print(f"Fehler: Backup-Verzeichnis '{host_source_path}' ist ungültig oder unvollständig.")
        return

    # Erstelle ein temporäres Verzeichnis im Container
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
        run_docker_command(container_name, f"rm -f {container_temp_dir}/*", use_n8n_prefix=False)
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

def main():
    parser = argparse.ArgumentParser(description="Python-Skript zur Verwaltung von n8n-Daten-Backups mit Docker.", formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--host-path", default="./n8n_backups", help="Basispfad für Backups.")
    parser.add_argument("--env-file", required=True, help="Pfad zur .env-Datei, die die n8n-Variablen enthält.")
    parser.add_argument("container_name", help="Der Name des n8n Docker-Containers.")

    subparsers = parser.add_subparsers(dest="command", required=True)
    import_parser = subparsers.add_parser("import", help="Importiert n8n-Daten vom Host.")
    import_parser.add_argument("backup_folder_name", help="Name des Backup-Ordners.")

    args = parser.parse_args()

    # Lade die Umgebungsvariablen aus der .env-Datei
    env_vars = load_env_file(args.env_file)

    # Filtere nur die für n8n relevanten Variablen
    required_keys = [ "POSTGRES_PASSWORD", "N8N_ENCRYPTION_KEY" ]
    n8n_env_vars = {key: env_vars[key] for key in required_keys if key in env_vars}

    # Umbenennen für n8n-Kompatibilität
    if "POSTGRES_PASSWORD" in n8n_env_vars:
        n8n_env_vars["DB_POSTGRESDB_PASSWORD"] = n8n_env_vars.pop("POSTGRES_PASSWORD")

    # Überprüfen, ob alle notwendigen Schlüssel vorhanden sind
    if len(n8n_env_vars) < len(required_keys):
        missing = set(required_keys) - set(n8n_env_vars.keys()) - ({"POSTGRES_PASSWORD"} if "DB_POSTGRESDB_PASSWORD" in n8n_env_vars else set())
        print(f"Fehler: Folgende notwendige Variablen fehlen in der .env-Datei: {', '.join(missing)}")
        return

    n8n_env_vars["DB_TYPE"]="postgresdb"
    n8n_env_vars["DB_POSTGRESDB_HOST"]="db"
    n8n_env_vars["DB_POSTGRESDB_USER"]="postgres"
    n8n_env_vars["DB_POSTGRESDB_DATABASE"]="postgres"

    try:
        if args.command == "import":
            import_n8n_data(args.container_name, args.host_path, args.backup_folder_name, n8n_env_vars)
    except Exception as e:
        print(f"\nSkriptausführung fehlgeschlagen: {e}")

if __name__ == "__main__":
    main()
