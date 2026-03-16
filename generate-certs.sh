#!/bin/bash
set -e

# Generate SSL Certificates for VISP
# This script generates self-signed certificates for local development

echo "🔐 Generating SSL Certificates for VISP"
echo "========================================"

# Create certs directory
mkdir -p certs

# 1. Fetch SWAMID certificate (optional)
echo ""
echo "📥 Fetching SWAMID certificate..."
if [ ! -f "certs/md-signer2.crt" ]; then
    if curl -f http://mds.swamid.se/md/md-signer2.crt -o certs/md-signer2.crt 2>/dev/null; then
        echo "✓ SWAMID certificate downloaded"
    else
        echo "⚠️  Warning: Could not fetch SWAMID certificate from mds.swamid.se"
        echo "   SWAMID authentication may not work properly."
        echo "   You can manually download it later if needed."
    fi
else
    echo "✓ SWAMID certificate already exists, skipping"
fi

# 2. Generate visp.local certificate
echo ""
echo "🔑 Generating visp.local TLS certificate..."
mkdir -p certs/visp.local

if [ ! -f "certs/visp.local/cert.crt" ] || [ ! -f "certs/visp.local/cert.key" ]; then
    openssl req -x509 -newkey rsa:4096 \
        -keyout certs/visp.local/cert.key \
        -out certs/visp.local/cert.crt \
        -nodes -days 3650 \
        -subj "/C=SE/ST=visp/L=visp/O=visp/OU=visp/CN=visp.local" \
        -addext "basicConstraints=critical,CA:FALSE" \
        -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
        -addext "extendedKeyUsage=serverAuth" \
        -addext "subjectAltName=DNS:visp.local,DNS:*.visp.local"
    echo "✓ visp.local certificate generated"
else
    echo "✓ visp.local certificate already exists, skipping"
fi

# 3. Generate SimpleSAMLphp IdP certificate
echo ""
echo "🔑 Generating SimpleSAMLphp IdP certificate..."
mkdir -p certs/ssp-idp-cert

if [ ! -f "certs/ssp-idp-cert/cert.pem" ] || [ ! -f "certs/ssp-idp-cert/key.pem" ]; then
    openssl req -x509 -newkey rsa:4096 \
        -keyout certs/ssp-idp-cert/key.pem \
        -out certs/ssp-idp-cert/cert.pem \
        -nodes -days 3650 \
        -subj "/C=SE/ST=visp/L=visp/O=visp/OU=visp/CN=visp.local" \
        -addext "basicConstraints=critical,CA:FALSE" \
        -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
        -addext "extendedKeyUsage=serverAuth,clientAuth" \
        -addext "subjectAltName=DNS:visp.local"
    echo "✓ SSP IdP certificate generated"
else
    echo "✓ SSP IdP certificate already exists, skipping"
fi

echo ""
echo "✅ Certificate generation complete!"
echo ""
echo "Generated certificates:"
echo "  • certs/visp.local/cert.crt + cert.key (HTTPS server)"
echo "  • certs/ssp-idp-cert/cert.pem + key.pem (SAML IdP)"
if [ -f "certs/md-signer2.crt" ]; then
    echo "  • certs/md-signer2.crt (SWAMID metadata signer)"
fi
echo ""
echo "Note: These are self-signed certificates for development use only."
echo "      For production, use certificates from a trusted CA."
