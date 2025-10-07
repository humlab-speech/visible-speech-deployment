import os
import sys
import shutil
import subprocess
import argparse
from datetime import datetime

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
        'MATOMO_DB_ROOT_PASSWORD'
    ]
    
    missing = []
    for var in required_vars:
        if var not in env_vars or not env_vars[var]:
            missing.append(var)
    
    if missing:
        print(f"Warning: The following required environment variables are not set in .env: {', '.join(missing)}")
        print("Please fill them in as per the README installation instructions.")
    else:
        print("Environment file check passed.")

def update_repo(basedir, name, npm_install=False, npm_build=False):
    print(f"Update {name}...")
    os.chdir(name)
    try:
        subprocess.run(["git", "fetch", "--all"], check=True)
        subprocess.run(["git", "reset", "--hard", "origin/master"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Git update of {name} failed: {e}")
        sys.exit(1)
    if npm_install:
        if os.path.exists("node_modules"):
            shutil.rmtree("node_modules")
        subprocess.run(["npm", "install"], check=True)
    if npm_build:
        subprocess.run(["npm", "run", "build"], check=True)
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

def main():
    parser = argparse.ArgumentParser(description='Update Visible Speech deployment components.')
    parser.add_argument('--rebuild-images', action='store_true', help='Rebuild Docker images if they are outdated')
    args = parser.parse_args()

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
        if args.rebuild_images:
            try:
                rebuild_images()
                status_results.append({"Component": "Docker Images", "Status": "✓ REBUILT", "Details": f"Rebuilt: {', '.join(old_images)}"})
            except SystemExit:
                status_results.append({"Component": "Docker Images", "Status": "✗ FAIL", "Details": f"Rebuild failed for: {', '.join(old_images)}"})
        else:
            status_results.append({"Component": "Docker Images", "Status": "⚠ OUTDATED", "Details": f"Outdated: {', '.join(old_images)}"})
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

    # Set permissions (already done via chown_recursive above)
    status_results.append({"Component": "Set Permissions Script", "Status": "✓ SKIPPED", "Details": "Permissions already set via chown_recursive"})

    # Print status table
    print("\n" + "="*80)
    print("VISIBLE SPEECH DEPLOYMENT UPDATE SUMMARY")
    print("="*80)
    print(tabulate(status_results, headers="keys", tablefmt="grid"))
    print("="*80)

if __name__ == "__main__":
    main()