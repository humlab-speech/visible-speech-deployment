#!/usr/bin/env python3

import os
import sys
import shutil
import subprocess
import argparse
from datetime import datetime
import string
import random
import getpass

try:
    from tabulate import tabulate
except ImportError:
    print("tabulate library not found. Install with: pip install tabulate")
    sys.exit(1)

def chown_recursive(path, uid, gid):
    try:
        os.chown(path, uid, gid)
        for root, dirs, files in os.walk(path):
            for d in dirs:
                os.chown(os.path.join(root, d), uid, gid)
            for f in files:
                os.chown(os.path.join(root, f), uid, gid)
    except OSError as e:
        print(f"Failed to chown {path}: {e}")

def generate_random_string(length=16):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def setup_env_file(auto_passwords=True, interactive=False):
    if not os.path.exists('.env'):
        if os.path.exists('.env-example'):
            shutil.copy('.env-example', '.env')
        else:
            print("Error: .env-example not found")
            return

    # Set defaults
    env_updates = {
        'ABS_ROOT_PATH': os.getcwd(),
        'ADMIN_EMAIL': 'admin@visp.local'
    }

    with open('.env', 'r', encoding='utf-8') as f:
        content = f.read()

    for key, value in env_updates.items():
        if f'{key}=' in content:
            content = content.replace(f'{key}=', f'{key}={value}')
        else:
            content += f'\n{key}={value}'

    # Password variables to handle
    password_vars = {
        'POSTGRES_PASSWORD': 'local',
        'TEST_USER_LOGIN_KEY': 'local',
        'VISP_API_ACCESS_TOKEN': 'local',
        'RSTUDIO_PASSWORD': 'local',
        'MONGO_ROOT_PASSWORD': 'local',
        'ELASTIC_AGENT_FLEET_ENROLLMENT_TOKEN': 'local',
        'MATOMO_DB_PASSWORD': 'local',
        'MATOMO_DB_ROOT_PASSWORD': 'local',
        'MATOMO_DB_USER': 'local'
    }

    for var, ptype in password_vars.items():
        if auto_passwords or ptype == 'local':
            random_value = generate_random_string()
            if f'{var}=' in content:
                content = content.replace(f'{var}=', f'{var}={random_value}')
            else:
                content += f'\n{var}={random_value}'
        elif interactive:
            if f'{var}=' not in content or content.split(f'{var}=')[1].split('\n')[0].strip() == '':
                password = getpass.getpass(f"Enter {var}: ")
                if f'{var}=' in content:
                    content = content.replace(f'{var}=', f'{var}={password}')
                else:
                    content += f'\n{var}={password}'

    with open('.env', 'w', encoding='utf-8') as f:
        f.write(content)

def check_env_file():
    if not os.path.exists('.env'):
        print("Warning: .env file not found. Please create it from .env-example and fill in the required values as per the README.")
        return
    with open('.env', 'r', encoding='utf-8') as f:
        lines = f.readlines()
    env_vars = {}
    for line in lines:
        if '=' in line and not line.strip().startswith('#'):
            key, value = line.split('=', 1)
            env_vars[key.strip()] = value.strip()
    
    required_vars = [
        'POSTGRES_PASSWORD',
        'VISP_API_ACCESS_TOKEN', 
        'MONGO_ROOT_PASSWORD',
        'RSTUDIO_PASSWORD',
        'MATOMO_DB_ROOT_PASSWORD',
        'MATOMO_DB_USER',
        'MATOMO_DB_PASSWORD'
    ]
    
    missing = []
    for var in required_vars:
        if var not in env_vars or not env_vars[var]:
            missing.append(var)
    
    if missing:
        print(f"Warning: The following required environment variables are not set in .env: {', '.join(missing)}")
        print("Auto-generating random values for demo deployment...")
        for var in missing:
            random_value = generate_random_string()
            # Find the line and replace
            with open('.env', 'r', encoding='utf-8') as f:
                content = f.read()
            if f'{var}=' in content:
                content = content.replace(f'{var}=', f'{var}={random_value}')
            else:
                # Append if not present
                content += f'\n{var}={random_value}'
            with open('.env', 'w', encoding='utf-8') as f:
                f.write(content)
        print("Environment variables auto-filled.")
    else:
        print("Environment file check passed.")

def run_command(cmd, description="", check=True):
    print(f"Running: {description or cmd}")
    try:
        result = subprocess.run(cmd, shell=True, check=check, capture_output=True, text=True)
        if result.stdout:
            print(result.stdout)
        return result
    except subprocess.CalledProcessError as e:
        print(f"Command failed: {e}")
        if e.stdout:
            print(e.stdout)
        if e.stderr:
            print(e.stderr)
        raise

def install_system():
    print("Starting VISP installation...")
    BASEDIR = os.getcwd()

    # Install dependencies (excluding Node.js since we'll use containers)
    run_command("apt update", "Updating package list")
    run_command("apt install -y curl git openssl docker.io docker-compose", "Installing dependencies")

    # Setup .env
    setup_env_file(auto_passwords=True)

    # Create directories
    os.makedirs("mounts/session-manager", exist_ok=True)
    with open("mounts/session-manager/session-manager.log", 'w', encoding='utf-8'):
        pass
    os.chown("mounts/session-manager/session-manager.log", 1000, 1000)
    os.chmod("mounts/session-manager/session-manager.log", 0o644)

    os.makedirs("mounts/webapi", exist_ok=True)
    os.makedirs("mounts/apache/apache/uploads", exist_ok=True)
    os.makedirs("mounts/mongo/logs", exist_ok=True)
    with open("mounts/mongo/logs/mongodb.log", 'w', encoding='utf-8'):
        pass
    os.makedirs("certs", exist_ok=True)
    os.makedirs("mounts/transcription-queued", exist_ok=True)

    # Fetch cert
    run_command("curl http://mds.swamid.se/md/md-signer2.crt -o certs/md-signer2.crt", "Fetching SWAMID cert")

    # Generate certs
    os.makedirs("certs/visp.local", exist_ok=True)
    run_command('openssl req -x509 -newkey rsa:4096 -keyout certs/visp.local/cert.key -out certs/visp.local/cert.crt -nodes -days 3650 -subj "/C=SE/ST=visp/L=visp/O=visp/OU=visp/CN=visp.local"', "Generating TLS cert")

    os.makedirs("certs/ssp-idp-cert", exist_ok=True)
    run_command('openssl req -x509 -newkey rsa:4096 -keyout certs/ssp-idp-cert/key.pem -out certs/ssp-idp-cert/cert.pem -nodes -days 3650 -subj "/C=SE/ST=visp/L=visp/O=visp/OU=visp/CN=visp.local"', "Generating IdP cert")

    # Clone repos
    repos = [
        ("webclient", "https://github.com/humlab-speech/webclient"),
        ("webapi", "https://github.com/humlab-speech/webapi"),
        ("container-agent", "https://github.com/humlab-speech/container-agent"),
        ("wsrng-server", "https://github.com/humlab-speech/wsrng-server"),
        ("session-manager", "https://github.com/humlab-speech/session-manager")
    ]

    for name, url in repos:
        if not os.path.exists(name):
            run_command(f"git clone {url}", f"Cloning {name}")

    # Setup emu-webapp-server .env
    os.makedirs("mounts/emu-webapp-server/logs", exist_ok=True)
    run_command("curl -L https://raw.githubusercontent.com/humlab-speech/emu-webapp-server/main/.env-example -o ./mounts/emu-webapp-server/.env", "Fetching emu-webapp-server .env")

    # Install SimpleSamlPhp
    run_command("curl -L https://github.com/simplesamlphp/simplesamlphp/releases/download/v1.19.6/simplesamlphp-1.19.6.tar.gz --output simplesamlphp.tar.gz", "Downloading SimpleSamlPhp")
    run_command("tar xzf simplesamlphp.tar.gz", "Extracting SimpleSamlPhp")
    os.rename("simplesamlphp-1.19.6", "mounts/simplesamlphp")
    run_command("rm simplesamlphp.tar.gz", "Cleaning up tar file")

    # Copy simplesamlphp config
    if os.path.exists("simplesamlphp-visp"):
        for root, _, files in os.walk("simplesamlphp-visp"):
            for file in files:
                src = os.path.join(root, file)
                dst = src.replace("simplesamlphp-visp/", "mounts/simplesamlphp/simplesamlphp/")
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)

    # Build components using Node.js container
    components = [
        ("container-agent", ["npm install", "npm run build"]),
        ("wsrng-server", ["npm install", "mkdir -p logs", "touch logs/wsrng-server.log"]),
        ("webclient", ["npm install", "npm run build"]),
        ("session-manager", ["npm install"])
    ]

    for comp, cmds in components:
        comp_path = os.path.join(BASEDIR, comp)
        for cmd in cmds:
            # Use Node.js container for builds instead of host Node.js
            run_command(f"docker run --rm -v {comp_path}:/app -w /app node:20 {cmd}", f"Building {comp}: {cmd}")

    # Set permissions
    chown_recursive("webclient", 1000, 1000)
    chown_recursive("certs", 1000, 1000)
    chown_recursive("container-agent", 1000, 1000)
    chown_recursive("webapi", 1000, 1000)
    chown_recursive("wsrng-server", 1000, 1000)
    chown_recursive("session-manager", 1000, 1000)

    print("Installation complete. Run 'docker-compose up -d' to start services.")

def update_repo(basedir, name, npm_install=False, npm_build=False):
    print(f"Update {name}...")
    repo_path = os.path.join(basedir, name)
    os.chdir(repo_path)
    try:
        subprocess.run(["git", "fetch", "--all"], check=True)
        subprocess.run(["git", "reset", "--hard", "origin/master"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Git update of {name} failed: {e}")
        sys.exit(1)
    if npm_install:
        if os.path.exists("node_modules"):
            shutil.rmtree("node_modules")
        run_command(f"docker run --rm -v {repo_path}:/app -w /app node:20 npm install", f"Installing npm dependencies for {name}")
    if npm_build:
        run_command(f"docker run --rm -v {repo_path}:/app -w /app node:20 npm run build", f"Building {name}")
    os.chdir(basedir)

def check_image_age(image_name, source_path):
    try:
        result = subprocess.run(["docker", "inspect", "-f", "{{.Created}}", image_name], capture_output=True, text=True, check=True)
        created_str = result.stdout.strip().strip('"')  # sometimes quoted
        created_time = datetime.fromisoformat(created_str.replace('Z', '+00:00'))
        source_mtime = os.path.getmtime(source_path)
        return source_mtime > created_time.timestamp()
    except subprocess.CalledProcessError:
        # Image doesn't exist
        return True
    except (ValueError, OSError) as e:
        print(f"Error checking age of {image_name}: {e}")
        return True

def rebuild_images():
    print("Rebuilding Docker images...")
    try:
        subprocess.run(["./docker/session-manager/build-session-images.sh"], check=True)
        print("Docker images rebuilt successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to rebuild Docker images: {e}")
        sys.exit(1)

def update_system():
    BASEDIR = os.getcwd()
    status_results = []

    # Check .env file
    try:
        check_env_file()
        status_results.append({"Component": "Environment Check", "Status": "✓ PASS", "Details": ".env file verified"})
    except SystemExit:
        status_results.append({"Component": "Environment Check", "Status": "✗ FAIL", "Details": ".env file issues"})

    # Initial ownership
    try:
        chown_recursive("webclient", 1000, 1000)
        chown_recursive("certs", 1000, 1000)
        chown_recursive("container-agent", 1000, 1000)
        chown_recursive("webapi", 1000, 1000)
        chown_recursive("wsrng-server", 1000, 1000)
        chown_recursive("session-manager", 1000, 1000)
        status_results.append({"Component": "Initial Permissions", "Status": "✓ PASS", "Details": "Ownership set"})
    except OSError as e:
        status_results.append({"Component": "Initial Permissions", "Status": "✗ FAIL", "Details": str(e)})

    # Update repositories
    repos = [
        ("webclient", True, True),
        ("container-agent", True, False),
        ("webapi", False, False),
        ("wsrng-server", True, True),
        ("session-manager", True, False),
    ]
    for repo, npm_inst, npm_build in repos:
        try:
            update_repo(BASEDIR, repo, npm_inst, npm_build)
            status_results.append({"Component": f"Update {repo}", "Status": "✓ PASS", "Details": "Updated successfully"})
        except SystemExit:
            status_results.append({"Component": f"Update {repo}", "Status": "✗ FAIL", "Details": "Update failed"})

    # Check Docker image ages
    images_to_check = [
        ("visp-operations-session", "docker/session-manager/operations-session"),
        ("visp-rstudio-session", "docker/session-manager/rstudio-session"),
        ("visp-jupyter-session", "docker/session-manager/jupyter-session"),
        ("visp-emu-webapp", "docker/emu-webapp"),
        ("visp-session-manager-dev", "docker/session-manager/build-context"),
        ("visp-whisper", "docker/whisper"),
        ("visp-apache", "docker/apache"),
        ("visp-octra", "docker/octra"),
        ("visp-emu-webapp-server", "docker/emu-webapp-server"),
        ("visp-wsrng-server", "docker/wsrng-server"),
    ]
    
    old_images = []
    for image, source in images_to_check:
        if check_image_age(image, source):
            old_images.append(image)
    
    if old_images:
        try:
            rebuild_images()
            status_results.append({"Component": "Docker Images", "Status": "✓ REBUILT", "Details": f"Rebuilt: {', '.join(old_images)}"})
        except SystemExit:
            status_results.append({"Component": "Docker Images", "Status": "✗ FAIL", "Details": f"Rebuild failed for: {', '.join(old_images)}"})
    else:
        status_results.append({"Component": "Docker Images", "Status": "✓ UP TO DATE", "Details": "All images current"})

    # Final ownership
    try:
        chown_recursive("webclient", 1000, 1000)
        chown_recursive("certs", 1000, 1000)
        chown_recursive("container-agent", 1000, 1000)
        chown_recursive("webapi", 1000, 1000)
        chown_recursive("wsrng-server", 1000, 1000)
        chown_recursive("session-manager", 1000, 1000)
        status_results.append({"Component": "Final Permissions", "Status": "✓ PASS", "Details": "Ownership set"})
    except OSError as e:
        status_results.append({"Component": "Final Permissions", "Status": "✗ FAIL", "Details": str(e)})

    # Print status table
    print("\n" + "="*80)
    print("VISIBLE SPEECH DEPLOYMENT UPDATE SUMMARY")
    print("="*80)
    print(tabulate(status_results, headers="keys", tablefmt="grid"))
    print("="*80)

def main():
    parser = argparse.ArgumentParser(description='VISP Deployment Manager')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Install command
    install_parser = subparsers.add_parser('install', help='Install VISP system')
    install_parser.add_argument('--auto-passwords', action='store_true', default=True, help='Auto-generate passwords (default)')
    install_parser.add_argument('--interactive-passwords', action='store_true', help='Prompt for passwords interactively')

    # Update command
    update_parser = subparsers.add_parser('update', help='Update VISP system components')
    update_parser.add_argument('--rebuild-images', action='store_true', help='Rebuild Docker images if outdated')

    args = parser.parse_args()

    if args.command == 'install':
        install_system()
    elif args.command == 'update':
        update_system()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()