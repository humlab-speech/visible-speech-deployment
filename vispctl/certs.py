"""Certificate management and local IdP file rendering for VISP.

Extracted from visp.py to keep the main script focused on CLI dispatch.
All functions that were module-level in visp.py are preserved with the same
signatures so callers only need an import change.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .runner import Colors, color


def is_cert_valid(cert_path: Path, min_days: int = 30) -> bool:
    """Return True if the certificate exists and won't expire within min_days."""
    if not cert_path.exists():
        return False
    result = subprocess.run(
        ["openssl", "x509", "-checkend", str(min_days * 86400), "-noout", "-in", str(cert_path)],
        capture_output=True,
    )
    return result.returncode == 0


def ensure_cert(spec: dict) -> bool:
    """Ensure a certificate exists and is valid, generating it if needed.

    spec keys:
      label        – human-readable name for log messages
      cert         – Path to the certificate file (.crt or .pem)
      key          – Path to the private key file
      method       – "openssl" | "shib-keygen"
      openssl_args – list of extra args for openssl req (method=openssl only)
      shib_host    – hostname for shib-keygen (method=shib-keygen only)
      post_chown   – optional (uid, gid) to apply via podman unshare chown
      min_days     – days before expiry that triggers regeneration (default 30)
    """
    cert_path: Path = spec["cert"]
    key_path: Path = spec["key"]
    label: str = spec["label"]
    method: str = spec.get("method", "openssl")
    min_days: int = spec.get("min_days", 30)

    if is_cert_valid(cert_path, min_days) and key_path.exists():
        print(f"  ✓ {label}: certificate already exists and is valid")
        return True

    if cert_path.exists() and not is_cert_valid(cert_path, min_days):
        print(color(f"  ⚠ {label}: certificate expiring within {min_days} days — regenerating", Colors.YELLOW))
    else:
        print(color(f"  • {label}: generating certificate…", Colors.CYAN))

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    if method == "openssl":
        cmd = [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:4096",
            "-keyout",
            str(key_path),
            "-out",
            str(cert_path),
            "-nodes",
            "-days",
            "3650",
        ] + spec.get("openssl_args", [])
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(color(f"  ✗ {label}: openssl failed: {result.stderr.strip()}", Colors.RED))
            return False

    elif method == "shib-keygen":
        raise ValueError(
            f"ensure_cert: method 'shib-keygen' is no longer supported; "
            f"use method 'openssl' with equivalent args (see shib-keygen source). "
            f"Cert spec: {spec['label']}"
        )
    else:
        print(color(f"  ✗ {label}: unknown method '{method}'", Colors.RED))
        return False

    # Apply ownership fix via podman unshare if requested.
    if "post_chown" in spec:
        uid, gid = spec["post_chown"]
        for path in [cert_path, key_path]:
            subprocess.run(
                ["podman", "unshare", "chown", f"{uid}:{gid}", str(path)],
                capture_output=True,
            )

    print(color(f"  ✓ {label}: certificate generated", Colors.GREEN))
    return True


def ensure_certs(project_dir: Path, base_domain: str) -> None:
    """Ensure all certificates required by VISP exist and are valid.

    Three cert types:
      1. TLS (visp.local)     — self-signed wildcard for Apache HTTPS (openssl)
      2. Shibboleth SP        — SAML SP signing cert (openssl)
      3. SimpleSAMLphp IdP    — SAML IdP signing cert for local-idp (openssl)
    """
    print(color("Checking certificates…", Colors.CYAN))

    cert_specs = [
        {
            "label": "TLS (dev HTTPS)",
            "cert": project_dir / f"certs/{base_domain}/cert.crt",
            "key": project_dir / f"certs/{base_domain}/cert.key",
            "method": "openssl",
            "openssl_args": [
                "-subj",
                f"/C=SE/ST=visp/L=visp/O=visp/OU=visp/CN={base_domain}",
                "-addext",
                "basicConstraints=critical,CA:FALSE",
                "-addext",
                "keyUsage=critical,digitalSignature,keyEncipherment",
                "-addext",
                "extendedKeyUsage=serverAuth",
                "-addext",
                f"subjectAltName=DNS:{base_domain},DNS:*.{base_domain}",
            ],
        },
        {
            "label": "Shibboleth SP signing cert",
            "cert": project_dir / "certs/sp-cert/cert.pem",
            "key": project_dir / "certs/sp-cert/key.pem",
            "method": "openssl",
            "openssl_args": [
                # Match shib-keygen defaults: 3072-bit RSA, SHA256, no passphrase,
                # CN=hostname, subjectAltName=DNS:hostname only.
                "-newkey",
                "rsa:3072",
                "-subj",
                f"/CN={base_domain}",
                "-addext",
                f"subjectAltName=DNS:{base_domain}",
                "-addext",
                "subjectKeyIdentifier=hash",
            ],
            # _shibd inside the apache container runs as UID 101, GID 102
            "post_chown": (101, 102),
        },
        {
            "label": "SimpleSAMLphp IdP signing cert",
            "cert": project_dir / "certs/ssp-idp-cert/cert.pem",
            "key": project_dir / "certs/ssp-idp-cert/key.pem",
            "method": "openssl",
            "openssl_args": [
                "-subj",
                f"/C=SE/ST=visp/L=visp/O=visp/OU=visp/CN={base_domain}",
                "-addext",
                "basicConstraints=critical,CA:FALSE",
                "-addext",
                "keyUsage=critical,digitalSignature,keyEncipherment",
                "-addext",
                "extendedKeyUsage=serverAuth,clientAuth",
                "-addext",
                f"subjectAltName=DNS:{base_domain}",
            ],
            # www-data inside the local-idp container runs as UID 33
            "post_chown": (33, 33),
        },
    ]

    for spec in cert_specs:
        ensure_cert(spec)

    print()


def render_local_idp_file(template_path: Path, output_path: Path, base_domain: str) -> bool:
    """Render a local IdP template file with BASE_DOMAIN substitution."""
    if not template_path.exists():
        print(color(f"  ⚠ Missing template: {template_path}", Colors.YELLOW))
        return False

    rendered = template_path.read_text().replace("{{BASE_DOMAIN}}", base_domain)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered)
    return True


def build_local_idp_metadata_xml(base_domain: str, cert_body: str) -> str:
    """Build IdP metadata XML consumed by the Apache Shibboleth SP."""
    idp_host = f"idp.{base_domain}"
    idp_entity_id = f"https://{idp_host}/simplesaml/saml2/idp/metadata.php"
    sso_url = f"https://{idp_host}/simplesaml/saml2/idp/SSOService.php"
    slo_url = f"https://{idp_host}/simplesaml/saml2/idp/SingleLogoutService.php"
    idp_scope = base_domain
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
                     xmlns:shibmd="urn:mace:shibboleth:metadata:1.0"
                     entityID="{idp_entity_id}">
  <md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <md:Extensions>
      <shibmd:Scope regexp="false">{idp_scope}</shibmd:Scope>
    </md:Extensions>
    <md:KeyDescriptor use="signing">
      <ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
        <ds:X509Data>
          <ds:X509Certificate>{cert_body}</ds:X509Certificate>
        </ds:X509Data>
      </ds:KeyInfo>
    </md:KeyDescriptor>
    <md:KeyDescriptor use="encryption">
      <ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
        <ds:X509Data>
          <ds:X509Certificate>{cert_body}</ds:X509Certificate>
        </ds:X509Data>
      </ds:KeyInfo>
    </md:KeyDescriptor>
    <md:NameIDFormat>urn:oasis:names:tc:SAML:2.0:nameid-format:persistent</md:NameIDFormat>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="{sso_url}"/>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" Location="{sso_url}"/>
    <md:SingleLogoutService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="{slo_url}"/>
    <md:SingleLogoutService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" Location="{slo_url}"/>
  </md:IDPSSODescriptor>
</md:EntityDescriptor>
"""


def setup_local_idp_files(project_dir: Path, env_vars: dict[str, str]) -> None:
    """Render local IdP templates and metadata files for dev mode."""
    print(color("Setting up local IdP files...", Colors.CYAN))

    base_domain = env_vars.get("BASE_DOMAIN", "").strip()
    if not base_domain:
        print(color("  ⚠ BASE_DOMAIN is not set; skipping local IdP file generation", Colors.YELLOW))
        return

    template_pairs = [
        (
            project_dir / "mounts/apache/saml/local-idp/shibboleth2.xml.template",
            project_dir / "mounts/apache/saml/local-idp/shibboleth2.xml",
        ),
        (
            project_dir / "mounts/local-idp/config/config-override.php.template",
            project_dir / "mounts/local-idp/config/config-override.php",
        ),
        (
            project_dir / "mounts/local-idp/metadata/saml20-idp-hosted.php.template",
            project_dir / "mounts/local-idp/metadata/saml20-idp-hosted.php",
        ),
        (
            project_dir / "mounts/local-idp/metadata/saml20-sp-remote.php.template",
            project_dir / "mounts/local-idp/metadata/saml20-sp-remote.php",
        ),
    ]

    for template_path, output_path in template_pairs:
        if render_local_idp_file(template_path, output_path, base_domain):
            print(color(f"  ✓ Rendered {output_path.relative_to(project_dir)}", Colors.GREEN))

    # Render IdP metadata XML for the Apache Shibboleth SP.
    idp_cert = project_dir / "certs/ssp-idp-cert/cert.pem"
    metadata_output = project_dir / "mounts/apache/saml/local-idp/idp-metadata.xml"
    if not idp_cert.exists():
        print(color("  ⚠ certs/ssp-idp-cert/cert.pem not found — run ./visp.py install to generate it", Colors.YELLOW))
        return

    cert_lines = []
    for line in idp_cert.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("-----"):
            continue
        cert_lines.append(stripped)

    cert_body = "".join(cert_lines)
    if not cert_body:
        print(color("  ⚠ Could not parse IdP certificate body from certs/ssp-idp-cert/cert.pem", Colors.YELLOW))
        return

    metadata_output.parent.mkdir(parents=True, exist_ok=True)
    metadata_output.write_text(build_local_idp_metadata_xml(base_domain, cert_body))
    print(color(f"  ✓ Rendered {metadata_output.relative_to(project_dir)}", Colors.GREEN))
